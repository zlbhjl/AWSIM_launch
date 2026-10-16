from __future__ import annotations

import pytest

from contracts.sampling import ProposalCandidate
from contracts.statistics import StatisticalReport
from orchestration.proposal_updates import ProposalUpdateManager
from runtime.repository.proposal_history import ProposalHistoryRepository


def _candidate() -> ProposalCandidate:
    return ProposalCandidate(
        plan_id="prism_is_v1",
        proposal_id="failure_biased_v2",
        proposal_version=2,
        definition={"transition_probabilities": {"normal": {"failure": 0.2}}},
        reason="failure paths are underrepresented",
        diagnostics={"effective_sample_size": 5.0},
    )


def test_suggest_mode_records_candidate_without_applying(tmp_path) -> None:
    repository = ProposalHistoryRepository(tmp_path / "proposal_history.jsonl")
    manager = ProposalUpdateManager(repository=repository, mode="suggest")

    manager.propose(_candidate())

    assert [event["event_type"] for event in repository.read_events()] == ["proposed"]
    assert manager.active_proposal(plan_id="prism_is_v1") is None


def test_human_approved_mode_requires_approval_before_apply(tmp_path) -> None:
    repository = ProposalHistoryRepository(tmp_path / "proposal_history.jsonl")
    manager = ProposalUpdateManager(repository=repository, mode="human_approved")
    update_id = manager.propose(_candidate())

    with pytest.raises(ValueError, match="must be 'approved'"):
        manager.apply_approved(update_id)

    manager.approve(update_id, approved_by="researcher")
    manager.apply_approved(update_id)

    assert [event["event_type"] for event in repository.read_events()] == [
        "proposed",
        "approved",
        "applied",
    ]
    assert manager.active_proposal(plan_id="prism_is_v1")["proposal_version"] == 2


def test_auto_mode_applies_after_recording_candidate(tmp_path) -> None:
    repository = ProposalHistoryRepository(tmp_path / "proposal_history.jsonl")
    manager = ProposalUpdateManager(repository=repository, mode="auto")

    manager.propose(_candidate())

    events = repository.read_events()
    assert [event["event_type"] for event in events] == ["proposed", "applied"]
    assert events[-1]["application_reason"] == "automatic_update"


def test_candidate_evaluation_keeps_variable_metrics_in_report_diagnostics(tmp_path) -> None:
    repository = ProposalHistoryRepository(tmp_path / "proposal_history.jsonl")
    manager = ProposalUpdateManager(repository=repository, mode="suggest")

    manager.record_evaluation(
        _candidate(),
        report=StatisticalReport(
            method="importance_sampling", metric="c_failure", sample_count=10,
            estimate=0.1, interval=(0.01, 0.2), sufficient=False,
            next_action="collect_more_samples", diagnostics={"effective_sample_size": 7.5},
        ),
        target_diagnostics={"max_transition_likelihood_ratio": 2.0},
    )

    event = repository.read_events()[0]
    assert event["event_type"] == "candidate_evaluated"
    assert event["report"]["diagnostics"] == {"effective_sample_size": 7.5}
    assert event["target_diagnostics"] == {"max_transition_likelihood_ratio": 2.0}
