from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from apps.cli.worker_main import LEGACY_MODES, parse_param_assignments
from orchestration.orchestrator import Orchestrator, OrchestratorConfig, build_task_payload
from runtime.cluster.ray_queue import TaskQueue
from targets.awsim.case_kinds import load_focus_points


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the v2 orchestrator locally for queued target tasks."
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Path to a local target fixture.",
    )
    parser.add_argument(
        "--param",
        dest="params",
        action="append",
        default=[],
        help="Direct simulation parameter in key=value form. Repeat this option to pass multiple values.",
    )
    parser.add_argument(
        "--cluster",
        action="store_true",
        help="Dispatch this invocation to the cluster-oriented v2 orchestrator entrypoint.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the JSONL file where EvaluationRecord rows are appended.",
    )
    parser.add_argument("--case-id", default=None, help="Override case id. Defaults to fixture stem.")
    parser.add_argument("--case-kind", default="uturn", help="Case kind label.")
    parser.add_argument(
        "--mode",
        choices=LEGACY_MODES,
        default="explore",
        help="Strategy mode label. Active strategy currently supports explore/focus/margin/jama_edge/ttc_edge/worst_ttc/dkw/dkw_fixed/verify_consistency/binomial_ci/boundary_gap.",
    )
    parser.add_argument(
        "--target",
        default="awsim",
        choices=["awsim", "bbsl"],
        help="Target name for the worker.",
    )
    parser.add_argument("--worker-id", default="worker_v2_local", help="Worker identifier.")
    parser.add_argument("--reason", default="manual_orchestrator_run", help="Task reason label.")
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="Task tag. Repeat this option to add multiple tags.",
    )
    parser.add_argument(
        "--config-module",
        default=None,
        help="Rule spec module used by the AWSIM result interpreter.",
    )
    parser.add_argument(
        "--focus_points",
        default=None,
        help="Legacy JSON string for focus mode points. When omitted in focus mode, FOCUS_POINTS is loaded from the case-kind module.",
    )
    parser.add_argument(
        "--ext_mode",
        default="cvm",
        help="Kinematics extractor mode forwarded to worker/backend execution.",
    )
    parser.add_argument(
        "--path-root",
        default=None,
        help="Optional root used when relativizing evidence paths on save.",
    )
    parser.add_argument(
        "--dataset-csv",
        default=None,
        help="Optional dataset CSV path for shared-store style merged output.",
    )
    parser.add_argument(
        "--history-path",
        default=None,
        help="Optional processed-loop history path forwarded to the worker.",
    )
    parser.add_argument(
        "--queue-actor-name",
        default="local_task_queue",
        help="Queue actor name forwarded to queue-mode workers.",
    )
    parser.add_argument(
        "--queue-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the queue actor.",
    )
    parser.add_argument(
        "--queue-address",
        default=None,
        help="Optional Ray address used when resolving queue/shared actors.",
    )
    parser.add_argument(
        "--shared-store-actor-name",
        default=None,
        help="Optional detached shared-store actor name forwarded to v2 workers.",
    )
    parser.add_argument(
        "--shared-store-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the shared-store actor.",
    )
    parser.add_argument(
        "--shared-store-address",
        default=None,
        help="Optional Ray address used when resolving the shared-store actor.",
    )
    parser.add_argument(
        "--dkw-bounds",
        "--dkw_bounds",
        dest="dkw_bounds",
        default=None,
        help="Optional JSON string defining the bounded region for DKW/binomial evaluation.",
    )
    parser.add_argument(
        "--dkw-region",
        "--dkw_region",
        dest="dkw_region",
        default="custom",
        help="Optional extractor region label for DKW/binomial evaluation.",
    )
    parser.add_argument(
        "--dkw-pure-smc",
        "--dkw_pure_smc",
        dest="dkw_pure_smc",
        action="store_true",
        help="Evaluate DKW using only SMC-tagged samples instead of the merged dataset.",
    )
    parser.add_argument(
        "--dkw-simultaneous",
        "--dkw_simultaneous",
        dest="dkw_simultaneous",
        action="store_true",
        help="Evaluate multiple DKW metrics with Bonferroni-style simultaneous guarantees.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=None,
        help="Optional worker refresh policy forwarded to queue-mode worker runs.",
    )
    parser.add_argument(
        "--max-strategy-cases",
        type=int,
        default=None,
        help="Optional limit for how many strategy-generated cases to enqueue in one orchestrator run.",
    )
    parser.add_argument(
        "--max-samples",
        "--max_samples",
        dest="max_samples",
        type=int,
        default=None,
        help="Optional statistical sampling cap used by dkw_fixed/binomial_ci modes.",
    )
    parser.add_argument(
        "--resume-from",
        default=None,
        help="Optional directory containing a past <case_kind>_dataset.csv to restore.",
    )
    parser.add_argument(
        "--resume-current-only",
        action="store_true",
        help="When resuming, compute the next loop number from the current dataset only.",
    )
    parser.add_argument(
        "--scenario-type",
        default=None,
        help="Optional scenario type for direct simulation mode. Defaults to case-kind.",
    )
    parser.add_argument(
        "--simulation-output-dir",
        default=None,
        help="Optional output directory used by the AWSIM backend in direct simulation mode.",
    )
    parser.add_argument(
        "--expected-trace-path",
        default=None,
        help="Optional explicit trace JSON path expected from direct simulation mode.",
    )
    parser.add_argument(
        "--local-loop-num",
        type=int,
        default=None,
        help="Optional local loop number used when naming direct simulation outputs.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Forward Xvfb-based headless execution to the v2 worker backend.",
    )
    parser.add_argument(
        "--with-host-worker",
        action="store_true",
        help="Start a host-side v2 worker process alongside orchestrator control.",
    )
    parser.add_argument(
        "--headless-host",
        action="store_true",
        help="Run the host-side worker in headless mode.",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=None,
        help="Worker count used when deriving queue refill watermarks.",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=None,
        help="Optional strategy cache size override. Defaults to the legacy worker_count * 2 policy.",
    )
    parser.add_argument(
        "--queue-high-water",
        type=int,
        default=None,
        help="Explicit queue high-water size. Defaults to worker_count * 4.",
    )
    parser.add_argument(
        "--queue-low-water",
        type=int,
        default=None,
        help="Explicit queue low-water size. Defaults to worker_count * 2.",
    )
    parser.add_argument(
        "--run-external-workers",
        action="store_true",
        help="Do not spawn an inline worker; orchestrator only manages queue/refill/stop control.",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=2.0,
        help="Polling interval used when waiting for external workers.",
    )
    parser.add_argument(
        "--binomial-target",
        "--binomial_target",
        dest="binomial_target",
        default="c_collision",
        help="Binary metric column used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-method",
        "--binomial_method",
        dest="binomial_method",
        choices=["wilson", "clopper-pearson"],
        default="wilson",
        help="Confidence interval method used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-confidence",
        "--binomial_confidence",
        dest="binomial_confidence",
        type=float,
        default=0.95,
        help="Confidence level used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-target-width",
        "--binomial_target_width",
        dest="binomial_target_width",
        type=float,
        default=0.02,
        help="Target interval width used in binomial_ci mode.",
    )
    parser.add_argument(
        "--binomial-min-samples",
        "--binomial_min_samples",
        dest="binomial_min_samples",
        type=int,
        default=None,
        help="Optional minimum sample count before binomial_ci may stop early.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    fixture_mode = bool(getattr(args, "fixture", None))
    simulation_mode = bool(getattr(args, "params", []))
    strategy_mode = not fixture_mode and not simulation_mode

    if fixture_mode and simulation_mode:
        raise ValueError("Specify exactly one of --fixture or --param key=value")
    if strategy_mode and getattr(args, "target", "awsim") != "awsim":
        raise ValueError("Strategy mode without --fixture/--param is only supported for target=awsim")
    if strategy_mode and getattr(args, "dataset_csv", None) is None:
        raise ValueError("--dataset-csv is required for AWSIM strategy mode")
    if strategy_mode and getattr(args, "mode", "explore") not in {
        "explore",
        "focus",
        "margin",
        "jama_edge",
        "ttc_edge",
        "worst_ttc",
        "dkw",
        "dkw_fixed",
        "verify_consistency",
        "binomial_ci",
        "boundary_gap",
    }:
        raise ValueError(
            "AWSIM strategy mode currently supports "
            "--mode explore/focus/margin/jama_edge/ttc_edge/worst_ttc/dkw/dkw_fixed/verify_consistency/binomial_ci/boundary_gap only"
        )
    if getattr(args, "target", "awsim") != "awsim" and getattr(args, "headless", False):
        raise ValueError("--headless is only supported for target=awsim")
    refresh_interval = getattr(args, "refresh_interval", None)
    if refresh_interval is not None and refresh_interval <= 0:
        raise ValueError("--refresh-interval must be a positive integer")
    max_strategy_cases = getattr(args, "max_strategy_cases", None)
    if max_strategy_cases is not None and max_strategy_cases <= 0:
        raise ValueError("--max-strategy-cases must be a positive integer")
    worker_count = getattr(args, "worker_count", None)
    if worker_count is not None and worker_count <= 0:
        raise ValueError("--worker-count must be a positive integer")
    cache_size = getattr(args, "cache_size", None)
    if cache_size is not None and cache_size <= 0:
        raise ValueError("--cache-size must be a positive integer")
    queue_high_water = getattr(args, "queue_high_water", None)
    if queue_high_water is not None and queue_high_water <= 0:
        raise ValueError("--queue-high-water must be a positive integer")
    queue_low_water = getattr(args, "queue_low_water", None)
    if queue_low_water is not None and queue_low_water < 0:
        raise ValueError("--queue-low-water must be non-negative")
    poll_interval_sec = getattr(args, "poll_interval_sec", 2.0)
    if poll_interval_sec is not None and float(poll_interval_sec) < 0.0:
        raise ValueError("--poll-interval-sec must be non-negative")
    max_samples = getattr(args, "max_samples", None)
    if max_samples is not None and max_samples <= 0:
        raise ValueError("--max-samples must be a positive integer")
    binomial_confidence = getattr(args, "binomial_confidence", 0.95)
    if not 0.0 < float(binomial_confidence) < 1.0:
        raise ValueError("--binomial-confidence must satisfy 0.0 < value < 1.0")
    binomial_target_width = getattr(args, "binomial_target_width", 0.02)
    if binomial_target_width is not None and float(binomial_target_width) < 0.0:
        raise ValueError("--binomial-target-width must be non-negative")
    binomial_min_samples = getattr(args, "binomial_min_samples", None)
    if binomial_min_samples is not None and binomial_min_samples < 0:
        raise ValueError("--binomial-min-samples must be non-negative")
    if getattr(args, "target", "awsim") != "awsim" and any(
        [
            getattr(args, "scenario_type", None) is not None,
            getattr(args, "simulation_output_dir", None) is not None,
            getattr(args, "expected_trace_path", None) is not None,
            getattr(args, "local_loop_num", None) is not None,
            getattr(args, "focus_points", None) is not None,
        ]
    ):
        raise ValueError("AWSIM-only direct execution options are not supported for non-awsim targets")


def normalize_args(
    args: argparse.Namespace,
    *,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    raw_argv = list(argv) if argv is not None else []
    explicit_config_module = "--config-module" in raw_argv
    explicit_focus_points = "--focus_points" in raw_argv
    explicit_dkw_bounds = any(flag in raw_argv for flag in ("--dkw-bounds", "--dkw_bounds"))

    if getattr(args, "target", "awsim") != "awsim":
        if getattr(args, "config_module", None) is None:
            args.config_module = f"targets.awsim.case_kinds.{args.case_kind}"
        return args

    if args.config_module is None or not explicit_config_module:
        args.config_module = f"targets.awsim.case_kinds.{args.case_kind}"

    if args.mode == "focus":
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
        )
    else:
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
            allow_config_default=False,
        )
    if explicit_dkw_bounds and args.dkw_bounds:
        try:
            parsed_bounds = json.loads(args.dkw_bounds)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--dkw-bounds must be valid JSON: {exc}") from exc
        if not isinstance(parsed_bounds, dict):
            raise ValueError("--dkw-bounds must decode to an object")
        args.dkw_bounds = parsed_bounds
    return args


