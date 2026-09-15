import hashlib
import subprocess
from pathlib import Path

import pytest

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
    sleep_calls = []

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
        sleeper=lambda seconds: sleep_calls.append(seconds),
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
            run_id="autoware180_replay_20260911",
            queue_namespace="awsim_cluster",
                shared_store_actor_name="SharedStoreActor",
                dataset_csv="/tmp/uturn_dataset.csv",
                sync_aw_runtime_monitor=False,
                worker_gpu_health_check=True,
        )
    )

    assert summary.head_address == "150.65.227.21:6379"
    assert summary.launched_workers == ("21号機", "22号機")
    assert sleep_calls == [20.0]
    ray_start_command, ray_start_call = next(
        (cmd, kwargs)
        for cmd, kwargs in run_calls
        if isinstance(cmd, list) and cmd[:2] == ["ray", "start"]
    )
    assert ray_start_call["timeout"] == 180
    assert not any(str(arg).startswith("--num-cpus") for arg in ray_start_command)
    local_command = next(
        cmd for cmd, _kwargs in run_calls if isinstance(cmd, str) and "run_worker_v2.py" in cmd
    )
    assert isinstance(local_command, str)
    assert "run_worker_v2.py" in local_command
    assert "--queue-actor-name TaskQueueActor" in local_command
    assert "--restart-on-refresh" in local_command
    assert "--queue-connect-retries 6" in local_command
    assert "--queue-connect-retry-interval 15.0" in local_command
    assert "--queue-empty-wait-timeout 300.0" in local_command
    assert "--queue-empty-wait-interval 5.0" in local_command
    assert "--queue-heartbeat-interval 60.0" in local_command
    assert "--worker-gpu-health-check" in local_command
    assert "--worker-gpu-health-timeout-sec 5.0" in local_command
    assert "--shared-store-actor-name SharedStoreActor" in local_command
    assert "--node-ip-address=150.65.227.21" in local_command
    assert "~/AWSIM_launch" not in local_command
    assert "/home/passd/AWSIM_launch:/home/passd/AWSIM_launch" in local_command
    assert (
        "/home/passd/simulation_traces_sim_worker_21_autoware180_replay_20260911:"
        "/home/passd/simulation_traces"
    ) in local_command
    remote_command = popen_calls[0][0][-1]
    assert "run_worker_v2.py" in remote_command
    assert "--restart-on-refresh" in remote_command
    assert "--shared-store-address 150.65.227.21:6379" in remote_command
    assert "--node-ip-address=150.65.227.22" in remote_command
    assert "~/AWSIM_launch" not in remote_command
    assert "/home/tomita1/AWSIM_launch:/home/passd/AWSIM_launch" in remote_command
    assert (
        "/home/tomita1/simulation_traces_sim_worker_22_autoware180_replay_20260911:"
        "/home/passd/simulation_traces"
    ) in remote_command


def test_cluster_manager_syncs_default_launch_and_requested_runtime_inputs() -> None:
    run_calls = []
    popen_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, dict(kwargs)))
        if isinstance(cmd, list) and cmd and cmd[0] == "ssh" and "sha256sum" in cmd[-1]:
            monitor_root = Path("/home/passd/AW-Runtime-Monitor")
            relative_paths = (
                "main.py",
                "recorder/Recorder.py",
                "recorder/AWSIMClientOpStateTracker.py",
            )
            stdout = "\n".join(
                f"{hashlib.sha256((monitor_root / relative_path).read_bytes()).hexdigest()} "
                f"/home/tomita1/AW-Runtime-Monitor/{relative_path}"
                for relative_path in relative_paths
            )
            return CompletedRun(stdout=stdout)
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=lambda *args, **kwargs: popen_calls.append((args, kwargs)),
        nodes={
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
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            sync_awsim_script_py=True,
            sync_autoware180_map=True,
        )
    )

    rsync_commands = [
        cmd
        for cmd, _kwargs in run_calls
        if isinstance(cmd, list) and cmd and cmd[0] == "rsync"
    ]
    assert [cmd[-2] for cmd in rsync_commands] == [
        "/home/passd/AWSIM_launch/",
        "/home/passd/AWSIMScriptPy/",
        "/home/passd/AW-Runtime-Monitor/",
        "/home/passd/autoware180_runtime/maps/",
    ]
    assert [cmd[-1] for cmd in rsync_commands] == [
        "tomita1@150.65.227.22:~/AWSIM_launch/",
        "tomita1@150.65.227.22:~/AWSIMScriptPy/",
        "tomita1@150.65.227.22:~/AW-Runtime-Monitor/",
        "tomita1@150.65.227.22:~/autoware180_runtime/maps/",
    ]
    assert "--exclude" in rsync_commands[0]
    assert ".git" in rsync_commands[0]
    assert ".pytest_cache" in rsync_commands[0]
    assert "tmp" in rsync_commands[0]
    remote_worker_command = popen_calls[0][0][0][-1]
    assert "AWSIM_CLIENT_OP_STATE_STOPPED" in remote_worker_command


