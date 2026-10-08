"""Evaluate evidence retrieval and optionally compare reranking."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import yaml

from rag.evidence_common import ROOT, get_store, get_tokenizer, retrieve
from rag.rerank import get_reranker, rerank_documents, reranker_settings
from scripts.evidence.evaluation_common import (
    SEEDS,
    load_pages,
    normalize,
    now,
    validate_case,
)


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

    automatically_generated = (
        dataset.get("kind") == "automatically_generated"
    )

    if automatically_generated:
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

    # Exclude model downloads/loading from per-question timing.
    get_tokenizer()

    if args.rerank:
        model = get_reranker()
        model.predict(
            [("warm-up", "warm-up")],
            show_progress_bar=False,
        )

    results = []
    candidate_hits = []

    for case in cases:
        if not case["answerable"]:
            results.append({
                "id": case["id"],
                "status": "not_scored",
                "reason": "Abstention requires answer-generation evaluation.",
            })
            continue

        expected = set(case["expected_sections"])

        if not expected or not expected.issubset(pages):
            raise ValueError(f"Invalid expected pages: {case['id']}")

        # Both variants use exactly the same candidate pool.
        started = perf_counter()
        candidates = retrieve(store, case["question"], top_k=20)
        retrieval_seconds = perf_counter() - started

        candidate_sections = {
            document.metadata["section_id"]
            for document in candidates
        }
        candidate_hit = bool(expected & candidate_sections)
        candidate_hits.append(candidate_hit)

        baseline = score_documents(candidates[:5], expected)
        baseline["query_seconds"] = retrieval_seconds

        result = {
            "id": case["id"],
            "question": case["question"],
            "expected_sections": sorted(expected),
            "candidate_count": len(candidates),
            "candidate_hit_at_20": candidate_hit,
            "baseline": baseline,
        }

        if args.rerank:
            started = perf_counter()

            reranked = rerank_documents(
                case["question"],
                candidates,
                top_k=5,
            )

            reranking_seconds = perf_counter() - started

            scored = score_documents(reranked, expected)
            scored["query_seconds"] = (
                retrieval_seconds + reranking_seconds
            )
            scored["reranking_seconds"] = reranking_seconds

            result["reranked"] = scored

        results.append(result)

    checked = len(candidate_hits)
    if not checked:
        raise ValueError("No answerable cases were evaluated.")

    baseline_metrics = summarize(results, "baseline")
    current_metrics = (
        summarize(results, "reranked")
        if args.rerank
        else baseline_metrics
    )

    report = {
        "generated_at": now(),
        "protocol": "phase7-shared-candidates-v1",
        "dataset": str(args.questions),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "reference_type": dataset.get("kind", "seed_references"),
        "clinical_validation": False,
        "index": configuration,
        "retrieval_settings": {
            "candidate_k": 20,
            "final_k": 5,
            "semantic_candidates": 20,
            "keyword_candidates": 20,
            "fusion": "reciprocal_rank_fusion",
            "rrf_k": 60,
        },
        "variant": "hybrid_rerank" if args.rerank else "hybrid",
        "reranker": reranker_settings() if args.rerank else None,
        "environment": {
            package: version(package)
            for package in [
                "langchain-core",
                "langchain-postgres",
                "sentence-transformers",
                "transformers",
                "torch",
            ]
        },
        "timing": (
            "Warm-model retrieval plus optional local reranking; "
            "excludes model loading and answer generation."
        ),
        "checked_cases": checked,
        "candidate_hit_at_20_percent": (
            100 * sum(candidate_hits) / checked
        ),
        "baseline_metrics": baseline_metrics,
        "metrics": current_metrics,
        "improvements": (
            calculate_changes(baseline_metrics, current_metrics)
            if args.rerank
            else None
        ),
        "passed": current_metrics["hit_at_5_percent"] == 100,
        "cases": results,
    }

    report_path = args.report or (
        ROOT
        / "reports"
        / f"retrieval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with report_path.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)
        file.write("\n")

    print("Baseline:")
    print(json.dumps(baseline_metrics, indent=2))

    if args.rerank:
        print("\nWith reranking:")
        print(json.dumps(current_metrics, indent=2))
        print("\nChanges:")
        print(json.dumps(report["improvements"], indent=2))

    print(f"\nReport: {report_path}")

    # A completed benchmark is not required to score 100%.
    # Actual execution/validation errors still raise exceptions.
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=SEEDS)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--rerank", action="store_true",
        help="Compare hybrid ranking with cross-encoder reranking.",
    )
    args = parser.parse_args()
    args.questions = args.questions.resolve()
    if args.report is not None:
        args.report = args.report.resolve()
    return evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