def _resolve_focus_points(
    *,
    case_kind: str,
    config_module: str,
    raw_focus_points: str | None,
    allow_config_default: bool = True,
) -> list[dict[str, object]] | None:
    if raw_focus_points:
        try:
            parsed = json.loads(raw_focus_points)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--focus_points must be valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("--focus_points must decode to a list of objects")
        return [dict(point) for point in parsed]
    if not allow_config_default:
        return None
    return load_focus_points(case_kind=case_kind, module_name=config_module)


def build_orchestrator_config(args: argparse.Namespace) -> OrchestratorConfig:
    return OrchestratorConfig(
        fixture=getattr(args, "fixture", None),
        output=args.output,
        params=parse_param_assignments(getattr(args, "params", [])),
        case_id=getattr(args, "case_id", None),
        case_kind=getattr(args, "case_kind", "uturn"),
        run_mode=getattr(args, "mode", "explore"),
        target=getattr(args, "target", "awsim"),
        worker_id=getattr(args, "worker_id", "worker_v2_local"),
        reason=getattr(args, "reason", "manual_orchestrator_run"),
        tags=list(getattr(args, "tags", [])),
        config_module=getattr(args, "config_module", None)
        or f"targets.awsim.case_kinds.{getattr(args, 'case_kind', 'uturn')}",
        focus_points=(
            [dict(point) for point in getattr(args, "focus_points", [])]
            if getattr(args, "focus_points", None)
            else None
        ),
        path_root=getattr(args, "path_root", None),
        dataset_csv=getattr(args, "dataset_csv", None),
        history_path=getattr(args, "history_path", None),
        ext_mode=getattr(args, "ext_mode", "cvm"),
        queue_actor_name=getattr(args, "queue_actor_name", "local_task_queue"),
        queue_namespace=getattr(args, "queue_namespace", None),
        queue_address=getattr(args, "queue_address", None),
        shared_store_actor_name=getattr(args, "shared_store_actor_name", None),
        shared_store_namespace=getattr(args, "shared_store_namespace", None),
        shared_store_address=getattr(args, "shared_store_address", None),
        dkw_bounds=getattr(args, "dkw_bounds", None),
        dkw_region=getattr(args, "dkw_region", "custom"),
        dkw_pure_smc=getattr(args, "dkw_pure_smc", False),
        dkw_simultaneous=getattr(args, "dkw_simultaneous", False),
        refresh_interval=getattr(args, "refresh_interval", None),
        max_strategy_cases=getattr(args, "max_strategy_cases", None),
        max_samples=getattr(args, "max_samples", None),
        resume_from=getattr(args, "resume_from", None),
        resume_current_only=getattr(args, "resume_current_only", False),
        scenario_type=getattr(args, "scenario_type", None),
        simulation_output_dir=getattr(args, "simulation_output_dir", None),
        expected_trace_path=getattr(args, "expected_trace_path", None),
        local_loop_num=getattr(args, "local_loop_num", None),
        headless=getattr(args, "headless", False),
        with_host_worker=getattr(args, "with_host_worker", False),
        headless_host=getattr(args, "headless_host", False),
        worker_count=getattr(args, "worker_count", None),
        cache_size=getattr(args, "cache_size", None),
        queue_high_water=getattr(args, "queue_high_water", None),
        queue_low_water=getattr(args, "queue_low_water", None),
        run_inline_worker=not getattr(args, "run_external_workers", False),
        poll_interval_sec=getattr(args, "poll_interval_sec", 2.0),
        binomial_target=getattr(args, "binomial_target", "c_collision"),
        binomial_method=getattr(args, "binomial_method", "wilson"),
        binomial_confidence=getattr(args, "binomial_confidence", 0.95),
        binomial_target_width=getattr(args, "binomial_target_width", 0.02),
        binomial_min_samples=getattr(args, "binomial_min_samples", None),
    )


def run_orchestrator(
    argv: Sequence[str] | None = None,
    *,
    queue: TaskQueue | None = None,
) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    args = build_parser().parse_args(argv_list)
    if getattr(args, "cluster", False):
        from apps.cli.orchestrator_cluster_main import run_cluster_orchestrator

        return int(run_cluster_orchestrator(argv_list))
    validate_args(args)
    args = normalize_args(args, argv=argv_list)
    orchestrator = Orchestrator(queue=queue)
    summary = orchestrator.run(build_orchestrator_config(args))
    for worker_summary in summary.get("worker_summaries", []):
        print(json.dumps(worker_summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))
    return int(summary["worker_exit_code"])


def main(argv: Sequence[str] | None = None) -> int:
    return run_orchestrator(argv)


if __name__ == "__main__":
    raise SystemExit(main())
