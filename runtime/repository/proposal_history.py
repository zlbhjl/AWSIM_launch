"""Append-only persistence for proposal-distribution update events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


class ProposalHistoryRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def append(self, event: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def read_events(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        events: list[dict[str, object]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid proposal history JSON at line {line_number}"
                    ) from exc
                if not isinstance(event, dict):
                    raise ValueError(
                        f"Proposal history event at line {line_number} must be an object"
                    )
                events.append(event)
        return events


__all__ = ["ProposalHistoryRepository"]
