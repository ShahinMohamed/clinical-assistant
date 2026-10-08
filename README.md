# Clinical Evidence and Synthetic Patient Cohort Assistant

Research-only project using public WHO evidence and synthetic Synthea FHIR R4
records. Not for diagnosis, prescribing or patient-specific treatment. Never
load real patient records into this prototype.

## Current layout

- `rag/`: runtime retrieval helpers, reranking and evidence chat.
- `scripts/evidence/`: PDF preparation, indexing, question generation and retrieval evaluation.
- `scripts/cohorts/`: synthetic FHIR profiling, ingestion and cohort checks.
- `db/`: synthetic-cohort schema and reference SQL.
- `evals/`: saved cohort baseline and evidence retrieval questions.
- `docs/`: scope, data dictionary, cohort definitions and evidence/evaluation policies.
- `data/`: local downloaded evidence and processed JSON; not committed.
- `reports/`: generated reports and cohort reference backups; not committed.

All commands below run from the project root using `python -m rag.<module>`,
`python -m scripts.evidence.<module>` or `python -m scripts.cohorts.<module>`.
Do not run these modules directly by filename: cross-package imports require
the project root on Python's import path. Select your project Python environment
in your editor before running them.

## Setup

```bash
python -m pip install -r requirements.txt
docker compose up -d db
```

The Docker configuration is for local development, not production deployment.
Configure an uncommitted `.env` in the project root:

```dotenv
DATABASE_URL=postgresql://clinical:clinical@localhost:5432/clinical_assistant
GEMINI_API_KEY=your_key
GEMINI_MODEL=your_available_model_id
```

Gemini settings are needed for generated answers and evaluation-question generation.
Indexing and retrieval checks use local models; the first run downloads them.
Answer generation sends questions and retrieved evidence excerpts to Google;
question generation sends selected evidence pages.

## Synthetic data tools

Replace `/path/to/synthea-fhir` with your synthetic FHIR directory.

```bash
python -m scripts.cohorts.profile_fhir /path/to/synthea-fhir
python -m scripts.cohorts.load_fhir /path/to/synthea-fhir
python -m scripts.cohorts.run_eval_checks
```

The cohort check compares with the existing fixed-date baseline; it does not
replace it. See [evaluation workflow](docs/evaluation-workflow.md) for initial
generation and explicit refresh commands.

## Evidence tools

Place the seed PDFs in `data/evidence/raw/`. Their filenames must match the
source IDs in `SOURCE_DETAILS` in `scripts/evidence/prepare_evidence.py`. New sources
need citation metadata there before they can be indexed.

```bash
python -m scripts.evidence.prepare_evidence
python -m scripts.evidence.index_evidence
python -m rag.ask_evidence "What does HEARTS-D say about HbA1c monitoring?" --retrieve-only
python -m scripts.evidence.evaluate_retrieval
python -m rag.ask_evidence
```

Indexing reads manifest-listed processed JSON, not the PDFs again. It combines
vector and PostgreSQL full-text retrieval using reciprocal rank fusion. It does
not include Text2SQL routing or corrective loops. Add `--rerank` to the chat command
to enable the optional local cross-encoder reranker.

Unchanged indexing inputs are skipped. `python -m scripts.evidence.index_evidence --force`
builds another index deliberately. New indexes activate only after indexing and
structural checks succeed; previous/failed-build tables are retained, so they
consume storage. Restart a running chat after reindexing to use the new index.

Answers return source excerpts and citation metadata. Citation-format checks
do not prove clinical accuracy or that every claim is supported. Cohort counts
come from reference SQL, not the evidence chat.

Question generation is separate from evaluation:

```bash
# Choose a new output filename: existing benchmarks are never overwritten.
python -m scripts.evidence.generate_questions --count 20 --output evals/evidence-questions-v2.yaml
python -m scripts.evidence.generate_questions --from-seeds --output evals/evidence-questions-from-seeds.yaml

# Reuse the same saved questions for comparable feature experiments.
python -m scripts.evidence.evaluate_retrieval --questions evals/evidence-questions-generated.yaml
python -m scripts.evidence.evaluate_retrieval --questions evals/evidence-questions-generated.yaml --rerank
```

Generated references are checked for source-quotation matches, not clinical
correctness. See the evaluation workflow for metric and report details.

## Project policies

- [Product scope](docs/product-scope.md)
- [Cohort definitions](docs/cohort-definitions.md)
- [Data dictionary](docs/data-dictionary.md)
- [Evidence policy](docs/evidence-policy.md)
- [Evaluation workflow](docs/evaluation-workflow.md)
