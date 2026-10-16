from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.alpha_spending import DEFAULT_SPENDING_EXPONENT
from evaluation.dkw import records_to_data_frame
import point_extractors


DEFAULT_P = DEFAULT_SPENDING_EXPONENT


def ebstop_update(
    *,
    samples: int,
    mean: float,
    std: float,
    value_range: float,
    epsilon: float,
    delta_t: float,
) -> dict[str, float]:
    """Empirical Bernstein bound c_t (Mnih, Szepesvari, Audibert 2008, eq. 7).

    c_t = std * sqrt(2*log(3/delta_t)/samples) + 3*value_range*log(3/delta_t)/samples
    """
    if samples <= 0:
        raise ValueError("samples must be positive")
    if not 0.0 < delta_t < 1.0:
        raise ValueError("delta_t must satisfy 0.0 < delta_t < 1.0")
    if value_range <= 0.0:
        raise ValueError("value_range must be positive")
    if std < 0.0:
        raise ValueError("std must be non-negative")

    log_term = math.log(3.0 / delta_t)
    c_t = std * math.sqrt(2.0 * log_term / samples) + 3.0 * value_range * log_term / samples
    mean_abs = abs(mean)
    return {
        "c_t": c_t,
        "lb_candidate": mean_abs - c_t,
        "ub_candidate": mean_abs + c_t,
    }


def _delta_t_sequence(n: int, delta: float, p: float = DEFAULT_P) -> np.ndarray:
    """d_t = c / t^p with c = delta*(p-1)/p, for t = 1..n (paper's default choice)."""
    c = delta * (p - 1.0) / p
    t = np.arange(1, n + 1, dtype=float)
    return c / (t**p)


def _ensure_data_frame(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
) -> pd.DataFrame | None:
    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        return data
    return records_to_data_frame(data)


def _resolve_value_range(value_range: float | tuple[float, float]) -> float:
    if isinstance(value_range, (tuple, list)):
        lo, hi = float(value_range[0]), float(value_range[1])
        if hi <= lo:
            raise ValueError("value_range=(lo, hi) must satisfy lo < hi")
        return hi - lo
    return float(value_range)


def calculate_ebstop_decision(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    epsilon: float,
    delta: float,
    value_range: float | tuple[float, float],
    bounds: dict[str, tuple[float, float]] | None = None,
    region: str = "custom",
    reason_pattern: str | None = None,
) -> dict[str, object] | None:
    if df is None or df.empty or target_column not in df.columns:
        return None

    working_df = df.copy()
    if "status" in working_df.columns:
        status_series = working_df["status"].fillna("").astype(str).str.lower()
        working_df = working_df[status_series.eq("success")]
    if reason_pattern and "reason" in working_df.columns:
        reason_series = working_df["reason"].fillna("").astype(str)
        working_df = working_df[reason_series.str.contains(reason_pattern, na=False)]

    try:
        working_df = point_extractors.filter_by_region_and_bounds(
            working_df,
            region=region,
            bounds=bounds,
        )
    except Exception:
        return None

    if working_df is None or working_df.empty:
        return None

    target = pd.to_numeric(working_df[target_column], errors="coerce").dropna()
    n = int(len(target))
    if n < 2:
        return None

    resolved_range = _resolve_value_range(value_range)
    values = target.to_numpy(dtype=float)
    t = np.arange(1, n + 1, dtype=float)
    running_mean = np.cumsum(values) / t
    running_mean_sq = np.cumsum(values**2) / t
    running_var = np.clip(running_mean_sq - running_mean**2, 0.0, None)
    running_std = np.sqrt(running_var)

    delta_t = _delta_t_sequence(n, delta)
    log_term = np.log(3.0 / delta_t)
    c_t = running_std * np.sqrt(2.0 * log_term / t) + 3.0 * resolved_range * log_term / t
    mean_abs = np.abs(running_mean)

    # LB/UB only start updating from t=2 onward (Algorithm 2: the first
    # observation X_1 is collected but not yet combined into an interval).
    lb_candidates = mean_abs[1:] - c_t[1:]
    ub_candidates = mean_abs[1:] + c_t[1:]
    lb = np.maximum(0.0, np.maximum.accumulate(lb_candidates)) if n >= 2 else np.array([0.0])
    ub = np.minimum.accumulate(ub_candidates) if n >= 2 else np.array([np.inf])

    final_lb = float(lb[-1])
    final_ub = float(ub[-1])
    final_mean = float(running_mean[-1])

    stopped = (1.0 + epsilon) * final_lb >= (1.0 - epsilon) * final_ub
    estimate = math.copysign(1.0, final_mean) * 0.5 * (
        (1.0 + epsilon) * final_lb + (1.0 - epsilon) * final_ub
    ) if stopped else final_mean

    return {
        "target_column": target_column,
        "sample_size": n,
        "mean": final_mean,
        "std": float(running_std[-1]),
        "lb": final_lb,
        "ub": final_ub,
        "stopped": stopped,
        "estimate": estimate,
        "epsilon": epsilon,
        "delta": delta,
        "value_range": resolved_range,
        "filtered_df": working_df.loc[target.index],
    }


