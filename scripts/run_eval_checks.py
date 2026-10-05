import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from dotenv import load_dotenv

import psycopg
import yaml
from psycopg.rows import dict_row

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

QUERY_FILE = Path("db/eval_queries.sql")
EVAL_OUTPUT = Path("evals/cohort-ground-truth.yaml")
REPORT_OUTPUT = Path("reports/data-quality.md")


ANALYSIS_DATE = "2026-10-04"
FRESHNESS_THRESHOLD_DAYS = 7


QUESTIONS = {
    "cohort_001": "How many synthetic patients are in the dataset?",
    "cohort_002": "How many synthetic patients are adults?",
    "cohort_003": "How many synthetic adults have active type 2 diabetes?",
    "cohort_004": "How many synthetic adults have active hypertension?",
    "cohort_005": (
        "How many synthetic adults have both type 2 diabetes "
        "and hypertension?"
    ),
    "cohort_006": (
        "How many synthetic patients have at least one dated "
        "HbA1c observation?"
    ),
    "cohort_007": (
        "How many synthetic adults with type 2 diabetes have an "
        "HbA1c observation in the last 12 months?"
    ),
    "cohort_008": (
        "How many synthetic adults with type 2 diabetes have no "
        "HbA1c observation in the last 12 months?"
    ),
    "cohort_009": (
        "How many synthetic adults with type 2 diabetes and "
        "hypertension have no HbA1c observation in the last "
        "12 months?"
    ),
    "cohort_010": (
        "How many synthetic patients have no birth date?"
    ),
}


REQUIRED_NON_EMPTY = [
    "raw_resources",
    "patients",
    "conditions",
    "observations",
    "type_2_diabetes_conditions",
    "hba1c_observations",
    "blood_pressure_observations",
]


ZERO_REQUIRED = [
    "orphan_conditions",
    "orphan_observations",
    "conditions_missing_codes",
    "observations_missing_codes",
    "hba1c_missing_dates",
    "hba1c_missing_units",
    "observations_before_birth",
    "onset_after_abatement",
]


def read_queries(sql_text):
    marker = re.compile(
        r"^-- name:\s*([a-z0-9_]+)\s*$",
        re.MULTILINE,
    )

    matches = list(marker.finditer(sql_text))
    queries = {}

    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()

        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            end = len(sql_text)

        query = sql_text[start:end].strip()

        if not query:
            raise ValueError(
                f"Query {name} is empty"
            )

        queries[name] = query

    return queries


def get_latest_run(cursor):
    cursor.execute(
        """
        SELECT
            run_id,
            dataset_fingerprint,
            source_name,
            as_of_date
        FROM ingestion_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC
        LIMIT 1
        """
    )

    run = cursor.fetchone()

    if not run:
        raise ValueError(
            "No completed ingestion run was found"
        )

    return run


def execute_queries(
    cursor,
    queries,
    run_id,
    analysis_date,
):
    parameters = {
        "run_id": run_id,
        "analysis_date": analysis_date,
    }

    results = {}

    for name, query in queries.items():
        cursor.execute(query, parameters)
        results[name] = cursor.fetchall()

    return results


def calculate_quality_status(
    quality,
    data_through_date,
):
    failures = []

    for field in REQUIRED_NON_EMPTY:
        actual = quality[field]

        if actual is None or actual <= 0:
            failures.append(
                f"{field}: expected at least one row, "
                f"got {actual}"
            )

    for field in ZERO_REQUIRED:
        actual = quality[field]

        if actual != 0:
            failures.append(
                f"{field}: expected 0, got {actual}"
            )

    if data_through_date is None:
        failures.append(
            "data_through_date is missing"
        )

    return failures


