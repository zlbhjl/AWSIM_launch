#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

LAUNCH_DIR = Path(__file__).resolve().parents[2]

from contracts.verification import VerificationInput  # noqa: E402
from evaluation.ft4d_service import run_ft4d  # noqa: E402
from strategist import ActiveLearningStrategist  # noqa: E402
from targets.bbsl.ft4d_bridge import (  # noqa: E402
    evaluate_ft4d_from_bbsl_batch_outputs as evaluate_ft4d_from_bbsl_batch_outputs_bridge,
    evaluate_ft4d_from_bbsl_output as evaluate_ft4d_from_bbsl_output_bridge,
)
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter  # noqa: E402
from targets.bbsl.event_builder import BBSLEventSetBuilder  # noqa: E402
from targets.bbsl.batch_loop import run_bbsl_until_ft4d_confident  # noqa: E402
from targets.bbsl.profile import build_execution_profile  # noqa: E402
from targets.bbsl.result_interpreter import ResultInterpreter  # noqa: E402
from targets.bbsl.runner import (  # noqa: E402
    cleanup_bbsl_batch_state,
    run_bbsl_experiment,
)
from targets.bbsl.verification_input import build_verification_input  # noqa: E402


DEFAULT_BBSL_REPO = "/home/passd/BBSL-test"
SIGMA_PF_EVENT_IDS = {
    "SALT_PEPPER",
    "OCCLUSION",
    "BLUR",
    "SP_OCC",
    "SP_BLUR",
    "OCC_BLUR",
    "ALL_THREE",
}

BASIC_EVENT_MAPPING = {
    "SALT_PEPPER": "salt_pepper",
    "OCCLUSION": "occlusion",
    "BLUR": "blur",
}

COMBINED_EVENT_MAPPING = {
    **BASIC_EVENT_MAPPING,
    "SP_OCC": "sp_occ",
    "SP_BLUR": "sp_blur",
    "OCC_BLUR": "occ_blur",
    "ALL_THREE": "all_three",
}


