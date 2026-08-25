from runtime.cluster.host_worker import HostWorkerConfig, HostWorkerManager


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 123
        self._returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        return self._returncode


def test_host_worker_manager_builds_v2_worker_command_without_dataset_when_shared_actor_is_used() -> None:
    command = HostWorkerManager._build_command(
        HostWorkerConfig(
            case_kind="uturn",
            run_mode="dkw",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            queue_namespace="awsim_cluster",
            queue_address="ray://127.0.0.1:10001",
            dataset_csv="/tmp/uturn_dataset.csv",
            shared_store_actor_name="SharedStoreActor",
            shared_store_namespace="awsim_cluster",
            shared_store_address="ray://127.0.0.1:10001",
            dkw_region="intersect_safe",
        )
    )

    assert "run_worker_v2.py" in command
    assert "--queue-actor-name" in command
    assert "--restart-on-refresh" in command
    assert "--shared-store-actor-name" in command
    assert "--dataset-csv" not in command


def test_host_worker_manager_start_and_stop_manage_process_lifecycle(tmp_path) -> None:
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return FakeProcess()

    manager = HostWorkerManager(popen_factory=fake_popen)
    manager.start(
        HostWorkerConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            log_dir=str(tmp_path),
        )
    )

    assert manager.is_running is True
    assert captured["cmd"][0:3] == ["python3", "-u", "run_worker_v2.py"]
    assert "--restart-on-refresh" in captured["cmd"]

    manager.stop()

    assert manager.is_running is False
