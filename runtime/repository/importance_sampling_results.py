"""Persist separate ordinary and importance-sampling experiment results."""
from __future__ import annotations

import json
from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus


class ImportanceSamplingResultsRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def records_path(self, run_kind: str) -> Path:
        self._validate_run_kind(run_kind)
        return self.root / run_kind / "records.jsonl"

    def append_record(self, run_kind: str, record: EvaluationRecord) -> None:
        path = self.records_path(run_kind)
        self._append(path, record)

    def candidate_records_path(self, proposal_id: str) -> Path:
        return self.root / "candidates" / self._safe_component(proposal_id) / "records.jsonl"

    def append_candidate_record(self, proposal_id: str, record: EvaluationRecord) -> None:
        self._append(self.candidate_records_path(proposal_id), record)

    def load_candidate_records(self, proposal_id: str) -> list[EvaluationRecord]:
        return self._load(self.candidate_records_path(proposal_id))

    def _append(self, path: Path, record: EvaluationRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "case_id": record.case_id, "target": record.target,
            "case_kind": record.case_kind, "status": record.status.value,
            "input": record.input, "output": record.output,
            "evidence": record.evidence, "meta": record.meta,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def load_records(self, run_kind: str) -> list[EvaluationRecord]:
        return self._load(self.records_path(run_kind))

    @staticmethod
    def _load(path: Path) -> list[EvaluationRecord]:
        if not path.exists():
            return []
        records: list[EvaluationRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            records.append(EvaluationRecord(
                case_id=str(row["case_id"]), target=str(row["target"]),
                case_kind=str(row["case_kind"]), status=RunStatus(str(row["status"])),
                input=dict(row.get("input", {})), output=dict(row.get("output", {})),
                evidence={str(k): str(v) for k, v in dict(row.get("evidence", {})).items()},
                meta=dict(row.get("meta", {})),
            ))
        return records

    def save_comparison(self, comparison: dict[str, object]) -> Path:
        path = self.root / "comparison.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _validate_run_kind(run_kind: str) -> None:
        if run_kind not in {"ordinary", "importance"}:
            raise ValueError("run_kind must be 'ordinary' or 'importance'")

    @staticmethod
    def _safe_component(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("proposal_id must be a non-empty string")
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


__all__ = ["ImportanceSamplingResultsRepository"]
