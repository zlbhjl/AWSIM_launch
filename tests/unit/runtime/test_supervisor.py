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
    assert expected_trace_path.exists()


def test_container_supervisor_returns_timeout_and_writes_marker(tmp_path: Path) -> None:
    ticks = iter([0.0, 1.0])
    expected_trace_path = tmp_path / "uturn_eval_sim9.json"
    supervisor = ContainerSupervisor(
        ArtifactWatcher(
            ArtifactWatcherConfig(poll_interval_sec=0.0, settle_time_sec=0.0),
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
    assert expected_trace_path.read_text(encoding="utf-8") == "TIMEOUT"
