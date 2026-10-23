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
    from orchestration.strategy_registry import plugin_strategy_spec

    assert set(registered_strategies()) >= {
        "fixture", "iid_sampling", "fixed_parameter_sampling", "fixed_parameter_dkw", "parameter_case",
    }
    assert SELF_TERMINATING_STRATEGIES <= set(registered_strategies())
    # The AWSIM strategies come from the awsim plugin, not from shared code.
    assert {"replay", "active_learning"}.isdisjoint(registered_strategies())
    assert plugin_strategy_spec("replay", "awsim") is not None
    assert plugin_strategy_spec("active_learning", "awsim") is not None


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


def test_plugin_strategies_are_resolved_with_their_metadata(monkeypatch) -> None:
    from types import SimpleNamespace

    from orchestration import strategy_registry
    from orchestration.strategy_registry import (
        StrategySpec,
        is_self_terminating,
        plugin_strategy_spec,
        select_strategy_name,
        strategy_builder,
    )

    def build(_orchestrator, _config):
        return "walk-strategy"

    plugin = SimpleNamespace(
        select_strategy=lambda config: "random_walk",
        strategies={
            "random_walk": StrategySpec(build, self_terminating=True, composed_as="composed_iid",
                                        target_total=lambda config, last: last + 3),
            "parameter_case": StrategySpec(build),
        },
    )
    monkeypatch.setattr(strategy_registry, "_plugin", lambda target: plugin if target == "walk" else None)
    config = SimpleNamespace(target="walk", fixture=None, run_mode="explore", params={}, replay_csv=None,
                             stopping_rule="legacy")

    assert select_strategy_name(config) == "random_walk"
    assert strategy_builder("random_walk", "walk")(None, config) == "walk-strategy"
    assert is_self_terminating("random_walk", "walk")
    assert plugin_strategy_spec("random_walk", "walk").target_total(config, 10) == 13
    config.stopping_rule = "binomial_ci_fixed_strict"
    assert select_strategy_name(config) == "composed_iid"
    # Shared strategies are found first; a plugin cannot replace them.
    assert strategy_builder("parameter_case", "walk") is strategy_registry._BUILDERS["parameter_case"]
    with pytest.raises(ValueError, match="Unknown strategy"):
        strategy_builder("random_walk", "other")


def test_plugin_strategies_must_be_specs(monkeypatch) -> None:
    from types import SimpleNamespace

    from orchestration import strategy_registry

    monkeypatch.setattr(strategy_registry, "_plugin", lambda target: SimpleNamespace(strategies={"x": object()}))
    with pytest.raises(TypeError, match="must be a StrategySpec"):
        strategy_registry.strategy_builder("x", "walk")
