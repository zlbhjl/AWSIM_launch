from runtime.cluster.cluster_manager import ClusterLaunchConfig, ContainerState
from runtime.cluster.worker_watchdog import StaleWorkerRestarter, WorkerRestartPolicy


class FakeClusterManager:
    def __init__(
        self,
        state: ContainerState,
        *,
        nodes=None,
        log_tail: str = "container failed",
        container_gpu_results=None,
        host_gpu_result=(True, "host gpu ready"),
        recovery_gpu_result=(True, "container gpu ready"),
    ) -> None:
        self.nodes = {
            "worker1": {
                "machine": "22号機",
                "ip": "150.65.227.22",
                "user": "tomita1",
                "container": {
                    "name": "sim_worker_22",
                    "ros_domain_id": 22,
                },
            }
        } if nodes is None else nodes
        self.state = state
        self.log_tail = log_tail
        self.log_calls = []
        self.restart_calls = []
        self.remove_calls = []
        self.container_gpu_results = list(container_gpu_results or [(True, "gpu ready")])
        self.host_gpu_result = host_gpu_result
        self.recovery_gpu_result = recovery_gpu_result

    def get_container_state(self, node):
        return self.state

    def restart_node_worker(self, *, node, config, head_address, sync_sources=False):
        self.restart_calls.append(
            {
                "node": node,
                "config": config,
                "head_address": head_address,
                "sync_sources": sync_sources,
            }
        )

    def get_container_logs(self, node, *, tail_lines=100):
        self.log_calls.append({"node": node, "tail_lines": tail_lines})
        return self.log_tail

    def probe_container_gpu(self, node, *, timeout_sec=5.0):
        if len(self.container_gpu_results) > 1:
            return self.container_gpu_results.pop(0)
        return self.container_gpu_results[0]

    def probe_host_gpu(self, node, *, timeout_sec=5.0):
        return self.host_gpu_result

    def remove_node_worker(self, node):
        self.remove_calls.append(node)
        return True, ""

    def wait_for_container_gpu(
        self,
        node,
        *,
        timeout_sec=30.0,
        poll_interval_sec=2.0,
        probe_timeout_sec=5.0,
    ):
        return self.recovery_gpu_result


def _cluster_config() -> ClusterLaunchConfig:
    return ClusterLaunchConfig(
        case_kind="uturn",
        run_mode="binomial_ci",
        output_path="/tmp/records.jsonl",
        queue_actor_name="TaskQueueActor",
        container_profile="autoware171",
    )


def test_prism_profile_disables_gpu_watchdog_even_if_policy_requests_it() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(False, "must not be probed")],
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=ClusterLaunchConfig(
            case_kind="simple_reliability_dtmc",
            run_mode="explore",
            output_path="/tmp/prism-records.jsonl",
            queue_actor_name="TaskQueueActor",
            target="prism",
            container_profile="prism_maude",
        ),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
        ),
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert events == []
    assert restarter.check_count == 0


def test_stale_worker_restarter_restarts_stopped_stale_container() -> None:
    manager = FakeClusterManager(ContainerState(exists=True, running=False, status="exited", exit_code=1))
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            enabled=True,
            stale_timeout_sec=600.0,
            check_interval_loops=1,
            restart_cooldown_sec=600.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert len(manager.restart_calls) == 1
    assert manager.restart_calls[0]["head_address"] == "150.65.227.108:6379"
    assert events[0]["action"] == "restart"
    assert events[0]["reason"] == "stale_container_stopped"
    assert events[0]["container_exit_code"] == 1


def test_stale_worker_restarter_does_not_restart_running_container() -> None:
    manager = FakeClusterManager(ContainerState(exists=True, running=True, status="running"))
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            enabled=True,
            stale_timeout_sec=600.0,
            check_interval_loops=1,
            restart_cooldown_sec=600.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert manager.restart_calls == []
    assert events == []


def test_stale_worker_restarter_respects_check_interval() -> None:
    manager = FakeClusterManager(ContainerState(exists=True, running=False, status="exited"))
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            enabled=True,
            stale_timeout_sec=600.0,
            check_interval_loops=2,
            restart_cooldown_sec=600.0,
        ),
        time_func=lambda: 1000.0,
    )

    first_events = restarter.check(
        {
            "queue_size": 1,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )
    second_events = restarter.check(
        {
            "queue_size": 1,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert first_events == []
    assert second_events[0]["action"] == "restart"
    assert len(manager.restart_calls) == 1


def test_stale_worker_restarter_does_not_restart_when_no_work_remains() -> None:
    manager = FakeClusterManager(ContainerState(exists=True, running=False, status="exited"))
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            enabled=True,
            stale_timeout_sec=600.0,
            check_interval_loops=1,
            restart_cooldown_sec=600.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 0,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert events == []
    assert manager.restart_calls == []


def test_stale_worker_restarter_restarts_missing_exited_1_worker() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=False, status="exited", exit_code=1),
        log_tail="ray client failed",
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_missing_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=600.0,
            max_restart_count=2,
            log_tail_lines=100,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {},
            "worker_status_updated_at": {},
        }
    )

    assert len(manager.restart_calls) == 1
    assert manager.log_calls[0]["tail_lines"] == 100
    assert events[0]["action"] == "restart"
    assert events[0]["reason"] == "missing_container_exited_1"
    assert events[0]["container_exit_code"] == 1
    assert events[0]["container_log_tail"] == "ray client failed"


def test_stale_worker_restarter_does_not_restart_missing_exited_0_worker() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=False, status="exited", exit_code=0)
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_missing_workers=True,
            check_interval_loops=1,
            max_restart_count=2,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {},
            "worker_status_updated_at": {},
        }
    )

    assert events == []
    assert manager.restart_calls == []


