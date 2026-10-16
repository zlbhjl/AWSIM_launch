from __future__ import annotations

import math

NPC_START_TRIGGER_EGO_ACCELERATION = 2.0
NPC_START_TRIGGER_MARGIN_SEC = 0.3
NPC_START_TRIGGER_EXTRA_MARGIN_RATIO = 0.03
NPC_START_TRIGGER_RATIO_RANGE = (0.85, 0.98)

SCENARIO_TYPE = "swerve"

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
    "dx0": (24.0, 43.0),
    "ego_speed": (30.0, 40.0),
    "npc_speed": (10.0, 15.0),
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
    "ego_init_lane": "355",
    "ego_init_offset": 10.0,
    "ego_goal_lane": "214",
    "ego_goal_offset": 10.0,
    "npc_init_lane": "205",
    "npc_init_offset": 60.0,
    "swerve_vy": 1.2,
    "swerve_ny": 1.8,
    "swerve_dis": 2.0,
    "swerve_right": True,
    "acceleration": 7.0,
}

NPC_INIT_EGO_ACCELERATION = 2.0
NPC_INIT_MARGIN_SEC = 0.3
NPC_INIT_ROUND_STEP_M = 1.0

EGO_GOAL_ACCELERATION = 2.0
EGO_GOAL_STATIC_MARGIN_M = 5.0
EGO_GOAL_ROUND_STEP_M = 1.0


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


def _estimate_npc_init_offset(
    *,
    ego_speed: float,
    npc_speed: float,
    swerve_vy: float = float(FIXED_PARAMS["swerve_vy"]),
    swerve_ny: float = float(FIXED_PARAMS["swerve_ny"]),
    swerve_dis: float = float(FIXED_PARAMS["swerve_dis"]),
    ego_acceleration: float = NPC_INIT_EGO_ACCELERATION,
    npc_acceleration: float = float(FIXED_PARAMS["acceleration"]),
    margin_sec: float = NPC_INIT_MARGIN_SEC,
    round_step_m: float = NPC_INIT_ROUND_STEP_M,
) -> float:
    legacy_minimum = _legacy_minimum_npc_init_offset(
        ego_speed=ego_speed,
        npc_speed=npc_speed,
    )
    reference_ego_speed, reference_npc_speed = _reference_speed_pair(
        ego_speed=ego_speed,
        npc_speed=npc_speed,
    )
    required_gap = _estimate_required_initial_gap(
        ego_speed=ego_speed,
        npc_speed=npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        ego_acceleration=ego_acceleration,
        npc_acceleration=npc_acceleration,
        margin_sec=margin_sec,
    )
    reference_gap = _estimate_required_initial_gap(
        ego_speed=reference_ego_speed,
        npc_speed=reference_npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        ego_acceleration=ego_acceleration,
        npc_acceleration=npc_acceleration,
        margin_sec=margin_sec,
    )
    extra_gap = max(required_gap - reference_gap, 0.0)
    rounded_extra_gap = round(extra_gap / max(round_step_m, 1e-5)) * round_step_m
    return round(legacy_minimum + rounded_extra_gap, 1)


def _estimate_required_initial_gap(
    *,
    ego_speed: float,
    npc_speed: float,
    swerve_vy: float,
    swerve_ny: float,
    swerve_dis: float,
    ego_acceleration: float,
    npc_acceleration: float,
    margin_sec: float,
) -> float:
    v_ego = ego_speed / 3.6
    v_npc = npc_speed / 3.6
    v_npc_long = _npc_longitudinal_speed(v_npc=v_npc, swerve_vy=swerve_vy)
    ego_warmup_distance = (v_ego * v_ego) / max(2.0 * ego_acceleration, 1e-5)
    npc_ready_time = (v_npc / max(npc_acceleration, 1e-5)) + margin_sec
    swerve_entry_time = _estimate_swerve_event_time(
        npc_speed=npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
    )
    closing_speed = v_ego + v_npc_long
    return ego_warmup_distance + closing_speed * (npc_ready_time + swerve_entry_time)


def _estimate_ego_goal_offset(
    *,
    ego_speed: float,
    npc_speed: float,
    swerve_vy: float = float(FIXED_PARAMS["swerve_vy"]),
    swerve_ny: float = float(FIXED_PARAMS["swerve_ny"]),
    swerve_dis: float = float(FIXED_PARAMS["swerve_dis"]),
    ego_acceleration: float = EGO_GOAL_ACCELERATION,
    static_margin_m: float = EGO_GOAL_STATIC_MARGIN_M,
    round_step_m: float = EGO_GOAL_ROUND_STEP_M,
) -> float:
    legacy_minimum = _legacy_minimum_ego_goal_offset(ego_speed=ego_speed)
    reference_ego_speed, reference_npc_speed = _reference_speed_pair(
        ego_speed=ego_speed,
        npc_speed=npc_speed,
    )
    required_distance = _estimate_required_goal_distance(
        ego_speed=ego_speed,
        npc_speed=npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        ego_acceleration=ego_acceleration,
        static_margin_m=static_margin_m,
    )
    reference_distance = _estimate_required_goal_distance(
        ego_speed=reference_ego_speed,
        npc_speed=reference_npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        ego_acceleration=ego_acceleration,
        static_margin_m=static_margin_m,
    )
    extra_distance = max(required_distance - reference_distance, 0.0)
    rounded_extra_distance = round(extra_distance / max(round_step_m, 1e-5)) * round_step_m
    return round(legacy_minimum + rounded_extra_distance, 1)


