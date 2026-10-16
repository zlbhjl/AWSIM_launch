from __future__ import annotations

from runtime.repository.proposal_history import ProposalHistoryRepository


def test_proposal_history_appends_and_reads_jsonl_events(tmp_path) -> None:
    repository = ProposalHistoryRepository(tmp_path / "proposal_history.jsonl")

    repository.append({"event_type": "proposed", "proposal_version": 1})

    assert repository.read_events() == [
        {"event_type": "proposed", "proposal_version": 1}
    ]
