from __future__ import annotations

from .base import ExternalVerifier
from verifiers.compatibility.legacy_bbsl_ft4d_adapter import BBSLFT4DVerifier


# This registry is intentionally kept only for the legacy-compatible
# external verifier CLI. Adapters registered here should stay thin and
# forward into targets/* + evaluation/* instead of growing a second
# execution stack beside the refactored design.
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
