from runtime.cluster.cluster_manager import (
    ClusterLaunchConfig,
    ClusterManager,
)


class CompletedRun:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_cluster_manager_starts_head_and_launches_v2_workers() -> None:
    run_calls = []
    popen_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, dict(kwargs)))
        return CompletedRun()

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, dict(kwargs)))
        return object()

    nodes = {
        "master": {
            "machine": "21号機",
            "ip": "150.65.227.21",
            "user": "passd",
            "enabled": True,
            "container": {
                "name": "sim_worker_21",
                "ros_domain_id": 21,
                "password": "passd",
                "user": "passd",
                "workspace": "/home/passd",
                "image": "autoware_internal:2026",
            },
        },
        "worker1": {
            "machine": "22号機",
            "ip": "150.65.227.22",
            "user": "tomita1",
            "enabled": True,
            "container": {
                "name": "sim_worker_22",
                "ros_domain_id": 22,
                "password": "passd",
                "user": "passd",
                "workspace": "/home/passd",
                "image": "autoware_internal:2026",
            },
        },
    }
    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=fake_popen,
        nodes=nodes,
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    summary = manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            queue_namespace="awsim_cluster",
            shared_store_actor_name="SharedStoreActor",
            dataset_csv="/tmp/uturn_dataset.csv",
        )
    )

    assert summary.head_address == "150.65.227.21:6379"
    assert summary.launched_workers == ("21号機", "22号機")
    local_command = run_calls[-1][0]
    assert isinstance(local_command, str)
    assert "run_worker_v2.py" in local_command
    assert "--queue-actor-name TaskQueueActor" in local_command
    assert "--restart-on-refresh" in local_command
    assert "--shared-store-actor-name SharedStoreActor" in local_command
    remote_command = popen_calls[0][0][-1]
    assert "run_worker_v2.py" in remote_command
    assert "--restart-on-refresh" in remote_command
    assert "--shared-store-address 150.65.227.21:6379" in remote_command