def _estimate_required_goal_distance(
    *,
    ego_speed: float,
    npc_speed: float,
    swerve_vy: float,
    swerve_ny: float,
    swerve_dis: float,
    ego_acceleration: float,
    static_margin_m: float,
) -> float:
    v_ego = ego_speed / 3.6
    warmup_distance = (v_ego * v_ego) / max(2.0 * ego_acceleration, 1e-5)
    event_time = _estimate_swerve_event_time(
        npc_speed=npc_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
    )
    return warmup_distance + (v_ego * event_time) + static_margin_m


def _estimate_swerve_event_time(
    *,
    npc_speed: float,
    swerve_vy: float,
    swerve_ny: float,
    swerve_dis: float,
) -> float:
    if swerve_vy <= 0.0:
        return 0.0
    v_npc_long = _npc_longitudinal_speed(v_npc=npc_speed / 3.6, swerve_vy=swerve_vy)
    if v_npc_long <= 0.0:
        return 0.0
    return (2.0 * swerve_ny / swerve_vy) + (swerve_dis / v_npc_long)


def _npc_longitudinal_speed(*, v_npc: float, swerve_vy: float) -> float:
    return math.sqrt(max((v_npc * v_npc) - (swerve_vy * swerve_vy), 0.0))


def _legacy_minimum_npc_init_offset(*, ego_speed: float, npc_speed: float) -> float:
    if ego_speed < 35.0:
        if npc_speed < 12.5:
            return 60.0
        return 55.0
    return 62.0


def _legacy_minimum_ego_goal_offset(*, ego_speed: float) -> float:
    if ego_speed < 35.0:
        return 10.0
    return 26.0


def _reference_speed_pair(*, ego_speed: float, npc_speed: float) -> tuple[float, float]:
    reference_ego_speed = 30.0 if ego_speed < 35.0 else 40.0
    reference_npc_speed = 10.0 if npc_speed < 12.5 else 15.0
    return reference_ego_speed, reference_npc_speed


def _build_swerve_profile(
    *,
    profile_id: str,
    npc_speed: float,
    acceleration: float = 7.0,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "npc_speed": npc_speed,
        "npc_init_lane": "205",
        "acceleration": acceleration,
        "ego_speed_bands": [
            {
                "max_ego_speed": 35.0,
                "ego_init_lane": "355",
                "ego_init_offset": 10.0,
                "ego_goal_lane": "214",
                "ego_goal_offset": _estimate_ego_goal_offset(
                    ego_speed=30.0,
                    npc_speed=npc_speed,
                ),
                "npc_init_offset": _estimate_npc_init_offset(
                    ego_speed=30.0,
                    npc_speed=npc_speed,
                ),
                "npc_start_speed_ratio": _estimate_npc_start_speed_ratio(ego_speed=30.0),
            },
            {
                "max_ego_speed": float("inf"),
                "ego_init_lane": "268",
                "ego_init_offset": 0.0,
                "ego_goal_lane": "214",
                "ego_goal_offset": _estimate_ego_goal_offset(
                    ego_speed=40.0,
                    npc_speed=npc_speed,
                ),
                "npc_init_offset": _estimate_npc_init_offset(
                    ego_speed=40.0,
                    npc_speed=npc_speed,
                ),
                "npc_start_speed_ratio": _estimate_npc_start_speed_ratio(ego_speed=40.0),
            },
        ],
    }


SCENARIO_PROFILES = [
    _build_swerve_profile(
        profile_id="swerve_10",
        npc_speed=10.0,
    ),
    _build_swerve_profile(
        profile_id="swerve_15",
        npc_speed=15.0,
    ),
]

FOCUS_POINTS = [
    {"dx0": 27.0, "ego_speed": 30.0, "npc_speed": 10.0},
    {"dx0": 29.0, "ego_speed": 30.0, "npc_speed": 15.0},
    {"dx0": 34.0, "ego_speed": 40.0, "npc_speed": 10.0},
    {"dx0": 35.0, "ego_speed": 40.0, "npc_speed": 15.0},
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
