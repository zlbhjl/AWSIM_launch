#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import importlib
import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import point_extractors


def resolve_dataset_path(target_path: str, scenario_type: str) -> str:
    target_path = os.path.expanduser(target_path)
    if target_path.endswith(".csv"):
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"CSV not found: {target_path}")
        return target_path

    csv_file_fixed = os.path.join(target_path, f"{scenario_type}_dataset_fixed.csv")
    csv_file_normal = os.path.join(target_path, f"{scenario_type}_dataset.csv")

    if os.path.exists(csv_file_fixed):
        return csv_file_fixed
    if os.path.exists(csv_file_normal):
        return csv_file_normal
    raise FileNotFoundError(
        f"Dataset not found: {csv_file_normal} (or _fixed.csv)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize sparse cells around the learned collision boundary."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="~/simulation_traces",
        help="Dataset directory or CSV file",
    )
    parser.add_argument("--type", default="uturn", help="Scenario type")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output CSV path for per-cell summary",
    )
    return parser


def run_analysis(target: str, scenario_type: str, output_path: str | None = None) -> dict:
    cfg = importlib.import_module(f"configs.{scenario_type}")
    csv_file = resolve_dataset_path(target, scenario_type)
    df = pd.read_csv(csv_file, engine="python", on_bad_lines="skip")
    param_names = list(cfg.PARAM_RANGES.keys())

    summary = point_extractors.summarize_boundary_gap_progress(df, param_names, cfg)
    cell_df = summary["cell_df"]

    if output_path is None:
        base_dir = os.path.dirname(csv_file)
        output_path = os.path.join(base_dir, f"{scenario_type}_boundary_gap_cells.csv")
    output_path = os.path.expanduser(output_path)

    cell_df.to_csv(output_path, index=False)

    return {
        "config": cfg,
        "csv_file": csv_file,
        "param_names": param_names,
        "summary": summary,
        "output_path": output_path,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_analysis(args.target, args.type, args.output)

    summary = result["summary"]
    param_names = result["param_names"]
    cell_df = summary["cell_df"]

    print("[BoundaryGap] 集計結果")
    print(f"  - 対象セル数: {summary['total_cells']}")
    print(f"  - まだ薄い境界セル: {summary['candidate_cells']}")
    print(f"  - 危険寄りだが十分に観測済みのセル: {summary['densified_cells']}")
    print(f"  - 明確化済みセル: {summary['clarified_cells']}")

    if not cell_df.empty:
        preview = cell_df[cell_df["is_candidate"]].head(12)
        if not preview.empty:
            print("\n[BoundaryGap] 優先候補セル")
            display_cols = param_names + [
                "sample_count",
                "collision_ratio",
                "near_ratio",
                "status",
            ]
            print(preview[display_cols].to_string(index=False))
        print(f"\n[BoundaryGap] セル一覧を保存しました: {result['output_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
