import json
from pathlib import Path

from contracts.verification import FT4DResult
from evaluation.ft4d_service import run_ft4d
from targets.awsim.result_interpreter import interpret_fixture
from targets.awsim.verification_input import build_verification_input


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "awsim"


def test_awsim_pipeline_smoke_runs_end_to_end(tmp_path: Path) -> None:
    tree_path = _write_tree_config(tmp_path / "awsim_smoke_tree.json")
    record = interpret_fixture(FIXTURES / "normal_trace_maude.json")

    verification_input = build_verification_input(
        record,
        {
            "c_collision": {
                "dataset_filter": None,
                "error_filter": "output.c_collision",
                "target_column": "c_collision",
            },
            "c_ttc_1.5": {
                "dataset_filter": None,
                "error_filter": "output.c_ttc_1.5",
                "target_column": "c_ttc_1.5",
            },
        },
        assumptions={
            "tree_path": str(tree_path),
            "sigma_pf_source": "dataset",
            "and_rule": "min",
            "sigma_pf_assumptions": {
                "c_collision": 0.5,
                "c_ttc_1.5": 0.5,
            },
        },
        meta={"tree_path": str(tree_path)},
    )
    result = run_ft4d(verification_input)

    assert isinstance(result, FT4DResult)
    assert result.tree_mode == "basic"
    assert result.top_sigma_pe is not None
    assert result.top_sigma_pe >= 0.0
    assert result.confidence is not None
    assert result.raw_result["tree_path"] == str(tree_path.resolve())

    node_ids = {node["node_id"] for node in result.node_summaries}
    assert {"TOP", "c_collision", "c_ttc_1.5"} <= node_ids


def _write_tree_config(path: Path) -> Path:
    payload = {
        "tree": {
            "top_event": {
                "id": "TOP",
                "gate": "OR",
                "use_dataset_aggregation": True,
                "children": ["c_collision", "c_ttc_1.5"],
            }
        },
        "params": {
            "c_collision": {"sigma_pf": 0.5, "sigma_pb": 0.0},
            "c_ttc_1.5": {"sigma_pf": 0.5, "sigma_pb": 0.0},
        },
        "labels": {
            "TOP": "AWSIM smoke top event",
            "c_collision": "collision risk",
            "c_ttc_1.5": "ttc below 1.5",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
