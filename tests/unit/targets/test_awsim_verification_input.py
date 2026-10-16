import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.verification import VerificationInput
from targets.awsim.verification_input import (
    VerificationContext,
    VerificationInputBuilder,
    build_verification_input,
)


def _record(
    *,
    case_id: str,
    status: RunStatus = RunStatus.SUCCESS,
    output: dict[str, object] | None = None,
) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=case_id,
        target="awsim",
        case_kind="uturn",
        status=status,
        output=output or {},
        evidence={"trace_json": f"/tmp/{case_id}.json"},
        meta={
            "schema_version": 1,
            "created_at": "2026-08-03T00:00:00+09:00",
            "source_module": "targets.awsim.result_interpreter",
            "verifier_name": "maude",
            "path_root": "/tmp",
        },
    )


def test_builder_creates_events_from_explicit_definitions() -> None:
    records = [
        _record(case_id="loop_1", output={"c_collision": 1, "c_ttc_1.5": 1}),
        _record(case_id="loop_2", output={"c_collision": 0, "c_ttc_1.5": 1}),
        _record(case_id="loop_3", output={"c_collision": 0, "c_ttc_1.5": 0}),
    ]

    verification_input = build_verification_input(
        records,
        {
            "collision": {
                "dataset_filter": None,
                "error_filter": "c_collision",
                "target_column": "c_collision",
            },
            "near_miss": {
                "dataset_filter": None,
                "error_filter": "output.c_ttc_1.5",
                "target_column": "c_ttc_1.5",
            },
        },
        assumptions={"tree_source": "uturn"},
    )

    assert isinstance(verification_input, VerificationInput)
    assert verification_input.tree_mode == "basic"
    assert verification_input.universal_dataset == {"loop_1", "loop_2", "loop_3"}
    assert verification_input.events["collision"]["dataset_e"] == {"loop_1"}
    assert verification_input.events["near_miss"]["dataset_e"] == {"loop_1", "loop_2"}
    assert verification_input.events["near_miss"]["correct_count"] == 1
    assert verification_input.assumptions["tree_source"] == "uturn"


def test_builder_generates_default_events_from_discrete_outputs() -> None:
    record = _record(
        case_id="loop_9",
        output={"c_collision": 0, "c_ttc_1.5": 1, "min_distance": 3.5},
    )

    verification_input = build_verification_input(record)

    assert {"c_collision", "c_ttc_1.5"} <= set(verification_input.events.keys())
    assert verification_input.events["c_collision"]["target_column"] == "c_collision"
    assert verification_input.events["c_ttc_1.5"]["dataset_e"] == {"loop_9"}


def test_builder_excludes_non_success_records_from_verification_dataset() -> None:
    builder = VerificationInputBuilder(
        VerificationContext(include_statuses=frozenset({RunStatus.SUCCESS}))
    )
    records = [
        _record(case_id="loop_ok", status=RunStatus.SUCCESS, output={"c_collision": 0}),
        _record(case_id="loop_timeout", status=RunStatus.TIMEOUT, output={"c_collision": 1}),
        _record(case_id="loop_invalid", status=RunStatus.INVALID, output={"c_collision": 1}),
    ]

    verification_input = builder.build(
        records,
        {
            "collision": {
                "dataset_filter": None,
                "error_filter": "c_collision",
            }
        },
    )

    assert verification_input.universal_dataset == {"loop_ok"}
    assert verification_input.events["collision"]["dataset_d"] == {"loop_ok"}
    assert verification_input.meta["eligible_record_count"] == 1
    assert verification_input.meta["excluded_status_counts"] == {
        "success": 1,
        "timeout": 1,
        "invalid": 1,
    }


def test_builder_rejects_record_missing_required_meta() -> None:
    record = _record(case_id="loop_bad", output={"c_collision": 0})
    record.meta.pop("source_module")

    with pytest.raises(ValueError, match="missing required keys"):
        build_verification_input(record)
