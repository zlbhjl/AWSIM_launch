"""Interpret a bernoulli run as a common EvaluationRecord."""

from __future__ import annotations

from collections.abc import Mapping

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RawRunResult, RunStatus
from contracts.sampling import copy_sampling_metadata

from .model import BernoulliParameters, failure_probability, score_mean

SOURCE_MODULE = "targets.bernoulli.result_interpreter"


class BernoulliResultInterpreter:
    def interpret_raw_run_result(self, raw_run_result: RawRunResult) -> EvaluationRecord:
        outcome = raw_run_result.meta.get("outcome")
        parameters = raw_run_result.meta.get("parameters")
        if (
            raw_run_result.status is not RunStatus.SUCCESS
            or not isinstance(outcome, Mapping)
            or not isinstance(parameters, Mapping)
        ):
            status = (
                raw_run_result.status
                if raw_run_result.status is not RunStatus.SUCCESS
                else RunStatus.ANALYSIS_ERROR
            )
            return self._record(raw_run_result, status=status, output={})
        resolved = BernoulliParameters(**{key: float(value) for key, value in parameters.items()})
        return self._record(
            raw_run_result,
            status=RunStatus.SUCCESS,
            output=dict(outcome),
            input=dict(parameters),
            truth={
                "failure_probability": failure_probability(resolved),
                "score_mean": score_mean(resolved),
                "rate": resolved.rate,
            },
        )

    @staticmethod
    def _record(
        raw_run_result: RawRunResult,
        *,
        status: RunStatus,
        output: dict[str, object],
        input: dict[str, object] | None = None,
        truth: dict[str, float] | None = None,
    ) -> EvaluationRecord:
        meta: dict[str, object] = {"raw_run_status": raw_run_result.status.value}
        if truth is not None:
            meta["truth"] = truth
        if "error_message" in raw_run_result.meta:
            meta["error_message"] = raw_run_result.meta["error_message"]
        return EvaluationRecord(
            case_id=raw_run_result.case_id,
            target=raw_run_result.target,
            case_kind=raw_run_result.case_kind,
            status=status,
            input=input or {},
            output=output,
            evidence=dict(raw_run_result.evidence),
            meta=ensure_evaluation_meta(
                copy_sampling_metadata(raw_run_result.meta, meta),
                source_module=SOURCE_MODULE,
            ),
        )


__all__ = ["BernoulliResultInterpreter"]
