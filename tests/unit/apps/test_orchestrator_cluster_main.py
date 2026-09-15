import json

import pytest

import apps.cli.orchestrator_cluster_main as cluster_main_module
from apps.cli.orchestrator_cluster_main import _format_worker_status, run_cluster_orchestrator


def test_format_worker_status_marks_stale_status() -> None:
    assert (
        _format_worker_status(
            "worker_21",
            "running",
            updated_at=100.0,
            now=701.0,
        )
        == "[worker_21] running stale(601s) restart_candidate"
    )


def test_format_worker_status_keeps_fresh_status_plain() -> None:
    assert (
        _format_worker_status(
            "worker_21",
            "running",
            updated_at=650.0,
            now=701.0,
        )
        == "[worker_21] running"
    )


def test_run_cluster_orchestrator_bootstraps_cluster_and_remote_queue(monkeypatch, capsys) -> None:
    captured = {}

    class FakeClusterManager:
        def start_head(self, config):
            captured["cluster_config"] = config
            captured.setdefault("call_order", []).append("start_head")
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            captured["launch_head_address"] = head_address
            captured.setdefault("call_order", []).append("launch_workers")

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
            captured.setdefault("call_order", []).append("connect")
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            captured["queue_actor_config"] = config
            captured.setdefault("call_order", []).append("ensure_task_queue_actor")
            return object()

        def ensure_shared_store_actor(
            self,
            config,
            *,
            dataset_csv_path,
            records_jsonl_path=None,
            buffer_timeout_sec=600,
        ):
            captured["shared_store_actor_config"] = config
            captured["dataset_csv_path"] = dataset_csv_path
            captured["records_jsonl_path"] = records_jsonl_path
            captured.setdefault("call_order", []).append("ensure_shared_store_actor")
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
            "--sync-aw-runtime-monitor",
        ]
    )

    assert exit_code == 0
    assert captured["cluster_config"].queue_actor_name == "TaskQueueActor"
    assert captured["cluster_config"].target == "awsim"
    assert captured["cluster_config"].worker_launch_stagger_sec == 20.0
    assert captured["cluster_config"].worker_queue_connect_retries == 6
    assert captured["cluster_config"].worker_queue_connect_retry_interval_sec == 15.0
    assert captured["cluster_config"].worker_queue_empty_wait_timeout_sec == 300.0
    assert captured["cluster_config"].worker_queue_empty_wait_interval_sec == 5.0
    assert captured["cluster_config"].worker_queue_heartbeat_interval_sec == 60.0
    assert captured["cluster_config"].worker_gpu_health_check is False
    assert captured["cluster_config"].sync_aw_runtime_monitor is True
    assert captured["launch_head_address"] == "150.65.227.21:6379"
    assert captured["call_order"] == [
        "start_head",
        "connect",
        "ensure_task_queue_actor",
        "ensure_shared_store_actor",
        "launch_workers",
    ]
    assert captured["dataset_csv_path"] == "/tmp/uturn_dataset.csv"
    assert captured["records_jsonl_path"] == "/tmp/records.jsonl"
    assert captured["orchestrator_config"].queue_address == "150.65.227.21:6379"
    assert captured["orchestrator_config"].run_inline_worker is False
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "cluster_queue"


def test_run_cluster_orchestrator_configures_prism_as_cpu_only_target(
    monkeypatch, capsys
) -> None:
    captured = {}

    class FakeClusterManager:
        def start_head(self, config):
            captured["cluster_config"] = config
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            class Summary:
                launched_workers = ("22号機",)
                skipped_workers = ()
                preflight_failures = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, _config):
            return FakeRay()

        def ensure_task_queue_actor(self, _config):
            return object()

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured["orchestrator_kwargs"] = kwargs

        def run(self, config):
            captured["orchestrator_config"] = config
            return {
                "mode": "cluster_queue",
                "enqueued": 1,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 1,
                "worker_statuses": {},
                "output_path": config.output,
            }

    monkeypatch.setattr(cluster_main_module, "ClusterManager", FakeClusterManager)
    monkeypatch.setattr(cluster_main_module, "ActorRuntime", FakeActorRuntime)
    monkeypatch.setattr(cluster_main_module, "Orchestrator", FakeOrchestrator)

    exit_code = run_cluster_orchestrator(
        [
            "--output",
            "/tmp/prism-records.jsonl",
            "--target",
            "prism",
            "--container-profile",
            "prism_maude",
            "--case-kind",
            "simple_reliability_dtmc",
            "--param",
            "model=simple_reliability_dtmc",
            "--auto-restart-gpu-workers",
        ]
    )

    assert exit_code == 0
    assert captured["cluster_config"].target == "prism"
    assert captured["cluster_config"].container_profile == "prism_maude"
    assert captured["cluster_config"].worker_gpu_health_check is False
    assert captured["orchestrator_config"].target == "prism"
    assert "maintenance_callback" not in captured["orchestrator_kwargs"]
    assert json.loads(capsys.readouterr().out.strip())["worker_exit_code"] == 0


