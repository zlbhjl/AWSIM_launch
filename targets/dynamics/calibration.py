"""Load and validate bundled empirical controller calibrations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


AUTOWARE171_UTURN_CONTROLLER_KIND = "autoware171_uturn_calibrated"
AUTOWARE171_UTURN_GEOMETRY_KIND = "autoware171_uturn_start_linear_v2"
_AUTOWARE171_UTURN_PATH = (
    Path(__file__).with_name("calibrations") / "autoware171_uturn_braking.json"
)
_AUTOWARE171_SCREENING_PATH = (
    Path(__file__).with_name("calibrations") / "autoware171_uturn_screening.json"
)
_AUTOWARE171_GEOMETRY_PATH = (
    Path(__file__).with_name("calibrations") / "autoware171_uturn_start_geometry.json"
)


def load_autoware171_uturn_braking_profile() -> tuple[dict[str, float], dict[str, object]]:
    payload = json.loads(_AUTOWARE171_UTURN_PATH.read_text(encoding="utf-8"))
    if payload.get("controller_kind") != AUTOWARE171_UTURN_CONTROLLER_KIND:
        raise ValueError("invalid Autoware U-turn calibration controller_kind")
    profile_value = payload.get("profile")
    if not isinstance(profile_value, Mapping):
        raise ValueError("Autoware U-turn calibration is missing profile")
    profile = {key: float(profile_value[key]) for key in ("t_delay", "t_jerk", "a_max")}
    if profile["t_delay"] < 0.0 or profile["t_jerk"] < 0.0 or profile["a_max"] <= 0.0:
        raise ValueError("Autoware U-turn calibration contains invalid values")
    provenance = {
        key: payload[key]
        for key in (
            "schema_version",
            "provenance",
            "selection_seed",
            "inspected_file_count",
            "usable_trace_count",
            "calibration_trace_count",
            "validation_trace_count",
            "empirical_intervals",
            "held_out_errors",
            "method",
        )
    }
    return profile, provenance


def load_autoware171_uturn_screening_margin() -> tuple[float, dict[str, object]]:
    """Clearance margin [m] below which a case is an AWSIM re-validation candidate."""
    payload = json.loads(_AUTOWARE171_SCREENING_PATH.read_text(encoding="utf-8"))
    margin = float(payload["screening_clearance_margin_m"])
    if not margin >= 0.0:
        raise ValueError("screening_clearance_margin_m must be non-negative")
    provenance = {
        key: payload[key]
        for key in (
            "schema_version",
            "decision_kind",
            "selection_rule",
            "development_data",
            "calibrated_for_controller_kind",
        )
    }
    return margin, provenance


def predict_autoware171_uturn_start_geometry(
    *,
    dx0: float,
    ego_speed: float,
    npc_speed: float,
) -> tuple[dict[str, float], dict[str, object]]:
    payload = json.loads(_AUTOWARE171_GEOMETRY_PATH.read_text(encoding="utf-8"))
    if payload.get("geometry_kind") != AUTOWARE171_UTURN_GEOMETRY_KIND:
        raise ValueError("invalid Autoware U-turn geometry calibration kind")
    coefficients = payload.get("coefficients")
    if not isinstance(coefficients, Mapping):
        raise ValueError("Autoware U-turn geometry calibration is missing coefficients")
    features = (1.0, float(dx0), float(ego_speed), float(npc_speed))
    predictions: dict[str, float] = {}
    for target, values in coefficients.items():
        if not isinstance(values, list) or len(values) != len(features):
            raise ValueError(f"invalid geometry coefficients for {target}")
        predictions[str(target)] = sum(
            float(coefficient) * feature
            for coefficient, feature in zip(values, features, strict=True)
        )
    required = {
        "longitudinal_center_m",
        "lateral_center_m",
        "ego_speed_mps",
        "npc_speed_mps",
        "npc_heading_rad",
        "turn_radius_m",
        "turn_angle_rad",
    }
    if not required <= predictions.keys():
        raise ValueError("Autoware U-turn geometry calibration is incomplete")
    provenance = {
        key: payload[key]
        for key in (
            "schema_version",
            "geometry_kind",
            "feature_order",
            "training_trace_count",
            "validation_trace_count",
            "validation_mae",
            "trigger_definition",
            "turn_definition",
            "coordinate_frame",
            "vehicle_center_offsets_m",
            "source_split",
        )
    }
    return predictions, provenance


__all__ = [
    "AUTOWARE171_UTURN_CONTROLLER_KIND",
    "AUTOWARE171_UTURN_GEOMETRY_KIND",
    "load_autoware171_uturn_braking_profile",
    "load_autoware171_uturn_screening_margin",
    "predict_autoware171_uturn_start_geometry",
]
