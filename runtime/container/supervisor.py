from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.execution import RunStatus
from .artifact_watcher import ArtifactWatcher


@dataclass(frozen=True)
class SupervisionResult:
    status: RunStatus
    trace_path: Path
    timeout_sec: float


class ContainerSupervisor:
    def __init__(self, artifact_watcher: ArtifactWatcher):
        self.artifact_watcher = artifact_watcher

    def wait_for_completion(
        self,
        *,
        expected_trace_path: Path,
        local_trace_path: Path,
        timeout_sec: float,
    ) -> SupervisionResult:
        trace_path = self.artifact_watcher.wait_for_trace(
            expected_trace_path=expected_trace_path,
            local_trace_path=local_trace_path,
            timeout_sec=timeout_sec,
        )
        if trace_path is None:
            trace_path = self.artifact_watcher.write_timeout_marker(expected_trace_path)
            return SupervisionResult(
                status=RunStatus.TIMEOUT,
                trace_path=trace_path,
                timeout_sec=timeout_sec,
            )

        return SupervisionResult(
            status=RunStatus.SUCCESS,
            trace_path=trace_path,
            timeout_sec=timeout_sec,
        )
