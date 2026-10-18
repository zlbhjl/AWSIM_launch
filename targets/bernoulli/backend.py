"""Execution boundary for the bernoulli target.

The outcome is computed in-process from the test case input; no artifacts are
written, so the outcome travels in ``RawRunResult.meta``.
"""

from __future__ import annotations

from contracts.execution import RawRunResult, RunStatus, TestCase
from contracts.sampling import copy_sampling_metadata

from .model import BernoulliParameters, evaluate

SOURCE_MODULE = "targets.bernoulli.backend"


class BernoulliBackend:
    def run(self, test_case: TestCase) -> RawRunResult:
        try:
            parameters = BernoulliParameters.from_input(test_case.input)
            outcome = evaluate(parameters, test_case.input)
        except (TypeError, ValueError) as exc:
            return self._result(test_case, RunStatus.INVALID, {"error_message": str(exc)})
        return self._result(
            test_case,
            RunStatus.SUCCESS,
            {"outcome": outcome, "parameters": parameters.__dict__.copy()},
        )

    @staticmethod
    def _result(test_case: TestCase, status: RunStatus, meta: dict[str, object]) -> RawRunResult:
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=status,
            meta=copy_sampling_metadata(test_case.meta, {"source_module": SOURCE_MODULE, **meta}),
        )


__all__ = ["BernoulliBackend"]