def test_phase8_prism_common_cli_contract(monkeypatch, capsys) -> None:
    captured = {}

    class FakeClusterManager:
        def start_head(self, config):
            captured["cluster_config"] = config
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            captured["worker_launch_config"] = config
            captured["head_address"] = head_address

            class Summary:
                launched_workers = ("21号機", "22号機")
                skipped_workers = ()
                preflight_failures = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, config):
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            return object()

        def ensure_shared_store_actor(
            self,
            config,
            *,
            dataset_csv_path,
            records_jsonl_path=None,
            buffer_timeout_sec=600,
        ):
            captured["shared_store_actor_config"] = config
            captured["dataset_csv_path"] = dataset_csv_path
            captured["records_jsonl_path"] = records_jsonl_path
            return object()

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured["orchestrator_kwargs"] = kwargs

        def run(self, config):
            captured["orchestrator_config"] = config
            return {
                "mode": "cluster_queue",
                "enqueued": 39,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 39,
                "worker_statuses": {},
                "output_path": config.output,
            }

    monkeypatch.setattr(cluster_main_module, "ClusterManager", FakeClusterManager)
    monkeypatch.setattr(cluster_main_module, "ActorRuntime", FakeActorRuntime)
    monkeypatch.setattr(cluster_main_module, "Orchestrator", FakeOrchestrator)

    exit_code = run_cluster_orchestrator(
        [
            "--target",
            "prism",
            "--container-profile",
            "prism_maude",
            "--case-kind",
            "simple_reliability_dtmc",
            "--mode",
            "binomial_ci",
            "--binomial-target",
            "c_failure",
            "--binomial-confidence",
            "0.95",
            "--binomial-target-width",
            "0.3",
            "--max-samples",
            "60",
            "--param",
            "steps=20",
            "--param",
            "p_normal_degrade=0.1",
            "--param",
            "p_normal_failure=0.01",
            "--param",
            "p_degraded_normal=0.3",
            "--param",
            "p_degraded_failure=0.1",
            "--output",
            "/home/passd/prism_results/records.jsonl",
            "--dataset-csv",
            "/home/passd/prism_results/dataset.csv",
        ]
    )

    assert exit_code == 0
    cluster_config = captured["cluster_config"]
    assert cluster_config.target == "prism"
    assert cluster_config.container_profile == "prism_maude"
    assert cluster_config.worker_gpu_health_check is False
    assert cluster_config.output_path == "/home/passd/prism_results/records.jsonl"
    assert captured["records_jsonl_path"] == "/home/passd/prism_results/records.jsonl"
    assert captured["dataset_csv_path"] == "/home/passd/prism_results/dataset.csv"

    config = captured["orchestrator_config"]
    assert config.target == "prism"
    assert config.case_kind == "simple_reliability_dtmc"
    assert config.run_mode == "binomial_ci"
    assert config.binomial_target == "c_failure"
    assert config.binomial_confidence == 0.95
    assert config.binomial_target_width == 0.3
    assert config.max_samples == 60
    assert config.params == {
        "steps": 20,
        "p_normal_degrade": 0.1,
        "p_normal_failure": 0.01,
        "p_degraded_normal": 0.3,
        "p_degraded_failure": 0.1,
    }
    assert config.shared_store_actor_name == "SharedStoreActor"
    assert json.loads(capsys.readouterr().out.strip())["completed_count"] == 39


def test_prism_cluster_requires_prism_container_profile() -> None:
    with pytest.raises(ValueError, match="requires --container-profile prism_maude"):
        run_cluster_orchestrator(
            [
                "--output",
                "/tmp/prism-records.jsonl",
                "--target",
                "prism",
                "--param",
                "model=simple_reliability_dtmc",
            ]
        )


