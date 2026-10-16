from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from apps.cli.ft4d_smoke_main import render_tree_report, save_output_json
from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.ft4d_service import run_ft4d
from targets.prism.verification_input import (
    TREE_PATH,
    build_verification_input_from_records,
)


LAUNCH_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TREE_PATH = TREE_PATH
DEFAULT_RECORDS_JSONL_PATH = (
    LAUNCH_DIR
    / "tests"
    / "fixtures"
    / "prism"
    / "prism_stage2_baseline_sample41.jsonl"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the PRISM smoke path through the new pipeline: "
            "records jsonl -> verification_input -> ft4d_service."
        )
    )
    parser.add_argument(
        "--tree",
        default=str(DEFAULT_TREE_PATH),
        help="Path to the FT4D JSON tree config inside AWSIM_launch.",
    )
    parser.add_argument(
        "--records-jsonl",
        default=str(DEFAULT_RECORDS_JSONL_PATH),
        help=(
            "Path to a JSONL file of PRISM EvaluationRecords (one per line), "
            "e.g. a batch produced by run_orchestrator_cluster_v2.py --target prism."
        ),
    )
    parser.add_argument(
        "--sigma-pf-source",
        choices=["dataset", "assumption"],
        default="dataset",
        help="sigma_pf source passed to the FT4D service.",
    )
    parser.add_argument(
        "--and-rule",
        choices=["min", "product"],
        default="min",
        help="AND combination rule for the FT4D smoke run.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to save the FT4D smoke result JSON.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(list(argv) if argv is not None else None)


def load_records(records_jsonl_path: str | Path) -> list[EvaluationRecord]:
    resolved_path = Path(records_jsonl_path).expanduser().resolve()
    records: list[EvaluationRecord] = []
    with resolved_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            records.append(
                EvaluationRecord(
                    case_id=payload["case_id"],
                    target=payload["target"],
                    case_kind=payload["case_kind"],
                    status=RunStatus(payload["status"]),
                    input=payload["input"],
                    output=payload["output"],
                    evidence=payload.get("evidence", {}),
                    meta=payload["meta"],
                )
            )
    return records


def run_smoke(
    records_jsonl_path: str | Path,
    tree_path: str | Path,
    sigma_pf_source: str,
    and_rule: str,
    *,
    load_records_fn: Callable[[str | Path], list[EvaluationRecord]] = load_records,
    build_verification_input_fn: Callable[..., object] = build_verification_input_from_records,
    ft4d_runner: Callable[[object], object] = run_ft4d,
) -> dict[str, object]:
    resolved_records_path = Path(records_jsonl_path).expanduser().resolve()
    resolved_tree_path = Path(tree_path).expanduser().resolve()

    records = load_records_fn(resolved_records_path)
    verification_input = build_verification_input_fn(
        records,
        assumptions={
            "tree_path": str(resolved_tree_path),
            "sigma_pf_source": sigma_pf_source,
            "and_rule": and_rule,
            "sigma_pf_assumptions": {
                "FAILURE": 1.0,
                "EARLY_FAILURE": 1.0,
                "REPEATED_DEGRADATION": 1.0,
            },
        },
    )
    ft4d_result = ft4d_runner(verification_input)
    tree_report = ft4d_result.raw_result["tree_report"]

    return {
        "records_jsonl_path": str(resolved_records_path),
        "tree_path": str(resolved_tree_path),
        "record_count": verification_input.meta["record_count"],
        "eligible_record_count": verification_input.meta["eligible_record_count"],
        "verification_input": {
            "tree_mode": verification_input.tree_mode,
            "universal_dataset_size": len(verification_input.universal_dataset),
            "events": {
                event_id: {
                    key: value
                    for key, value in metrics.items()
                    if key not in {"dataset_d", "dataset_e"}
                }
                for event_id, metrics in verification_input.events.items()
            },
            "assumptions": verification_input.assumptions,
            "meta": verification_input.meta,
        },
        "sigma_pf_source": ft4d_result.raw_result["sigma_pf_source"],
        "and_rule": ft4d_result.raw_result["and_rule"],
        "tree_report": tree_report,
        "rendered_tree": render_tree_report(tree_report),
        "top_sigma_pe": ft4d_result.top_sigma_pe,
        "confidence": ft4d_result.confidence,
        "node_summaries": ft4d_result.node_summaries,
    }


def print_summary(result: dict[str, object]) -> None:
    print("=" * 60)
    print("AWSIM_launch internal PRISM FT4D smoke run")
    print(f"records jsonl : {result['records_jsonl_path']}")
    print(f"tree path     : {result['tree_path']}")
    print(f"records       : {result['eligible_record_count']} / {result['record_count']} eligible")
    print(f"sigma_pf      : {result['sigma_pf_source']}")
    print(f"and rule      : {result['and_rule']}")
    print(f"top sigma_pe  : {result['top_sigma_pe']:.6f}")
    if result["confidence"] is not None:
        print(f"confidence    : {result['confidence']:.6f}")
    print()
    print(result["rendered_tree"])
    print("=" * 60)


def run_prism_ft4d_smoke(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke(
        records_jsonl_path=args.records_jsonl,
        tree_path=args.tree,
        sigma_pf_source=args.sigma_pf_source,
        and_rule=args.and_rule,
    )
    print_summary(result)
    if args.output_json:
        output_path = save_output_json(args.output_json, result)
        print(f"Saved smoke result to {output_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_prism_ft4d_smoke(argv)
