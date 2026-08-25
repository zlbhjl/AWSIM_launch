from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostWorkerConfig:
    case_kind: str
    run_mode: str
    output_path: str
    queue_actor_name: str
    queue_namespace: str | None = "awsim_cluster"
    queue_address: str | None = None
    config_module: str | None = None
    dataset_csv: str | None = None
    path_root: str | None = None
    history_path: str | None = None
    refresh_interval: int | None = 10
    focus_points: list[dict[str, object]] | None = None
    dkw_bounds: dict[str, object] | None = None
    dkw_region: str = "custom"
    dkw_pure_smc: bool = False
    dkw_simultaneous: bool = False
    ext_mode: str = "cvm"
    headless: bool = False
    shared_store_actor_name: str | None = None
    shared_store_namespace: str | None = "awsim_cluster"
    shared_store_address: str | None = None
    restart_on_refresh: bool = True
    worker_id: str = "worker_v2_host"
    log_dir: str = "~/simulation_traces_host"


class HostWorkerManager:
    def __init__(
        self,
        *,
        popen_factory=None,
    ) -> None:
        self.popen_factory = popen_factory or subprocess.Popen
        self.proc = None
        self.log_path: str | None = None
        self._log_handle = None

    def start(self, config: HostWorkerConfig) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return

        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = "21"
        env["EXEC_MODE"] = "host"
        if config.queue_address:
            env["RAY_ADDRESS"] = config.queue_address

        resolved_log_dir = Path(config.log_dir).expanduser()
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = str((resolved_log_dir / "host_worker_console.log").resolve())
        self._log_handle = open(self.log_path, "w", encoding="utf-8")

        cmd = self._build_command(config)
        self.proc = self.popen_factory(
            cmd,
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )

    def stop(self, timeout: float = 5.0) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self._close_log()
            return

        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        finally:
            self.proc = None
            self._close_log()

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        if self.proc is None:
            return None
        return self.proc.poll()

    @staticmethod
    def _build_command(config: HostWorkerConfig) -> list[str]:
        command = [
            "python3",
            "-u",
            "run_worker_v2.py",
            "--queue-actor-name",
            config.queue_actor_name,
            "--output",
            config.output_path,
            "--worker-id",
            config.worker_id,
            "--case-kind",
            config.case_kind,
            "--mode",
            config.run_mode,
            "--ext_mode",
            config.ext_mode,
            "--dkw-region",
            config.dkw_region,
        ]
        if config.queue_namespace:
            command.extend(["--queue-namespace", config.queue_namespace])
        if config.queue_address:
            command.extend(["--queue-address", config.queue_address])
        if config.config_module:
            command.extend(["--config-module", config.config_module])
        if config.dataset_csv and not config.shared_store_actor_name:
            command.extend(["--dataset-csv", config.dataset_csv])
        if config.path_root:
            command.extend(["--path-root", config.path_root])
        if config.history_path:
            command.extend(["--history-path", config.history_path])
        if config.refresh_interval is not None:
            command.extend(["--refresh-interval", str(config.refresh_interval)])
        if config.restart_on_refresh:
            command.append("--restart-on-refresh")
        if config.focus_points:
            command.extend(["--focus_points", json.dumps(config.focus_points)])
        if config.dkw_bounds:
            command.extend(["--dkw-bounds", json.dumps(config.dkw_bounds)])
        if config.dkw_pure_smc:
            command.append("--dkw-pure-smc")
        if config.dkw_simultaneous:
            command.append("--dkw-simultaneous")
        if config.headless:
            command.append("--headless")
        if config.shared_store_actor_name:
            command.extend(["--shared-store-actor-name", config.shared_store_actor_name])
            if config.shared_store_namespace:
                command.extend(["--shared-store-namespace", config.shared_store_namespace])
            if config.shared_store_address:
                command.extend(["--shared-store-address", config.shared_store_address])
        return command

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


__all__ = [
    "HostWorkerConfig",
    "HostWorkerManager",
]
