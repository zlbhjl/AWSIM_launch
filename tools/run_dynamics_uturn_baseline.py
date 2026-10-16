#!/usr/bin/env python3
"""Run the initial ODE/SDE U-turn numerical and sensitivity experiment set."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from targets.dynamics.models.uturn import simulate_uturn
from targets.dynamics.models.uturn_sde import simulate_uturn_sde
from targets.dynamics.profile import build_execution_profile
from targets.dynamics.result_interpreter import _jama_metrics, _safety_metrics


REPRESENTATIVE_CASES = {
    "risk": {"dx0": 10.0, "ego_speed": 40.0, "npc_speed": 10.0},
    "boundary": {"dx0": 12.25, "ego_speed": 36.0, "npc_speed": 18.0},
    "safe": {"dx0": 25.0, "ego_speed": 30.0, "npc_speed": 25.0},
}
TIME_STEPS_SEC = (0.04, 0.02, 0.01, 0.005)
NOISE_LEVELS = {
    "npc_acceleration_std": (0.0, 0.1, 0.25, 0.5),
    "ego_brake_acceleration_std": (0.0, 0.1, 0.25, 0.5),
    "npc_heading_std_rad_per_sqrt_sec": (0.0, 0.005, 0.01, 0.02),
}


def _simulate(inputs: Mapping[str, object]) -> dict[str, object]:
    profile = build_execution_profile(inputs, default_output_root="artifacts/unused")
    result = (
        simulate_uturn(profile.ode_config)
        if profile.solver_kind == "ode"
        else simulate_uturn_sde(profile.sde_config)
    )
    states = result.states
    trace = {
        "ego_x_m": states[:, 0], "ego_y_m": states[:, 1],
        "ego_heading_rad": states[:, 2], "ego_speed_mps": states[:, 3],
        "npc_x_m": states[:, 4], "npc_y_m": states[:, 5],
        "npc_heading_rad": states[:, 6], "npc_speed_mps": states[:, 7],
    }
    metrics = _safety_metrics(
        trace,
        collision_distance_m=profile.collision_distance_m,
        collision_model=profile.collision_model,
        vehicle_geometry_m=profile.vehicle_geometry_m,
    )
    metrics.update(_jama_metrics(inputs))
    return {
        **metrics,
        "uturn_start_time_sec": result.uturn_start_time_sec,
        "uturn_end_time_sec": result.uturn_end_time_sec,
        "ego_final_speed_mps": float(states[-1, 3]),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _finite(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="artifacts/dynamics_uturn_baseline_20261005")
    parser.add_argument("--seed-count", type=int, default=100)
    parser.add_argument("--seed-start", type=int, default=1000)
    args = parser.parse_args()
    if args.seed_count <= 0:
        raise ValueError("--seed-count must be positive")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ode_rows: list[dict[str, object]] = []
    zero_rows: list[dict[str, object]] = []
    boundary_scan_rows: list[dict[str, object]] = []
    sde_boundary_scan_rows: list[dict[str, object]] = []
    for case_name, values in REPRESENTATIVE_CASES.items():
        for dt in TIME_STEPS_SEC:
            ode = _simulate({**values, "max_step_sec": dt})
            ode_rows.append({"case": case_name, "max_step_sec": dt, **values, **ode})
            sde = _simulate({
                **values, "solver_kind": "sde", "sde_seed": args.seed_start,
                "sde_dt_sec": dt, "max_step_sec": dt,
                "sde_noise": {name: 0.0 for name in NOISE_LEVELS},
            })
            zero_rows.append({
                "case": case_name, "dt_sec": dt, **values,
                "ode_c_collision": ode["c_collision"], "sde_c_collision": sde["c_collision"],
                "min_ttc_abs_error_sec": abs(float(ode["min_ttc"]) - float(sde["min_ttc"])),
                "uturn_start_abs_error_sec": _difference(ode["uturn_start_time_sec"], sde["uturn_start_time_sec"]),
                "ode_zone_a": ode["theory_zone_a"], "sde_zone_a": sde["theory_zone_a"],
                "ode_zone_b": ode["theory_zone_b"], "sde_zone_b": sde["theory_zone_b"],
            })

    for dx0 in np.arange(10.0, 25.0 + 1e-9, 0.5):
        metrics = _simulate({"dx0": float(dx0), "ego_speed": 36.0, "npc_speed": 18.0, "max_step_sec": 0.01})
        boundary_scan_rows.append({"dx0": float(dx0), "ego_speed": 36.0, "npc_speed": 18.0, **metrics})
        sde_metrics = _simulate({
            "dx0": float(dx0), "ego_speed": 36.0, "npc_speed": 18.0,
            "solver_kind": "sde", "sde_seed": args.seed_start,
            "sde_dt_sec": 0.01, "max_step_sec": 0.01, "sde_noise": {},
        })
        sde_boundary_scan_rows.append({"dx0": float(dx0), "ego_speed": 36.0, "npc_speed": 18.0, **sde_metrics})

    sensitivity_rows: list[dict[str, object]] = []
    boundary = REPRESENTATIVE_CASES["boundary"]
    for noise_name, levels in NOISE_LEVELS.items():
        for level in levels:
            for seed in range(args.seed_start, args.seed_start + args.seed_count):
                noise = {name: 0.0 for name in NOISE_LEVELS}
                noise[noise_name] = level
                metrics = _simulate({
                    **boundary, "solver_kind": "sde", "sde_seed": seed,
                    "sde_dt_sec": 0.01, "max_step_sec": 0.01, "sde_noise": noise,
                })
                sensitivity_rows.append({
                    "noise_name": noise_name, "noise_level": level, "seed": seed,
                    **boundary, **noise, **metrics,
                })

    _write_csv(output_dir / "ode_convergence.csv", ode_rows)
    _write_csv(output_dir / "zero_noise_sde_comparison.csv", zero_rows)
    _write_csv(output_dir / "ode_dx0_boundary_scan.csv", boundary_scan_rows)
    _write_csv(output_dir / "zero_noise_sde_dx0_boundary_scan.csv", sde_boundary_scan_rows)
    _write_csv(output_dir / "sde_sensitivity_samples.csv", sensitivity_rows)
    summary = _summary(ode_rows, zero_rows, boundary_scan_rows, sde_boundary_scan_rows, sensitivity_rows, seed_count=args.seed_count)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **summary}, ensure_ascii=False))
    return 0


def _difference(left: object, right: object) -> float | None:
    first, second = _finite(left), _finite(right)
    return None if first is None or second is None else abs(first - second)


def _summary(
    ode_rows: list[dict[str, object]], zero_rows: list[dict[str, object]],
    boundary_scan_rows: list[dict[str, object]], sde_boundary_scan_rows: list[dict[str, object]],
    sensitivity_rows: list[dict[str, object]], *, seed_count: int,
) -> dict[str, object]:
    groups: list[dict[str, object]] = []
    for noise_name in NOISE_LEVELS:
        for level in NOISE_LEVELS[noise_name]:
            rows = [row for row in sensitivity_rows if row["noise_name"] == noise_name and row["noise_level"] == level]
            ttc = np.asarray([float(row["min_ttc"]) for row in rows], dtype=float)
            groups.append({
                "noise_name": noise_name, "noise_level": level, "sample_count": len(rows),
                "collision_rate": float(np.mean([int(row["c_collision"]) for row in rows])),
                "min_ttc_mean_sec": float(np.mean(ttc)), "min_ttc_median_sec": float(np.median(ttc)),
                "min_ttc_q05_sec": float(np.quantile(ttc, 0.05)),
                "zone_a_b_rate": float(np.mean([row["theory_zone_a"] == "B" for row in rows])),
            })
    return {
        "experiment": "dynamics_uturn_ode_sde_baseline", "seed_count": seed_count,
        "representative_cases": REPRESENTATIVE_CASES, "time_steps_sec": list(TIME_STEPS_SEC),
        "noise_levels": {key: list(value) for key, value in NOISE_LEVELS.items()},
        "ode_convergence_rows": len(ode_rows), "zero_noise_comparison_rows": len(zero_rows),
        "ode_boundary_collision_interval_m": _collision_interval(boundary_scan_rows),
        "zero_noise_sde_boundary_collision_interval_m": _collision_interval(sde_boundary_scan_rows),
        "zero_noise_max_min_ttc_abs_error_sec": max(float(row["min_ttc_abs_error_sec"]) for row in zero_rows),
        "sensitivity_groups": groups,
    }


def _collision_interval(rows: list[dict[str, object]]) -> list[float] | None:
    collisions = [float(row["dx0"]) for row in rows if int(row["c_collision"]) == 1]
    safe = [float(row["dx0"]) for row in rows if int(row["c_collision"]) == 0]
    if not collisions or not safe:
        return None
    return [max(collisions), min(safe)]


if __name__ == "__main__":
    raise SystemExit(main())
