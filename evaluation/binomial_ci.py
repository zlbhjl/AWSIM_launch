from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from scipy.stats import beta, norm

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.alpha_spending import spending_budget_at
from evaluation.dkw import records_to_data_frame
import point_extractors


def calculate_binomial_confidence_interval(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    confidence_level: float = 0.95,
    method: str = "wilson",
    bounds: dict[str, tuple[float, float]] | None = None,
    region: str = "custom",
    reason_pattern: str | None = None,
    anytime_valid: bool = False,
) -> dict[str, object] | None:
    # "wilson" is a CLT-approximation method: its actual coverage can fall below
    # confidence_level when the true proportion is near 0/1 and n is still small
    # (e.g. n=20, true p=0.05 -> ~92.5% actual coverage for a nominal 95% interval).
    # "clopper-pearson" is exact (no CLT approximation) and never undershoots the
    # nominal coverage, at the cost of a wider interval. Use clopper-pearson for
    # safety-critical decisions (e.g. c_collision).
    if df is None or df.empty or target_column not in df.columns:
        return None

    working_df = df.copy()
    # Use only records whose final analysis succeeded. A raw execution timeout
    # remains usable when its trace is recovered and successfully interpreted,
    # because the resulting EvaluationRecord has final status ``success``.
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

    target = pd.to_numeric(working_df[target_column], errors="coerce")
    valid_mask = target.isin([0, 1])
    valid_df = working_df[valid_mask].copy()
    if valid_df.empty:
        return None

    target_valid = pd.to_numeric(valid_df[target_column], errors="coerce")
    n = int(len(target_valid))
    k = int((target_valid == 1).sum())
    p_hat = k / n
    # anytime_valid replaces the fixed per-look alpha with a per-sample-count
    # budget (evaluation/alpha_spending.py, the same union-bound "peeling"
    # schedule evaluation/ebstop.py uses) so a fresh interval built at every
    # single n remains simultaneously valid across all possible stopping
    # points, not just at whichever n happens to be checked. The cost is real:
    # reaching the same target width typically needs ~5-8x more samples than
    # the fixed-alpha interval below, because the per-look budget shrinks (and
    # the required z-value grows) as n grows.
    alpha = (
        spending_budget_at(n, total_delta=1.0 - confidence_level)
        if anytime_valid
        else 1.0 - confidence_level
    )

    if method == "wilson":
        z = norm.ppf(1.0 - alpha / 2.0)
        denom = 1.0 + (z**2) / n
        center = (p_hat + (z**2) / (2.0 * n)) / denom
        half_width = (
            z
            * (((p_hat * (1.0 - p_hat) / n) + (z**2) / (4.0 * (n**2))) ** 0.5)
            / denom
        )
        lower = max(0.0, center - half_width)
        upper = min(1.0, center + half_width)
    elif method == "clopper-pearson":
        lower = 0.0 if k == 0 else float(beta.ppf(alpha / 2.0, k, n - k + 1))
        upper = 1.0 if k == n else float(beta.ppf(1.0 - alpha / 2.0, k + 1, n - k))
    else:
        raise ValueError(f"Unsupported binomial CI method: {method}")

    return {
        "target_column": target_column,
        "method": method,
        "confidence_level": confidence_level,
        "anytime_valid": anytime_valid,
        "effective_confidence_level": 1.0 - alpha,
        "sample_size": n,
        "success_count": k,
        "estimate": p_hat,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
        "filtered_df": valid_df,
    }


