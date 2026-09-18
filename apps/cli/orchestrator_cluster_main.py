from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from typing import Sequence

from apps.cli.orchestrator_main import (
    build_orchestrator_config,
    build_parser,
    normalize_args,
    validate_args,
)
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from runtime.cluster.actor_runtime import ActorRuntime, DetachedActorConfig
from runtime.cluster.cluster_manager import ClusterLaunchConfig, ClusterManager
from runtime.cluster.host_worker import HostWorkerManager
from runtime.cluster.ray_client import is_ray_control_plane_error
from runtime.cluster.task_queue_gateway import TaskQueueGateway
from runtime.cluster.worker_watchdog import StaleWorkerRestarter, WorkerRestartPolicy
from runtime.container.profile import resolve_container_launch_profile


STALE_WORKER_STATUS_SEC = 600.0


def _format_worker_status(
    worker_id: str,
    status: str,
    *,
    updated_at: object | None,
    now: float,
) -> str:
    if isinstance(updated_at, (int, float)):
        age_sec = max(now - float(updated_at), 0.0)
        if age_sec >= STALE_WORKER_STATUS_SEC:
            return f"[{worker_id}] {status} stale({int(age_sec)}s) restart_candidate"
    return f"[{worker_id}] {status}"


def _render_progress(snapshot: dict[str, object], config: OrchestratorConfig) -> None:
    worker_statuses = dict(snapshot.get("worker_statuses", {}))
    worker_status_updated_at = dict(snapshot.get("worker_status_updated_at", {}))
    queue_size = int(snapshot.get("queue_size", 0))
    completed = int(snapshot.get("completed_count", 0))
    now = time.time()
    worker_status_text = " | ".join(
        _format_worker_status(
            str(worker_id),
            str(status),
            updated_at=worker_status_updated_at.get(worker_id),
            now=now,
        )
        for worker_id, status in sorted(worker_statuses.items())
    )
    if config.run_mode in {"dkw", "verify_consistency", "binomial_ci", "replay", "sprt", "ebstop"}:
        line = f"[Orchestrator] 進行状況 (総ループ: {completed}) | キュー={queue_size}"
    elif config.run_mode == "boundary_gap":
        line = f"[Orchestrator] boundary_gap 継続中 | 完了={completed} | キュー={queue_size}"
    else:
        line = f"[Orchestrator] 完了={completed} | キュー={queue_size}"
    if worker_status_text:
        line = f"{line} || {worker_status_text}"
    sys.stdout.write(f"\r\033[K{line}")
    sys.stdout.flush()