def test_run_cluster_orchestrator_wires_auto_restart_callback(monkeypatch, capsys) -> None:
    captured = {}

    class FakeClusterManager:
        nodes = {}

        def start_head(self, config):
            captured["cluster_config"] = config
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            captured["launch_head_address"] = head_address

            class Summary:
                head_address = "150.65.227.21:6379"
                launched_workers = ("21号機",)
                skipped_workers = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, config):
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            return object()

        def ensure_shared_store_actor(
            self,
            config,
            *,
            dataset_csv_path,
            records_jsonl_path=None,
            buffer_timeout_sec=600,
        ):
            return object()

    class FakeOrchestrator:
        def __init__(
            self,
            *,
            queue_gateway=None,
            host_worker_manager=None,
            progress_callback=None,
            maintenance_callback=None,
        ):
            captured["maintenance_callback"] = maintenance_callback

        def run(self, config):
            events = captured["maintenance_callback"](
                {
                    "queue_size": 0,
                    "in_flight_count": 0,
                    "worker_statuses": {},
                    "worker_status_updated_at": {},
                },
                config,
            )
            captured["maintenance_events"] = events
            return {
                "mode": "cluster_queue",
                "enqueued": 0,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 0,
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
            "--auto-restart-stale-workers",
            "--auto-restart-gpu-workers",
        ]
    )

    assert exit_code == 0
    assert captured["cluster_config"].worker_gpu_health_check is True
    assert callable(captured["maintenance_callback"])
    assert captured["maintenance_events"] == []
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "cluster_queue"


def test_run_cluster_orchestrator_wires_missing_worker_restart_callback(monkeypatch, capsys) -> None:
    captured = {}

    class FakeClusterManager:
        nodes = {}

        def start_head(self, config):
            captured["cluster_config"] = config
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            class Summary:
                head_address = "150.65.227.21:6379"
                launched_workers = ("21号機",)
                skipped_workers = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, config):
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            return object()

        def ensure_shared_store_actor(
            self,
            config,
            *,
            dataset_csv_path,
            records_jsonl_path=None,
            buffer_timeout_sec=600,
        ):
            return object()

    class FakeOrchestrator:
        def __init__(
            self,
            *,
            queue_gateway=None,
            host_worker_manager=None,
            progress_callback=None,
            maintenance_callback=None,
        ):
            captured["maintenance_callback"] = maintenance_callback

        def run(self, config):
            captured["maintenance_events"] = captured["maintenance_callback"](
                {
                    "queue_size": 0,
                    "in_flight_count": 0,
                    "worker_statuses": {},
                    "worker_status_updated_at": {},
                },
                config,
            )
            return {
                "mode": "cluster_queue",
                "enqueued": 0,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 0,
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
            "--auto-restart-missing-workers",
        ]
    )

    assert exit_code == 0
    assert captured["cluster_config"].worker_queue_connect_retries == 6
    assert captured["cluster_config"].worker_queue_connect_retry_interval_sec == 15.0
    assert callable(captured["maintenance_callback"])
    assert captured["maintenance_events"] == []
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] == "cluster_queue"


def test_run_cluster_orchestrator_reports_ray_gcs_loss(monkeypatch, capsys) -> None:
    class FakeClusterManager:
        def start_head(self, config):
            return "150.65.227.21:6379"

        def launch_workers(self, config, *, head_address):
            class Summary:
                head_address = "150.65.227.21:6379"
                launched_workers = ("21号機",)
                skipped_workers = ()

            return Summary()

    class FakeRay:
        @staticmethod
        def get(value):
            return value

    class FakeActorRuntime:
        def connect(self, config):
            return FakeRay()

        def ensure_task_queue_actor(self, config):
            return object()

        def ensure_shared_store_actor(
            self,
            config,
            *,
            dataset_csv_path,
            records_jsonl_path=None,
            buffer_timeout_sec=600,
        ):
            return object()

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            return None

        def run(self, config):
            raise RuntimeError("Failed to connect to GCS within 60 seconds")

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

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["stop_reason"] == "run_failed_ray_gcs_lost"
    assert "Failed to connect to GCS" in payload["reason"]
