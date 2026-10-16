import json
from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from apps.cli.result_interpreter_main import (
    build_interpreter,
    run_result_interpreter,
    serialize_evaluation_record,
)


def test_serialize_evaluation_record_converts_status_to_string() -> None:
    payload = serialize_evaluation_record(
        EvaluationRecord(
            case_id="case_1",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            input={"dx0": 15.0},
            output={"c_collision": 0},
            evidence={"trace_json": "/tmp/trace.json"},
            meta={"verifier_name": "maude"},
        )
    )

    assert payload["status"] == "success"
    assert payload["output"]["c_collision"] == 0


def test_build_interpreter_uses_awsim_context_override() -> None:
    class Args:
        target = "awsim"
        case_kind = "uturn"
        config_module = "targets.awsim.case_kinds.uturn"

    interpreter = build_interpreter(Args())

    assert interpreter.context.case_kind == "uturn"
    assert interpreter.context.config_module == "targets.awsim.case_kinds.uturn"


def test_run_result_interpreter_prints_json_when_no_output_path(capsys) -> None:
    exit_code = run_result_interpreter(
        [
            "--input",
            "tests/fixtures/bbsl/experiment_all_raw_result_mini.json",
            "--target",
            "bbsl",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == "bbsl"
    assert payload["status"] == "success"
    assert payload["output"]["condition_result_count"] == 4


def test_run_result_interpreter_writes_output_json_for_awsim(tmp_path: Path, capsys) -> None:
    output_path = tmp_path / "interpreted.json"

    exit_code = run_result_interpreter(
        [
            "--input",
            "tests/fixtures/awsim/normal_trace_maude.json",
            "--target",
            "awsim",
            "--case-id",
            "awsim_cli_case",
            "--output-json",
            str(output_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["case_id"] == "awsim_cli_case"
    assert payload["target"] == "awsim"
    assert payload["status"] == "success"
    stdout = capsys.readouterr().out
    assert "AWSIM_launch result interpreter finished" in stdout
    assert str(output_path) in stdout
