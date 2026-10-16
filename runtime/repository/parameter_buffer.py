from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParameterEntry:
    params: dict[str, object]
    reason: str
    timestamp: datetime


class ParameterBuffer:
    def __init__(self, timeout_sec: int = 600):
        self.timeout_sec = timeout_sec
        self._entries: dict[int, ParameterEntry] = {}

    def put(
        self,
        loop_num: int,
        params: dict[str, object],
        reason: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        self._entries[loop_num] = ParameterEntry(
            params=dict(params),
            reason=reason,
            timestamp=timestamp or datetime.now(),
        )

    def get(self, loop_num: int) -> ParameterEntry | None:
        return self._entries.get(loop_num)

    def pop(self, loop_num: int) -> ParameterEntry | None:
        return self._entries.pop(loop_num, None)

    def cleanup_stale(self, now: datetime | None = None) -> list[int]:
        current = now or datetime.now()
        stale_keys = [
            loop_num
            for loop_num, entry in self._entries.items()
            if (current - entry.timestamp).total_seconds() > self.timeout_sec
        ]
        for loop_num in stale_keys:
            self._entries.pop(loop_num, None)
        return stale_keys
