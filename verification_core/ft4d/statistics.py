"""Statistical helpers for FT4D basic-event testing.

The functions in this module implement the Chernoff-Hoeffding based
sample-size and pass/fail criteria described in the FT4D paper.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, log
from typing import Optional, Sequence


@dataclass(frozen=True)
class RecognitionTestResult:
    """Result of a Chernoff-Hoeffding recognition-rate test."""

    n: int
    required_sample_size: int
    has_required_sample_size: bool
    correct_count: int
    required_correct_count: int
    meets_correct_count: bool
    sample_recognition_rate: float
    required_sample_rate: float
    expected_recognition_rate: float
    epsilon: float
    delta: float
    confidence: float
    passed: bool


def _validate_probability(name: str, value: float,
                          allow_zero: bool = False) -> None:
    lower_ok = value >= 0.0 if allow_zero else value > 0.0
    if not lower_ok or value > 1.0:
        bracket = "[0, 1]" if allow_zero else "(0, 1]"
        raise ValueError(f"{name} must be in {bracket}, got {value}")


def sample_size(epsilon: float, delta: float) -> int:
    """Return n = ceil((1 / (2 * epsilon^2)) * ln(2 / delta))."""
    _validate_probability("epsilon", epsilon)
    if not 0.0 < delta < 1.0:
        raise ValueError(f"delta must be in (0, 1), got {delta}")
    return ceil((1.0 / (2.0 * epsilon ** 2)) * log(2.0 / delta))


def decision_threshold(n: int, expected_recognition_rate: float,
                       epsilon: float) -> int:
    """Return the minimum correct-count needed to pass the test."""
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    _validate_probability(
        "expected_recognition_rate", expected_recognition_rate,
        allow_zero=True,
    )
    _validate_probability("epsilon", epsilon)
    return ceil(n * (expected_recognition_rate + epsilon))


def sample_recognition_rate(correct_count: int, n: int) -> float:
    """Return correct_count / n after validating the counts."""
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not 0 <= correct_count <= n:
        raise ValueError(
            f"correct_count must be in [0, n], got {correct_count} for n={n}"
        )
    return correct_count / n


def evaluate_recognition_test(
    correct_count: int,
    expected_recognition_rate: float,
    epsilon: float,
    delta: float,
    n: Optional[int] = None,
) -> RecognitionTestResult:
    """Evaluate the FT4D basic-event recognition-rate pass/fail rule.

    If n is omitted, it is computed from epsilon and delta.
    """
    required_n = sample_size(epsilon, delta)
    actual_n = required_n if n is None else n
    rate = sample_recognition_rate(correct_count, actual_n)
    required = decision_threshold(actual_n, expected_recognition_rate, epsilon)
    meets_correct_count = correct_count >= required
    has_required_sample_size = actual_n >= required_n
    return RecognitionTestResult(
        n=actual_n,
        required_sample_size=required_n,
        has_required_sample_size=has_required_sample_size,
        correct_count=correct_count,
        required_correct_count=required,
        meets_correct_count=meets_correct_count,
        sample_recognition_rate=rate,
        required_sample_rate=required / actual_n,
        expected_recognition_rate=expected_recognition_rate,
        epsilon=epsilon,
        delta=delta,
        confidence=1.0 - delta,
        passed=has_required_sample_size and meets_correct_count,
    )


def bonferroni_child_delta(parent_delta: float,
                           number_of_child_tests: int) -> float:
    """Return per-child delta for Bonferroni correction."""
    if not 0.0 < parent_delta < 1.0:
        raise ValueError(f"parent_delta must be in (0, 1), got {parent_delta}")
    if number_of_child_tests <= 0:
        raise ValueError(
            "number_of_child_tests must be positive, "
            f"got {number_of_child_tests}"
        )
    return parent_delta / number_of_child_tests


def basic_error_rate(error_count: int, total_count: int) -> float:
    """Return sigma_pb = error_count / total_count for a basic event."""
    if total_count < 0:
        raise ValueError(f"total_count must be non-negative, got {total_count}")
    if total_count == 0:
        return 0.0
    if not 0 <= error_count <= total_count:
        raise ValueError(
            f"error_count must be in [0, total_count], got {error_count}"
        )
    return error_count / total_count


def combine_confidence_bounds(confidences: Sequence[float]) -> float:
    """Combine lower-bound confidences with the Bonferroni union bound."""
    total_delta = 0.0
    for confidence in confidences:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1], got {confidence}"
            )
        total_delta += 1.0 - confidence
    return max(0.0, 1.0 - total_delta)
