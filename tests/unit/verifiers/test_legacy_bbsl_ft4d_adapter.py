from external_verifiers.bbsl_ft4d import BBSLFT4DVerifier as LegacyWrapperVerifier
from verifiers.compatibility.legacy_bbsl_ft4d_adapter import (
    BBSLFT4DVerifier as CompatibilityVerifier,
)


def test_legacy_wrapper_reexports_compatibility_verifier() -> None:
    assert LegacyWrapperVerifier is CompatibilityVerifier
