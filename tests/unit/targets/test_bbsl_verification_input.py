import json
from pathlib import Path

import pytest

from contracts.execution import RunStatus
from contracts.verification import VerificationInput
from targets.bbsl.result_interpreter import ResultInterpreter
from targets.bbsl.verification_input import build_verification_input


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_build_verification_input_from_evaluation_record() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "experiment_all_raw_result_mini.json"
    )

    verification_input = build_verification_input(record)

    assert isinstance(verification_input, VerificationInput)
    assert verification_input.tree_mode == "basic"
    assert len(verification_input.universal_dataset) == 32
    assert set(verification_input.events.keys()) == {
        "SALT_PEPPER",
        "OCCLUSION",
        "BLUR",
    }
    assert verification_input.events["SALT_PEPPER"]["total_count"] == 7
    assert verification_input.events["SALT_PEPPER"]["error_count"] == 0
    assert verification_input.assumptions["sigma_pf_source"] == "dataset"
    assert verification_input.assumptions["sigma_pf_assumptions"]["SALT_PEPPER"] == 0.1
    assert verification_input.assumptions["and_rule"] == "min"
    assert str(verification_input.assumptions["tree_path"]).endswith("tree_basic.json")
    assert verification_input.meta["source_module"] == "targets.bbsl.verification_input"


def test_build_verification_input_accepts_raw_payload() -> None:
    payload = json.loads(
        (FIXTURES / "experiment_all_raw_result_mini.json").read_text(encoding="utf-8")
    )

    verification_input = build_verification_input(payload)

    assert verification_input.events["BLUR"]["dataset_d"]
    assert verification_input.assumptions["statistical_test_config"]["epsilon"] == 0.005


def test_build_verification_input_rejects_non_success_record() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "empty_experiment_all_raw_result.json"
    )

    with pytest.raises(ValueError, match="successful EvaluationRecord"):
        build_verification_input(record)

    assert record.status == RunStatus.INVALID


def test_build_verification_input_rejects_missing_combined_conditions() -> None:
    record = ResultInterpreter().interpret_fixture(FIXTURES / "noisy_batch_0001.json")

    with pytest.raises(ValueError, match="missing condition_results"):
        build_verification_input(record, tree_mode="combined")


def test_build_verification_input_rejects_record_missing_required_meta() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "experiment_all_raw_result_mini.json"
    )
    record.meta.pop("source_module")

    with pytest.raises(ValueError, match="missing required keys"):
        build_verification_input(record)
