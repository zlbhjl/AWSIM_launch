"""The importance-sampling runners reproduce the golden runs bit for bit.

Regenerate only on purpose (the plan requires identical results):
UPDATE_IMPORTANCE_GOLDEN=1 python3 -m pytest tests/regression/test_importance_golden.py
"""

import os

import pytest

from tests.regression.importance_golden import golden_cases, load_golden, run_case, save_golden

CASES = golden_cases()


def test_golden_covers_every_case() -> None:
    if os.environ.get("UPDATE_IMPORTANCE_GOLDEN") == "1":
        pytest.skip("regenerating")
    assert sorted(load_golden()) == sorted(case.name for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_runner_reproduces_golden_run(case, tmp_path) -> None:
    actual = run_case(case, tmp_path / "golden")
    if os.environ.get("UPDATE_IMPORTANCE_GOLDEN") == "1":
        golden = load_golden() if os.path.exists(os.fspath(_golden_path())) else {}
        golden[case.name] = actual
        save_golden(golden)
        return
    assert actual == load_golden()[case.name]


def _golden_path():
    from tests.regression.importance_golden import GOLDEN_PATH

    return GOLDEN_PATH
