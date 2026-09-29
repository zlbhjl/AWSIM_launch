from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from contracts.execution import TestCase


@dataclass(frozen=True)
class TaskQueueGatewayConfig:
    target: str = "awsim"
    case_kind: str = "uturn"
    case_id_prefix: str = "queue_case"
    source_module: str = "runtime.cluster.task_queue_gateway"


class TaskQueueGateway:
    def __init__(
        self,
        *,
        actor: Any,
        config: TaskQueueGatewayConfig | None = None,
        ray_get: Callable[[Any], Any] | None = None,
    ):
        self.actor = actor
        self.config = config or TaskQueueGatewayConfig()
        self.ray_get = ray_get
        self.last_stop_reason = ""

    @classmethod
    def from_actor_name(
        cls,
        actor_name: str,
        *,
        namespace: str | None = None,
        config: TaskQueueGatewayConfig | None = None,
        ray_module: Any | None = None,
    ) -> "TaskQueueGateway":
        resolved_ray = ray_module
        if resolved_ray is None:
            import ray as resolved_ray  # type: ignore

        actor = resolved_ray.get_actor(actor_name, namespace=namespace)
        return cls(actor=actor, config=config, ray_get=resolved_ray.get)

    def fetch_next(self) -> TestCase | None:
        payload = self._invoke("get_next_task")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise TypeError("task payload must be a dict")

        if payload.get("system_command") == "stop":
            self.last_stop_reason = str(payload.get("reason", ""))
            raise StopIteration(self.last_stop_reason or "task queue stop")

        return self._build_test_case(payload)

    def update_worker_status(self, worker_id: str, status: str) -> Any:
        return self._invoke(
            "update_worker_status",
            worker_id,
            status,
            worker_id=worker_id,
            status=status,
        )

    def report_completion(self, loop_num: int, status: str) -> Any:
        return self._invoke(
            "report_completion",
            loop_num,
            status,
            loop_num=loop_num,
            status=status,
        )

    def add_task(self, payload: dict[str, Any]) -> Any:
        return self._invoke("add_task", payload, task=payload)

    def get_status(self) -> tuple[int, int, dict[str, str]]:
        return self._invoke("get_status")

    def get_snapshot(self) -> dict[str, Any]:
        if hasattr(self.actor, "get_snapshot"):
            return self._invoke("get_snapshot")
        queue_size, completed_count, worker_statuses = self.get_status()
        return {
            "queue_size": queue_size,
            "completed_count": completed_count,
            "dispatched_count": completed_count + queue_size,
            "in_flight_count": 0,
            "in_flight_tasks": [],
            "worker_statuses": worker_statuses,
            "stop_signal": False,
            "stop_reason": "",
        }

    def get_in_flight_tasks(self) -> list[dict[str, Any]]:
        if hasattr(self.actor, "get_in_flight_tasks"):
            return self._invoke("get_in_flight_tasks")
        return list(self.get_snapshot().get("in_flight_tasks", []))

    def set_start_counts(self, count: int) -> Any:
        return self._invoke("set_start_counts", count, count=count)

    def set_stop_signal(self, reason: str = "Target Reached or Master Stopped") -> Any:
        return self._invoke("set_stop_signal", reason, reason=reason)

    def cancel_pending_tasks(self) -> int:
        if not hasattr(self.actor, "cancel_pending_tasks"):
            return 0
        return self._invoke("cancel_pending_tasks")

    def _build_test_case(self, payload: dict[str, Any]) -> TestCase:
        task = dict(payload)
        reason = str(task.pop("reason", ""))
        global_loop_num = task.pop("global_loop_num", None)
        case_id = str(
            task.pop(
                "case_id",
                f"{self.config.case_id_prefix}_{global_loop_num}"
                if global_loop_num is not None
                else self.config.case_id_prefix,
            )
        )
        target = str(task.pop("target", self.config.target))
        case_kind = str(task.pop("case_kind", self.config.case_kind))
        tags = self._normalize_tags(task.pop("tags", []))
        config_module = task.pop("config_module", None)
        scenario_profile = task.pop("scenario_profile", None)
        container_profile = task.pop("container_profile", None)
        replay_meta = {
            key: task.pop(key)
            for key in (
                "replay_source_loop_num",
                "replay_source_case_id",
                "replay_source_collision",
                "replay_source_reason",
                "replay_source_csv",
            )
            if key in task
        }

        meta = {
            "source_module": self.config.source_module,
        }
        if global_loop_num is not None:
            meta["global_loop_num"] = global_loop_num
        if isinstance(config_module, str) and config_module:
            meta["config_module"] = config_module
        if isinstance(scenario_profile, str) and scenario_profile:
            meta["scenario_profile"] = scenario_profile
        if isinstance(container_profile, str) and container_profile:
            meta["container_profile"] = container_profile
        meta.update(replay_meta)

        return TestCase(
            case_id=case_id,
            target=target,
            case_kind=case_kind,
            input=task,
            tags=tags,
            reason=reason,
            meta=meta,
        )

    def _invoke(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.actor, method_name)
        if hasattr(method, "remote"):
            remote_result = method.remote(**kwargs) if kwargs else method.remote(*args)
            return self._resolve_remote(remote_result)
        return method(*args) if args else method(**kwargs)

    def _resolve_remote(self, remote_result: Any) -> Any:
        if self.ray_get is not None:
            return self.ray_get(remote_result)

        import ray  # type: ignore

        return ray.get(remote_result)

    def _normalize_tags(self, raw_tags: Any) -> list[str]:
        if raw_tags is None:
            return []
        if isinstance(raw_tags, str):
            return [raw_tags]
        if isinstance(raw_tags, list):
            return [str(tag) for tag in raw_tags]
        raise TypeError("tags must be a string, list, or None")