def _load_sigma_pf_assumption_overrides(args):
    overrides = {}

    if args.sigma_pf_json is not None:
        with open(args.sigma_pf_json, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("--sigma-pf-json must point to a JSON object")
        for key, value in payload.items():
            event_id = str(key).strip().upper()
            if event_id not in SIGMA_PF_EVENT_IDS:
                raise ValueError(
                    f"Unknown sigma_pf event ID in JSON: {event_id!r}. "
                    f"Expected one of {sorted(SIGMA_PF_EVENT_IDS)}"
                )
            overrides[event_id] = float(value)

    for item in args.sigma_pf_value:
        if "=" not in item:
            raise ValueError(
                f"Invalid --sigma-pf-value {item!r}. Use EVENT_ID=value."
            )
        event_id_raw, value_raw = item.split("=", 1)
        event_id = event_id_raw.strip().upper()
        if event_id not in SIGMA_PF_EVENT_IDS:
            raise ValueError(
                f"Unknown sigma_pf event ID: {event_id!r}. "
                f"Expected one of {sorted(SIGMA_PF_EVENT_IDS)}"
            )
        overrides[event_id] = float(value_raw)

    for event_id, value in overrides.items():
        if value < 0.0:
            raise ValueError(
                f"sigma_pf override for {event_id} must be non-negative, got {value}"
            )
    return overrides or None


def _merge_sigma_pf_assumptions(base, overrides):
    merged = dict(base or {})
    if overrides:
        merged.update(overrides)
    return merged


def _tree_path_for_mode(tree_mode: str) -> str:
    if tree_mode not in {"basic", "combined"}:
        raise ValueError(f"Unsupported tree mode: {tree_mode!r}")
    filename = "tree_basic.json" if tree_mode == "basic" else "tree_bbsl.json"
    return str(LAUNCH_DIR / "verification_core" / "ft4d" / "config" / filename)


def _event_mapping(tree_mode: str) -> dict[str, str]:
    if tree_mode == "basic":
        return BASIC_EVENT_MAPPING
    if tree_mode == "combined":
        return COMBINED_EVENT_MAPPING
    raise ValueError(f"Unsupported tree mode: {tree_mode!r}")


def _render_tree_report(report: dict) -> str:
    labels = report.get("labels", {})
    lines: list[str] = []

    def build(node: dict, indent: str = "", is_last: bool = True) -> None:
        node_id = node["id"]
        label = labels.get(node_id, node_id)
        sigma_pf = float(node.get("sigma_pf", 0.0))
        sigma_pe = float(node.get("sigma_pe", 0.0))
        connector = "└── " if is_last else "├── "

        if node.get("type") == "basic":
            sigma_pb = float(node.get("sigma_pb", 0.0))
            line = (
                f"{indent}{connector}■ {label}  "
                f"(σpf={sigma_pf:.4f}, σpb={sigma_pb:.4f}, σpe={sigma_pe:.6f})"
            )
        else:
            gate = node.get("gate", "")
            line = (
                f"{indent}{connector}□ {label}  [{gate}]  "
                f"(σpf={sigma_pf:.4f}, σpe={sigma_pe:.6f})"
            )
        lines.append(line)

        children = node.get("children", [])
        child_indent = indent + ("    " if is_last else "│   ")
        for index, child in enumerate(children):
            build(child, child_indent, index == len(children) - 1)

    build(report["tree"])
    return "\n".join(lines)


def _serialize_event_inputs(event_inputs: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    serialized: dict[str, dict[str, object]] = {}
    for event_id, payload in event_inputs.items():
        serialized[event_id] = {
            **{
                key: value
                for key, value in payload.items()
                if key not in {"dataset_d", "dataset_e"}
            },
            "dataset_d": sorted(payload.get("dataset_d", [])),
            "dataset_e": sorted(payload.get("dataset_e", [])),
        }
    return serialized


def _verification_input_from_built(
    built: dict[str, object],
    *,
    tree_mode: str,
    sigma_pf_source: str,
    and_rule: str,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
) -> tuple[VerificationInput, dict[str, dict[str, object]]]:
    mapping = _event_mapping(tree_mode)
    sigma_pf_assumptions = _merge_sigma_pf_assumptions(
        built.get("sigma_pf_assumptions", {}),
        sigma_pf_assumption_overrides,
    )
    built_events = built["events"]
    event_inputs: dict[str, dict[str, object]] = {}

    for event_id, condition_name in mapping.items():
        metrics = built_events[condition_name]
        event_inputs[event_id] = {
            "condition_name": condition_name,
            "dataset_d": set(metrics.get("dataset_d", [])),
            "dataset_e": set(metrics.get("dataset_e", [])),
            "total_count": int(metrics.get("total_count", 0) or 0),
            "correct_count": int(metrics.get("correct_count", 0) or 0),
            "error_count": int(metrics.get("error_count", 0) or 0),
            "sigma_pb": float(metrics.get("sigma_pb", 0.0) or 0.0),
            "sigma_pb_mode": metrics.get("sigma_pb_mode", built.get("sigma_pb_mode", "raw")),
            "recognition_test": metrics.get("recognition_test"),
        }

    verification_input = VerificationInput(
        tree_mode=tree_mode,
        universal_dataset=set(built.get("universal_dataset", set())),
        events=event_inputs,
        assumptions={
            "tree_path": _tree_path_for_mode(tree_mode),
            "sigma_pf_source": sigma_pf_source,
            "sigma_pf_assumptions": sigma_pf_assumptions,
            "sigma_pb_mode": built.get("sigma_pb_mode", "raw"),
            "and_rule": and_rule,
            "statistical_test_config": built.get("statistical_test_config", {}),
        },
        meta={
            "source_module": "run_bbsl_local_ft4d",
            "target": "bbsl",
            "tree_mode": tree_mode,
        },
    )
    return verification_input, event_inputs


def _run_ft4d_from_verification_input(
    verification_input: VerificationInput,
) -> dict[str, object]:
    ft4d_result = run_ft4d(verification_input)
    tree_report = ft4d_result.raw_result["tree_report"]
    return {
        "tree_mode": verification_input.tree_mode,
        "event_inputs": _serialize_event_inputs(verification_input.events),
        "local_ft4d_report": tree_report,
        "rendered_tree": _render_tree_report(tree_report),
        "top_sigma_pe": ft4d_result.top_sigma_pe,
        "confidence": ft4d_result.confidence,
        "node_summaries": ft4d_result.node_summaries,
        "raw_result": ft4d_result.raw_result,
    }


def _evaluate_bbsl_output_via_new_pipeline(
    output_json_path: str,
    *,
    tree_mode: str,
    sigma_pf_source: str,
    and_rule: str,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    return evaluate_ft4d_from_bbsl_output_bridge(
        output_json_path,
        tree_mode=tree_mode,
        sigma_pf_source=sigma_pf_source,
        and_rule=and_rule,
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )


def _iter_batch_payloads(adapter: BBSLExperimentAdapter, batch_output_paths):
    for path in batch_output_paths:
        payload = adapter.load_output(path)
        payload["source_output_json"] = path
        yield payload


def _evaluate_bbsl_batch_outputs_via_new_pipeline(
    clean_output_json_path: str,
    batch_output_paths: list[str],
    *,
    tree_mode: str,
    sigma_pf_source: str,
    sigma_pb_mode: str,
    and_rule: str,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    return evaluate_ft4d_from_bbsl_batch_outputs_bridge(
        clean_output_json_path,
        batch_output_paths,
        tree_mode=tree_mode,
        sigma_pf_source=sigma_pf_source,
        sigma_pb_mode=sigma_pb_mode,
        and_rule=and_rule,
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )


def _rebuild_batch_loop_result_via_new_pipeline(
    legacy_result: dict[str, object],
    *,
    tree_mode: str,
    sigma_pf_source: str,
    sigma_pb_mode: str,
    and_rule: str,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    batch_loop = legacy_result.get("batch_loop")
    if not isinstance(batch_loop, dict):
        raise ValueError("legacy_result does not contain batch_loop metadata")

    result = _evaluate_bbsl_batch_outputs_via_new_pipeline(
        batch_loop["clean_baseline_json"],
        batch_loop["source_batch_jsons"],
        tree_mode=tree_mode,
        sigma_pf_source=sigma_pf_source,
        sigma_pb_mode=sigma_pb_mode,
        and_rule=and_rule,
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )
    result["batch_loop"] = dict(batch_loop)
    return result


def _build_execution_profile_from_args(
    args,
    *,
    sigma_pf_source: str,
):
    return build_execution_profile(
        {
            "target_repo": args.target_repo,
            "mini": args.mini,
            "max_images": args.max_images,
            "tree_mode": args.tree,
            "sigma_pf_source": sigma_pf_source,
            "sigma_pb_mode": args.sigma_pb_mode,
            "and_rule": args.and_rule,
            "detect_timeout": args.detect_timeout,
            "reuse_existing_output": args.reuse_existing_output,
        },
        default_target_repo=DEFAULT_BBSL_REPO,
    )

def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(
        description=(
            "Run BBSL data generation with BBSL-test outputs, then rebuild "
            "U/D/E inside AWSIM_launch and execute the local FT4D core."
        )
    )
    parser.add_argument("--target-repo", default=DEFAULT_BBSL_REPO)
    parser.add_argument(
        "--execution-mode",
        choices=["legacy", "batch-loop"],
        default="batch-loop",
        help="legacy は従来の run_full_experiment_all.py、batch-loop は clean baseline + noisy batch repeat を使う。",
    )
    parser.add_argument(
        "--condition-policy",
        choices=["all", "underconfident"],
        default="all",
        help="batch-loop 時に毎回全条件を回すか、不足事象に対応する条件だけ追加実行するか。",
    )
    parser.add_argument(
        "--run-mode",
        choices=["fresh", "resume"],
        default="resume",
        help=(
            "batch-loop 時の開始方法。fresh は古い batch JSON と生成済みノイズ画像を消して最初から、"
            "resume は既存状態を引き継いで続きから実行する。"
        ),
    )
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
        "--sigma-pf-json",
        default=None,
        help=(
            "JSON file containing sigma_pf assumptions, for example "
            "{\"SALT_PEPPER\": 0.12, \"OCCLUSION\": 0.03, \"BLUR\": 0.08}."
        ),
    )
    parser.add_argument(
        "--sigma-pf-value",
        action="append",
        default=[],
        help=(
            "Override one sigma_pf assumption with EVENT_ID=value. "
            "Can be repeated, for example --sigma-pf-value SALT_PEPPER=0.12"
        ),
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
    parser.add_argument("--master-seed", type=int, default=1000)
    parser.add_argument(
        "--salt-pepper-density-range",
        type=float,
        nargs=2,
        metavar=("MIN", "MAX"),
        default=(0.01, 0.08),
    )
    parser.add_argument(
        "--occlusion-severity-range",
        type=float,
        nargs=2,
        metavar=("MIN", "MAX"),
        default=(0.2, 0.5),
    )
    parser.add_argument(
        "--blur-kernel-range",
        type=int,
        nargs=2,
        metavar=("MIN", "MAX"),
        default=(5, 11),
    )
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--max-total-trials", type=int, default=500000)
    parser.add_argument("--no-progress-patience", type=int, default=3)
    parser.add_argument(
        "--no-resume-batches",
        action="store_true",
        help="既存 noisy_batch_*.json を引き継がず、batch 1 からやり直す。",
    )
    parser.add_argument(
        "--refresh-clean-baseline",
        action="store_true",
        help="batch-loop 時に既存 clean baseline を無視して再作成する。",
    )
    parser.add_argument(
        "--reuse-existing-output",
        action="store_true",
        help="Reuse the existing BBSL output JSON instead of rerunning the experiment.",
    )
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    adapter = BBSLExperimentAdapter()
    cleanup_summary = None
    sigma_pf_assumption_overrides = _load_sigma_pf_assumption_overrides(args)
    effective_sigma_pf_source = args.sigma_pf_source
    if (
        sigma_pf_assumption_overrides is not None
        and effective_sigma_pf_source != "assumption"
    ):
        effective_sigma_pf_source = "assumption"
    execution_profile = _build_execution_profile_from_args(
        args,
        sigma_pf_source=effective_sigma_pf_source,
    )

    if args.run_mode == "fresh":
        if args.execution_mode != "batch-loop":
            raise ValueError("--run-mode fresh is supported only with --execution-mode batch-loop")
        cleanup_summary = cleanup_bbsl_batch_state(execution_profile.target_repo)

    if args.execution_mode == "batch-loop" and args.condition_policy == "underconfident":
        strategist = ActiveLearningStrategist(
            "ft4d_bridge",
            SimpleNamespace(
                PARAM_RANGES={"x": (0.0, 1.0)},
                FT4D_MIN_CONFIDENCE=args.min_confidence,
            ),
        )
        strategic_result = strategist.inspect_bbsl_ft4d_confidence_gaps(
            target_repo=execution_profile.target_repo,
            execution_mode="batch-loop",
            condition_policy="underconfident",
            mini=execution_profile.mini,
            max_images=execution_profile.max_images,
            tree=execution_profile.tree_mode,
            sigma_pf_source=execution_profile.sigma_pf_source,
            sigma_pb_mode=execution_profile.sigma_pb_mode,
            and_rule=execution_profile.and_rule,
            detect_timeout=execution_profile.detect_timeout,
            master_seed=args.master_seed,
            salt_pepper_density_range=tuple(args.salt_pepper_density_range),
            occlusion_severity_range=tuple(args.occlusion_severity_range),
            blur_kernel_range=tuple(args.blur_kernel_range),
            refresh_clean_baseline=(args.run_mode == "fresh") or args.refresh_clean_baseline,
            resume_batches=(args.run_mode == "resume") and (not args.no_resume_batches),
            max_batches=args.max_batches,
            max_total_trials=args.max_total_trials,
            no_progress_patience=args.no_progress_patience,
            min_confidence=args.min_confidence,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
        result = _rebuild_batch_loop_result_via_new_pipeline(
            strategic_result["ft4d_result"],
            tree_mode=execution_profile.tree_mode,
            sigma_pf_source=execution_profile.sigma_pf_source,
            sigma_pb_mode=execution_profile.sigma_pb_mode,
            and_rule=execution_profile.and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
        result["confidence_summary"] = strategic_result["confidence_summary"]
        result["condition_policy"] = strategic_result["condition_policy"]
    elif args.execution_mode == "batch-loop":
        legacy_result = run_bbsl_until_ft4d_confident(
            target_repo=execution_profile.target_repo,
            mini=execution_profile.mini,
            max_images=execution_profile.max_images,
            tree=execution_profile.tree_mode,
            sigma_pf_source=execution_profile.sigma_pf_source,
            sigma_pb_mode=execution_profile.sigma_pb_mode,
            and_rule=execution_profile.and_rule,
            detect_timeout=execution_profile.detect_timeout,
            master_seed=args.master_seed,
            salt_pepper_density_range=tuple(args.salt_pepper_density_range),
            occlusion_severity_range=tuple(args.occlusion_severity_range),
            blur_kernel_range=tuple(args.blur_kernel_range),
            reuse_clean_baseline=not ((args.run_mode == "fresh") or args.refresh_clean_baseline),
            resume_batches=(args.run_mode == "resume") and (not args.no_resume_batches),
            max_batches=args.max_batches,
            max_total_trials=args.max_total_trials,
            no_progress_patience=args.no_progress_patience,
            min_confidence=args.min_confidence,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
        result = _rebuild_batch_loop_result_via_new_pipeline(
            legacy_result,
            tree_mode=execution_profile.tree_mode,
            sigma_pf_source=execution_profile.sigma_pf_source,
            sigma_pb_mode=execution_profile.sigma_pb_mode,
            and_rule=execution_profile.and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
    else:
        if execution_profile.reuse_existing_output:
            source_output_json = adapter.default_output_path(
                execution_profile.target_repo,
                execution_profile.mini,
                prefer_raw=True,
            )
        else:
            source_output_json = run_bbsl_experiment(
                execution_profile.target_repo,
                mini=execution_profile.mini,
                max_images=execution_profile.max_images,
                tree=(
                    "basic"
                    if execution_profile.tree_mode == "all"
                    else execution_profile.tree_mode
                ),
                sigma_pf_source=execution_profile.sigma_pf_source,
                sigma_pb_mode=execution_profile.sigma_pb_mode,
                and_rule=execution_profile.and_rule,
                detect_timeout=execution_profile.detect_timeout,
            )
        result = _evaluate_bbsl_output_via_new_pipeline(
            source_output_json,
            tree_mode=execution_profile.tree_mode,
            sigma_pf_source=execution_profile.sigma_pf_source,
            and_rule=execution_profile.and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )

    result["run_mode"] = args.run_mode
    if cleanup_summary is not None:
        result["cleanup"] = cleanup_summary

    if args.output_json is None:
        os.makedirs(LAUNCH_DIR / "verification_results", exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output_json = str(
            LAUNCH_DIR
            / "verification_results"
            / f"bbsl_local_ft4d_{suffix}.json"
        )

    with open(args.output_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    print("=" * 60)
    print("AWSIM_launch local FT4D over BBSL output")
    if "source_output_json" in result:
        print(f"source output  : {result['source_output_json']}")
    elif "source_batch_jsons" in result:
        print(f"clean baseline : {result['clean_baseline_json']}")
        print(f"source batches : {result['source_batch_jsons']}")
    print(f"tree mode      : {result['tree_mode']}")
    print(f"conditions     : {result['active_conditions']}")
    print(f"sigma_pf       : {result['sigma_pf_source']}")
    if sigma_pf_assumption_overrides:
        print(f"sigma_pf override: {sigma_pf_assumption_overrides}")
    print(f"sigma_pb       : {result['sigma_pb_mode']}")
    print(f"and rule       : {result['and_rule']}")
    print(f"top sigma_pe   : {result['top_sigma_pe']}")
    print(f"execution mode : {args.execution_mode}")
    print(f"run mode       : {args.run_mode}")
    print(f"condition policy: {args.condition_policy}")
    print(f"resume batches : {(args.run_mode == 'resume') and (not args.no_resume_batches)}")
    print(f"result json    : {args.output_json}")
    if cleanup_summary is not None:
        print(
            "cleanup        : "
            f"json={len(cleanup_summary['removed_json_files'])}, "
            f"png={len(cleanup_summary['removed_detect_pngs'])}, "
            f"image_dirs={len(cleanup_summary['removed_generated_dirs'])}"
        )
    if "batch_loop" in result:
        loop = result["batch_loop"]
        print(f"clean baseline : {loop['clean_baseline_json']}")
        print(f"batch count    : {loop['completed_batches']}")
        print(f"stop reason    : {loop['stop_reason']}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
