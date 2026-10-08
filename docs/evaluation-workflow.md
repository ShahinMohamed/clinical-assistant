# Evaluation workflow

The cohort runner is a regression check, not an LLM evaluation or independent
clinical validation. It compares reference SQL results with a saved baseline.

```bash
# Normal check: never modifies the saved reference file.
python -m scripts.cohorts.run_eval_checks

# First reference, only if no reference file exists.
python -m scripts.cohorts.run_eval_checks --generate-reference --analysis-date 2026-10-04

# Explicit refresh after reviewing changed data, definitions or SQL.
# The previous reference is copied to reports/reference-backups first.
python -m scripts.cohorts.run_eval_checks --refresh-reference --analysis-date 2026-10-04

```

- Checks use the baseline's ingestion run and fixed analysis date. New patient
  imports do not silently change existing expected answers.
- Generation uses the latest completed run. No sample row totals are hardcoded.
- Raw distinct resource IDs must reconcile with normalized table counts.
- Failed data-quality checks never create or replace a baseline.
- Baselines record dataset fingerprint and SQL checksum. Generation is labeled
  `reference_generated`, not `verified`.
- Every cohort result warns when the dataset is stale, including adult counts.
- Cohort question IDs and text are defined in `scripts/cohorts/run_eval_checks.py`;
  the saved reference is `evals/cohort-ground-truth.yaml`.
- Evidence loading validates citation metadata and extraction character quality.
  Retrieval cases are defined in `evals/evidence-questions.yaml`; the runner
  checks whether expected pages occur among the top five retrieved chunks.
  It does not validate generated answers or score the unanswerable case.
- Default production dates remain current UTC. Fixed dates here are deliberate
  for repeatable testing, not a change to production behaviour.

## Evidence retrieval

Run from the project root with the dependencies installed and an existing
evidence index. These checks do not call Gemini or modify the database.

```bash
python -m scripts.evidence.evaluate_retrieval

# Generate a separate benchmark once, then reuse it for comparisons.
# Use a new filename if this output already exists.
python -m scripts.evidence.generate_questions --count 20 --output evals/evidence-questions-v2.yaml

# Alternatively, generate references for the existing seed questions.
python -m scripts.evidence.generate_questions --from-seeds --output evals/evidence-questions-from-seeds.yaml

python -m scripts.evidence.evaluate_retrieval --questions evals/evidence-questions-generated.yaml
python -m scripts.evidence.evaluate_retrieval --questions evals/evidence-questions-generated.yaml --rerank
```

The evaluator does not generate questions. Generation options belong only to
`scripts.evidence.generate_questions`; `--count` replaces the old `--generate COUNT`.
The generator requires Gemini and sends selected public evidence pages to Google.
Its quotation checks do not independently verify clinical correctness or prove
that negative cases are unanswerable across the corpus.

The runner writes a timestamped JSON file in `reports/`. Set `--report` to choose
a filename; existing reports are never overwritten. `hit_at_5` requires at
least one expected page per answerable case; `expected_page_coverage` measures
how many expected pages were retrieved. A pass is a retrieval smoke test, not
clinical validation. Review misses rather than changing expectations to force
a pass. Reindex when processed evidence or embedding settings change.

With `--rerank`, both variants use the same 20 candidates and return five chunks.
The report includes paired metric changes and timing. Evaluation keeps the
existing corpus-checksum and dataset-checksum checks; no benchmark is regenerated
as a side effect of evaluation. A completed run exits successfully even when
retrieval misses occur; inspect metrics rather than treating exit status as accuracy.
