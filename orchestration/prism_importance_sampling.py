"""Fixed-sample runner comparing ordinary and importance-sampled PRISM paths."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from contracts.sampling import ProposalCandidate
from contracts.statistics import StatisticalRequest
from evaluation.importance_sampling import evaluate_standard_importance_sampling_request
from orchestration.fixed_importance_sampling import run_fixed_importance_sampling_comparison
from evaluation.weighted_sequential import evaluate_weighted_sequential_request
from orchestration.proposal_updates import ProposalUpdateManager
from orchestration.sampling_plan import SamplingPlan, load_sampling_plan
from orchestration.worker_loop import WorkerLoop
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from runtime.repository.proposal_history import ProposalHistoryRepository
from targets.prism.model_catalog import get_model_definition
from targets.prism.proposal import build_proposal_candidates


@dataclass(frozen=True, slots=True)
class PrismImportanceSamplingRunConfig:
    sampling_plan_path: str | Path
    output_root: str | Path
    max_samples: int | None = None
    metric: str = "c_failure"
    confidence: float = 0.95
    target_width: float | None = None
    stop_mode: str = "fixed_samples"
    sequential_options: Mapping[str, object] = field(default_factory=dict)
    seed: int | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_samples is not None and self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        if self.stop_mode not in {"fixed_samples", "importance_width", "weighted_sequential"}:
            raise ValueError("stop_mode must be fixed_samples, importance_width, or weighted_sequential")
        if self.stop_mode == "importance_width" and self.target_width is None:
            raise ValueError("target_width is required for importance_width")
        object.__setattr__(self, "sequential_options", dict(self.sequential_options))


class PrismImportanceSamplingRunner:
    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def run_fixed_samples(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        if config.stop_mode != "fixed_samples":
            raise ValueError("run_fixed_samples requires stop_mode='fixed_samples'")
        if config.max_samples is None:
            raise ValueError("max_samples is required for fixed_samples")
        plan = load_sampling_plan(config.sampling_plan_path)
        if plan.target != "prism":
            raise ValueError("sampling plan target must be 'prism'")
        repository = ImportanceSamplingResultsRepository(config.output_root)
        return run_fixed_importance_sampling_comparison(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            repository=repository,
            max_samples=config.max_samples,
            build_case=lambda run_kind, index: self._build_case(
                run_kind=run_kind, index=index, plan=plan, config=config,
            ),
            metric=config.metric,
            confidence=config.confidence,
            target_width=config.target_width,
            plan_id=plan.experiment_id,
        )

    def run(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        """Run a fixed-Q experiment without changing or approving Q."""
        if config.stop_mode == "fixed_samples":
            return self.run_fixed_samples(config)
        return self.run_until_stop(config)

    def run_until_stop(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        """Append fixed-Q importance samples until a configured stop rule fires.

        Only the importance run is extended here.  An ordinary baseline is a
        separate fixed-sample comparison, so its cost cannot silently affect a
        sequential decision.
        """
        if config.max_samples is None:
            raise ValueError("max_samples is required as a safety cap for stop modes")
        if config.stop_mode not in {"importance_width", "weighted_sequential"}:
            raise ValueError("run_until_stop requires a non-fixed stop mode")
        plan = load_sampling_plan(config.sampling_plan_path)
        if plan.target != "prism":
            raise ValueError("sampling plan target must be 'prism'")
        if plan.sampling_mode != "manual_proposal":
            raise ValueError(
                "sequential stop modes require a human-selected fixed Q "
                "(proposal.mode='manual_proposal')"
            )
        repository = ImportanceSamplingResultsRepository(config.output_root)
        records = repository.load_records("importance")
        report = self._stop_report(records, config, plan) if records else None
        while (report is None or not report.sufficient) and len(records) < config.max_samples:
            self._run_missing(
                run_kind="importance",
                existing_count=len(records),
                max_samples=len(records) + 1,
                plan=plan,
                config=config,
                repository=repository,
            )
            records = repository.load_records("importance")
            report = self._stop_report(records, config, plan)
        if report is None:
            raise RuntimeError("stop-mode run did not produce an evaluation report")
        stopped_by_rule = report.sufficient
        result = {
            "plan_id": plan.experiment_id,
            "stop_mode": config.stop_mode,
            "sample_count": len(records),
            "max_samples": config.max_samples,
            "stopped_by_rule": stopped_by_rule,
            "stop_reason": report.next_action if stopped_by_rule else "max_samples_reached",
            "statistical_report": _report_payload(report),
            "importance_records_path": str(repository.records_path("importance")),
        }
        comparison_path = repository.save_comparison(result)
        result["comparison_path"] = str(comparison_path)
        return result

    @staticmethod
    def _stop_report(
        records,
        config: PrismImportanceSamplingRunConfig,
        plan: SamplingPlan,
    ):
        if config.stop_mode == "importance_width":
            return evaluate_standard_importance_sampling_request(
                records,
                StatisticalRequest(
                    method="ordinary_importance_sampling",
                    metric=config.metric,
                    confidence=config.confidence,
                    target_width=config.target_width,
                ),
            )
        configured_options = plan.sequential_decision
        if configured_options is None:
            configured_options = {}
        if not isinstance(configured_options, Mapping):
            raise ValueError("sequential_decision must be a mapping when weighted_sequential is used")
        return evaluate_weighted_sequential_request(
            records,
            StatisticalRequest(
                method="weighted_sequential",
                metric=config.metric,
                confidence=config.confidence,
                options={**dict(configured_options), **dict(config.sequential_options)},
            ),
        )

    def run_suggest_pilot(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        """Evaluate human-bounded Q candidates without applying any of them."""
        plan = load_sampling_plan(config.sampling_plan_path)
        search = plan.proposal_search
        if not isinstance(search, Mapping) or search.get("enabled") is not True:
            raise ValueError("proposal_search.enabled must be true for suggest pilot")
        if search.get("policy", "suggest") != "suggest":
            raise ValueError("run_suggest_pilot requires proposal_search.policy='suggest'")
        evaluation = search.get("evaluation", {})
        if not isinstance(evaluation, Mapping):
            raise ValueError("proposal_search.evaluation must be a mapping")
        configured_samples = evaluation.get("pilot_samples", config.max_samples)
        if configured_samples is None:
            raise ValueError("pilot_samples or max_samples is required for suggest pilot")
        pilot_samples = int(configured_samples)
        if pilot_samples < 1:
            raise ValueError("proposal_search.evaluation.pilot_samples must be positive")
        metric = str(evaluation.get("metric", config.metric))

        definition = self._model_definition(plan)
        candidates = build_proposal_candidates(
            plan,
            definition,
            target_constants=self._target_constants(plan),
        )
        selected_ids = evaluation.get("candidate_ids")
        if selected_ids is not None:
            if not isinstance(selected_ids, list) or not all(isinstance(value, str) for value in selected_ids):
                raise ValueError("proposal_search.evaluation.candidate_ids must be a list of strings")
            selected_id_set = set(selected_ids)
            candidates = tuple(candidate for candidate in candidates if candidate.proposal_id in selected_id_set)
            if len(candidates) != len(selected_id_set):
                raise ValueError("proposal_search.evaluation.candidate_ids contains an unknown candidate")
        repository = ImportanceSamplingResultsRepository(config.output_root)
        history = ProposalHistoryRepository(Path(config.output_root) / "proposal_history.jsonl")
        updates = ProposalUpdateManager(repository=history, mode="suggest")
        results: list[dict[str, object]] = []
        for candidate in candidates:
            records = repository.load_candidate_records(candidate.proposal_id)
            self._run_missing_candidate(
                candidate=candidate,
                existing_count=len(records),
                max_samples=pilot_samples,
                plan=plan,
                config=config,
                repository=repository,
            )
            report = evaluate_standard_importance_sampling_request(
                repository.load_candidate_records(candidate.proposal_id),
                StatisticalRequest(
                    method="ordinary_importance_sampling",
                    metric=metric,
                    confidence=config.confidence,
                ),
            )
            if not self._evaluation_already_recorded(history, candidate):
                updates.record_evaluation(
                    candidate,
                    report=report,
                    target_diagnostics=candidate.diagnostics,
                )
                updates.propose(candidate)
            records = repository.load_candidate_records(candidate.proposal_id)
            results.append({
                "proposal_id": candidate.proposal_id,
                "proposal_version": candidate.proposal_version,
                "adjustments": list(candidate.diagnostics.get("adjustments", ())),
                "report": _report_payload(report),
                "importance_weight_status_counts": _weight_status_counts(records),
                "records_path": str(repository.candidate_records_path(candidate.proposal_id)),
            })
        return {"plan_id": plan.experiment_id, "mode": "suggest", "candidates": results}

    def _run_missing(self, *, run_kind: str, existing_count: int, max_samples: int | None = None,
                     plan: SamplingPlan,
                     config: PrismImportanceSamplingRunConfig,
                     repository: ImportanceSamplingResultsRepository) -> None:
        loop = WorkerLoop(
            backend=self.backend, result_interpreter=self.result_interpreter,
            result_sink=lambda record: repository.append_record(run_kind, record),
        )
        limit = config.max_samples if max_samples is None else max_samples
        if limit is None:
            raise ValueError("max_samples is required")
        for index in range(existing_count + 1, limit + 1):
            case = self._build_case(run_kind=run_kind, index=index, plan=plan, config=config)
            loop.run_once(case)

    def _run_missing_candidate(
        self,
        *,
        candidate: ProposalCandidate,
        existing_count: int,
        max_samples: int,
        plan: SamplingPlan,
        config: PrismImportanceSamplingRunConfig,
        repository: ImportanceSamplingResultsRepository,
    ) -> None:
        loop = WorkerLoop(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            result_sink=lambda record: repository.append_candidate_record(candidate.proposal_id, record),
        )
        for index in range(existing_count + 1, max_samples + 1):
            loop.run_once(self._build_case(
                run_kind="candidate", index=index, plan=plan, config=config, candidate=candidate,
            ))

    @staticmethod
    def _build_case(*, run_kind: str, index: int, plan: SamplingPlan,
                    config: PrismImportanceSamplingRunConfig,
                    candidate: ProposalCandidate | None = None) -> TestCase:
        input_payload = {
            **dict(config.params), "execution_kind": "sample_path", "record_kind": "sample",
            "sample_index": index,
        }
        if run_kind == "ordinary" and config.seed is not None:
            input_payload["sampling_engine"] = "target_adapter"
            input_payload["sampling_seed"] = _sample_seed(config.seed, index)
        sampling_meta: dict[str, object] = {
            "plan_id": plan.experiment_id, "target_measure_id": plan.target_measure_id,
            "sampling_mode": "uniform" if run_kind == "ordinary" else (
                "adaptive" if candidate is not None else "manual_proposal"
            ),
            "importance_weight_status": "not_applicable" if run_kind == "ordinary" else "unavailable",
        }
        if run_kind in {"importance", "candidate"}:
            input_payload["sampling_plan_path"] = str(Path(config.sampling_plan_path).resolve())
            if config.seed is not None:
                input_payload["sampling_seed"] = _sample_seed(config.seed, index)
            if candidate is None:
                sampling_meta["proposal_id"] = plan.proposal_id
            else:
                input_payload.update({
                    "proposal_definition": candidate.definition,
                    "proposal_id": candidate.proposal_id,
                    "proposal_version": candidate.proposal_version,
                    "proposal_sampling_mode": "adaptive",
                })
                sampling_meta.update({
                    "proposal_id": candidate.proposal_id,
                    "proposal_version": candidate.proposal_version,
                })
        target_definition = plan.target_measure_definition
        target_constants = (
            target_definition.get("constants", {})
            if isinstance(target_definition, Mapping)
            else {}
        )
        if not isinstance(target_constants, Mapping):
            raise ValueError("PRISM target_measure.definition.constants must be a mapping")
        input_payload = {**dict(target_constants), **input_payload}
        return TestCase(
            case_id=(
                f"{plan.experiment_id}-{candidate.proposal_id if candidate else run_kind}-{index:05d}"
            ), target="prism",
            case_kind="simple_reliability_dtmc", input=input_payload,
            reason=f"PRISM_IMPORTANCE_{run_kind.upper()}: {plan.experiment_id} sample={index}",
            meta={"sampling": sampling_meta, "source_module": "orchestration.prism_importance_sampling"},
        )

    @staticmethod
    def _target_constants(plan: SamplingPlan) -> Mapping[str, object]:
        target_definition = plan.target_measure_definition
        target_constants = (
            target_definition.get("constants", {})
            if isinstance(target_definition, Mapping)
            else {}
        )
        if not isinstance(target_constants, Mapping):
            raise ValueError("PRISM target_measure.definition.constants must be a mapping")
        return target_constants

    @staticmethod
    def _model_definition(plan: SamplingPlan):
        target_definition = plan.target_measure_definition
        if not isinstance(target_definition, Mapping):
            raise ValueError("PRISM target_measure.definition must be a mapping")
        model_id = target_definition.get("model")
        if not isinstance(model_id, str):
            raise ValueError("PRISM target_measure.definition.model must be a string")
        return get_model_definition(model_id)

    @staticmethod
    def _evaluation_already_recorded(
        history: ProposalHistoryRepository, candidate: ProposalCandidate,
    ) -> bool:
        return any(
            event.get("event_type") == "candidate_evaluated"
            and event.get("proposal_id") == candidate.proposal_id
            and event.get("proposal_version") == candidate.proposal_version
            for event in history.read_events()
        )


def _report_payload(report) -> dict[str, object]:
    return {
        "method": report.method,
        "metric": report.metric,
        "sample_count": report.sample_count,
        "estimate": report.estimate,
        "interval": list(report.interval) if report.interval is not None else None,
        "sufficient": report.sufficient,
        "next_action": report.next_action,
        "diagnostics": dict(report.diagnostics),
    }


def _weight_status_counts(records) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        sampling = record.meta.get("sampling")
        status = sampling.get("importance_weight_status") if isinstance(sampling, Mapping) else None
        label = str(status) if status is not None else "missing"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _sample_seed(experiment_seed: int, sample_index: int) -> int:
    """Give each replicate a disjoint deterministic seed block.

    The same ``experiment_seed`` still gives ordinary and proposal runs common
    random numbers, but distinct outer seeds cannot share sample-index seeds.
    """
    if sample_index < 1:
        raise ValueError("sample_index must be positive")
    return experiment_seed * 1_000_000 + sample_index


__all__ = ["PrismImportanceSamplingRunConfig", "PrismImportanceSamplingRunner"]
