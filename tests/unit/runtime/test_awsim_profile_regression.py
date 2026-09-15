from pathlib import Path

import pytest

from runtime.cluster.cluster_manager import ClusterLaunchConfig, ClusterManager
from runtime.container.profile import (
    DEFAULT_AUTOWARE_COMMAND,
    DEFAULT_AWSIM_COMMAND,
    DEFAULT_SETUP_BASH,
    build_runtime_profile,
    resolve_container_launch_profile,
)
from verifiers.maude.backend import DEFAULT_TOOL_DIR, DEFAULT_VENV_PYTHON, MaudeBackendConfig, build_command


EXPECTED_PROFILES = {
    "legacy": {
        "image": "autoware_internal:2026",
        "docker_user": None,
        "network_mode": "host",
        "privileged": True,
        "start_ray_worker_node": True,
        "add_host_gateway": False,
        "required_mounts": (),
    },
    "autoware171": {
        "image": "autoware_internal:2026-1.7.1-x11-verified-20260808",
        "docker_user": None,
        "network_mode": "bridge",
        "privileged": False,
        "start_ray_worker_node": False,
        "add_host_gateway": True,
        "required_mounts": (
            "{host_home}/aw-cheaker:{workspace}/aw-cheaker",
            "{host_home}/AWSIMScriptPy:{workspace}/AWSIMScriptPy",
            "{host_home}/AW-Runtime-Monitor:{workspace}/AW-Runtime-Monitor",
            "{host_home}/autoware_map:{workspace}/autoware_map",
        ),
    },
    "autoware180": {
        "image": "autoware_internal:2026-1.8.0-awsim-expmods-v1",
        "docker_user": "root",
        "network_mode": "bridge",
        "privileged": False,
        "start_ray_worker_node": False,
        "add_host_gateway": True,
        "required_mounts": (
            "{host_home}/aw-cheaker:{workspace}/aw-cheaker",
            "{host_home}/AWSIMScriptPy:{workspace}/AWSIMScriptPy",
            "{host_home}/AW-Runtime-Monitor:{workspace}/AW-Runtime-Monitor",
            "{host_home}/autoware180_runtime/maps:{workspace}/autoware_map",
            "{host_home}/autoware180_runtime/ml_models:{workspace}/autoware_data/ml_models",
        ),
    },
    "autoware180_ekfdiagfix": {
        "image": "autoware_internal:1.8.0-ekfdiagfix",
        "docker_user": "root",
        "network_mode": "bridge",
        "privileged": False,
        "start_ray_worker_node": False,
        "add_host_gateway": True,
        "required_mounts": (
            "{host_home}/aw-cheaker:{workspace}/aw-cheaker",
            "{host_home}/AWSIMScriptPy:{workspace}/AWSIMScriptPy",
            "{host_home}/AW-Runtime-Monitor:{workspace}/AW-Runtime-Monitor",
            "{host_home}/autoware180_runtime/maps:{workspace}/autoware_map",
            "{host_home}/autoware180_runtime/ml_models:{workspace}/autoware_data/ml_models",
        ),
    },
}


@pytest.mark.parametrize("profile_name", EXPECTED_PROFILES)
def test_awsim_container_profile_regression(profile_name: str) -> None:
    expected = EXPECTED_PROFILES[profile_name]
    profile = resolve_container_launch_profile(profile_name)

    assert profile is not None
    for field in (
        "image",
        "docker_user",
        "network_mode",
        "privileged",
        "start_ray_worker_node",
        "add_host_gateway",
    ):
        assert getattr(profile, field) == expected[field]
    for mount in expected["required_mounts"]:
        assert mount in profile.mounts


@pytest.mark.parametrize("profile_name", EXPECTED_PROFILES)
def test_awsim_commands_do_not_change_with_container_profile(profile_name: str) -> None:
    runtime = build_runtime_profile(case_kind="uturn", container_profile=profile_name)

    assert runtime.awsim_command == DEFAULT_AWSIM_COMMAND
    assert runtime.autoware_command == DEFAULT_AUTOWARE_COMMAND
    assert runtime.source_setup_script == DEFAULT_SETUP_BASH


@pytest.mark.parametrize("profile_name", EXPECTED_PROFILES)
def test_cluster_launch_keeps_awsim_gpu_and_security_settings(profile_name: str) -> None:
    expected = EXPECTED_PROFILES[profile_name]
    node = {
        "machine": "21号機",
        "ip": "150.65.227.21",
        "user": "passd",
        "container": {
            "name": "sim_worker_21",
            "ros_domain_id": 21,
            "password": "passd",
            "user": "passd",
            "workspace": "/home/passd",
            "image": "node-default:latest",
        },
    }
    manager = ClusterManager(nodes={"master": node}, master_ip="150.65.227.21")
    command = manager._build_worker_launch_command(
        node=node,
        config=ClusterLaunchConfig(
            case_kind="uturn",
            run_mode="binomial_ci",
            output_path="/tmp/records.jsonl",
            queue_actor_name="TaskQueueActor",
            container_profile=profile_name,
            sync_aw_runtime_monitor=False,
        ),
        head_address="150.65.227.21:6379",
    )

    assert expected["image"] in command
    assert "--gpus all" in command
    assert f"--network {expected['network_mode']}" in command
    expected_user = expected["docker_user"] or "passd"
    assert f"--user {expected_user}" in command
    assert ("--privileged" in command) is expected["privileged"]


def test_awsim_awchecker_regression_uses_mounted_tool_and_its_venv() -> None:
    trace = Path("/tmp/uturn_eval_sim1.json")
    python_path = DEFAULT_TOOL_DIR / DEFAULT_VENV_PYTHON
    command = build_command(
        trace,
        formulas=["[] collisionFree"],
        config=MaudeBackendConfig(python_executable=str(python_path)),
    )

    assert DEFAULT_TOOL_DIR == Path("/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
    assert command == [
        str(python_path),
        "aw_checkerpy.py",
        str(trace.resolve()),
        "[] collisionFree",
    ]
