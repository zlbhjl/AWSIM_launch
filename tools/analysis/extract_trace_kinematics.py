"""Re-extract kinematics metrics (min_ttc etc.) from archived AWSIM trace JSON.

v2 runs before 2026-10-05 did not call the kinematics extractor, so their
dataset CSVs have empty ``min_ttc`` / ``min_distance`` / ``min_ttb`` /
``z_margin``. This tool recomputes them from an experiment package's
``json_traces/trace_index.csv`` with the same bridge the interpreter now uses.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from targets.awsim.kinematics_bridge import extract_kinematics_metrics


METRICS = ("min_ttc", "min_distance", "min_ttb", "z_margin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package-root", required=True, help="Experiment package directory")
    parser.add_argument("--experiment", action="append", required=True, help="trace_index experiment name")
    parser.add_argument("--mode", default="cvm")
    parser.add_argument("--target-npc", action="append", default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", required=True)
    return parser


def _extract(task: tuple[str, str, tuple[str, ...]]) -> dict[str, object]:
    path, mode, npcs = task
    try:
        metrics = extract_kinematics_metrics(path, mode=mode, target_npcs=list(npcs))
        return {**{key: metrics.get(key) for key in METRICS}, "kinematics_c_collision": metrics.get("c_collision"), "error": ""}
    except Exception as exc:  # noqa: BLE001 - keep going and report per trace
        return {**{key: None for key in METRICS}, "kinematics_c_collision": None, "error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.package_root).expanduser()
    index = pd.read_csv(root / "json_traces" / "trace_index.csv")
    index = index[index["experiment"].isin(args.experiment)]
    usable = index[index["copied_exists"].astype(bool) & ~index["timeout_marker"].astype(bool)]
    npcs = tuple(args.target_npc or ["npc1"])
    tasks = [(str(root / rel), args.mode, npcs) for rel in usable["package_relative_path"]]

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_extract, tasks, chunksize=16))

    extracted = pd.concat([usable.reset_index(drop=True), pd.DataFrame(results)], axis=1)
    columns = ["experiment", "node", "worker_id", "loop_num", "source_loop_num", "status", *METRICS, "kinematics_c_collision", "error"]
    extracted[columns].to_csv(args.output, index=False)
    print(
        f"traces={len(index)} extracted={len(usable)} errors={(extracted['error'] != '').sum()} "
        f"skipped(no trace or TIMEOUT marker)={len(index) - len(usable)} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
