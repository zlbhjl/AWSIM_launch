import pytest

from runtime.container.profile import (
    SUPPORTED_CONTAINER_PROFILES,
    resolve_container_launch_profile,
)


AWSIM_PROFILES = (
    "legacy",
    "autoware171",
    "autoware180",
    "autoware180_ekfdiagfix",
)


@pytest.mark.parametrize("profile_name", AWSIM_PROFILES)
def test_existing_profiles_keep_awsim_capability_defaults(profile_name: str) -> None:
    profile = resolve_container_launch_profile(profile_name)

    assert profile is not None
    assert profile.target == "awsim"
    assert profile.requires_gpu is True
    assert profile.requires_ros is True
    assert profile.requires_runtime_monitor is True


def test_prism_maude_profile_declares_cpu_only_capabilities() -> None:
    profile = resolve_container_launch_profile("prism_maude")

    assert profile is not None
    assert profile.name == "prism_maude"
    assert profile.image == "awsim-launch/prism-maude:0.1.0"
    assert profile.target == "prism"
    assert profile.requires_gpu is False
    assert profile.requires_ros is False
    assert profile.requires_runtime_monitor is False
    assert profile.container_name_template == "prism_worker_{ros_domain_id}"
    assert profile.docker_user == "coder"
    assert profile.network_mode == "bridge"
    assert profile.privileged is False
    assert profile.mounts == ()


def test_prism_maude_is_a_supported_container_profile() -> None:
    assert "prism_maude" in SUPPORTED_CONTAINER_PROFILES
