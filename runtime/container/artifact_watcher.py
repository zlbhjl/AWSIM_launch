from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ArtifactWatcherConfig:
    poll_interval_sec: float = 2.0
    settle_time_sec: float = 5.0
    post_timeout_grace_sec: float = 10.0
    timeout_marker: str = "TIMEOUT"


class ArtifactWatcher:
    def __init__(
        self,
        config: ArtifactWatcherConfig | None = None,
        *,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config or ArtifactWatcherConfig()
        self.monotonic = monotonic or time.monotonic
        self.sleeper = sleeper or time.sleep

    def wait_for_trace(
        self,
        *,
        expected_trace_path: Path,
        local_trace_path: Path,
        timeout_sec: float,
    ) -> Path | None:
        deadline = self.monotonic() + timeout_sec
        while self.monotonic() <= deadline:
            if expected_trace_path.exists():
                self._settle_after_trace_found()
                return expected_trace_path
            if local_trace_path.exists():
                expected_trace_path.parent.mkdir(parents=True, exist_ok=True)
                if local_trace_path != expected_trace_path:
                    local_trace_path.replace(expected_trace_path)
                self._settle_after_trace_found()
                return expected_trace_path
            if self.config.poll_interval_sec > 0:
                self.sleeper(self.config.poll_interval_sec)
        return None

    def write_timeout_marker(self, trace_path: Path) -> Path:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(self.config.timeout_marker, encoding="utf-8")
        return trace_path

    def _settle_after_trace_found(self) -> None:
        if self.config.settle_time_sec > 0:
            self.sleeper(self.config.settle_time_sec)
