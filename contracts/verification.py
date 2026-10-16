from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VerificationInput:
    tree_mode: str
    universal_dataset: set[str] | set[int]
    events: dict[str, dict[str, object]]
    assumptions: dict[str, object] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class FT4DResult:
    tree_mode: str
    top_sigma_pe: float | None
    confidence: float | None
    node_summaries: list[dict[str, object]]
    raw_result: dict[str, object]
