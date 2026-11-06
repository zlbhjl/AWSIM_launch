#!/usr/bin/env python3
"""Run a fixed-P/fixed-Q importance-sampling experiment for any target.

The target comes from the sampling plan (``target:``). The shared runner does
the experiment (orchestration/importance/runner.py); the target's plugin only
adds its own options and builds its cases (contracts/importance_sampling_plugin.py).

    python3 -m apps.cli.importance_main --sampling-plan PLAN --output-root DIR --max-samples N [options]

``--help`` together with ``--sampling-plan`` also lists the target's options.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from contracts.importance_sampling_plugin import ImportanceSamplingHooks
from orchestration.importance.runner import (
    STOP_MODES,
    ImportanceRunConfig,
    ImportanceSamplingRunner,
    hooks_for_target,
)
from orchestration.sampling_plan import load_sampling_plan
from orchestration.seeding import PAIRINGS, PHASES


def build_parser(hooks: ImportanceSamplingHooks | None = None, *, target: str | None = None) -> argparse.ArgumentParser:
    description = "Run a fixed-P/fixed-Q importance-sampling experiment"
    parser = argparse.ArgumentParser(description=description + (f" (target={target})" if target else ""))
    parser.add_argument("--sampling-plan", required=True, help="sampling plan YAML; its target selects the plugin")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=None, help="samples per run kind (ordinary and importance)")
    parser.add_argument("--sampling-seed", type=int, default=None, help="legacy seed policy: the outer seed")
    parser.add_argument("--metric", default=None, help="binary metric; default: the target's")
    parser.add_argument("--confidence", type=float, default=0.95)
    streams = parser.add_argument_group("random streams (seed policy 'streams')")
    streams.add_argument(
        "--phase", choices=sorted(PHASES), default="final",
        help="random-stream phase; pilot runs never share streams with final runs",
    )
    streams.add_argument(
        "--root-entropy", type=int, default=None,
        help="128-bit root of all random streams; default: reuse the manifest's, or draw a new one",
    )
    streams.add_argument(
        "--pairing", choices=list(PAIRINGS), default="independent",
        help="'paired' uses the same streams for the ordinary and importance runs",
    )
    streams.add_argument(
        "--retry-failed", action="store_true",
        help="retry samples whose latest attempt failed, with the same inputs and streams",
    )
    stopping = parser.add_argument_group("stopping")
    stopping.add_argument("--stop-mode", choices=STOP_MODES, default="fixed_samples")
    stopping.add_argument("--target-width", type=float, default=None)
    stopping.add_argument("--null-upper", type=float, default=None)
    stopping.add_argument("--alternative-lower", type=float, default=None)
    cluster = parser.add_argument_group("cluster (docs/cluster_importance_sampling_plan.md)")
    cluster.add_argument("--cluster", action="store_true",
                         help="run the cases on the cluster's workers (Ray queue) instead of here")
    cluster.add_argument("--case-timeout", type=float, default=None,
                         help="seconds after a worker took a case before it is submitted again "
                              "(default: the target's, e.g. AWSIM's scenario timeout plus its start-up and grace times)")
    cluster.add_argument("--max-attempts", type=int, default=3,
                         help="attempts per sample in total: a failed or lost sample is run again with the same inputs")
    cluster.add_argument("--max-in-flight", type=int, default=None,
                         help="cases queued or running at once; default: the number of launched workers")
    cluster.add_argument("--cluster-poll-interval", type=float, default=2.0,
                         help="seconds between checks for finished cases")
    cluster.add_argument("--auto-restart-stale-workers", action="store_true")
    cluster.add_argument("--auto-restart-missing-workers", action="store_true")
    if hooks is not None:
        add_arguments = getattr(hooks, "add_arguments", None)
        if callable(add_arguments):
            add_arguments(parser)
    return parser


def support_for_target(target: str) -> ImportanceSamplingHooks:
    return hooks_for_target(target)


def run_with_hooks(hooks: ImportanceSamplingHooks, args: argparse.Namespace) -> dict[str, object]:
    alternative = getattr(hooks, "alternative_run", None)
    if callable(alternative):
        result = alternative(args)
        if result is not None:
            return result
    if args.max_samples is None:
        raise ValueError("--max-samples is required")
    if args.stop_mode == "importance_width" and args.target_width is None:
        raise ValueError("--target-width is required when --stop-mode=importance_width")
    run_options = getattr(hooks, "run_options", None)
    target_values = dict(run_options(args)) if callable(run_options) else {}
    config = ImportanceRunConfig(
        sampling_plan_path=args.sampling_plan,
        output_root=args.output_root,
        max_samples=args.max_samples,
        metric=args.metric,
        confidence=args.confidence,
        seed=args.sampling_seed,
        phase=args.phase,
        root_entropy=args.root_entropy,
        pairing=args.pairing,
        retry_failed=args.retry_failed,
        max_attempts=args.max_attempts,
        stop_mode=args.stop_mode,
        target_width=args.target_width,
        sequential_options={
            key: value
            for key, value in {"null_upper": args.null_upper, "alternative_lower": args.alternative_lower}.items()
            if value is not None
        },
        params=target_values.get("params", {}),
        options=target_values.get("options", {}),
    )
    if getattr(args, "cluster", False):
        return run_on_cluster(hooks, args, config)
    components = hooks.build_components(args)
    try:
        runner = ImportanceSamplingRunner(
            backend=components.backend, result_interpreter=components.result_interpreter, hooks=hooks,
        )
        return runner.run(config)
    finally:
        close = getattr(components.backend, "close", None)
        if callable(close):
            close()


def _default_case_timeout(target: str, launch_options: dict[str, object]) -> float:
    from targets.registry import get_target_plugin

    hook = getattr(get_target_plugin(target), "default_case_timeout_sec", None)
    if not callable(hook):
        raise ValueError(f"--case-timeout is required: target {target!r} has no default case timeout")
    return float(hook(str(launch_options.get("case_kind", target))))


def run_on_cluster(hooks: ImportanceSamplingHooks, args: argparse.Namespace, config: ImportanceRunConfig) -> dict[str, object]:
    """Start the cluster's workers for the plan's target and run the experiment on them."""
    import re
    import time

    from apps.cli.orchestrator_cluster_main import build_worker_restarter, start_cluster
    from orchestration.importance.cluster_executor import ClusterExecutor, JsonlRecordFeed
    from runtime.cluster.cluster_manager import ClusterLaunchConfig, ClusterManager
    from runtime.cluster.task_queue_gateway import TaskQueueGateway
    from runtime.container.profile import resolve_container_launch_profile

    launch_options = getattr(hooks, "cluster_launch_options", None)
    if not callable(launch_options):
        raise ValueError("this target cannot run importance sampling on the cluster (no cluster_launch_options)")
    plan = load_sampling_plan(args.sampling_plan)
    options = dict(launch_options(args))
    profile = resolve_container_launch_profile(options.get("container_profile"))
    if profile is None or profile.target != plan.target:
        raise ValueError(f"--cluster needs a container profile of target={plan.target}")
    # Fresh actors per run: detached actors are reused by name and would carry
    # another run's queue or output files.
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{plan.experiment_id}-{time.strftime('%Y%m%d%H%M%S')}")
    cluster_dir = Path(args.output_root).resolve() / "cluster" / tag
    cluster_dir.mkdir(parents=True, exist_ok=True)
    records_path = cluster_dir / "records.jsonl"
    queue_name, store_name = f"IS_TaskQueue_{tag}", f"IS_SharedStore_{tag}"
    launch_config = ClusterLaunchConfig(
        run_mode="explore",
        output_path=str(records_path),
        queue_actor_name=queue_name,
        target=plan.target,
        dataset_csv=str(cluster_dir / "dataset.csv"),
        shared_store_actor_name=store_name,
        **options,
    )
    manager = ClusterManager()
    session = start_cluster(
        manager, launch_config,
        shared_store_actor_name=store_name, shared_store_namespace=launch_config.shared_store_namespace,
        shared_store_address=None, dataset_csv_path=launch_config.dataset_csv, records_jsonl_path=str(records_path),
    )
    gateway = TaskQueueGateway(actor=session.queue_actor, ray_get=getattr(session.ray_module, "get", None))
    launched = list(getattr(session.cluster_summary, "launched_workers", ()))
    executor = ClusterExecutor(
        queue=gateway,
        feed=JsonlRecordFeed(records_path),
        side_root=cluster_dir,
        max_in_flight=args.max_in_flight or max(1, len(launched)),
        case_timeout_sec=args.case_timeout if args.case_timeout is not None else _default_case_timeout(plan.target, options),
        max_attempts=args.max_attempts,
        poll_interval_sec=args.cluster_poll_interval,
        log=lambda message: print(message, file=sys.stderr, flush=True),
        maintenance=build_worker_restarter(args, manager, launch_config, session.head_address,
                                           gpu_recovery_enabled=False),
        path_root=Path(__file__).resolve().parents[2],
    )
    try:
        result = ImportanceSamplingRunner(hooks=hooks, executor=executor).run(config)
    finally:
        gateway.set_stop_signal("importance sampling finished")
    result["cluster"] = {
        "launched_workers": launched,
        "skipped_workers": list(getattr(session.cluster_summary, "skipped_workers", ())),
        "queue_actor": queue_name,
        "records_jsonl": str(records_path),
    }
    return result


def run_for_target(target: str, argv: Sequence[str] | None = None) -> int:
    """Parse the options of ``target`` and run its experiment."""
    hooks = support_for_target(target)
    parser = build_parser(hooks, target=target)
    args = parser.parse_args(argv)
    try:
        result = run_with_hooks(hooks, args)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--sampling-plan")
    known, _ = pre.parse_known_args(arguments)
    if known.sampling_plan is None:
        build_parser().parse_args(arguments)  # prints help or the missing-option error
        return 2
    plan = load_sampling_plan(known.sampling_plan)
    try:
        support_for_target(plan.target)
    except ValueError as exc:
        build_parser().error(str(exc))
    return run_for_target(plan.target, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
