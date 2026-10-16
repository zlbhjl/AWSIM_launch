from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import pytest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "awsim"


@dataclass(frozen=True)
class AWSIMRegressionCase:
    fixture_name: str
    expected_status: str
    notes: str


CASES = [
    AWSIMRegressionCase(
        fixture_name="normal_trace_maude.json",
        expected_status="success",
        notes="旧 awchecker.py 互換の正常系",
    ),
    AWSIMRegressionCase(
        fixture_name="timeout_trace.txt",
        expected_status="timeout",
        notes="run_manager.py が書く TIMEOUT ダミー",
    ),
    AWSIMRegressionCase(
        fixture_name="maude_failure_missing_vehicle_sizes.json",
        expected_status="analysis_error",
        notes="旧 aw_checkerpy.py の vehicle_sizes 依存を踏むケース",
    ),
    AWSIMRegressionCase(
        fixture_name="empty_trace_with_vehicle_sizes.json",
        expected_status="invalid",
        notes="JSON は読めるが時系列が空のケース",
    ),
]


def _import_result_interpreter_module():
    try:
        return importlib.import_module("targets.awsim.result_interpreter")
    except ModuleNotFoundError:
        return None


def _status_to_str(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _extract_status(record: Any) -> str:
    if hasattr(record, "status"):
        return _status_to_str(record.status)
    if isinstance(record, dict) and "status" in record:
        return _status_to_str(record["status"])
    raise AssertionError("result_interpreter output does not expose a status field")


def _build_runner(module: Any) -> Callable[[Path], Any]:
    if hasattr(module, "interpret_fixture"):
        return lambda fixture_path: module.interpret_fixture(fixture_path)

    if hasattr(module, "interpret_path"):
        return lambda fixture_path: module.interpret_path(fixture_path)

    if hasattr(module, "interpret_raw_run_result_fixture"):
        return lambda fixture_path: module.interpret_raw_run_result_fixture(fixture_path)

    if hasattr(module, "ResultInterpreter"):
        interpreter = module.ResultInterpreter()
        if hasattr(interpreter, "interpret_fixture"):
            return lambda fixture_path: interpreter.interpret_fixture(fixture_path)
        if hasattr(interpreter, "interpret_path"):
            return lambda fixture_path: interpreter.interpret_path(fixture_path)

    raise AssertionError(
        "targets.awsim.result_interpreter must expose interpret_fixture(), "
        "interpret_path(), interpret_raw_run_result_fixture(), or ResultInterpreter"
    )


def test_awsim_regression_matrix_is_defined() -> None:
    assert [case.fixture_name for case in CASES] == [
        "normal_trace_maude.json",
        "timeout_trace.txt",
        "maude_failure_missing_vehicle_sizes.json",
        "empty_trace_with_vehicle_sizes.json",
    ]
    for case in CASES:
        assert (FIXTURES / case.fixture_name).exists(), case.fixture_name
        assert case.expected_status in {
            "success",
            "timeout",
            "analysis_error",
            "invalid",
        }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture_name)
def test_awsim_result_interpreter_regression_status(case: AWSIMRegressionCase) -> None:
    module = _import_result_interpreter_module()
    if module is None:
        pytest.skip("targets.awsim.result_interpreter is not implemented yet")

    runner = _build_runner(module)
    record = runner(FIXTURES / case.fixture_name)
    assert _extract_status(record) == case.expected_status, case.notes
