#!/usr/bin/env python3
"""Run fixed-P/fixed-Q input importance sampling for the dynamics target."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
)
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed-Q input importance sampling for U-turn dynamics"
    )
    parser.add_argument("--sampling-plan", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, required=True)
    parser.add_argument("--sampling-seed", type=int, default=None)
    parser.add_argument("--metric", default="c_collision")
    parser.add_argument("--confidence", type=float, default=0.95)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = DynamicsBackend(
        DynamicsBackendConfig(output_root=args.output_root / "artifacts")
    )
    runner = DynamicsImportanceSamplingRunner(
        backend=backend,
        result_interpreter=DynamicsResultInterpreter(),
    )
    result = runner.run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=args.sampling_plan,
            output_root=args.output_root,
            max_samples=args.max_samples,
            metric=args.metric,
            confidence=args.confidence,
            seed=args.sampling_seed,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
