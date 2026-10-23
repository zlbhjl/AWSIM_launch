from __future__ import annotations

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.importance_sampling_audit import audit_fixed_sample_run

PLANNED = ["importance-000001", "importance-000002"]


def _record(
    sample_id: str | None,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    value: object = 0,
    log_weight: float | None = 0.0,
    attempt_id: int | None = None,
) -> EvaluationRecord:
    sampling: dict[str, object] = {"importance_weight_status": "available"}
    if sample_id is not None:
        sampling["sample_id"] = sample_id
    if log_weight is None:
        sampling.update({"weight_kind": "zero", "log_importance_weight": None})
    else:
        sampling["log_importance_weight"] = log_weight
    meta: dict[str, object] = {"sampling": sampling}
    if attempt_id is not None:
        meta["attempt_id"] = attempt_id
    return EvaluationRecord(
        case_id=str(sample_id),
        target="dynamics",
        case_kind="ou_scalar",
        status=status,
        output={} if status is not RunStatus.SUCCESS else {"c_event": value},
        meta=meta,
    )


def test_all_judged_samples_are_complete_and_zero_weights_count() -> None:
    audit = audit_fixed_sample_run(
        [_record(PLANNED[0], value=1), _record(PLANNED[1], log_weight=None)],
        planned_sample_ids=PLANNED,
        metric="c_event",
    )

    assert audit.complete is True
    assert audit.counts["judged"] == 2
    assert audit.counts["zero_weight"] == 1


def test_missing_and_failed_samples_are_unresolved() -> None:
    audit = audit_fixed_sample_run(
        [_record(PLANNED[0], status=RunStatus.EXECUTION_ERROR)],
        planned_sample_ids=PLANNED,
        metric="c_event",
    )

    assert audit.complete is False
    assert audit.unresolved_sample_ids == PLANNED
    assert audit.counts["attempted"] == 1


def test_latest_attempt_decides_and_retries_are_counted() -> None:
    audit = audit_fixed_sample_run(
        [
            _record(PLANNED[0], status=RunStatus.EXECUTION_ERROR, attempt_id=0),
            _record(PLANNED[0], attempt_id=1),
            _record(PLANNED[1]),
        ],
        planned_sample_ids=PLANNED,
        metric="c_event",
    )

    assert audit.complete is True
    assert audit.counts["retries"] == 1


def test_duplicate_successes_make_the_run_incomplete() -> None:
    audit = audit_fixed_sample_run(
        [_record(PLANNED[0], attempt_id=0), _record(PLANNED[0], attempt_id=1), _record(PLANNED[1])],
        planned_sample_ids=PLANNED,
        metric="c_event",
    )

    assert audit.complete is False
    assert audit.counts["duplicate_successes"] == 1


@pytest.mark.parametrize(
    "record",
    [
        _record(PLANNED[1], value=None),
        _record(PLANNED[1], value=2.0),
        _record(PLANNED[1], log_weight=float("nan")),
    ],
)
def test_unjudged_out_of_range_or_broken_weight_is_unresolved(record: EvaluationRecord) -> None:
    audit = audit_fixed_sample_run([_record(PLANNED[0]), record], planned_sample_ids=PLANNED, metric="c_event")

    assert audit.complete is False
    assert audit.unresolved_sample_ids == [PLANNED[1]]


def test_records_outside_the_plan_or_without_an_id_are_problems() -> None:
    audit = audit_fixed_sample_run(
        [_record(PLANNED[0]), _record(PLANNED[1]), _record("importance-000009"), _record(None)],
        planned_sample_ids=PLANNED,
        metric="c_event",
    )

    assert audit.complete is False
    assert len(audit.problems) == 2


def test_ordinary_runs_do_not_need_a_weight() -> None:
    record = _record(PLANNED[0])
    record.meta["sampling"] = {"importance_weight_status": "not_applicable", "sample_id": PLANNED[0]}

    audit = audit_fixed_sample_run([record], planned_sample_ids=PLANNED[:1], metric="c_event", weighted=False)

    assert audit.complete is True
