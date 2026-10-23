"""The empirical Bernstein deviation bound shared by EBStop and fixed-N IS intervals.

For n independent observations in an interval of length R, with empirical
standard deviation ``std`` (denominator n), with probability at least 1 - delta

    |mean - E[X]| <= std * sqrt(2 ln(3/delta) / n) + 3 R ln(3/delta) / n

(Audibert, Munos, Szepesvari 2009, Theorem 1; used by EBStop, Mnih,
Szepesvari, Audibert 2008).
"""

from __future__ import annotations

import numpy as np


def empirical_bernstein_radius(samples, std, value_range, delta):
    """Return the bound's radius. Accepts scalars or numpy arrays."""
    log_term = np.log(3.0 / delta)
    radius = std * np.sqrt(2.0 * log_term / samples) + 3.0 * value_range * log_term / samples
    return float(radius) if np.ndim(radius) == 0 else radius


__all__ = ["empirical_bernstein_radius"]
