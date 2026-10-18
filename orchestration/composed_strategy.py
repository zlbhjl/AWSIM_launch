"""Strategies that combine an existing sampling design with a new stopping rule.

``--stopping-rule`` selects one of the rules in ``evaluation.stopping_rules``.
The sampling stays exactly as in the legacy strategies (independent inputs for
IID targets, repeated fixed parameters for PRISM-like targets); only the
decision of when to stop and what to report comes from the rule. The rule is
plugged into the strategy through the statistical-service slot the legacy
strategies already have.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from contracts.experiment import SampleKind, StoppingRule, combined_guarantee
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.ebstop import EBStopService
from evaluation.sprt import SPRTService
from evaluation.stopping_rules import (
    BinomialFixedStrict,
    BinomialSequentialAnytime,
    DKWFixedStrict,
    DKWSequentialAnytime,
    STOPPING_RULE_NAMES,
    ebstop_anytime,
    sprt_wald,
)
from orchestration.fixed_parameter_sampling import (
    FixedParameterSamplingStrategy,
    FixedParameterSamplingStrategyConfig,
)
from orchestration.random_parameter_sampling import (
    RandomParameterSamplingConfig,
    RandomParameterSamplingStrategy,
)

if TYPE_CHECKING:
    from orchestration.orchestrator import Orchestrator, OrchestratorConfig

# The --mode each rule belongs to, so the CLI options of that mode apply.
RULE_MODES = {
    "binomial_ci_fixed_strict": {"binomial_ci"},
    "binomial_ci_sequential_anytime": {"binomial_ci"},
    "dkw_fixed_strict": {"dkw", "dkw_fixed"},
    "dkw_sequential_anytime": {"dkw", "dkw_fixed"},
    "sprt_wald": {"sprt"},
    "ebstop_anytime": {"ebstop"},
}
DKW_QUANTILE = 0.05  # q of every DKW request in the framework


class StoppingRuleService:
    """Adapts a stopping rule to the statistical-service slot of the legacy strategies."""

    def __init__(self, rule: StoppingRule, sample_kind: SampleKind) -> None:
        self.rule = rule
        self.sample_kind = sample_kind

    def evaluate_request(self, data: pd.DataFrame | None, request: StatisticalRequest) -> StatisticalReport:
        completed = 0 if data is None else int(len(data))
        decision = self.rule.decide(data, issued=completed)
        report = decision.report or StatisticalReport(
            method=self.rule.spec.method, metric=request.metric, sample_count=completed, estimate=None,
            interval=None, sufficient=False, next_action="collect_more_samples",
            diagnostics={"status": "pending", "stopping_rule": self.rule.spec.name},
        )
        report.diagnostics["guarantee"] = combined_guarantee(self.rule.spec, self.sample_kind).value
        report.diagnostics["sample_kind"] = self.sample_kind.value
        if decision.stop and report.next_action not in {"stop", "stop_max_samples", "error"}:
            report.next_action = "stop"
        return report


def build_stopping_rule(config: "OrchestratorConfig", metrics: dict[str, object]) -> StoppingRule:
    name = config.stopping_rule
    max_samples = int(config.max_samples or 0)
    lower_support = metrics.get("minimum_dkw_value")
    lower_support = -math.inf if lower_support is None else float(lower_support)
    if name == "binomial_ci_fixed_strict":
        return BinomialFixedStrict(metric=metrics["binary"], sample_size=max_samples,
                                   confidence=config.binomial_confidence)
    if name == "binomial_ci_sequential_anytime":
        return BinomialSequentialAnytime(metric=metrics["binary"], target_width=config.binomial_target_width,
                                         max_samples=max_samples, confidence=config.binomial_confidence)
    if name == "dkw_fixed_strict":
        return DKWFixedStrict(metric=metrics["dkw"], sample_size=max_samples, q=DKW_QUANTILE,
                              confidence=config.prism_dkw_confidence, lower_support=lower_support)
    if name == "dkw_sequential_anytime":
        return DKWSequentialAnytime(metric=metrics["dkw"], target_width=config.prism_dkw_target_epsilon,
                                    max_samples=max_samples, q=DKW_QUANTILE,
                                    confidence=config.prism_dkw_confidence, lower_support=lower_support)
    if name == "sprt_wald":
        return sprt_wald(SPRTService(), StatisticalRequest(
            method="sprt", metric=metrics["sprt"], confidence=config.sprt_confidence,
            options={"p0": config.sprt_p0, "p1": config.sprt_p1, "beta": config.sprt_beta, "region": "custom",
                     "min_samples": config.sprt_min_samples or 0, "max_samples": max_samples},
        ))
    if name == "ebstop_anytime":
        return ebstop_anytime(EBStopService(), StatisticalRequest(
            method="ebstop", metric=metrics["ebstop"], confidence=config.ebstop_confidence,
            options={"epsilon": config.ebstop_epsilon, "value_range": config.ebstop_value_range,
                     "region": "custom", "max_samples": max_samples},
        ))
    raise ValueError(f"Unknown stopping rule: {name} (choose from {', '.join(STOPPING_RULE_NAMES)})")


def _request(rule: StoppingRule, metric: str, config: "OrchestratorConfig") -> StatisticalRequest:
    return StatisticalRequest(method=rule.spec.method, metric=metric, options={"stopping_rule": rule.spec.name})


def _metric_for(rule: StoppingRule, metrics: dict[str, object]) -> str:
    return str({"binomial_ci": metrics["binary"], "sprt": metrics["sprt"], "dkw": metrics["dkw"],
                "ebstop": metrics["ebstop"]}[rule.spec.method])


def build_composed_iid_strategy(orchestrator: "Orchestrator", config: "OrchestratorConfig") -> RandomParameterSamplingStrategy:
    from orchestration.orchestrator import _iid_statistical_metrics

    if config.max_samples is None:
        raise ValueError(f"{config.target} statistical sampling requires max_samples")
    metrics = _iid_statistical_metrics(config)
    rule = build_stopping_rule(config, metrics)
    return RandomParameterSamplingStrategy(
        RandomParameterSamplingConfig(
            target=config.target, case_kind=config.case_kind, params=dict(config.params),
            max_samples=int(config.max_samples), experiment_id=config.experiment_id,
            case_id_prefix=config.case_id, reason_prefix=f"{config.target.upper()}_IID_SAMPLING",
            tags=tuple(config.tags), input_distribution=config.dynamics_input_distribution,
            seed=config.sampling_seed,
        ),
        dataset_repository=orchestrator._build_dataset_repository(config),
        statistical_service=StoppingRuleService(rule, SampleKind.IID_UNIFORM),
        statistical_request=_request(rule, _metric_for(rule, metrics), config),
    )


def build_composed_fixed_parameter_strategy(
    orchestrator: "Orchestrator", config: "OrchestratorConfig"
) -> FixedParameterSamplingStrategy:
    from orchestration.orchestrator import _fixed_parameter_reason_prefix, _runs_model_check_once

    if config.max_samples is None:
        raise ValueError(f"{config.target.upper()} fixed sampling requires max_samples")
    metrics = {
        "binary": config.binomial_target if config.stopping_rule.startswith("binomial") else config.sprt_target,
        "sprt": config.sprt_target,
        "dkw": config.prism_dkw_target_metric,
        "ebstop": config.ebstop_target,
        "minimum_dkw_value": None,
    }
    rule = build_stopping_rule(config, metrics)
    return FixedParameterSamplingStrategy(
        FixedParameterSamplingStrategyConfig(
            target=config.target, case_kind=config.case_kind, params=dict(config.params),
            max_samples=int(config.max_samples), experiment_id=config.experiment_id,
            case_id_prefix=config.case_id, reason_prefix=_fixed_parameter_reason_prefix(config.target),
            tags=tuple(config.tags), run_model_check_once=_runs_model_check_once(config.target),
        ),
        dataset_repository=orchestrator._build_dataset_repository(config),
        statistical_service=StoppingRuleService(rule, SampleKind.FIXED_POINT_IID),
        statistical_request=_request(rule, _metric_for(rule, metrics), config),
    )


__all__ = [
    "RULE_MODES",
    "StoppingRuleService",
    "build_composed_fixed_parameter_strategy",
    "build_composed_iid_strategy",
    "build_stopping_rule",
]
