# Clinical Evidence and Synthetic Patient Cohort Assistant

Research-only project using public WHO evidence and synthetic Synthea FHIR R4
records. Not for diagnosis, prescribing or patient-specific treatment. Never
load real patient records into this prototype.

## Current layout

- `rag/`: runtime retrieval helpers, reranking and evidence chat.
- `cohorts/`: schema-aware synthetic-cohort Text2SQL graph.
- `safety/`: shared always-on input and answer checks.
- `web/`: local single-page workbench with selectable features and inspectable execution flow.
- `tests/`: offline guardrail and graph regression tests.
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
python -m pip install -r requirements-dev.txt
docker compose up -d db
```

The Docker configuration is for local development, not production deployment.
Configure an uncommitted `.env` in the project root:

```dotenv
DATABASE_URL=postgresql://clinical:clinical@localhost:5432/clinical_assistant
GEMINI_API_KEY=your_key
GEMINI_MODEL=your_available_model_id
```

Gemini settings are needed for generated answers, Text2SQL, HyDE, CRAG,
and evaluation-question generation.
Indexing and retrieval checks use local models; the first run downloads them.
Answer generation sends questions and retrieved evidence excerpts to Google;
question generation sends selected evidence pages.

## Vercel and Neon hosting

`vercel.json` serves only the four UI assets from the generated `public/`
directory. `/api/ask` and `/api/status` run in the private FastAPI Function.
The build bundles the pinned BGE and MiniLM models and installs CPU-only
PyTorch. Enable `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` for the larger Python
bundle. The backend uses Python 3.14 and a maximum duration of 300 seconds.

Use Neon Free with pgvector and migrate the completed ingestion metadata,
patients, conditions, observations, active evidence index and its chunk table.
Raw FHIR JSON is unnecessary for the hosted research pipeline.
Apply `db/hosting.sql`, then create a separate runtime login with SELECT
access to those public tables and access to the private request-budget table.
Keep the owner connection string for migrations only.

Set `DATABASE_URL`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, and a randomly generated
`APP_RATE_LIMIT_SECRET` of at least 32 characters as Vercel production Secrets.
Set `GEMINI_MODEL` as a server Config variable. Never prefix secrets with
`PUBLIC_`, `NEXT_PUBLIC_`, or `VITE_`. `.env*` files are excluded from deployments.

Hosted research uses a shared database limit: one request per minute and ten
per UTC day for each client IP, with fifty requests per UTC day across the
demo. `RESEARCH_CLIENT_DAILY_LIMIT` and `RESEARCH_DAILY_LIMIT` can lower those
limits; zero disables hosted research. Budget failures stop before calling
Gemini. Only a keyed hash of the client IP is stored; counters older than two
days are removed on the next research request.
Free hosting and model API quotas are separate.

Generated SQL is restricted to aggregate SELECT queries on the three cohort
tables, with allowed functions, transaction-level read-only enforcement, and
a five-second SQL timeout. Client responses redact configured secrets, and
security headers restrict scripts, framing, and browser capabilities.

## Research workbench UI

After setup, launch the single-page interface from the project root:

```bash
.venv/bin/python -m web.server
# Or use your active Python environment: python -m web.server
```

Open http://127.0.0.1:8000. Use `--port 8001` to choose another port.
The interface uses the existing unified assistant to route questions to evidence,
synthetic cohorts, or both. It adds no frontend dependencies or build step.

- Select **HyDE**, **Reranking**, **CRAG**, and **Web fallback** with checkboxes.
  Hybrid retrieval and safety checks stay on. Selecting web enables CRAG;
  turning CRAG off also turns web off. Evidence features do not affect cohort-only queries.
- Enter a question or choose an example, then click **Run research** (Ctrl/Cmd+Enter).
  Optional cohort settings select the analysis date and ingestion run.
- After receiving the result, expand **More details** on any execution step to
  inspect its actual output: hypothetical passage, candidate excerpts, rankings,
  evidence-support checks, SQL, rows, or answer. Conditional stages appear only
  when executed; web fallback runs only if local evidence is insufficient.
- Use **Sources** to inspect citation excerpts, **Run data** for the full result,
  or **Export run** to save the answer, selected features, timings, and flow as JSON.

The server listens only on the local loopback interface. It runs one question at
a time and does not persist questions or results. The configuration indicator
checks setting presence, not database/model connectivity. Restart after editing
`.env` or reindexing. Optional tracing leaves the existing CLI behavior unchanged;
generated text rejected by output guards is withheld from stage details too.

UI/API and graph-tracing regression checks use mocks without database or model calls:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Synthetic data tools

Replace `/path/to/synthea-fhir` with your synthetic FHIR directory.

```bash
python -m scripts.cohorts.profile_fhir /path/to/synthea-fhir
python -m scripts.cohorts.load_fhir /path/to/synthea-fhir
python -m scripts.cohorts.run_eval_checks --evaluate-text2sql --analysis-date 2026-10-04
python -m cohorts.sql_agent "How many synthetic patients are in the dataset?" --analysis-date 2026-10-04
```

The cohort check compares Text2SQL patient counts with freshly executed reference
SQL for the same saved ingestion run and explicit analysis date. It does not
rewrite the question dataset or use its saved expected counts.

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
vector and PostgreSQL full-text retrieval using reciprocal rank fusion. The graph
supports optional `--hyde`, `--rerank`, and `--crag`; `--web` requires `--crag`.
Text2SQL is still a separate command, not automatically routed from evidence chat.

Unchanged indexing inputs are skipped. `python -m scripts.evidence.index_evidence --force`
builds another index deliberately. New indexes activate only after indexing and
structural checks succeed; previous/failed-build tables are retained, so they
consume storage. Restart a running chat after reindexing to use the new index.

Answers return source excerpts and citation metadata. Citation-format checks
do not prove clinical accuracy or that every claim is supported. Cohort counts
come from the separate Text2SQL graph, not the evidence chat.

## Always-on guardrails

Evidence and cohort graphs run deterministic input checks automatically. There
is no `--guardrails` flag or opt-out. Common instruction overrides, pasted record
markers, contact details, patient-list requests, and personal medication requests
are refused before retrieval or SQL generation. Cohort refusals also skip the
dataset lookup. Generated evidence answers undergo citation-identifier and
personal-directive checks; cohort explanations undergo personal-directive checks.
Failed explanation checks withhold the text without changing database results.

These small English-language heuristics can miss violations and falsely refuse
valid questions. They do not verify medical facts, prove citation support,
identify all sensitive data, certify synthetic records, or replace SQL controls.

```bash
# Offline rule report: no database or model/API calls.
python -m scripts.evaluate_guardrails

# Offline graph tests with mocked retrieval, database, and model calls.
python -m unittest discover -s tests -v
```

Retrieval evaluation applies the same input guards to both plain-hybrid baseline
and selected variants. Guardrail refusals are separate from CRAG abstentions;
refused answerable cases remain in retrieval metrics as misses. Retrieval-only
evaluation does not test generated answers. Older v3 timing reports use a different
baseline boundary, so use the new paired v4 reports for timing comparisons.

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
