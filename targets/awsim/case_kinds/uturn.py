from __future__ import annotations


SCENARIO_TYPE = "uturn"

# Legacy worker loops still read this upper bound while the v2 path migrates.
REPEAT_COUNT = 10000
TIMEOUT_SEC = 200

RESULT_LABELS = [
    "c_collision",
    "c_ttc_1.5",
    "c_ttc_1.3",
    "c_ttc_1.2",
    "c_ttc_1.1",
    "c_ttc_0.9",
    "c_ttc_0.7",
    "c_ttc_0.5",
    "c_ttc_0.3",
    "c_pos_diff_4.0",
    "c_npc_stuck",
]

FORMULAS = [
    '[] ~ collision("ego", "npc1")',
    '[] ttc("npc1") >= 1.5',
    '[] ttc("npc1") >= 1.3',
    '[] ttc("npc1") >= 1.2',
    '[] ttc("npc1") >= 1.1',
    '[] ttc("npc1") >= 0.9',
    '[] ttc("npc1") >= 0.7',
    '[] ttc("npc1") >= 0.5',
    '[] ttc("npc1") >= 0.3',
    '[] pos-diff("ego", "npc1") >= 4.0',
    '<> speed("npc1") >= 0.1',
]

TARGET_NPCS = ["npc1"]

TARGET_PRIORITIES = [
    "c_collision",
    "c_ttc_0.3",
    "c_ttc_0.5",
    "c_ttc_0.7",
    "c_ttc_0.9",
    "c_ttc_1.1",
    "c_ttc_1.2",
    "c_ttc_1.3",
    "c_ttc_1.5",
]

PARAM_RANGES = {
    "dx0": (10.0, 25.0),
    "ego_speed": (30.0, 40.0),
    "npc_speed": (10.0, 25.0),
}

INITIAL_EXPLORATION_LIMIT = 100
MIN_SAMPLES = 500
MAX_SAMPLES = 10000

STABILITY_REFERENCE_POINTS = 2000
STABILITY_HISTORY_LENGTH = 50
STABILITY_HYSTERESIS = (0.40, 0.60)
STABILITY_SHIFT_THRESHOLD = 0.01
STABILITY_REQUIRED_STREAK = 3
STEP2_MAX_EXPLORATION = 500

MARGIN_RANGE = (0.3, 0.48)
MARGIN_MAX_UNCERTAINTY = 0.05

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
            },
            {
                "max_ego_speed": 37.5,
                "ego_init_lane": "514",
                "ego_init_offset": 17.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
            },
            {
                "max_ego_speed": float("inf"),
                "ego_init_lane": "282",
                "ego_init_offset": 4.0,
                "ego_goal_lane": "124",
                "ego_goal_offset": 18.0,
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
            },
            {
                "max_ego_speed": 37.5,
                "ego_init_lane": "514",
                "ego_init_offset": 17.0,
                "ego_goal_lane": "516",
                "ego_goal_offset": 20.0,
            },
            {
                "max_ego_speed": float("inf"),
                "ego_init_lane": "282",
                "ego_init_offset": 4.0,
                "ego_goal_lane": "124",
                "ego_goal_offset": 18.0,
            },
        ],
    },
]

FOCUS_POINTS = [
    {"dx0": 10.09, "ego_speed": 37.98, "npc_speed": 14.20},
    {"dx0": 14.81, "ego_speed": 39.80, "npc_speed": 13.49},
    {"dx0": 10.23, "ego_speed": 35.96, "npc_speed": 17.80},
    {"dx0": 13.29, "ego_speed": 39.33, "npc_speed": 13.95},
    {"dx0": 14.16, "ego_speed": 35.43, "npc_speed": 10.02},
    {"dx0": 11.17, "ego_speed": 31.90, "npc_speed": 11.91},
]
FOCUS_NOISE = 0.05

JAMA_PROFILES = {
    "human": {
        "t_delay": 0.75,
        "t_jerk": 0.6,
        "a_max": 7.58,
    },
    "ai_aeb": {
        "t_delay": 0.1,
        "t_jerk": 0.1,
        "a_max": 8.33,
    },
}

TTC_EDGE_THRESHOLD = 1.5

BOUNDARY_GAP_GRID_SIZE = 8
BOUNDARY_GAP_MAX_CASES = 12
BOUNDARY_GAP_MIN_SAMPLES = 2
BOUNDARY_GAP_MAX_SAMPLES = 12
BOUNDARY_GAP_COLLISION_RATIO_RANGE = (0.15, 0.85)
BOUNDARY_GAP_TTC_THRESHOLD = 1.1

DKW_TARGET_METRIC = "z_margin"
DKW_TARGET_METRICS = ["min_ttc", "min_distance"]

