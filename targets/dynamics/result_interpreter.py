"""Interpret a dynamics ODE artifact as a common EvaluationRecord."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import numpy as np

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RawRunResult, RunStatus
from scenario_specs.uturn import JAMA_PROFILES
from theoretical_calculator import TheoreticalSafetyCalculator


@dataclass(frozen=True)
class InterpretationContext:
    target: str = "dynamics"
    source_module: str = "targets.dynamics.result_interpreter"
    schema_version: int = 1


class DynamicsResultInterpreter:
    def __init__(self, context: InterpretationContext | None = None):
        self.context = context or InterpretationContext()

    def interpret_raw_run_result(self, raw_run_result: RawRunResult) -> EvaluationRecord:
        raw_path = raw_run_result.evidence.get("raw_result_json")
        if raw_run_result.status is not RunStatus.SUCCESS or not raw_path:
            status = (
                raw_run_result.status
                if raw_run_result.status is not RunStatus.SUCCESS
                else RunStatus.ANALYSIS_ERROR
            )
            return self._error_record(
                raw_run_result,
                status=status,
                message="missing_raw_result_json" if not raw_path else "raw_run_not_successful",
            )
        try:
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            return self._interpret_payload(raw_run_result, payload)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return self._error_record(
                raw_run_result,
                status=RunStatus.ANALYSIS_ERROR,
                message=str(exc),
            )

    def _interpret_payload(
        self,
        raw_run_result: RawRunResult,
        payload: Mapping[str, object],
    ) -> EvaluationRecord:
        if str(payload.get("model_id")) != "uturn":
            raise ValueError("unsupported dynamics model_id")
        inputs = payload.get("input")
        execution = payload.get("execution")
        trace_value = payload.get("trace_csv")
        if not isinstance(inputs, Mapping) or not isinstance(execution, Mapping):
            raise ValueError("dynamics raw result has invalid input or execution")
        if not isinstance(trace_value, str) or not trace_value:
            raise ValueError("dynamics raw result is missing trace_csv")

        trace_path = Path(trace_value).expanduser().resolve()
        trace = _load_trace(trace_path)
        collision_distance_m = float(execution["collision_distance_m"])
        collision_model = str(execution.get("collision_model", "center_distance"))
        vehicle_geometry = execution.get("vehicle_geometry_m")
        metrics = _safety_metrics(
            trace,
            collision_distance_m=collision_distance_m,
            collision_model=collision_model,
            vehicle_geometry_m=vehicle_geometry,
        )
        if "min_clearance" in metrics and "screening_clearance_margin_m" in execution:
            # Two decision modes share one trajectory:
            #   judgment  -> c_collision (bodies overlap; AWSIM-substitute use)
            #   screening -> c_screening_candidate (clearance below a margin
            #                calibrated so no development AWSIM collision is
            #                missed; candidates are re-validated in AWSIM)
            margin = float(execution["screening_clearance_margin_m"])
            metrics["c_screening_candidate"] = int(float(metrics["min_clearance"]) < margin)
        metrics.update(_jama_metrics(inputs))
        events = payload.get("events")
        if not isinstance(events, Mapping):
            raise ValueError("dynamics raw result has invalid events")
        metrics["c_npc_stuck"] = int(events.get("npc_start_time_sec") is None)
        metrics["c_ego_stuck"] = int(float(np.max(trace["ego_speed_mps"])) < 0.1)
        metrics["uturn_started"] = int(events.get("uturn_start_time_sec") is not None)
        metrics["uturn_completed"] = int(events.get("uturn_end_time_sec") is not None)

        evidence = dict(raw_run_result.evidence)
        evidence["trace_csv"] = str(trace_path)
        return EvaluationRecord(
            case_id=raw_run_result.case_id,
            target=raw_run_result.target,
            case_kind=raw_run_result.case_kind,
            status=RunStatus.SUCCESS,
            input={str(key): value for key, value in inputs.items()},
            output=metrics,
            evidence=evidence,
            meta=ensure_evaluation_meta(
                {
                    "model_id": "uturn",
                    "solver_kind": execution.get("solver_kind", "ode"),
                    "collision_distance_m": collision_distance_m,
                    "collision_model": collision_model,
                    "screening_clearance_margin_m": execution.get("screening_clearance_margin_m"),
                    "screening_provenance": execution.get("screening_provenance"),
                    "ttc_model": execution.get("ttc_model"),
                    "vehicle_geometry_m": vehicle_geometry,
                    "controller_kind": execution.get("controller_kind"),
                    "controller_provenance": execution.get("controller_provenance"),
                    "events": dict(events),
                    "raw_run_status": raw_run_result.status.value,
                    "raw_run_meta": dict(raw_run_result.meta),
                },
                source_module=self.context.source_module,
                schema_version=self.context.schema_version,
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )

    def _error_record(
        self,
        raw_run_result: RawRunResult,
        *,
        status: RunStatus,
        message: str,
    ) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw_run_result.case_id,
            target=raw_run_result.target,
            case_kind=raw_run_result.case_kind,
            status=status,
            evidence=dict(raw_run_result.evidence),
            meta=ensure_evaluation_meta(
                {
                    "error_message": message,
                    "raw_run_status": raw_run_result.status.value,
                    "raw_run_meta": dict(raw_run_result.meta),
                },
                source_module=self.context.source_module,
                schema_version=self.context.schema_version,
            ),
        )


def _load_trace(path: Path) -> dict[str, np.ndarray]:
    required = {
        "ego_x_m",
        "ego_y_m",
        "ego_heading_rad",
        "ego_speed_mps",
        "npc_x_m",
        "npc_y_m",
        "npc_heading_rad",
        "npc_speed_mps",
    }
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError("dynamics trace is missing required columns")
        rows = list(reader)
    if not rows:
        raise ValueError("dynamics trace has no rows")
    return {
        key: np.asarray([float(row[key]) for row in rows], dtype=float)
        for key in required
    }


def _safety_metrics(
    trace: Mapping[str, np.ndarray],
    *,
    collision_distance_m: float,
    collision_model: str = "center_distance",
    vehicle_geometry_m: object = None,
) -> dict[str, object]:
    relative_x = trace["npc_x_m"] - trace["ego_x_m"]
    relative_y = trace["npc_y_m"] - trace["ego_y_m"]
    distances = np.hypot(relative_x, relative_y)
    ego_velocity_x = trace["ego_speed_mps"] * np.cos(trace["ego_heading_rad"])
    ego_velocity_y = trace["ego_speed_mps"] * np.sin(trace["ego_heading_rad"])
    npc_velocity_x = trace["npc_speed_mps"] * np.cos(trace["npc_heading_rad"])
    npc_velocity_y = trace["npc_speed_mps"] * np.sin(trace["npc_heading_rad"])
    relative_velocity_x = npc_velocity_x - ego_velocity_x
    relative_velocity_y = npc_velocity_y - ego_velocity_y
    closing_speed = -(
        relative_x * relative_velocity_x + relative_y * relative_velocity_y
    ) / np.maximum(distances, 1e-12)
    min_distance = float(np.min(distances))
    min_clearance: float | None = None
    if collision_model == "obb":
        geometry = _validate_vehicle_geometry(vehicle_geometry_m)
        overlaps = _obb_overlaps(trace, geometry)
        collision = bool(np.any(overlaps))
        min_clearance = 0.0 if collision else float(np.min(_obb_clearances(trace, geometry)))
        ttc = _cvm_obb_ttc(trace, geometry)
    elif collision_model == "center_distance":
        collision = min_distance <= collision_distance_m
        ttc = np.divide(
            distances,
            closing_speed,
            out=np.full_like(distances, np.inf),
            where=closing_speed > 0.0,
        )
    else:
        raise ValueError(f"unsupported collision_model: {collision_model}")
    finite_ttc = ttc[np.isfinite(ttc)]
    min_ttc = float(np.min(finite_ttc)) if finite_ttc.size else float("inf")
    metrics = {
        "c_collision": int(collision),
        "min_distance": min_distance,
        "min_ttc": min_ttc,
        "c_ttc_1_5": int(min_ttc < 1.5),
        "ttc_observed": int(finite_ttc.size > 0),
    }
    if min_clearance is not None:
        # Body-to-body gap, comparable with the AWSIM extractor's min_distance.
        # min_distance stays the centre distance for backward compatibility.
        metrics["min_clearance"] = min_clearance
    return metrics


def _validate_vehicle_geometry(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, Mapping):
        raise ValueError("vehicle_geometry_m is required for OBB collision")
    result: dict[str, dict[str, float]] = {}
    for actor in ("ego", "npc"):
        actor_value = value.get(actor)
        if not isinstance(actor_value, Mapping):
            raise ValueError(f"vehicle_geometry_m.{actor} is required")
        dimensions = {
            key: float(actor_value[key])
            for key in ("length", "width")
        }
        if any(not np.isfinite(item) or item <= 0.0 for item in dimensions.values()):
            raise ValueError("vehicle dimensions must be finite and positive")
        result[actor] = dimensions
    return result


def _obb_overlaps(
    trace: Mapping[str, np.ndarray],
    geometry: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    """Return oriented-rectangle overlap for every synchronized trace row."""
    relative = np.column_stack(
        (trace["npc_x_m"] - trace["ego_x_m"], trace["npc_y_m"] - trace["ego_y_m"])
    )
    ego_heading = trace["ego_heading_rad"]
    npc_heading = trace["npc_heading_rad"]
    ego_forward = np.column_stack((np.cos(ego_heading), np.sin(ego_heading)))
    ego_left = np.column_stack((-np.sin(ego_heading), np.cos(ego_heading)))
    npc_forward = np.column_stack((np.cos(npc_heading), np.sin(npc_heading)))
    npc_left = np.column_stack((-np.sin(npc_heading), np.cos(npc_heading)))
    ego_half = (geometry["ego"]["length"] / 2.0, geometry["ego"]["width"] / 2.0)
    npc_half = (geometry["npc"]["length"] / 2.0, geometry["npc"]["width"] / 2.0)
    overlaps = np.ones(relative.shape[0], dtype=bool)
    for axis in (ego_forward, ego_left, npc_forward, npc_left):
        center_projection = np.abs(np.sum(relative * axis, axis=1))
        ego_radius = (
            ego_half[0] * np.abs(np.sum(ego_forward * axis, axis=1))
            + ego_half[1] * np.abs(np.sum(ego_left * axis, axis=1))
        )
        npc_radius = (
            npc_half[0] * np.abs(np.sum(npc_forward * axis, axis=1))
            + npc_half[1] * np.abs(np.sum(npc_left * axis, axis=1))
        )
        overlaps &= center_projection <= ego_radius + npc_radius
    return overlaps


def _obb_corners(
    x: np.ndarray,
    y: np.ndarray,
    heading: np.ndarray,
    half_length: float,
    half_width: float,
) -> np.ndarray:
    """Return rectangle corners with shape (rows, 4, 2) in drawing order."""
    forward = np.stack((np.cos(heading), np.sin(heading)), axis=1)
    left = np.stack((-np.sin(heading), np.cos(heading)), axis=1)
    center = np.stack((x, y), axis=1)
    signs = ((1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (-1.0, 1.0))
    return np.stack(
        [
            center + sign_l * half_length * forward + sign_w * half_width * left
            for sign_l, sign_w in signs
        ],
        axis=1,
    )


def _points_to_segments_distance(points: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Minimum distance from each row's 4 points to that row's 4 polygon edges."""
    starts = corners[:, None, :, :]
    ends = np.roll(corners, -1, axis=1)[:, None, :, :]
    query = points[:, :, None, :]
    edge = ends - starts
    length_sq = np.maximum(np.sum(edge * edge, axis=-1), 1e-12)
    fraction = np.clip(np.sum((query - starts) * edge, axis=-1) / length_sq, 0.0, 1.0)
    nearest = starts + fraction[..., None] * edge
    return np.min(np.linalg.norm(query - nearest, axis=-1), axis=(1, 2))


