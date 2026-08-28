from __future__ import annotations

NPC_START_TRIGGER_EGO_ACCELERATION = 2.0
NPC_START_TRIGGER_MARGIN_SEC = 0.3
NPC_START_TRIGGER_EXTRA_MARGIN_RATIO = 0.03
NPC_START_TRIGGER_RATIO_RANGE = (0.85, 0.98)

SCENARIO_TYPE = "cutin"

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
    "dx0": (11.0, 13.0),
    "ego_speed": (30.0, 40.0),
    "npc_speed": (10.0, 20.0),
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
    "ego_goal_offset": 150.0,
    "npc_init_lane": "112",
    "npc_init_offset": 80.0,
    "cutin_next_lane": "111",
    "cutin_vy": 1.4,
    "acceleration": 7.0,
}


def _estimate_npc_start_speed_ratio(
    *,
    ego_speed: float,
    ego_acceleration: float = NPC_START_TRIGGER_EGO_ACCELERATION,
    margin_sec: float = NPC_START_TRIGGER_MARGIN_SEC,
    extra_margin_ratio: float = NPC_START_TRIGGER_EXTRA_MARGIN_RATIO,
) -> float:
    v_target = ego_speed / 3.6
    if v_target <= 0.0:
        return 1.0

    ratio = 1.0 - (ego_acceleration * margin_sec / v_target) - extra_margin_ratio
    lower, upper = NPC_START_TRIGGER_RATIO_RANGE
    return round(min(max(ratio, lower), upper), 4)


def _build_cutin_profile(
    *,
    profile_id: str,
    npc_speed: float,
    lane_id: str = "111",
    adjacent_lane_id: str = "112",
    acceleration: float = 7.0,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "npc_speed": npc_speed,
        "npc_init_lane": adjacent_lane_id,
        "cutin_next_lane": lane_id,
        "acceleration": acceleration,
        "ego_speed_bands": [
            _build_ego_speed_band(max_ego_speed=35.0, ego_speed_design=30.0, npc_speed=npc_speed),
            _build_ego_speed_band(max_ego_speed=float("inf"), ego_speed_design=40.0, npc_speed=npc_speed),
        ],
    }


def _build_ego_speed_band(
    *,
    max_ego_speed: float,
    ego_speed_design: float,
    npc_speed: float,
) -> dict[str, object]:
    npc_init_offset = _estimate_npc_init_offset(
        ego_speed=ego_speed_design,
        npc_speed=npc_speed,
    )
    return {
        "max_ego_speed": max_ego_speed,
        "ego_init_lane": "111",
        "ego_init_offset": 0.0,
        "ego_goal_lane": "111",
        "ego_goal_offset": _estimate_ego_goal_offset(ego_speed=ego_speed_design),
        "npc_init_offset": npc_init_offset,
        "npc_start_speed_ratio": _estimate_npc_start_speed_ratio(ego_speed=ego_speed_design),
    }


def _estimate_npc_init_offset(*, ego_speed: float, npc_speed: float, dx_reference: float = 12.0) -> float:
    v_ego = ego_speed / 3.6
    v_npc = npc_speed / 3.6
    v_relative = max(v_ego - v_npc, 0.5)

    # Reuse the known-stable 30/10 and 40/20 anchors by targeting a cut-in
    # trigger point around 94 m and 184 m respectively.
    trigger_offset = 94.0 + 9.0 * max(ego_speed - 30.0, 0.0)
    ego_warmup_distance = (v_ego * v_ego) / 4.0
    closure_distance = max(trigger_offset - ego_warmup_distance, 0.0)
    trigger_time_after_warmup = closure_distance / max(v_ego, 0.1)
    npc_init_offset = ego_warmup_distance + dx_reference + (v_relative * trigger_time_after_warmup)
    return 5.0 * round(npc_init_offset / 5.0)


def _estimate_ego_goal_offset(*, ego_speed: float) -> float:
    return round(150.0 + 6.0 * max(ego_speed - 30.0, 0.0), 1)


SCENARIO_PROFILES = [
    _build_cutin_profile(profile_id="cutin_10", npc_speed=10.0),
    _build_cutin_profile(profile_id="cutin_15", npc_speed=15.0),
    _build_cutin_profile(profile_id="cutin_20", npc_speed=20.0),
]

FOCUS_POINTS = [
    {"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0},
    {"dx0": 11.0, "ego_speed": 30.0, "npc_speed": 10.0},
    {"dx0": 13.0, "ego_speed": 40.0, "npc_speed": 20.0},
    {"dx0": 12.0, "ego_speed": 40.0, "npc_speed": 20.0},
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
            "cutin_next_lane": profile["cutin_next_lane"],
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
                "cutin_next_lane": profile["cutin_next_lane"],
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
    "NPC_START_TRIGGER_EGO_ACCELERATION",
    "NPC_START_TRIGGER_MARGIN_SEC",
    "NPC_START_TRIGGER_EXTRA_MARGIN_RATIO",
    "NPC_START_TRIGGER_RATIO_RANGE",
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
