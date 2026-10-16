"""Target-neutral fixed-sample P/Q comparison execution.

Targets retain responsibility for interpreting P/Q and building ``TestCase``
objects.  This module only owns the repeated ordinary/importance execution,
separate persistence, and common comparison sequence.
"""

from __future__ import annotations

from collections.abc import Callable

from contracts.execution import TestCase
from evaluation.importance_sampling_comparison import compare_sampling_methods
from orchestration.worker_loop import WorkerLoop
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository


def run_fixed_importance_sampling_comparison(
    *,
    backend: object,
    result_interpreter: object,
    repository: ImportanceSamplingResultsRepository,
    max_samples: int,
    build_case: Callable[[str, int], TestCase],
    metric: str,
    confidence: float,
    plan_id: str,
    target_width: float | None = None,
) -> dict[str, object]:
    """Resume and compare fixed-size ordinary and importance runs."""

    if max_samples < 1:
        raise ValueError("max_samples must be positive")
    for run_kind in ("ordinary", "importance"):
        _run_missing(
            backend=backend,
            result_interpreter=result_interpreter,
            repository=repository,
            run_kind=run_kind,
            existing_count=len(repository.load_records(run_kind)),
            max_samples=max_samples,
            build_case=build_case,
        )
    comparison = compare_sampling_methods(
        repository.load_records("ordinary"),
        repository.load_records("importance"),
        metric=metric,
        confidence=confidence,
        target_width=target_width,
    )
    comparison.update({"plan_id": plan_id, "stop_mode": "fixed_samples"})
    comparison_path = repository.save_comparison(comparison)
    return {"comparison": comparison, "comparison_path": str(comparison_path)}


def _run_missing(
    *,
    backend: object,
    result_interpreter: object,
    repository: ImportanceSamplingResultsRepository,
    run_kind: str,
    existing_count: int,
    max_samples: int,
    build_case: Callable[[str, int], TestCase],
) -> None:
    loop = WorkerLoop(
        backend=backend,
        result_interpreter=result_interpreter,
        result_sink=lambda record: repository.append_record(run_kind, record),
    )
    for index in range(existing_count + 1, max_samples + 1):
        loop.run_once(build_case(run_kind, index))


__all__ = ["run_fixed_importance_sampling_comparison"]
