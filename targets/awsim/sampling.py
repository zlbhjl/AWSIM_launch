"""Input-space P/Q sampling for AWSIM U-turn experiments.

The initial adapter deliberately samples only the external ego/NPC speeds.
``dx0`` remains a human-selected scenario condition, rather than inventing a
probability law for an unobserved U-turn timing variable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from orchestration.importance.input_law import InputVariables, draw_proposal, draw_target, parse_input_laws
from orchestration.sampling_plan import SamplingPlan


_SPEED_NAMES = ("ego_speed", "npc_speed")
# The input variables AWSIM declares to the shared input laws.
INPUT_VARIABLES = InputVariables(names=_SPEED_NAMES, label="AWSIM")


@dataclass(frozen=True, slots=True)
class AWSIMInputSample:
    values: dict[str, float]
    sampling_metadata: dict[str, object]


class AWSIMInputSampler:
    """Interpret AWSIM-specific speed distributions in a common SamplingPlan."""

    def __init__(self, plan: SamplingPlan):
        if plan.target != "awsim":
            raise ValueError("sampling plan target must be 'awsim'")
        if plan.sampling_mode != "manual_proposal":
            raise ValueError("AWSIM importance sampling currently requires manual_proposal")
        definition = _mapping(plan.target_measure_definition, "awsim target_measure.definition")
        self.plan = plan
        self.case_kind = _string(definition.get("case_kind", "uturn"), "case_kind")
        if self.case_kind != "uturn":
            raise ValueError("AWSIM importance sampling currently supports case_kind 'uturn'")
        fixed_inputs = dict(_mapping(definition.get("fixed_inputs"), "awsim fixed_inputs"))
        if "dx0" not in fixed_inputs:
            raise ValueError("awsim fixed_inputs.dx0 is required")
        self.fixed_inputs = fixed_inputs
        # Container and CLI execution options are deliberately not scenario
        # inputs.  They are selected by the runtime profile at the CLI edge.
        _mapping(definition.get("execution", {}), "awsim execution")
        _mapping(definition.get("parameters"), "awsim target_measure.definition.parameters")
        self.laws = parse_input_laws(
            definition, _mapping(plan.proposal_definition, "awsim proposal.definition"), INPUT_VARIABLES,
        )
        self.importance_weight_upper_bound = self.laws.weight_upper_bound

    def sample_target(self, *, seed: int | None) -> AWSIMInputSample:
        return AWSIMInputSample(
            values=draw_target(self.laws, seed=seed),
            sampling_metadata={
                "plan_id": self.plan.experiment_id,
                "target_measure_id": self.plan.target_measure_id,
                "sampling_mode": "uniform",
                "importance_weight_status": "not_applicable",
            },
        )

    def sample_proposal(self, *, seed: int | None) -> AWSIMInputSample:
        draw = draw_proposal(self.laws, seed=seed)
        log_p, log_q = draw.log_p, draw.log_q
        return AWSIMInputSample(
            values=draw.values,
            sampling_metadata={
                "plan_id": self.plan.experiment_id,
                "target_measure_id": self.plan.target_measure_id,
                "sampling_mode": "manual_proposal",
                "proposal_id": self.plan.proposal_id,
                "importance_weight_status": "available",
                "log_p": log_p,
                "log_q": log_q,
                "log_importance_weight": log_p - log_q,
                "importance_weight_upper_bound": self.importance_weight_upper_bound,
            },
        )


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value


__all__ = ["AWSIMInputSample", "AWSIMInputSampler"]
