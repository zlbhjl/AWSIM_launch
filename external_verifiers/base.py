from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ExternalVerificationRequest:
    verifier_name: str
    target_name: str
    target_repo: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalVerificationResult:
    verifier_name: str
    target_name: str
    command: List[str]
    workdir: str
    returncode: int
    raw_result_path: str | None
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExternalVerifier(ABC):
    name: str

    @abstractmethod
    def run(self, request: ExternalVerificationRequest) -> ExternalVerificationResult:
        raise NotImplementedError