def _obb_clearances(
    trace: Mapping[str, np.ndarray],
    geometry: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    """Body-to-body distance per row; 0 where the rectangles overlap."""
    ego = _obb_corners(
        trace["ego_x_m"], trace["ego_y_m"], trace["ego_heading_rad"],
        geometry["ego"]["length"] / 2.0, geometry["ego"]["width"] / 2.0,
    )
    npc = _obb_corners(
        trace["npc_x_m"], trace["npc_y_m"], trace["npc_heading_rad"],
        geometry["npc"]["length"] / 2.0, geometry["npc"]["width"] / 2.0,
    )
    clearance = np.minimum(
        _points_to_segments_distance(ego, npc),
        _points_to_segments_distance(npc, ego),
    )
    clearance[_obb_overlaps(trace, geometry)] = 0.0
    return clearance


def _cvm_obb_ttc(
    trace: Mapping[str, np.ndarray],
    geometry: Mapping[str, Mapping[str, float]],
    *,
    horizon_sec: float = 5.0,
    step_sec: float = 0.1,
) -> np.ndarray:
    """Reproduce the AWSIM CVM extractor's 0.1 s OBB projection."""
    count = len(trace["ego_x_m"])
    result = np.full(count, np.inf, dtype=float)
    active = np.ones(count, dtype=bool)
    ego_vx = trace["ego_speed_mps"] * np.cos(trace["ego_heading_rad"])
    ego_vy = trace["ego_speed_mps"] * np.sin(trace["ego_heading_rad"])
    npc_vx = trace["npc_speed_mps"] * np.cos(trace["npc_heading_rad"])
    npc_vy = trace["npc_speed_mps"] * np.sin(trace["npc_heading_rad"])
    step_count = int(np.ceil(horizon_sec / step_sec)) + 1
    for future_sec in np.round(np.arange(step_count) * step_sec, 2):
        if not np.any(active):
            break
        projected = {
            "ego_x_m": trace["ego_x_m"] + ego_vx * future_sec,
            "ego_y_m": trace["ego_y_m"] + ego_vy * future_sec,
            "ego_heading_rad": trace["ego_heading_rad"],
            "npc_x_m": trace["npc_x_m"] + npc_vx * future_sec,
            "npc_y_m": trace["npc_y_m"] + npc_vy * future_sec,
            "npc_heading_rad": trace["npc_heading_rad"],
        }
        collisions = _obb_overlaps(projected, geometry)
        newly_colliding = active & collisions
        result[newly_colliding] = float(future_sec)
        active[newly_colliding] = False
    return result


def _jama_metrics(inputs: Mapping[str, object]) -> dict[str, object]:
    values = {
        "dx0": float(inputs["dx0"]),
        "ego_speed": float(inputs["ego_speed"]),
        "npc_speed": float(inputs["npc_speed"]),
    }
    calculator = TheoreticalSafetyCalculator(
        SimpleNamespace(JAMA_PROFILES=JAMA_PROFILES)
    )
    return calculator.evaluate(**values)


__all__ = ["DynamicsResultInterpreter", "InterpretationContext"]
