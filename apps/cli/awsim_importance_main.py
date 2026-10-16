#!/usr/bin/env python3
"""Run fixed-P/fixed-Q AWSIM U-turn input importance sampling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from orchestration.awsim_importance_sampling import (
    AWSIMImportanceSamplingRunConfig,
    AWSIMImportanceSamplingRunner,
)
from targets.registry import build_target_components


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed-Q AWSIM input importance sampling")
    parser.add_argument("--sampling-plan", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, required=True)
    parser.add_argument("--sampling-seed", type=int, default=None)
    parser.add_argument("--metric", default="c_collision")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--container-profile", default="autoware171")
    parser.add_argument("--scenario-profile", default="autoware171")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--reuse-infra-between-samples",
        action="store_true",
        help=(
            "Reuse the AWSIM infrastructure between samples. Disabled by default "
            "so that each importance-sampling record starts from a clean world."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Reuse the normal AWSIM component assembly.  In particular this retains
    # managed infrastructure, readiness probes, and the verified profile
    # settings instead of creating a second container startup path here.
    components = build_target_components(
        SimpleNamespace(
            target="awsim",
            case_kind="uturn",
            headless=args.headless,
            container_profile=args.container_profile,
            scenario_profile=args.scenario_profile,
            config_module=None,
            ext_mode="cvm",
            # A raw trace may be produced even when its later semantic
            # interpretation rejects the case.  Do not retain that world for
            # the next importance-sampling record unless explicitly requested.
            fresh_infra_per_sample=not args.reuse_infra_between_samples,
        )
    )
    runner = AWSIMImportanceSamplingRunner(
        backend=components.backend,
        result_interpreter=components.result_interpreter,
    )
    try:
        result = runner.run_fixed_samples(
            AWSIMImportanceSamplingRunConfig(
                sampling_plan_path=args.sampling_plan,
                output_root=args.output_root,
                max_samples=args.max_samples,
                metric=args.metric,
                confidence=args.confidence,
                seed=args.sampling_seed,
                scenario_profile=args.scenario_profile,
            )
        )
    finally:
        close = getattr(components.backend, "close", None)
        if callable(close):
            close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
