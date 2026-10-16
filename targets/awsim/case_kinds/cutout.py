from __future__ import annotations

import math


SCENARIO_TYPE = "cutout"

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
    '[] ~ collision("ego", "npc2")',
    '[] ttc("npc2") >= 1.5',
    '[] ttc("npc2") >= 1.3',
    '[] ttc("npc2") >= 1.2',
    '[] ttc("npc2") >= 1.1',
    '[] ttc("npc2") >= 0.9',
    '[] ttc("npc2") >= 0.7',
    '[] ttc("npc2") >= 0.5',
    '[] ttc("npc2") >= 0.3',
    '[] pos-diff("ego", "npc2") >= 4.0',
    '<> speed("npc1") >= 0.1',
]

TARGET_NPCS = ["npc2"]

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
    "ego_speed": (30.0, 40.0),
    "cutout_vy": (1.0, 2.0),
    "dx_f": (5.0, 15.0),
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
    "ego_init_lane": "111",
    "ego_init_offset": 0.0,
    "ego_goal_lane": "111",
    "ego_goal_offset": 210.0,
    "cutout_next_lane": "112",
}

SPAWN_TRIGGER_EGO_ACCELERATION = 2.0
SPAWN_TRIGGER_NPC_ACCELERATION = 500.0
SPAWN_TRIGGER_MARGIN_SEC = 0.3
SPAWN_TRIGGER_EXTRA_MARGIN_RATIO = 0.05
SPAWN_TRIGGER_RATIO_RANGE = (0.85, 0.98)
LANE_CHANGE_TRIGGER_SPEED_RATIO = 1.0

EGO_GOAL_ACCELERATION = 2.0
EGO_GOAL_EVENT_BUFFER_SEC = 20.0
EGO_GOAL_STATIC_MARGIN_M = 23.0
EGO_GOAL_ROUND_STEP_M = 10.0
EGO_GOAL_LEGACY_MINIMUMS = (
    (32.5, 180.0),
    (37.5, 210.0),
    (float("inf"), 240.0),
)


def _estimate_spawn_trigger_speed_ratio(
    *,
    ego_speed_kmh: float,
    ego_acceleration: float = SPAWN_TRIGGER_EGO_ACCELERATION,
    npc_acceleration: float = SPAWN_TRIGGER_NPC_ACCELERATION,
    lane_change_speed_ratio: float = LANE_CHANGE_TRIGGER_SPEED_RATIO,
    margin_sec: float = SPAWN_TRIGGER_MARGIN_SEC,
    extra_margin_ratio: float = SPAWN_TRIGGER_EXTRA_MARGIN_RATIO,
) -> float:
    v_target = ego_speed_kmh / 3.6
    if v_target <= 0.0:
        return 1.0

    npc_ready_time = (lane_change_speed_ratio * v_target) / max(npc_acceleration, 1e-5)
    r_s_min = 1.0 - (ego_acceleration * (npc_ready_time + margin_sec) / v_target)
    ratio = r_s_min - extra_margin_ratio
    return round(
        min(max(ratio, SPAWN_TRIGGER_RATIO_RANGE[0]), SPAWN_TRIGGER_RATIO_RANGE[1]),
        4,
    )


def _estimate_ego_goal_offset(
    *,
    ego_speed_kmh: float,
    ego_acceleration: float = EGO_GOAL_ACCELERATION,
    event_buffer_sec: float = EGO_GOAL_EVENT_BUFFER_SEC,
    static_margin_m: float = EGO_GOAL_STATIC_MARGIN_M,
    round_step_m: float = EGO_GOAL_ROUND_STEP_M,
    ego_init_offset: float = float(FIXED_PARAMS["ego_init_offset"]),
) -> float:
    v_ego = ego_speed_kmh / 3.6
    if v_ego <= 0.0:
        return float(FIXED_PARAMS["ego_goal_offset"])

    warmup_distance = (v_ego * v_ego) / max(2.0 * ego_acceleration, 1e-5)
    event_distance = v_ego * event_buffer_sec
    goal_offset = ego_init_offset + warmup_distance + event_distance + static_margin_m
    rounded_goal_offset = round(goal_offset / max(round_step_m, 1e-5)) * round_step_m
    return max(rounded_goal_offset, _legacy_minimum_ego_goal_offset(ego_speed_kmh=ego_speed_kmh))


def _legacy_minimum_ego_goal_offset(*, ego_speed_kmh: float) -> float:
    for max_ego_speed, goal_offset in EGO_GOAL_LEGACY_MINIMUMS:
        if ego_speed_kmh < max_ego_speed:
            return goal_offset
    return float(FIXED_PARAMS["ego_goal_offset"])


def _build_cutout_speed_band(
    *,
    max_ego_speed: float,
    ego_speed_design: float,
) -> dict[str, object]:
    return {
        "max_ego_speed": max_ego_speed,
        "ego_init_lane": "111",
        "ego_init_offset": 0.0,
        "ego_goal_lane": "111",
        "ego_goal_offset": _estimate_ego_goal_offset(
            ego_speed_kmh=ego_speed_design,
        ),
        "spawn_trigger_speed_ratio": _estimate_spawn_trigger_speed_ratio(
            ego_speed_kmh=ego_speed_design,
        ),
    }


SCENARIO_PROFILES = [
    {
        "profile_id": "default",
        "cutout_next_lane": "112",
        "ego_speed_bands": [
            _build_cutout_speed_band(
                max_ego_speed=32.5,
                ego_speed_design=30.0,
            ),
            _build_cutout_speed_band(
                max_ego_speed=37.5,
                ego_speed_design=35.0,
            ),
            _build_cutout_speed_band(
                max_ego_speed=float("inf"),
                ego_speed_design=40.0,
            ),
        ],
    }
]

FOCUS_POINTS = [
    {"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 10.0},
    {"ego_speed": 35.0, "cutout_vy": 1.5, "dx_f": 10.0},
    {"ego_speed": 40.0, "cutout_vy": 1.2, "dx_f": 8.0},
    {"ego_speed": 40.0, "cutout_vy": 1.8, "dx_f": 12.0},
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
            "cutout_next_lane": profile["cutout_next_lane"],
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
                "cutout_next_lane": profile["cutout_next_lane"],
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
    "FIXED_PARAMS",
    "SPAWN_TRIGGER_EGO_ACCELERATION",
    "SPAWN_TRIGGER_NPC_ACCELERATION",
    "SPAWN_TRIGGER_MARGIN_SEC",
    "SPAWN_TRIGGER_EXTRA_MARGIN_RATIO",
    "SPAWN_TRIGGER_RATIO_RANGE",
    "LANE_CHANGE_TRIGGER_SPEED_RATIO",
    "EGO_GOAL_ACCELERATION",
    "EGO_GOAL_STATIC_MARGIN_M",
    "EGO_GOAL_ROUND_STEP_M",
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
