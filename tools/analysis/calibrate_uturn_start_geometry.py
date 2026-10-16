#!/usr/bin/env python3
"""Fit event-aligned U-turn start geometry from saved AWSIM traces."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


TARGETS = (
    "longitudinal_center_m",
    "lateral_center_m",
    "ego_speed_mps",
    "npc_speed_mps",
    "npc_heading_rad",
    "turn_radius_m",
    "turn_angle_rad",
)

# Fraction of the total heading change used for the constant-curvature fit.
# The ends are excluded because AWSIM's waypoint follower blends into and out
# of the arc instead of switching curvature instantaneously.
_ARC_FIT_RANGE = (0.1, 0.9)
# Arc-length window [m] after the trigger where the NPC has finished turning
# but has not yet followed later road curvature.  Trace ends are not used:
# long traces can continue for hundreds of metres along curved lanes.
_SETTLED_ARC_WINDOW_M = (16.0, 25.0)


@dataclass(frozen=True)
class GeometrySample:
    source: str
    features: tuple[float, float, float, float]
    targets: tuple[float, float, float, float, float, float, float]


def extract_geometry_sample(
    payload: Mapping[str, object],
    inputs: Mapping[str, object],
    *,
    source: str,
) -> GeometrySample | None:
    rows = payload.get("groundtruth_kinematic")
    sizes_value = payload.get("groundtruth_size")
    if not isinstance(rows, list) or len(rows) < 100 or not isinstance(sizes_value, Mapping):
        return None
    sizes = {
        str(item.get("name")): item
        for item in sizes_value.get("vehicle_sizes", [])
        if isinstance(item, Mapping)
    }
    if "ego" not in sizes or "npc1" not in sizes:
        return None
    if any(not isinstance(row, Mapping) or not row.get("groundtruth_vehicles") for row in rows):
        return None
    try:
        npc_headings = np.unwrap(
            np.deg2rad(
                [float(row["groundtruth_vehicles"][0]["pose"]["rotation"]["z"]) for row in rows]
            )
        )
        npc_speeds = np.asarray(
            [_speed(row["groundtruth_vehicles"][0]) for row in rows]
        )
        base_heading = float(np.median(npc_headings[:100]))
        indices = np.flatnonzero(
            (np.abs(npc_headings - base_heading) > np.deg2rad(0.5))
            & (npc_speeds > 0.5)
        )
        if not indices.size:
            return None
        row = rows[int(indices[0])]
        ego_center, ego_heading = _geometric_center(row["groundtruth_ego"], sizes["ego"])
        npc = row["groundtruth_vehicles"][0]
        npc_center, npc_heading = _geometric_center(npc, sizes["npc1"])
        relative = npc_center - ego_center
        rotation = np.asarray(
            [
                [math.cos(ego_heading), math.sin(ego_heading)],
                [-math.sin(ego_heading), math.cos(ego_heading)],
            ]
        )
        local = rotation @ relative
        turn = measure_npc_turn(rows[int(indices[0]):])
        if turn is None:
            return None
        turn_radius, turn_angle = turn
        features = (
            1.0,
            float(inputs["dx0"]),
            float(inputs["ego_speed"]),
            float(inputs["npc_speed"]),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return GeometrySample(
        source=source,
        features=features,
        targets=(
            float(local[0]),
            float(local[1]),
            _speed(row["groundtruth_ego"]),
            _speed(npc),
            _wrapped_angle(npc_heading - ego_heading),
            turn_radius,
            turn_angle,
        ),
    )


def measure_npc_turn(rows: Sequence[Mapping[str, object]]) -> tuple[float, float] | None:
    """Measure the executed NPC turn as a constant-curvature arc.

    The arc is measured on the AWSIM pose origin, which is the kinematic
    turning reference.  The geometric center lies ahead of it and does not
    follow a constant-curvature path; the ODE adds that offset explicitly.

    Returns ``(radius_m, angle_rad)``.  The angle is the median heading change
    from the trigger row over a settled arc-length window after the turn; the
    radius is the inverse slope of heading against arc length over the central
    part of the turn.
    The waypoint chord is not used because AWSIM's follower cuts inside it.
    """
    centers: list[np.ndarray] = []
    headings: list[float] = []
    for row in rows:
        vehicles = row.get("groundtruth_vehicles")
        if not vehicles:
            continue
        pose = vehicles[0]["pose"]
        centers.append(np.asarray([float(pose["position"]["x"]), float(pose["position"]["y"])]))
        headings.append(math.radians(float(pose["rotation"]["z"])))
    if len(centers) < 10:
        return None
    path = np.asarray(centers)
    arc = np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(path, axis=0).T))))
    heading_change = np.abs(np.unwrap(np.asarray(headings)) - headings[0])
    settled = (arc >= _SETTLED_ARC_WINDOW_M[0]) & (arc <= _SETTLED_ARC_WINDOW_M[1])
    if int(np.count_nonzero(settled)) < 5:
        return None
    angle = float(np.median(heading_change[settled]))
    if not np.pi / 2.0 < angle < 1.5 * np.pi:
        return None
    lower, upper = (fraction * angle for fraction in _ARC_FIT_RANGE)
    mask = (heading_change >= lower) & (heading_change <= upper) & (arc < _SETTLED_ARC_WINDOW_M[0])
    if int(np.count_nonzero(mask)) < 5:
        return None
    slope = float(np.polyfit(arc[mask], heading_change[mask], 1)[0])
    if slope <= 0.0:
        return None
    return 1.0 / slope, angle


def fit_linear_geometry(
    training: Sequence[GeometrySample],
    validation: Sequence[GeometrySample],
) -> dict[str, object]:
    if len(training) < 10 or not validation:
        raise ValueError("geometry fitting requires at least 10 training and 1 validation sample")
    x_train = np.asarray([sample.features for sample in training], dtype=float)
    y_train = np.asarray([sample.targets for sample in training], dtype=float)
    coefficients = np.linalg.lstsq(x_train, y_train, rcond=None)[0]
    x_validation = np.asarray([sample.features for sample in validation], dtype=float)
    y_validation = np.asarray([sample.targets for sample in validation], dtype=float)
    predictions = x_validation @ coefficients
    return {
        "feature_order": ["intercept", "dx0", "ego_speed", "npc_speed"],
        "coefficients": {
            target: [float(value) for value in coefficients[:, index]]
            for index, target in enumerate(TARGETS)
        },
        "validation_mae": {
            target: float(np.mean(np.abs(predictions[:, index] - y_validation[:, index])))
            for index, target in enumerate(TARGETS)
        },
    }


def _speed(actor: Mapping[str, object]) -> float:
    vector = actor["twist"]["linear"]
    return math.hypot(float(vector["x"]), float(vector["y"]))


def _geometric_center(
    actor: Mapping[str, object],
    size_entry: Mapping[str, object],
) -> tuple[np.ndarray, float]:
    position = actor["pose"]["position"]
    heading = math.radians(float(actor["pose"]["rotation"]["z"]))
    offset = size_entry["center"]
    offset_x, offset_y = float(offset["x"]), float(offset["y"])
    center = np.asarray(
        [
            float(position["x"]) + offset_x * math.cos(heading) - offset_y * math.sin(heading),
            float(position["y"]) + offset_x * math.sin(heading) + offset_y * math.cos(heading),
        ]
    )
    return center, heading


def _wrapped_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _load_records(path: Path) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            result[int(record["meta"]["global_loop_num"])] = record
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return result


def _loop_number(source: str) -> int:
    match = re.search(r"sim(\d+)", source)
    if match is None:
        raise ValueError(f"cannot parse simulation number from {source}")
    return int(match.group(1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--records-jsonl", type=Path, required=True)
    parser.add_argument("--split-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--final-count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument(
        "--exclude-report",
        type=Path,
        action="append",
        default=[],
        help=(
            "Earlier calibration report whose final_validation_sources were already "
            "inspected; they become development data and are kept out of the new final set."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trace_dir = args.trace_dir.expanduser().resolve()
    records = _load_records(args.records_jsonl.expanduser().resolve())
    split = json.loads(args.split_report.read_text(encoding="utf-8"))

    def samples_for(key: str) -> list[GeometrySample]:
        result: list[GeometrySample] = []
        for source_value in split[key]:
            source = str(source_value)
            record = records.get(_loop_number(source))
            if record is None:
                continue
            payload = json.loads((trace_dir / source).read_text(encoding="utf-8"))
            sample = extract_geometry_sample(payload, record["input"], source=source)
            if sample is not None:
                result.append(sample)
        return result

    training = samples_for("calibration_sources")
    validation = samples_for("validation_sources")
    result = fit_linear_geometry(training, validation)

    previously_inspected: list[str] = []
    for report_path in args.exclude_report:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        previously_inspected.extend(str(item) for item in report.get("final_validation_sources", []))
    excluded = {sample.source for sample in training + validation} | set(previously_inspected)
    candidates: dict[int, list[str]] = {0: [], 1: []}
    for loop, record in records.items():
        source = f"uturn_eval_sim{loop}.json"
        if source in excluded or not (trace_dir / source).exists():
            continue
        try:
            label = int(record["output"]["c_collision"])
        except (KeyError, TypeError, ValueError):
            continue
        if label in candidates:
            candidates[label].append(source)
    rng = random.Random(args.seed)
    for values in candidates.values():
        rng.shuffle(values)
    collision_count = min(args.final_count // 2, len(candidates[1]))
    noncollision_count = min(args.final_count - collision_count, len(candidates[0]))
    final_sources = candidates[1][:collision_count] + candidates[0][:noncollision_count]
    rng.shuffle(final_sources)
    result.update(
        {
            "schema_version": 1,
            "geometry_kind": "autoware171_uturn_start_linear_v2",
            "training_trace_count": len(training),
            "validation_trace_count": len(validation),
            "training_sources": [sample.source for sample in training],
            "development_validation_sources": [sample.source for sample in validation],
            "development_evaluation_sources": sorted(set(previously_inspected)),
            "final_validation_sources": final_sources,
            "final_selection_seed": args.seed,
            "trigger_definition": "first NPC heading deviation >0.5 deg while NPC speed >0.5 m/s",
            "coordinate_frame": "ego geometric center at trigger; +x ego-forward; +y ego-left",
            "turn_definition": (
                "executed NPC pose-origin path: angle = median heading change 16-25 m after trigger; "
                "radius = 1 / d(heading)/d(arc) over 10-90% of the angle"
            ),
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("validation_mae", "training_trace_count", "validation_trace_count", "final_selection_seed")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
