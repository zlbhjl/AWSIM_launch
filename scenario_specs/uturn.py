"""Shared physical and trigger definitions for the JAMA U-turn scenario.

This module deliberately contains no AWSIM, ROS, lane-map, or solver imports.
Targets may add their own geometry and runtime details while using these values
as the common definition of sampled parameters and trigger semantics.
"""

from __future__ import annotations

from collections.abc import Mapping


SCENARIO_TYPE = "uturn"

PARAM_RANGES = {
    "dx0": (10.0, 25.0),
    "ego_speed": (30.0, 40.0),
    "npc_speed": (10.0, 25.0),
}

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

NPC_START_TRIGGER_EGO_ACCELERATION = 2.0
NPC_START_TRIGGER_MARGIN_SEC = 0.3
NPC_START_TRIGGER_EXTRA_MARGIN_RATIO = 0.03
NPC_START_TRIGGER_RATIO_RANGE = (0.85, 0.98)
# AWSIM selects one of these configured ego-speed bands before constructing
# the scenario.  The ratio is calculated from the representative band speed,
# not from every sampled speed inside that band.
NPC_START_TRIGGER_EGO_SPEED_BANDS = (
    (32.5, 30.0),
    (37.5, 35.0),
    (float("inf"), 40.0),
)

UTURN_START_CONDITION = "longitudinal_distance_to_ego <= dx0"
NPC_START_CONDITION = "ego_speed_mps >= npc_start_speed_ratio * ego_target_speed_mps"


def estimate_npc_start_speed_ratio(
    *,
    ego_speed: float,
    ego_acceleration: float = NPC_START_TRIGGER_EGO_ACCELERATION,
    margin_sec: float = NPC_START_TRIGGER_MARGIN_SEC,
    extra_margin_ratio: float = NPC_START_TRIGGER_EXTRA_MARGIN_RATIO,
) -> float:
    """Return the AWSIM-compatible NPC start-speed ratio for ego speed in km/h."""
    v_target_mps = ego_speed / 3.6
    if v_target_mps <= 0.0:
        return 1.0

    ratio = 1.0 - (ego_acceleration * margin_sec / v_target_mps) - extra_margin_ratio
    lower, upper = NPC_START_TRIGGER_RATIO_RANGE
    return round(min(max(ratio, lower), upper), 4)


def npc_start_speed_threshold_mps(
    *,
    ego_target_speed_mps: float,
    npc_start_speed_ratio: float,
) -> float:
    """Return the ego speed at which the NPC begins its lane-following action."""
    ratio = min(max(float(npc_start_speed_ratio), 0.0), 1.0)
    return ratio * float(ego_target_speed_mps)


def resolve_awsim_npc_start_speed_ratio(*, ego_speed: float) -> float:
    """Resolve the ratio exactly as the current AWSIM U-turn speed bands do."""
    speed = float(ego_speed)
    for maximum, representative in NPC_START_TRIGGER_EGO_SPEED_BANDS:
        if speed < maximum:
            return estimate_npc_start_speed_ratio(ego_speed=representative)
    raise ValueError(f"No AWSIM U-turn ego-speed band matched {speed}")


def should_start_npc(
    *,
    ego_speed_mps: float,
    ego_target_speed_mps: float,
    npc_start_speed_ratio: float,
) -> bool:
    """Evaluate the common NPC lane-following trigger."""
    return float(ego_speed_mps) >= npc_start_speed_threshold_mps(
        ego_target_speed_mps=ego_target_speed_mps,
        npc_start_speed_ratio=npc_start_speed_ratio,
    )


def should_start_uturn(
    *,
    longitudinal_distance_to_ego_m: float,
    dx0_m: float,
) -> bool:
    """Evaluate the common U-turn trigger at the inclusive JAMA distance bound."""
    return float(longitudinal_distance_to_ego_m) <= float(dx0_m)


def copy_jama_profiles() -> dict[str, dict[str, float]]:
    """Return a mutable copy for consumers that need target-local configuration."""
    return {
        name: {key: float(value) for key, value in profile.items()}
        for name, profile in JAMA_PROFILES.items()
    }


def validate_sampled_parameters(values: Mapping[str, object]) -> None:
    """Validate the three sampled U-turn parameters against their shared bounds."""
    for key, (lower, upper) in PARAM_RANGES.items():
        try:
            value = float(values[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Missing or invalid U-turn parameter: {key}") from exc
        if not lower <= value <= upper:
            raise ValueError(
                f"U-turn parameter {key}={value} is outside [{lower}, {upper}]"
            )


__all__ = [
    "JAMA_PROFILES",
    "NPC_START_CONDITION",
    "NPC_START_TRIGGER_EGO_ACCELERATION",
    "NPC_START_TRIGGER_EXTRA_MARGIN_RATIO",
    "NPC_START_TRIGGER_EGO_SPEED_BANDS",
    "NPC_START_TRIGGER_MARGIN_SEC",
    "NPC_START_TRIGGER_RATIO_RANGE",
    "PARAM_RANGES",
    "SCENARIO_TYPE",
    "UTURN_START_CONDITION",
    "copy_jama_profiles",
    "estimate_npc_start_speed_ratio",
    "npc_start_speed_threshold_mps",
    "resolve_awsim_npc_start_speed_ratio",
    "should_start_npc",
    "should_start_uturn",
    "validate_sampled_parameters",
]
