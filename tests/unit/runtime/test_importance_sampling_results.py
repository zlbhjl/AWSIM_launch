from __future__ import annotations

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository


def test_candidate_records_are_separated_by_proposal_id(tmp_path) -> None:
    repository = ImportanceSamplingResultsRepository(tmp_path)
    first = EvaluationRecord("one", "prism", "simple_reliability_dtmc", RunStatus.SUCCESS)
    second = EvaluationRecord("two", "prism", "simple_reliability_dtmc", RunStatus.SUCCESS)

    repository.append_candidate_record("candidate_a", first)
    repository.append_candidate_record("candidate_b", second)

    assert [record.case_id for record in repository.load_candidate_records("candidate_a")] == ["one"]
    assert [record.case_id for record in repository.load_candidate_records("candidate_b")] == ["two"]
