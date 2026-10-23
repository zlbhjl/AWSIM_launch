"""Choose and build the strategy that issues test cases and decides when to stop.

A strategy is named. Each target plugin may pick a name for a configuration
(``select_strategy``) or reject a configuration it cannot run
(``check_strategy``); otherwise the shared default applies. Builders are
registered by name, so new strategies (for example, a composition of a
sampling design and a stopping rule) can be added without touching the
orchestrator.

A target may also bring its own strategies: an optional plugin attribute
``strategies`` maps names to :class:`StrategySpec`. A spec says how to build
the strategy, whether it stops by itself, which composed strategy replaces it
under ``--stopping-rule``, and how many cases the orchestrator should wait for.
Shared strategies are looked up first; a plugin cannot replace them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
TargetTotal = Callable[["OrchestratorConfig", int], "int | None"]
_BUILDERS: dict[str, StrategyBuilder] = {}


@dataclass(frozen=True)
class StrategySpec:
    """A strategy a target plugin provides (plugin attribute ``strategies``)."""

    builder: StrategyBuilder
    # The strategy stops by itself, so the orchestrator waits for no fixed count.
    self_terminating: bool = False
    # The composed strategy that replaces it under --stopping-rule (None: not allowed).
    composed_as: str | None = None
    # The loop number to finish at, given the last one (None: no fixed total).
    # Without it the shared rule applies (max_samples, otherwise no total).
    target_total: TargetTotal | None = None


def register_strategy(name: str, builder: StrategyBuilder) -> None:
    if name in _BUILDERS:
        raise ValueError(f"strategy {name!r} is already registered")
    _BUILDERS[name] = builder


def plugin_strategy_spec(name: str, target: str | None) -> StrategySpec | None:
    """The spec of a strategy the target's plugin provides, if any."""
    if target is None or name in _BUILDERS:
        return None
    specs = getattr(_plugin(target), "strategies", None)
    if not isinstance(specs, Mapping):
        return None
    spec = specs.get(name)
    if spec is not None and not isinstance(spec, StrategySpec):
        raise TypeError(f"target {target!r} strategy {name!r} must be a StrategySpec")
    return spec


def strategy_builder(name: str, target: str | None = None) -> StrategyBuilder:
    if name in _BUILDERS:
        return _BUILDERS[name]
    spec = plugin_strategy_spec(name, target)
    if spec is None:
        raise ValueError(f"Unknown strategy: {name}")
    return spec.builder


def is_self_terminating(name: str, target: str | None = None) -> bool:
    if name in SELF_TERMINATING_STRATEGIES:
        return True
    spec = plugin_strategy_spec(name, target)
    return spec is not None and spec.self_terminating


def _composed_name(name: str, target: str | None) -> str | None:
    if name in COMPOSED_STRATEGIES:
        return COMPOSED_STRATEGIES[name]
    spec = plugin_strategy_spec(name, target)
    return None if spec is None else spec.composed_as


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
    """The shared defaults; any other strategy is chosen by the target's plugin."""
    if supports_iid_sampling(config.target) and config.run_mode in STATISTICAL_MODES:
        return "iid_sampling"
    if config.params:
        return "parameter_case"
    raise ValueError(
        f"target={config.target} has no strategy for --mode {config.run_mode} without "
        "--fixture or --param"
    )


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
    composed = _composed_name(name, config.target)
    if composed is None:
        raise ValueError(
            f"--stopping-rule {stopping_rule} is not available for target={config.target} "
            f"--mode {config.run_mode} (strategy {name!r}); it works with IID-sampling targets and "
            "fixed-parameter sampling"
        )
    return composed


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
    "StrategySpec",
    "check_strategy",
    "default_strategy_name",
    "is_self_terminating",
    "plugin_strategy_spec",
    "register_strategy",
    "registered_strategies",
    "select_strategy_name",
    "strategy_builder",
]
