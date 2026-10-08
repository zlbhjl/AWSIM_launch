#!/usr/bin/env python3
"""Validate the calibrated U-turn dynamics surrogate against saved AWSIM runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from targets.awsim.kinematics_bridge import extract_kinematics_metrics
from targets.dynamics.models.uturn import simulate_uturn
from targets.dynamics.profile import build_execution_profile
from targets.dynamics.result_interpreter import _cvm_obb_ttc, _safety_metrics


TTC_THRESHOLDS = (1.5, 1.3, 1.2, 1.1, 0.9, 0.7, 0.5, 0.3)


def confusion_summary(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_key: str,
    predicted_key: str,
) -> dict[str, object]:
    tp = sum(int(row[expected_key]) == 1 and int(row[predicted_key]) == 1 for row in rows)
    tn = sum(int(row[expected_key]) == 0 and int(row[predicted_key]) == 0 for row in rows)
    fp = sum(int(row[expected_key]) == 0 and int(row[predicted_key]) == 1 for row in rows)
    fn = sum(int(row[expected_key]) == 1 and int(row[predicted_key]) == 0 for row in rows)
    total = len(rows)
    return {
        "sample_count": total,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": (tp + tn) / total if total else None,
        "sensitivity": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
    }


def continuous_ttc_summary(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_key: str = "awsim_min_ttc",
    predicted_key: str = "dynamics_min_ttc",
) -> dict[str, object]:
    pairs = [
        (float(row[expected_key]), float(row[predicted_key]))
        for row in rows
        if math.isfinite(float(row[expected_key]))
        and math.isfinite(float(row[predicted_key]))
    ]
    if not pairs:
        return {"finite_pair_count": 0, "mae_sec": None, "median_absolute_error_sec": None, "bias_sec": None}
    errors = np.asarray([predicted - expected for expected, predicted in pairs])
    return {
        "finite_pair_count": len(pairs),
        "mae_sec": float(np.mean(np.abs(errors))),
        "median_absolute_error_sec": float(np.median(np.abs(errors))),
        "bias_sec": float(np.mean(errors)),
    }


def _load_records(path: Path) -> dict[int, dict[str, object]]:
    records: dict[int, dict[str, object]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
                loop = int(record["meta"]["global_loop_num"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            records[loop] = record
    return records


def _loop_number(source: str) -> int:
    match = re.search(r"sim(\d+)", source)
    if match is None:
        raise ValueError(f"cannot resolve simulation number from {source!r}")
    return int(match.group(1))


def _run_dynamics(
    values: Mapping[str, object],
    *,
    controller_kind: str,
) -> tuple[dict[str, object], dict[str, object]]:
    profile = build_execution_profile(
        {
            "dx0": values["dx0"],
            "ego_speed": values["ego_speed"],
            "npc_speed": values["npc_speed"],
            "alignment_mode": "uturn_trigger_aligned",
            "controller_kind": controller_kind,
            "collision_model": "obb",
            "max_step_sec": 0.01,
        },
        default_output_root="artifacts/unused",
    )
    result = simulate_uturn(profile.ode_config)
    states = result.states
    trace = {
        "ego_x_m": states[:, 0],
        "ego_y_m": states[:, 1],
        "ego_heading_rad": states[:, 2],
        "ego_speed_mps": states[:, 3],
        "npc_x_m": states[:, 4],
        "npc_y_m": states[:, 5],
        "npc_heading_rad": states[:, 6],
        "npc_speed_mps": states[:, 7],
    }
    metrics = _safety_metrics(
        trace,
        collision_distance_m=profile.collision_distance_m,
        collision_model=profile.collision_model,
        vehicle_geometry_m=profile.vehicle_geometry_m,
    )
    metrics["c_screening_candidate"] = int(
        float(metrics["min_clearance"]) < profile.screening_clearance_margin_m
    )
    return metrics, profile.to_meta()


def awsim_world_frame_min_ttc(
    trace_path: Path,
    *,
    horizon_sec: float = 10.0,
) -> float:
    """CVM OBB TTC of an AWSIM trace using the same code as the dynamics side.

    AWSIM ``twist.linear`` is already in the world frame (its direction equals
    the pose yaw), so velocities are taken as ``speed * (cos yaw, sin yaw)``.
    The legacy AW_Kinematics_Extractor CVM mode rotates the twist by yaw a
    second time; its ``min_ttc`` is kept only as ``awsim_min_ttc_extractor``.
    The window starts at the same NPC-heading trigger as the dynamics model.
    """
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    sizes = {
        str(item["name"]): item
        for item in payload["groundtruth_size"]["vehicle_sizes"]
    }
    rows = [row for row in payload["groundtruth_kinematic"] if row.get("groundtruth_vehicles")]
    columns: dict[str, list[float]] = {key: [] for key in (
        "time", "ego_x_m", "ego_y_m", "ego_heading_rad", "ego_speed_mps",
        "npc_x_m", "npc_y_m", "npc_heading_rad", "npc_speed_mps",
    )}
    for row in rows:
        columns["time"].append(float(row["timestamp"]))
        for prefix, actor, size_name in (
            ("ego", row["groundtruth_ego"], "ego"),
            ("npc", row["groundtruth_vehicles"][0], "npc1"),
        ):
            heading = math.radians(float(actor["pose"]["rotation"]["z"]))
            offset = float(sizes[size_name]["center"]["x"])
            position = actor["pose"]["position"]
            linear = actor["twist"]["linear"]
            columns[f"{prefix}_x_m"].append(float(position["x"]) + offset * math.cos(heading))
            columns[f"{prefix}_y_m"].append(float(position["y"]) + offset * math.sin(heading))
            columns[f"{prefix}_heading_rad"].append(heading)
            columns[f"{prefix}_speed_mps"].append(math.hypot(float(linear["x"]), float(linear["y"])))
    trace = {key: np.asarray(values, dtype=float) for key, values in columns.items()}
    npc_heading = np.unwrap(trace["npc_heading_rad"])
    turning = np.flatnonzero(
        (np.abs(npc_heading - np.median(npc_heading[:100])) > np.deg2rad(0.5))
        & (trace["npc_speed_mps"] > 0.5)
    )
    if not turning.size:
        return math.inf
    times = trace["time"]
    window = (times >= times[turning[0]]) & (times <= times[turning[0]] + horizon_sec)
    windowed = {key: values[window] for key, values in trace.items()}
    geometry = {
        actor: {
            "length": float(sizes[name]["size"]["x"]),
            "width": float(sizes[name]["size"]["y"]),
        }
        for actor, name in (("ego", "ego"), ("npc", "npc1"))
    }
    ttc = _cvm_obb_ttc(windowed, geometry)
    finite = ttc[np.isfinite(ttc)]
    return float(np.min(finite)) if finite.size else math.inf


def validate(
    *,
    trace_dir: Path,
    records_jsonl: Path,
    calibration_report: Path,
    sources_key: str = "validation_sources",
) -> tuple[list[dict[str, object]], dict[str, object]]:
    calibration = json.loads(calibration_report.read_text(encoding="utf-8"))
    sources = calibration.get(sources_key)
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"calibration report has no {sources_key}")
    records = _load_records(records_jsonl)
    rows: list[dict[str, object]] = []
    missing: list[str] = []
    profile_meta: dict[str, object] | None = None
    for source_value in sources:
        source = str(source_value)
        loop = _loop_number(source)
        record = records.get(loop)
        trace_path = trace_dir / source
        if record is None or not trace_path.exists():
            missing.append(source)
            continue
        inputs = record["input"]
        awsim_output = record["output"]
        dynamics, profile_meta = _run_dynamics(
            inputs,
            controller_kind="autoware171_uturn_calibrated",
        )
        jama, jama_profile_meta = _run_dynamics(
            inputs,
            controller_kind="jama_ai_aeb",
        )
        awsim_kinematics = extract_kinematics_metrics(trace_path)
        row: dict[str, object] = {
            "simulation": loop,
            "trace": source,
            "dx0": float(inputs["dx0"]),
            "ego_speed": float(inputs["ego_speed"]),
            "npc_speed": float(inputs["npc_speed"]),
            "awsim_c_collision": int(awsim_output["c_collision"]),
            "dynamics_c_collision": int(dynamics["c_collision"]),
            "dynamics_c_screening_candidate": int(dynamics["c_screening_candidate"]),
            "collision_agree": int(int(awsim_output["c_collision"]) == int(dynamics["c_collision"])),
            "awsim_min_ttc": awsim_world_frame_min_ttc(trace_path),
            "awsim_min_ttc_extractor": float(awsim_kinematics.get("min_ttc", math.inf)),
            "dynamics_min_ttc": float(dynamics["min_ttc"]),
            "awsim_min_distance": float(awsim_kinematics.get("min_distance", math.inf)),
            "dynamics_min_distance": float(dynamics["min_distance"]),
            "dynamics_min_clearance": float(dynamics.get("min_clearance", math.nan)),
            "jama_c_collision": int(jama["c_collision"]),
            "jama_min_ttc": float(jama["min_ttc"]),
            "jama_min_distance": float(jama["min_distance"]),
            "ego_init_lane": str(inputs.get("ego_init_lane", "")),
            "ego_init_offset": float(inputs.get("ego_init_offset", math.nan)),
        }
        for threshold in TTC_THRESHOLDS:
            label = f"c_ttc_{threshold}"
            awsim_key = f"c_ttc_{threshold:.1f}"
            row[f"awsim_{label}"] = int(awsim_output[awsim_key])
            row[f"dynamics_{label}"] = int(float(dynamics["min_ttc"]) < threshold)
            row[f"jama_{label}"] = int(float(jama["min_ttc"]) < threshold)
        rows.append(row)
    if not rows:
        raise ValueError("no hold-out AWSIM records could be validated")

    threshold_summaries = {
        str(threshold): confusion_summary(
            rows,
            expected_key=f"awsim_c_ttc_{threshold}",
            predicted_key=f"dynamics_c_ttc_{threshold}",
        )
        for threshold in TTC_THRESHOLDS
    }
    jama_threshold_summaries = {
        str(threshold): confusion_summary(
            rows,
            expected_key=f"awsim_c_ttc_{threshold}",
            predicted_key=f"jama_c_ttc_{threshold}",
        )
        for threshold in TTC_THRESHOLDS
    }
    scenario_layouts = sorted(
        {
            f"lane={row['ego_init_lane']},offset={row['ego_init_offset']}"
            for row in rows
        }
    )
    calibrated_collision = confusion_summary(
        rows,
        expected_key="awsim_c_collision",
        predicted_key="dynamics_c_collision",
    )
    calibrated_ttc = continuous_ttc_summary(rows)
    screening_collision = confusion_summary(
        rows,
        expected_key="awsim_c_collision",
        predicted_key="dynamics_c_screening_candidate",
    )
    jama_collision = confusion_summary(
        rows,
        expected_key="awsim_c_collision",
        predicted_key="jama_c_collision",
    )
    jama_ttc = continuous_ttc_summary(rows, predicted_key="jama_min_ttc")
    limitations = [
        "AWSIM TTC thresholds are Maude labels; continuous min_ttc is recomputed with the dynamics CVM OBB code on world-frame AWSIM velocities.",
        "awsim_min_ttc_extractor is the legacy AW_Kinematics_Extractor CVM value, which rotates the already world-frame twist by yaw again.",
        "Saved runs use the lane/offset configuration recorded per case, which may differ from the current scenario profile.",
    ]
    if sources_key == "validation_sources":
        limitations.insert(
            0,
            "The hold-out split was defined for braking calibration and is reused here.",
        )
    summary = {
        "schema_version": 1,
        "evaluation_role": (
            "final_stratified_external_validation"
            if sources_key == "final_validation_sources"
            else "development_external_validation"
        ),
        "source_selection_key": sources_key,
        "sample_count": len(rows),
        "missing_sources": missing,
        "collision": calibrated_collision,
        "screening": screening_collision,
        "continuous_ttc": calibrated_ttc,
        "ttc_thresholds": threshold_summaries,
        "jama_baseline": {
            "collision": jama_collision,
            "continuous_ttc": jama_ttc,
            "ttc_thresholds": jama_threshold_summaries,
            "dynamics_profile": jama_profile_meta,
        },
        "calibrated_improvement_over_jama": {
            "collision_accuracy_delta": (
                float(calibrated_collision["accuracy"])
                - float(jama_collision["accuracy"])
            ),
            "continuous_ttc_mae_reduction_sec": (
                float(jama_ttc["mae_sec"]) - float(calibrated_ttc["mae_sec"])
                if jama_ttc["mae_sec"] is not None and calibrated_ttc["mae_sec"] is not None
                else None
            ),
        },
        "scenario_layouts_in_saved_data": scenario_layouts,
        "dynamics_profile": profile_meta,
        "limitations": limitations,
    }
    return rows, summary


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--records-jsonl", type=Path, required=True)
    parser.add_argument("--calibration-report", type=Path, required=True)
    parser.add_argument("--sources-key", default="validation_sources")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows, summary = validate(
        trace_dir=args.trace_dir.expanduser().resolve(),
        records_jsonl=args.records_jsonl.expanduser().resolve(),
        calibration_report=args.calibration_report.expanduser().resolve(),
        sources_key=args.sources_key,
    )
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "per_case.csv", rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
