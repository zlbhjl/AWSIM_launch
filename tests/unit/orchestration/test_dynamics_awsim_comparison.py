from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RawRunResult, RunStatus
from orchestration.dynamics_awsim_comparison import (
    ComparisonCase,
    DynamicsAWSIMComparisonRunner,
    build_comparison_record,
)


def _record(target: str, status: RunStatus, **output: object) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=f"case-{target}", target=target, case_kind="uturn", status=status,
        output=output, evidence={"trace": f"/{target}.json"},
        meta=ensure_evaluation_meta(source_module="test"),
    )


def test_comparison_record_reports_agreement_and_ttc_delta() -> None:
    case = ComparisonCase("cmp-1", "uturn", {"dx0": 15, "ego_speed": 36, "npc_speed": 18})
    dynamics = _record("dynamics", RunStatus.SUCCESS, c_collision=1, min_ttc=0.8, theory_zone_a="B", theory_zone_b="A")
    awsim = _record("awsim", RunStatus.SUCCESS, c_collision=1, min_ttc=1.1)

    record = build_comparison_record(case, dynamics, awsim)

    assert record.status is RunStatus.SUCCESS
    assert record.output["comparison_status"] == "comparable"
    assert record.output["comparison_basis"] == "shared_inputs_trigger_aligned_obb_autoware_calibrated"
    assert record.output["full_awsim_equivalence"] == 0
    assert record.output["collision_agree"] == 1
    assert record.output["min_ttc_delta_sec"] == -0.30000000000000004
    assert record.output["jama_zone_a_agree"] == 1
    assert record.evidence["awsim_trace"] == "/awsim.json"


def test_awsim_failure_is_revalidation_unavailable_not_disagreement() -> None:
    case = ComparisonCase("cmp-2", "uturn", {"dx0": 15, "ego_speed": 36, "npc_speed": 18})
    dynamics = _record("dynamics", RunStatus.SUCCESS, c_collision=0, min_ttc=2.0)
    awsim = _record("awsim", RunStatus.TIMEOUT)

    record = build_comparison_record(case, dynamics, awsim)

    assert record.status is RunStatus.ANALYSIS_ERROR
    assert record.output["comparison_status"] == "revalidation_unavailable"
    assert record.output["collision_agree"] is None


def test_runner_sends_identical_shared_inputs_to_both_targets() -> None:
    seen: list[object] = []

    class Backend:
        def __init__(self, target: str) -> None:
            self.target = target

        def run(self, case):
            seen.append(case)
            return RawRunResult(case.case_id, self.target, "uturn", RunStatus.SUCCESS)

    class Interpreter:
        def __init__(self, target: str) -> None:
            self.target = target

        def interpret_raw_run_result(self, raw):
            return _record(self.target, RunStatus.SUCCESS, c_collision=0, min_ttc=2.0)

    case = ComparisonCase("cmp-3", "uturn", {"dx0": 15, "ego_speed": 36, "npc_speed": 18})
    runner = DynamicsAWSIMComparisonRunner(
        dynamics_backend=Backend("dynamics"), dynamics_interpreter=Interpreter("dynamics"),
        awsim_backend=Backend("awsim"), awsim_interpreter=Interpreter("awsim"),
    )
    _, _, comparison = runner.run(case)

    assert seen[0].input["dx0"] == seen[1].input["dx0"] == 15
    assert seen[0].input["ego_speed"] == seen[1].input["ego_speed"] == 36
    assert seen[0].input["alignment_mode"] == "uturn_trigger_aligned"
    assert seen[0].input["controller_kind"] == "autoware171_uturn_calibrated"
    assert comparison.output["comparison_status"] == "comparable"


def test_screening_mode_flags_missed_awsim_collision_separately() -> None:
    case = ComparisonCase(
        "cmp-4", "uturn", {"dx0": 15, "ego_speed": 36, "npc_speed": 18}, decision_mode="screening"
    )
    awsim = _record("awsim", RunStatus.SUCCESS, c_collision=1, min_ttc=0.0)

    caught = build_comparison_record(
        case, _record("dynamics", RunStatus.SUCCESS, c_collision=0, c_screening_candidate=1, min_ttc=0.6), awsim
    )
    missed = build_comparison_record(
        case, _record("dynamics", RunStatus.SUCCESS, c_collision=0, c_screening_candidate=0, min_ttc=1.2), awsim
    )

    assert caught.output["decision_mode"] == "screening"
    assert caught.output["collision_agree"] == 0
    assert caught.output["decision_agree"] == 1
    assert caught.output["decision_missed_awsim_collision"] == 0
    assert missed.output["decision_missed_awsim_collision"] == 1


def test_comparison_case_rejects_unknown_decision_mode() -> None:
    import pytest

    with pytest.raises(ValueError, match="decision_mode"):
        ComparisonCase("cmp-5", "uturn", {"dx0": 15, "ego_speed": 36, "npc_speed": 18}, decision_mode="x")
