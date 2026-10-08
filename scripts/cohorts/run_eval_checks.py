"""Compare executed reference SQL with Text2SQL patient counts."""

import argparse
import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter

import yaml

from cohort.sql_agent import (
    agent_settings,
    build_sql_agent,
    get_dataset,
    get_engine,
)


ROOT = Path(__file__).resolve().parents[2]

QUESTIONS_FILE = ROOT / "evals/cohort-ground-truth.yaml"
SQL_FILE = ROOT / "db/eval_queries.sql"


def read_queries(sql_text):
    """Read SQL blocks marked with: -- name: cohort_001"""

    markers = list(
        re.finditer(
            r"^-- name:\s*([a-z0-9_]+)\s*$",
            sql_text,
            re.MULTILINE,
        )
    )

    queries = {}

    for index, marker in enumerate(markers):
        name = marker.group(1)

        end = (
            markers[index + 1].start()
            if index + 1 < len(markers)
            else len(sql_text)
        )

        query = sql_text[marker.end():end].strip()

        if not query:
            raise ValueError(f"Empty SQL query: {name}")

        if name in queries:
            raise ValueError(f"Duplicate SQL query: {name}")

        queries[name] = query

    return queries


def execute_baseline(engine, sql, run_id, analysis_date):
    """Execute reference SQL instead of using saved expected counts."""

    parameters = {
        "run_id": run_id,
        "analysis_date": analysis_date,
    }

    started = perf_counter()

    with engine.connect() as connection:
        # Reference queries use psycopg-style %(name)s parameters.
        rows = connection.exec_driver_sql(
            sql,
            parameters,
        ).mappings().all()

    seconds = perf_counter() - started

    if len(rows) != 1 or "expected_count" not in rows[0]:
        raise ValueError(
            "Reference SQL must return one row with expected_count."
        )

    count = rows[0]["expected_count"]

    if type(count) is not int or count < 0:
        raise ValueError(
            "Reference SQL must return a non-negative integer count."
        )

    return {
        "status": "completed",
        "count": count,
        "query_seconds": seconds,
        "sql": sql,
        "parameters": {
            "run_id": run_id,
            "analysis_date": analysis_date.isoformat(),
        },
    }


