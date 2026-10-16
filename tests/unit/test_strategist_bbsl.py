from types import SimpleNamespace

import strategist as strategist_module


def test_inspect_bbsl_ft4d_confidence_gaps_uses_new_batch_and_bridge(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_bbsl_underconfident_loop(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "ft4d_result": {
                "tree_mode": "basic",
                "batch_loop": {
                    "stop_reason": "confidence-satisfied",
                    "completed_batches": 1,
                },
            },
            "confidence_summary": {
                "underconfident_events": [],
                "underconfident_nodes": [],
                "threshold": kwargs["min_confidence"],
            },
        }

    monkeypatch.setattr(
        strategist_module,
        "run_bbsl_underconfident_loop",
        fake_run_bbsl_underconfident_loop,
    )

    strategist = strategist_module.ActiveLearningStrategist(
        "ft4d_bridge",
        SimpleNamespace(
            PARAM_RANGES={"x": (0.0, 1.0)},
            FT4D_MIN_CONFIDENCE=0.95,
        ),
    )
    result = strategist.inspect_bbsl_ft4d_confidence_gaps(
        target_repo="/tmp/BBSL-test",
        execution_mode="batch-loop",
        condition_policy="underconfident",
        mini=True,
        tree="basic",
        min_confidence=0.95,
    )

    assert result["execution_mode"] == "batch-loop"
    assert result["condition_policy"] == "underconfident"
    assert result["ft4d_result"]["batch_loop"]["stop_reason"] == "confidence-satisfied"
    assert captured["kwargs"]["target_repo"] == "/tmp/BBSL-test"
    assert captured["kwargs"]["tree"] == "basic"
    assert captured["kwargs"]["min_confidence"] == 0.95
    assert callable(captured["kwargs"]["summarize_confidence_gaps_fn"])