@dataclass(slots=True)
class EBStopService:
    def evaluate_request(
        self,
        data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        region = str(request.options.get("region", "custom"))
        reason_pattern = request.options.get("reason_pattern")
        if reason_pattern is not None:
            reason_pattern = str(reason_pattern)
        max_samples = request.options.get("max_samples")
        resolved_max_samples = int(max_samples) if max_samples is not None else None
        delta = 1.0 - request.confidence

        if "epsilon" not in request.options:
            raise ValueError("EBStop requires options['epsilon'] (relative error)")
        if "value_range" not in request.options:
            raise ValueError("EBStop requires options['value_range'] (bounded range of the metric)")
        epsilon = float(request.options["epsilon"])
        value_range = request.options["value_range"]

        df = _ensure_data_frame(data)
        has_any_rows = df is not None and not df.empty
        has_target_column = bool(has_any_rows and request.metric in df.columns)

        result = calculate_ebstop_decision(
            df,
            target_column=request.metric,
            epsilon=epsilon,
            delta=delta,
            value_range=value_range,
            bounds=request.bounds,
            region=region,
            reason_pattern=reason_pattern,
        )

        if not result:
            if not has_any_rows or has_target_column:
                return StatisticalReport(
                    method=request.method,
                    metric=request.metric,
                    sample_count=0,
                    estimate=None,
                    interval=None,
                    sufficient=False,
                    next_action="collect_more_samples",
                    diagnostics={
                        "status": "pending",
                        "message": "Fewer than 2 EBStop samples are available yet.",
                        "confidence": request.confidence,
                        "epsilon": epsilon,
                        "region": region,
                        "reason_pattern": reason_pattern,
                    },
                )
            return StatisticalReport(
                method=request.method,
                metric=request.metric,
                sample_count=0,
                estimate=None,
                interval=None,
                sufficient=False,
                next_action="error",
                diagnostics={
                    "status": "error",
                    "message": "EBStop decision could not be computed.",
                    "confidence": request.confidence,
                    "epsilon": epsilon,
                    "region": region,
                    "reason_pattern": reason_pattern,
                },
            )

        sample_count = int(result["sample_size"])
        sufficient = bool(result["stopped"])
        if sufficient:
            next_action = "stop"
        elif resolved_max_samples is not None and sample_count >= resolved_max_samples:
            next_action = "stop_max_samples"
        else:
            next_action = "collect_more_samples"

        return StatisticalReport(
            method=request.method,
            metric=request.metric,
            sample_count=sample_count,
            estimate=result["estimate"],
            interval=(result["lb"], result["ub"]),
            sufficient=sufficient,
            next_action=next_action,
            diagnostics={
                "status": "success",
                "confidence": request.confidence,
                "epsilon": epsilon,
                "value_range": result["value_range"],
                "mean": result["mean"],
                "std": result["std"],
                "region": region,
                "reason_pattern": reason_pattern,
                "max_samples": resolved_max_samples,
                "filtered_df": result["filtered_df"],
            },
        )


def evaluate_ebstop_request(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return EBStopService().evaluate_request(data, request)


__all__ = [
    "EBStopService",
    "calculate_ebstop_decision",
    "ebstop_update",
    "evaluate_ebstop_request",
]
