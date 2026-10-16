from __future__ import annotations

DEFAULT_SPENDING_EXPONENT = 1.1


def spending_budget_at(
    t: int,
    total_delta: float,
    p: float = DEFAULT_SPENDING_EXPONENT,
) -> float:
    """Per-look error budget d_t = c/t^p, c = total_delta*(p-1)/p, t = 1, 2, ...

    sum_{t=1}^{infinity} d_t = c * sum(1/t^p) <= c/(p-1) = total_delta/p < total_delta,
    so building a (1 - d_t)-confidence result at every look t and union-bounding
    over all t keeps the overall false-decision probability under total_delta,
    regardless of which t the process actually stops at ("anytime-valid").

    This is the same "peeling" schedule evaluation/ebstop.py uses (Mnih,
    Szepesvari, Audibert 2008-style) to guard its variance-based bound against
    repeated peeking; it is reused as-is by evaluation/binomial_ci.py's opt-in
    anytime-valid mode. Note: for any fixed target interval width, this
    schedule costs roughly 5-8x more samples than a single fixed-confidence
    check would need, because the per-look budget shrinks (and the required
    z-value grows) as t grows. Tuning p does not meaningfully change this; it
    is an inherent cost of remaining valid at every possible stopping point.
    """
    if t < 1:
        raise ValueError("t must be a positive integer (1-indexed look count)")
    if not 0.0 < total_delta < 1.0:
        raise ValueError("total_delta must satisfy 0.0 < total_delta < 1.0")
    if p <= 1.0:
        raise ValueError("p must be > 1.0 for sum_t d_t to converge")
    c = total_delta * (p - 1.0) / p
    return c / (float(t) ** p)


__all__ = ["DEFAULT_SPENDING_EXPONENT", "spending_budget_at"]
