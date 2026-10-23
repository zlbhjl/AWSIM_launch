from __future__ import annotations

from pathlib import Path

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus, TestCase
from orchestration.fixed_importance_sampling import run_strict_fixed_importance_sampling_comparison
from runtime.repository.importance_experiment_manifest import ExperimentManifest, save_or_verify_manifest
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository


class Stop(BaseException):
    """Simulates an interrupted process (not caught by the worker loop)."""


class Backend:
    def __init__(self, *, fail: set[str] = frozenset(), stop_after: int | None = None) -> None:
        self.fail = set(fail)
        self.stop_after = stop_after
        self.calls: list[str] = []

    def run(self, case: TestCase) -> RawRunResult:
        if self.stop_after is not None and len(self.calls) >= self.stop_after:
            raise Stop()
        self.calls.append(case.case_id)
        if case.meta["sampling"]["sample_id"] in self.fail:
            raise RuntimeError("injected failure")
        return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)


class Interpreter:
    def interpret_raw_run_result(self, raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            raw.case_id, raw.target, raw.case_kind, raw.status,
            output={"c_event": int(raw.case_id.split("-")[1]) % 2}, meta=dict(raw.meta),
        )


def _build_case(run_kind: str, index: int) -> TestCase:
    sampling: dict[str, object] = {
        "plan_id": "ou_a",
        "target_measure_id": "ou_p",
        "sampling_mode": "uniform" if run_kind == "ordinary" else "manual_proposal",
        "importance_weight_status": "not_applicable" if run_kind == "ordinary" else "available",
    }
    if run_kind == "importance":
        sampling.update({"proposal_id": "q1", "log_p": 0.0, "log_q": 0.0, "log_importance_weight": 0.0,
                         "importance_weight_upper_bound": 1.0})
    return TestCase(
        case_id=f"{run_kind}-{index:05d}",
        target="dynamics",
        case_kind="ou_scalar",
        input={"index": index},
        meta={"sampling": sampling},
    )


def _run(tmp_path: Path, backend: Backend, *, planned: int = 4, retry_failed: bool = False) -> dict:
    manifest = save_or_verify_manifest(
        tmp_path,
        ExperimentManifest(
            experiment_id="ou_a",
            target_definition={"model_id": "ou_scalar"},
            proposal_definition={"q": 1},
            numerics={"dt_sec": 0.01},
            rng={"policy_version": 2, "root_entropy": "1", "phase": "final", "pairing": "independent"},
            planned_samples=planned,
            metric="c_event",
        ),
    )
    return run_strict_fixed_importance_sampling_comparison(
        backend=backend,
        result_interpreter=Interpreter(),
        repository=ImportanceSamplingResultsRepository(tmp_path),
        manifest=manifest,
        build_case=_build_case,
        metric="c_event",
        confidence=0.95,
        retry_failed=retry_failed,
    )["comparison"]


def test_complete_run_tags_records_and_reports_complete(tmp_path: Path) -> None:
    comparison = _run(tmp_path, Backend())

    assert comparison["final_status"] == "complete"
    assert comparison["diagnostic_only"] is False
    assert comparison["identity_check"] == "fingerprint"
    record = ImportanceSamplingResultsRepository(tmp_path).load_records("importance")[0]
    assert record.meta["sampling"]["sample_id"] == "importance-000001"
    assert record.meta["attempt_id"] == 0


def test_failed_sample_is_incomplete_until_retried_with_the_same_case(tmp_path: Path) -> None:
    first = _run(tmp_path, Backend(fail={"importance-000002"}))
    assert first["final_status"] == "incomplete"
    assert first["audit"]["importance"]["unresolved_sample_ids"] == ["importance-000002"]

    without_retry = Backend()
    assert _run(tmp_path, without_retry)["final_status"] == "incomplete"
    assert without_retry.calls == []

    with_retry = Backend()
    final = _run(tmp_path, with_retry, retry_failed=True)
    assert with_retry.calls == ["importance-00002-a1"]
    assert final["final_status"] == "complete"
    assert final["audit"]["importance"]["counts"]["retries"] == 1


def test_interrupted_run_resumes_only_missing_samples(tmp_path: Path) -> None:
    with pytest.raises(Stop):
        _run(tmp_path, Backend(stop_after=3))

    resumed = Backend()
    comparison = _run(tmp_path, resumed)

    assert resumed.calls == ["ordinary-00004"] + [f"importance-0000{i}" for i in range(1, 5)]
    assert comparison["final_status"] == "complete"
    assert len(ImportanceSamplingResultsRepository(tmp_path).load_records("ordinary")) == 4


def test_all_failed_importance_run_is_saved_as_incomplete(tmp_path: Path) -> None:
    comparison = _run(tmp_path, Backend(fail={f"importance-00000{i}" for i in range(1, 5)}))

    assert comparison["final_status"] == "incomplete"
    assert "No valid importance-weighted samples" in comparison["error"]
