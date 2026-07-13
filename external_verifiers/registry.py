from __future__ import annotations

from .base import ExternalVerifier
from .bbsl_ft4d import BBSLFT4DVerifier


_VERIFIERS = {
    BBSLFT4DVerifier.name: BBSLFT4DVerifier,
}


def list_verifiers() -> list[str]:
    return sorted(_VERIFIERS.keys())


def create_verifier(name: str) -> ExternalVerifier:
    try:
        verifier_cls = _VERIFIERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown verifier: {name}. Available: {', '.join(list_verifiers())}"
        ) from exc
    return verifier_cls()
