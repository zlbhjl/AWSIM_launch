from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import pandas as pd


class StatisticalRegionPolicy(Protocol):
    """Prepare one candidate point for an optional named-region filter."""

    def build_filter_frame(self, point: Mapping[str, object]) -> pd.DataFrame:
        ...


class PassthroughStatisticalRegionPolicy:
    """Target-neutral policy used when no derived region fields are required."""

    def build_filter_frame(self, point: Mapping[str, object]) -> pd.DataFrame:
        return pd.DataFrame([dict(point)])


__all__ = [
    "PassthroughStatisticalRegionPolicy",
    "StatisticalRegionPolicy",
]
