from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_LAUNCH_DIR = Path(__file__).resolve().parents[2]
DEFAULT_HOME_DIR = Path("/home/passd")
DEFAULT_SETUP_BASH = Path("/home/passd/autoware/install/setup.bash")
DEFAULT_CONTAINER_OUTPUT_DIR = Path.home() / "simulation_traces"
DEFAULT_HOST_OUTPUT_DIR = Path.home() / "simulation_traces_host"
DEFAULT_AWSIM_COMMAND = "./awsim_labs.x86_64 -noise false"
DEFAULT_BASH_ARGS = ("-i", "-c")
DEFAULT_AUTOWARE_COMMAND = (
    "ros2 launch autoware_launch e2e_simulator.launch.xml "
    "vehicle_model:=awsim_labs_vehicle "
    "sensor_model:=awsim_labs_sensor_kit "
    f"map_path:={DEFAULT_HOME_DIR}/autoware_map/nishishinjuku_autoware_map "
    "launch_vehicle_interface:=true"
)
DISABLE_RVIZ_ARG = "rviz:=false"
DEFAULT_CLUSTER_WORKER_ENV = (
    ("DISPLAY", ":99"),
    ("VK_ICD_FILENAMES", "/usr/share/vulkan/icd.d/nvidia_icd.json"),
)
SUPPORTED_CONTAINER_PROFILES = (
    "legacy",
    "autoware171",
    "autoware180",
    "autoware180_ekfdiagfix",
    "prism_maude",
)


@dataclass(frozen=True)
class ContainerLaunchProfile:
    name: str
    image: str
    # Execution capabilities are consumed by cluster/container orchestration.
    # AWSIM-compatible defaults preserve every existing profile unchanged.
    target: str = "awsim"
    requires_gpu: bool = True
    requires_ros: bool = True
    requires_runtime_monitor: bool = True
    container_name_template: str | None = None
    docker_user: str | None = None
    network_mode: str = "host"
    privileged: bool = True
    start_ray_worker_node: bool = True
    add_host_gateway: bool = False
    env: tuple[tuple[str, str], ...] = ()
    mounts: tuple[str, ...] = ()
    docker_run_args: tuple[str, ...] = ()
    bash_args: tuple[str, ...] = DEFAULT_BASH_ARGS
    bootstrap_apt_packages: tuple[str, ...] = ("xvfb",)
    bootstrap_pip_packages: tuple[str, ...] = ("ray==2.55.0",)
    worker_env: tuple[tuple[str, str], ...] = DEFAULT_CLUSTER_WORKER_ENV


