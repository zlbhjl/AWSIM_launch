"""Lifecycle control for Q proposals without changing target measure P."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Mapping
from uuid import uuid4

from contracts.sampling import ProposalCandidate
from contracts.statistics import StatisticalReport
from runtime.repository.proposal_history import ProposalHistoryRepository


SUPPORTED_PROPOSAL_UPDATE_MODES = frozenset({"suggest", "human_approved", "auto"})


class ProposalUpdateManager:
    """Record proposals and enforce suggest, approval, and auto-update modes."""

    def __init__(
        self,
        *,
        repository: ProposalHistoryRepository,
        mode: str,
    ) -> None:
        if mode not in SUPPORTED_PROPOSAL_UPDATE_MODES:
            raise ValueError(
                f"Unsupported proposal update mode: {mode!r}; "
                f"supported: {sorted(SUPPORTED_PROPOSAL_UPDATE_MODES)}"
            )
        self.repository = repository
        self.mode = mode

    def propose(self, candidate: ProposalCandidate) -> str:
        _validate_candidate(candidate)
        update_id = uuid4().hex
        self.repository.append(
            _event(
                "proposed",
                update_id=update_id,
                candidate=candidate,
                actor="system",
            )
        )
        if self.mode == "auto":
            self._apply(update_id, candidate=candidate, actor="system", reason="automatic_update")
        return update_id

    def record_evaluation(
        self,
        candidate: ProposalCandidate,
        *,
        report: StatisticalReport,
        target_diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        """Append a pilot evaluation without interpreting target diagnostics."""
        _validate_candidate(candidate)
        if target_diagnostics is not None and not isinstance(target_diagnostics, Mapping):
            raise ValueError("target_diagnostics must be a mapping or None")
        self.repository.append(
            {
                **_event(
                    "candidate_evaluated",
                    update_id=uuid4().hex,
                    candidate=candidate,
                    actor="system",
                ),
                "report": _report_payload(report),
                "target_diagnostics": deepcopy(dict(target_diagnostics or {})),
            }
        )

    def approve(self, update_id: str, *, approved_by: str) -> None:
        if self.mode != "human_approved":
            raise ValueError("approval is available only in human_approved mode")
        candidate = self._pending_candidate(update_id)
        if not approved_by.strip():
            raise ValueError("approved_by must be a non-empty string")
        self.repository.append(
            _event(
                "approved",
                update_id=update_id,
                candidate=candidate,
                actor=approved_by,
            )
        )

    def apply_approved(self, update_id: str) -> None:
        if self.mode != "human_approved":
            raise ValueError("apply_approved is available only in human_approved mode")
        candidate = self._pending_candidate(update_id, required_last_event="approved")
        self._apply(update_id, candidate=candidate, actor="system", reason="human_approved")

    def active_proposal(self, *, plan_id: str) -> dict[str, object] | None:
        applied = [
            event
            for event in self.repository.read_events()
            if event.get("event_type") == "applied" and event.get("plan_id") == plan_id
        ]
        if not applied:
            return None
        latest = max(applied, key=lambda event: int(event["proposal_version"]))
        return deepcopy(latest)

    def _pending_candidate(
        self,
        update_id: str,
        *,
        required_last_event: str = "proposed",
    ) -> ProposalCandidate:
        matching = [
            event for event in self.repository.read_events() if event.get("update_id") == update_id
        ]
        if not matching:
            raise ValueError(f"Unknown proposal update: {update_id}")
        if matching[-1].get("event_type") != required_last_event:
            raise ValueError(
                f"Proposal update {update_id} must be {required_last_event!r} before this action"
            )
        latest = matching[-1]
        return ProposalCandidate(
            plan_id=str(latest["plan_id"]),
            proposal_id=str(latest["proposal_id"]),
            proposal_version=int(latest["proposal_version"]),
            definition=deepcopy(latest["definition"]),
            reason=str(latest["reason"]),
            diagnostics=deepcopy(latest.get("diagnostics", {})),
        )

    def _apply(
        self,
        update_id: str,
        *,
        candidate: ProposalCandidate,
        actor: str,
        reason: str,
    ) -> None:
        self.repository.append(
            _event(
                "applied",
                update_id=update_id,
                candidate=candidate,
                actor=actor,
                application_reason=reason,
            )
        )


def _validate_candidate(candidate: ProposalCandidate) -> None:
    for field_name in ("plan_id", "proposal_id", "reason"):
        value = getattr(candidate, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
    if candidate.proposal_version < 1:
        raise ValueError("proposal_version must be a positive integer")
    if not isinstance(candidate.diagnostics, Mapping):
        raise ValueError("diagnostics must be a mapping")


def _event(
    event_type: str,
    *,
    update_id: str,
    candidate: ProposalCandidate,
    actor: str,
    application_reason: str | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "event_type": event_type,
        "update_id": update_id,
        "plan_id": candidate.plan_id,
        "proposal_id": candidate.proposal_id,
        "proposal_version": candidate.proposal_version,
        "definition": deepcopy(candidate.definition),
        "reason": candidate.reason,
        "diagnostics": deepcopy(dict(candidate.diagnostics)),
        "actor": actor,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if application_reason is not None:
        event["application_reason"] = application_reason
    return event


def _report_payload(report: StatisticalReport) -> dict[str, object]:
    return {
        "method": report.method,
        "metric": report.metric,
        "sample_count": report.sample_count,
        "estimate": report.estimate,
        "interval": list(report.interval) if report.interval is not None else None,
        "sufficient": report.sufficient,
        "next_action": report.next_action,
        "diagnostics": deepcopy(report.diagnostics),
    }


__all__ = [
    "ProposalUpdateManager",
    "SUPPORTED_PROPOSAL_UPDATE_MODES",
]
