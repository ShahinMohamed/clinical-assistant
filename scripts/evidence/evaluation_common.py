"""Shared evidence benchmark paths and source-quotation validation."""

from datetime import UTC, datetime

import yaml

from rag.evidence_common import ROOT, load_documents


SEEDS = ROOT / "evals/evidence-questions.yaml"
GENERATED = ROOT / "evals/evidence-questions-generated.yaml"


def now():
    return datetime.now(UTC).isoformat()


def normalize(text):
    return " ".join(text.split())


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_pages():
    documents, checksum = load_documents()
    pages = {
        document.metadata["section_id"]: document
        for document in documents
    }
    return pages, checksum


def validate_case(case, pages):
    """Validate fields and source quotations, not clinical correctness."""
    for field in ["id", "question", "reference_answer"]:
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise ValueError(f"Missing {field}")

    if not isinstance(case.get("answerable"), bool):
        raise ValueError("answerable must be true or false")

    expected = case.get("expected_sections")
    if (
        not isinstance(expected, list)
        or any(not isinstance(section, str) for section in expected)
    ):
        raise ValueError("expected_sections must be a list of strings")

    if len(expected) != len(set(expected)):
        raise ValueError("Duplicate expected sections")

    if not case["answerable"]:
        if expected or case.get("supporting_quotes"):
            raise ValueError("Negative cases cannot claim supporting pages")
        return

    if not expected or any(section not in pages for section in expected):
        raise ValueError("Missing or unknown expected sections")

    quotes = case.get("supporting_quotes")
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("Supporting quotations are required")

    quoted_sections = set()

    for item in quotes:
        section = item["section_id"]
        quote = normalize(item["quote"])

        if section not in expected:
            raise ValueError("Quotation refers to an unexpected section")

        if len(quote) < 20:
            raise ValueError("Supporting quotation is too short")

        if quote not in normalize(pages[section].page_content):
            raise ValueError(f"Quotation not found in {section}")

        quoted_sections.add(section)

    if quoted_sections != set(expected):
        raise ValueError("Every expected section needs a supporting quote")
