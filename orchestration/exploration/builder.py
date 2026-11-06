"""Build the shared GP exploration from an OrchestratorConfig (docs/exploration_unification_plan.md G4)."""

from __future__ import annotations

import importlib

from orchestration.cache_policy import resolve_strategy_cache_size
from orchestration.exploration.active_learning import ActiveLearningStrategist
from orchestration.region_sampling import REGION_SAMPLING_MODES, build_region_sampling_strategy

# Modes in which the strategist decides when to stop, so there is no fixed total.
OPEN_ENDED_MODES = frozenset({"boundary_gap", "dkw", "verify_consistency", "replay"})


def build_gp_exploration_strategy(orchestrator, config, *, spec=None, strategist_class=None):
    """The GP exploration for any target; its statistical modes run on the region sampling.

    ``config.config_module`` names the target's settings module (PARAM_RANGES,
    FIXED_PARAMS, TARGET_PRIORITIES, phase limits, ...); ``spec`` is the
    target's ExplorationSpec.
    """
    if config.run_mode in REGION_SAMPLING_MODES:
        # The statistical modes run on the shared statistical path (orchestration/region_sampling.py).
        return build_region_sampling_strategy(
            orchestrator, config, region_vocabulary=getattr(spec, "region_vocabulary", None),
        )
    strategist_class = strategist_class or ActiveLearningStrategist
    strategy_config = importlib.import_module(config.config_module)
    dataset_repository = orchestrator._build_dataset_repository(config)
    return strategist_class(
        spec=spec,
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
        max_samples=config.max_samples,
        cache_size=resolve_strategy_cache_size(
            worker_count=orchestrator._resolve_worker_count(config),
            run_mode=config.run_mode,
            resume_from=config.resume_from,
            max_samples=config.max_samples,
            explicit_cache_size=config.cache_size,
        ),
    )


def gp_exploration_target_total(config, last_loop_num: int) -> int | None:
    """Cases the orchestrator waits for in a GP exploration run."""
    # The statistical modes run on the region sampling, which stops by itself: it
    # issues at most max_samples samples and waits for their results. A count of
    # finished cases would stop the run before a lost sample's retry is back
    # (verification V10b, 2026-11-05).
    if config.run_mode in OPEN_ENDED_MODES or config.run_mode in REGION_SAMPLING_MODES:
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


__all__ = ["OPEN_ENDED_MODES", "build_gp_exploration_strategy", "gp_exploration_target_total"]
