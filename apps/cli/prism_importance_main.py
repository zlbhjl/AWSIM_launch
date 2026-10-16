#!/usr/bin/env python3
"""Run fixed-Q or human-bounded proposal-search PRISM experiments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestration.prism_importance_sampling import PrismImportanceSamplingRunConfig, PrismImportanceSamplingRunner
from targets.prism.backend import PrismBackend, PrismBackendConfig
from targets.prism.result_interpreter import PrismResultInterpreter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PRISM importance-sampling experiments")
    parser.add_argument("--sampling-plan", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--proposal-mode", choices=("fixed_samples", "suggest"), default="fixed_samples")
    parser.add_argument(
        "--stop-mode",
        choices=("fixed_samples", "importance_width", "weighted_sequential"),
        default="fixed_samples",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--sampling-seed", type=int, default=None)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--target-width", type=float, default=None)
    parser.add_argument("--null-upper", type=float, default=None)
    parser.add_argument("--alternative-lower", type=float, default=None)
    parser.add_argument("--prism-executable", default="prism")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.proposal_mode == "fixed_samples" and args.max_samples is None:
        build_parser().error("--max-samples is required when --proposal-mode=fixed_samples")
    if args.proposal_mode == "suggest" and args.stop_mode != "fixed_samples":
        build_parser().error("--stop-mode is available only for fixed-Q experiments")
    if args.stop_mode == "importance_width" and args.target_width is None:
        build_parser().error("--target-width is required when --stop-mode=importance_width")
    backend = PrismBackend(PrismBackendConfig(output_root=args.output_root / "artifacts", prism_executable=args.prism_executable))
    runner = PrismImportanceSamplingRunner(backend=backend, result_interpreter=PrismResultInterpreter())
    config = PrismImportanceSamplingRunConfig(
        sampling_plan_path=args.sampling_plan, output_root=args.output_root,
        max_samples=args.max_samples, confidence=args.confidence, target_width=args.target_width,
        stop_mode=args.stop_mode,
        sequential_options={
            key: value
            for key, value in {
                "null_upper": args.null_upper,
                "alternative_lower": args.alternative_lower,
            }.items()
            if value is not None
        },
        seed=args.sampling_seed, params={"model": "simple_reliability_dtmc", "steps": args.steps},
    )
    result = (
        runner.run_suggest_pilot(config)
        if args.proposal_mode == "suggest"
        else runner.run(config)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
