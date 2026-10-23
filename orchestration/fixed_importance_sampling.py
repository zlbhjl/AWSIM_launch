"""Target-neutral fixed-sample P/Q comparison execution.

Targets retain responsibility for interpreting P/Q and building ``TestCase``
objects.  This module only owns the repeated ordinary/importance execution,
separate persistence, and common comparison sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from contracts.evaluation import EvaluationRecord
from contracts.execution import TestCase
from evaluation.importance_sampling_audit import audit_fixed_sample_run
from evaluation.importance_sampling_comparison import compare_sampling_methods
from orchestration.worker_loop import WorkerLoop
from runtime.repository.importance_experiment_manifest import ExperimentManifest
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository

RUN_KINDS = ("ordinary", "importance")


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


def sample_id(run_kind: str, index: int) -> str:
    return f"{run_kind}-{index:06d}"


def run_strict_fixed_importance_sampling_comparison(
    *,
    backend: object,
    result_interpreter: object,
    repository: ImportanceSamplingResultsRepository,
    manifest: ExperimentManifest,
    build_case: Callable[[str, int], TestCase],
    metric: str,
    confidence: float,
    retry_failed: bool = False,
) -> dict[str, object]:
    """Fixed-N comparison with sample ids, a completion audit and resume by id.

    ``manifest`` must already be saved or verified for ``repository.root``.
    ``build_case`` must be deterministic in (run_kind, index): a retry uses the
    same inputs and random streams, so an unresolved sample is retried, never
    replaced by a new draw. Each case is tagged with its ``sample_id``, the
    manifest fingerprints and ``meta.attempt_id``.

    Without ``retry_failed``, samples whose latest attempt failed stay
    unresolved and the comparison is marked incomplete.
    """

    planned = manifest.planned_samples
    for run_kind in RUN_KINDS:
        attempts = _latest_attempts(repository.load_records(run_kind))
        loop = WorkerLoop(
            backend=backend,
            result_interpreter=result_interpreter,
            result_sink=lambda record, kind=run_kind: repository.append_record(kind, record),
        )
        for index in range(1, planned + 1):
            identifier = sample_id(run_kind, index)
            latest = attempts.get(identifier)
            if latest is None:
                attempt_id = 0
            elif retry_failed and not _is_resolved(latest[1], run_kind=run_kind, metric=metric):
                attempt_id = latest[0] + 1
            else:
                continue
            loop.run_once(_tag_case(build_case(run_kind, index), manifest, run_kind, identifier, attempt_id))

    records = {run_kind: repository.load_records(run_kind) for run_kind in RUN_KINDS}
    audits = {
        run_kind: audit_fixed_sample_run(
            records[run_kind],
            planned_sample_ids=[sample_id(run_kind, index) for index in range(1, planned + 1)],
            metric=metric,
            weighted=run_kind == "importance",
        )
        for run_kind in RUN_KINDS
    }
    complete = all(audit.complete for audit in audits.values())
    try:
        comparison = compare_sampling_methods(
            _latest_records(records["ordinary"]),
            _latest_records(records["importance"]),
            metric=metric,
            confidence=confidence,
        )
    except ValueError as exc:
        if complete:
            raise
        comparison = {"error": str(exc)}
    comparison.update(
        {
            "plan_id": manifest.experiment_id,
            "stop_mode": "fixed_samples",
            "final_status": "complete" if complete else "incomplete",
            "diagnostic_only": not complete,
            "audit": {run_kind: audit.to_dict() for run_kind, audit in audits.items()},
            "manifest": {
                "target_fingerprint": manifest.target_fingerprint,
                "proposal_fingerprint": manifest.proposal_fingerprint,
                "rng": manifest.rng,
                "planned_samples": planned,
            },
        }
    )
    comparison_path = repository.save_comparison(comparison)
    return {"comparison": comparison, "comparison_path": str(comparison_path)}


def _tag_case(
    case: TestCase,
    manifest: ExperimentManifest,
    run_kind: str,
    identifier: str,
    attempt_id: int,
) -> TestCase:
    sampling = dict(case.meta.get("sampling") or {})
    sampling["sample_id"] = identifier
    sampling["target_fingerprint"] = manifest.target_fingerprint
    if run_kind == "importance":
        sampling["proposal_fingerprint"] = manifest.proposal_fingerprint
    case.meta = {**case.meta, "sampling": sampling, "attempt_id": attempt_id}
    case.case_id = f"{case.case_id}-a{attempt_id}" if attempt_id else case.case_id
    return case


def _sample_key(record: EvaluationRecord) -> str | None:
    sampling = record.meta.get("sampling")
    value = sampling.get("sample_id") if isinstance(sampling, dict) else None
    return value if isinstance(value, str) else None


def _latest_attempts(records: Sequence[EvaluationRecord]) -> dict[str, tuple[int, EvaluationRecord]]:
    latest: dict[str, tuple[int, EvaluationRecord]] = {}
    for record in records:
        key = _sample_key(record)
        if key is None:
            continue
        attempt = int(record.meta.get("attempt_id", 0))
        if key not in latest or attempt >= latest[key][0]:
            latest[key] = (attempt, record)
    return latest


def _latest_records(records: Sequence[EvaluationRecord]) -> list[EvaluationRecord]:
    """One record per sample (its latest attempt), in sample order."""
    latest = _latest_attempts(records)
    return [latest[key][1] for key in sorted(latest)]


def _is_resolved(record: EvaluationRecord, *, run_kind: str, metric: str) -> bool:
    identifier = _sample_key(record)
    return audit_fixed_sample_run(
        [record],
        planned_sample_ids=[identifier],
        metric=metric,
        weighted=run_kind == "importance",
    ).complete


__all__ = [
    "run_fixed_importance_sampling_comparison",
    "run_strict_fixed_importance_sampling_comparison",
    "sample_id",
]