def calculate_improvements(baseline, current):
    changes = {}

    for metric, before in baseline.items():
        after = current[metric]
        difference = after - before
        lower_is_better = metric.endswith("_seconds")

        improvement = -difference if lower_is_better else difference

        changes[metric] = {
            "baseline": round(before, 4),
            "new": round(after, 4),
            "absolute_change": round(difference, 4),
            "percentage_point_change": (
                round(difference, 4)
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


def evaluate_text2sql(analysis_date, report_path):
    question_bytes = QUESTIONS_FILE.read_bytes()
    sql_bytes = SQL_FILE.read_bytes()

    benchmark = yaml.safe_load(question_bytes)
    cases = benchmark["cases"]
    saved_dataset = benchmark["dataset"]

    if saved_dataset.get("synthetic") is not True:
        raise ValueError("Evaluation requires a synthetic dataset.")

    if not cases:
        raise ValueError("No evaluation questions found.")

    ids = [case["id"] for case in cases]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evaluation case IDs.")

    queries = read_queries(sql_bytes.decode("utf-8"))

    for case in cases:
        if case["id"] not in queries:
            raise ValueError(f"No reference SQL for {case['id']}.")

        if not isinstance(case["question"], str):
            raise ValueError(f"Invalid question for {case['id']}.")

        if not case["question"].strip():
            raise ValueError(f"Empty question for {case['id']}.")

    report_path = Path(report_path).resolve()

    if report_path.exists():
        raise FileExistsError(
            f"Report already exists: {report_path}. "
            "Choose a new filename."
        )

    run_id = saved_dataset["run_id"]

    if type(run_id) is not int or run_id < 1:
        raise ValueError("Dataset run_id must be a positive integer.")

    engine = get_engine()

    dataset = get_dataset(
        engine,
        analysis_date,
        run_id,
        saved_dataset["dataset_fingerprint"],
    )

    fingerprint = dataset["dataset_fingerprint"]

    # Run every reference first. A broken baseline stops evaluation.
    baselines = {
        case["id"]: execute_baseline(
            engine,
            queries[case["id"]],
            run_id,
            analysis_date,
        )
        for case in cases
    }

    # The agent receives neither reference SQL nor reference counts.
    agent = build_sql_agent()
    results = []

    for case in cases:
        baseline = baselines[case["id"]]
        started = perf_counter()

        try:
            result = agent(
                case["question"],
                analysis_date=analysis_date.isoformat(),
                run_id=run_id,
                expected_fingerprint=fingerprint,
                generate_answer=False,
            )

        except Exception as error:
            result = {
                "status": "error",
                "count": None,
                "error_type": type(error).__name__,
            }

        seconds = perf_counter() - started
        actual_count = result.get("count")

        same_context = (
            result.get("status") == "completed"
            and result.get("analysis_date") == analysis_date.isoformat()
            and result.get("dataset", {}).get("run_id") == run_id
            and result.get("dataset", {}).get(
                "dataset_fingerprint"
            ) == fingerprint
        )

        matched = (
            same_context
            and type(actual_count) is int
            and actual_count == baseline["count"]
            and not result.get("truncated", False)
        )

        results.append({
            "id": case["id"],
            "question": case["question"],
            "baseline": baseline,
            "text2sql": {
                **result,
                "query_seconds": seconds,
            },
            "same_context": same_context,
            "result_matches_baseline": matched,
            "count_difference": (
                actual_count - baseline["count"]
                if type(actual_count) is int
                else None
            ),
            "passed": matched,
        })

        print(
            f"{case['id']}: "
            f"SQL={baseline['count']}, "
            f"Text2SQL={actual_count}, "
            f"{'PASS' if matched else 'FAIL'}"
        )

    total = len(results)

    baseline_metrics = {
        "query_success_percent": 100.0,
        "result_match_percent": 100.0,
        "mean_query_seconds": (
            sum(item["baseline"]["query_seconds"] for item in results)
            / total
        ),
    }

    current_metrics = {
        "query_success_percent": (
            100 * sum(
                item["text2sql"]["status"] == "completed"
                for item in results
            ) / total
        ),
        "result_match_percent": (
            100 * sum(item["passed"] for item in results) / total
        ),
        "mean_query_seconds": (
            sum(item["text2sql"]["query_seconds"] for item in results)
            / total
        ),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": "reference-sql-vs-text2sql-v1",
        "analysis_date": analysis_date.isoformat(),
        "dataset": dataset,
        "clinical_validation": False,
        "evaluation_scope": "Patient-count agreement with reference SQL.",
        "saved_expected_counts_used": False,
        "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        "baseline_sql_sha256": hashlib.sha256(sql_bytes).hexdigest(),
        "settings": agent_settings(),
        "checked_cases": total,
        "baseline_metrics": baseline_metrics,
        "metrics": current_metrics,
        "improvements": calculate_improvements(
            baseline_metrics,
            current_metrics,
        ),
        "timing_note": (
            "Baseline timing measures direct SQL execution. "
            "Text2SQL timing includes schema retrieval, model calls, "
            "checking, SQL execution, and any retries."
        ),
        "cases": results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)

    with report_path.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)
        file.write("\n")

    print(f"\nReport: {report_path}")
    print(
        f"Result agreement: "
        f"{current_metrics['result_match_percent']:.1f}%"
    )

    return 0 if all(item["passed"] for item in results) else 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        allow_abbrev=False,
    )

    parser.add_argument(
        "--evaluate-text2sql",
        action="store_true",
        required=True,
        help="Compare Text2SQL results against executed reference SQL.",
    )

    parser.add_argument(
        "--analysis-date",
        type=date.fromisoformat,
        required=True,
        help="Explicit evaluation date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--text2sql-report",
        type=Path,
        default=ROOT / "reports" / (
            "text2sql-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        ),
    )

    args = parser.parse_args()

    try:
        return evaluate_text2sql(
            args.analysis_date,
            args.text2sql_report,
        )

    except Exception as error:
        parser.exit(2, f"Evaluation stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())