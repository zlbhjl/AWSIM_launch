"""Choose and build the strategy that issues test cases and decides when to stop.

A strategy is named. Each target plugin may pick a name for a configuration
(``select_strategy``) or reject a configuration it cannot run
(``check_strategy``); otherwise the shared default applies. Builders are
registered by name, so new strategies (for example, a composition of a
sampling design and a stopping rule) can be added without touching the
orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from targets.registry import get_target_plugin
from targets.statistical import supports_iid_sampling

if TYPE_CHECKING:
    from orchestration.orchestrator import Orchestrator, OrchestratorConfig

STATISTICAL_MODES = frozenset({"binomial_ci", "sprt", "dkw", "dkw_fixed", "ebstop"})

# Strategies that stop by themselves (statistical stopping), so the orchestrator
# has no fixed number of cases to wait for.
SELF_TERMINATING_STRATEGIES = frozenset({
    "iid_sampling", "fixed_parameter_sampling", "fixed_parameter_dkw", "composed_iid", "composed_fixed_parameter",
})
LEGACY_STOPPING_RULE = "legacy"
# A --stopping-rule replaces the stopping logic of these strategies; the sampling stays the same.
COMPOSED_STRATEGIES = {
    "iid_sampling": "composed_iid",
    "fixed_parameter_sampling": "composed_fixed_parameter",
    "fixed_parameter_dkw": "composed_fixed_parameter",
}

StrategyBuilder = Callable[["Orchestrator", "OrchestratorConfig"], object]
_BUILDERS: dict[str, StrategyBuilder] = {}


def register_strategy(name: str, builder: StrategyBuilder) -> None:
    if name in _BUILDERS:
        raise ValueError(f"strategy {name!r} is already registered")
    _BUILDERS[name] = builder


def strategy_builder(name: str) -> StrategyBuilder:
    try:
        return _BUILDERS[name]
    except KeyError:
        raise ValueError(f"Unknown strategy: {name}") from None


def registered_strategies() -> list[str]:
    return sorted(_BUILDERS)


def _plugin(target: str) -> object | None:
    try:
        return get_target_plugin(target)
    except ValueError:
        return None


def check_strategy(config: "OrchestratorConfig") -> None:
    """Let the target reject a configuration it has no strategy for."""
    checker = getattr(_plugin(config.target), "check_strategy", None)
    if callable(checker):
        checker(config)


def default_strategy_name(config: "OrchestratorConfig") -> str:
    if supports_iid_sampling(config.target) and config.run_mode in STATISTICAL_MODES:
        return "iid_sampling"
    if config.params:
        return "parameter_case"
    if config.replay_csv is not None:
        return "replay"
    return "active_learning"


def select_strategy_name(config: "OrchestratorConfig", *, include_fixture: bool = True) -> str:
    if include_fixture and config.fixture is not None:
        return "fixture"
    name = None
    selector = getattr(_plugin(config.target), "select_strategy", None)
    if callable(selector):
        name = selector(config)
    name = str(name) if name is not None else default_strategy_name(config)
    stopping_rule = getattr(config, "stopping_rule", LEGACY_STOPPING_RULE)
    if stopping_rule == LEGACY_STOPPING_RULE:
        return name
    if name not in COMPOSED_STRATEGIES:
        raise ValueError(
            f"--stopping-rule {stopping_rule} is not available for target={config.target} "
            f"--mode {config.run_mode} (strategy {name!r}); it works with IID-sampling targets and "
            "fixed-parameter sampling"
        )
    return COMPOSED_STRATEGIES[name]


def _method(name: str) -> StrategyBuilder:
    def build(orchestrator: "Orchestrator", config: "OrchestratorConfig") -> object:
        return getattr(orchestrator, name)(config)

    return build


# The existing strategies, built by the orchestrator exactly as before.
for _name, _method_name in (
    ("fixture", "_build_fixture_strategy"),
    ("iid_sampling", "_build_dynamics_sampling_strategy"),
    ("fixed_parameter_sampling", "_build_fixed_parameter_sampling_strategy"),
    ("fixed_parameter_dkw", "_build_fixed_parameter_dkw_strategy"),
    ("parameter_case", "_build_parameter_case_strategy"),
    ("replay", "_build_replay_strategy"),
    ("active_learning", "_build_active_learning_strategy"),
):
    register_strategy(_name, _method(_method_name))


def _composed(builder_name: str) -> StrategyBuilder:
    def build(orchestrator: "Orchestrator", config: "OrchestratorConfig") -> object:
        from orchestration import composed_strategy

        return getattr(composed_strategy, builder_name)(orchestrator, config)

    return build


register_strategy("composed_iid", _composed("build_composed_iid_strategy"))
register_strategy("composed_fixed_parameter", _composed("build_composed_fixed_parameter_strategy"))


__all__ = [
    "SELF_TERMINATING_STRATEGIES",
    "STATISTICAL_MODES",
    "check_strategy",
    "default_strategy_name",
    "register_strategy",
    "registered_strategies",
    "select_strategy_name",
    "strategy_builder",
]
