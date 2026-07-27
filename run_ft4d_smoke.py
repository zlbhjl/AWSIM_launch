#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from adapters.awsim import AWSIMEventSetBuilder  # noqa: E402
from verification_core.ft4d import (  # noqa: E402
    FT4DCalculator,
    FT4DVisualizer,
    FaultTree,
    basic_error_rate,
)


DEFAULT_TREE_PATH = os.path.join(
    LAUNCH_DIR,
    "verification_core",
    "ft4d",
    "config",
    "awsim_demo_tree.json",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the local FT4D core inside AWSIM_launch with a synthetic "
            "AWSIM-like dataset. This is a smoke check only; it does not touch "
            "the existing strategist/estimator pipeline."
        )
    )
    parser.add_argument(
        "--tree",
        default=DEFAULT_TREE_PATH,
        help="Path to the FT4D JSON tree config inside AWSIM_launch.",
    )
    parser.add_argument(
        "--sigma-pf-source",
        choices=["dataset", "assumption"],
        default="dataset",
        help="sigma_pf source passed to FT4DCalculator.",
    )
    parser.add_argument(
        "--and-rule",
        choices=["min", "product"],
        default="min",
        help="AND combination rule for the FT4D smoke run.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to save the FT4D smoke result JSON.",
    )
    return parser.parse_args()


def build_demo_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"loop_num": 1, "ego_speed": 38.0, "dx0": 14.0, "c_collision": 1, "min_ttc": 0.9},
            {"loop_num": 2, "ego_speed": 36.0, "dx0": 20.0, "c_collision": 0, "min_ttc": 2.4},
            {"loop_num": 3, "ego_speed": 28.0, "dx0": 12.0, "c_collision": 0, "min_ttc": 1.2},
            {"loop_num": 4, "ego_speed": 32.0, "dx0": 16.0, "c_collision": 0, "min_ttc": 1.8},
            {"loop_num": 5, "ego_speed": 42.0, "dx0": 11.0, "c_collision": 1, "min_ttc": 0.6},
            {"loop_num": 6, "ego_speed": 26.0, "dx0": 22.0, "c_collision": 0, "min_ttc": 3.5},
            {"loop_num": 7, "ego_speed": 34.0, "dx0": 15.0, "c_collision": 0, "min_ttc": 1.4},
            {"loop_num": 8, "ego_speed": 40.0, "dx0": 19.0, "c_collision": 0, "min_ttc": 1.1},
        ]
    )


def build_demo_event_definitions() -> dict[str, dict[str, str]]:
    return {
        "HIGH_SPEED": {
            "dataset_filter": "ego_speed >= 35",
            "error_filter": "c_collision == 1",
            "target_column": "c_collision",
        },
        "SHORT_GAP": {
            "dataset_filter": "dx0 <= 18",
            "error_filter": "(c_collision == 1) | (min_ttc < 1.5)",
            "target_column": "min_ttc",
        },
        "LOW_TTC": {
            "dataset_filter": "min_ttc >= 0",
            "error_filter": "(c_collision == 1) | (min_ttc < 1.5)",
            "target_column": "min_ttc",
        },
    }


def run_smoke(tree_path: str, sigma_pf_source: str, and_rule: str) -> dict:
    demo_df = build_demo_dataframe()
    event_definitions = build_demo_event_definitions()
    builder = AWSIMEventSetBuilder()
    event_inputs = builder.build_event_inputs(demo_df, event_definitions)

    tree = FaultTree.from_json(tree_path)
    calc = FT4DCalculator(
        tree,
        sigma_pf_source=sigma_pf_source,
        and_rule=and_rule,
    )
    calc.set_universal_dataset(event_inputs["universal_dataset"])

    for event_id, metrics in event_inputs["events"].items():
        sigma_pb = basic_error_rate(
            metrics["error_count"],
            metrics["total_count"],
        )
        sigma_pf_assumed = tree.params.get(event_id, {}).get("sigma_pf", 0.0)
        calc.set_basic_event(event_id, sigma_pf=sigma_pf_assumed, sigma_pb=sigma_pb)
        calc.set_basic_event_datasets(
            event_id,
            metrics["dataset_d"],
            metrics["dataset_e"],
        )

    report = calc.calculate()
    visualizer = FT4DVisualizer(tree)
    return {
        "tree_path": tree_path,
        "sigma_pf_source": sigma_pf_source,
        "and_rule": and_rule,
        "event_inputs": {
            event_id: {
                **{
                    key: value
                    for key, value in metrics.items()
                    if key not in {"dataset_d", "dataset_e"}
                },
                "dataset_d": sorted(metrics["dataset_d"]),
                "dataset_e": sorted(metrics["dataset_e"]),
            }
            for event_id, metrics in event_inputs["events"].items()
        },
        "tree_report": report,
        "rendered_tree": visualizer.render_tree(),
        "top_sigma_pe": report["tree"]["sigma_pe"],
    }


def main():
    args = parse_args()
    result = run_smoke(
        tree_path=args.tree,
        sigma_pf_source=args.sigma_pf_source,
        and_rule=args.and_rule,
    )
    print("=" * 60)
    print("AWSIM_launch internal FT4D smoke run")
    print(f"tree path     : {result['tree_path']}")
    print(f"sigma_pf      : {result['sigma_pf_source']}")
    print(f"and rule      : {result['and_rule']}")
    print(f"top sigma_pe  : {result['top_sigma_pe']:.6f}")
    print()
    print(result["rendered_tree"])
    print("=" * 60)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        print(f"Saved smoke result to {args.output_json}")


if __name__ == "__main__":
    main()
