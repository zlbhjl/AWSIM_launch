from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class FormulaSpec:
    formula: str
    header: str


@dataclass(frozen=True)
class MaudeEvaluationSummary:
    metrics: dict[str, int]
    output: dict[str, object]
    has_error: bool
    missing_headers: list[str]
    invalid_headers: list[str]


def evaluate_formula_results(
    stdout: str,
    formula_specs: Sequence[FormulaSpec],
    *,
    base_output: Mapping[str, object] | None = None,
    invalid_conditions: Mapping[str, object] | None = None,
) -> MaudeEvaluationSummary:
    metrics: dict[str, int] = {}
    output = dict(base_output or {})
    missing_headers: list[str] = []

    for spec in formula_specs:
        pattern = re.escape(spec.formula) + r".*?Model checking result: (True|False)"
        match = re.search(pattern, stdout, re.DOTALL)
        if match:
            metrics[spec.header] = 0 if match.group(1) == "True" else 1
        else:
            metrics[spec.header] = -1
            missing_headers.append(spec.header)

    output.update(metrics)
    _apply_collision_rules(output)
    _apply_ttc_monotonic_rules(output)

    invalid_headers = _matching_invalid_conditions(output, invalid_conditions or {})
    # A trace without required vehicle motion is not a no-collision sample.
    # Preserve the trace for diagnosis, but remove it from collision statistics.
    if invalid_headers:
        output["c_collision"] = -1
    has_error = bool(missing_headers or invalid_headers)

    return MaudeEvaluationSummary(
        metrics=metrics,
        output=output,
        has_error=has_error,
        missing_headers=missing_headers,
        invalid_headers=invalid_headers,
    )


def _apply_collision_rules(output: dict[str, object]) -> None:
    if output.get("c_collision") == 1:
        output["min_distance"] = 0.0
        for key in list(output.keys()):
            if key.startswith("c_ttc_"):
                output[key] = 1


def _apply_ttc_monotonic_rules(output: dict[str, object]) -> None:
    ttc_keys = [key for key in output if key.startswith("c_ttc_")]
    ttc_keys.sort(key=_ttc_threshold_from_key)
    violated = False
    for key in ttc_keys:
        if output.get(key) == 1:
            violated = True
        elif violated:
            output[key] = 1


def _matching_invalid_conditions(
    output: Mapping[str, object],
    invalid_conditions: Mapping[str, object],
) -> list[str]:
    matches: list[str] = []
    for key, expected_value in invalid_conditions.items():
        if output.get(key) == expected_value:
            matches.append(key)
    return matches


def _ttc_threshold_from_key(key: str) -> float:
    return float(key.split("_")[-1])