def test_cluster_manager_rejects_runtime_monitor_checksum_mismatch() -> None:
    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "ssh" and "sha256sum" in cmd[-1]:
            return CompletedRun(
                stdout=(
                    "bad /home/tomita1/AW-Runtime-Monitor/main.py\n"
                    "bad /home/tomita1/AW-Runtime-Monitor/recorder/Recorder.py\n"
                    "bad /home/tomita1/AW-Runtime-Monitor/recorder/AWSIMClientOpStateTracker.py"
                )
            )
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=lambda *args, **kwargs: object(),
        nodes={
            "worker1": {
                "machine": "22号機",
                "ip": "150.65.227.22",
                "user": "tomita1",
                "enabled": True,
                "container": {
                    "name": "sim_worker_22",
                    "ros_domain_id": 22,
                    "user": "passd",
                    "workspace": "/home/passd",
                },
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        manager.launch_workers(
            ClusterLaunchConfig(
                case_kind="uturn",
                run_mode="explore",
                output_path="/tmp/records.jsonl",
                queue_actor_name="TaskQueueActor",
                sync_aw_runtime_monitor=True,
            ),
            head_address="150.65.227.21:6379",
        )


def test_cluster_manager_skips_node_when_container_gpu_probe_fails() -> None:
    popen_calls = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "docker" and "nvidia-smi" in cmd:
            return CompletedRun(returncode=1, stderr="no CUDA-capable device")
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=lambda *args, **kwargs: popen_calls.append((args, kwargs)),
        nodes={
            "master": {
                "machine": "21号機",
                "ip": "150.65.227.21",
                "user": "passd",
                "enabled": True,
                "container": {
                    "name": "sim_worker_21",
                    "ros_domain_id": 21,
                    "user": "passd",
                    "workspace": "/home/passd",
                    "image": "autoware_internal:2026",
                },
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    summary = manager.launch_workers(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            sync_awsim_launch=False,
        ),
        head_address="150.65.227.21:6379",
    )

    assert summary.launched_workers == ()
    assert summary.skipped_workers == ("21号機",)
    assert summary.preflight_failures == ("21号機: no CUDA-capable device",)
    assert popen_calls == []


def test_cluster_manager_uses_named_container_and_scenario_profiles() -> None:
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, dict(kwargs)))
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=lambda *args, **kwargs: object(),
        nodes={
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
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            container_profile="autoware171",
            sync_aw_runtime_monitor=False,
            scenario_profile="autoware171",
        )
    )

    local_command = run_calls[-1][0]
    assert "autoware_internal:2026-1.7.1-x11-verified-20260808" in local_command
    assert "--network bridge" in local_command
    assert "--add-host host.docker.internal:host-gateway" in local_command
    assert "--scenario-profile autoware171" in local_command
    assert "--container-profile autoware171" in local_command
    assert "~/AWSIM_launch" not in local_command
    assert "PYTHONPATH=/opt/awsim_python_deps/py310" in local_command
    assert "--queue-address ray://150.65.227.21:10001" in local_command
    assert "apt-get update" not in local_command
    assert "apt-get install" not in local_command
    assert (
        "/home/passd/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro"
        in local_command
    )
    assert "/home/passd/AWSIMScriptPy:/home/passd/AWSIMScriptPy" in local_command
    assert "python3 -m pip install --user --no-cache-dir ray==2.55.0" not in local_command


def test_cluster_manager_skips_ray_worker_node_for_bridge_profile() -> None:
    popen_calls = []

    def fake_run(cmd, **kwargs):
        return CompletedRun()

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, dict(kwargs)))
        return object()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=fake_popen,
        nodes={
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
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            container_profile="autoware171",
            sync_aw_runtime_monitor=False,
        )
    )

    remote_command = popen_calls[0][0][-1]
    assert "--network bridge" in remote_command
    assert "ray start --address=150.65.227.21:6379" not in remote_command
    assert "--node-ip-address=150.65.227.22" not in remote_command
    assert "--queue-address ray://150.65.227.21:10001" in remote_command
    assert "hostname -I" not in remote_command
    assert "~/AWSIM_launch" not in remote_command
    assert "PYTHONPATH=/opt/awsim_python_deps/py310" in remote_command
    assert (
        "/home/tomita1/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro"
        in remote_command
    )
    assert "/home/tomita1/AWSIM_launch:/home/passd/AWSIM_launch" in remote_command
    assert "/home/tomita1/AWSIMScriptPy:/home/passd/AWSIMScriptPy" in remote_command
    assert "python3 -m pip install --user --no-cache-dir ray==2.55.0" not in remote_command


