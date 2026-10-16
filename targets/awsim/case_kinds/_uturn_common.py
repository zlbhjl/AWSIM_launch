from __future__ import annotations

from scenario_specs.uturn import (
    JAMA_PROFILES,
    NPC_START_TRIGGER_EGO_ACCELERATION,
    NPC_START_TRIGGER_EXTRA_MARGIN_RATIO,
    NPC_START_TRIGGER_MARGIN_SEC,
    NPC_START_TRIGGER_RATIO_RANGE,
    PARAM_RANGES,
    SCENARIO_TYPE,
    estimate_npc_start_speed_ratio,
)

# Legacy worker loops still read this upper bound while the v2 path migrates.
REPEAT_COUNT = 10000
# Preserve the legacy worker's full trace-collection window. The outer backend
# owns this deadline; Scenario.run() must not fabricate goal arrival at 200s.
TIMEOUT_SEC = 300

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
    "c_ego_stuck",
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
    '<> speed("ego") >= 0.1',
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

FOCUS_POINTS = [
    {"dx0": 10.09, "ego_speed": 37.98, "npc_speed": 14.20},
    {"dx0": 14.81, "ego_speed": 39.80, "npc_speed": 13.49},
    {"dx0": 10.23, "ego_speed": 35.96, "npc_speed": 17.80},
    {"dx0": 13.29, "ego_speed": 39.33, "npc_speed": 13.95},
    {"dx0": 14.16, "ego_speed": 35.43, "npc_speed": 10.02},
    {"dx0": 11.17, "ego_speed": 31.90, "npc_speed": 11.91},
]
FOCUS_NOISE = 0.05

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
    "c_ego_stuck": 1,
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


def build_case_definition(
    *,
    fixed_params: dict[str, object],
    scenario_profiles: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "scenario_type": SCENARIO_TYPE,
        "repeat_count": REPEAT_COUNT,
        "timeout_sec": TIMEOUT_SEC,
        "target_npcs": list(TARGET_NPCS),
        "param_ranges": dict(PARAM_RANGES),
        "fixed_params": dict(fixed_params),
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
            for profile in scenario_profiles
        ],
    }


def build_rule_spec() -> dict[str, object]:
    return {
        "result_labels": list(RESULT_LABELS),
        "formulas": list(FORMULAS),
        "invalid_conditions": dict(INVALID_CONDITIONS),
        "event_definitions": {
            event_id: dict(event_definition)
            for event_id, event_definition in EVENT_DEFINITIONS.items()
        },
    }


def build_strategy_settings() -> dict[str, object]:
    return {
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
