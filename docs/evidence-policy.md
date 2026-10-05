# Evidence policy

This is a research-only corpus of three WHO publications, not a substitute for
current national guidance or patient-specific clinical advice.

## Preparation

- Put PDFs in `data/evidence/raw` and run `scripts/prepare_evidence.py`.
- No `evidence/source-registry.yaml` is used. There is no automated license
  check. Public availability does not remove reuse obligations; deployment
  owners remain responsible for permissions and attribution.
- Seed citation details live in `SOURCE_DETAILS` in the script: title,
  organizational author, publisher, publication date, canonical WHO URL and
  global geographic scope. Unknown PDFs need citation details before indexing.
- Keep original PDFs and full extracted TXT files. JSON contains individual
  physical PDF pages suitable for later chunking, not curated sections.
- Use pip-installed pypdf in plain mode, with PyMuPDF fallback for empty pages
  or undecodable characters. Poppler is not required by this script.
- Omit empty pages and pages still containing replacement characters from
  JSON, recording them in the manifest. Never silently delete undecodable
  characters or guess missing clinical values.
- Record source SHA-256, preparation timestamp and parser. Checksum changes
  do not block preparation; changed sources require evaluation-reference review.
- Index only source IDs listed in the current manifest, not leftover files.

## Citations and interpretation

- Show source title, publisher, publication date, geographic scope, URL and
  physical PDF page alongside a supporting passage.
- Do not present older guidance as the latest recommendation. Supersession
  has not been assessed for this seed corpus.
- Do not ingest real patient records or individual patient case reports.
- Keep synthetic cohort findings separate from published evidence.
- Answer unsupported questions with an evidence-insufficiency response.
- License pages, contents and references may remain in JSON; retrieval should
  prefer passages supporting the question, not boilerplate.

## Extraction limitations

Passing the character check does not guarantee correct table, image, flowchart
or reading-order interpretation. OCR is not implemented. Inspect empty and
excluded pages before relying on their content. The source PDF remains
authoritative, especially for numerical tables. TXT is for inspection, not indexing.

## Dependency deployment note

The PyMuPDF fallback has AGPL/commercial licensing options. Review its
[official licensing terms](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
before deploying or distributing the application. This is separate from evidence
licensing and does not introduce automated license checks into preparation.

Seed metadata sources:

- [Diabetes monitoring, 2024](https://www.who.int/publications/i/item/9789240102248)
- [HEARTS-D, 2020](https://www.who.int/publications/i/item/who-ucn-ncd-20.1)
- [NCD monitoring, 2022](https://www.who.int/publications/i/item/9789240057067)
