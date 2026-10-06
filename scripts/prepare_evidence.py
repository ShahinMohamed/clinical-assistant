"""Extract every PDF in data/evidence/raw without a source registry."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
import pymupdf


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/evidence/raw"
PROCESSED = ROOT / "data/evidence/processed"

# Citation details for the three seed PDFs; no YAML registry or license gate.
SOURCE_DETAILS = {
    "who-diabetes-monitoring-2024": {
        "title": "Guidance on global monitoring for diabetes prevention and control: framework, indicators and application",
        "publication_date": "2024-11-14",
        "canonical_url": "https://www.who.int/publications/i/item/9789240102248",
    },
    "who-hearts-d-2020": {
        "title": "HEARTS-D: diagnosis and management of type 2 diabetes",
        "publication_date": "2020-04-22",
        "canonical_url": "https://www.who.int/publications/i/item/who-ucn-ncd-20.1",
    },
    "who-ncd-monitoring-2022": {
        "title": "Noncommunicable disease facility-based monitoring guidance: framework, indicators and application",
        "publication_date": "2022-11-11",
        "canonical_url": "https://www.who.int/publications/i/item/9789240057067",
    },
}


def extract_page(page, fallback_page):
    # Plain extraction preserves words and reading order better in this corpus.
    text = page.extract_text() or ""
    parser = "pypdf plain extraction"
    if "\ufffd" in text or not text.strip():
        fallback = fallback_page.get_text()
        if fallback.count("\ufffd") < text.count("\ufffd") or (not text.strip() and fallback.strip()):
            text, parser = fallback, "PyMuPDF fallback extraction"
    return text, parser


def clean_page(text, repeated_edges):
    lines = text.splitlines()
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    edges = set(nonempty[:1] + nonempty[-1:])
    result = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index in edges and (stripped in repeated_edges or stripped.isdigit()):
            continue
        # Do not guess missing words, numbers or table cells.
        result.append(line.rstrip())
    return "\n".join(result).strip()


def prepare_source(pdf):
    source_id = pdf.stem
    content = pdf.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"Not a PDF: {pdf}")
    checksum = hashlib.sha256(content).hexdigest()

    reader = PdfReader(pdf)
    with pymupdf.open(pdf) as fallback:
        extracted = [extract_page(page, fallback[index]) for index, page in enumerate(reader.pages)]
    pages = [text for text, _ in extracted]

    # Remove only frequently repeated first/last lines, not repeated body text.
    edge_counts = {}
    for page in pages:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        for line in set(lines[:1] + lines[-1:]):
            edge_counts[line] = edge_counts.get(line, 0) + 1
    repeated_edges = {
        line for line, count in edge_counts.items()
        if count >= 3 and len(line) >= 60
    }

    pdf_metadata = reader.metadata
    details = SOURCE_DETAILS.get(source_id, {})
    title = details.get("title") or (pdf_metadata.title if pdf_metadata else None) or pdf.stem
    author = pdf_metadata.author if pdf_metadata else None
    metadata = {
        "source_id": source_id,
        "title": title,
        "author": author,
        "source_file": str(pdf.relative_to(ROOT)),
        "checksum": checksum,
        "pdf_page_count": len(pages),
        "publisher": "World Health Organization" if details else None,
        "publication_date": details.get("publication_date"),
        "canonical_url": details.get("canonical_url"),
        "geographic_scope": "Global; adapt to national guidance" if details else None,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    if details:
        metadata["author"] = "World Health Organization"
    records = []
    empty_pages = []
    excluded_pages = []
    for number, page_text in enumerate(pages, start=1):
        section_text = clean_page(page_text, repeated_edges)
        if not section_text:
            empty_pages.append(number)
            continue
        issues = []
        if "\ufffd" in section_text:
            issues.append("undecodable characters; review or OCR needed")
        if not details:
            issues.append("missing citation metadata; add SOURCE_DETAILS in this script")
        if issues:
            excluded_pages.append({"pdf_page": number, "issues": issues})
            continue
        records.append({
            **metadata,
            "section_id": f"{source_id}/page-{number}",
            "section_title": f"Page {number}",
            "section_path": [title, f"Page {number}"],
            "pdf_page_start": number,
            "pdf_page_end": number,
            "pages": [{"pdf_page": number, "text": section_text}],
            "text": section_text,
            "parser": extracted[number - 1][1],
            "extraction_quality": "passed_character_check",
        })

    output = PROCESSED / f"{source_id}.json"
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "source_id": source_id,
        "sections": len(records),
        "pdf_pages": len(pages),
        "empty_pages": empty_pages,
        "excluded_pages": excluded_pages,
        "fallback_pages": [i for i, (_, parser) in enumerate(extracted, 1) if parser.startswith("PyMuPDF")],
        "checksum": checksum,
    }


def main():
    if not RAW.is_dir():
        raise SystemExit(f"PDF directory not found: {RAW}")
    pdfs = sorted(path for path in RAW.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    if not pdfs:
        raise SystemExit(f"No PDF files found in {RAW}")
    PROCESSED.mkdir(parents=True, exist_ok=True)
    results = [prepare_source(pdf) for pdf in pdfs]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": results,
        "total_sections": sum(item["sections"] for item in results),
    }
    (PROCESSED / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    if not summary["total_sections"]:
        raise SystemExit("No indexable evidence pages. See manifest exclusions; add citation metadata or review extraction/OCR.")


if __name__ == "__main__":
    main()
