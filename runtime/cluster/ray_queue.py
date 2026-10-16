from __future__ import annotations

import time
from typing import Any


class TaskQueue:
    def __init__(self, *, time_func=time.time) -> None:
        self.queue: list[dict[str, Any]] = []
        self.completed_count = 0
        self.stop_signal = False
        self.stop_reason = "Target Reached or Master Stopped"
        self.dispatched_count = 0
        self.worker_statuses: dict[str, str] = {}
        self.worker_status_updated_at: dict[str, float] = {}
        self.in_flight: dict[int, dict[str, Any]] = {}
        self.completed_loop_nums: set[int] = set()
        self.time_func = time_func

    def update_worker_status(self, worker_id: str, status: str) -> None:
        self.worker_statuses[worker_id] = status
        self.worker_status_updated_at[worker_id] = float(self.time_func())

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
        self.in_flight[self.dispatched_count] = dict(task)
        return task

    def set_start_counts(self, count: int) -> None:
        if self.dispatched_count == 0:
            self.dispatched_count = count
            self.completed_count = count

    def report_completion(self, loop_num: int, status: str) -> bool:
        if loop_num in self.completed_loop_nums:
            return False
        self.completed_loop_nums.add(loop_num)
        self.in_flight.pop(loop_num, None)
        self.completed_count += 1
        return True

    def get_in_flight_tasks(self) -> list[dict[str, Any]]:
        return [dict(task) for _, task in sorted(self.in_flight.items())]

    def get_status(self) -> tuple[int, int, dict[str, str]]:
        return len(self.queue), self.completed_count, dict(self.worker_statuses)

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "queue_size": len(self.queue),
            "completed_count": self.completed_count,
            "dispatched_count": self.dispatched_count,
            "in_flight_count": len(self.in_flight),
            "in_flight_tasks": self.get_in_flight_tasks(),
            "worker_statuses": dict(self.worker_statuses),
            "worker_status_updated_at": dict(self.worker_status_updated_at),
            "stop_signal": self.stop_signal,
            "stop_reason": self.stop_reason,
        }

    def set_stop_signal(self, reason: str = "Target Reached or Master Stopped") -> None:
        self.stop_signal = True
        self.stop_reason = reason

    def cancel_pending_tasks(self) -> int:
        """Drop tasks that are still queued but not yet dispatched to any worker.

        Once the strategist has decided no more samples are needed, leftover
        pending tasks will never be claimed if the workers that would have
        polled for them have already exited -- nothing else ever pops them,
        so the queue would otherwise stay non-empty forever. In-flight tasks
        (already dispatched to a worker) are left untouched.
        """
        cancelled = len(self.queue)
        self.queue.clear()
        return cancelled
