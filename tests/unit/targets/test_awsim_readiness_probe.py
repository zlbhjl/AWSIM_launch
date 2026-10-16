from targets.awsim.readiness_probe import (
    AWSIMReadinessConfig,
    AWSIMReadinessProbe,
    CommandResult,
    wait_until_ready,
)


def test_wait_until_ready_requires_consecutive_successes() -> None:
    execution_rounds = iter([False, True, True])

    result = wait_until_ready(
        AWSIMReadinessConfig(
            required_services=("svc",),
            probe_timeout_sec=5.0,
            per_service_timeout_sec=0.1,
            stability_checks=2,
            stability_interval_sec=0.0,
        ),
        service_checker=lambda _service, _timeout_sec: True,
        execution_state_checker=lambda _timeout_sec: next(execution_rounds),
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert result.ready is True
    assert result.attempts == 3
    assert result.message == "ready"


def test_wait_until_ready_returns_after_bounded_failure() -> None:
    result = wait_until_ready(
        AWSIMReadinessConfig(
            required_services=("svc",),
            probe_timeout_sec=0.0,
            per_service_timeout_sec=0.1,
            stability_checks=2,
            stability_interval_sec=0.0,
        ),
        service_checker=lambda _service, _timeout_sec: False,
        execution_state_checker=lambda _timeout_sec: True,
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    assert result.ready is False
    assert result.attempts == 1
    assert result.message == "service_unavailable:svc"


def test_probe_runner_parses_json_response() -> None:
    class FakeRunner:
        def run_command(self, command, *, cwd, env, source_setup_script):
            assert command[:3] == ["python3", "-m", "targets.awsim.readiness_probe"]
            return CommandResult(
                returncode=0,
                stdout='{"ready": true, "attempts": 2, "checked_services": ["svc"], "message": "ready"}\n',
            )

    probe = AWSIMReadinessProbe(runner=FakeRunner())
    result = probe.probe(
        cwd=None,
        env={},
        source_setup_script=None,
        config=AWSIMReadinessConfig(
            required_services=("svc",),
            probe_timeout_sec=1.0,
            per_service_timeout_sec=0.1,
            stability_checks=2,
            stability_interval_sec=0.0,
        ),
    )

    assert result.ready is True
    assert result.attempts == 2
    assert result.checked_services == ("svc",)


def test_default_required_services_excludes_runtime_monitor_recording_state() -> None:
    config = AWSIMReadinessConfig()

    assert "/monitor/recording/state" not in config.required_services
