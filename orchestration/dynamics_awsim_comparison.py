"""Comparison contract for one shared dynamics/AWSIM U-turn input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RunStatus, TestCase
from scenario_specs.uturn import validate_sampled_parameters
from targets.awsim.theory import build_theory_metrics


DECISION_METRICS = {
    "judgment": "c_collision",
    "screening": "c_screening_candidate",
}


@dataclass(frozen=True)
class ComparisonCase:
    comparison_id: str
    case_kind: str
    inputs: dict[str, object]
    jama_profile: str = "ai_aeb"
    # judgment: dynamics c_collision is compared with AWSIM as a substitute.
    # screening: dynamics c_screening_candidate decides whether AWSIM must
    # re-validate the case; a missed AWSIM collision is the key failure.
    decision_mode: str = "judgment"

    def __post_init__(self) -> None:
        if not self.comparison_id.strip():
            raise ValueError("comparison_id must not be empty")
        if self.decision_mode not in DECISION_METRICS:
            raise ValueError("decision_mode must be 'judgment' or 'screening'")
        if self.case_kind != "uturn":
            raise ValueError("comparison currently supports only case_kind='uturn'")
        validate_sampled_parameters({key: float(self.inputs[key]) for key in ("dx0", "ego_speed", "npc_speed")})

    def dynamics_test_case(self) -> TestCase:
        return TestCase(
            case_id=f"{self.comparison_id}-dynamics", target="dynamics", case_kind=self.case_kind,
            input={
                **self.inputs,
                "jama_profile": self.jama_profile,
                "alignment_mode": "uturn_trigger_aligned",
                "controller_kind": "autoware171_uturn_calibrated",
            },
            reason=f"DYNAMICS_AWSIM_COMPARE: {self.comparison_id}",
            meta={"comparison_id": self.comparison_id, "comparison_side": "dynamics"},
        )

    def awsim_test_case(self) -> TestCase:
        return TestCase(
            case_id=f"{self.comparison_id}-awsim", target="awsim", case_kind=self.case_kind,
            input={**self.inputs, "scenario_type": self.case_kind},
            reason=f"DYNAMICS_AWSIM_COMPARE: {self.comparison_id}",
            meta={"comparison_id": self.comparison_id, "comparison_side": "awsim"},
        )


class DynamicsAWSIMComparisonRunner:
    """Run both target adapters for one shared case, then normalize the delta."""

    def __init__(
        self,
        *,
        dynamics_backend: object,
        dynamics_interpreter: object,
        awsim_backend: object,
        awsim_interpreter: object,
    ) -> None:
        self.dynamics_backend = dynamics_backend
        self.dynamics_interpreter = dynamics_interpreter
        self.awsim_backend = awsim_backend
        self.awsim_interpreter = awsim_interpreter

    def run(self, comparison_case: ComparisonCase) -> tuple[EvaluationRecord, EvaluationRecord, EvaluationRecord]:
        dynamics_raw = self.dynamics_backend.run(comparison_case.dynamics_test_case())
        dynamics = self.dynamics_interpreter.interpret_raw_run_result(dynamics_raw)
        awsim_raw = self.awsim_backend.run(comparison_case.awsim_test_case())
        awsim = self.awsim_interpreter.interpret_raw_run_result(awsim_raw)
        return dynamics, awsim, build_comparison_record(comparison_case, dynamics, awsim)


def build_comparison_record(
    comparison_case: ComparisonCase,
    dynamics: EvaluationRecord,
    awsim: EvaluationRecord,
) -> EvaluationRecord:
    comparable = dynamics.status is RunStatus.SUCCESS and awsim.status is RunStatus.SUCCESS
    dynamics_theory = build_theory_metrics(
        case_kind=comparison_case.case_kind,
        values=comparison_case.inputs,
    )
    output: dict[str, object] = {
        "comparison_status": "comparable" if comparable else "revalidation_unavailable",
        "comparison_basis": "shared_inputs_trigger_aligned_obb_autoware_calibrated",
        "full_awsim_equivalence": 0,
        "dynamics_controller_kind": dynamics.meta.get("controller_kind"),
        "dynamics_collision_model": dynamics.meta.get("collision_model"),
        "dynamics_status": dynamics.status.value,
        "awsim_status": awsim.status.value,
        "decision_mode": comparison_case.decision_mode,
        "dynamics_c_collision": dynamics.output.get("c_collision"),
        "dynamics_c_screening_candidate": dynamics.output.get("c_screening_candidate"),
        "dynamics_decision": dynamics.output.get(DECISION_METRICS[comparison_case.decision_mode]),
        "awsim_c_collision": awsim.output.get("c_collision"),
        "dynamics_min_ttc": dynamics.output.get("min_ttc"),
        "awsim_min_ttc": awsim.output.get("min_ttc"),
        "dynamics_theory_zone_a": dynamics.output.get("theory_zone_a"),
        "awsim_theory_zone_a": awsim.output.get("theory_zone_a", dynamics_theory.get("theory_zone_a")),
        "dynamics_theory_zone_b": dynamics.output.get("theory_zone_b"),
        "awsim_theory_zone_b": awsim.output.get("theory_zone_b", dynamics_theory.get("theory_zone_b")),
    }
    if comparable:
        output["collision_agree"] = int(output["dynamics_c_collision"] == output["awsim_c_collision"])
        output["decision_agree"] = int(output["dynamics_decision"] == output["awsim_c_collision"])
        output["decision_missed_awsim_collision"] = int(
            output["awsim_c_collision"] == 1 and output["dynamics_decision"] == 0
        )
        output["min_ttc_delta_sec"] = _numeric_delta(output["dynamics_min_ttc"], output["awsim_min_ttc"])
        output["jama_zone_a_agree"] = int(output["dynamics_theory_zone_a"] == output["awsim_theory_zone_a"])
        output["jama_zone_b_agree"] = int(output["dynamics_theory_zone_b"] == output["awsim_theory_zone_b"])
    else:
        output.update(
            collision_agree=None, decision_agree=None, decision_missed_awsim_collision=None,
            min_ttc_delta_sec=None, jama_zone_a_agree=None, jama_zone_b_agree=None,
        )

    evidence = _prefixed_evidence("dynamics", dynamics.evidence)
    evidence.update(_prefixed_evidence("awsim", awsim.evidence))
    status = RunStatus.SUCCESS if comparable else RunStatus.ANALYSIS_ERROR
    return EvaluationRecord(
        case_id=comparison_case.comparison_id, target="dynamics_awsim_compare", case_kind=comparison_case.case_kind,
        status=status, input={**comparison_case.inputs, "jama_profile": comparison_case.jama_profile,
                              "comparison_id": comparison_case.comparison_id}, output=output, evidence=evidence,
        meta=ensure_evaluation_meta(
            {"comparison_id": comparison_case.comparison_id, "dynamics_case_id": dynamics.case_id,
             "awsim_case_id": awsim.case_id, "dynamics_meta": dict(dynamics.meta), "awsim_meta": dict(awsim.meta)},
            source_module="orchestration.dynamics_awsim_comparison",
        ),
    )


def _numeric_delta(left: object, right: object) -> float | None:
    try:
        return float(left) - float(right)
    except (TypeError, ValueError):
        return None


def _prefixed_evidence(prefix: str, evidence: Mapping[str, str]) -> dict[str, str]:
    return {f"{prefix}_{key}": value for key, value in evidence.items()}


__all__ = ["DECISION_METRICS", "ComparisonCase", "DynamicsAWSIMComparisonRunner", "build_comparison_record"]
