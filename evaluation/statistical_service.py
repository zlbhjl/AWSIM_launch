from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from evaluation.binomial_ci import evaluate_and_summarize_binomial_ci
from evaluation.dkw import (
    calculate_quantile_with_dkw,
    evaluate_and_summarize_dkw,
    evaluate_and_summarize_dkw_multiple,
)


@dataclass
class StatisticalEvaluationService:
    feature_names: list[str]

    def summarize_dkw(
        self,
        df: pd.DataFrame | None,
        *,
        target_column: str,
        q: float = 0.05,
        delta: float = 0.05,
        epsilon: float = 0.15,
    ) -> dict[str, object]:
        return evaluate_and_summarize_dkw(
            df,
            target_column=target_column,
            q=q,
            delta=delta,
            epsilon=epsilon,
        )

    def summarize_dkw_multiple(
        self,
        df: pd.DataFrame | None,
        *,
        target_columns: Sequence[str],
        q: float = 0.05,
        delta_total: float = 0.05,
        epsilon: float = 0.15,
        use_kde_weighting: bool = False,
        region: str = "custom",
        bounds: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, object]:
        return evaluate_and_summarize_dkw_multiple(
            df,
            target_columns=target_columns,
            q=q,
            delta_total=delta_total,
            epsilon=epsilon,
            use_kde_weighting=use_kde_weighting,
            region=region,
            bounds=bounds,
            feature_names=self.feature_names,
        )

    def summarize_binomial_ci(
        self,
        df: pd.DataFrame | None,
        *,
        target_column: str,
        confidence_level: float = 0.95,
        method: str = "wilson",
        bounds: dict[str, tuple[float, float]] | None = None,
        region: str = "custom",
        reason_pattern: str | None = None,
    ) -> dict[str, object]:
        return evaluate_and_summarize_binomial_ci(
            df,
            target_column=target_column,
            confidence_level=confidence_level,
            method=method,
            bounds=bounds,
            region=region,
            reason_pattern=reason_pattern,
        )

    def calculate_dkw_quantile(
        self,
        df: pd.DataFrame | None,
        *,
        target_column: str,
        q: float = 0.05,
        delta: float = 0.05,
        bounds: dict[str, tuple[float, float]] | None = None,
        region: str = "custom",
        use_kde_weighting: bool = False,
    ) -> dict[str, object] | None:
        return calculate_quantile_with_dkw(
            df,
            target_column=target_column,
            q=q,
            delta=delta,
            bounds=bounds,
            region=region,
            use_kde_weighting=use_kde_weighting,
            feature_names=self.feature_names,
        )
