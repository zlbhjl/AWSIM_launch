import json
from pathlib import Path

from apps.cli.ft4d_smoke_main import run_ft4d_smoke, run_smoke
from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.verification import FT4DResult, VerificationInput


def test_run_smoke_builds_serializable_result() -> None:
    captured: dict[str, object] = {}

    def fake_interpret_path(trace_path):
        captured["trace_path"] = str(trace_path)
        return EvaluationRecord(
            case_id="loop_1",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            output={"c_collision": 0, "c_ttc_1.5": 1, "c_ttc_0.9": 0},
            meta={"schema_version": 1},
        )

    def fake_build_verification_input(record, event_definitions, *, assumptions=None, meta=None):
        captured["event_ids"] = sorted(event_definitions.keys())
        captured["assumptions"] = dict(assumptions or {})
        captured["meta"] = dict(meta or {})
        return VerificationInput(
            tree_mode="basic",
            universal_dataset={"loop_1"},
            events={
                "HIGH_SPEED": {
                    "dataset_d": {"loop_1"},
                    "dataset_e": set(),
                    "total_count": 1,
                    "correct_count": 1,
                    "error_count": 0,
                }
            },
            assumptions=dict(assumptions or {}),
            meta=dict(meta or {}),
        )

    def fake_ft4d_runner(verification_input):
        captured["tree_mode"] = verification_input.tree_mode
        return FT4DResult(
            tree_mode="basic",
            top_sigma_pe=0.125,
            confidence=0.95,
            node_summaries=[{"node_id": "TOP"}],
            raw_result={
                "sigma_pf_source": "dataset",
                "and_rule": "min",
                "tree_report": {
                    "labels": {"TOP": "Top Event"},
                    "tree": {
                        "id": "TOP",
                        "type": "or",
                        "gate": "OR",
                        "sigma_pf": 0.0,
                        "sigma_pe": 0.125,
                        "children": [],
                    },
                },
            },
        )

    result = run_smoke(
        trace_path="tests/fixtures/awsim/normal_trace_maude.json",
        tree_path="verification_core/ft4d/config/awsim_demo_tree.json",
        sigma_pf_source="dataset",
        and_rule="min",
        interpret_path_fn=fake_interpret_path,
        build_verification_input_fn=fake_build_verification_input,
        ft4d_runner=fake_ft4d_runner,
    )

    assert result["record_status"] == "success"
    assert result["top_sigma_pe"] == 0.125
    assert result["verification_input"]["universal_dataset"] == ["loop_1"]
    assert captured["event_ids"] == ["HIGH_SPEED", "LOW_TTC", "SHORT_GAP"]
    assert captured["tree_mode"] == "basic"


def test_run_ft4d_smoke_writes_output_json(tmp_path: Path, monkeypatch, capsys) -> None:
    output_path = tmp_path / "smoke_result.json"

    def fake_run_smoke(**kwargs):
        return {
            "trace_path": "/tmp/trace.json",
            "tree_path": "/tmp/tree.json",
            "record_status": "success",
            "record_output": {"c_collision": 0},
            "verification_input": {
                "tree_mode": "basic",
                "universal_dataset": ["loop_1"],
                "events": {},
                "assumptions": {},
                "meta": {},
            },
            "sigma_pf_source": "dataset",
            "and_rule": "min",
            "tree_report": {"tree": {"id": "TOP"}},
            "rendered_tree": "TOP",
            "top_sigma_pe": 0.0,
            "confidence": 1.0,
            "node_summaries": [],
        }

    monkeypatch.setattr("apps.cli.ft4d_smoke_main.run_smoke", fake_run_smoke)

    exit_code = run_ft4d_smoke(
        [
            "--output-json",
            str(output_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["record_status"] == "success"
    assert payload["top_sigma_pe"] == 0.0

    stdout = capsys.readouterr().out
    assert "AWSIM_launch internal FT4D smoke run" in stdout
    assert str(output_path) in stdout
