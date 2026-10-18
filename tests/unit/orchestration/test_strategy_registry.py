from types import SimpleNamespace

import pytest

from orchestration.strategy_registry import (
    SELF_TERMINATING_STRATEGIES,
    check_strategy,
    registered_strategies,
    select_strategy_name,
)


def _config(**overrides):
    values = dict(target="awsim", run_mode="explore", params={}, fixture=None, replay_csv=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_existing_strategies_are_registered() -> None:
    assert set(registered_strategies()) >= {
        "fixture", "iid_sampling", "fixed_parameter_sampling", "fixed_parameter_dkw",
        "parameter_case", "replay", "active_learning",
    }
    assert SELF_TERMINATING_STRATEGIES <= set(registered_strategies())


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "active_learning"),
        ({"replay_csv": "x.csv"}, "replay"),
        ({"fixture": "case.json", "run_mode": "binomial_ci", "target": "dynamics"}, "fixture"),
        ({"target": "dynamics", "run_mode": "binomial_ci"}, "iid_sampling"),
        ({"target": "bernoulli", "run_mode": "dkw_fixed"}, "iid_sampling"),
        ({"target": "prism", "params": {"steps": 20}, "run_mode": "sprt"}, "fixed_parameter_sampling"),
        ({"target": "prism", "params": {"steps": 20}, "run_mode": "dkw"}, "fixed_parameter_dkw"),
        ({"target": "prism", "params": {"steps": 20}, "run_mode": "explore"}, "parameter_case"),
    ],
)
def test_strategy_selection(overrides, expected) -> None:
    assert select_strategy_name(_config(**overrides)) == expected


def test_prism_rejects_modes_without_a_live_path() -> None:
    with pytest.raises(ValueError, match="no live execution path"):
        check_strategy(_config(target="prism", params={"steps": 20}, run_mode="boundary_gap"))