def test_cluster_manager_starts_autoware180_entrypoint_as_root() -> None:
    manager = ClusterManager(
        nodes={},
        master_ip="150.65.227.108",
        ray_port="6379",
    )
    node = {
        "machine": "21号機",
        "ip": "150.65.227.108",
        "user": "passd",
        "container": {
            "name": "sim_worker_21",
            "ros_domain_id": 21,
            "password": "passd",
            "user": "passd",
            "workspace": "/home/passd",
        },
    }

    command = manager._build_worker_launch_command(
        node=node,
        config=ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="replay",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            container_profile="autoware180_ekfdiagfix",
            scenario_profile="autoware171",
            sync_aw_runtime_monitor=False,
        ),
        head_address="150.65.227.108:6379",
    )

    assert "--user root" in command
    assert "autoware_internal:1.8.0-ekfdiagfix" in command
    assert "PYTHONPATH=/opt/awsim_python_deps/py310" in command
    assert (
        "/home/passd/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro"
        in command
    )


def test_cluster_manager_uses_ray_client_address_for_bridge_shared_store() -> None:
    popen_calls = []

    def fake_run(cmd, **kwargs):
        return CompletedRun()

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, dict(kwargs)))
        return object()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=fake_popen,
        nodes={
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
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="binomial_ci",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            shared_store_actor_name="SharedStoreActor",
                dataset_csv="/tmp/uturn_dataset.csv",
                container_profile="autoware171",
                sync_aw_runtime_monitor=False,
        )
    )

    remote_command = popen_calls[0][0][-1]
    assert "--queue-address ray://150.65.227.21:10001" in remote_command
    assert "--shared-store-address ray://150.65.227.21:10001" in remote_command


def test_cluster_manager_uses_configured_node_ip_for_legacy_profile_ray_worker() -> None:
    popen_calls = []

    def fake_run(cmd, **kwargs):
        return CompletedRun()

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, dict(kwargs)))
        return object()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=fake_popen,
        nodes={
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
            }
        },
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    manager.start_cluster(
        ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="explore",
            output_path="/tmp/records.jsonl",
                queue_actor_name="TaskQueueActor",
                container_profile="legacy",
                sync_aw_runtime_monitor=False,
        )
    )

    remote_command = popen_calls[0][0][-1]
    assert "--network host" in remote_command
    assert "python3 -m pip install --user --no-cache-dir ray==2.55.0" in remote_command
    assert "ray start --address=150.65.227.21:6379" in remote_command
    assert "--node-ip-address=150.65.227.22" in remote_command
    assert "hostname -I" not in remote_command


def test_cluster_worker_is_started_by_process_supervisor() -> None:
    manager = ClusterManager(nodes={}, master_ip="150.65.227.108", ray_port="6379")

    bootstrap = manager._build_container_bootstrap(
        "python3 -u run_worker_v2.py --worker-id worker_21",
        "/home/passd",
        "150.65.227.108:6379",
        "150.65.227.108",
        "passd",
        start_ray_worker_node=False,
        bootstrap_apt_packages=(),
        bootstrap_pip_packages=(),
    )

    assert "exec python3 -u -m runtime.container.supervised_process.server" in bootstrap
    assert "AWSIM_PROCESS_SUPERVISOR_SOCKET=/tmp/awsim-process-supervisor.sock" in bootstrap
    assert "--worker-command 'python3 -u run_worker_v2.py --worker-id worker_21'" in bootstrap


def test_cluster_manager_cleans_up_ray_when_head_start_times_out() -> None:
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, dict(kwargs)))
        if isinstance(cmd, list) and cmd[:2] == ["ray", "start"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5, output="booting")
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        subprocess_popen=lambda *args, **kwargs: object(),
        nodes={},
        master_ip="150.65.227.21",
        ray_port="6379",
    )

    try:
        manager.start_cluster(
            ClusterLaunchConfig(
                case_kind="uturn",
                run_mode="explore",
                output_path="/tmp/records.jsonl",
                queue_actor_name="TaskQueueActor",
                ray_head_start_timeout_sec=5,
            )
        )
    except RuntimeError as exc:
        assert "Timed out while starting Ray head node after 5s" in str(exc)
    else:
        raise AssertionError("expected Ray head timeout to be wrapped")

    assert [cmd for cmd, _kwargs in run_calls if cmd == ["ray", "stop", "--force"]] == [
        ["ray", "stop", "--force"],
        ["ray", "stop", "--force"],
    ]
