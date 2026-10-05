# Evaluation workflow

The cohort runner is a regression check, not an LLM evaluation or independent
clinical validation. It compares reference SQL results with a saved baseline.

```bash
# Normal check: never modifies the saved reference file.
python scripts/run_eval_checks.py

# First reference, only if no reference file exists.
python scripts/run_eval_checks.py --generate-reference --analysis-date 2026-10-04

# Explicit refresh after reviewing changed data, definitions or SQL.
# The previous reference is copied to reports/reference-backups first.
python scripts/run_eval_checks.py --refresh-reference --analysis-date 2026-10-04

```

- Checks use the baseline's ingestion run and fixed analysis date. New patient
  imports do not silently change existing expected answers.
- Generation uses the latest completed run. No sample row totals are hardcoded.
- Raw distinct resource IDs must reconcile with normalized table counts.
- Failed data-quality checks never create or replace a baseline.
- Baselines record dataset fingerprint and SQL checksum. Generation is labeled
  `reference_generated`, not `verified`.
- Every cohort result warns when the dataset is stale, including adult counts.
- `questions-v1.yaml` cohort IDs map to the same questions in the baseline.
  Its starter evidence question has a separate ID from evidence retrieval tests.
- Evidence tests verify citation metadata, page references and extraction
  character quality. They do not yet evaluate an LLM's answers.
- Default production dates remain current UTC. Fixed dates here are deliberate
  for repeatable testing, not a change to production behaviour.
