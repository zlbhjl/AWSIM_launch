from __future__ import annotations

from runtime.cluster.cluster_manager import ClusterLaunchConfig, ClusterManager


class CompletedRun:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _node(*, ip: str = "150.65.227.22") -> dict[str, object]:
    return {
        "machine": "22号機",
        "ip": ip,
        "user": "tomita1",
        "enabled": True,
        "container": {
            "name": "sim_worker_22",
            "ros_domain_id": 22,
            "password": "passd",
            "user": "passd",
            "workspace": "/home/passd",
            "image": "must-not-be-used",
        },
    }


def _config(**overrides: object) -> ClusterLaunchConfig:
    values: dict[str, object] = {
        "case_kind": "simple_reliability_dtmc",
        "run_mode": "explore",
        "output_path": "/tmp/records.jsonl",
        "queue_actor_name": "TaskQueueActor",
        "target": "prism",
        "container_profile": "prism_maude",
    }
    values.update(overrides)
    return ClusterLaunchConfig(**values)


def test_prism_launch_uses_cpu_only_worker_on_the_shared_ray_queue() -> None:
    node = _node()
    manager = ClusterManager(nodes={"worker": node}, master_ip="150.65.227.21")

    command = manager._build_worker_launch_command(
        node=node,
        config=_config(worker_gpu_health_check=True),
        head_address="150.65.227.21:6379",
    )

    assert "awsim-launch/prism-maude:0.1.0" in command
    assert "docker rm -f prism_worker_22" in command
    assert "docker rm -f sim_worker_22" not in command
    assert "--target prism" in command
    assert "--container-profile prism_maude" in command
    assert "--queue-actor-name TaskQueueActor" in command
    assert "--queue-address ray://150.65.227.21:10001" in command
    assert "--queue-namespace awsim_cluster" in command
    assert "--gpus all" not in command
    assert "--worker-gpu-health-check" not in command
    assert "ROS_DOMAIN_ID" not in command
    assert "Xvfb" not in command
    assert "AW-Runtime-Monitor" not in command
    assert "AWSIMScriptPy" not in command
    assert "autoware_map" not in command
    assert "/home/tomita1/AWSIM_launch:/home/passd/AWSIM_launch" in command
    assert (
        "/home/tomita1/simulation_traces_prism_worker_22:"
        "/home/passd/simulation_traces"
    ) in command


def test_prism_head_does_not_pin_cpu_count_so_ray_client_can_initialize() -> None:
    calls: list[object] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        nodes={},
        master_ip="150.65.227.21",
    )

    manager.start_head(_config())

    ray_start_command = next(
        command
        for command in calls
        if isinstance(command, list) and command[:2] == ["ray", "start"]
    )
    assert not any(str(arg).startswith("--num-cpus") for arg in ray_start_command)


def test_prism_syncs_only_framework_sources_even_when_awsim_sync_flags_are_set() -> None:
    specs = ClusterManager._build_sync_specs(
        _config(
            sync_awsim_script_py=True,
            sync_aw_runtime_monitor=True,
            sync_autoware180_map=True,
        )
    )

    assert [spec.label for spec in specs] == ["AWSIM_launch"]


def test_prism_launch_skips_gpu_preflight_entirely() -> None:
    calls: list[object] = []
    node = _node(ip="150.65.227.21")

    def fake_run(command, **_kwargs):
        calls.append(command)
        return CompletedRun()

    manager = ClusterManager(
        subprocess_run=fake_run,
        nodes={"worker": node},
        master_ip="150.65.227.21",
    )

    summary = manager.launch_workers(
        _config(worker_launch_stagger_sec=0),
        head_address="150.65.227.21:6379",
    )

    assert summary.launched_workers == ("22号機",)
    assert summary.preflight_failures == ()
    assert not any("nvidia-smi" in str(command) for command in calls)


def test_target_and_container_profile_must_match() -> None:
    manager = ClusterManager(nodes={"worker": _node()}, master_ip="150.65.227.21")

    try:
        manager._build_worker_launch_command(
            node=_node(),
            config=_config(target="awsim"),
            head_address="150.65.227.21:6379",
        )
    except ValueError as exc:
        assert "not 'awsim'" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("target/profile mismatch must be rejected")
