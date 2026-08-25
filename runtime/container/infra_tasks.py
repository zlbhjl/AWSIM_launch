from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from runtime.container.profile import (
    ContainerRuntimeProfile,
    DEFAULT_AUTOWARE_COMMAND,
    DEFAULT_AWSIM_COMMAND,
    DEFAULT_HOME_DIR,
)


@dataclass(frozen=True)
class InfraTask:
    name: str
    work_dir: Path
    command: str
    delay_sec: float = 2.0
    source_setup: bool = False
    resident: bool = False
    log_filename: str | None = None


def build_awsim_infra_tasks(
    *,
    case_kind: str,
    output_dir: str | Path,
    launch_dir: str | Path | None = None,
    ext_mode: str = "cvm",
    include_awchecker: bool = True,
    runtime_profile: ContainerRuntimeProfile | None = None,
    home_dir: str | Path = DEFAULT_HOME_DIR,
    is_master: bool = False,
) -> list[InfraTask]:
    resolved_output_dir = Path(output_dir).expanduser().resolve()
    if runtime_profile is None:
        resolved_home_dir = Path(home_dir).expanduser().resolve()
        resolved_launch_dir = (
            Path(launch_dir).expanduser().resolve()
            if launch_dir is not None
            else Path.cwd().resolve()
        )
        awsim_dir = resolved_home_dir / "awsim_labs"
        autoware_dir = resolved_home_dir / "autoware"
        runtime_monitor_dir = resolved_home_dir / "AW-Runtime-Monitor"
        awsim_command = DEFAULT_AWSIM_COMMAND
        autoware_command = DEFAULT_AUTOWARE_COMMAND
        awsim_delay_sec = 15.0
        autoware_delay_sec = 40.0 if is_master else 90.0
        runtime_monitor_delay_sec = 5.0
    else:
        resolved_launch_dir = runtime_profile.launch_dir
        awsim_dir = runtime_profile.awsim_dir
        autoware_dir = runtime_profile.autoware_dir
        runtime_monitor_dir = runtime_profile.runtime_monitor_dir
        awsim_command = runtime_profile.awsim_command
        autoware_command = runtime_profile.autoware_command
        awsim_delay_sec = runtime_profile.awsim_delay_sec
        autoware_delay_sec = runtime_profile.autoware_delay_sec
        runtime_monitor_delay_sec = runtime_profile.runtime_monitor_delay_sec

    runtime_monitor_output_prefix = resolved_output_dir / f"{case_kind}_test"

    tasks = [
        InfraTask(
            name="AWSIM Labs",
            work_dir=awsim_dir,
            command=awsim_command,
            delay_sec=awsim_delay_sec,
            log_filename="awsim.log",
        ),
        InfraTask(
            name="Autoware",
            work_dir=autoware_dir,
            command=autoware_command,
            delay_sec=autoware_delay_sec,
            source_setup=True,
            log_filename="autoware.log",
        ),
        InfraTask(
            name="Runtime Monitor",
            work_dir=runtime_monitor_dir,
            command=(
                f"python3 main.py -o {runtime_monitor_output_prefix} "
                "-n {sim_num}"
            ),
            delay_sec=runtime_monitor_delay_sec,
            source_setup=True,
            log_filename="runtime_monitor.log",
        ),
    ]
    if include_awchecker:
        tasks.append(
            InfraTask(
                name="AW Checker (Safety Evaluator)",
                work_dir=resolved_launch_dir,
                command=f"python3 awchecker.py --type {case_kind} --ext_mode {ext_mode}",
                resident=True,
                log_filename="awchecker_error.log",
            )
        )
    return tasks
