from __future__ import annotations

from collections.abc import Mapping

from targets.awsim.case_kinds.cutout import JAMA_PROFILES


DEFAULT_CUTOUT_VY = 1.5
EFFECTIVE_REVEAL_DISTANCE_M = 1.75
BASE_HEADWAY_SEC = 2.0


def build_theory_metrics(values: Mapping[str, object]) -> dict[str, object]:
    try:
        ego_speed_kmh = float(values["ego_speed"])
        dx_f = float(values["dx_f"])
        cutout_vy = float(values.get("cutout_vy", DEFAULT_CUTOUT_VY))
    except (KeyError, TypeError, ValueError):
        return {}

    if cutout_vy <= 0.0:
        return {}

    v_ego = ego_speed_kmh / 3.6
    available_gap = (BASE_HEADWAY_SEC * v_ego) + dx_f
    reveal_delay_distance = v_ego * (EFFECTIVE_REVEAL_DISTANCE_M / cutout_vy)

    human_gap = reveal_delay_distance + _stopping_distance(v_ego, JAMA_PROFILES["human"])
    ai_gap = reveal_delay_distance + _stopping_distance(v_ego, JAMA_PROFILES["ai_aeb"])

    margin_a_human = available_gap - human_gap
    margin_a_ai = available_gap - ai_gap

    return {
        "theory_d_total_human": round(human_gap, 4),
        "theory_d_total_ai": round(ai_gap, 4),
        "theory_margin_a_human": round(margin_a_human, 4),
        "theory_margin_a_ai": round(margin_a_ai, 4),
        "theory_zone_a": _determine_zone(margin_a_human, margin_a_ai),
        "theory_margin_b_human": round(margin_a_human, 4),
        "theory_margin_b_ai": round(margin_a_ai, 4),
        "theory_zone_b": _determine_zone(margin_a_human, margin_a_ai),
    }


def _stopping_distance(initial_speed: float, profile: Mapping[str, float]) -> float:
    t_delay = float(profile["t_delay"])
    t_jerk = float(profile["t_jerk"])
    a_max = float(profile["a_max"])

    if a_max <= 0.0:
        return float("inf")
    if t_jerk <= 0.0:
        t_jerk = 1e-5

    d_delay = initial_speed * t_delay
    v_drop_jerk = 0.5 * a_max * t_jerk
    v_after_jerk = initial_speed - v_drop_jerk

    if v_after_jerk < 0.0:
        t_stop = (2.0 * initial_speed * t_jerk / a_max) ** 0.5
        d_jerk = initial_speed * t_stop - (1.0 / 6.0) * (a_max / t_jerk) * (t_stop**3)
        return d_delay + d_jerk

    d_jerk = initial_speed * t_jerk - (1.0 / 6.0) * a_max * (t_jerk**2)
    d_braking = (v_after_jerk**2) / (2.0 * a_max)
    return d_delay + d_jerk + d_braking


def _determine_zone(margin_human: float, margin_ai: float) -> str:
    if margin_human >= 0.0 and margin_ai >= 0.0:
        return "A"
    if margin_human < 0.0 and margin_ai >= 0.0:
        return "B"
    if margin_human < 0.0 and margin_ai < 0.0:
        return "C"
    return "D"
