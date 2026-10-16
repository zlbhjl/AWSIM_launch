from pathlib import Path

from runtime.container.artifact_watcher import ArtifactWatcher, ArtifactWatcherConfig


def test_artifact_watcher_promotes_local_trace_to_expected_path(tmp_path: Path) -> None:
    local_trace_path = tmp_path / "uturn_test_sim3.json"
    expected_trace_path = tmp_path / "uturn_eval_sim3.json"
    local_trace_path.write_text("{}", encoding="utf-8")

    watcher = ArtifactWatcher(
        ArtifactWatcherConfig(poll_interval_sec=0.0, settle_time_sec=0.0)
    )
    resolved = watcher.wait_for_trace(
        expected_trace_path=expected_trace_path,
        local_trace_path=local_trace_path,
        timeout_sec=0.1,
    )

    assert resolved == expected_trace_path
    assert expected_trace_path.exists()
    assert not local_trace_path.exists()


def test_artifact_watcher_returns_none_on_timeout() -> None:
    ticks = iter([0.0, 1.0])
    watcher = ArtifactWatcher(
        ArtifactWatcherConfig(poll_interval_sec=0.0, settle_time_sec=0.0),
        monotonic=lambda: next(ticks),
        sleeper=lambda _: None,
    )

    resolved = watcher.wait_for_trace(
        expected_trace_path=Path("/tmp/missing_eval.json"),
        local_trace_path=Path("/tmp/missing_test.json"),
        timeout_sec=0.5,
    )

    assert resolved is None


def test_artifact_watcher_writes_timeout_marker(tmp_path: Path) -> None:
    trace_path = tmp_path / "uturn_eval_sim9.json"
    watcher = ArtifactWatcher(
        ArtifactWatcherConfig(poll_interval_sec=0.0, settle_time_sec=0.0)
    )

    resolved = watcher.write_timeout_marker(trace_path)

    assert resolved == trace_path
    assert trace_path.read_text(encoding="utf-8") == "TIMEOUT"
