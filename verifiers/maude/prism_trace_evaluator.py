from __future__ import annotations

from collections.abc import Mapping


REQUIRED_RULE_IDS = (
    "early_failure",
    "repeated_degradation",
    "absorbing_failure",
)
VALID_VERDICTS = frozenset({"safe", "violation", "invalid"})


class PrismTraceEvaluator:
    """Convert the checker's fixed JSON-shaped payload to framework metrics."""

    def evaluate(self, checker_result: Mapping[str, object]) -> dict[str, object]:
        if int(checker_result.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported PRISM Maude checker schema_version")
        verdicts = checker_result.get("verdicts")
        if not isinstance(verdicts, Mapping):
            raise ValueError("PRISM Maude checker result is missing verdicts")
        normalized: dict[str, str] = {}
        for rule_id in REQUIRED_RULE_IDS:
            verdict = str(verdicts.get(rule_id, ""))
            if verdict not in VALID_VERDICTS:
                raise ValueError(
                    f"Invalid PRISM Maude verdict for {rule_id}: {verdict!r}"
                )
            normalized[rule_id] = verdict
        return {
            "m_early_failure": int(normalized["early_failure"] == "violation"),
            "m_repeated_degradation": int(
                normalized["repeated_degradation"] == "violation"
            ),
            "m_absorbing_failure_invalid": int(
                normalized["absorbing_failure"] == "invalid"
            ),
            "maude_verdicts": normalized,
            "maude_rule_violations": list(
                checker_result.get("rule_violations", [])
            ),
            "maude_invalid_rules": list(checker_result.get("invalid_rules", [])),
            "maude_spec_path": str(checker_result.get("spec_path", "")),
        }


def evaluate_prism_trace_result(
    checker_result: Mapping[str, object],
) -> dict[str, object]:
    return PrismTraceEvaluator().evaluate(checker_result)


__all__ = [
    "PrismTraceEvaluator",
    "evaluate_prism_trace_result",
]
