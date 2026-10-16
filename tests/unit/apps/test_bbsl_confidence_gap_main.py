import json
from pathlib import Path

import apps.cli.bbsl_confidence_gap_main as bbsl_confidence_gap_main


def test_run_confidence_gap_writes_summary_json_for_underconfident_batch_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "confidence_gap.json"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bbsl_confidence_gap_main,
        "run_bbsl_underconfident_loop",
        lambda **kwargs: (
            captured.setdefault("kwargs", kwargs),
            {
                "ft4d_result": {
                    "tree_mode": "basic",
                    "event_inputs": {"BLUR": {"total_count": 8}},
                    "local_ft4d_report": {
                        "tree": {
                            "id": "TOP",
                            "type": "gate",
                            "confidence": 0.91,
                            "children": [
                                {
                                    "id": "BLUR",
                                    "type": "basic",
                                    "confidence": 0.91,
                                    "recognition_test": {
                                        "has_required_sample_size": True,
                                        "passed": True,
                                    },
                                    "children": [],
                                }
                            ],
                        }
                    },
                },
                "confidence_summary": {
                    "underconfident_unique_count": 1,
                    "total_unique_events": 2,
                },
            },
        )[1],
    )
    monkeypatch.setattr(
        bbsl_confidence_gap_main,
        "summarize_ft4d_completion",
        lambda ft4d_result, min_confidence: {
            "complete": False,
            "min_confidence": 0.91,
            "total_trials": 8,
        },
    )

    payload = bbsl_confidence_gap_main.run_confidence_gap(
        [
            "--target-repo",
            str(tmp_path / "bbsl_repo"),
            "--execution-mode",
            "batch-loop",
            "--condition-policy",
            "underconfident",
            "--output-json",
            str(output_path),
        ]
    )

    assert captured["kwargs"]["target_repo"] == str((tmp_path / "bbsl_repo").resolve())
    assert payload["confidence_summary"]["underconfident_unique_count"] == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["completion_summary"]["total_trials"] == 8
