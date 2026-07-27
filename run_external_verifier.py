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

from external_verifiers import (  # noqa: E402
    ExternalVerificationRequest,
    create_verifier,
    list_verifiers,
)


DEFAULT_BBSL_REPO = "/home/passd/BBSL-test"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run an external experiment/verifier entrypoint from the "
            "AWSIM_launch framework. This keeps the verifier and the target "
            "system decoupled. For FT4D-core unification, prefer the "
            "raw-result flow via run_bbsl_local_ft4d.py."
        )
    )
    parser.add_argument(
        "--verifier",
        choices=list_verifiers(),
        default="bbsl_ft4d",
        help="External experiment/verifier adapter to use.",
    )
    parser.add_argument(
        "--target-name",
        default="BBSL-test",
        help="Human-readable target name.",
    )
    parser.add_argument(
        "--target-repo",
        default=DEFAULT_BBSL_REPO,
        help="Repository root of the external target/verifier project.",
    )
    parser.add_argument("--mini", action="store_true", help="Run the target verifier in mini mode.")
    parser.add_argument("--max-images", type=int, default=None, help="Image limit passed to BBSL-test.")
    parser.add_argument(
        "--tree",
        choices=["all", "basic", "combined"],
        default="basic",
        help="Fault-tree mode passed to BBSL-test.",
    )
    parser.add_argument(
        "--sigma-pf-source",
        choices=["dataset", "assumption"],
        default="dataset",
        help="sigma_pf source passed to BBSL-test.",
    )
    parser.add_argument(
        "--sigma-pb-mode",
        choices=["raw", "delta-clean"],
        default="delta-clean",
        help="sigma_pb mode passed to BBSL-test.",
    )
    parser.add_argument(
        "--and-rule",
        choices=["min", "product"],
        default="min",
        help="AND rule passed to BBSL-test.",
    )
    parser.add_argument(
        "--detect-timeout",
        type=int,
        default=None,
        help="Optional detect.py timeout override passed to BBSL-test.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to save the normalized AWSIM-side verification result.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    verifier = create_verifier(args.verifier)
    request = ExternalVerificationRequest(
        verifier_name=args.verifier,
        target_name=args.target_name,
        target_repo=args.target_repo,
        parameters={
            "mini": args.mini,
            "max_images": args.max_images,
            "tree": args.tree,
            "sigma_pf_source": args.sigma_pf_source,
            "sigma_pb_mode": args.sigma_pb_mode,
            "and_rule": args.and_rule,
            "detect_timeout": args.detect_timeout,
        },
    )

    result = verifier.run(request)
    payload = result.to_dict()

    if args.output_json is None:
        os.makedirs(os.path.join(LAUNCH_DIR, "verification_results"), exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output_json = os.path.join(
            LAUNCH_DIR,
            "verification_results",
            f"{args.verifier}_{suffix}.json",
        )

    with open(args.output_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    print("=" * 60)
    print("AWSIM_launch external verifier finished")
    print(f"verifier      : {result.verifier_name}")
    print(f"target        : {result.target_name}")
    print(f"return code   : {result.returncode}")
    print(f"result json   : {args.output_json}")
    print(f"external output: {result.raw_result_path}")
    if result.summary:
        print(f"summary state : {result.summary.get('status')}")
        print(f"mode          : {result.summary.get('integration_mode', 'external-launch-only')}")
        print(f"tree mode     : {result.summary.get('tree_mode')}")
        print(f"conditions    : {result.summary.get('active_conditions')}")
        print(f"top sigma_pe  : {result.summary.get('top_sigma_pe')}")
        if result.summary.get("recommended_path"):
            print(
                "recommended   : use "
                f"{result.summary.get('recommended_path')} "
                "for the raw-result -> AWSIM FT4D path"
            )
    print("=" * 60)

    if result.returncode != 0:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
