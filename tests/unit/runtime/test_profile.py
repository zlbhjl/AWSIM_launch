from pathlib import Path

from runtime.container.profile import (
    DEFAULT_CONTAINER_OUTPUT_DIR,
    DEFAULT_HOST_OUTPUT_DIR,
    ContainerRuntimeProfile,
    build_runtime_profile,
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
