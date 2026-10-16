import pytest

from contracts.evaluation import (
    EvaluationRecord,
    ensure_evaluation_meta,
    validate_evaluation_meta,
)
from contracts.execution import RunStatus


def test_evaluation_record_defaults_are_empty_collections() -> None:
    record = EvaluationRecord(
        case_id="case-1",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.SUCCESS,
    )

    assert record.input == {}
    assert record.output == {}
    assert record.evidence == {}
    assert record.meta == {}


def test_validate_evaluation_meta_accepts_required_common_keys() -> None:
    record = EvaluationRecord(
        case_id="case-2",
        target="bbsl",
        case_kind="batch_loop",
        status=RunStatus.ANALYSIS_ERROR,
        meta={
            "schema_version": 1,
            "created_at": "2026-08-03T12:00:00+09:00",
            "source_module": "targets.bbsl.result_interpreter",
        },
    )

    validate_evaluation_meta(record.meta)
    assert record.meta["schema_version"] == 1
    assert record.meta["created_at"] == "2026-08-03T12:00:00+09:00"
    assert record.meta["source_module"] == "targets.bbsl.result_interpreter"


def test_validate_evaluation_meta_rejects_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        validate_evaluation_meta({"created_at": "2026-08-03T12:00:00+09:00"})


def test_ensure_evaluation_meta_sets_required_defaults() -> None:
    meta = ensure_evaluation_meta(
        {"verifier_name": "maude"},
        source_module="targets.awsim.result_interpreter",
        schema_version=2,
        created_at="2026-08-17T00:00:00+09:00",
    )

    assert meta["schema_version"] == 2
    assert meta["source_module"] == "targets.awsim.result_interpreter"
    assert meta["created_at"] == "2026-08-17T00:00:00+09:00"
    assert meta["verifier_name"] == "maude"


def test_evaluation_record_keeps_run_status_enum() -> None:
    record = EvaluationRecord(
        case_id="case-3",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.INVALID,
    )

    assert record.status is RunStatus.INVALID
