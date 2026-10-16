from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .supervised_process import SupervisorClient, supervisor_client_from_environment


DEFAULT_XVFB_DISPLAY = ":199"
DEFAULT_XVFB_SCREEN_INDEX = "0"
DEFAULT_XVFB_SCREEN_GEOMETRY = "1920x1080x24"
DEFAULT_VK_ICD_FILENAMES = "/usr/share/vulkan/icd.d/nvidia_icd.json"


@dataclass(frozen=True)
class XvfbConfig:
    enabled: bool = False
    executable: str = "Xvfb"
    display: str = DEFAULT_XVFB_DISPLAY
    screen_index: str = DEFAULT_XVFB_SCREEN_INDEX
    screen_geometry: str = DEFAULT_XVFB_SCREEN_GEOMETRY
    warmup_sec: float = 2.0
    cleanup_before_start: bool = True
    vk_icd_filenames: str | None = DEFAULT_VK_ICD_FILENAMES

    def build_command(self) -> list[str]:
        return [
            self.executable,
            self.display,
            "-screen",
            self.screen_index,
            self.screen_geometry,
        ]

    def build_cleanup_command(self) -> str:
        return f"pkill -9 -f 'Xvfb {self.display}' > /dev/null 2>&1"


@dataclass
class XvfbSession:
    process: subprocess.Popen[bytes] | object
    display: str
    vk_icd_filenames: str | None


class XvfbController:
    def __init__(
        self,
        *,
        popen_factory: Callable[..., object] | None = None,
        system_runner: Callable[[str], int] | None = None,
        sleeper: Callable[[float], None] | None = None,
        env: dict[str, str] | None = None,
        signal_sender: Callable[[object, int], None] | None = None,
        supervisor_client: SupervisorClient | None = None,
    ) -> None:
        self.popen_factory = popen_factory or subprocess.Popen
        self.system_runner = system_runner or os.system
        self.sleeper = sleeper or time.sleep
        self.env = env if env is not None else os.environ
        self.signal_sender = signal_sender or self._default_signal_sender
        self.supervisor_client = (
            supervisor_client
            if supervisor_client is not None
            else supervisor_client_from_environment()
        )

    def start(self, config: XvfbConfig) -> XvfbSession | None:
        if not config.enabled:
            return None

        if config.cleanup_before_start:
            if self.supervisor_client is not None:
                self.supervisor_client.run(
                    ["/bin/bash", "-lc", f"{config.build_cleanup_command()} || true"],
                    cwd=Path.home(),
                    env=dict(self.env),
                )
            else:
                self.system_runner(config.build_cleanup_command())

        if self.supervisor_client is not None:
            process = self.supervisor_client.spawn(
                config.build_command(),
                cwd=Path.home(),
                env=dict(self.env),
                name=f"Xvfb {config.display}",
            )
        else:
            process = self.popen_factory(
                config.build_command(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.env.update(self.apply_environment(config, env=self.env))
        if config.warmup_sec > 0:
            self.sleeper(config.warmup_sec)
        return XvfbSession(
            process=process,
            display=config.display,
            vk_icd_filenames=config.vk_icd_filenames,
        )

    def stop(self, session: XvfbSession | None) -> None:
        if session is None:
            return

        poll = getattr(session.process, "poll", None)
        if callable(poll) and poll() is not None:
            if self.supervisor_client is not None:
                process_id = getattr(session.process, "process_id", None)
                if process_id is not None:
                    self.supervisor_client.release(process_id)
            return

        if self.supervisor_client is not None:
            process_id = getattr(session.process, "process_id", None)
            if process_id is not None:
                self.supervisor_client.signal(process_id, signal.SIGKILL)
                self.supervisor_client.release(process_id)
        else:
            self.signal_sender(session.process, signal.SIGKILL)

    def apply_environment(
        self,
        config: XvfbConfig,
        *,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        base_env = dict(env or self.env)
        if not config.enabled:
            return base_env

        base_env["DISPLAY"] = config.display
        if config.vk_icd_filenames:
            base_env["VK_ICD_FILENAMES"] = config.vk_icd_filenames
        return base_env

    @staticmethod
    def _default_signal_sender(process: object, sig: int) -> None:
        pid = getattr(process, "pid", None)
        if pid is None:
            return
        os.kill(pid, sig)
