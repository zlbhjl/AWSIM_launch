from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from contracts.execution import TestCase
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.binomial_ci import BinomialCIService

# exact_model_check only ever reports failure *probabilities*
# (eventual_failure_probability / bounded_failure_probability). Comparing those
# against a report for any other metric (e.g. EBStop/DKW tracking a step count
# like steps_to_failure_capped) mixes units and produces a meaningless
# "error". Only the metric whose semantics match a failure probability is
# eligible for the comparison.
_EXACT_PROBABILITY_METRIC = "c_failure"


@dataclass(frozen=True)
class FixedParameterSamplingStrategyConfig:
    target: str
    case_kind: str
    params: Mapping[str, object]
    max_samples: int
    experiment_id: str | None = None
    case_id_prefix: str | None = None
    reason_prefix: str = "FIXED_PARAMETER_SAMPLING"
    tags: tuple[str, ...] = field(default_factory=tuple)
    run_model_check_once: bool = False

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("target must not be empty")
        if not self.case_kind.strip():
            raise ValueError("case_kind must not be empty")
        if int(self.max_samples) < 1:
            raise ValueError("max_samples must be a positive integer")


class FixedParameterSamplingStrategy:
    """Emit independent TestCases with one fixed parameter set.

    Statistical stopping is optional so the generator can be reused by targets
    other than PRISM. When configured, only rows carrying this strategy's
    experiment id and sampling signature are passed to the evaluator.
    """

    def __init__(
        self,
        config: FixedParameterSamplingStrategyConfig,
        *,
        dataset_repository: object | None = None,
        statistical_service: BinomialCIService | None = None,
        statistical_request: StatisticalRequest | None = None,
    ) -> None:
        self.config = config
        self.params = dict(config.params)
        self.sampling_signature = build_sampling_signature(
            target=config.target,
            case_kind=config.case_kind,
            params=self.params,
        )
        self.experiment_id = str(
            config.experiment_id or f"fixed-{self.sampling_signature[:12]}"
        )
        self.dataset_repository = dataset_repository
        self.statistical_service = statistical_service
        self.statistical_request = statistical_request
        self.issued_count = 0
        self._baseline_completed_count: int | None = None
        self.stop_reason = ""
        self.waiting_for_result = False
        self._model_check_issued = False
        self.exact_model_check: dict[str, object] | None = None
        self.latest_statistical_report: StatisticalReport | None = None

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

        completed = self._load_matching_records("sample")
        report = self._evaluate(completed)
        if report is not None and report.next_action in {
            "stop",
            "stop_max_samples",
            "error",
        }:
            self.stop_reason = self._report_stop_reason(report)
            return None

        completed_count = 0 if completed is None else int(len(completed))
        if self._baseline_completed_count is None:
            self._baseline_completed_count = completed_count
        scheduled_count = self._baseline_completed_count + self.issued_count
        if scheduled_count >= int(self.config.max_samples):
            self.stop_reason = (
                f"Fixed parameter sampling reached max_samples="
                f"{self.config.max_samples}"
            )
            return None

        self.issued_count += 1
        sample_index = self._baseline_completed_count + self.issued_count
        prefix = self.config.case_id_prefix or (
            f"{self.config.case_kind}-{self.experiment_id}"
        )
        case_id = f"{_safe_identifier(prefix)}-{sample_index:04d}"
        sample_input = {
            **self.params,
            "execution_kind": "sample_path",
            "record_kind": "sample",
            "experiment_id": self.experiment_id,
            "sampling_signature": self.sampling_signature,
            "sample_index": sample_index,
        }
        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input=sample_input,
            tags=list(self.config.tags),
            reason=(
                f"{self.config.reason_prefix}: {self.experiment_id} "
                f"sample={sample_index}"
            ),
            meta={
                "source_module": "orchestration.fixed_parameter_sampling",
                "experiment_id": self.experiment_id,
                "sampling_signature": self.sampling_signature,
                "sample_index": sample_index,
            },
        )

    def statistical_report_payload(self) -> dict[str, object] | None:
        report = self.latest_statistical_report
        if report is None:
            return None
        diagnostics = {
            key: value
            for key, value in report.diagnostics.items()
            if key != "filtered_df"
        }
        return {
            "method": report.method,
            "metric": report.metric,
            "sample_count": report.sample_count,
            "estimate": report.estimate,
            "interval": report.interval,
            "sufficient": report.sufficient,
            "next_action": report.next_action,
            "diagnostics": diagnostics,
            "experiment_id": self.experiment_id,
            "sampling_signature": self.sampling_signature,
            "exact_model_check": self._exact_comparison_payload(report),
        }

    def _load_matching_records(self, record_kind: str) -> pd.DataFrame | None:
        repository = self.dataset_repository
        if repository is None or not hasattr(repository, "load_dataset"):
            return None
        dataset = repository.load_dataset()
        if dataset is None or dataset.empty:
            return None
        required = {"experiment_id", "sampling_signature"}
        if not required.issubset(dataset.columns):
            return dataset.iloc[0:0].copy()
        experiment_ids = dataset["experiment_id"].fillna("").astype(str)
        signatures = dataset["sampling_signature"].fillna("").astype(str)
        matching = dataset[
            experiment_ids.eq(self.experiment_id)
            & signatures.eq(self.sampling_signature)
        ].copy()
        if "record_kind" not in matching.columns:
            return matching if record_kind == "sample" else matching.iloc[0:0].copy()
        kinds = matching["record_kind"].fillna("sample").astype(str)
        return matching[kinds.eq(record_kind)].copy()

    def _build_model_check_case(self) -> TestCase:
        prefix = self.config.case_id_prefix or (
            f"{self.config.case_kind}-{self.experiment_id}"
        )
        return TestCase(
            case_id=f"{_safe_identifier(prefix)}-model-check",
            target=self.config.target,
            case_kind=self.config.case_kind,
            input={
                **self.params,
                "execution_kind": "model_check",
                "record_kind": "exact_model_check",
                "experiment_id": self.experiment_id,
                "sampling_signature": self.sampling_signature,
            },
            tags=list(self.config.tags),
            reason=f"{self.config.reason_prefix}: {self.experiment_id} model-check",
            meta={
                "source_module": "orchestration.fixed_parameter_sampling",
                "experiment_id": self.experiment_id,
                "sampling_signature": self.sampling_signature,
                "record_kind": "exact_model_check",
            },
        )

    def _capture_exact_model_check(self, frame: pd.DataFrame) -> bool:
        successful = frame
        if "status" in successful.columns:
            statuses = successful["status"].fillna("").astype(str).str.lower()
            successful = successful[statuses.eq("success")]
        if successful.empty:
            return False
        row = successful.iloc[-1]
        try:
            self.exact_model_check = {
                "eventual_failure_probability": float(
                    row["prism_eventual_failure_probability"]
                ),
                "bounded_failure_probability": float(
                    row["prism_bounded_failure_probability"]
                ),
            }
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def _exact_comparison_payload(
        self,
        report: StatisticalReport,
    ) -> dict[str, object] | None:
        if self.exact_model_check is None:
            return None
        payload: dict[str, object] = {
            **self.exact_model_check,
            "comparison_metric": report.metric,
        }
        if report.metric != _EXACT_PROBABILITY_METRIC:
            payload["ci_contains_bounded_probability"] = None
            payload["absolute_estimation_error"] = None
            payload["comparison_skipped_reason"] = (
                "exact_model_check reports a failure probability; "
                f"metric '{report.metric}' is not directly comparable to it"
            )
            return payload

        bounded = float(self.exact_model_check["bounded_failure_probability"])
        interval = report.interval
        estimate = report.estimate
        payload["ci_contains_bounded_probability"] = (
            interval is not None and interval[0] <= bounded <= interval[1]
        )
        payload["absolute_estimation_error"] = (
            None if estimate is None else abs(float(estimate) - bounded)
        )
        return payload

    def _evaluate(self, data: pd.DataFrame | None) -> StatisticalReport | None:
        if self.statistical_service is None or self.statistical_request is None:
            return None
        report = self.statistical_service.evaluate_request(
            data,
            self.statistical_request,
        )
        self.latest_statistical_report = report
        return report

    def _report_stop_reason(self, report: StatisticalReport) -> str:
        if report.next_action == "stop":
            width = report.interval_width
            rendered_width = "unknown" if width is None else f"{width:.6f}"
            return (
                f"Statistical interval sufficient for {report.metric}: "
                f"width={rendered_width}, samples={report.sample_count}"
            )
        if report.next_action == "stop_max_samples":
            return f"Statistical sampling reached max_samples={self.config.max_samples}"
        return str(
            report.diagnostics.get(
                "message",
                f"Statistical evaluation failed for {report.metric}",
            )
        )


def build_sampling_signature(
    *,
    target: str,
    case_kind: str,
    params: Mapping[str, object],
) -> str:
    payload = {
        "target": str(target),
        "case_kind": str(case_kind),
        "params": dict(params),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    return normalized or "fixed-sample"


__all__ = [
    "FixedParameterSamplingStrategy",
    "FixedParameterSamplingStrategyConfig",
    "build_sampling_signature",
]
