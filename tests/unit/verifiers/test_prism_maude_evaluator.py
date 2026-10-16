import pytest

from targets.prism.maude_checker import PrismMaudeChecker as CompatibilityChecker
from verifiers.maude.prism_checker import PrismMaudeChecker
from verifiers.maude.prism_trace_evaluator import PrismTraceEvaluator


def test_prism_trace_evaluator_converts_fixed_checker_payload() -> None:
    output = PrismTraceEvaluator().evaluate(
        {
            "schema_version": 1,
            "checker": "maude_prism_trace",
            "verdicts": {
                "early_failure": "violation",
                "repeated_degradation": "safe",
                "absorbing_failure": "invalid",
            },
            "rule_violations": ["early_failure"],
            "invalid_rules": ["absorbing_failure"],
            "spec_path": "/workspace/verifiers/maude/specs/prism_trace.maude",
        }
    )

    assert output["m_early_failure"] == 1
    assert output["m_repeated_degradation"] == 0
    assert output["m_absorbing_failure_invalid"] == 1
    assert output["maude_rule_violations"] == ["early_failure"]


def test_prism_trace_evaluator_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError, match="Invalid PRISM Maude verdict"):
        PrismTraceEvaluator().evaluate(
            {
                "schema_version": 1,
                "verdicts": {
                    "early_failure": "unknown",
                    "repeated_degradation": "safe",
                    "absorbing_failure": "safe",
                },
            }
        )


def test_old_target_checker_path_is_a_compatibility_alias() -> None:
    assert CompatibilityChecker is PrismMaudeChecker
