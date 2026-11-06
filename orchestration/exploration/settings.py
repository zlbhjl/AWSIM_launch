"""Reading a target's settings module (PARAM_RANGES, FIXED_PARAMS, strategy settings).

Shared by the GP exploration and the region sampling of the statistical modes
(orchestration/region_sampling.py).
"""

from __future__ import annotations


class _RegionPolicyWithVocabulary:
    """A region policy that also declares region names (from the target's ExplorationSpec)."""

    def __init__(self, policy, vocabulary):
        self.policy = policy
        self.vocabulary = vocabulary

    def build_filter_frame(self, point):
        return self.policy.build_filter_frame(point)


def _with_vocabulary(policy, vocabulary):
    from contracts.statistical_region import PassthroughStatisticalRegionPolicy

    if vocabulary is None or getattr(policy, "vocabulary", None) is not None:
        return policy
    return _RegionPolicyWithVocabulary(policy or PassthroughStatisticalRegionPolicy(), vocabulary)


def _load_case_definition_from_config(config: object, scenario_name: str) -> dict[str, object]:
    explicit_loader = getattr(config, "get_case_definition", None)
    if callable(explicit_loader):
        loaded = dict(explicit_loader())
        return {
            "scenario_type": loaded.get("scenario_type", scenario_name),
            "repeat_count": int(loaded.get("repeat_count", 0)),
            "timeout_sec": float(loaded.get("timeout_sec", 200.0)),
            "target_npcs": list(loaded.get("target_npcs", [])),
            "param_ranges": dict(loaded.get("param_ranges", {})),
            "fixed_params": dict(loaded.get("fixed_params", {})),
        }

    return {
        "scenario_type": getattr(config, "SCENARIO_TYPE", scenario_name),
        "repeat_count": int(getattr(config, "REPEAT_COUNT", 0)),
        "timeout_sec": float(getattr(config, "TIMEOUT_SEC", 200.0)),
        "target_npcs": list(getattr(config, "TARGET_NPCS", [])),
        "param_ranges": dict(getattr(config, "PARAM_RANGES", {})),
        "fixed_params": dict(getattr(config, "FIXED_PARAMS", {})),
    }


def _load_strategy_settings_from_config(config: object) -> dict[str, object]:
    explicit_loader = getattr(config, "get_strategy_settings", None)
    if callable(explicit_loader):
        return dict(explicit_loader())

    return {
        "target_priorities": list(getattr(config, "TARGET_PRIORITIES", [])),
        "initial_exploration_limit": getattr(config, "INITIAL_EXPLORATION_LIMIT", 100),
        "min_samples": getattr(config, "MIN_SAMPLES", 500),
        "max_samples": getattr(config, "MAX_SAMPLES", 2000),
        "stability_reference_points": getattr(config, "STABILITY_REFERENCE_POINTS", 2000),
        "stability_history_length": getattr(config, "STABILITY_HISTORY_LENGTH", 50),
        "stability_hysteresis": getattr(config, "STABILITY_HYSTERESIS", (0.40, 0.60)),
        "stability_shift_threshold": getattr(config, "STABILITY_SHIFT_THRESHOLD", 0.01),
        "stability_required_streak": getattr(config, "STABILITY_REQUIRED_STREAK", 3),
        "step2_max_exploration": getattr(config, "STEP2_MAX_EXPLORATION", 500),
        "margin_range": getattr(config, "MARGIN_RANGE", (0.3, 0.48)),
        "margin_max_uncertainty": getattr(config, "MARGIN_MAX_UNCERTAINTY", 0.05),
        "focus_points": list(getattr(config, "FOCUS_POINTS", [])),
        "focus_noise": getattr(config, "FOCUS_NOISE", 0.05),
        "dkw_target_metric": getattr(config, "DKW_TARGET_METRIC", "min_ttc"),
        "dkw_target_metrics": list(
            getattr(config, "DKW_TARGET_METRICS", ["min_ttc", "min_distance"])
        ),
        "binomial_ci_target": getattr(config, "BINOMIAL_CI_TARGET", "c_collision"),
        "binomial_ci_method": getattr(config, "BINOMIAL_CI_METHOD", "wilson"),
        "binomial_ci_confidence": getattr(config, "BINOMIAL_CI_CONFIDENCE", 0.95),
        "binomial_ci_target_width": getattr(config, "BINOMIAL_CI_TARGET_WIDTH", 0.02),
        "binomial_ci_min_samples": getattr(config, "BINOMIAL_CI_MIN_SAMPLES", 100),
        "sprt_target": getattr(config, "SPRT_TARGET", "c_collision"),
        "sprt_p0": getattr(config, "SPRT_P0", 0.1),
        "sprt_p1": getattr(config, "SPRT_P1", 0.01),
        "sprt_beta": getattr(config, "SPRT_BETA", 0.05),
        "sprt_confidence": getattr(config, "SPRT_CONFIDENCE", 0.95),
        "sprt_min_samples": getattr(config, "SPRT_MIN_SAMPLES", 0),
        "ebstop_target": getattr(config, "EBSTOP_TARGET", "min_ttc"),
        "ebstop_epsilon": getattr(config, "EBSTOP_EPSILON", 0.1),
        "ebstop_value_range": getattr(config, "EBSTOP_VALUE_RANGE", (0.0, 10.0)),
        "ebstop_confidence": getattr(config, "EBSTOP_CONFIDENCE", 0.95),
        "ebstop_max_samples": getattr(config, "EBSTOP_MAX_SAMPLES", 2000),
    }
