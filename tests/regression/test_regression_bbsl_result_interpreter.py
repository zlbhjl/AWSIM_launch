from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import pytest


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bbsl"


@dataclass(frozen=True)
class BBSLRegressionCase:
    fixture_name: str
    expected_status: str
    notes: str


CASES = [
    BBSLRegressionCase(
        fixture_name="experiment_all_raw_result_mini.json",
        expected_status="success",
        notes="BBSL raw result の正常系",
    ),
    BBSLRegressionCase(
        fixture_name="invalid_missing_bbsl_results.json",
        expected_status="analysis_error",
        notes="必須キー bbsl_results 欠落ケース",
    ),
    BBSLRegressionCase(
        fixture_name="empty_experiment_all_raw_result.json",
        expected_status="invalid",
        notes="必須キーはあるが結果集合が空のケース",
    ),
]


def _import_result_interpreter_module():
    try:
        return importlib.import_module("targets.bbsl.result_interpreter")
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
        "targets.bbsl.result_interpreter must expose interpret_fixture(), "
        "interpret_path(), interpret_raw_run_result_fixture(), or ResultInterpreter"
    )


def test_bbsl_regression_matrix_is_defined() -> None:
    assert [case.fixture_name for case in CASES] == [
        "experiment_all_raw_result_mini.json",
        "invalid_missing_bbsl_results.json",
        "empty_experiment_all_raw_result.json",
    ]
    for case in CASES:
        assert (FIXTURES / case.fixture_name).exists(), case.fixture_name
        assert case.expected_status in {"success", "analysis_error", "invalid"}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture_name)
def test_bbsl_result_interpreter_regression_status(case: BBSLRegressionCase) -> None:
    module = _import_result_interpreter_module()
    if module is None:
        pytest.skip("targets.bbsl.result_interpreter is not implemented yet")

    runner = _build_runner(module)
    record = runner(FIXTURES / case.fixture_name)
    assert _extract_status(record) == case.expected_status, case.notes
