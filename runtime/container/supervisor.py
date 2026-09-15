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
    artifact_timing: str


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
            # AWSIM writes the final trace during scenario shutdown. Give that
            # export a bounded grace period before creating a timeout marker.
            grace_sec = max(float(self.artifact_watcher.config.post_timeout_grace_sec), 0.0)
            if grace_sec > 0:
                trace_path = self.artifact_watcher.wait_for_trace(
                    expected_trace_path=expected_trace_path,
                    local_trace_path=local_trace_path,
                    timeout_sec=grace_sec,
                )
                if trace_path is not None:
                    return SupervisionResult(
                        status=RunStatus.SUCCESS,
                        trace_path=trace_path,
                        timeout_sec=timeout_sec,
                        artifact_timing="late",
                    )
            trace_path = self.artifact_watcher.write_timeout_marker(expected_trace_path)
            return SupervisionResult(
                status=RunStatus.TIMEOUT,
                trace_path=trace_path,
                timeout_sec=timeout_sec,
                artifact_timing="missing",
            )

        return SupervisionResult(
            status=RunStatus.SUCCESS,
            trace_path=trace_path,
            timeout_sec=timeout_sec,
            artifact_timing="on_time",
        )
