from __future__ import annotations

from collections.abc import Mapping

from targets.awsim.case_kinds.cutin import FIXED_PARAMS, JAMA_PROFILES


# The cut-in conflict begins before the NPC center fully reaches the ego lane.
# We approximate this with half a standard lane width so the theory stays
# conservative without depending on map geometry.
EFFECTIVE_LATERAL_DISTANCE_M = 1.75
DEFAULT_CUTIN_VY = float(FIXED_PARAMS["cutin_vy"])


def build_theory_metrics(values: Mapping[str, object]) -> dict[str, object]:
    try:
        dx0 = float(values["dx0"])
        ego_speed_kmh = float(values["ego_speed"])
        npc_speed_kmh = float(values["npc_speed"])
        cutin_vy = float(values.get("cutin_vy", DEFAULT_CUTIN_VY))
    except (KeyError, TypeError, ValueError):
        return {}

    if cutin_vy <= 0.0:
        return {}

    v_ego = ego_speed_kmh / 3.6
    v_npc = npc_speed_kmh / 3.6

    human_gap, human_time = _required_gap_for_profile(
        v_ego=v_ego,
        v_npc=v_npc,
        cutin_vy=cutin_vy,
        profile=JAMA_PROFILES["human"],
    )
    ai_gap, ai_time = _required_gap_for_profile(
        v_ego=v_ego,
        v_npc=v_npc,
        cutin_vy=cutin_vy,
        profile=JAMA_PROFILES["ai_aeb"],
    )

    margin_a_human = dx0 - human_gap
    margin_a_ai = dx0 - ai_gap
    margin_b_human = dx0 - _required_gap_with_npc_progress(
        v_ego=v_ego,
        v_npc=v_npc,
        cutin_vy=cutin_vy,
        stopping_distance=_stopping_distance_and_time(v_ego, JAMA_PROFILES["human"])[0],
        stopping_time=human_time,
    )
    margin_b_ai = dx0 - _required_gap_with_npc_progress(
        v_ego=v_ego,
        v_npc=v_npc,
        cutin_vy=cutin_vy,
        stopping_distance=_stopping_distance_and_time(v_ego, JAMA_PROFILES["ai_aeb"])[0],
        stopping_time=ai_time,
    )

    return {
        "theory_d_total_human": round(human_gap, 4),
        "theory_d_total_ai": round(ai_gap, 4),
        "theory_margin_a_human": round(margin_a_human, 4),
        "theory_margin_a_ai": round(margin_a_ai, 4),
        "theory_zone_a": _determine_zone(margin_a_human, margin_a_ai),
        "theory_margin_b_human": round(margin_b_human, 4),
        "theory_margin_b_ai": round(margin_b_ai, 4),
        "theory_zone_b": _determine_zone(margin_b_human, margin_b_ai),
    }


def _required_gap_for_profile(
    *,
    v_ego: float,
    v_npc: float,
    cutin_vy: float,
    profile: Mapping[str, float],
) -> tuple[float, float]:
    stopping_distance, stopping_time = _stopping_distance_and_time(v_ego, profile)
    merge_closure = _merge_phase_closure(v_ego=v_ego, v_npc=v_npc, cutin_vy=cutin_vy)
    return merge_closure + stopping_distance, stopping_time


def _required_gap_with_npc_progress(
    *,
    v_ego: float,
    v_npc: float,
    cutin_vy: float,
    stopping_distance: float,
    stopping_time: float,
) -> float:
    merge_closure = _merge_phase_closure(v_ego=v_ego, v_npc=v_npc, cutin_vy=cutin_vy)
    post_merge_gap = max(stopping_distance - (v_npc * stopping_time), 0.0)
    return merge_closure + post_merge_gap


def _merge_phase_closure(*, v_ego: float, v_npc: float, cutin_vy: float) -> float:
    merge_time = EFFECTIVE_LATERAL_DISTANCE_M / cutin_vy
    npc_longitudinal_speed = max((v_npc**2 - cutin_vy**2) ** 0.5, 0.0)
    relative_speed = max(v_ego - npc_longitudinal_speed, 0.0)
    return relative_speed * merge_time


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
        d_jerk = (
            initial_speed * t_stop
            - (1.0 / 6.0) * (a_max / t_jerk) * (t_stop**3)
        )
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
