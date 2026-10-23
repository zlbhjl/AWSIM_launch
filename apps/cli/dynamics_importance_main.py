#!/usr/bin/env python3
"""Run fixed-P/fixed-Q importance sampling for the dynamics target.

A v1 plan samples U-turn inputs. A v2 plan (``dynamics_schema_version: 2``)
runs a registered model such as ``ou_scalar`` with a proposal on the Brownian
path; it writes an experiment manifest, resumes by sample id and reports
whether the fixed-N evaluation is complete. ``--phase``, ``--root-entropy``,
``--pairing`` and ``--retry-failed`` apply to v2 plans only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
)
from orchestration.seeding import PAIRINGS, PHASES
from targets.dynamics.backend import ARTIFACT_POLICIES, DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed-Q importance sampling for the dynamics target"
    )
    parser.add_argument("--sampling-plan", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, required=True)
    parser.add_argument("--sampling-seed", type=int, default=None)
    parser.add_argument(
        "--metric",
        default=None,
        help="binary metric; default: the model's (c_collision for the U-turn, c_event for OU)",
    )
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument(
        "--phase", choices=sorted(PHASES), default="final",
        help="v2: random-stream phase; pilot runs never share streams with final runs",
    )
    parser.add_argument(
        "--root-entropy", type=int, default=None,
        help="v2: 128-bit root of all random streams; default: reuse the manifest's, or draw a new one",
    )
    parser.add_argument(
        "--pairing", choices=list(PAIRINGS), default="independent",
        help="v2: 'paired' uses the same streams for the ordinary and importance runs",
    )
    parser.add_argument(
        "--artifacts", choices=list(ARTIFACT_POLICIES), default="files",
        help="registered models: 'summary' keeps trajectories in memory and stores only the judged records",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="v2: retry samples whose latest attempt failed, with the same inputs and streams",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = DynamicsBackend(
        DynamicsBackendConfig(output_root=args.output_root / "artifacts", artifact_policy=args.artifacts)
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
            phase=args.phase,
            root_entropy=args.root_entropy,
            pairing=args.pairing,
            retry_failed=args.retry_failed,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
