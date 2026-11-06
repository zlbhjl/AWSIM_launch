"""Importance sampling of PRISM paths: hooks of the shared runner, plus PRISM's proposal suggestions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from contracts.importance_sampling_plugin import CaseRequest, PreparedTarget
from contracts.sampling import ProposalCandidate
from contracts.statistics import StatisticalRequest
from contracts.target_plugin import TargetComponents
from evaluation.importance_sampling import evaluate_standard_importance_sampling_request
from orchestration.importance.runner import ImportanceRunConfig, ImportanceSamplingRunner, report_payload
from orchestration.importance.weights import compose_internal_weight
from orchestration.proposal_updates import ProposalUpdateManager
from orchestration.sampling_plan import SamplingPlan, load_sampling_plan
from orchestration.seeding import legacy_sample_seed
from orchestration.worker_loop import WorkerLoop
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from runtime.repository.proposal_history import ProposalHistoryRepository
from targets.prism.model_catalog import get_model_definition
from targets.prism.proposal import build_proposal_candidates
from targets.prism.sampling import internal_proposal


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

    def shared(self) -> ImportanceRunConfig:
        return ImportanceRunConfig(
            sampling_plan_path=self.sampling_plan_path, output_root=self.output_root,
            max_samples=self.max_samples, metric=self.metric, confidence=self.confidence,
            seed=self.seed, params=self.params, stop_mode=self.stop_mode,
            target_width=self.target_width, sequential_options=self.sequential_options,
        )


class PrismImportanceSamplingRunner:
    """Compatible entry point: the shared runner with the PRISM hooks, and the suggest pilot."""

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def _shared(self) -> ImportanceSamplingRunner:
        return ImportanceSamplingRunner(
            backend=self.backend, result_interpreter=self.result_interpreter, hooks=PrismImportanceSampling(),
        )

    def run_fixed_samples(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        return self._shared().run_fixed_samples(config.shared())

    def run(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        """Run a fixed-Q experiment without changing or approving Q."""
        return self._shared().run(config.shared())

    def run_until_stop(self, config: PrismImportanceSamplingRunConfig) -> dict[str, object]:
        return self._shared().run_until_stop(config.shared())

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
                "report": report_payload(report),
                "importance_weight_status_counts": _weight_status_counts(records),
                "records_path": str(repository.candidate_records_path(candidate.proposal_id)),
            })
        return {"plan_id": plan.experiment_id, "mode": "suggest", "candidates": results}

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
            result_sink=lambda record: repository.append_candidate_record(
                candidate.proposal_id, compose_internal_weight(record, internal_proposal(candidate.definition)),
            ),
        )
        for index in range(existing_count + 1, max_samples + 1):
            loop.run_once(prism_case(
                run_kind="candidate", index=index, plan=plan, seed=_sample_seed(config.seed, index),
                params=config.params, sampling_plan_path=str(Path(config.sampling_plan_path).resolve()),
                candidate=candidate,
            ))

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




def prism_case(
    *,
    run_kind: str,
    index: int,
    plan: SamplingPlan,
    seed: int | None,
    params: Mapping[str, object],
    sampling_plan_path: str,
    candidate: ProposalCandidate | None = None,
) -> TestCase:
    """One PRISM path: under P (ordinary), the plan's Q (importance) or a candidate Q."""
    input_payload = {
        **dict(params), "execution_kind": "sample_path", "record_kind": "sample",
        "sample_index": index,
    }
    if run_kind == "ordinary" and seed is not None:
        input_payload["sampling_engine"] = "target_adapter"
        input_payload["sampling_seed"] = seed
    sampling_meta: dict[str, object] = {
        "plan_id": plan.experiment_id, "target_measure_id": plan.target_measure_id,
        "sampling_mode": "uniform" if run_kind == "ordinary" else (
            "adaptive" if candidate is not None else "manual_proposal"
        ),
        "importance_weight_status": "not_applicable" if run_kind == "ordinary" else "unavailable",
    }
    if run_kind in {"importance", "candidate"}:
        input_payload["sampling_plan_path"] = sampling_plan_path
        if seed is not None:
            input_payload["sampling_seed"] = seed
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
        meta={"sampling": sampling_meta, "source_module": "targets.prism.importance_sampling"},
    )


def _weight_status_counts(records) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        sampling = record.meta.get("sampling")
        status = sampling.get("importance_weight_status") if isinstance(sampling, Mapping) else None
        label = str(status) if status is not None else "missing"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _sample_seed(experiment_seed: int | None, sample_index: int) -> int | None:
    return legacy_sample_seed(experiment_seed, sample_index)


class PrismImportanceSampling:
    """Importance-sampling hooks of the PRISM plugin: Q over the transitions of the DTMC paths."""

    def add_arguments(self, parser) -> None:
        parser.add_argument("--proposal-mode", choices=("fixed_samples", "suggest"), default="fixed_samples")
        parser.add_argument("--steps", type=int, default=20)
        parser.add_argument("--prism-executable", default="prism")

    def build_components(self, args) -> TargetComponents:
        from targets.prism.backend import PrismBackend, PrismBackendConfig
        from targets.prism.result_interpreter import PrismResultInterpreter

        return TargetComponents(
            backend=PrismBackend(PrismBackendConfig(
                output_root=Path(args.output_root) / "artifacts", prism_executable=args.prism_executable,
            )),
            result_interpreter=PrismResultInterpreter(),
        )

    def run_options(self, args) -> dict[str, object]:
        return {"params": {"model": "simple_reliability_dtmc", "steps": args.steps}}

    def alternative_run(self, args) -> dict[str, object] | None:
        """``--proposal-mode suggest``: evaluate candidate Qs without applying any."""
        if args.proposal_mode != "suggest":
            return None
        if args.stop_mode != "fixed_samples":
            raise ValueError("--stop-mode is available only for fixed-Q experiments")
        components = self.build_components(args)
        runner = PrismImportanceSamplingRunner(
            backend=components.backend, result_interpreter=components.result_interpreter,
        )
        return runner.run_suggest_pilot(PrismImportanceSamplingRunConfig(
            sampling_plan_path=args.sampling_plan, output_root=args.output_root,
            max_samples=args.max_samples, metric=args.metric or "c_failure", confidence=args.confidence,
            seed=args.sampling_seed, params=self.run_options(args)["params"],
        ))

    def prepare(self, plan: SamplingPlan, config) -> PreparedTarget:
        if plan.target != "prism":
            raise ValueError("sampling plan target must be 'prism'")
        return PreparedTarget(
            default_metric="c_failure",
            seed_policy="legacy",
            internal_proposal=internal_proposal(plan.proposal_definition),
        )

    def build_case(self, request: CaseRequest) -> TestCase:
        return prism_case(
            run_kind=request.run_kind, index=request.sample_index, plan=request.plan,
            seed=request.seeds["sample"], params=request.params,
            sampling_plan_path=request.sampling_plan_path,
        )


__all__ = [
    "PrismImportanceSampling",
    "PrismImportanceSamplingRunConfig",
    "PrismImportanceSamplingRunner",
    "prism_case",
]
