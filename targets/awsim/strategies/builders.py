"""Build the AWSIM strategies from an OrchestratorConfig (registered in targets/awsim/plugin.py).

Moved from orchestration/orchestrator.py; the strategies are built exactly as before.
"""

from __future__ import annotations

import importlib

from orchestration.exploration.builder import (
    OPEN_ENDED_MODES,
    build_gp_exploration_strategy as _shared_builder,
    gp_exploration_target_total,
)

from .active_learning import ActiveLearningStrategist
from .replay import ReplayCsvStrategy, ReplayCsvStrategyConfig


def build_replay_strategy(orchestrator, config) -> ReplayCsvStrategy:
    strategy_config = importlib.import_module(config.config_module)
    case_definition = load_replay_case_definition(strategy_config, config.case_kind)
    input_types = dict(case_definition.get("fixed_params", {}))
    for name, bounds in dict(case_definition.get("param_ranges", {})).items():
        if name in input_types:
            continue
        try:
            input_types[name] = float(bounds[0])
        except (KeyError, TypeError, IndexError, ValueError):
            input_types[name] = ""
    return ReplayCsvStrategy(
        ReplayCsvStrategyConfig(
            source_csv=config.replay_csv,
            input_types=input_types,
            case_kind=config.case_kind,
            scenario_type=str(case_definition.get("scenario_type", config.case_kind)),
            target=config.target,
            reason_pattern=config.replay_reason_pattern,
            expected_count=config.replay_expected_count,
            completed_source_loop_nums=frozenset(load_completed_replay_source_loops(orchestrator, config)),
        )
    )


def build_active_learning_strategy(orchestrator, config) -> ActiveLearningStrategist:
    # The module-level name is looked up here, so tests can replace it.
    return _shared_builder(orchestrator, config, strategist_class=ActiveLearningStrategist)


def load_replay_case_definition(config_module: object, case_kind: str) -> dict[str, object]:
    loader = getattr(config_module, "get_case_definition", None)
    if callable(loader):
        return dict(loader())
    return {
        "scenario_type": getattr(config_module, "SCENARIO_TYPE", case_kind),
        "fixed_params": dict(getattr(config_module, "FIXED_PARAMS", {})),
        "param_ranges": dict(getattr(config_module, "PARAM_RANGES", {})),
    }


def load_completed_replay_source_loops(orchestrator, config) -> set[int]:
    dataset = orchestrator._build_dataset_repository(config).load_dataset()
    if dataset is None or "meta_replay_source_loop_num" not in dataset.columns:
        return set()
    completed: set[int] = set()
    for value in dataset["meta_replay_source_loop_num"].dropna().tolist():
        try:
            completed.add(int(float(value)))
        except (TypeError, ValueError):
            continue
    return completed


def active_learning_target_total(config, last_loop_num: int) -> int | None:
    """Cases to wait for, as before the strategist moved to the AWSIM target."""
    return gp_exploration_target_total(config, last_loop_num)


def replay_target_total(config, last_loop_num: int) -> int | None:
    del config, last_loop_num
    return None


__all__ = [
    "active_learning_target_total",
    "build_active_learning_strategy",
    "build_replay_strategy",
    "replay_target_total",
]
