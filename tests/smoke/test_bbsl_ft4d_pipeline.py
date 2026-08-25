from pathlib import Path

from contracts.verification import FT4DResult
from evaluation.ft4d_service import run_ft4d
from targets.bbsl.result_interpreter import ResultInterpreter
from targets.bbsl.verification_input import build_verification_input


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bbsl"


def test_bbsl_pipeline_smoke_runs_end_to_end() -> None:
    fixture_path = FIXTURES / "experiment_all_raw_result_mini.json"

    record = ResultInterpreter().interpret_fixture(fixture_path)
    verification_input = build_verification_input(record)
    result = run_ft4d(verification_input)

    assert isinstance(result, FT4DResult)
    assert result.tree_mode == "basic"
    assert result.top_sigma_pe is not None
    assert result.top_sigma_pe >= 0.0
    assert result.confidence is not None
    assert result.confidence >= 0.0
    assert result.raw_result["tree_path"].endswith("tree_basic.json")

    node_ids = {node["node_id"] for node in result.node_summaries}
    assert {"TOP", "SALT_PEPPER", "OCCLUSION", "BLUR"} <= node_ids
