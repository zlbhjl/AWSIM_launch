from targets.prism.trace_parser import summarize_trace


def test_summary_records_failure_and_absorption():
    summary = summarize_trace(
        [{"step": 0, "state": 0}, {"step": 3, "state": 2}, {"step": 5, "state": 2}], horizon=10
    )

    assert summary["failure_reached"] == 1
    assert summary["first_failure_step"] == 3
    assert summary["absorbing_state_valid"] == 1


def test_summary_uses_horizon_cap_when_failure_is_absent():
    summary = summarize_trace([{"step": 0, "state": 0}], horizon=10)

    assert summary["steps_to_failure_capped"] == 11
