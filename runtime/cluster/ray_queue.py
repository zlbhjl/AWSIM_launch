from __future__ import annotations

from typing import Any


class TaskQueue:
    def __init__(self) -> None:
        self.queue: list[dict[str, Any]] = []
        self.completed_count = 0
        self.stop_signal = False
        self.stop_reason = "Target Reached or Master Stopped"
        self.dispatched_count = 0
        self.worker_statuses: dict[str, str] = {}

    def update_worker_status(self, worker_id: str, status: str) -> None:
        self.worker_statuses[worker_id] = status

    def add_task(self, task: dict[str, Any]) -> None:
        self.queue.append(dict(task))

    def get_next_task(self) -> dict[str, Any] | None:
        if self.stop_signal:
            return {"system_command": "stop", "reason": self.stop_reason}
        if not self.queue:
            return None

        task = dict(self.queue.pop(0))
        self.dispatched_count += 1
        task["global_loop_num"] = self.dispatched_count
        return task

    def set_start_counts(self, count: int) -> None:
        if self.dispatched_count == 0:
            self.dispatched_count = count
            self.completed_count = count

    def report_completion(self, loop_num: int, status: str) -> bool:
        self.completed_count += 1
        return True

    def get_status(self) -> tuple[int, int, dict[str, str]]:
        return len(self.queue), self.completed_count, dict(self.worker_statuses)

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "queue_size": len(self.queue),
            "completed_count": self.completed_count,
            "dispatched_count": self.dispatched_count,
            "worker_statuses": dict(self.worker_statuses),
            "stop_signal": self.stop_signal,
            "stop_reason": self.stop_reason,
        }

    def set_stop_signal(self, reason: str = "Target Reached or Master Stopped") -> None:
        self.stop_signal = True
        self.stop_reason = reason
