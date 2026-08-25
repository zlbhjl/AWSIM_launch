from __future__ import annotations

import argparse
from typing import Sequence

from runtime.repository.consistency_dkw_summary import (
    ConsistencyDkwSummaryRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the latest verify_consistency DKW summary CSV and print "
            "a human-readable report for consistent/stochastic classifications."
        )
    )
    parser.add_argument(
        "--scenario-name",
        "--type",
        dest="scenario_name",
        default="uturn",
        help="Scenario name used to resolve <scenario>_consistency_dkw_summary.csv.",
    )
    parser.add_argument(
        "--traces-dir",
        default="~/simulation_traces",
        help="Directory containing the consistency DKW summary CSV.",
    )
    parser.add_argument(
        "--summary-csv",
        default=None,
        help="Optional explicit path to a consistency DKW summary CSV.",
    )
    return parser


def format_classification_block(
    classification: str,
    row: dict[str, str],
) -> str:
    title = (
        "Consistent Risk"
        if classification == "consistent"
        else "Stochastic Risk"
        if classification == "stochastic"
        else classification
    )
    lines = [f"--- {title} ---"]
    lines.append(f"status       : {row.get('status', '')}")
    lines.append(f"metric       : {row.get('metric', '')}")
    lines.append(f"sample count : {row.get('sample_count', '')}")
    lines.append(f"confidence   : {row.get('confidence_level', '')}")
    estimate = row.get("estimate", "")
    lower = row.get("lower_bound", "")
    upper = row.get("upper_bound", "")
    width = row.get("interval_width", "")
    target = row.get("target_epsilon", "")
    if estimate:
        lines.append(f"estimate     : {estimate}")
    if lower or upper:
        lines.append(f"interval     : [{lower}, {upper}]")
    if width:
        lines.append(f"width        : {width} (target <= {target})")
    lines.append(f"next action  : {row.get('next_action', '')}")
    message = row.get("message", "")
    if message:
        lines.append(f"message      : {message}")
    return "\n".join(lines)


def render_consistency_summary(
    scenario_name: str,
    rows_by_classification: dict[str, dict[str, str]],
) -> str:
    lines = ["=" * 60]
    lines.append("AWSIM_launch verify_consistency DKW summary")
    lines.append(f"scenario     : {scenario_name}")
    if not rows_by_classification:
        lines.append("status       : no summary rows found")
        lines.append("=" * 60)
        return "\n".join(lines)

    for classification in ("consistent", "stochastic"):
        row = rows_by_classification.get(classification)
        if row is None:
            continue
        lines.append("")
        lines.append(format_classification_block(classification, row))
    lines.append("=" * 60)
    return "\n".join(lines)


def run_consistency_summary(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    repository = ConsistencyDkwSummaryRepository(
        scenario_name=args.scenario_name,
        traces_dir=args.traces_dir,
        summary_csv=args.summary_csv,
    )
    rows_by_classification = repository.read_latest_rows_by_classification()
    print(render_consistency_summary(args.scenario_name, rows_by_classification))
    return 0 if rows_by_classification else 1


def main(argv: Sequence[str] | None = None) -> int:
    return run_consistency_summary(argv)


if __name__ == "__main__":
    raise SystemExit(main())
