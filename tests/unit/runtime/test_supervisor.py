from pathlib import Path

from contracts.execution import RunStatus
from runtime.container.artifact_watcher import ArtifactWatcher, ArtifactWatcherConfig
from runtime.container.supervisor import ContainerSupervisor


def test_container_supervisor_returns_success_when_trace_is_found(tmp_path: Path) -> None:
    local_trace_path = tmp_path / "uturn_test_sim3.json"
    expected_trace_path = tmp_path / "uturn_eval_sim3.json"
    local_trace_path.write_text("{}", encoding="utf-8")

    supervisor = ContainerSupervisor(
        ArtifactWatcher(ArtifactWatcherConfig(poll_interval_sec=0.0, settle_time_sec=0.0))
    )
    result = supervisor.wait_for_completion(
        expected_trace_path=expected_trace_path,
        local_trace_path=local_trace_path,
        timeout_sec=0.1,
    )

    assert result.status is RunStatus.SUCCESS
    assert result.trace_path == expected_trace_path
    assert result.artifact_timing == "on_time"
    assert expected_trace_path.exists()


def test_container_supervisor_returns_timeout_and_writes_marker(tmp_path: Path) -> None:
    ticks = iter([0.0, 1.0])
    expected_trace_path = tmp_path / "uturn_eval_sim9.json"
    supervisor = ContainerSupervisor(
        ArtifactWatcher(
            ArtifactWatcherConfig(
                poll_interval_sec=0.0,
                settle_time_sec=0.0,
                post_timeout_grace_sec=0.0,
            ),
            monotonic=lambda: next(ticks),
            sleeper=lambda _: None,
        )
    )

    result = supervisor.wait_for_completion(
        expected_trace_path=expected_trace_path,
        local_trace_path=tmp_path / "uturn_test_sim9.json",
        timeout_sec=0.5,
    )

    assert result.status is RunStatus.TIMEOUT
    assert result.trace_path == expected_trace_path
    assert result.artifact_timing == "missing"
    assert expected_trace_path.read_text(encoding="utf-8") == "TIMEOUT"


def test_container_supervisor_recovers_trace_during_post_timeout_grace(tmp_path: Path) -> None:
    expected_trace_path = tmp_path / "uturn_eval_sim10.json"
    local_trace_path = tmp_path / "uturn_test_sim10.json"

    class DelayedTraceWatcher:
        config = ArtifactWatcherConfig(
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            post_timeout_grace_sec=10.0,
        )

        def __init__(self) -> None:
            self.calls = 0

        def wait_for_trace(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return None
            local_trace_path.write_text("{}", encoding="utf-8")
            local_trace_path.replace(expected_trace_path)
            return expected_trace_path

        def write_timeout_marker(self, _path: Path) -> Path:
            raise AssertionError("grace trace must prevent timeout marker creation")

    result = ContainerSupervisor(DelayedTraceWatcher()).wait_for_completion(
        expected_trace_path=expected_trace_path,
        local_trace_path=local_trace_path,
        timeout_sec=200.0,
    )

    assert result.status is RunStatus.SUCCESS
    assert result.artifact_timing == "late"
    assert expected_trace_path.read_text(encoding="utf-8") == "{}"
