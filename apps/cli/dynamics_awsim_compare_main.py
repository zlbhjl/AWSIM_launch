"""CLI for a single, input-aligned dynamics versus AWSIM comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

from orchestration.dynamics_awsim_comparison import (
    ComparisonCase,
    DynamicsAWSIMComparisonRunner,
)
from runtime.cluster.result_sink import JsonlResultSink, SharedStoreResultSink, serialize_evaluation_record
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter
from targets.registry import build_target_components


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare one U-turn ODE case with AWSIM.")
    parser.add_argument("--comparison-id", default="uturn_compare_direct")
    parser.add_argument("--dx0", required=True, type=float)
    parser.add_argument("--ego-speed", required=True, type=float)
    parser.add_argument("--npc-speed", required=True, type=float)
    parser.add_argument("--jama-profile", default="ai_aeb", choices=["human", "ai_aeb"])
    parser.add_argument(
        "--decision-mode",
        choices=["judgment", "screening"],
        default="judgment",
        help="judgment: ODE collision as AWSIM substitute; screening: ODE candidate for AWSIM re-validation.",
    )
    parser.add_argument("--output", required=True, help="Comparison JSONL output path.")
    parser.add_argument("--dataset-csv", default=None, help="Optional comparison dataset CSV path.")
    parser.add_argument("--dynamics-output-root", default="artifacts/dynamics_compare")
    parser.add_argument("--headless", action="store_true", help="Run AWSIM headlessly.")
    parser.add_argument("--ext-mode", default="cvm")
    parser.add_argument("--container-profile", default=None)
    parser.add_argument("--scenario-profile", default=None)
    return parser


def run_compare(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    case = ComparisonCase(
        comparison_id=args.comparison_id,
        case_kind="uturn",
        inputs={"dx0": args.dx0, "ego_speed": args.ego_speed, "npc_speed": args.npc_speed},
        jama_profile=args.jama_profile,
        decision_mode=args.decision_mode,
    )
    awsim_args = SimpleNamespace(
        target="awsim", case_kind="uturn", config_module="targets.awsim.case_kinds.uturn",
        headless=args.headless, ext_mode=args.ext_mode, container_profile=args.container_profile,
        scenario_profile=args.scenario_profile,
    )
    awsim = build_target_components(awsim_args)
    runner = DynamicsAWSIMComparisonRunner(
        dynamics_backend=DynamicsBackend(DynamicsBackendConfig(output_root=Path(args.dynamics_output_root))),
        dynamics_interpreter=DynamicsResultInterpreter(),
        awsim_backend=awsim.backend,
        awsim_interpreter=awsim.result_interpreter,
    )
    _, _, comparison = runner.run(case)
    JsonlResultSink(args.output).save(comparison)
    if args.dataset_csv:
        SharedStoreResultSink.from_dataset_csv(args.dataset_csv).save(comparison)
    print(json.dumps(serialize_evaluation_record(comparison), ensure_ascii=False))
    return 0 if comparison.status.value == "success" else 1


def main(argv: Sequence[str] | None = None) -> int:
    return run_compare(argv)


if __name__ == "__main__":
    raise SystemExit(main())
