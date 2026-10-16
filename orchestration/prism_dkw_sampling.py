from __future__ import annotations

from contracts.execution import TestCase
from contracts.statistics import StatisticalReport
from evaluation.dkw import DKWService
from orchestration.dkw_mode import DKWModeRunner, DKWModeState
from orchestration.fixed_parameter_sampling import (
    FixedParameterSamplingStrategy,
    FixedParameterSamplingStrategyConfig,
    _safe_identifier,
)


class _RecordingDKWService:
    """Wraps DKWService to expose the last StatisticalReport it produced.

    DKWModeRunner.handle_sequential/handle_fixed only ever return a plain
    dict payload (next task or stop signal), not the StatisticalReport
    itself. FixedParameterSamplingStrategy's statistical_report_payload()
    needs the report, so this wrapper captures it as a side effect instead
    of changing DKWModeRunner's (already correct, AWSIM-shared) interface.
    """

    def __init__(self, inner: DKWService) -> None:
        self._inner = inner
        self.last_report: StatisticalReport | None = None

    def evaluate_request(self, data, request):
        report = self._inner.evaluate_request(data, request)
        self.last_report = report
        return report

    def evaluate_request_multiple_summary(self, data, *, metrics, request):
        summary = self._inner.evaluate_request_multiple_summary(
            data,
            metrics=metrics,
            request=request,
        )
        reports = summary.get("reports")
        if isinstance(reports, dict) and reports:
            self.last_report = next(iter(reports.values()))
        return summary


class PrismDkwSamplingStrategy(FixedParameterSamplingStrategy):
    """Fixed-point DKW sampling for target=prism.

    Reuses DKWModeRunner (the same stage-based alpha-spending engine AWSIM's
    --mode dkw/dkw_fixed already use) by always handing it the same fixed
    parameter point instead of a randomly sampled one. This subclasses
    FixedParameterSamplingStrategy (rather than composing it) purely so the
    orchestrator's dispatch loop, which special-cases
    `isinstance(strategy, FixedParameterSamplingStrategy)` to allow multiple
    tasks per tick, still applies here.
    """

    def __init__(
        self,
        config: FixedParameterSamplingStrategyConfig,
        *,
        dataset_repository: object | None,
        dkw_mode_runner: DKWModeRunner,
        sequential: bool,
    ) -> None:
        super().__init__(
            config,
            dataset_repository=dataset_repository,
            statistical_service=None,
            statistical_request=None,
        )
        self.dkw_mode_runner = dkw_mode_runner
        self.sequential = sequential
        self.dkw_state = DKWModeState()

    def next_test_case(self) -> TestCase | None:
        self.waiting_for_result = False
        if self.config.run_model_check_once:
            model_check = self._load_matching_records("exact_model_check")
            if model_check is None or model_check.empty:
                if not self._model_check_issued:
                    self._model_check_issued = True
                    return self._build_model_check_case()
                self.waiting_for_result = True
                return None
            if not self._capture_exact_model_check(model_check):
                self.stop_reason = "PRISM exact model check failed"
                return None

        df_dataset = self._load_matching_records("sample")
        has_samples = df_dataset is not None and not df_dataset.empty
        if self.sequential and not has_samples and self.dkw_state.dispatched_task_count == 0:
            # DKWModeRunner.handle_sequential evaluates *before* dispatching a
            # new task, unlike handle_fixed. With zero prior samples that
            # evaluation call fails outright (DKWService has nothing to
            # compute a bound from) and handle_sequential treats that as a
            # terminal "stop" - meaning a true cold start would stop after the
            # exact_model_check without ever issuing a single sample-path
            # task. AWSIM's usage never hits this because dkw mode is only
            # ever run against a dataset that already has prior history from
            # other exploration modes; PRISM's fixed-point sampling has no
            # such bootstrap, so issue the very first sample directly instead
            # of routing it through handle_sequential's evaluate-then-dispatch
            # order.
            payload, self.dkw_state.random_index, self.dkw_state.dispatched_task_count = (
                self.dkw_mode_runner.issue_region_aware_random_task(
                    reason=f"SMC: Sequential-DKW Sampling (Stage {self.dkw_state.stage})",
                    get_random_point=self._get_fixed_point,
                    random_index=self.dkw_state.random_index,
                    base_index=0,
                    dispatched_task_count=self.dkw_state.dispatched_task_count,
                )
            )
        else:
            handler = (
                self.dkw_mode_runner.handle_sequential
                if self.sequential
                else self.dkw_mode_runner.handle_fixed
            )
            payload = handler(
                df_dataset,
                state=self.dkw_state,
                get_random_point=self._get_fixed_point,
            )
        if payload.get("system_command") == "stop":
            self.latest_statistical_report = getattr(
                self.dkw_mode_runner.dkw_service, "last_report", None
            )
            self.stop_reason = str(payload.get("reason") or "DKW sampling complete")
            return None

        self.latest_statistical_report = getattr(
            self.dkw_mode_runner.dkw_service, "last_report", None
        )
        sample_index = int(payload["sample_index"])
        prefix = self.config.case_id_prefix or (
            f"{self.config.case_kind}-{self.experiment_id}"
        )
        case_id = f"{_safe_identifier(prefix)}-dkw-{sample_index:05d}"
        task_input = {key: value for key, value in payload.items() if key != "reason"}
        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input=task_input,
            tags=list(self.config.tags),
            reason=str(payload.get("reason", "")),
            meta={
                "source_module": "orchestration.prism_dkw_sampling",
                "experiment_id": self.experiment_id,
                "sampling_signature": self.sampling_signature,
                "sample_index": sample_index,
            },
        )

    def _get_fixed_point(self, index: int) -> dict[str, object]:
        return {
            **self.params,
            "execution_kind": "sample_path",
            "record_kind": "sample",
            "experiment_id": self.experiment_id,
            "sampling_signature": self.sampling_signature,
            "sample_index": index,
        }


__all__ = ["PrismDkwSamplingStrategy", "_RecordingDKWService"]
