from targets.bbsl.batch_loop import (
    bbsl_batch_matches_config,
    run_bbsl_until_ft4d_confident,
    summarize_ft4d_completion,
)


def test_summarize_ft4d_completion_reports_complete() -> None:
    result = summarize_ft4d_completion(
        {
            "tree_mode": "basic",
            "event_inputs": {
                "SALT_PEPPER": {"total_count": 5},
            },
            "local_ft4d_report": {
                "tree": {
                    "id": "TOP",
                    "type": "gate",
                    "confidence": 0.98,
                    "children": [
                        {
                            "id": "SALT_PEPPER",
                            "type": "basic",
                            "confidence": 0.98,
                            "recognition_test": {
                                "has_required_sample_size": True,
                            },
                            "children": [],
                        }
                    ],
                }
            },
        },
        min_confidence=0.95,
    )

    assert result["complete"] is True
    assert result["min_confidence"] == 0.98
    assert result["insufficient_basic_events"] == 0
    assert result["total_trials"] == 5


def test_bbsl_batch_matches_config_checks_tree_and_ranges() -> None:
    payload = {
        "mode": "noisy_batch",
        "master_seed": 1000,
        "max_images": 8,
        "tree_mode": "combined",
        "noise_ranges": {
            "salt_pepper_density_range": [0.01, 0.08],
            "occlusion_severity_range": [0.2, 0.5],
            "blur_kernel_range": [5, 11],
        },
        "active_conditions": ["salt_pepper", "sp_occ"],
    }

    assert bbsl_batch_matches_config(
        payload,
        tree="combined",
        master_seed=1000,
        max_images=8,
        salt_pepper_density_range=(0.01, 0.08),
        occlusion_severity_range=(0.2, 0.5),
        blur_kernel_range=(5, 11),
        allowed_conditions=("salt_pepper", "sp_occ", "sp_blur"),
    )


def test_run_bbsl_until_ft4d_confident_stops_after_confidence_satisfied() -> None:
    calls: dict[str, object] = {}

    def fake_load_resume_state(**kwargs):
        calls["load_resume_state"] = kwargs
        return {
            "clean_output_json": "/tmp/clean.json",
            "clean_success_path": "/tmp/clean_success.json",
            "batch_output_paths": [],
            "final_result": None,
            "status": None,
            "next_batch_id": 1,
        }

    def fake_run_noisy_batch_once(**kwargs):
        calls["run_noisy_batch_once"] = kwargs
        return "/tmp/noisy_batch_0001.json"

    def fake_evaluate_batch_outputs(*args, **kwargs):
        calls["evaluate_batch_outputs"] = {"args": args, "kwargs": kwargs}
        return {
            "tree_mode": "basic",
            "event_inputs": {
                "SALT_PEPPER": {"total_count": 3},
            },
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
                            "recognition_test": {
                                "has_required_sample_size": True,
                            },
                            "children": [],
                        }
                    ],
                }
            },
        }

    result = run_bbsl_until_ft4d_confident(
        target_repo="/tmp/BBSL-test",
        mini=True,
        tree="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        max_batches=3,
        min_confidence=0.95,
        load_resume_state_fn=fake_load_resume_state,
        run_noisy_batch_once_fn=fake_run_noisy_batch_once,
        evaluate_batch_outputs_fn=fake_evaluate_batch_outputs,
    )

    assert result["batch_loop"]["stop_reason"] == "confidence-satisfied"
    assert result["batch_loop"]["completed_batches"] == 1
    assert calls["run_noisy_batch_once"]["batch_id"] == 1
    assert calls["evaluate_batch_outputs"]["kwargs"]["tree_mode"] == "basic"
