import json
from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.verification import FT4DResult
from evaluation.ft4d_service import run_ft4d
from targets.prism.verification_input import TREE_PATH, build_verification_input_from_records


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prism"


def _load_records(path: Path) -> list[EvaluationRecord]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            records.append(
                EvaluationRecord(
                    case_id=payload["case_id"],
                    target=payload["target"],
                    case_kind=payload["case_kind"],
                    status=RunStatus(payload["status"]),
                    input=payload["input"],
                    output=payload["output"],
                    evidence=payload.get("evidence", {}),
                    meta=payload["meta"],
                )
            )
    return records


def test_prism_pipeline_smoke_runs_end_to_end_on_real_cluster_data() -> None:
    fixture_path = FIXTURES / "prism_stage2_baseline_sample41.jsonl"
    records = _load_records(fixture_path)

    # The fixture also carries the one exact_model_check record from the same
    # experiment; build_verification_input_from_records must filter it out on
    # its own, so we pass every loaded record straight through here.
    verification_input = build_verification_input_from_records(
        records,
        assumptions={
            "tree_path": str(TREE_PATH),
            "sigma_pf_source": "dataset",
            "and_rule": "min",
            "sigma_pf_assumptions": {
                "FAILURE": 1.0,
                "EARLY_FAILURE": 1.0,
                "REPEATED_DEGRADATION": 1.0,
            },
        },
    )
    result = run_ft4d(verification_input)

    assert isinstance(result, FT4DResult)
    assert result.tree_mode == "prism_demo"
    assert result.top_sigma_pe is not None
    assert 0.0 <= result.top_sigma_pe <= 1.0
    assert result.confidence is not None
    assert 0.0 <= result.confidence <= 1.0

    node_ids = {node["node_id"] for node in result.node_summaries}
    assert {"PRISM_TOP", "FAILURE", "EARLY_FAILURE", "REPEATED_DEGRADATION"} <= node_ids

    # 40 real sample-path records from the 2026-09-15 baseline cluster run;
    # the exact_model_check record must be excluded from the universe.
    assert verification_input.meta["eligible_record_count"] == 40