def run_cluster_orchestrator(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv_list)
    validate_args(args)
    args = normalize_args(args, argv=argv_list)

    launch_profile = resolve_container_launch_profile(
        getattr(args, "container_profile", None)
    )
    target = getattr(args, "target", "awsim")
    if launch_profile is not None and launch_profile.target != target:
        raise ValueError(
            f"--container-profile {launch_profile.name} is for target="
            f"{launch_profile.target}, not target={target}"
        )
    if target == "prism" and launch_profile is None:
        raise ValueError(
            "Cluster target=prism requires --container-profile prism_maude"
        )
    requires_gpu = launch_profile.requires_gpu if launch_profile is not None else True
    gpu_recovery_enabled = bool(
        getattr(args, "auto_restart_gpu_workers", False)
    ) and requires_gpu

    queue_actor_name = (
        args.queue_actor_name
        if getattr(args, "queue_actor_name", "local_task_queue") != "local_task_queue"
        else "TaskQueueActor"
    )
    queue_namespace = getattr(args, "queue_namespace", None) or "awsim_cluster"
    shared_store_actor_name = getattr(args, "shared_store_actor_name", None)
    if shared_store_actor_name is None and getattr(args, "dataset_csv", None):
        shared_store_actor_name = "SharedStoreActor"

    base_config = build_orchestrator_config(args)
    if base_config.replay_csv is not None:
        Orchestrator().validate_replay_source(base_config)

    cluster_manager = ClusterManager()
    cluster_launch_config = ClusterLaunchConfig(
        case_kind=args.case_kind,
        run_mode=args.mode,
        output_path=args.output,
        queue_actor_name=queue_actor_name,
        target=target,
        run_id=getattr(args, "run_id", None),
        queue_namespace=queue_namespace,
        queue_address=getattr(args, "queue_address", None),
        config_module=getattr(args, "config_module", None),
        container_profile=getattr(args, "container_profile", None),
        scenario_profile=getattr(args, "scenario_profile", None),
        dataset_csv=getattr(args, "dataset_csv", None),
        ext_mode=getattr(args, "ext_mode", "cvm"),
        headless=getattr(args, "headless", False),
        with_host_worker=getattr(args, "with_host_worker", False),
        refresh_interval=getattr(args, "refresh_interval", 10),
        shared_store_actor_name=shared_store_actor_name,
        shared_store_namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
        shared_store_address=getattr(args, "shared_store_address", None),
        sync_awsim_launch=getattr(args, "sync_awsim_launch", True),
        sync_awsim_script_py=getattr(args, "sync_awsim_script_py", False),
        sync_aw_runtime_monitor=getattr(args, "sync_aw_runtime_monitor", True),
        sync_autoware180_map=getattr(args, "sync_autoware180_map", False),
        worker_launch_stagger_sec=getattr(args, "worker_launch_stagger_sec", 20.0),
        worker_queue_connect_retries=getattr(args, "worker_queue_connect_retries", 6),
        worker_queue_connect_retry_interval_sec=getattr(
            args,
            "worker_queue_connect_retry_interval_sec",
            15.0,
        ),
        worker_queue_empty_wait_timeout_sec=getattr(
            args,
            "worker_queue_empty_wait_timeout_sec",
            300.0,
        ),
        worker_queue_empty_wait_interval_sec=getattr(
            args,
            "worker_queue_empty_wait_interval_sec",
            5.0,
        ),
        worker_queue_heartbeat_interval_sec=getattr(
            args,
            "worker_queue_heartbeat_interval_sec",
            60.0,
        ),
        worker_gpu_health_check=gpu_recovery_enabled,
        worker_gpu_health_timeout_sec=float(
            getattr(args, "gpu_worker_probe_timeout_sec", 5.0)
        ),
    )
    head_address = cluster_manager.start_head(cluster_launch_config)

    actor_runtime = ActorRuntime()
    ray_module = actor_runtime.connect(
        DetachedActorConfig(
            name=queue_actor_name,
            namespace=queue_namespace,
            address=head_address,
        )
    )
    task_queue_actor = actor_runtime.ensure_task_queue_actor(
        DetachedActorConfig(
            name=queue_actor_name,
            namespace=queue_namespace,
            address=head_address,
        )
    )
    if shared_store_actor_name and getattr(args, "dataset_csv", None):
        actor_runtime.ensure_shared_store_actor(
            DetachedActorConfig(
                name=shared_store_actor_name,
                namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
                address=getattr(args, "shared_store_address", None) or head_address,
            ),
            dataset_csv_path=args.dataset_csv,
            records_jsonl_path=args.output,
        )

    cluster_summary = cluster_manager.launch_workers(
        cluster_launch_config,
        head_address=head_address,
    )

    worker_count = max(
        int(getattr(args, "worker_count", 0) or 0),
        len(cluster_summary.launched_workers) + (1 if getattr(args, "with_host_worker", False) else 0),
        1,
    )
    config = replace(
        base_config,
        queue_actor_name=queue_actor_name,
        queue_namespace=queue_namespace,
        queue_address=head_address,
        shared_store_actor_name=shared_store_actor_name,
        shared_store_namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
        shared_store_address=getattr(args, "shared_store_address", None) or head_address,
        worker_count=worker_count,
        run_inline_worker=False,
    )
    maintenance_callback = None
    if any(
        (
            bool(getattr(args, "auto_restart_stale_workers", False)),
            bool(getattr(args, "auto_restart_missing_workers", False)),
            gpu_recovery_enabled,
        )
    ):
        worker_restarter = StaleWorkerRestarter(
            cluster_manager=cluster_manager,
            cluster_config=cluster_launch_config,
            head_address=head_address,
            policy=WorkerRestartPolicy(
                enabled=bool(getattr(args, "auto_restart_stale_workers", False)),
                restart_missing_workers=bool(
                    getattr(args, "auto_restart_missing_workers", False)
                ),
                restart_gpu_workers=gpu_recovery_enabled,
                stale_timeout_sec=float(getattr(args, "stale_worker_timeout_sec", 600.0)),
                check_interval_loops=int(
                    getattr(args, "stale_worker_check_interval_loops", 5)
                ),
                restart_cooldown_sec=float(
                    getattr(args, "stale_worker_restart_cooldown_sec", 600.0)
                ),
                max_restart_count=int(getattr(args, "worker_restart_max_count", 2)),
                log_tail_lines=int(getattr(args, "worker_restart_log_tail_lines", 100)),
                gpu_max_restarts_per_24h=int(
                    getattr(args, "gpu_worker_max_restarts_per_24h", 5)
                ),
                gpu_max_consecutive_failures=int(
                    getattr(args, "gpu_worker_max_consecutive_failures", 3)
                ),
                gpu_stable_reset_sec=float(
                    getattr(args, "gpu_worker_stable_reset_sec", 1800.0)
                ),
                gpu_probe_timeout_sec=float(
                    getattr(args, "gpu_worker_probe_timeout_sec", 5.0)
                ),
            ),
        )
        maintenance_callback = worker_restarter.check

    orchestrator_kwargs = {
        "queue_gateway": TaskQueueGateway(actor=task_queue_actor, ray_get=ray_module.get),
        "host_worker_manager": HostWorkerManager(),
        "progress_callback": _render_progress,
    }
    if maintenance_callback is not None:
        orchestrator_kwargs["maintenance_callback"] = maintenance_callback
    orchestrator = Orchestrator(**orchestrator_kwargs)
    try:
        try:
            summary = orchestrator.run(config)
        except Exception as exc:
            if not is_ray_control_plane_error(exc):
                raise
            summary = {
                "mode": "cluster_queue",
                "enqueued": 0,
                "worker_exit_code": 1,
                "queue_size": None,
                "completed_count": None,
                "worker_statuses": {},
                "worker_summaries": [],
                "output_path": config.output,
                "resume_from": config.resume_from,
                "restored_base_csv": None,
                "last_loop_num": None,
                "next_loop_num": None,
                "stop_reason": "run_failed_ray_gcs_lost",
                "interrupted": False,
                "maintenance_events": [],
                "final_report": None,
                "reason": str(exc),
            }
    finally:
        print()
    summary["cluster_launched_workers"] = list(cluster_summary.launched_workers)
    summary["cluster_skipped_workers"] = list(cluster_summary.skipped_workers)
    summary["cluster_preflight_failures"] = list(
        getattr(cluster_summary, "preflight_failures", ())
    )
    print(json.dumps(summary, ensure_ascii=False, default=_json_default))
    return int(summary["worker_exit_code"])


def main(argv: Sequence[str] | None = None) -> int:
    return run_cluster_orchestrator(argv)


def _json_default(value):
    """Convert scalar values returned by NumPy/pandas without losing numbers."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
