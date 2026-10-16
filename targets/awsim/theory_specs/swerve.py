from __future__ import annotations

import math
from collections.abc import Mapping

from targets.awsim.case_kinds.swerve import FIXED_PARAMS, JAMA_PROFILES


DEFAULT_SWERVE_VY = float(FIXED_PARAMS["swerve_vy"])
DEFAULT_SWERVE_NY = float(FIXED_PARAMS["swerve_ny"])
DEFAULT_SWERVE_DIS = float(FIXED_PARAMS["swerve_dis"])

# Treat conflict start as the point where the oncoming NPC center has crossed
# far enough into the ego lane to matter, without depending on lane geometry.
EFFECTIVE_CONFLICT_ENTRY_M = 1.75


def build_theory_metrics(values: Mapping[str, object]) -> dict[str, object]:
    try:
        dx0 = float(values["dx0"])
        ego_speed_kmh = float(values["ego_speed"])
        npc_speed_kmh = float(values["npc_speed"])
        swerve_vy = float(values.get("swerve_vy", DEFAULT_SWERVE_VY))
        swerve_ny = float(values.get("swerve_ny", DEFAULT_SWERVE_NY))
        swerve_dis = float(values.get("swerve_dis", DEFAULT_SWERVE_DIS))
    except (KeyError, TypeError, ValueError):
        return {}

    if swerve_vy <= 0.0 or swerve_ny <= 0.0 or swerve_dis < 0.0:
        return {}

    v_ego = ego_speed_kmh / 3.6
    v_npc = npc_speed_kmh / 3.6
    npc_longitudinal_speed = _npc_longitudinal_speed(v_npc=v_npc, swerve_vy=swerve_vy)
    if npc_longitudinal_speed <= 0.0:
        return {}

    human_gap_a, human_gap_b = _required_gaps_for_profile(
        v_ego=v_ego,
        npc_longitudinal_speed=npc_longitudinal_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        profile=JAMA_PROFILES["human"],
    )
    ai_gap_a, ai_gap_b = _required_gaps_for_profile(
        v_ego=v_ego,
        npc_longitudinal_speed=npc_longitudinal_speed,
        swerve_vy=swerve_vy,
        swerve_ny=swerve_ny,
        swerve_dis=swerve_dis,
        profile=JAMA_PROFILES["ai_aeb"],
    )

    margin_a_human = dx0 - human_gap_a
    margin_a_ai = dx0 - ai_gap_a
    margin_b_human = dx0 - human_gap_b
    margin_b_ai = dx0 - ai_gap_b

    return {
        "theory_d_total_human": round(human_gap_a, 4),
        "theory_d_total_ai": round(ai_gap_a, 4),
        "theory_margin_a_human": round(margin_a_human, 4),
        "theory_margin_a_ai": round(margin_a_ai, 4),
        "theory_zone_a": _determine_zone(margin_a_human, margin_a_ai),
        "theory_margin_b_human": round(margin_b_human, 4),
        "theory_margin_b_ai": round(margin_b_ai, 4),
        "theory_zone_b": _determine_zone(margin_b_human, margin_b_ai),
    }


def _required_gaps_for_profile(
    *,
    v_ego: float,
    npc_longitudinal_speed: float,
    swerve_vy: float,
    swerve_ny: float,
    swerve_dis: float,
    profile: Mapping[str, float],
) -> tuple[float, float]:
    stopping_distance, stopping_time = _stopping_distance_and_time(v_ego, profile)
    conflict_entry_time = EFFECTIVE_CONFLICT_ENTRY_M / swerve_vy
    conflict_clear_time = (2.0 * swerve_ny / swerve_vy) + (swerve_dis / npc_longitudinal_speed)

    conservative_gap = stopping_distance + (npc_longitudinal_speed * conflict_entry_time)
    finite_exposure_gap = _distance_during_braking(
        initial_speed=v_ego,
        profile=profile,
        elapsed_time=conflict_clear_time,
    )
    finite_exposure_gap += npc_longitudinal_speed * conflict_clear_time

    return conservative_gap, finite_exposure_gap


def _npc_longitudinal_speed(*, v_npc: float, swerve_vy: float) -> float:
    squared = (v_npc * v_npc) - (swerve_vy * swerve_vy)
    return math.sqrt(max(squared, 0.0))


def _stopping_distance_and_time(
    initial_speed: float,
    profile: Mapping[str, float],
) -> tuple[float, float]:
    t_delay = float(profile["t_delay"])
    t_jerk = float(profile["t_jerk"])
    a_max = float(profile["a_max"])

    if a_max <= 0.0:
        return float("inf"), float("inf")
    if t_jerk <= 0.0:
        t_jerk = 1e-5

    d_delay = initial_speed * t_delay
    v_drop_jerk = 0.5 * a_max * t_jerk
    v_after_jerk = initial_speed - v_drop_jerk

    if v_after_jerk < 0.0:
        t_stop = (2.0 * initial_speed * t_jerk / a_max) ** 0.5
        d_jerk = initial_speed * t_stop - (1.0 / 6.0) * (a_max / t_jerk) * (t_stop**3)
        return d_delay + d_jerk, t_delay + t_stop

    d_jerk = initial_speed * t_jerk - (1.0 / 6.0) * a_max * (t_jerk**2)
    d_braking = (v_after_jerk**2) / (2.0 * a_max)
    t_braking = v_after_jerk / a_max
    return d_delay + d_jerk + d_braking, t_delay + t_jerk + t_braking


def _distance_during_braking(
    *,
    initial_speed: float,
    profile: Mapping[str, float],
    elapsed_time: float,
) -> float:
    if elapsed_time <= 0.0:
        return 0.0

    stopping_distance, stopping_time = _stopping_distance_and_time(initial_speed, profile)
    if elapsed_time >= stopping_time:
        return stopping_distance

    t_delay = float(profile["t_delay"])
    t_jerk = max(float(profile["t_jerk"]), 1e-5)
    a_max = float(profile["a_max"])

    if elapsed_time <= t_delay:
        return initial_speed * elapsed_time

    d_delay = initial_speed * t_delay
    elapsed_time -= t_delay

    jerk_stop_time = (2.0 * initial_speed * t_jerk / a_max) ** 0.5 if a_max > 0.0 else float("inf")
    if jerk_stop_time <= t_jerk and elapsed_time <= jerk_stop_time:
        return d_delay + (
            initial_speed * elapsed_time
            - (1.0 / 6.0) * (a_max / t_jerk) * (elapsed_time**3)
        )

    if elapsed_time <= t_jerk:
        return d_delay + (
            initial_speed * elapsed_time
            - (1.0 / 6.0) * (a_max / t_jerk) * (elapsed_time**3)
        )

    d_jerk = initial_speed * t_jerk - (1.0 / 6.0) * a_max * (t_jerk**2)
    v_after_jerk = initial_speed - (0.5 * a_max * t_jerk)
    elapsed_time -= t_jerk

    return d_delay + d_jerk + (v_after_jerk * elapsed_time) - (0.5 * a_max * (elapsed_time**2))


def _determine_zone(margin_human: float, margin_ai: float) -> str:
    if margin_human >= 0.0 and margin_ai >= 0.0:
        return "A"
    if margin_human < 0.0 and margin_ai >= 0.0:
        return "B"
    if margin_human < 0.0 and margin_ai < 0.0:
        return "C"
    return "D"
