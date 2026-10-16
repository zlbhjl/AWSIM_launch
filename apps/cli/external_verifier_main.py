from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from external_verifiers import (
    ExternalVerificationRequest,
    ExternalVerificationResult,
    create_verifier,
    list_verifiers,
)


LAUNCH_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BBSL_REPO = "/home/passd/BBSL-test"


def build_parser(verifier_choices: Sequence[str] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a legacy-compatible external experiment/verifier entrypoint "
            "from the AWSIM_launch framework. In the refactored design, BBSL "
            "itself belongs under targets/bbsl and this CLI remains as a "
            "migration/compatibility path. The current bbsl_ft4d adapter keeps "
            "the legacy request/response shape but internally calls the new "
            "targets/bbsl/* + evaluation/ft4d_service.py path in-process."
        )
    )
    parser.add_argument(
        "--verifier",
        choices=list(verifier_choices or list_verifiers()),
        default="bbsl_ft4d",
        help="Legacy external experiment/verifier adapter to use.",
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
        "--reuse-existing-output",
        action="store_true",
        help=(
            "Reuse an existing BBSL raw output JSON when the legacy adapter "
            "delegates to the new in-process BBSL target pipeline."
        ),
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to save the normalized AWSIM-side legacy verification result.",
    )
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    verifier_choices: Sequence[str] | None = None,
) -> argparse.Namespace:
    return build_parser(verifier_choices=verifier_choices).parse_args(
        list(argv) if argv is not None else None
    )


def build_request(args: argparse.Namespace) -> ExternalVerificationRequest:
    return ExternalVerificationRequest(
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
            "reuse_existing_output": args.reuse_existing_output,
        },
    )


def resolve_output_json_path(
    output_json: str | None,
    *,
    verifier_name: str,
    now: Callable[[], datetime] | None = None,
) -> Path:
    if output_json:
        return Path(output_json).expanduser().resolve()

    clock = now or datetime.now
    suffix = clock().strftime("%Y%m%d_%H%M%S")
    return (
        LAUNCH_DIR
        / "verification_results"
        / f"{verifier_name}_{suffix}.json"
    ).resolve()


def save_result_payload(output_path: Path, payload: dict[str, object]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_summary(result: ExternalVerificationResult, output_path: Path) -> None:
    print("=" * 60)
    print("AWSIM_launch external verifier finished")
    print(f"verifier      : {result.verifier_name}")
    print(f"target        : {result.target_name}")
    print(f"return code   : {result.returncode}")
    print(f"result json   : {output_path}")
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


def run_external_verifier(
    argv: Sequence[str] | None = None,
    *,
    create_verifier_fn=create_verifier,
    verifier_choices: Sequence[str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = parse_args(argv, verifier_choices=verifier_choices)
    verifier = create_verifier_fn(args.verifier)
    request = build_request(args)
    result = verifier.run(request)
    output_path = resolve_output_json_path(
        args.output_json,
        verifier_name=result.verifier_name,
        now=now,
    )
    save_result_payload(output_path, result.to_dict())
    print_summary(result, output_path)
    return int(result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    return run_external_verifier(argv)
