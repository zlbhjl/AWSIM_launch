#!/usr/bin/env python3
"""Estimate a simple U-turn braking profile from AWSIM ground-truth traces."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares


# Speed-curve fit window after the trigger [s].  Long enough to cover the
# delay, ramp, and sustained deceleration, short enough to end before Autoware
# typically releases the brake once the NPC has cleared the ego lane.
FIT_WINDOW_SEC = 2.5


@dataclass(frozen=True)
class BrakingSample:
    source: str
    response_delay_sec: float
    jerk_ramp_sec: float
    max_deceleration_mps2: float
    ego_speed_at_trigger_mps: float
    npc_speed_at_trigger_mps: float


def extract_braking_sample(
    payload: Mapping[str, object],
    *,
    source: str,
) -> BrakingSample | None:
    rows = payload.get("groundtruth_kinematic")
    if not isinstance(rows, list) or len(rows) < 100:
        return None
    if any(not isinstance(row, Mapping) or not row.get("groundtruth_vehicles") for row in rows):
        return None

    try:
        times = np.asarray([float(row["timestamp"]) for row in rows])
        ego_speeds = np.asarray(
            [_planar_speed(row["groundtruth_ego"]["twist"]["linear"]) for row in rows]
        )
        npc_speeds = np.asarray(
            [
                _planar_speed(row["groundtruth_vehicles"][0]["twist"]["linear"])
                for row in rows
            ]
        )
        npc_headings = np.unwrap(
            np.deg2rad(
                [
                    float(row["groundtruth_vehicles"][0]["pose"]["rotation"]["z"])
                    for row in rows
                ]
            )
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if np.any(np.diff(times) <= 0.0):
        return None

    reference_heading = float(np.median(npc_headings[: min(100, len(rows))]))
    turn_indices = np.flatnonzero(
        (np.abs(npc_headings - reference_heading) > np.deg2rad(2.0))
        & (npc_speeds > 0.5)
    )
    if not turn_indices.size:
        return None
    trigger_index = int(turn_indices[0])
    trigger_time = float(times[trigger_index])

    median_dt = float(np.median(np.diff(times)))
    window = max(3, int(round(0.2 / median_dt)))
    if window % 2 == 0:
        window += 1
    smoothed_speed = np.convolve(
        ego_speeds, np.ones(window, dtype=float) / window, mode="same"
    )
    acceleration = np.gradient(smoothed_speed, times)
    onset = _find_sustained_braking_onset(
        times,
        acceleration,
        trigger_time=trigger_time,
    )
    if onset is None:
        return None

    response_delay = float(times[onset] - trigger_time)
    end = int(np.searchsorted(times, times[onset] + 2.5))
    deceleration = -acceleration[onset:end]
    if deceleration.size < 3:
        return None
    max_deceleration = float(np.quantile(deceleration, 0.9))
    if not math.isfinite(max_deceleration) or max_deceleration <= 0.0:
        return None
    ramp_indices = np.flatnonzero(deceleration >= 0.8 * max_deceleration)
    if not ramp_indices.size:
        return None
    jerk_ramp = max(float(times[onset + int(ramp_indices[0])] - times[onset]), median_dt)
    fitted = fit_braking_curve(
        times[trigger_index:] - trigger_time,
        ego_speeds[trigger_index:],
        initial=(response_delay, jerk_ramp, max_deceleration),
    )
    if fitted is None:
        return None
    response_delay, jerk_ramp, max_deceleration = fitted
    return BrakingSample(
        source=source,
        response_delay_sec=response_delay,
        jerk_ramp_sec=jerk_ramp,
        max_deceleration_mps2=max_deceleration,
        ego_speed_at_trigger_mps=float(ego_speeds[trigger_index]),
        npc_speed_at_trigger_mps=float(npc_speeds[trigger_index]),
    )


def braking_speed_curve(
    elapsed_sec: np.ndarray,
    *,
    initial_speed_mps: float,
    delay_sec: float,
    ramp_sec: float,
    deceleration_mps2: float,
) -> np.ndarray:
    """Closed-form speed of the delay / linear-ramp / constant brake profile."""
    active = np.clip(elapsed_sec - delay_sec, 0.0, None)
    ramp = max(ramp_sec, 1e-6)
    in_ramp = np.minimum(active, ramp)
    lost = deceleration_mps2 * (
        in_ramp**2 / (2.0 * ramp) + np.clip(active - ramp, 0.0, None)
    )
    return np.maximum(initial_speed_mps - lost, 0.0)


def fit_braking_curve(
    elapsed_sec: np.ndarray,
    speeds_mps: np.ndarray,
    *,
    initial: tuple[float, float, float],
) -> tuple[float, float, float] | None:
    """Least-squares fit of (delay, ramp, deceleration) to the speed trace.

    Fitting the integrated speed instead of a percentile of the differentiated
    speed avoids biasing the deceleration toward short noisy peaks.
    """
    mask = (elapsed_sec >= 0.0) & (elapsed_sec <= FIT_WINDOW_SEC)
    if int(np.count_nonzero(mask)) < 10:
        return None
    elapsed = elapsed_sec[mask]
    observed = speeds_mps[mask]
    initial_speed = float(observed[0])
    lower = np.array([0.0, 0.01, 0.5])
    upper = np.array([1.5, 1.5, 10.0])
    start = np.clip(np.asarray(initial, dtype=float), lower + 1e-6, upper - 1e-6)
    result = least_squares(
        lambda params: braking_speed_curve(
            elapsed,
            initial_speed_mps=initial_speed,
            delay_sec=params[0],
            ramp_sec=params[1],
            deceleration_mps2=params[2],
        )
        - observed,
        start,
        bounds=(lower, upper),
    )
    if not result.success:
        return None
    return tuple(float(value) for value in result.x)


def calibrate(samples: Sequence[BrakingSample]) -> dict[str, object]:
    if len(samples) < 10:
        raise ValueError("at least 10 usable AWSIM traces are required")
    fields = {
        "t_delay": np.asarray([sample.response_delay_sec for sample in samples]),
        "t_jerk": np.asarray([sample.jerk_ramp_sec for sample in samples]),
        "a_max": np.asarray([sample.max_deceleration_mps2 for sample in samples]),
    }
    profile = {name: round(float(np.median(values)), 6) for name, values in fields.items()}
    intervals = {
        name: {
            "q10": round(float(np.quantile(values, 0.1)), 6),
            "q90": round(float(np.quantile(values, 0.9)), 6),
        }
        for name, values in fields.items()
    }
    return {"profile": profile, "empirical_intervals": intervals}


def _find_sustained_braking_onset(
    times: np.ndarray,
    acceleration: np.ndarray,
    *,
    trigger_time: float,
) -> int | None:
    candidates = np.flatnonzero(
        (times >= trigger_time)
        & (times <= trigger_time + 3.0)
        & (acceleration < -0.5)
    )
    for index in candidates:
        end = int(np.searchsorted(times, times[index] + 0.15))
        if end > index and np.mean(acceleration[index:end] < -0.4) >= 0.7:
            return int(index)
    return None


def _planar_speed(vector: Mapping[str, object]) -> float:
    return math.hypot(float(vector["x"]), float(vector["y"]))


def _held_out_errors(
    validation: Sequence[BrakingSample], profile: Mapping[str, float]
) -> dict[str, object]:
    mappings = {
        "t_delay": "response_delay_sec",
        "t_jerk": "jerk_ramp_sec",
        "a_max": "max_deceleration_mps2",
    }
    return {
        name: {
            "median_absolute_error": round(
                float(
                    np.median(
                        [abs(getattr(sample, attribute) - float(profile[name])) for sample in validation]
                    )
                ),
                6,
            )
        }
        for name, attribute in mappings.items()
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--calibration-fraction", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=20261005)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_files < 10:
        raise ValueError("--max-files must be at least 10")
    if not 0.5 <= args.calibration_fraction < 1.0:
        raise ValueError("--calibration-fraction must be in [0.5, 1.0)")
    files = sorted(args.trace_dir.glob("uturn_eval_sim*.json"))
    random.Random(args.seed).shuffle(files)
    samples: list[BrakingSample] = []
    inspected = 0
    for path in files:
        if inspected >= args.max_files:
            break
        inspected += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sample = extract_braking_sample(payload, source=path.name)
        if sample is not None:
            samples.append(sample)
    split = max(10, int(len(samples) * args.calibration_fraction))
    if split >= len(samples):
        raise ValueError("not enough usable traces for calibration and validation")
    training, validation = samples[:split], samples[split:]
    result = calibrate(training)
    result.update(
        {
            "schema_version": 2,
            "controller_kind": "autoware171_uturn_calibrated",
            "source_trace_dir": str(args.trace_dir.resolve()),
            "selection_seed": args.seed,
            "inspected_file_count": inspected,
            "usable_trace_count": len(samples),
            "calibration_trace_count": len(training),
            "validation_trace_count": len(validation),
            "held_out_errors": _held_out_errors(validation, result["profile"]),
            "calibration_sources": [sample.source for sample in training],
            "validation_sources": [sample.source for sample in validation],
            "method": {
                "trigger": "first NPC heading change >2 deg while speed >0.5 m/s",
                "speed_smoothing_window_sec": 0.2,
                "usable_trace": "sustained braking onset >0.5 m/s^2 for 0.15 s within 3 s",
                "profile_fit": "least-squares fit of delay/ramp/deceleration to raw ego speed 0-2.5 s after trigger",
                "aggregate": "component-wise median",
            },
            "samples": [asdict(sample) for sample in samples],
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("profile", "usable_trace_count", "calibration_trace_count", "validation_trace_count", "held_out_errors")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

