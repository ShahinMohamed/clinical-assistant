"""Command-line entry point for the unified research assistant."""

import argparse
import json
from datetime import date

from assistant.graph import build_assistant
from rag.retrieval_pipeline import (
    add_feature_arguments,
    feature_options,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("question")
    parser.add_argument(
        "--analysis-date",
        type=date.fromisoformat,
        help="Database reference date, YYYY-MM-DD. Defaults to today in UTC.",
    )
    parser.add_argument(
        "--run-id",
        type=int,
        help="Use a particular Synthea ingestion run.",
    )

    add_feature_arguments(parser)
    args = parser.parse_args()

    if args.web and not args.crag:
        parser.error("--web requires --crag.")

    if args.run_id is not None and args.run_id < 1:
        parser.error("--run-id must be a positive integer.")

    assistant = build_assistant(**feature_options(args))

    result = assistant(
        args.question,
        analysis_date=args.analysis_date,
        run_id=args.run_id,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()