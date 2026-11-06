"""Run one fixed-N importance-sampling experiment of any target, for the validation tools.

The tools (pilot tuning, coverage, reference comparison, plain-MC reference)
choose the target from the plan's ``target:`` and use the shared runner with the
target's hooks and its default CLI options (docs/blackbox_importance_sampling_plan.md §5.4).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from apps.cli.importance_main import build_parser
from orchestration.importance.runner import ImportanceRunConfig, ImportanceSamplingRunner, hooks_for_target
from orchestration.sampling_plan import SamplingPlan, load_sampling_plan

# Option values the validation tools use whenever a target has the option:
# keep results in memory instead of writing every trajectory.
TOOL_OPTIONS = {"artifacts": "summary"}


def target_args(plan_path: str | Path, workdir: str | Path, **overrides: object) -> argparse.Namespace:
    """The target's CLI options at their defaults (plus the tool options and ``overrides``)."""
    plan = load_sampling_plan(plan_path)
    hooks = hooks_for_target(plan.target)
    args = build_parser(hooks, target=plan.target).parse_args(
        ["--sampling-plan", str(plan_path), "--output-root", str(workdir)]
    )
    for name, value in {**TOOL_OPTIONS, **overrides}.items():
        if hasattr(args, name):
            setattr(args, name, value)
    return args


def run_plan_fixed(
    plan_path: str | Path,
    *,
    workdir: str | Path,
    max_samples: int,
    phase: str = "final",
    root_entropy: int | None = None,
    seed: int | None = None,
    options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run ``plan_path`` with N ordinary and N importance samples; return the comparison.

    The backend keeps its artifacts under ``workdir/artifacts`` and the records go
    to ``workdir/run`` (a fresh ``workdir`` per run: the runner resumes from it).
    """
    plan = load_sampling_plan(plan_path)
    hooks = hooks_for_target(plan.target)
    args = target_args(plan_path, workdir, **dict(options or {}))
    run_options = getattr(hooks, "run_options", None)
    values = dict(run_options(args)) if callable(run_options) else {}
    components = hooks.build_components(args)
    try:
        runner = ImportanceSamplingRunner(
            backend=components.backend, result_interpreter=components.result_interpreter, hooks=hooks,
        )
        return runner.run_fixed_samples(ImportanceRunConfig(
            sampling_plan_path=plan_path,
            output_root=Path(workdir) / "run",
            max_samples=max_samples,
            phase=phase,
            root_entropy=root_entropy,
            seed=seed,
            params=values.get("params", {}),
            options=values.get("options", {}),
        ))["comparison"]
    finally:
        close = getattr(components.backend, "close", None)
        if callable(close):
            close()


def exact_probability(plan_path: str | Path) -> float:
    """The exact probability of the plan's event, if its target can compute it."""
    plan = load_sampling_plan(plan_path)
    hook = getattr(hooks_for_target(plan.target), "exact_probability", None)
    if not callable(hook):
        raise ValueError(f"target {plan.target!r} gives no exact probability (hook exact_probability)")
    return float(hook(plan))


def check_plan(plan: SamplingPlan) -> None:
    """Let the target check a plan before anything runs (its ``prepare``)."""
    hooks_for_target(plan.target).prepare(plan, None)


__all__ = ["check_plan", "exact_probability", "run_plan_fixed", "target_args"]
