from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from contracts.execution import RawRunResult, RunStatus, TestCase
from runtime.container.artifact_watcher import ArtifactWatcher, ArtifactWatcherConfig
from runtime.container.infra_tasks import build_awsim_infra_tasks
from runtime.container.launcher import ContainerLauncher, LaunchRequest
from runtime.container.process_manager import ContainerProcessManager
from runtime.container.profile import ContainerRuntimeProfile, build_runtime_profile
from runtime.container.runner import CommandResult
from runtime.container.supervisor import ContainerSupervisor
from runtime.container.xvfb import XvfbConfig, XvfbController
from targets.awsim.case_kinds import build_default_case_kind_module_name, load_timeout_sec
from targets.awsim.readiness_probe import (
    AWSIMReadinessConfig,
    AWSIMReadinessProbe,
    DEFAULT_REQUIRED_SERVICES,
)


RESERVED_INPUT_KEYS = {
    "fixture_path",
    "expected_trace_path",
    "output_dir",
    "scenario_type",
    "local_loop_num",
    "ext_mode",
}


@dataclass(frozen=True)
class AWSIMBackendConfig:
    runtime_profile: ContainerRuntimeProfile = field(
        default_factory=lambda: build_runtime_profile(case_kind="awsim")
    )
    xvfb_config: XvfbConfig | None = None
    scenario_script: str = "run_scenario.py"
    python_executable: str = "python3"
    output_dir: Path | None = None
    timeout_sec: float | None = None
    poll_interval_sec: float = 2.0
    settle_time_sec: float = 5.0
    post_timeout_grace_sec: float = 10.0
    manage_infra: bool = False
    reuse_infra_between_runs: bool = False
    initial_warmup_sec: float = 0.0
    extra_refresh_warmup_sec: float = 0.0
    infra_ext_mode: str = "cvm"
    include_awchecker: bool = True
    infra_home_dir: Path = Path("/home/passd")
    scenario_log_filename: str = "scenario_runner.log"
    startup_probe_timeout_sec: float = 0.0
    refresh_probe_timeout_sec: float = 0.0
    probe_service_timeout_sec: float = 2.0
    startup_probe_stability_checks: int = 2
    refresh_probe_stability_checks: int = 3
    probe_stability_interval_sec: float = 1.0
    max_refresh_probe_retries: int = 0
    readiness_required_services: tuple[str, ...] = DEFAULT_REQUIRED_SERVICES

