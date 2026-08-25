from __future__ import annotations

import json
import sys
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
from runtime.cluster.task_queue_gateway import TaskQueueGateway


def _render_progress(snapshot: dict[str, object], config: OrchestratorConfig) -> None:
    worker_statuses = dict(snapshot.get("worker_statuses", {}))
    queue_size = int(snapshot.get("queue_size", 0))
    completed = int(snapshot.get("completed_count", 0))
    worker_status_text = " | ".join(
        f"[{worker_id}] {status}"
        for worker_id, status in sorted(worker_statuses.items())
    )
    if config.run_mode in {"dkw", "verify_consistency", "binomial_ci"}:
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

    queue_actor_name = (
        args.queue_actor_name
        if getattr(args, "queue_actor_name", "local_task_queue") != "local_task_queue"
        else "TaskQueueActor"
    )
    queue_namespace = getattr(args, "queue_namespace", None) or "awsim_cluster"
    shared_store_actor_name = getattr(args, "shared_store_actor_name", None)
    if shared_store_actor_name is None and getattr(args, "dataset_csv", None):
        shared_store_actor_name = "SharedStoreActor"

    cluster_manager = ClusterManager()
    cluster_summary = cluster_manager.start_cluster(
        ClusterLaunchConfig(
            case_kind=args.case_kind,
            run_mode=args.mode,
            output_path=args.output,
            queue_actor_name=queue_actor_name,
            queue_namespace=queue_namespace,
            queue_address=getattr(args, "queue_address", None),
            config_module=getattr(args, "config_module", None),
            dataset_csv=getattr(args, "dataset_csv", None),
            ext_mode=getattr(args, "ext_mode", "cvm"),
            headless=getattr(args, "headless", False),
            with_host_worker=getattr(args, "with_host_worker", False),
            refresh_interval=getattr(args, "refresh_interval", 10),
            shared_store_actor_name=shared_store_actor_name,
            shared_store_namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
            shared_store_address=getattr(args, "shared_store_address", None),
        )
    )

    actor_runtime = ActorRuntime()
    ray_module = actor_runtime.connect(
        DetachedActorConfig(
            name=queue_actor_name,
            namespace=queue_namespace,
            address=cluster_summary.head_address,
        )
    )
    task_queue_actor = actor_runtime.ensure_task_queue_actor(
        DetachedActorConfig(
            name=queue_actor_name,
            namespace=queue_namespace,
            address=cluster_summary.head_address,
        )
    )
    if shared_store_actor_name and getattr(args, "dataset_csv", None):
        actor_runtime.ensure_shared_store_actor(
            DetachedActorConfig(
                name=shared_store_actor_name,
                namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
                address=getattr(args, "shared_store_address", None) or cluster_summary.head_address,
            ),
            dataset_csv_path=args.dataset_csv,
        )

    base_config = build_orchestrator_config(args)
    worker_count = max(
        int(getattr(args, "worker_count", 0) or 0),
        len(cluster_summary.launched_workers) + (1 if getattr(args, "with_host_worker", False) else 0),
        1,
    )
    config = replace(
        base_config,
        queue_actor_name=queue_actor_name,
        queue_namespace=queue_namespace,
        queue_address=cluster_summary.head_address,
        shared_store_actor_name=shared_store_actor_name,
        shared_store_namespace=getattr(args, "shared_store_namespace", None) or queue_namespace,
        shared_store_address=getattr(args, "shared_store_address", None) or cluster_summary.head_address,
        worker_count=worker_count,
        run_inline_worker=False,
    )

    orchestrator = Orchestrator(
        queue_gateway=TaskQueueGateway(actor=task_queue_actor, ray_get=ray_module.get),
        host_worker_manager=HostWorkerManager(),
        progress_callback=_render_progress,
    )
    try:
        summary = orchestrator.run(config)
    finally:
        print()
    print(json.dumps(summary, ensure_ascii=False))
    return int(summary["worker_exit_code"])


def main(argv: Sequence[str] | None = None) -> int:
    return run_cluster_orchestrator(argv)


if __name__ == "__main__":
    raise SystemExit(main())
