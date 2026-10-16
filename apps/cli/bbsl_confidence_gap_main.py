from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from targets.bbsl.batch_loop import run_bbsl_until_ft4d_confident
from targets.bbsl.ft4d_analysis import (
    summarize_ft4d_completion,
    summarize_ft4d_confidence_gaps,
)
from targets.bbsl.ft4d_bridge import run_bbsl_and_evaluate_ft4d
from targets.bbsl.underconfident_loop import run_bbsl_underconfident_loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the v2 BBSL confidence-gap workflow and emit a summarized FT4D report."
    )
    parser.add_argument("--target-repo", required=True)
    parser.add_argument(
        "--execution-mode",
        choices=["legacy", "batch-loop"],
        default="legacy",
    )
    parser.add_argument(
        "--condition-policy",
        choices=["all", "underconfident"],
        default="all",
    )
    parser.add_argument(
        "--run-mode",
        choices=["fresh", "resume"],
        default="resume",
    )
    parser.add_argument("--mini", action="store_true")
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument("--tree", choices=["basic", "combined", "all"], default="basic")
    parser.add_argument("--sigma-pf-source", default="dataset")
    parser.add_argument("--sigma-pb-mode", default="delta-clean")
    parser.add_argument("--and-rule", default="min")
    parser.add_argument("--detect-timeout", type=int, default=None)
    parser.add_argument("--reuse-existing-output", action="store_true")
    parser.add_argument("--master-seed", type=int, default=1000)
    parser.add_argument("--refresh-clean-baseline", action="store_true")
    parser.add_argument("--no-resume-batches", action="store_true")
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--max-total-trials", type=int, default=500000)
    parser.add_argument("--no-progress-patience", type=int, default=3)
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--output-json", default=None)
    return parser


def run_confidence_gap(argv: Sequence[str] | None = None) -> dict[str, object]:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    target_repo = str(Path(args.target_repo).expanduser().resolve())

    if args.execution_mode == "batch-loop" and args.condition_policy == "underconfident":
        result = run_bbsl_underconfident_loop(
            target_repo=target_repo,
            mini=args.mini,
            max_images=args.max_images,
            tree=args.tree,
            sigma_pf_source=args.sigma_pf_source,
            sigma_pb_mode=args.sigma_pb_mode,
            and_rule=args.and_rule,
            detect_timeout=args.detect_timeout,
            master_seed=args.master_seed,
            refresh_clean_baseline=(args.run_mode == "fresh") or args.refresh_clean_baseline,
            resume_batches=(args.run_mode == "resume") and (not args.no_resume_batches),
            max_batches=args.max_batches,
            max_total_trials=args.max_total_trials,
            no_progress_patience=args.no_progress_patience,
            min_confidence=args.min_confidence,
            summarize_confidence_gaps_fn=summarize_ft4d_confidence_gaps,
        )
        ft4d_result = result["ft4d_result"]
        confidence_summary = result["confidence_summary"]
    elif args.execution_mode == "batch-loop":
        ft4d_result = run_bbsl_until_ft4d_confident(
            target_repo=target_repo,
            mini=args.mini,
            max_images=args.max_images,
            tree=args.tree,
            sigma_pf_source=args.sigma_pf_source,
            sigma_pb_mode=args.sigma_pb_mode,
            and_rule=args.and_rule,
            detect_timeout=args.detect_timeout,
            master_seed=args.master_seed,
            reuse_clean_baseline=not ((args.run_mode == "fresh") or args.refresh_clean_baseline),
            resume_batches=(args.run_mode == "resume") and (not args.no_resume_batches),
            max_batches=args.max_batches,
            max_total_trials=args.max_total_trials,
            no_progress_patience=args.no_progress_patience,
            min_confidence=args.min_confidence,
        )
        confidence_summary = summarize_ft4d_confidence_gaps(
            ft4d_result,
            min_confidence=args.min_confidence,
        )
    else:
        ft4d_result = run_bbsl_and_evaluate_ft4d(
            target_repo=target_repo,
            mini=args.mini,
            max_images=args.max_images,
            tree=args.tree,
            sigma_pf_source=args.sigma_pf_source,
            sigma_pb_mode=args.sigma_pb_mode,
            and_rule=args.and_rule,
            detect_timeout=args.detect_timeout,
            reuse_existing_output=args.reuse_existing_output,
        )
        confidence_summary = summarize_ft4d_confidence_gaps(
            ft4d_result,
            min_confidence=args.min_confidence,
        )

    completion_summary = summarize_ft4d_completion(
        ft4d_result,
        min_confidence=args.min_confidence,
    )
    payload = {
        "target_repo": target_repo,
        "execution_mode": args.execution_mode,
        "condition_policy": args.condition_policy,
        "run_mode": args.run_mode,
        "tree_mode": args.tree,
        "ft4d_result": ft4d_result,
        "confidence_summary": confidence_summary,
        "completion_summary": completion_summary,
    }

    output_json = args.output_json
    if output_json is None:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_json = str(
            Path("verification_results").resolve()
            / f"bbsl_confidence_gap_{suffix}.json"
        )
    output_path = Path(output_json).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    payload["output_json"] = str(output_path)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    payload = run_confidence_gap(argv)
    confidence_summary = payload["confidence_summary"]
    completion_summary = payload["completion_summary"]
    print("=" * 60)
    print("AWSIM_launch BBSL confidence-gap summary")
    print(f"target repo    : {payload['target_repo']}")
    print(f"execution mode : {payload['execution_mode']}")
    print(f"condition policy: {payload['condition_policy']}")
    print(f"run mode       : {payload['run_mode']}")
    print(f"tree mode      : {payload['tree_mode']}")
    print(f"underconfident : {confidence_summary['underconfident_unique_count']}")
    print(f"total events   : {confidence_summary['total_unique_events']}")
    print(f"complete       : {completion_summary['complete']}")
    print(f"min confidence : {completion_summary['min_confidence']:.5f}")
    print(f"total trials   : {completion_summary['total_trials']}")
    print(f"result json    : {payload['output_json']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