def evaluate_and_summarize_binomial_ci(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    confidence_level: float = 0.95,
    method: str = "wilson",
    bounds: dict[str, tuple[float, float]] | None = None,
    region: str = "custom",
    reason_pattern: str | None = None,
) -> dict[str, object]:
    result = calculate_binomial_confidence_interval(
        df,
        target_column=target_column,
        confidence_level=confidence_level,
        method=method,
        bounds=bounds,
        region=region,
        reason_pattern=reason_pattern,
    )
    if not result:
        return {"status": "error", "message": "二項信頼区間を計算できませんでした。"}

    return {
        "status": "success",
        "target_column": target_column,
        "method": method,
        "confidence_level": confidence_level,
        "sample_size": result["sample_size"],
        "success_count": result["success_count"],
        "estimate": result["estimate"],
        "lower_bound": result["lower_bound"],
        "upper_bound": result["upper_bound"],
        "interval_width": result["interval_width"],
        "filtered_df": result["filtered_df"],
    }


def _ensure_data_frame(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
) -> pd.DataFrame | None:
    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        return data
    return records_to_data_frame(data)


@dataclass(slots=True)
class BinomialCIService:
    def evaluate_request(
        self,
        data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        method = str(request.options.get("method", "wilson"))
        region = str(request.options.get("region", "custom"))
        min_samples = int(request.options.get("min_samples", 0))
        max_samples = request.options.get("max_samples")
        resolved_max_samples = int(max_samples) if max_samples is not None else None
        reason_pattern = request.options.get("reason_pattern")
        if reason_pattern is not None:
            reason_pattern = str(reason_pattern)
        anytime_valid = bool(request.options.get("anytime_valid", False))

        df = _ensure_data_frame(data)
        has_any_rows = df is not None and not df.empty
        has_target_column = bool(has_any_rows and request.metric in df.columns)
        result = calculate_binomial_confidence_interval(
            df,
            target_column=request.metric,
            confidence_level=request.confidence,
            method=method,
            bounds=request.bounds,
            region=region,
            reason_pattern=reason_pattern,
            anytime_valid=anytime_valid,
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
                        "message": "No binomial samples are available yet.",
                        "confidence": request.confidence,
                        "target_width": request.target_width,
                        "method": method,
                        "region": region,
                        "reason_pattern": reason_pattern,
                        "anytime_valid": anytime_valid,
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
                    "message": "Binomial confidence interval could not be computed.",
                    "confidence": request.confidence,
                    "target_width": request.target_width,
                    "method": method,
                    "region": region,
                    "reason_pattern": reason_pattern,
                    "anytime_valid": anytime_valid,
                },
            )

        interval = (result["lower_bound"], result["upper_bound"])
        meets_target_width = (
            request.target_width is not None
            and result["interval_width"] <= request.target_width
        )
        meets_min_samples = int(result["sample_size"]) >= min_samples
        sufficient = meets_target_width and meets_min_samples
        if sufficient:
            next_action = "stop"
        elif (
            resolved_max_samples is not None
            and int(result["sample_size"]) >= resolved_max_samples
        ):
            next_action = "stop_max_samples"
        else:
            next_action = "collect_more_samples"
        return StatisticalReport(
            method=request.method,
            metric=request.metric,
            sample_count=int(result["sample_size"]),
            estimate=result["estimate"],
            interval=interval,
            sufficient=sufficient,
            next_action=next_action,
            diagnostics={
                "status": "success",
                "confidence": request.confidence,
                "target_width": request.target_width,
                "method": method,
                "region": region,
                "reason_pattern": reason_pattern,
                "min_samples": min_samples,
                "max_samples": resolved_max_samples,
                "meets_min_samples": meets_min_samples,
                "meets_target_width": meets_target_width,
                "success_count": int(result["success_count"]),
                "interval_width": float(result["interval_width"]),
                "anytime_valid": anytime_valid,
                "effective_confidence_level": float(result["effective_confidence_level"]),
                "filtered_df": result["filtered_df"],
            },
        )


def evaluate_binomial_request(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return BinomialCIService().evaluate_request(data, request)


__all__ = [
    "BinomialCIService",
    "calculate_binomial_confidence_interval",
    "evaluate_and_summarize_binomial_ci",
    "evaluate_binomial_request",
]
