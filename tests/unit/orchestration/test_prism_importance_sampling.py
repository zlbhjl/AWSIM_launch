from __future__ import annotations

from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.prism_importance_sampling import (
    PrismImportanceSamplingRunConfig,
    PrismImportanceSamplingRunner,
    _sample_seed,
)


def test_fixed_runner_separates_runs_and_saves_comparison(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text("""schema_version: 1
experiment_id: prism_is_v1
target: prism
target_measure:
  id: original_model_v1
  definition: {model: simple_reliability_dtmc}
proposal:
  id: failure_biased_v1
  mode: manual_proposal
  definition: {transition_probabilities: {}}
""", encoding="utf-8")
    class Backend:
        def run(self, case):
            meta = {"source_module": "test.backend"}
            if "sampling_plan_path" in case.input:
                meta["sampling"] = {**case.meta["sampling"], "importance_weight_status": "available", "log_p": 0.0, "log_q": 0.0, "log_importance_weight": 0.0, "importance_weight_upper_bound": 1.0}
            return RawRunResult(case.case_id, "prism", case.case_kind, RunStatus.SUCCESS, meta=meta)
    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(raw.case_id, "prism", raw.case_kind, raw.status, output={"c_failure": 0}, meta=dict(raw.meta))
    result = PrismImportanceSamplingRunner(backend=Backend(), result_interpreter=Interpreter()).run_fixed_samples(
        PrismImportanceSamplingRunConfig(sampling_plan_path=plan, output_root=tmp_path / "run", max_samples=2)
    )
    assert (tmp_path / "run" / "ordinary" / "records.jsonl").exists()
    assert (tmp_path / "run" / "importance" / "records.jsonl").exists()
    assert Path(result["comparison_path"]).exists()
    assert result["comparison"]["diagnostic"]["ordinary"]["sample_size"] == 2
    assert result["comparison"]["diagnostic"]["importance"]["sample_size"] == 2
    assert result["comparison"]["diagnostic"]["importance_estimator"] == "ordinary_importance_sampling"


def test_suggest_pilot_saves_candidate_records_and_never_applies_q(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text("""schema_version: 1
experiment_id: prism_search_v1
target: prism
target_measure:
  id: original_model_v1
  definition:
    model: simple_reliability_dtmc
    constants:
      P_NORMAL_DEGRADE: 0.02
      P_NORMAL_FAILURE: 0.001
      P_DEGRADED_NORMAL: 0.30
      P_DEGRADED_FAILURE: 0.02
proposal:
  id: initial_q
  mode: manual_proposal
  definition:
    transition_probabilities:
      normal: {normal: 0.96, degraded: 0.03, failure: 0.01}
      degraded: {normal: 0.28, degraded: 0.66, failure: 0.06}
      failure: {failure: 1.0}
proposal_search:
  enabled: true
  policy: suggest
  adjustable_transitions:
    - {from: normal, to: failure, compensate_to: normal, values: [0.005, 0.02]}
  constraints: {preserve_row_sum: true, preserve_support: true}
  evaluation: {metric: c_failure, pilot_samples: 2}
""", encoding="utf-8")

    class Backend:
        def run(self, case):
            assert "proposal_definition" in case.input
            assert case.input["proposal_sampling_mode"] == "adaptive"
            sampling = {
                **case.meta["sampling"], "importance_weight_status": "available",
                "log_p": 0.0, "log_q": 0.0, "log_importance_weight": 0.0,
                "importance_weight_upper_bound": 1.0,
            }
            return RawRunResult(case.case_id, "prism", case.case_kind, RunStatus.SUCCESS,
                                meta={"source_module": "test.backend", "sampling": sampling})

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(raw.case_id, "prism", raw.case_kind, raw.status,
                                    output={"c_failure": 0}, meta=dict(raw.meta))

    output_root = tmp_path / "suggest"
    result = PrismImportanceSamplingRunner(
        backend=Backend(), result_interpreter=Interpreter(),
    ).run_suggest_pilot(
        PrismImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=output_root, max_samples=9,
        )
    )

    assert result["mode"] == "suggest"
    assert len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        assert Path(candidate["records_path"]).exists()
        assert candidate["report"]["sample_count"] == 2
        assert candidate["importance_weight_status_counts"] == {"available": 2}
    events = [
        __import__("json").loads(line)
        for line in (output_root / "proposal_history.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {event["event_type"] for event in events} == {"candidate_evaluated", "proposed"}
    assert all(event["event_type"] != "applied" for event in events)


def test_suggest_pilot_runs_only_human_selected_candidate_ids(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text("""schema_version: 1
experiment_id: prism_search_selected_v1
target: prism
target_measure:
  id: original_model_v1
  definition:
    model: simple_reliability_dtmc
    constants: {P_NORMAL_DEGRADE: 0.02, P_NORMAL_FAILURE: 0.001, P_DEGRADED_NORMAL: 0.30, P_DEGRADED_FAILURE: 0.02}
proposal:
  id: initial_q
  mode: manual_proposal
  definition:
    transition_probabilities:
      normal: {normal: 0.96, degraded: 0.03, failure: 0.01}
      degraded: {normal: 0.28, degraded: 0.66, failure: 0.06}
      failure: {failure: 1.0}
proposal_search:
  enabled: true
  policy: suggest
  adjustable_transitions:
    - {from: normal, to: failure, compensate_to: normal, values: [0.005, 0.02]}
  constraints: {preserve_row_sum: true, preserve_support: true}
  evaluation:
    metric: c_failure
    pilot_samples: 1
    candidate_ids: [initial_q__candidate_002]
""", encoding="utf-8")
    class Backend:
        def run(self, case):
            return RawRunResult(case.case_id, "prism", case.case_kind, RunStatus.SUCCESS, meta={
                "source_module": "test.backend", "sampling": {**case.meta["sampling"],
                    "importance_weight_status": "available", "log_p": 0.0, "log_q": 0.0,
                    "log_importance_weight": 0.0, "importance_weight_upper_bound": 1.0},
            })
    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(raw.case_id, "prism", raw.case_kind, raw.status,
                                    output={"c_failure": 0}, meta=dict(raw.meta))
    result = PrismImportanceSamplingRunner(backend=Backend(), result_interpreter=Interpreter()).run_suggest_pilot(
        PrismImportanceSamplingRunConfig(sampling_plan_path=plan, output_root=tmp_path / "run")
    )
    assert [candidate["proposal_id"] for candidate in result["candidates"]] == ["initial_q__candidate_002"]


def test_outer_seed_uses_disjoint_sample_seed_blocks() -> None:
    first = {_sample_seed(0, index) for index in range(1, 101)}
    second = {_sample_seed(1, index) for index in range(1, 101)}

    assert first.isdisjoint(second)


def test_weighted_sequential_runner_stops_without_changing_fixed_q(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text("""schema_version: 1
experiment_id: prism_sequential_v1
target: prism
target_measure:
  id: original_model_v1
  definition: {model: simple_reliability_dtmc}
proposal:
  id: fixed_q_v1
  mode: manual_proposal
  definition: {transition_probabilities: {}}
sequential_decision: {null_upper: 0.99, alternative_lower: 1.0}
""", encoding="utf-8")

    class Backend:
        def run(self, case):
            sampling = {
                **case.meta["sampling"],
                "importance_weight_status": "available",
                "log_p": 0.0,
                "log_q": 0.0,
                "log_importance_weight": 0.0,
                "importance_weight_upper_bound": 1.0,
            }
            return RawRunResult(
                case.case_id, "prism", case.case_kind, RunStatus.SUCCESS,
                meta={"source_module": "test.backend", "sampling": sampling},
            )

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(
                raw.case_id, "prism", raw.case_kind, raw.status,
                output={"c_failure": 0}, meta=dict(raw.meta),
            )

    result = PrismImportanceSamplingRunner(
        backend=Backend(), result_interpreter=Interpreter(),
    ).run(
        PrismImportanceSamplingRunConfig(
            sampling_plan_path=plan,
            output_root=tmp_path / "sequential",
            max_samples=20,
            stop_mode="weighted_sequential",
        )
    )

    assert result["stopped_by_rule"] is True
    assert result["sample_count"] < 20
    assert result["statistical_report"]["diagnostics"]["decision"] == "accept_null"
    assert not (tmp_path / "sequential" / "ordinary" / "records.jsonl").exists()
