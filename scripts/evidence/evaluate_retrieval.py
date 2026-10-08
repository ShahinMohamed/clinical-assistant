"""Evaluate evidence retrieval and optionally compare reranking."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import yaml

from rag.evidence_common import ROOT, get_store, get_tokenizer
from safety.checks import check_input, guardrail_settings
from rag.rerank import get_reranker, reranker_settings
from scripts.evidence.evaluation_common import (
    SEEDS,
    load_pages,
    normalize,
    now,
    validate_case,
)

from rag.retrieval_pipeline import (
    add_feature_arguments,
    build_pipeline,
    feature_options,
    variant_name,
)

from rag.hyde import hyde_settings

def score_documents(documents, expected):
    sections = [
        document.metadata["section_id"]
        for document in documents
    ]
    matched = expected & set(sections)

    reciprocal_rank = next(
        (
            1 / rank
            for rank, section in enumerate(sections, start=1)
            if section in expected
        ),
        0,
    )

    return {
        "hit_at_5": bool(matched),
        "expected_page_coverage": len(matched) / len(expected),
        "reciprocal_rank_at_5": reciprocal_rank,
        "ranked_sections": sections,
        "matched_sections": sorted(matched),
    }


def summarize(results, variant):
    scored = [
        result[variant]
        for result in results
        if variant in result
    ]

    count = len(scored)
    if not count:
        raise ValueError("No answerable cases were evaluated.")

    return {
        "hit_at_5_percent": (
            100 * sum(row["hit_at_5"] for row in scored) / count
        ),
        "mean_expected_page_coverage_percent": (
            100
            * sum(row["expected_page_coverage"] for row in scored)
            / count
        ),
        "mrr_at_5": (
            sum(row["reciprocal_rank_at_5"] for row in scored)
            / count
        ),
        "mean_query_seconds": (
            sum(row["query_seconds"] for row in scored)
            / count
        ),
    }


def calculate_changes(baseline, current):
    changes = {}

    for metric, before in baseline.items():
        after = current[metric]
        change = after - before

        lower_is_better = metric == "mean_query_seconds"
        improvement = -change if lower_is_better else change

        changes[metric] = {
            "baseline": round(before, 4),
            "new": round(after, 4),
            "absolute_change": round(change, 4),
            "percentage_point_change": (
                round(change, 4)
                if metric.endswith("_percent")
                else None
            ),
            "relative_improvement_percent": (
                round(100 * improvement / before, 2)
                if before != 0
                else None
            ),
            "direction": (
                "lower_is_better"
                if lower_is_better
                else "higher_is_better"
            ),
        }

    return changes

def evaluate(args):
    dataset_bytes = args.questions.read_bytes()
    dataset = yaml.safe_load(dataset_bytes)
    pages, checksum = load_pages()

    if dataset.get("kind") == "automatically_generated":
        if dataset["corpus_checksum"] != checksum:
            raise ValueError(
                "Evidence changed. Generate a new benchmark version."
            )

        for case in dataset["cases"]:
            validate_case(case, pages)

    cases = dataset["cases"]

    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evaluation case IDs.")

    question_keys = [
        normalize(case["question"]).casefold()
        for case in cases
    ]
    if len(question_keys) != len(set(question_keys)):
        raise ValueError("Duplicate evaluation questions.")

    store, configuration = get_store()

    if configuration["corpus_checksum"] != checksum:
        raise ValueError("Active index and evaluation corpus differ.")

    options = feature_options(args)
    enabled = any(options.values())

    get_tokenizer()

    if options["rerank"]:
        get_reranker().predict(
            [("warm-up", "warm-up")],
            show_progress_bar=False,
        )

    pipeline = build_pipeline(store, **options)
    baseline_pipeline = build_pipeline(store)

    results = []
    baseline_candidate_hits = []
    selected_candidate_hits = []

    for case in cases:
        if not case["answerable"]:
            result = {
                "id": case["id"],
                "status": "not_scored",
                "reason": (
                    "Negative cases are excluded from "
                    "positive retrieval metrics."
                ),
            }
            checked_input = check_input(case["question"])
            if not checked_input["allowed"]:
                result["status"] = "guardrail_refused"
                result["guardrails"] = {"enabled": True, "input": checked_input}
            elif options["web"]:
                result["reason"] = (
                    "Local negative references do not establish "
                    "unanswerability on the live web."
                )

            elif options["crag"]:
                started = perf_counter()
                state = pipeline(case["question"])
                seconds = perf_counter() - started
                result["guardrails"] = state["guardrails"]
                if state["blocked"]:
                    result["status"] = "guardrail_refused"
                else:
                    result["crag_negative"] = {
                        "abstained": state["crag"]["abstained"],
                        "query_seconds": seconds,
                        "trace": state["crag"],
                        "retrieval_steps": state["retrieval_steps"],
                    }

            results.append(result)
            continue

        expected = set(case["expected_sections"])

        if not expected or not expected.issubset(pages):
            raise ValueError(
                f"Invalid expected pages: {case['id']}"
            )

        # Baseline is ALWAYS plain hybrid, regardless of flags.
        started = perf_counter()

        baseline_state = baseline_pipeline(case["question"])
        candidates = baseline_state["candidates"]

        baseline_seconds = perf_counter() - started

        baseline_sections = {
            document.metadata["section_id"]
            for document in candidates
        }

        baseline_candidate_hit = bool(
            expected & baseline_sections
        )
        baseline_candidate_hits.append(
            baseline_candidate_hit
        )

        baseline = score_documents(
            baseline_state["documents"],
            expected,
        )
        baseline["query_seconds"] = baseline_seconds
        baseline["guardrails"] = baseline_state["guardrails"]
        baseline["request_blocked"] = baseline_state["blocked"]

        result = {
            "id": case["id"],
            "question": baseline_state["question"],
            "expected_sections": sorted(expected),
            "candidate_count": len(candidates),
            "candidate_hit_at_20": baseline_candidate_hit,
            "baseline": baseline,
        }

        if enabled:
            # HyDE needs its own candidate retrieval.
            # Other combinations can reuse baseline candidates,
            # then apply reranking and/or CRAG.
            reuse_baseline = not options["hyde"]

            started = perf_counter()

            state = pipeline(
                case["question"],
                initial_candidates=(
                    candidates if reuse_baseline else None
                ),
            )

            selected_seconds = perf_counter() - started

            if reuse_baseline and not state["blocked"]:
                selected_seconds += baseline_seconds

            selected = score_documents(
                state["documents"],
                expected,
            )
            selected["query_seconds"] = selected_seconds
            selected["trace"] = state["crag"]
            selected["retrieval_steps"] = state["retrieval_steps"]
            selected["guardrails"] = state["guardrails"]
            selected["request_blocked"] = state["blocked"]

            # CRAG can retrieve twice. This checks whether an expected
            # page was available in ANY attempt's candidate pool.
            selected_sections = {
                section
                for step in state["retrieval_steps"]
                for section in step.get(
                    "candidate_sections", []
                )
            }

            selected_candidate_hits.append(
                bool(expected & selected_sections)
            )

            result["selected"] = selected

        else:
            result["selected"] = dict(baseline)
            selected_candidate_hits.append(
                baseline_candidate_hit
            )

        results.append(result)

    checked = len(baseline_candidate_hits)
    if not checked:
        raise ValueError("No answerable cases were evaluated.")

    baseline_metrics = summarize(results, "baseline")
    current_metrics = summarize(results, "selected")

    crag_positive = [
        result["selected"]["trace"]
        for result in results
        if "selected" in result
        and result["selected"].get("trace") is not None
    ]

    crag_negative = [
        result["crag_negative"]
        for result in results
        if "crag_negative" in result
    ]

    feature_settings = {}

    if options["hyde"]:
        feature_settings["hyde"] = hyde_settings()

    if options["rerank"]:
        feature_settings["rerank"] = reranker_settings()

    if options["crag"]:
        from rag.crag import crag_settings

        feature_settings["crag"] = crag_settings(
            web=options["web"],
        )
        if options["web"]:
            from rag.web_evidence import web_settings

            feature_settings["web"] = {
                **web_settings(),
                "chunk_size": 350,
                "chunk_overlap": 50,
                "max_chunks_per_source": 80,
                "candidate_k": 20,
                "final_k": 5,
                "candidate_ranking": "embedding_similarity",
            }

    packages = [
        "langchain-core",
        "langchain-postgres",
        "sentence-transformers",
        "transformers",
        "torch",
        "langchain-google-genai",
        "langgraph"
    ]

    if options["web"]:
        packages.extend(["httpx", "trafilatura"])

    report = {
        "generated_at": now(),
        "protocol": "graph-hybrid-baseline-always-on-guardrails-v4",
        "dataset": str(args.questions),
        "dataset_sha256": hashlib.sha256(
            dataset_bytes
        ).hexdigest(),
        "reference_type": dataset.get(
            "kind", "seed_references"
        ),
        "clinical_validation": False,
        "index": configuration,

        "baseline_variant": "hybrid",
        "baseline_features": {
            name: False
            for name in options
        },
        "variant": variant_name(options),
        "features": options,
        "feature_settings": feature_settings,
        "guardrails": {
            **guardrail_settings(),
            "applied_to": "Both plain hybrid and selected variant",
            "answer_checks_evaluated": False,
        },
        "answerable_guardrail_refusal_percent": (
            100 * sum(
                result["selected"]["request_blocked"]
                for result in results if "selected" in result
            ) / checked
        ),

        "retrieval_settings": {
            "candidate_k_per_attempt": 20,
            "final_k": 5,
            "semantic_candidates": 20,
            "keyword_candidates": 20,
            "fusion": "reciprocal_rank_fusion",
            "rrf_k": 60,
        },

        "environment": {
            package: version(package)
            for package in packages
        },

        "timing": (
            "Baseline runs the guarded plain-hybrid graph, fetches 20 "
            "candidates and scores five. Both variants use input guards. "
            "Selected timing includes all enabled feature work. "
            "Reused initial retrieval time is added exactly once. "
            "Model loading and final answer generation are excluded."
        ),

        "checked_cases": checked,

        "baseline_candidate_hit_at_20_percent": (
            100 * sum(baseline_candidate_hits) / checked
        ),
        "selected_candidate_hit_at_20_any_attempt_percent": (
            100 * sum(selected_candidate_hits) / checked
        ),

        "baseline_metrics": baseline_metrics,
        "metrics": current_metrics,
        "improvements": (
            calculate_changes(
                baseline_metrics,
                current_metrics,
            )
            if enabled
            else None
        ),

        "crag_decisions": (
            {
                "answerable_case_abstention_percent": (
                    100
                    * sum(
                        row["abstained"]
                        for row in crag_positive
                    )
                    / len(crag_positive) if crag_positive else None
                ),
                "correction_triggered_percent": (
                    100
                    * sum(
                        row["attempts"] > 1
                        for row in crag_positive
                    )
                    / len(crag_positive) if crag_positive else None
                ),
                "negative_cases": len(crag_negative),
                "negative_case_abstention_percent": (
                    100
                    * sum(
                        row["abstained"]
                        for row in crag_negative
                    )
                    / len(crag_negative)
                    if crag_negative
                    else None
                ),
            }
            if options["crag"]
            else None
        ),

        "passed": current_metrics["hit_at_5_percent"] == 100,
        "cases": results,
        "medical_fact_verification": False,
        "evaluation_scope": (
            "Expected local-page retrieval, not final-answer correctness."
        ),
        "live_web_repeatability": (
            "Not guaranteed: search results and source content can change."
            if options["web"]
            else "Not applicable."
        ),
    }

    report_path = args.report or (
        ROOT
        / "reports"
        / (
            f"{variant_name(options)}-"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
            ".json"
        )
    )

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with report_path.open("x", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")

    print("Plain hybrid baseline:")
    print(json.dumps(baseline_metrics, indent=2))

    print(f"\nSelected: {report['variant']}")
    print(json.dumps(current_metrics, indent=2))

    if enabled:
        print("\nChanges against plain hybrid:")
        print(json.dumps(report["improvements"], indent=2))

    print(f"\nReport: {report_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        default=SEEDS,
    )
    parser.add_argument("--report", type=Path)

    add_feature_arguments(parser)

    args = parser.parse_args()
    
    options = feature_options(args)
    if options["web"] and not options["crag"]:
        parser.error("--web requires --crag.")

    args.questions = args.questions.resolve()

    if args.report is not None:
        args.report = args.report.resolve()

    return evaluate(args)

if __name__ == "__main__":
    raise SystemExit(main())
