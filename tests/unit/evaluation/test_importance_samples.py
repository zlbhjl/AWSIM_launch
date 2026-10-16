from __future__ import annotations

from math import log

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.importance_samples import collect_available_importance_samples


def _record(value: float, *, status: str = "available") -> EvaluationRecord:
    sampling: dict[str, object] = {"importance_weight_status": status}
    if status == "available":
        sampling["log_importance_weight"] = log(2.0)
    return EvaluationRecord(
        case_id="case",
        target="prism",
        case_kind="test",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={"sampling": sampling},
    )


def test_collects_only_samples_with_available_finite_weights() -> None:
    samples = collect_available_importance_samples(
        [_record(1.0), _record(0.0, status="unavailable")],
        metric="c_failure",
    )

    assert len(samples) == 1
    assert samples[0].value == 1.0
    assert samples[0].weight == pytest.approx(2.0)


def test_rejects_out_of_range_available_metric() -> None:
    with pytest.raises(ValueError, match=r"bounded in \[0, 1\]"):
        collect_available_importance_samples([_record(2.0)], metric="c_failure")
