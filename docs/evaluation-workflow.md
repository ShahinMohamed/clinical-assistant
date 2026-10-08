# Evaluation workflow

The cohort runner compares Text2SQL patient counts with freshly executed reference
SQL for the same saved ingestion run and explicit analysis date. It does not
validate clinical correctness, full SQL equivalence, or generated explanations.

```bash
# Never modifies the saved questions or overwrites an existing report.
python -m scripts.cohorts.run_eval_checks --evaluate-text2sql --analysis-date 2026-10-04 --text2sql-report reports/text2sql-v1.json
```

- Questions and ingestion-run identity come from `evals/cohort-ground-truth.yaml`.
  SQL blocks with matching case IDs come from `db/eval_queries.sql`.
- The selected date applies to both reference SQL and Text2SQL. Saved YAML expected
  counts are ignored. New patient imports do not silently switch the saved run.
- Dataset fingerprints and report checksums identify the comparison context.
- Reference SQL defines the target; its 100% agreement is not independent validation.
- Completed cohort results warn when the dataset is stale, including adult counts.
- Evidence loading validates citation metadata and extraction character quality.
  Retrieval cases are defined in `evals/evidence-questions.yaml`; the runner
  checks whether expected pages occur among the top five retrieved chunks.
  It does not validate generated answers or score the unanswerable case.
- Default production dates remain current UTC. Fixed dates here are deliberate
  for repeatable testing, not a change to production behaviour.

## Evidence retrieval

Run from the project root with the dependencies installed and an existing
evidence index. Plain hybrid and reranking do not call Gemini. HyDE and CRAG do;
`--web` additionally permits live external search. Retrieval checks do not modify
the index or generate final answers.

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

## Always-on guardrails

Both the plain-hybrid baseline and selected retrieval graph use the same input
checks. There is no `--guardrails` argument. Refused answerable cases count as
misses rather than disappearing from the denominator. Reports include guardrail
settings and refusal percentages. A guardrail refusal is not a CRAG abstention.
The v4 graph baseline includes input-check/graph overhead; do not attribute timing
differences from older v3 reports solely to retrieval-feature improvements.

```bash
python -m scripts.evaluate_guardrails
python -m unittest discover -s tests -v
```

The separate rule report uses 16 handcrafted English input/output examples with
policy-defined expected outcomes. Its percentages apply only to those examples,
not arbitrary attacks, complete sensitive-data detection, or clinical safety.
The graph tests mock external services and local models. Live database, retrieval,
and Gemini evaluations still require the normal project environment.
