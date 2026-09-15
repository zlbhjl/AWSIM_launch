from pathlib import Path

import pytest

from contracts.execution import TestCase
from targets.prism.profile import resolve_prism_execution_profile


def test_profile_resolves_typed_sample_execution(tmp_path: Path) -> None:
    profile = resolve_prism_execution_profile(
        TestCase(
            case_id="sample-1",
            target="prism",
            case_kind="simple_reliability_dtmc",
            input={
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "steps": 12,
                "timeout_sec": 4,
                "output_root": str(tmp_path),
            },
        ),
        default_output_root="unused",
    )

    assert profile.definition.model_id == "simple_reliability_dtmc"
    assert profile.horizon == 12
    assert profile.timeout_sec == 4.0
    assert profile.output_root == tmp_path.resolve()


def test_profile_rejects_mismatched_execution_and_record_kinds() -> None:
    with pytest.raises(ValueError, match="requires record_kind"):
        resolve_prism_execution_profile(
            TestCase(
                case_id="invalid",
                target="prism",
                case_kind="simple_reliability_dtmc",
                input={
                    "execution_kind": "model_check",
                    "record_kind": "sample",
                },
            ),
            default_output_root="artifacts/prism",
        )
