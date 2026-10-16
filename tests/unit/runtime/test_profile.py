from pathlib import Path

from runtime.container.profile import (
    DEFAULT_CONTAINER_OUTPUT_DIR,
    DEFAULT_HOST_OUTPUT_DIR,
    ContainerRuntimeProfile,
    build_runtime_profile,
    resolve_container_launch_profile,
)


def test_build_runtime_profile_uses_host_output_dir_in_host_mode() -> None:
    profile = build_runtime_profile(case_kind="uturn", host_mode=True)

    assert profile.case_kind == "uturn"
    assert profile.host_mode is True
    assert profile.default_output_dir == DEFAULT_HOST_OUTPUT_DIR


def test_build_runtime_profile_uses_container_output_dir_by_default() -> None:
    profile = build_runtime_profile(case_kind="uturn")

    assert profile.host_mode is False
    assert profile.default_output_dir == DEFAULT_CONTAINER_OUTPUT_DIR
    assert "rviz:=false" not in profile.autoware_command


def test_build_runtime_profile_disables_rviz_when_headless() -> None:
    profile = build_runtime_profile(case_kind="uturn", headless=True)

    assert profile.headless is True
    assert "rviz:=false" in profile.autoware_command


def test_build_runtime_profile_keeps_explicit_rviz_setting() -> None:
    profile = build_runtime_profile(
        case_kind="uturn",
        headless=True,
        autoware_command="ros2 launch autoware_launch e2e_simulator.launch.xml rviz:=true",
    )

    assert profile.autoware_command.endswith("rviz:=true")
    assert "rviz:=false" not in profile.autoware_command


def test_build_runtime_profile_uses_master_autoware_delay_and_runtime_dirs() -> None:
    profile = build_runtime_profile(
        case_kind="uturn",
        machine_role="master",
        home_dir="/tmp/home",
    )

    assert profile.autoware_delay_sec == 40.0
    assert profile.awsim_dir == Path("/tmp/home/awsim_labs").resolve()
    assert profile.autoware_dir == Path("/tmp/home/autoware").resolve()
    assert profile.runtime_monitor_dir == Path("/tmp/home/AW-Runtime-Monitor").resolve()


def test_container_runtime_profile_resolve_output_dir_prefers_explicit_path() -> None:
    profile = ContainerRuntimeProfile(default_output_dir=Path("/tmp/default"))

    resolved = profile.resolve_output_dir(
        explicit_output_dir="/tmp/explicit",
        env={"AW_OUTPUT_DIR": "/tmp/from-env"},
    )

    assert resolved == Path("/tmp/explicit").resolve()


def test_container_runtime_profile_resolve_output_dir_uses_env_before_default() -> None:
    profile = ContainerRuntimeProfile(default_output_dir=Path("/tmp/default"))

    resolved = profile.resolve_output_dir(env={"AW_OUTPUT_DIR": "/tmp/from-env"})

    assert resolved == Path("/tmp/from-env").resolve()


def test_build_runtime_profile_attaches_named_container_profile() -> None:
    profile = build_runtime_profile(case_kind="uturn", container_profile="autoware171")

    assert profile.container_profile == "autoware171"
    assert profile.container_launch_profile is not None
    assert profile.container_launch_profile.network_mode == "bridge"


def test_resolve_container_launch_profile_uses_legacy_defaults() -> None:
    profile = resolve_container_launch_profile("legacy")

    assert profile is not None
    assert profile.image == "autoware_internal:2026"


def test_resolve_container_launch_profile_uses_autoware180_defaults() -> None:
    profile = resolve_container_launch_profile("autoware180")

    assert profile is not None
    assert profile.image == "autoware_internal:2026-1.8.0-awsim-expmods-v1"
    assert profile.docker_user == "root"
    assert profile.network_mode == "bridge"
    assert profile.privileged is False
    assert ("PYTHONPATH", "/opt/awsim_python_deps/py310") in profile.env
    assert (
        "{host_home}/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro"
        in profile.mounts
    )
    assert (
        "{host_home}/autoware180_runtime/maps:{workspace}/autoware_map"
        in profile.mounts
    )
    assert (
        "{host_home}/autoware180_runtime/ml_models:{workspace}/autoware_data/ml_models"
        in profile.mounts
    )


def test_resolve_container_launch_profile_uses_autoware180_ekfdiagfix_defaults() -> None:
    profile = resolve_container_launch_profile("autoware180_ekfdiagfix")

    assert profile is not None
    assert profile.image == "autoware_internal:1.8.0-ekfdiagfix"
    assert profile.docker_user == "root"
    assert profile.network_mode == "bridge"
    assert profile.privileged is False
    assert ("PYTHONPATH", "/opt/awsim_python_deps/py310") in profile.env
    assert (
        "{host_home}/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro"
        in profile.mounts
    )
    assert (
        "{host_home}/autoware180_runtime/maps:{workspace}/autoware_map"
        in profile.mounts
    )
    assert (
        "{host_home}/autoware180_runtime/ml_models:{workspace}/autoware_data/ml_models"
        in profile.mounts
    )