BINOMIAL_CI_TARGET = "c_collision"
BINOMIAL_CI_METHOD = "wilson"
BINOMIAL_CI_CONFIDENCE = 0.95
BINOMIAL_CI_TARGET_WIDTH = 0.02
BINOMIAL_CI_MIN_SAMPLES = 100

INVALID_CONDITIONS = {
    "c_npc_stuck": 1,
}

EVENT_DEFINITIONS = {
    label: {
        "dataset_filter": None,
        "error_filter": f"output.{label}",
        "target_column": label,
    }
    for label in RESULT_LABELS
    if label.startswith("c_")
}

CASE_DEFINITION = {
    "scenario_type": SCENARIO_TYPE,
    "repeat_count": REPEAT_COUNT,
    "timeout_sec": TIMEOUT_SEC,
    "target_npcs": list(TARGET_NPCS),
    "param_ranges": dict(PARAM_RANGES),
    "fixed_params": dict(FIXED_PARAMS),
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
        for profile in SCENARIO_PROFILES
    ],
}

RULE_SPEC = {
    "result_labels": list(RESULT_LABELS),
    "formulas": list(FORMULAS),
    "invalid_conditions": dict(INVALID_CONDITIONS),
    "event_definitions": {
        event_id: dict(event_definition)
        for event_id, event_definition in EVENT_DEFINITIONS.items()
    },
}

STRATEGY_SETTINGS = {
    "target_priorities": list(TARGET_PRIORITIES),
    "initial_exploration_limit": INITIAL_EXPLORATION_LIMIT,
    "min_samples": MIN_SAMPLES,
    "max_samples": MAX_SAMPLES,
    "stability_reference_points": STABILITY_REFERENCE_POINTS,
    "stability_history_length": STABILITY_HISTORY_LENGTH,
    "stability_hysteresis": tuple(STABILITY_HYSTERESIS),
    "stability_shift_threshold": STABILITY_SHIFT_THRESHOLD,
    "stability_required_streak": STABILITY_REQUIRED_STREAK,
    "step2_max_exploration": STEP2_MAX_EXPLORATION,
    "margin_range": tuple(MARGIN_RANGE),
    "margin_max_uncertainty": MARGIN_MAX_UNCERTAINTY,
    "focus_points": [dict(point) for point in FOCUS_POINTS],
    "focus_noise": FOCUS_NOISE,
    "dkw_target_metric": DKW_TARGET_METRIC,
    "dkw_target_metrics": list(DKW_TARGET_METRICS),
    "binomial_ci_target": BINOMIAL_CI_TARGET,
    "binomial_ci_method": BINOMIAL_CI_METHOD,
    "binomial_ci_confidence": BINOMIAL_CI_CONFIDENCE,
    "binomial_ci_target_width": BINOMIAL_CI_TARGET_WIDTH,
    "binomial_ci_min_samples": BINOMIAL_CI_MIN_SAMPLES,
}


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

__all__ = [
    "SCENARIO_TYPE",
    "REPEAT_COUNT",
    "TIMEOUT_SEC",
    "RESULT_LABELS",
    "FORMULAS",
    "TARGET_NPCS",
    "TARGET_PRIORITIES",
    "PARAM_RANGES",
    "INITIAL_EXPLORATION_LIMIT",
    "MIN_SAMPLES",
    "MAX_SAMPLES",
    "STABILITY_REFERENCE_POINTS",
    "STABILITY_HISTORY_LENGTH",
    "STABILITY_HYSTERESIS",
    "STABILITY_SHIFT_THRESHOLD",
    "STABILITY_REQUIRED_STREAK",
    "STEP2_MAX_EXPLORATION",
    "MARGIN_RANGE",
    "MARGIN_MAX_UNCERTAINTY",
    "FIXED_PARAMS",
    "SCENARIO_PROFILES",
    "FOCUS_POINTS",
    "FOCUS_NOISE",
    "JAMA_PROFILES",
    "TTC_EDGE_THRESHOLD",
    "BOUNDARY_GAP_GRID_SIZE",
    "BOUNDARY_GAP_MAX_CASES",
    "BOUNDARY_GAP_MIN_SAMPLES",
    "BOUNDARY_GAP_MAX_SAMPLES",
    "BOUNDARY_GAP_COLLISION_RATIO_RANGE",
    "BOUNDARY_GAP_TTC_THRESHOLD",
    "DKW_TARGET_METRIC",
    "DKW_TARGET_METRICS",
    "BINOMIAL_CI_TARGET",
    "BINOMIAL_CI_METHOD",
    "BINOMIAL_CI_CONFIDENCE",
    "BINOMIAL_CI_TARGET_WIDTH",
    "BINOMIAL_CI_MIN_SAMPLES",
    "INVALID_CONDITIONS",
    "EVENT_DEFINITIONS",
    "CASE_DEFINITION",
    "RULE_SPEC",
    "STRATEGY_SETTINGS",
    "get_case_definition",
    "get_rule_spec",
    "get_strategy_settings",
]