class AWSIMBackend:
    def __init__(
        self,
        config: AWSIMBackendConfig | None = None,
        command_runner: Callable[..., CommandResult] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        xvfb_controller: XvfbController | None = None,
        process_manager: ContainerProcessManager | None = None,
        readiness_probe: AWSIMReadinessProbe | None = None,
    ):
        self.config = config or AWSIMBackendConfig()
        self.command_runner = command_runner
        self.sleeper = sleeper or time.sleep
        self.container_launcher = ContainerLauncher(xvfb_controller=xvfb_controller)
        self.process_manager = process_manager or ContainerProcessManager(sleeper=sleeper)
        self.readiness_probe = readiness_probe or AWSIMReadinessProbe()
        self.artifact_watcher = ArtifactWatcher(
            ArtifactWatcherConfig(
                poll_interval_sec=self.config.poll_interval_sec,
                settle_time_sec=self.config.settle_time_sec,
                post_timeout_grace_sec=self.config.post_timeout_grace_sec,
            ),
            monotonic=monotonic,
            sleeper=sleeper,
        )
        self.container_supervisor = ContainerSupervisor(self.artifact_watcher)
        self._persistent_launch = None
        self._infra_started = False
        self._initial_warmup_done = False
        self._last_refresh_reason = ""
        self._next_local_loop_num = 1

    def run(self, test_case: TestCase) -> RawRunResult:
        if "fixture_path" in test_case.input:
            return self._run_fixture(test_case)
        return self._run_simulation(test_case)

    def _run_fixture(self, test_case: TestCase) -> RawRunResult:
        fixture_path = self._resolve_fixture_path(test_case)
        content = fixture_path.read_text(encoding="utf-8").strip()
        status = self._resolve_fixture_status(test_case=test_case, content=content)
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=status,
            evidence={"trace_json": str(fixture_path)},
            meta={
                "source_module": "targets.awsim.backend",
                "path_root": str(fixture_path.parent),
                "backend_mode": "fixture",
            },
        )

    def _run_simulation(self, test_case: TestCase) -> RawRunResult:
        output_dir = self._resolve_output_dir(test_case)
        local_loop_num = self._claim_local_loop_num(test_case)
        global_loop_num = self._resolve_loop_num(
            test_case.meta,
            primary_key="global_loop_num",
            fallback_key="history_loop_num",
        )
        expected_trace_path, local_trace_path = self._resolve_trace_paths(
            test_case,
            output_dir,
            local_loop_num=local_loop_num,
        )
        timeout_sec = self._resolve_timeout_sec(test_case)
        command = self._build_command(test_case)
        env = os.environ.copy()
        env[self.config.runtime_profile.output_env_var] = str(output_dir)
        prepared_launch = self._prepare_launch(command=command, env=env)

        self._cleanup_stale_artifacts(expected_trace_path, local_trace_path)

        status = RunStatus.EXECUTION_ERROR
        client_process_state = "not_started"
        try:
            command_result = CommandResult(returncode=0, stdout="", stderr="")
            client_process = None
            if self.config.manage_infra:
                self._ensure_infra_started(
                    test_case=test_case,
                    output_dir=output_dir,
                    local_loop_num=local_loop_num,
                    prepared_launch=prepared_launch,
                )
                client_process = self.process_manager.launch_client(
                    prepared_launch.command,
                    work_dir=prepared_launch.cwd,
                    source_setup_script=prepared_launch.source_setup_script,
                    output_dir=output_dir,
                    log_filename=self.config.scenario_log_filename,
                    env=prepared_launch.env,
                )
            else:
                command_result = self._run_command(prepared_launch)
                if command_result.returncode != 0:
                    raise RuntimeError(
                        f"AWSIM scenario command failed with return code "
                        f"{command_result.returncode}: {command_result.stderr.strip()}"
                    )

            supervision = self.container_supervisor.wait_for_completion(
                expected_trace_path=expected_trace_path,
                local_trace_path=local_trace_path,
                timeout_sec=timeout_sec,
            )
            trace_json_path = supervision.trace_path
            status = supervision.status
            if supervision.status is RunStatus.SUCCESS:
                self._promote_related_artifacts(
                    expected_trace_path=expected_trace_path,
                    local_trace_path=local_trace_path,
                )
            if client_process is not None:
                process_returncode = self._poll_process_returncode(client_process.process)
                command_result = CommandResult(
                    returncode=process_returncode,
                    stdout="",
                    stderr="",
                )
                client_process_state = (
                    "running_at_watch_deadline"
                    if process_returncode is None
                    else "exited"
                )
                status = self._resolve_runtime_status(
                    supervision_status=supervision.status,
                    process_returncode=process_returncode,
                )
        finally:
            if self.config.manage_infra:
                if self.config.reuse_infra_between_runs and status is RunStatus.SUCCESS:
                    self.process_manager.stop_case_client()
                elif self.config.reuse_infra_between_runs:
                    self._refresh_managed_infra()
                else:
                    self._shutdown_managed_infra()
            else:
                self.container_launcher.close_launch(prepared_launch)

        evidence = {"trace_json": str(trace_json_path)}
        evidence.update(self._collect_optional_artifacts(trace_json_path))
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=status,
            evidence=evidence,
            meta={
                "source_module": "targets.awsim.backend",
                "path_root": str(trace_json_path.parent),
                "backend_mode": "simulation",
                "command": command,
                "returncode": command_result.returncode,
                "client_process_state": client_process_state,
                "stdout": command_result.stdout,
                "stderr": command_result.stderr,
                "timeout_sec": supervision.timeout_sec,
                "artifact_timing": supervision.artifact_timing,
                "post_timeout_grace_sec": self.config.post_timeout_grace_sec,
                "local_loop_num": local_loop_num,
                "global_loop_num": global_loop_num,
            },
        )

    def close(self) -> None:
        self._shutdown_managed_infra()

    def refresh_infra(self, reason: str = "refresh_interval_reached") -> None:
        if not self.config.manage_infra:
            return
        self._last_refresh_reason = reason
        self._refresh_managed_infra()

    def _prepare_launch(self, *, command: list[str], env: Mapping[str, str]):
        if self.config.manage_infra and self.config.reuse_infra_between_runs:
            if self._persistent_launch is None:
                self._persistent_launch = self.container_launcher.prepare_launch(
                    LaunchRequest(command=command, env=dict(env)),
                    runtime_profile=self.config.runtime_profile,
                    xvfb_config=self._resolve_xvfb_config(),
                )
            else:
                self._persistent_launch.command = list(command)
                self._persistent_launch.env.update(dict(env))
            return self._persistent_launch

        return self.container_launcher.prepare_launch(
            LaunchRequest(command=command, env=dict(env)),
            runtime_profile=self.config.runtime_profile,
            xvfb_config=self._resolve_xvfb_config(),
        )

    def _ensure_infra_started(
        self,
        *,
        test_case: TestCase,
        output_dir: Path,
        local_loop_num: int,
        prepared_launch,
    ) -> None:
        if self.config.reuse_infra_between_runs and self._infra_started:
            return
        infra_tasks = self._build_infra_tasks(test_case=test_case, output_dir=output_dir)
        current_is_refresh = self._initial_warmup_done
        max_attempts = max(int(self.config.max_refresh_probe_retries), 0) + 1
        last_probe_result_message = ""

        for attempt_index in range(max_attempts):
            self.process_manager.start_infra_processes(
                infra_tasks,
                sim_num=local_loop_num,
                output_dir=output_dir,
                source_setup_script=prepared_launch.source_setup_script,
                env=prepared_launch.env,
            )
            self._infra_started = True
            self._apply_infra_warmup(is_refresh=current_is_refresh)
            readiness_config = self._build_readiness_config(is_refresh=current_is_refresh)
            if readiness_config is None:
                return

            probe_result = self.readiness_probe.probe(
                cwd=prepared_launch.cwd,
                env=prepared_launch.env,
                source_setup_script=prepared_launch.source_setup_script,
                config=readiness_config,
            )
            if probe_result.ready:
                return

            last_probe_result_message = probe_result.message
            if attempt_index + 1 >= max_attempts:
                raise RuntimeError(
                    "AWSIM readiness probe failed: "
                    f"{probe_result.message or 'service checks did not stabilize'}"
                )
            self._refresh_managed_infra()
            current_is_refresh = True

        raise RuntimeError(
            "AWSIM readiness probe failed: "
            f"{last_probe_result_message or 'service checks did not stabilize'}"
        )

    def _refresh_managed_infra(self) -> None:
        if self.config.manage_infra:
            self.process_manager.refresh_non_resident_infra()
        self._infra_started = False

    def _shutdown_managed_infra(self) -> None:
        if self.config.manage_infra:
            self.process_manager.shutdown_all()
        if self._persistent_launch is not None:
            self.container_launcher.close_launch(self._persistent_launch)
        self._persistent_launch = None
        self._infra_started = False

    def _resolve_xvfb_config(self) -> XvfbConfig:
        if self.config.xvfb_config is not None:
            return self.config.xvfb_config
        return XvfbConfig(enabled=self.config.runtime_profile.headless)

    def _apply_infra_warmup(self, *, is_refresh: bool) -> None:
        if not self._initial_warmup_done:
            if self.config.initial_warmup_sec > 0:
                self.sleeper(self.config.initial_warmup_sec)
            self._initial_warmup_done = True
            return
        if is_refresh and self.config.extra_refresh_warmup_sec > 0:
            self.sleeper(self.config.extra_refresh_warmup_sec)

    def _build_readiness_config(
        self,
        *,
        is_refresh: bool,
    ) -> AWSIMReadinessConfig | None:
        timeout_sec = (
            self.config.refresh_probe_timeout_sec
            if is_refresh
            else self.config.startup_probe_timeout_sec
        )
        if timeout_sec <= 0:
            return None
        stability_checks = (
            self.config.refresh_probe_stability_checks
            if is_refresh
            else self.config.startup_probe_stability_checks
        )
        return AWSIMReadinessConfig(
            required_services=tuple(self.config.readiness_required_services),
            probe_timeout_sec=float(timeout_sec),
            per_service_timeout_sec=float(self.config.probe_service_timeout_sec),
            stability_checks=int(stability_checks),
            stability_interval_sec=float(self.config.probe_stability_interval_sec),
        )

    def _resolve_fixture_path(self, test_case: TestCase) -> Path:
        fixture_value = test_case.input.get("fixture_path")
        if not fixture_value:
            raise ValueError("test_case.input['fixture_path'] is required")

        fixture_path = Path(str(fixture_value)).expanduser().resolve()
        if not fixture_path.exists():
            raise FileNotFoundError(f"AWSIM fixture not found: {fixture_path}")
        return fixture_path

    def _resolve_fixture_status(
        self,
        *,
        test_case: TestCase,
        content: str,
    ) -> RunStatus:
        if content == "TIMEOUT":
            return RunStatus.TIMEOUT
        explicit_status = (
            test_case.input.get("fixture_raw_run_status")
            or test_case.meta.get("fixture_raw_run_status")
        )
        return self._coerce_run_status(explicit_status) or RunStatus.SUCCESS

    @staticmethod
    def _coerce_run_status(value: object) -> RunStatus | None:
        if value is None:
            return None
        if isinstance(value, RunStatus):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return RunStatus(stripped)
            except ValueError as exc:
                raise ValueError(f"Unsupported run status: {value}") from exc
        raise TypeError(f"Run status must be a string or RunStatus, got {type(value).__name__}")

    @staticmethod
    def _resolve_runtime_status(
        *,
        supervision_status: RunStatus,
        process_returncode: int | None,
    ) -> RunStatus:
        if supervision_status is RunStatus.TIMEOUT:
            return RunStatus.TIMEOUT
        # The artifact is the experiment result. Client exit state is evidence
        # only: a trace is evaluated by the checker regardless of its code.
        return supervision_status

    def _resolve_output_dir(self, test_case: TestCase) -> Path:
        explicit_output_dir = test_case.input.get("output_dir")
        configured_output_dir = self.config.output_dir
        selected_output_dir = explicit_output_dir if explicit_output_dir is not None else configured_output_dir
        return self.config.runtime_profile.resolve_output_dir(
            explicit_output_dir=selected_output_dir,
            env=os.environ,
        )

    def _resolve_timeout_sec(self, test_case: TestCase) -> float:
        if self.config.timeout_sec is not None:
            return self.config.timeout_sec

        config_module_name = str(
            test_case.meta.get(
                "config_module",
                build_default_case_kind_module_name(
                    case_kind=test_case.case_kind,
                    scenario_profile=(
                        str(test_case.meta.get("scenario_profile"))
                        if test_case.meta.get("scenario_profile") is not None
                        else None
                    ),
                ),
            )
        )
        return load_timeout_sec(
            case_kind=test_case.case_kind,
            module_name=config_module_name,
            default=200.0,
        )

    def _resolve_trace_paths(
        self,
        test_case: TestCase,
        output_dir: Path,
        *,
        local_loop_num: int,
    ) -> tuple[Path, Path]:
        global_loop_num = self._resolve_loop_num(
            test_case.meta,
            primary_key="global_loop_num",
            fallback_key="history_loop_num",
        )
        eval_loop_num = global_loop_num or local_loop_num or 1
        test_loop_num = local_loop_num

        explicit_trace_path = test_case.input.get("expected_trace_path")
        if explicit_trace_path:
            expected_trace_path = Path(str(explicit_trace_path)).expanduser().resolve()
            local_trace_path = output_dir / f"{test_case.case_kind}_test_sim{test_loop_num}.json"
            return expected_trace_path, local_trace_path

        expected_trace_path = output_dir / f"{test_case.case_kind}_eval_sim{eval_loop_num}.json"
        local_trace_path = output_dir / f"{test_case.case_kind}_test_sim{test_loop_num}.json"
        return expected_trace_path, local_trace_path

    def _claim_local_loop_num(self, test_case: TestCase) -> int:
        explicit_loop_num = self._resolve_loop_num(
            test_case.input,
            primary_key="local_loop_num",
            fallback_key="local_loop_num",
        )
        if explicit_loop_num is None:
            explicit_loop_num = self._resolve_loop_num(
                test_case.meta,
                primary_key="local_loop_num",
                fallback_key="local_loop_num",
            )

        if explicit_loop_num is not None and explicit_loop_num > 0:
            self._next_local_loop_num = max(
                self._next_local_loop_num,
                explicit_loop_num + 1,
            )
            return explicit_loop_num

        local_loop_num = self._next_local_loop_num
        self._next_local_loop_num += 1
        return local_loop_num

    def _build_infra_tasks(
        self,
        *,
        test_case: TestCase,
        output_dir: Path,
    ):
        return build_awsim_infra_tasks(
            case_kind=test_case.case_kind,
            output_dir=output_dir,
            ext_mode=str(test_case.input.get("ext_mode", self.config.infra_ext_mode)),
            include_awchecker=self.config.include_awchecker,
            runtime_profile=self.config.runtime_profile,
        )

    def _build_command(self, test_case: TestCase) -> list[str]:
        scenario_type = str(test_case.input.get("scenario_type", test_case.case_kind))
        command = [
            self.config.python_executable,
            self.config.scenario_script,
            "--type",
            scenario_type,
        ]
        scenario_profile = test_case.meta.get("scenario_profile")
        if scenario_profile:
            command.extend(["--scenario-profile", str(scenario_profile)])
        config_module = test_case.meta.get("config_module")
        default_config_module = build_default_case_kind_module_name(
            case_kind=test_case.case_kind,
            scenario_profile=str(scenario_profile) if scenario_profile is not None else None,
        )
        if config_module and str(config_module) != default_config_module:
            command.extend(["--config-module", str(config_module)])
        for key, value in self._dynamic_params(test_case.input).items():
            command.extend([f"--{key}", self._serialize_param_value(value)])
        return command

    def _dynamic_params(self, input_values: Mapping[str, object]) -> dict[str, object]:
        params: dict[str, object] = {}
        for key, value in input_values.items():
            if key in RESERVED_INPUT_KEYS:
                continue
            params[key] = value
        return params

    def _collect_optional_artifacts(self, trace_json_path: Path) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        trace_name = trace_json_path.name
        if trace_name.endswith(".json"):
            prefix = trace_name[:-5]
            footage_prefix = trace_json_path.parent / f"{prefix}_footage"
            for extension, artifact_key in (
                (".mp4", "video"),
                (".meta.json", "video_meta_json"),
            ):
                artifact_path = Path(str(footage_prefix) + extension)
                if artifact_path.exists():
                    artifacts[artifact_key] = str(artifact_path)
        return artifacts

    def _cleanup_stale_artifacts(
        self,
        expected_trace_path: Path,
        local_trace_path: Path,
    ) -> None:
        for trace_path in {expected_trace_path, local_trace_path}:
            if trace_path.exists():
                trace_path.unlink()
            self._delete_optional_artifacts(trace_path)

    def _delete_optional_artifacts(self, trace_path: Path) -> None:
        for artifact_path in self._optional_artifact_paths(trace_path).values():
            if artifact_path.exists():
                artifact_path.unlink()

    def _promote_related_artifacts(
        self,
        *,
        expected_trace_path: Path,
        local_trace_path: Path,
    ) -> None:
        if expected_trace_path == local_trace_path:
            return
        local_artifacts = self._optional_artifact_paths(local_trace_path)
        expected_artifacts = self._optional_artifact_paths(expected_trace_path)
        for artifact_key, local_artifact_path in local_artifacts.items():
            if not local_artifact_path.exists():
                continue
            expected_artifact_path = expected_artifacts[artifact_key]
            expected_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            if expected_artifact_path.exists():
                expected_artifact_path.unlink()
            local_artifact_path.replace(expected_artifact_path)

    def _optional_artifact_paths(self, trace_json_path: Path) -> dict[str, Path]:
        trace_name = trace_json_path.name
        if not trace_name.endswith(".json"):
            return {}
        prefix = trace_name[:-5]
        footage_prefix = trace_json_path.parent / f"{prefix}_footage"
        return {
            "video": Path(str(footage_prefix) + ".mp4"),
            "video_meta_json": Path(str(footage_prefix) + ".meta.json"),
        }

    def _resolve_loop_num(
        self,
        values: Mapping[str, object],
        *,
        primary_key: str,
        fallback_key: str,
    ) -> int | None:
        for key in (primary_key, fallback_key):
            value = values.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    @staticmethod
    def _serialize_param_value(value: object) -> str:
        if isinstance(value, Path):
            return str(value)
        return str(value)

    def _run_command(
        self,
        prepared_launch,
    ) -> CommandResult:
        if self.command_runner is not None:
            return self.command_runner(
                prepared_launch.command,
                cwd=prepared_launch.cwd,
                env=prepared_launch.env,
                source_setup_script=prepared_launch.source_setup_script,
            )
        return self.container_launcher.launch_prepared(prepared_launch)

    @staticmethod
    def _poll_process_returncode(process: object) -> int | None:
        poll = getattr(process, "poll", None)
        if not callable(poll):
            return 0
        returncode = poll()
        return None if returncode is None else int(returncode)