def write_evaluation_file(
    run,
    analysis_date,
    data_through_date,
    freshness_days,
    stale,
    results,
):
    cases = []

    for query_id, question in QUESTIONS.items():
        rows = results[query_id]

        if len(rows) != 1:
            raise ValueError(
                f"{query_id} did not return one row"
            )

        expected_count = rows[0]["expected_count"]

        cases.append(
            {
                "id": query_id,
                "question": question,
                "analysis_date": analysis_date.isoformat(),
                "expected_count": expected_count,
                "stale_data_warning": (
                    stale
                    if query_id
                    in {
                        "cohort_007",
                        "cohort_008",
                        "cohort_009",
                    }
                    else False
                ),
                "status": "verified",
            }
        )

    document = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "source": run["source_name"],
            "synthetic": True,
            "run_id": run["run_id"],
            "dataset_fingerprint": (
                run["dataset_fingerprint"]
            ),
            "analysis_date": analysis_date.isoformat(),
            "data_through_date": (
                data_through_date.isoformat()
                if data_through_date
                else None
            ),
            "freshness_days": freshness_days,
        },
        "cases": cases,
    }

    EVAL_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    EVAL_OUTPUT.write_text(
        yaml.safe_dump(
            document,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def write_report(
    run,
    analysis_date,
    data_through_date,
    freshness_days,
    freshness_threshold,
    stale,
    quality,
    status_rows,
    unit_rows,
    failures,
):
    overall_status = (
        "PASS" if not failures else "FAIL"
    )

    lines = [
        "# Data-Quality Report",
        "",
        f"- Status: **{overall_status}**",
        f"- Generated at: {datetime.now(UTC).isoformat()}",
        f"- Ingestion run: `{run['run_id']}`",
        (
            "- Dataset fingerprint: "
            f"`{run['dataset_fingerprint']}`"
        ),
        f"- Analysis date: `{analysis_date}`",
        f"- Data-through date: `{data_through_date}`",
        f"- Freshness: `{freshness_days}` days",
        "",
        "## Import reconciliation",
        "",
        "| Check | Expected | Actual | Status |",
        "|---|---:|---:|---|",
    ]

    for field in REQUIRED_NON_EMPTY:
        actual = quality[field]
        status = (
            "PASS"
            if actual is not None and actual > 0
            else "FAIL"
        )

        lines.append(
            f"| {field} | greater than 0 | "
            f"{actual} | {status} |"
        )

    lines.extend(
        [
            "",
            "## Required zero-value checks",
            "",
            "| Check | Required | Actual | Status |",
            "|---|---:|---:|---|",
        ]
    )

    for field in ZERO_REQUIRED:
        actual = quality[field]
        status = "PASS" if actual == 0 else "FAIL"

        lines.append(
            f"| {field} | 0 | {actual} | {status} |"
        )

    lines.extend(
        [
            "",
            "## Other data-quality information",
            "",
            (
                "- Patients missing birth date: "
                f"`{quality['patients_missing_birth_date']}`"
            ),
            (
                "- Earliest observation: "
                f"`{quality['earliest_observation']}`"
            ),
            (
                "- Latest observation: "
                f"`{quality['latest_observation']}`"
            ),
            (
                "- Observations after the analysis date: "
                f"`{quality['observations_after_analysis_date']}`"
            ),
            "",
            "## Condition statuses",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
    )

    for row in status_rows:
        lines.append(
            f"| {row['clinical_status']} | {row['count']} |"
        )

    lines.extend(
        [
            "",
            "## HbA1c units",
            "",
            "| Unit | Count |",
            "|---|---:|",
        ]
    )

    for row in unit_rows:
        lines.append(
            f"| {row['unit']} | {row['count']} |"
        )

    lines.extend(
        [
            "",
            "## Freshness assessment",
            "",
            (
                f"- Freshness threshold: "
                f"`{freshness_threshold}` days"
            ),
            f"- Dataset stale: `{'yes' if stale else 'no'}`",
        ]
    )

    if stale:
        lines.extend(
            [
                "",
                "> This dataset is not current. Missing recent "
                "observations may reflect delayed or discontinued "
                "data ingestion rather than missing clinical care.",
            ]
        )

    lines.extend(
        [
            "",
            "## Failures",
            "",
        ]
    )

    if failures:
        for failure in failures:
            lines.append(f"- {failure}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- All patient records are synthetic.",
            "- Counts are tied to the recorded analysis date.",
            "- Missing records do not prove missing clinical care.",
            "- Results are not real-world prevalence estimates.",
            "",
        ]
    )

    REPORT_OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_OUTPUT.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    sql_text = QUERY_FILE.read_text(
        encoding="utf-8"
    )

    analysis_date = date.fromisoformat(ANALYSIS_DATE)
    freshness_threshold = FRESHNESS_THRESHOLD_DAYS

    queries = read_queries(sql_text)

    required_queries = {
        "quality_summary",
        "condition_statuses",
        "hba1c_units",
        *QUESTIONS.keys(),
    }

    missing_queries = required_queries - queries.keys()

    if missing_queries:
        raise ValueError(
            "Missing queries: "
            + ", ".join(sorted(missing_queries))
        )

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION READ ONLY"
            )

            run = get_latest_run(cursor)

            results = execute_queries(
                cursor,
                queries,
                run["run_id"],
                analysis_date,
            )

    quality = results["quality_summary"][0]

    data_through_date = run["as_of_date"]

    if data_through_date:
        data_through_day = data_through_date.date()
        freshness_days = max(
            (analysis_date - data_through_day).days,
            0,
        )
    else:
        data_through_day = None
        freshness_days = None

    stale = (
        freshness_days is not None
        and freshness_days > freshness_threshold
    )

    failures = calculate_quality_status(
        quality,
        data_through_day,
    )

    write_evaluation_file(
        run,
        analysis_date,
        data_through_day,
        freshness_days,
        stale,
        results,
    )

    write_report(
        run,
        analysis_date,
        data_through_day,
        freshness_days,
        freshness_threshold,
        stale,
        quality,
        results["condition_statuses"],
        results["hba1c_units"],
        failures,
    )

    print(f"Evaluation: {EVAL_OUTPUT}")
    print(f"Report: {REPORT_OUTPUT}")

    if failures:
        print("\nEval check failed:")

        for failure in failures:
            print(f"- {failure}")

        return 1

    print("\nEval check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())