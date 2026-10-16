from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    ANALYSIS_ERROR = "analysis_error"
    INVALID = "invalid"


@dataclass
class TestCase:
    case_id: str
    target: str
    case_kind: str
    input: dict[str, object] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    reason: str = ""
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class RawRunResult:
    case_id: str
    target: str
    case_kind: str
    status: RunStatus
    evidence: dict[str, str] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
