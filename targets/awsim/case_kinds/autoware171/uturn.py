from __future__ import annotations

from targets.awsim.case_kinds._uturn_common import (
    BINOMIAL_CI_CONFIDENCE,
    BINOMIAL_CI_METHOD,
    BINOMIAL_CI_MIN_SAMPLES,
    BINOMIAL_CI_TARGET,
    BINOMIAL_CI_TARGET_WIDTH,
    BOUNDARY_GAP_COLLISION_RATIO_RANGE,
    BOUNDARY_GAP_GRID_SIZE,
    BOUNDARY_GAP_MAX_CASES,
    BOUNDARY_GAP_MAX_SAMPLES,
    BOUNDARY_GAP_MIN_SAMPLES,
    BOUNDARY_GAP_TTC_THRESHOLD,
    DKW_TARGET_METRIC,
    DKW_TARGET_METRICS,
    EVENT_DEFINITIONS,
    FOCUS_NOISE,
    FOCUS_POINTS,
    FORMULAS,
    INITIAL_EXPLORATION_LIMIT,
    INVALID_CONDITIONS,
    JAMA_PROFILES,
    MARGIN_MAX_UNCERTAINTY,
    MARGIN_RANGE,
    MAX_SAMPLES,
    MIN_SAMPLES,
    PARAM_RANGES,
    REPEAT_COUNT,
    RESULT_LABELS,
    SCENARIO_TYPE,
    STABILITY_HISTORY_LENGTH,
    STABILITY_HYSTERESIS,
    STABILITY_REFERENCE_POINTS,
    STABILITY_REQUIRED_STREAK,
    STABILITY_SHIFT_THRESHOLD,
    STEP2_MAX_EXPLORATION,
    TARGET_NPCS,
    TARGET_PRIORITIES,
    TIMEOUT_SEC,
    TTC_EDGE_THRESHOLD,
    build_case_definition,
    build_rule_spec,
    build_strategy_settings,
    estimate_npc_start_speed_ratio,
)


FIXED_PARAMS = {
    "ego_init_lane": "514",
    "ego_init_offset": 38,
    "ego_goal_lane": "516",
    "ego_goal_offset": 20,
    "npc_init_lane": "521",
    "npc_init_offset": 32,
    "uturn_next_lane": "511",
    "acceleration": 7.0,
}

SCENARIO_PROFILES = [
    {
        "profile_id": "right_10",
        "npc_speed": 10.0,
        "npc_init_lane": "521",
        "npc_init_offset": 32.0,
        "uturn_next_lane": "511",
        "acceleration": 7.0,
        "ego_speed_bands": [
            {
                "max_ego_speed": 32.5,
                "ego_init_lane": "514",
                "ego_init_offset": 30.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=30.0),
            },
            {
                "max_ego_speed": 37.5,
                "ego_init_lane": "514",
                "ego_init_offset": 17.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=35.0),
            },
            {
                "max_ego_speed": float("inf"),
                "ego_init_lane": "282",
                "ego_init_offset": 4.0,
                "ego_goal_lane": "124",
                "ego_goal_offset": 18.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=40.0),
            },
        ],
    },
    {
        "profile_id": "right_15",
        "npc_speed": 15.0,
        "npc_init_lane": "521",
        "npc_init_offset": 32.0,
        "uturn_next_lane": "511",
        "acceleration": 7.0,
        "ego_speed_bands": [
            {
                "max_ego_speed": 32.5,
                "ego_init_lane": "514",
                "ego_init_offset": 38.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=30.0),
            },
            {
                "max_ego_speed": 37.5,
                "ego_init_lane": "514",
                "ego_init_offset": 17.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=35.0),
            },
            {
                "max_ego_speed": float("inf"),
                "ego_init_lane": "282",
                "ego_init_offset": 4.0,
                "ego_goal_lane": "124",
                "ego_goal_offset": 18.0,
                "npc_start_speed_ratio": estimate_npc_start_speed_ratio(ego_speed=40.0),
            },
        ],
    },
]

CASE_DEFINITION = build_case_definition(
    fixed_params=FIXED_PARAMS,
    scenario_profiles=SCENARIO_PROFILES,
)
RULE_SPEC = build_rule_spec()
STRATEGY_SETTINGS = build_strategy_settings()


def get_case_definition() -> dict[str, object]:
    return {
        "scenario_type": CASE_DEFINITION["scenario_type"],
        "repeat_count": CASE_DEFINITION["repeat_count"],
        "timeout_sec": CASE_DEFINITION["timeout_sec"],
        "target_npcs": list(CASE_DEFINITION["target_npcs"]),
        "param_ranges": dict(CASE_DEFINITION["param_ranges"]),
        "fixed_params": dict(CASE_DEFINITION["fixed_params"]),
        "scenario_profiles": [
            {
                "profile_id": profile["profile_id"],
                "npc_speed": profile["npc_speed"],
                "npc_init_lane": profile["npc_init_lane"],
                "npc_init_offset": profile["npc_init_offset"],
                "uturn_next_lane": profile["uturn_next_lane"],
                "acceleration": profile["acceleration"],
                "ego_speed_bands": [dict(band) for band in profile["ego_speed_bands"]],
            }
            for profile in CASE_DEFINITION["scenario_profiles"]
        ],
    }


def get_rule_spec() -> dict[str, object]:
    return {
        "result_labels": list(RULE_SPEC["result_labels"]),
        "formulas": list(RULE_SPEC["formulas"]),
        "invalid_conditions": dict(RULE_SPEC["invalid_conditions"]),
        "event_definitions": {
            event_id: dict(event_definition)
            for event_id, event_definition in RULE_SPEC["event_definitions"].items()
        },
    }


def get_strategy_settings() -> dict[str, object]:
    return {
        "target_priorities": list(STRATEGY_SETTINGS["target_priorities"]),
        "initial_exploration_limit": STRATEGY_SETTINGS["initial_exploration_limit"],
        "min_samples": STRATEGY_SETTINGS["min_samples"],
        "max_samples": STRATEGY_SETTINGS["max_samples"],
        "stability_reference_points": STRATEGY_SETTINGS["stability_reference_points"],
        "stability_history_length": STRATEGY_SETTINGS["stability_history_length"],
        "stability_hysteresis": tuple(STRATEGY_SETTINGS["stability_hysteresis"]),
        "stability_shift_threshold": STRATEGY_SETTINGS["stability_shift_threshold"],
        "stability_required_streak": STRATEGY_SETTINGS["stability_required_streak"],
        "step2_max_exploration": STRATEGY_SETTINGS["step2_max_exploration"],
        "margin_range": tuple(STRATEGY_SETTINGS["margin_range"]),
        "margin_max_uncertainty": STRATEGY_SETTINGS["margin_max_uncertainty"],
        "focus_points": [dict(point) for point in STRATEGY_SETTINGS["focus_points"]],
        "focus_noise": STRATEGY_SETTINGS["focus_noise"],
        "dkw_target_metric": STRATEGY_SETTINGS["dkw_target_metric"],
        "dkw_target_metrics": list(STRATEGY_SETTINGS["dkw_target_metrics"]),
        "binomial_ci_target": STRATEGY_SETTINGS["binomial_ci_target"],
        "binomial_ci_method": STRATEGY_SETTINGS["binomial_ci_method"],
        "binomial_ci_confidence": STRATEGY_SETTINGS["binomial_ci_confidence"],
        "binomial_ci_target_width": STRATEGY_SETTINGS["binomial_ci_target_width"],
        "binomial_ci_min_samples": STRATEGY_SETTINGS["binomial_ci_min_samples"],
    }