def test_missing_worker_restart_option_does_not_restart_stale_registered_worker() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=False, status="exited", exit_code=1)
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            enabled=False,
            restart_missing_workers=True,
            stale_timeout_sec=600.0,
            check_interval_loops=1,
            max_restart_count=2,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 100.0},
        }
    )

    assert events == []
    assert manager.restart_calls == []


def test_stale_worker_restarter_respects_missing_restart_limit() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=False, status="exited", exit_code=1)
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_missing_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=0.0,
            max_restart_count=1,
        ),
        time_func=lambda: 1000.0,
    )
    snapshot = {
        "queue_size": 1,
        "in_flight_count": 0,
        "worker_statuses": {},
        "worker_status_updated_at": {},
    }

    first_events = restarter.check(snapshot)
    second_events = restarter.check(snapshot)

    assert first_events[0]["action"] == "restart"
    assert second_events[0]["action"] == "skip"
    assert second_events[0]["reason"] == "restart_limit_reached"
    assert len(manager.restart_calls) == 1


def test_gpu_watchdog_recreates_container_and_releases_quarantine() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(False, "Failed to initialize NVML")],
        recovery_gpu_result=(True, "NVIDIA RTX"),
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=0.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "gpu_unavailable"},
            "worker_status_updated_at": {"worker_22": 999.0},
        }
    )

    assert len(manager.remove_calls) == 1
    assert len(manager.restart_calls) == 1
    assert events[0]["action"] == "restart"
    assert events[0]["reason"] == "gpu_container_recovered"
    assert "worker_22" not in restarter.gpu_quarantined


def test_gpu_watchdog_recreates_missing_gpu_unavailable_container() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=False),
        recovery_gpu_result=(True, "NVIDIA RTX"),
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=0.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "in_flight_count": 0,
            "worker_statuses": {"worker_22": "gpu_unavailable"},
            "worker_status_updated_at": {"worker_22": 999.0},
        }
    )

    assert manager.remove_calls == []
    assert len(manager.restart_calls) == 1
    assert events[0]["action"] == "restart"
    assert events[0]["reason"] == "gpu_container_recovered"
    assert events[0]["gpu_probe_error"] == "container is missing"


def test_gpu_watchdog_stops_unhealthy_container_during_restart_cooldown() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(False, "Failed to initialize NVML")],
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=600.0,
        ),
        time_func=lambda: 1000.0,
    )
    restarter.last_restart_at["worker_22"] = 900.0

    events = restarter.check(
        {
            "queue_size": 1,
            "worker_statuses": {"worker_22": "gpu_unavailable"},
            "worker_status_updated_at": {"worker_22": 999.0},
        }
    )

    assert len(manager.remove_calls) == 1
    assert manager.restart_calls == []
    assert events[0]["action"] == "quarantine"
    assert events[0]["reason"] == "gpu_restart_cooldown"


def test_gpu_watchdog_waits_for_running_case_before_recreating_container() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(False, "Failed to initialize NVML")],
    )
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=0.0,
        ),
        time_func=lambda: 1000.0,
    )

    events = restarter.check(
        {
            "queue_size": 1,
            "worker_statuses": {"worker_22": "running"},
            "worker_status_updated_at": {"worker_22": 999.0},
        }
    )

    assert manager.remove_calls == []
    assert manager.restart_calls == []
    assert events[0]["action"] == "quarantine_pending"
    assert events[0]["reason"] == "gpu_failure_waiting_for_case_boundary"


def test_gpu_watchdog_terminally_quarantines_after_consecutive_failures() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(False, "NVML unavailable")],
        recovery_gpu_result=(False, "container GPU unavailable"),
    )
    now = [1000.0]
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            restart_cooldown_sec=0.0,
            gpu_max_consecutive_failures=3,
            gpu_max_restarts_per_24h=5,
        ),
        time_func=lambda: now[0],
    )
    snapshot = {
        "queue_size": 1,
        "in_flight_count": 0,
        "worker_statuses": {"worker_22": "gpu_unavailable"},
        "worker_status_updated_at": {"worker_22": 999.0},
    }

    first = restarter.check(snapshot)
    now[0] += 1.0
    second = restarter.check(snapshot)
    now[0] += 1.0
    third = restarter.check(snapshot)

    assert first[0]["action"] == "quarantine"
    assert second[0]["action"] == "quarantine"
    assert third[0]["action"] == "terminal_quarantine"
    assert len(manager.restart_calls) == 3
    assert "worker_22" in restarter.gpu_terminal_quarantine


def test_gpu_watchdog_resets_failure_streak_after_stable_healthy_period() -> None:
    manager = FakeClusterManager(
        ContainerState(exists=True, running=True, status="running"),
        container_gpu_results=[(True, "NVIDIA RTX")],
    )
    now = [1000.0]
    restarter = StaleWorkerRestarter(
        cluster_manager=manager,
        cluster_config=_cluster_config(),
        head_address="150.65.227.108:6379",
        policy=WorkerRestartPolicy(
            restart_gpu_workers=True,
            check_interval_loops=1,
            gpu_stable_reset_sec=1800.0,
        ),
        time_func=lambda: now[0],
    )
    restarter.gpu_consecutive_failures["worker_22"] = 2
    snapshot = {
        "queue_size": 1,
        "worker_statuses": {"worker_22": "running"},
        "worker_status_updated_at": {"worker_22": 999.0},
    }

    restarter.check(snapshot)
    assert restarter.gpu_consecutive_failures["worker_22"] == 2
    now[0] += 1800.0
    restarter.check(snapshot)

    assert restarter.gpu_consecutive_failures["worker_22"] == 0
