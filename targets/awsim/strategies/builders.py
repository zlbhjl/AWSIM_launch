"""Build the AWSIM strategies from an OrchestratorConfig (registered in targets/awsim/plugin.py).

Moved from orchestration/orchestrator.py; the strategies are built exactly as before.
"""

from __future__ import annotations

import importlib

from orchestration.cache_policy import resolve_strategy_cache_size
from runtime.repository.statistical_history import StatisticalHistoryRepository
from targets.statistical import build_statistical_region_policy, load_statistical_target_profile

from .active_learning import ActiveLearningStrategist
from .replay import ReplayCsvStrategy, ReplayCsvStrategyConfig

# Modes in which the strategist decides when to stop, so there is no fixed total.
OPEN_ENDED_MODES = frozenset({"boundary_gap", "dkw", "verify_consistency", "replay"})


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
    strategy_config = importlib.import_module(config.config_module)
    dataset_repository = orchestrator._build_dataset_repository(config)
    statistical_profile = load_statistical_target_profile(config.target)
    return ActiveLearningStrategist(
        scenario_name=config.case_kind,
        target=config.target,
        config=strategy_config,
        focus_points=(
            [dict(point) for point in config.focus_points]
            if config.focus_points is not None
            else None
        ),
        run_mode=config.run_mode,
        dataset_repository=dataset_repository,
        statistical_history_repository=StatisticalHistoryRepository(
            scenario_name=config.case_kind,
            traces_dir=dataset_repository.paths.traces_dir,
        ),
        dkw_bounds=config.dkw_bounds,
        dkw_region=config.dkw_region,
        dkw_pure_smc=config.dkw_pure_smc,
        dkw_simultaneous=config.dkw_simultaneous,
        max_samples=config.max_samples,
        binomial_target=config.binomial_target,
        binomial_method=config.binomial_method,
        binomial_confidence=config.binomial_confidence,
        binomial_target_width=config.binomial_target_width,
        binomial_min_samples=config.binomial_min_samples,
        binomial_anytime_valid=config.binomial_anytime_valid,
        sprt_target=config.sprt_target,
        sprt_p0=config.sprt_p0,
        sprt_p1=config.sprt_p1,
        sprt_beta=config.sprt_beta,
        sprt_confidence=config.sprt_confidence,
        sprt_min_samples=config.sprt_min_samples,
        ebstop_target=config.ebstop_target,
        ebstop_epsilon=config.ebstop_epsilon,
        ebstop_value_range=config.ebstop_value_range,
        ebstop_confidence=config.ebstop_confidence,
        ebstop_max_samples=config.ebstop_max_samples,
        cache_size=resolve_strategy_cache_size(
            worker_count=orchestrator._resolve_worker_count(config),
            run_mode=config.run_mode,
            resume_from=config.resume_from,
            max_samples=config.max_samples,
            explicit_cache_size=config.cache_size,
        ),
        statistical_region_policy=build_statistical_region_policy(
            config.target,
            case_kind=config.case_kind,
            config_module_name=config.config_module,
        ),
        dkw_minimum_value=statistical_profile.minimum_dkw_value,
    )


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
    if config.run_mode in OPEN_ENDED_MODES:
        return None
    if config.max_samples is not None:
        return last_loop_num + int(config.max_samples)
    if config.run_mode == "binomial_ci":
        return None
    try:
        strategy_config = importlib.import_module(config.config_module)
    except ImportError:
        return None
    repeat_count = getattr(strategy_config, "REPEAT_COUNT", None)
    return None if repeat_count is None else last_loop_num + int(repeat_count)


def replay_target_total(config, last_loop_num: int) -> int | None:
    del config, last_loop_num
    return None


__all__ = [
    "active_learning_target_total",
    "build_active_learning_strategy",
    "build_replay_strategy",
    "replay_target_total",
]
