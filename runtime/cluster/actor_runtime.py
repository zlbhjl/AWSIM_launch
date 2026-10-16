from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.cluster.ray_client import RayActorLocator, RayConnectionConfig
from runtime.cluster.ray_queue import TaskQueue
from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.parameter_buffer import ParameterBuffer
from runtime.repository.shared_store import SharedStore


class _TaskQueueActorBackend:
    def __init__(self) -> None:
        self.queue = TaskQueue()

    def update_worker_status(self, worker_id: str, status: str) -> None:
        self.queue.update_worker_status(worker_id, status)

    def add_task(self, task: dict[str, Any]) -> None:
        self.queue.add_task(task)

    def get_next_task(self) -> dict[str, Any] | None:
        return self.queue.get_next_task()

    def set_start_counts(self, count: int) -> None:
        self.queue.set_start_counts(count)

    def report_completion(self, loop_num: int, status: str) -> bool:
        return self.queue.report_completion(loop_num, status)

    def get_status(self) -> tuple[int, int, dict[str, str]]:
        return self.queue.get_status()

    def get_in_flight_tasks(self) -> list[dict[str, Any]]:
        return self.queue.get_in_flight_tasks()

    def get_snapshot(self) -> dict[str, Any]:
        return self.queue.get_snapshot()

    def set_stop_signal(self, reason: str = "Target Reached or Master Stopped") -> None:
        self.queue.set_stop_signal(reason)


class _SharedStoreActorBackend:
    def __init__(
        self,
        dataset_csv_path: str,
        *,
        records_jsonl_path: str | None = None,
        buffer_timeout_sec: int = 600,
    ) -> None:
        repository = DatasetCsvRepository(Path(dataset_csv_path).expanduser())
        self.shared_store = SharedStore(
            repository,
            parameter_buffer=ParameterBuffer(timeout_sec=buffer_timeout_sec),
        )
        self.dataset_csv_path = str(Path(dataset_csv_path).expanduser())
        self.records_jsonl_path = (
            Path(records_jsonl_path).expanduser().resolve()
            if records_jsonl_path is not None
            else None
        )

    def buffer_parameters(
        self,
        loop_num: int,
        input_row: dict[str, object],
        reason: str = "",
    ) -> None:
        self.shared_store.buffer_parameters(loop_num, input_row, reason=reason)

    def merge_result(self, result_row: dict[str, object]) -> dict[str, object] | None:
        return self.shared_store.merge_result(result_row)

    def flush_timeout(
        self,
        loop_num: int,
        timeout_row: dict[str, object],
        input_row: dict[str, object] | None = None,
        reason: str = "",
    ) -> dict[str, object]:
        return self.shared_store.flush_timeout(
            loop_num,
            timeout_row,
            input_row=input_row,
            reason=reason,
        )

    def log_parameters(
        self,
        _output_dir: str,
        _file_name: str,
        loop_num: int,
        params_dict: dict[str, object],
        reason: str = "",
    ) -> None:
        self.buffer_parameters(loop_num, params_dict, reason=reason)

    def log_and_merge_result(
        self,
        _scenario_name: str,
        result_row: dict[str, object],
    ) -> dict[str, object] | None:
        return self.merge_result(result_row)

    def flush_timeout_task(
        self,
        _scenario_name: str,
        loop_num: int,
        params_dict: dict[str, object],
        reason: str,
        result_headers: list[str],
    ) -> dict[str, object]:
        timeout_row: dict[str, object] = {"loop_num": loop_num}
        for header in result_headers:
            if header == "loop_num":
                continue
            timeout_row[header] = -1
        timeout_row.setdefault("status", "timeout")
        return self.flush_timeout(
            loop_num,
            timeout_row,
            input_row=params_dict,
            reason=reason,
        )

    def get_dataset_csv_path(self) -> str:
        return self.dataset_csv_path

    def append_evaluation_record(
        self,
        record_payload: dict[str, object],
    ) -> bool:
        if self.records_jsonl_path is None:
            return False
        self.records_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.records_jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record_payload, ensure_ascii=False) + "\n")
        return True

    def get_records_jsonl_path(self) -> str | None:
        if self.records_jsonl_path is None:
            return None
        return str(self.records_jsonl_path)


@dataclass(frozen=True)
class DetachedActorConfig:
    name: str
    namespace: str | None = "awsim_cluster"
    address: str | None = None
    lifetime: str = "detached"
    num_cpus: int = 0
    pin_to_current_node: bool = True


class ActorRuntime:
    def __init__(
        self,
        *,
        actor_locator: RayActorLocator | None = None,
        ray_module: Any | None = None,
    ) -> None:
        self.actor_locator = actor_locator or RayActorLocator(ray_module=ray_module)
        self.ray_module = ray_module

    def connect(self, config: DetachedActorConfig) -> Any:
        resolved_ray = self.actor_locator.connect(
            RayConnectionConfig(address=config.address, namespace=config.namespace)
        )
        self.ray_module = resolved_ray
        return resolved_ray

    def ensure_task_queue_actor(self, config: DetachedActorConfig) -> Any:
        return self._ensure_named_actor(config, _TaskQueueActorBackend)

    def ensure_shared_store_actor(
        self,
        config: DetachedActorConfig,
        *,
        dataset_csv_path: str,
        records_jsonl_path: str | None = None,
        buffer_timeout_sec: int = 600,
    ) -> Any:
        return self._ensure_named_actor(
            config,
            _SharedStoreActorBackend,
            dataset_csv_path,
            records_jsonl_path=records_jsonl_path,
            buffer_timeout_sec=buffer_timeout_sec,
        )

    def get_actor(self, config: DetachedActorConfig) -> Any:
        resolved_ray = self.connect(config)
        return resolved_ray.get_actor(config.name, namespace=config.namespace)

    def stop_actor(self, config: DetachedActorConfig) -> bool:
        resolved_ray = self.connect(config)
        try:
            actor = resolved_ray.get_actor(config.name, namespace=config.namespace)
        except Exception:
            return False
        kill = getattr(resolved_ray, "kill", None)
        if callable(kill):
            kill(actor, no_restart=True)
            return True
        return False

    def _ensure_named_actor(
        self,
        config: DetachedActorConfig,
        backend_class: type[object],
        *args: object,
        **kwargs: object,
    ) -> Any:
        resolved_ray = self.connect(config)
        try:
            return resolved_ray.get_actor(config.name, namespace=config.namespace)
        except Exception:
            pass

        remote_class = resolved_ray.remote(backend_class)
        options: dict[str, object] = {
            "name": config.name,
            "lifetime": config.lifetime,
            "num_cpus": config.num_cpus,
        }
        scheduling_strategy = self._build_node_affinity_strategy(resolved_ray, config)
        if scheduling_strategy is not None:
            options["scheduling_strategy"] = scheduling_strategy
        return remote_class.options(**options).remote(*args, **kwargs)

    @staticmethod
    def _build_node_affinity_strategy(
        ray_module: Any,
        config: DetachedActorConfig,
    ) -> object | None:
        if not config.pin_to_current_node:
            return None
        runtime_context = getattr(ray_module, "get_runtime_context", None)
        util_module = getattr(ray_module, "util", None)
        if not callable(runtime_context) or util_module is None:
            return None
        strategies = getattr(util_module, "scheduling_strategies", None)
        if strategies is None:
            return None
        strategy_class = getattr(strategies, "NodeAffinitySchedulingStrategy", None)
        if strategy_class is None:
            return None
        return strategy_class(
            node_id=runtime_context().get_node_id(),
            soft=False,
        )


__all__ = [
    "ActorRuntime",
    "DetachedActorConfig",
]
