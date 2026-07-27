#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

LAUNCH_DIR = os.path.dirname(os.path.abspath(__file__))
if LAUNCH_DIR not in sys.path:
    sys.path.append(LAUNCH_DIR)

from estimator import create_ft4d_only_estimator  # noqa: E402


DEFAULT_BBSL_REPO = "/home/passd/BBSL-test"

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run BBSL data generation with BBSL-test outputs, then rebuild "
            "U/D/E inside AWSIM_launch and execute the local FT4D core."
        )
    )
    parser.add_argument("--target-repo", default=DEFAULT_BBSL_REPO)
    parser.add_argument("--mini", action="store_true")
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument(
        "--tree",
        choices=["all", "basic", "combined"],
        default="basic",
    )
    parser.add_argument(
        "--sigma-pf-source",
        choices=["dataset", "assumption"],
        default="dataset",
    )
    parser.add_argument(
        "--sigma-pb-mode",
        choices=["raw", "delta-clean"],
        default="delta-clean",
    )
    parser.add_argument(
        "--and-rule",
        choices=["min", "product"],
        default="min",
    )
    parser.add_argument("--detect-timeout", type=int, default=None)
    parser.add_argument(
        "--reuse-existing-output",
        action="store_true",
        help="Reuse the existing BBSL output JSON instead of rerunning the experiment.",
    )
    parser.add_argument("--output-json", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    estimator = create_ft4d_only_estimator()
    result = estimator.run_bbsl_and_evaluate_ft4d(
        target_repo=args.target_repo,
        mini=args.mini,
        max_images=args.max_images,
        tree=args.tree,
        sigma_pf_source=args.sigma_pf_source,
        sigma_pb_mode=args.sigma_pb_mode,
        and_rule=args.and_rule,
        detect_timeout=args.detect_timeout,
        reuse_existing_output=args.reuse_existing_output,
    )

    if args.output_json is None:
        os.makedirs(os.path.join(LAUNCH_DIR, "verification_results"), exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output_json = os.path.join(
            LAUNCH_DIR,
            "verification_results",
            f"bbsl_local_ft4d_{suffix}.json",
        )

    with open(args.output_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    print("=" * 60)
    print("AWSIM_launch local FT4D over BBSL output")
    print(f"source output  : {result['source_output_json']}")
    print(f"tree mode      : {result['tree_mode']}")
    print(f"conditions     : {result['active_conditions']}")
    print(f"sigma_pf       : {result['sigma_pf_source']}")
    print(f"sigma_pb       : {result['sigma_pb_mode']}")
    print(f"and rule       : {result['and_rule']}")
    print(f"top sigma_pe   : {result['top_sigma_pe']}")
    print(f"result json    : {args.output_json}")
    print()
    if result["tree_mode"] == "all":
        print("[basic]")
        print(result["local_runs"]["basic"]["rendered_tree"])
        print()
        print("[combined]")
        print(result["local_runs"]["combined"]["rendered_tree"])
    else:
        print(result["rendered_tree"])
    print("=" * 60)


if __name__ == "__main__":
    main()
