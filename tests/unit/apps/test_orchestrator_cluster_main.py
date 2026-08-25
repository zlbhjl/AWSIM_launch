import json

import apps.cli.orchestrator_cluster_main as cluster_main_module
from apps.cli.orchestrator_cluster_main import run_cluster_orchestrator


def test_run_cluster_orchestrator_bootstraps_cluster_and_remote_queue(monkeypatch, capsys) -> None:
    captured = {}

    class FakeClusterManager:
        def start_cluster(self, config):
            captured["cluster_config"] = config

            class Summary:
                head_address = "150.65.227.21:6379"
                launched_workers = ("21号機", "22号機")
                skipped_workers = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, config):
            captured["connect_config"] = config
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            captured["queue_actor_config"] = config
            return object()

        def ensure_shared_store_actor(self, config, *, dataset_csv_path, buffer_timeout_sec=600):
            captured["shared_store_actor_config"] = config
            captured["dataset_csv_path"] = dataset_csv_path
            return object()

    class FakeOrchestrator:
        def __init__(self, *, queue_gateway=None, host_worker_manager=None, progress_callback=None):
            captured["queue_gateway"] = queue_gateway
            captured["progress_callback"] = progress_callback

        def run(self, config):
            captured["orchestrator_config"] = config
            return {
                "mode": "cluster_queue",
                "enqueued": 2,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 2,
                "worker_statuses": {},
                "output_path": config.output,
            }

    monkeypatch.setattr(cluster_main_module, "ClusterManager", FakeClusterManager)
    monkeypatch.setattr(cluster_main_module, "ActorRuntime", FakeActorRuntime)
    monkeypatch.setattr(cluster_main_module, "Orchestrator", FakeOrchestrator)

    exit_code = run_cluster_orchestrator(
        [
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
            "--mode",
            "explore",
        ]
    )

    assert exit_code == 0
    assert captured["cluster_config"].queue_actor_name == "TaskQueueActor"
    assert captured["dataset_csv_path"] == "/tmp/uturn_dataset.csv"
    assert captured["orchestrator_config"].queue_address == "150.65.227.21:6379"
    assert captured["orchestrator_config"].run_inline_worker is False
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "cluster_queue"
