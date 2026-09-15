from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from runtime.cluster.cluster_manager import ClusterLaunchConfig, ClusterManager
from runtime.container.profile import resolve_container_launch_profile


@dataclass(frozen=True)
class WorkerRestartPolicy:
    enabled: bool = False
    restart_missing_workers: bool = False
    restart_gpu_workers: bool = False
    stale_timeout_sec: float = 600.0
    check_interval_loops: int = 5
    restart_cooldown_sec: float = 600.0
    max_restart_count: int = 2
    log_tail_lines: int = 100
    gpu_max_restarts_per_24h: int = 5
    gpu_max_consecutive_failures: int = 3
    gpu_stable_reset_sec: float = 1800.0
    gpu_probe_timeout_sec: float = 5.0
    gpu_restart_window_sec: float = 86400.0
    gpu_post_restart_wait_sec: float = 30.0


class StaleWorkerRestarter:
    def __init__(
        self,
        *,
        cluster_manager: ClusterManager,
        cluster_config: ClusterLaunchConfig,
        head_address: str,
        policy: WorkerRestartPolicy,
        time_func: Callable[[], float] = time.time,
    ) -> None:
        self.cluster_manager = cluster_manager
        self.cluster_config = cluster_config
        self.head_address = head_address
        self.policy = policy
        launch_profile = resolve_container_launch_profile(
            cluster_config.container_profile
        )
        self.restart_gpu_workers = policy.restart_gpu_workers and (
            launch_profile.requires_gpu if launch_profile is not None else True
        )
        self.time_func = time_func
        self.check_count = 0
        self.restart_history: list[dict[str, object]] = []
        self.last_restart_at: dict[str, float] = {}
        self.restart_counts: dict[str, int] = {}
        self.gpu_restart_attempts: dict[str, list[float]] = {}
        self.gpu_consecutive_failures: dict[str, int] = {}
        self.gpu_healthy_since: dict[str, float] = {}
        self.gpu_quarantined: set[str] = set()
        self.gpu_terminal_quarantine: set[str] = set()
        nodes_by_worker_id = self._build_nodes_by_worker_id(cluster_manager.nodes)
        node_resolver = getattr(cluster_manager, "resolve_node_for_launch", None)
        if callable(node_resolver):
            nodes_by_worker_id = {
                worker_id: node_resolver(node, cluster_config)
                for worker_id, node in nodes_by_worker_id.items()
            }
        self.nodes_by_worker_id = nodes_by_worker_id

    def check(
        self,
        snapshot: Mapping[str, object],
        _config: object | None = None,
    ) -> list[dict[str, object]]:
        if not any(
            (
                self.policy.enabled,
                self.policy.restart_missing_workers,
                self.restart_gpu_workers,
            )
        ):
            return []
        self.check_count += 1
        interval = max(int(self.policy.check_interval_loops), 1)
        if self.check_count % interval != 0:
            return []
        if not self._has_remaining_work(snapshot):
            return []

        now = float(self.time_func())
        worker_statuses = dict(snapshot.get("worker_statuses", {}))
        worker_status_updated_at = dict(snapshot.get("worker_status_updated_at", {}))
        events: list[dict[str, object]] = []
        if self.restart_gpu_workers:
            events.extend(self._recover_gpu_workers(worker_statuses, now=now))
        if self.policy.enabled:
            for worker_id, status in sorted(worker_statuses.items()):
                worker_key = str(worker_id)
                if worker_key in self.gpu_quarantined:
                    continue
                updated_at = worker_status_updated_at.get(worker_id)
                if updated_at is None:
                    updated_at = worker_status_updated_at.get(worker_key)
                if not self._is_stale(updated_at, now=now):
                    continue
                if not self._cooldown_elapsed(worker_key, now=now):
                    continue
                if not self._restart_count_available(worker_key):
                    events.append(
                        self._record_event(
                            worker_id=worker_key,
                            status=str(status),
                            action="skip",
                            reason="restart_limit_reached",
                            now=now,
                            updated_at=updated_at,
                        )
                    )
                    continue
                node = self.nodes_by_worker_id.get(worker_key)
                if node is None:
                    continue

                state = self.cluster_manager.get_container_state(node)
                if state.inspect_error:
                    continue
                if state.exists and state.running:
                    continue

                log_tail = self._get_container_log_tail(node)
                self.cluster_manager.restart_node_worker(
                    node=node,
                    config=self.cluster_config,
                    head_address=self.head_address,
                    sync_sources=False,
                )
                self._mark_restarted(worker_key, now=now)
                events.append(
                    self._record_event(
                        worker_id=worker_key,
                        status=str(status),
                        action="restart",
                        reason="stale_container_stopped",
                        now=now,
                        updated_at=updated_at,
                        container_status=state.status,
                        container_exit_code=state.exit_code,
                        container_log_tail=log_tail,
                    )
                )
        events.extend(self._restart_missing_workers(worker_statuses, now=now))
        return events

    def _restart_missing_workers(
        self,
        worker_statuses: Mapping[object, object],
        *,
        now: float,
    ) -> list[dict[str, object]]:
        if not self.policy.restart_missing_workers:
            return []
        events: list[dict[str, object]] = []
        known_worker_ids = {str(worker_id) for worker_id in worker_statuses}
        for worker_id, node in sorted(self.nodes_by_worker_id.items()):
            if worker_id in self.gpu_quarantined:
                continue
            if worker_id in known_worker_ids:
                continue
            if not self._cooldown_elapsed(worker_id, now=now):
                continue
            state = self.cluster_manager.get_container_state(node)
            if state.inspect_error:
                continue
            if not state.exists or state.running or state.exit_code != 1:
                continue
            if not self._restart_count_available(worker_id):
                events.append(
                    self._record_event(
                        worker_id=worker_id,
                        status="missing",
                        action="skip",
                        reason="restart_limit_reached",
                        now=now,
                        updated_at=None,
                        container_status=state.status,
                        container_exit_code=state.exit_code,
                        container_log_tail=self._get_container_log_tail(node),
                    )
                )
                continue

            log_tail = self._get_container_log_tail(node)
            self.cluster_manager.restart_node_worker(
                node=node,
                config=self.cluster_config,
                head_address=self.head_address,
                sync_sources=False,
            )
            self._mark_restarted(worker_id, now=now)
            events.append(
                self._record_event(
                    worker_id=worker_id,
                    status="missing",
                    action="restart",
                    reason="missing_container_exited_1",
                    now=now,
                    updated_at=None,
                    container_status=state.status,
                    container_exit_code=state.exit_code,
                    container_log_tail=log_tail,
                )
            )
        return events

    def _recover_gpu_workers(
        self,
        worker_statuses: Mapping[object, object],
        *,
        now: float,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for worker_id, node in sorted(self.nodes_by_worker_id.items()):
            if worker_id in self.gpu_terminal_quarantine:
                continue
            state = self.cluster_manager.get_container_state(node)
            status = str(worker_statuses.get(worker_id, "missing"))
            gpu_exit = state.exit_code == 75
            should_probe = state.exists and state.running
            explicitly_unhealthy = status == "gpu_unavailable" or gpu_exit

            if should_probe:
                healthy, detail = self.cluster_manager.probe_container_gpu(
                    node,
                    timeout_sec=self.policy.gpu_probe_timeout_sec,
                )
                if healthy:
                    self._mark_gpu_healthy(worker_id, now=now)
                    continue
                explicitly_unhealthy = True
            else:
                detail = (
                    f"container is {state.status} (exit_code={state.exit_code})"
                    if state.exists
                    else "container is missing"
                )

            if not explicitly_unhealthy and worker_id not in self.gpu_quarantined:
                continue
            newly_quarantined = worker_id not in self.gpu_quarantined
            self.gpu_quarantined.add(worker_id)
            log_tail = self._get_container_log_tail(node) if state.exists else ""
            if state.exists and state.running and status == "running":
                if newly_quarantined:
                    events.append(
                        self._record_event(
                            worker_id=worker_id,
                            status=status,
                            action="quarantine_pending",
                            reason="gpu_failure_waiting_for_case_boundary",
                            now=now,
                            updated_at=None,
                            container_log_tail=log_tail,
                            details={"gpu_probe_error": detail},
                        )
                    )
                continue
            if state.exists and state.running:
                self.cluster_manager.remove_node_worker(node)
            if not self._cooldown_elapsed(worker_id, now=now):
                if newly_quarantined:
                    events.append(
                        self._record_event(
                            worker_id=worker_id,
                            status=status,
                            action="quarantine",
                            reason="gpu_restart_cooldown",
                            now=now,
                            updated_at=None,
                            container_log_tail=log_tail,
                            details={"gpu_probe_error": detail},
                        )
                    )
                continue

            if state.exists and not state.running:
                self.cluster_manager.remove_node_worker(node)
            self._prune_gpu_attempts(worker_id, now=now)
            if not self._gpu_attempt_available(worker_id):
                events.append(
                    self._terminally_quarantine_gpu_worker(
                        worker_id=worker_id,
                        status=status,
                        reason="gpu_restart_24h_limit_reached",
                        now=now,
                        detail=detail,
                        log_tail=log_tail,
                    )
                )
                continue

            host_healthy, host_detail = self.cluster_manager.probe_host_gpu(
                node,
                timeout_sec=self.policy.gpu_probe_timeout_sec,
            )
            self.last_restart_at[worker_id] = now
            if not host_healthy:
                self._mark_gpu_recovery_failed(worker_id)
                action = "quarantine"
                reason = "gpu_host_unavailable"
                if self._gpu_failure_limit_reached(worker_id):
                    self.gpu_terminal_quarantine.add(worker_id)
                    action = "terminal_quarantine"
                    reason = "gpu_host_failure_limit_reached"
                events.append(
                    self._record_event(
                        worker_id=worker_id,
                        status=status,
                        action=action,
                        reason=reason,
                        now=now,
                        updated_at=None,
                        container_log_tail=log_tail,
                        details={
                            "gpu_probe_error": detail,
                            "host_gpu_probe_error": host_detail,
                            "gpu_consecutive_failures": self.gpu_consecutive_failures.get(
                                worker_id, 0
                            ),
                        },
                    )
                )
                continue

            self.gpu_restart_attempts.setdefault(worker_id, []).append(now)
            try:
                self.cluster_manager.restart_node_worker(
                    node=node,
                    config=self.cluster_config,
                    head_address=self.head_address,
                    sync_sources=False,
                )
                recovered, recovery_detail = self.cluster_manager.wait_for_container_gpu(
                    node,
                    timeout_sec=self.policy.gpu_post_restart_wait_sec,
                    probe_timeout_sec=self.policy.gpu_probe_timeout_sec,
                )
            except Exception as exc:
                recovered = False
                recovery_detail = str(exc)

            if recovered:
                self.gpu_quarantined.discard(worker_id)
                self.gpu_healthy_since[worker_id] = now
                events.append(
                    self._record_event(
                        worker_id=worker_id,
                        status=status,
                        action="restart",
                        reason="gpu_container_recovered",
                        now=now,
                        updated_at=None,
                        container_log_tail=log_tail,
                        details={
                            "gpu_probe_error": detail,
                            "recovery_gpu_probe": recovery_detail,
                            "gpu_restarts_in_24h": len(
                                self.gpu_restart_attempts.get(worker_id, [])
                            ),
                        },
                    )
                )
                continue

            self.cluster_manager.remove_node_worker(node)
            self._mark_gpu_recovery_failed(worker_id)
            action = "quarantine"
            reason = "gpu_container_recovery_failed"
            if self._gpu_failure_limit_reached(worker_id) or not self._gpu_attempt_available(
                worker_id
            ):
                self.gpu_terminal_quarantine.add(worker_id)
                action = "terminal_quarantine"
                reason = "gpu_recovery_limit_reached"
            events.append(
                self._record_event(
                    worker_id=worker_id,
                    status=status,
                    action=action,
                    reason=reason,
                    now=now,
                    updated_at=None,
                    container_log_tail=log_tail,
                    details={
                        "gpu_probe_error": detail,
                        "recovery_gpu_probe_error": recovery_detail,
                        "gpu_consecutive_failures": self.gpu_consecutive_failures.get(
                            worker_id, 0
                        ),
                        "gpu_restarts_in_24h": len(
                            self.gpu_restart_attempts.get(worker_id, [])
                        ),
                    },
                )
            )
        return events

    def _mark_gpu_healthy(self, worker_id: str, *, now: float) -> None:
        healthy_since = self.gpu_healthy_since.setdefault(worker_id, now)
        if now - healthy_since >= float(self.policy.gpu_stable_reset_sec):
            self.gpu_consecutive_failures[worker_id] = 0

    def _mark_gpu_recovery_failed(self, worker_id: str) -> None:
        self.gpu_healthy_since.pop(worker_id, None)
        self.gpu_consecutive_failures[worker_id] = (
            self.gpu_consecutive_failures.get(worker_id, 0) + 1
        )

    def _gpu_failure_limit_reached(self, worker_id: str) -> bool:
        return self.gpu_consecutive_failures.get(worker_id, 0) >= int(
            self.policy.gpu_max_consecutive_failures
        )

    def _prune_gpu_attempts(self, worker_id: str, *, now: float) -> None:
        cutoff = now - float(self.policy.gpu_restart_window_sec)
        self.gpu_restart_attempts[worker_id] = [
            timestamp
            for timestamp in self.gpu_restart_attempts.get(worker_id, [])
            if timestamp >= cutoff
        ]

    def _gpu_attempt_available(self, worker_id: str) -> bool:
        return len(self.gpu_restart_attempts.get(worker_id, [])) < int(
            self.policy.gpu_max_restarts_per_24h
        )

    def _terminally_quarantine_gpu_worker(
        self,
        *,
        worker_id: str,
        status: str,
        reason: str,
        now: float,
        detail: str,
        log_tail: str,
    ) -> dict[str, object]:
        self.gpu_terminal_quarantine.add(worker_id)
        return self._record_event(
            worker_id=worker_id,
            status=status,
            action="terminal_quarantine",
            reason=reason,
            now=now,
            updated_at=None,
            container_log_tail=log_tail,
            details={
                "gpu_probe_error": detail,
                "gpu_restarts_in_24h": len(self.gpu_restart_attempts.get(worker_id, [])),
            },
        )

    def _record_event(
        self,
        *,
        worker_id: str,
        status: str,
        action: str,
        reason: str,
        now: float,
        updated_at: object,
        container_status: str | None = None,
        container_exit_code: int | None = None,
        container_log_tail: str | None = None,
        inspect_error: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        event: dict[str, object] = {
            "worker_id": worker_id,
            "status": status,
            "action": action,
            "reason": reason,
            "checked_at": now,
        }
        if isinstance(updated_at, (int, float)):
            event["stale_age_sec"] = max(now - float(updated_at), 0.0)
        if container_status is not None:
            event["container_status"] = container_status
        if container_exit_code is not None:
            event["container_exit_code"] = container_exit_code
        if container_log_tail:
            event["container_log_tail"] = container_log_tail
        if inspect_error:
            event["inspect_error"] = inspect_error
        if details:
            event.update(details)
        self.restart_history.append(dict(event))
        return event

    def _get_container_log_tail(self, node: Mapping[str, object]) -> str:
        log_reader = getattr(self.cluster_manager, "get_container_logs", None)
        if not callable(log_reader):
            return ""
        return str(log_reader(node, tail_lines=self.policy.log_tail_lines) or "")

    def _restart_count_available(self, worker_id: str) -> bool:
        return self.restart_counts.get(worker_id, 0) < max(
            int(self.policy.max_restart_count),
            0,
        )

    def _mark_restarted(self, worker_id: str, *, now: float) -> None:
        self.last_restart_at[worker_id] = now
        self.restart_counts[worker_id] = self.restart_counts.get(worker_id, 0) + 1

    def _is_stale(self, updated_at: object, *, now: float) -> bool:
        if not isinstance(updated_at, (int, float)):
            return False
        return now - float(updated_at) >= float(self.policy.stale_timeout_sec)

    def _cooldown_elapsed(self, worker_id: str, *, now: float) -> bool:
        last_restart = self.last_restart_at.get(worker_id)
        if last_restart is None:
            return True
        return now - last_restart >= float(self.policy.restart_cooldown_sec)

    @staticmethod
    def _has_remaining_work(snapshot: Mapping[str, object]) -> bool:
        queue_size = int(snapshot.get("queue_size", 0) or 0)
        in_flight_count = int(snapshot.get("in_flight_count", 0) or 0)
        return queue_size > 0 or in_flight_count > 0

    @staticmethod
    def _build_nodes_by_worker_id(
        nodes: Mapping[str, Mapping[str, object]],
    ) -> dict[str, Mapping[str, object]]:
        resolved: dict[str, Mapping[str, object]] = {}
        for node in nodes.values():
            container = node.get("container")
            if not isinstance(container, Mapping):
                continue
            ros_domain_id = container.get("ros_domain_id")
            if ros_domain_id is None:
                continue
            resolved[f"worker_{ros_domain_id}"] = node
        return resolved


__all__ = [
    "StaleWorkerRestarter",
    "WorkerRestartPolicy",
]
