#!/usr/bin/env python3
"""Run a fixed-P/fixed-Q importance-sampling experiment for any target.

The target comes from the sampling plan (``target:``); its plugin adds its own
options and runs the experiment (``contracts/importance_sampling_plugin.py``).

    python3 -m apps.cli.importance_main --sampling-plan PLAN --output-root DIR --max-samples N [target options]

``--help`` together with ``--sampling-plan`` also lists the target's options.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from contracts.importance_sampling_plugin import ImportanceSamplingSupport, importance_sampling_support
from orchestration.sampling_plan import load_sampling_plan
from targets.registry import get_target_plugin


def build_parser(support: ImportanceSamplingSupport | None = None, *, target: str | None = None) -> argparse.ArgumentParser:
    description = "Run a fixed-P/fixed-Q importance-sampling experiment"
    parser = argparse.ArgumentParser(description=description + (f" (target={target})" if target else ""))
    parser.add_argument("--sampling-plan", required=True, help="sampling plan YAML; its target selects the plugin")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=None, help="samples per run kind (ordinary and importance)")
    parser.add_argument("--sampling-seed", type=int, default=None)
    parser.add_argument("--metric", default=None, help="binary metric; default: the target's")
    parser.add_argument("--confidence", type=float, default=0.95)
    if support is not None:
        support.add_arguments(parser)
    return parser


def support_for_target(target: str) -> ImportanceSamplingSupport:
    support = importance_sampling_support(get_target_plugin(target))
    if support is None:
        raise ValueError(f"target {target!r} does not support importance sampling (its plugin has no importance_sampling)")
    return support


def run_for_target(target: str, argv: Sequence[str] | None = None) -> int:
    """Parse the options of ``target`` and run its experiment."""
    support = support_for_target(target)
    parser = build_parser(support, target=target)
    args = parser.parse_args(argv)
    try:
        result = support.run(args)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--sampling-plan")
    known, _ = pre.parse_known_args(arguments)
    if known.sampling_plan is None:
        build_parser().parse_args(arguments)  # prints help or the missing-option error
        return 2
    plan = load_sampling_plan(known.sampling_plan)
    try:
        support_for_target(plan.target)
    except ValueError as exc:
        build_parser().error(str(exc))
    return run_for_target(plan.target, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
