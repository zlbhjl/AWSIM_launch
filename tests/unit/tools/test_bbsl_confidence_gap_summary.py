import json

from tools.analysis.bbsl_confidence_gap_summary import (
    load_payload,
    render_confidence_gap_summary,
    run_confidence_gap_summary,
)


def test_render_confidence_gap_summary_formats_payload(tmp_path, capsys) -> None:
    result_json = tmp_path / "bbsl_confidence_gap.json"
    result_json.write_text(
        json.dumps(
            {
                "target_repo": "/tmp/bbsl",
                "execution_mode": "batch-loop",
                "condition_policy": "underconfident",
                "run_mode": "resume",
                "tree_mode": "basic",
                "confidence_summary": {
                    "underconfident_unique_count": 2,
                    "total_unique_events": 5,
                    "underconfident_events": [
                        {
                            "node_id": "BLUR",
                            "event_id": "blur_basic",
                            "sigma_pe": 0.1,
                            "trials": 8,
                            "confidence": 0.91,
                        }
                    ],
                },
                "completion_summary": {
                    "complete": False,
                    "min_confidence": 0.91,
                    "total_trials": 8,
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = run_confidence_gap_summary([str(result_json)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "AWSIM_launch BBSL confidence-gap summary" in stdout
    assert "underconfident : 2" in stdout
    assert "node=BLUR" in stdout


def test_load_payload_keeps_resolved_path(tmp_path) -> None:
    result_json = tmp_path / "bbsl_confidence_gap.json"
    result_json.write_text("{}", encoding="utf-8")

    payload = load_payload(result_json)
    rendered = render_confidence_gap_summary(payload)

    assert payload["_result_path"] == str(result_json.resolve())
    assert f"result json    : {result_json.resolve()}" in rendered
