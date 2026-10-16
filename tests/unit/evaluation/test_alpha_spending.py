import pytest

from evaluation.alpha_spending import spending_budget_at


def test_spending_budget_decreases_with_t() -> None:
    values = [spending_budget_at(t, total_delta=0.05) for t in (1, 10, 100, 1000)]

    assert values == sorted(values, reverse=True)
    assert all(v > 0.0 for v in values)


def test_spending_budget_partial_sum_stays_under_total_delta() -> None:
    total_delta = 0.05

    partial_sum = sum(spending_budget_at(t, total_delta) for t in range(1, 100_000))

    assert partial_sum < total_delta


def test_spending_budget_rejects_invalid_t() -> None:
    with pytest.raises(ValueError, match="t must be"):
        spending_budget_at(0, total_delta=0.05)


def test_spending_budget_rejects_invalid_total_delta() -> None:
    with pytest.raises(ValueError, match="total_delta"):
        spending_budget_at(1, total_delta=1.5)


def test_spending_budget_rejects_p_not_greater_than_one() -> None:
    with pytest.raises(ValueError, match="p must be"):
        spending_budget_at(1, total_delta=0.05, p=1.0)
