from targets.bbsl.underconfident_loop import (
    default_bbsl_conditions,
    run_bbsl_underconfident_loop,
    select_conditions_from_confidence_summary,
)


def test_select_conditions_from_confidence_summary_prefers_underconfident_basic_events() -> None:
    selected = select_conditions_from_confidence_summary(
        {
            "underconfident_events": [
                {"type": "gate", "node_id": "TOP"},
                {"type": "basic", "node_id": "OCCLUSION"},
                {"type": "basic", "node_id": "BLUR"},
            ]
        },
        tree="basic",
    )

    assert selected == ["occlusion", "blur"]
    assert default_bbsl_conditions("combined")[-1] == "all_three"


def test_run_bbsl_underconfident_loop_delegates_and_stops_when_complete() -> None:
    captured: dict[str, object] = {}

    def fake_load_resume_state(**kwargs):
        captured["load_resume_state"] = kwargs
        return {
            "clean_output_json": "/tmp/clean.json",
            "clean_success_path": "/tmp/clean_success.json",
            "batch_output_paths": [],
            "final_result": None,
            "status": None,
            "next_batch_id": 1,
        }

    def fake_run_noisy_batch_once(**kwargs):
        captured["run_noisy_batch_once"] = kwargs
        return "/tmp/noisy_batch_0001.json"

    ft4d_result = {
        "tree_mode": "basic",
        "event_inputs": {"SALT_PEPPER": {"total_count": 3}},
        "local_ft4d_report": {
            "tree": {
                "id": "TOP",
                "type": "gate",
                "confidence": 0.99,
                "children": [
                    {
                        "id": "SALT_PEPPER",
                        "type": "basic",
                        "confidence": 0.99,
                        "recognition_test": {"has_required_sample_size": True},
                        "children": [],
                    }
                ],
            }
        },
        "top_sigma_pe": 0.01,
    }

    def fake_evaluate_batch_outputs(*args, **kwargs):
        captured["evaluate_batch_outputs"] = {"args": args, "kwargs": kwargs}
        return ft4d_result

    def fake_summarize_confidence_gaps(result, min_confidence=None):
        captured["summarize_confidence_gaps"] = {
            "result": result,
            "min_confidence": min_confidence,
        }
        return {
            "underconfident_events": [],
            "underconfident_nodes": [],
            "threshold": min_confidence,
        }

    result = run_bbsl_underconfident_loop(
        target_repo="/tmp/BBSL-test",
        tree="basic",
        min_confidence=0.95,
        summarize_confidence_gaps_fn=fake_summarize_confidence_gaps,
        load_resume_state_fn=fake_load_resume_state,
        run_noisy_batch_once_fn=fake_run_noisy_batch_once,
        evaluate_batch_outputs_fn=fake_evaluate_batch_outputs,
    )

    assert result["ft4d_result"]["batch_loop"]["stop_reason"] == "confidence-satisfied"
    assert captured["load_resume_state"]["target_repo"] == "/tmp/BBSL-test"
    assert captured["run_noisy_batch_once"]["batch_id"] == 1
    assert captured["evaluate_batch_outputs"]["kwargs"]["tree_mode"] == "basic"
    assert captured["summarize_confidence_gaps"]["min_confidence"] == 0.95
