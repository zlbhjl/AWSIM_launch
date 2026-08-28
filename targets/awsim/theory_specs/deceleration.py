from __future__ import annotations

from collections.abc import Mapping

from targets.awsim.case_kinds.deceleration import FIXED_PARAMS, JAMA_PROFILES


DEFAULT_HEADWAY_SEC = float(FIXED_PARAMS["spawn_headway_sec"])
DEFAULT_NPC_DECELERATION = float(FIXED_PARAMS["npc_deceleration"])


def build_theory_metrics(values: Mapping[str, object]) -> dict[str, object]:
    try:
        ego_speed_kmh = float(values["ego_speed"])
        headway_sec = float(values.get("spawn_headway_sec", DEFAULT_HEADWAY_SEC))
        npc_deceleration = float(values.get("npc_deceleration", DEFAULT_NPC_DECELERATION))
    except (KeyError, TypeError, ValueError):
        return {}

    if headway_sec <= 0.0 or npc_deceleration <= 0.0:
        return {}

    v_ego = ego_speed_kmh / 3.6
    dx0 = headway_sec * v_ego

    human_stop_distance, human_stop_time = _stopping_distance_and_time(v_ego, JAMA_PROFILES["human"])
    ai_stop_distance, ai_stop_time = _stopping_distance_and_time(v_ego, JAMA_PROFILES["ai_aeb"])

    human_npc_progress = _npc_progress_until_time(
        initial_speed=v_ego,
        deceleration=npc_deceleration,
        elapsed_time=human_stop_time,
    )
    ai_npc_progress = _npc_progress_until_time(
        initial_speed=v_ego,
        deceleration=npc_deceleration,
        elapsed_time=ai_stop_time,
    )

    margin_a_human = dx0 - human_stop_distance
    margin_a_ai = dx0 - ai_stop_distance
    margin_b_human = (dx0 + human_npc_progress) - human_stop_distance
    margin_b_ai = (dx0 + ai_npc_progress) - ai_stop_distance

    return {
        "theory_d_total_human": round(human_stop_distance, 4),
        "theory_d_total_ai": round(ai_stop_distance, 4),
        "theory_margin_a_human": round(margin_a_human, 4),
        "theory_margin_a_ai": round(margin_a_ai, 4),
        "theory_zone_a": _determine_zone(margin_a_human, margin_a_ai),
        "theory_margin_b_human": round(margin_b_human, 4),
        "theory_margin_b_ai": round(margin_b_ai, 4),
        "theory_zone_b": _determine_zone(margin_b_human, margin_b_ai),
    }


def _npc_progress_until_time(
    *,
    initial_speed: float,
    deceleration: float,
    elapsed_time: float,
) -> float:
    stop_time = initial_speed / deceleration
    effective_time = min(max(elapsed_time, 0.0), stop_time)
    return (initial_speed * effective_time) - (0.5 * deceleration * (effective_time**2))


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


def _determine_zone(margin_human: float, margin_ai: float) -> str:
    if margin_human >= 0.0 and margin_ai >= 0.0:
        return "A"
    if margin_human < 0.0 and margin_ai >= 0.0:
        return "B"
    if margin_human < 0.0 and margin_ai < 0.0:
        return "C"
    return "D"
