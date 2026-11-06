"""Target-neutral fixed-sample P/Q comparison execution.

Targets retain responsibility for interpreting P/Q and building ``TestCase``
objects.  This module only owns the repeated ordinary/importance execution,
separate persistence, and common comparison sequence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from contracts.evaluation import EvaluationRecord
from contracts.execution import TestCase
from contracts.importance_sampling_plugin import InternalProposal
from evaluation.importance_sampling_audit import audit_fixed_sample_run
from evaluation.importance_sampling_comparison import compare_sampling_methods
from orchestration.importance.executors import CaseExecutor, CaseJob, LocalExecutor
from orchestration.importance.weights import compose_internal_weight
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
    internal_proposal: InternalProposal | None = None,
    executor: CaseExecutor | None = None,
) -> dict[str, object]:
    """Resume and compare fixed-size ordinary and importance runs.

    ``internal_proposal``: the proposal over the target's internal randomness,
    used to turn each record's ``internal`` report into its weight.
    """

    if max_samples < 1:
        raise ValueError("max_samples must be positive")
    executor = executor or LocalExecutor(backend=backend, result_interpreter=result_interpreter)
    jobs: list[CaseJob] = []
    for run_kind in RUN_KINDS:
        sink = importance_record_sink(repository, run_kind, internal_proposal)
        existing_count = len(repository.load_records(run_kind))
        jobs += [CaseJob(build_case(run_kind, index), sink, run_kind)
                 for index in range(existing_count + 1, max_samples + 1)]
    executor.run(jobs)
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


def importance_record_sink(
    repository: ImportanceSamplingResultsRepository,
    run_kind: str,
    internal_proposal: InternalProposal | None,
) -> Callable[[EvaluationRecord], object]:
    """Save a record after composing its weight from the ``internal`` report, if any."""

    def sink(record: EvaluationRecord) -> object:
        return repository.append_record(run_kind, compose_internal_weight(record, internal_proposal))

    return sink


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
    internal_proposal: InternalProposal | None = None,
    executor: CaseExecutor | None = None,
    max_attempts: int = 1,
) -> dict[str, object]:
    """Fixed-N comparison with sample ids, a completion audit and resume by id.

    ``manifest`` must already be saved or verified for ``repository.root``.
    ``build_case`` must be deterministic in (run_kind, index): a retry uses the
    same inputs and random streams, so an unresolved sample is retried, never
    replaced by a new draw. Each case is tagged with its ``sample_id``, the
    manifest fingerprints and ``meta.attempt_id``.

    A sample whose attempt failed is run again (same inputs and streams)
    until it resolves or has had ``max_attempts`` attempts. ``retry_failed``
    gives samples that used up their attempts in an earlier run one more.
    Samples that stay unresolved mark the comparison incomplete.
    """

    planned = manifest.planned_samples
    executor = executor or LocalExecutor(backend=backend, result_interpreter=result_interpreter)
    first_round = True
    # One attempt per unresolved sample and round; a sample whose attempt left
    # no record (lost on a cluster) does not come back, so the rounds are bounded.
    for _round in range(max_attempts + (1 if retry_failed else 0)):
        jobs: list[CaseJob] = []
        for run_kind in RUN_KINDS:
            attempts = _latest_attempts(repository.load_records(run_kind))
            sink = importance_record_sink(repository, run_kind, internal_proposal)
            for index in range(1, planned + 1):
                identifier = sample_id(run_kind, index)
                latest = attempts.get(identifier)
                if latest is None:
                    attempt_id = 0
                elif _is_resolved(latest[1], run_kind=run_kind, metric=metric):
                    continue
                elif latest[0] + 1 < max_attempts or (first_round and retry_failed):
                    attempt_id = latest[0] + 1
                else:
                    continue
                jobs.append(CaseJob(
                    _tag_case(build_case(run_kind, index), manifest, run_kind, identifier, attempt_id), sink, run_kind,
                ))
        if not jobs:
            break
        executor.run(jobs)
        first_round = False

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
    "importance_record_sink",
    "run_fixed_importance_sampling_comparison",
    "run_strict_fixed_importance_sampling_comparison",
    "sample_id",
]
