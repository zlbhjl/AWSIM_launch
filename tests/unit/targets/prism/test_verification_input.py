import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from targets.prism.verification_input import (
    TREE_PATH,
    build_verification_input_from_records,
)


def _sample_record(
    index: int,
    *,
    c_failure: int,
    m_early_failure: int,
    m_repeated_degradation: int,
    status: RunStatus = RunStatus.SUCCESS,
    record_kind: str = "sample",
) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=f"case-{index}",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=status,
        input={"record_kind": record_kind, "experiment_id": "unit-test"},
        output={
            "c_failure": c_failure,
            "m_early_failure": m_early_failure,
            "m_repeated_degradation": m_repeated_degradation,
        },
        meta={"schema_version": 1, "source_module": "test"},
    )


def _exact_record(index: int = 0) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=f"exact-{index}",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        input={"record_kind": "exact_model_check"},
        output={"prism_bounded_failure_probability": 0.42},
        meta={"schema_version": 1, "source_module": "test"},
    )


def test_build_verification_input_from_records_aggregates_batch() -> None:
    records = [
        _sample_record(1, c_failure=1, m_early_failure=1, m_repeated_degradation=0),
        _sample_record(2, c_failure=0, m_early_failure=0, m_repeated_degradation=0),
        _sample_record(3, c_failure=1, m_early_failure=0, m_repeated_degradation=1),
        _sample_record(4, c_failure=0, m_early_failure=0, m_repeated_degradation=0),
    ]

    verification_input = build_verification_input_from_records(records)

    assert verification_input.tree_mode == "prism_demo"
    assert verification_input.universal_dataset == {
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    }
    assert verification_input.assumptions["tree_path"] == str(TREE_PATH)

    failure = verification_input.events["FAILURE"]
    assert failure["dataset_d"] == verification_input.universal_dataset
    assert failure["dataset_e"] == {"case-1", "case-3"}
    assert failure["total_count"] == 4
    assert failure["error_count"] == 2

    early_failure = verification_input.events["EARLY_FAILURE"]
    assert early_failure["dataset_e"] == {"case-1"}
    assert early_failure["error_count"] == 1

    repeated_degradation = verification_input.events["REPEATED_DEGRADATION"]
    assert repeated_degradation["dataset_e"] == {"case-3"}
    assert repeated_degradation["error_count"] == 1


def test_build_verification_input_from_records_excludes_exact_model_check() -> None:
    records = [
        _exact_record(),
        _sample_record(1, c_failure=1, m_early_failure=0, m_repeated_degradation=0),
        _sample_record(2, c_failure=0, m_early_failure=0, m_repeated_degradation=0),
    ]

    verification_input = build_verification_input_from_records(records)

    assert verification_input.universal_dataset == {"case-1", "case-2"}
    assert verification_input.meta["record_count"] == 3
    assert verification_input.meta["eligible_record_count"] == 2


def test_build_verification_input_from_records_excludes_non_success_status() -> None:
    records = [
        _sample_record(1, c_failure=1, m_early_failure=0, m_repeated_degradation=0),
        _sample_record(
            2,
            c_failure=0,
            m_early_failure=0,
            m_repeated_degradation=0,
            status=RunStatus.TIMEOUT,
        ),
    ]

    verification_input = build_verification_input_from_records(records)

    assert verification_input.universal_dataset == {"case-1"}


def test_build_verification_input_from_records_rejects_empty_batch() -> None:
    with pytest.raises(ValueError):
        build_verification_input_from_records([_exact_record()])


def test_build_verification_input_from_records_merges_caller_assumptions() -> None:
    records = [
        _sample_record(1, c_failure=1, m_early_failure=0, m_repeated_degradation=0),
    ]

    verification_input = build_verification_input_from_records(
        records,
        assumptions={"sigma_pf_assumptions": {"FAILURE": 1.0}},
        meta={"experiment_id": "unit-test"},
    )

    assert verification_input.assumptions["sigma_pf_assumptions"] == {"FAILURE": 1.0}
    assert verification_input.assumptions["tree_path"] == str(TREE_PATH)
    assert verification_input.meta["experiment_id"] == "unit-test"