@dataclass(frozen=True)
class ContainerRuntimeProfile:
    case_kind: str = "generic"
    machine_role: str = "local"
    headless: bool = False
    host_mode: bool = False
    container_profile: str | None = None
    scenario_profile: str | None = None
    launch_dir: Path = DEFAULT_LAUNCH_DIR
    home_dir: Path = DEFAULT_HOME_DIR
    source_setup_script: Path | None = DEFAULT_SETUP_BASH
    output_env_var: str = "AW_OUTPUT_DIR"
    default_output_dir: Path = DEFAULT_CONTAINER_OUTPUT_DIR
    awsim_command: str = DEFAULT_AWSIM_COMMAND
    autoware_command: str = DEFAULT_AUTOWARE_COMMAND
    awsim_delay_sec: float = 15.0
    autoware_delay_sec: float = 90.0
    runtime_monitor_delay_sec: float = 5.0
    runtime_monitor_dirname: str = "AW-Runtime-Monitor"
    awsim_dirname: str = "awsim_labs"
    autoware_dirname: str = "autoware"
    container_launch_profile: ContainerLaunchProfile | None = None

    def resolve_output_dir(
        self,
        *,
        explicit_output_dir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Path:
        if explicit_output_dir is not None:
            return Path(explicit_output_dir).expanduser().resolve()

        resolved_env = env or os.environ
        env_value = resolved_env.get(self.output_env_var)
        if env_value:
            return Path(env_value).expanduser().resolve()

        return self.default_output_dir.expanduser().resolve()

    @property
    def awsim_dir(self) -> Path:
        return self.home_dir / self.awsim_dirname

    @property
    def autoware_dir(self) -> Path:
        return self.home_dir / self.autoware_dirname

    @property
    def runtime_monitor_dir(self) -> Path:
        return self.home_dir / self.runtime_monitor_dirname


def build_runtime_profile(
    *,
    case_kind: str = "generic",
    machine_role: str = "local",
    headless: bool = False,
    host_mode: bool = False,
    container_profile: str | None = None,
    scenario_profile: str | None = None,
    launch_dir: str | Path | None = None,
    home_dir: str | Path | None = None,
    source_setup_script: str | Path | None = DEFAULT_SETUP_BASH,
    output_env_var: str = "AW_OUTPUT_DIR",
    awsim_command: str = DEFAULT_AWSIM_COMMAND,
    autoware_command: str = DEFAULT_AUTOWARE_COMMAND,
    awsim_delay_sec: float = 15.0,
    autoware_delay_sec: float | None = None,
    runtime_monitor_delay_sec: float = 5.0,
) -> ContainerRuntimeProfile:
    resolved_container_launch_profile = resolve_container_launch_profile(container_profile)
    default_output_dir = DEFAULT_HOST_OUTPUT_DIR if host_mode else DEFAULT_CONTAINER_OUTPUT_DIR
    resolved_launch_dir = (
        Path(launch_dir).expanduser().resolve()
        if launch_dir is not None
        else DEFAULT_LAUNCH_DIR
    )
    resolved_home_dir = (
        Path(home_dir).expanduser().resolve()
        if home_dir is not None
        else DEFAULT_HOME_DIR
    )
    resolved_setup_script = (
        Path(source_setup_script).expanduser().resolve()
        if source_setup_script is not None
        else None
    )
    resolved_autoware_delay_sec = (
        float(autoware_delay_sec)
        if autoware_delay_sec is not None
        else (40.0 if machine_role == "master" else 90.0)
    )
    return ContainerRuntimeProfile(
        case_kind=case_kind,
        machine_role=machine_role,
        headless=headless,
        host_mode=host_mode,
        container_profile=container_profile,
        scenario_profile=scenario_profile,
        launch_dir=resolved_launch_dir,
        home_dir=resolved_home_dir,
        source_setup_script=resolved_setup_script,
        output_env_var=output_env_var,
        default_output_dir=default_output_dir,
        awsim_command=awsim_command,
        autoware_command=_resolve_autoware_command(
            autoware_command=autoware_command,
            headless=headless,
        ),
        awsim_delay_sec=float(awsim_delay_sec),
        autoware_delay_sec=resolved_autoware_delay_sec,
        runtime_monitor_delay_sec=float(runtime_monitor_delay_sec),
        container_launch_profile=resolved_container_launch_profile,
    )


def _resolve_autoware_command(*, autoware_command: str, headless: bool) -> str:
    if not headless or _has_launch_arg(autoware_command, "rviz:="):
        return autoware_command
    return f"{autoware_command} {DISABLE_RVIZ_ARG}"


def _has_launch_arg(command: str, prefix: str) -> bool:
    return any(part.startswith(prefix) for part in command.split())


def resolve_container_launch_profile(
    profile_name: str | None,
) -> ContainerLaunchProfile | None:
    if profile_name is None:
        return None
    if profile_name not in SUPPORTED_CONTAINER_PROFILES:
        raise ValueError(f"Unsupported container profile: {profile_name}")
    module = importlib.import_module(f"runtime.container.profiles.{profile_name}")
    profile = getattr(module, "CONTAINER_LAUNCH_PROFILE", None)
    if not isinstance(profile, ContainerLaunchProfile):
        raise TypeError(
            f"runtime.container.profiles.{profile_name} must export CONTAINER_LAUNCH_PROFILE"
        )
    return profile
