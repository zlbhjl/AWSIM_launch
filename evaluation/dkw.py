from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde
from sklearn.preprocessing import StandardScaler

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
import point_extractors


def records_to_data_frame(
    records: Sequence[EvaluationRecord] | None,
) -> pd.DataFrame | None:
    if not records:
        return None

    rows: list[dict[str, object]] = []
    for record in records:
        row: dict[str, object] = {}
        row.update(dict(record.input))
        row.update(dict(record.output))
        row["case_id"] = record.case_id
        row["target"] = record.target
        row["case_kind"] = record.case_kind
        row["status"] = record.status.value

        task_reason = record.meta.get("task_reason")
        if task_reason is not None:
            row["reason"] = task_reason

        for loop_key in ("global_loop_num", "loop_num", "history_loop_num"):
            loop_value = record.meta.get(loop_key)
            if loop_value is not None:
                row["loop_num"] = loop_value
                break

        rows.append(row)

    if not rows:
        return None
    return pd.DataFrame(rows)


def _ensure_data_frame(
    data: pd.DataFrame | Sequence[EvaluationRecord] | None,
) -> pd.DataFrame | None:
    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        return data
    return records_to_data_frame(data)


def _resolve_target_width(request: StatisticalRequest, epsilon: float | None) -> float | None:
    if request.target_width is not None:
        return request.target_width
    if epsilon is not None:
        return float(epsilon)
    return None


@dataclass(slots=True)
class DKWService:
    feature_names: list[str] | None = None

    def evaluate_request(
        self,
        data: pd.DataFrame | Sequence[EvaluationRecord] | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        q = float(request.options.get("q", 0.05))
        use_kde_weighting = bool(request.options.get("use_kde_weighting", False))
        region = str(request.options.get("region", "custom"))
        stage_target_samples = request.options.get("stage_target_samples")
        resolved_stage_target_samples = (
            int(stage_target_samples) if stage_target_samples is not None else None
        )
        epsilon = (
            float(request.options["epsilon"])
            if "epsilon" in request.options
            else None
        )
        df = _ensure_data_frame(data)
        result = calculate_quantile_with_dkw(
            df,
            target_column=request.metric,
            q=q,
            delta=1.0 - request.confidence,
            bounds=request.bounds,
            region=region,
            use_kde_weighting=use_kde_weighting,
            feature_names=self.feature_names,
        )
        target_width = _resolve_target_width(request, epsilon)
        if not result:
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
                    "message": "DKW evaluation could not be computed.",
                    "q": q,
                    "confidence": request.confidence,
                    "region": region,
                    "use_kde_weighting": use_kde_weighting,
                    "target_width": target_width,
                },
            )

        interval = (result["lower_bound"], result["upper_bound"])
        interval_width = float(result["upper_bound"] - result["lower_bound"])
        sufficient = target_width is not None and interval_width <= target_width
        if sufficient:
            next_action = "stop"
        elif (
            resolved_stage_target_samples is not None
            and int(result["sample_size"]) >= resolved_stage_target_samples
        ):
            next_action = "advance_stage"
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
                "q": q,
                "confidence": request.confidence,
                "delta": 1.0 - request.confidence,
                "target_width": target_width,
                "interval_width": interval_width,
                "region": region,
                "use_kde_weighting": use_kde_weighting,
                "stage_target_samples": resolved_stage_target_samples,
                "filtered_df": result["filtered_df"],
            },
        )

    def evaluate_request_multiple(
        self,
        data: pd.DataFrame | Sequence[EvaluationRecord] | None,
        *,
        metrics: Sequence[str],
        request: StatisticalRequest,
    ) -> dict[str, StatisticalReport]:
        if not metrics:
            return {}

        total_delta = 1.0 - request.confidence
        per_metric_confidence = 1.0 - (total_delta / len(metrics))
        reports: dict[str, StatisticalReport] = {}
        for metric in metrics:
            metric_request = StatisticalRequest(
                method=request.method,
                metric=metric,
                bounds=request.bounds,
                confidence=per_metric_confidence,
                target_width=request.target_width,
                options=request.options,
            )
            reports[metric] = self.evaluate_request(data, metric_request)
        return reports

    def evaluate_request_multiple_summary(
        self,
        data: pd.DataFrame | Sequence[EvaluationRecord] | None,
        *,
        metrics: Sequence[str],
        request: StatisticalRequest,
    ) -> dict[str, object]:
        reports = self.evaluate_request_multiple(
            data,
            metrics=metrics,
            request=request,
        )
        if not reports:
            return {
                "status": "error",
                "next_action": "error",
                "reports": {},
                "sample_count": 0,
            }

        if any(report.next_action == "error" for report in reports.values()):
            next_action = "error"
        elif all(report.next_action == "stop" for report in reports.values()):
            next_action = "stop"
        elif all(
            report.next_action in {"stop", "advance_stage"}
            for report in reports.values()
        ) and any(
            report.next_action == "advance_stage" for report in reports.values()
        ):
            next_action = "advance_stage"
        else:
            next_action = "collect_more_samples"

        sample_count = min(report.sample_count for report in reports.values())
        return {
            "status": "success",
            "next_action": next_action,
            "reports": reports,
            "sample_count": sample_count,
        }


def calculate_dkw_bounds(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    delta: float = 0.05,
    bounds: dict[str, tuple[float, float]] | None = None,
    region: str = "custom",
    use_kde_weighting: bool = False,
    feature_names: Sequence[str] | None = None,
) -> dict[str, object] | None:
    if df is None or df.empty or target_column not in df.columns:
        return None

    try:
        filtered_df = point_extractors.filter_by_region_and_bounds(
            df.copy(),
            region=region,
            bounds=bounds,
        )
    except Exception:
        return None

    if "c_collision" in filtered_df.columns:
        filtered_df = filtered_df[~filtered_df["c_collision"].isin([-1, "-1", -1.0])]

    data = pd.to_numeric(filtered_df[target_column], errors="coerce").dropna()
    if target_column in {"min_ttc", "min_distance", "z_margin"}:
        data = data[data >= 0]
    data = data.replace([np.inf, -np.inf], [5.0, -5.0])

    sample_size = len(data)
    if sample_size == 0:
        return None

    y_values = data.values
    valid_indices = data.index

    if use_kde_weighting and sample_size > 1:
        if not feature_names:
            raise ValueError("feature_names is required when use_kde_weighting=True")
        try:
            x_params = filtered_df.loc[valid_indices, list(feature_names)].copy()
            for column in feature_names:
                x_params[column] = pd.to_numeric(x_params[column], errors="coerce")
            x_params = x_params.dropna(subset=list(feature_names))
            if x_params.empty:
                return None
            data = data.loc[x_params.index]
            y_values = data.values
            if len(y_values) == 0:
                return None
            x_scaled = StandardScaler().fit_transform(x_params.values).T
            kde = gaussian_kde(x_scaled)
            density = np.clip(kde.evaluate(x_scaled), 1e-10, None)
            weights = 1.0 / density
            clip_value = np.percentile(weights, 99)
            weights = np.clip(weights, 0.0, clip_value)
            weights /= np.sum(weights)

            sort_idx = np.argsort(y_values)
            x_sorted = y_values[sort_idx]
            weights_sorted = weights[sort_idx]
            ecdf = np.cumsum(weights_sorted)
            effective_sample_size = float(1.0 / np.sum(weights**2))
        except Exception:
            return None
    else:
        x_sorted = np.sort(y_values)
        ecdf = np.arange(1, sample_size + 1) / sample_size
        effective_sample_size = float(sample_size)

    delta_margin = float(np.sqrt(np.log(2.0 / delta) / (2 * effective_sample_size)))
    lower_bound = np.maximum(ecdf - delta_margin, 0.0)
    upper_bound = np.minimum(ecdf + delta_margin, 1.0)

    return {
        "x": x_sorted,
        "ecdf": ecdf,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "delta": delta,
        "delta_margin": delta_margin,
        "sample_size": effective_sample_size,
        "filtered_df": filtered_df,
    }


def calculate_quantile_with_dkw(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    q: float = 0.05,
    delta: float = 0.05,
    bounds: dict[str, tuple[float, float]] | None = None,
    region: str = "custom",
    use_kde_weighting: bool = False,
    feature_names: Sequence[str] | None = None,
) -> dict[str, object] | None:
    dkw_bounds = calculate_dkw_bounds(
        df,
        target_column=target_column,
        delta=delta,
        bounds=bounds,
        region=region,
        use_kde_weighting=use_kde_weighting,
        feature_names=feature_names,
    )
    if dkw_bounds is None:
        return None

    x = dkw_bounds["x"]
    ecdf = dkw_bounds["ecdf"]
    upper = dkw_bounds["upper_bound"]
    lower = dkw_bounds["lower_bound"]

    estimate_index = np.searchsorted(ecdf, q)
    lower_index = np.searchsorted(upper, q)
    upper_index = np.searchsorted(lower, q)

    return {
        "q": q,
        "confidence_level": 1 - delta,
        "estimate": x[estimate_index] if estimate_index < len(x) else x[-1],
        "lower_bound": x[lower_index] if lower_index < len(x) else x[-1],
        "upper_bound": x[upper_index] if upper_index < len(x) else x[-1],
        "sample_size": dkw_bounds["sample_size"],
        "filtered_df": dkw_bounds["filtered_df"],
    }


def evaluate_and_summarize_dkw(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    q: float = 0.05,
    delta: float = 0.05,
    epsilon: float = 0.15,
) -> dict[str, object]:
    if df is None or df.empty or len(df) < 2:
        return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}

    bounds_result = calculate_quantile_with_dkw(
        df,
        target_column=target_column,
        q=q,
        delta=delta,
    )
    if not bounds_result:
        return {"status": "error", "message": "DKW評価を実行できませんでした。"}

    return {
        "status": "success",
        "q": q,
        "delta": delta,
        "epsilon": epsilon,
        "estimate": bounds_result["estimate"],
        "lower_bound": bounds_result["lower_bound"],
        "upper_bound": bounds_result["upper_bound"],
        "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
        "sample_size": bounds_result["sample_size"],
    }


def evaluate_and_summarize_dkw_multiple(
    df: pd.DataFrame | None,
    *,
    target_columns: Sequence[str],
    q: float = 0.05,
    delta_total: float = 0.05,
    epsilon: float = 0.15,
    use_kde_weighting: bool = False,
    region: str = "custom",
    bounds: dict[str, tuple[float, float]] | None = None,
    feature_names: Sequence[str] | None = None,
) -> dict[str, object]:
    if df is None or df.empty or len(df) < 2:
        return {"status": "error", "message": "評価対象のデータが不足しています(2件以上必要)。"}
    if len(target_columns) == 0:
        return {"status": "error", "message": "評価対象の指標が指定されていません。"}

    delta_i = delta_total / len(target_columns)
    results = {"status": "success", "metrics": {}, "epsilon": epsilon, "delta_total": delta_total}
    for target in target_columns:
        bounds_result = calculate_quantile_with_dkw(
            df,
            target_column=target,
            q=q,
            delta=delta_i,
            use_kde_weighting=use_kde_weighting,
            region=region,
            bounds=bounds,
            feature_names=feature_names,
        )
        if not bounds_result:
            return {
                "status": "error",
                "message": f"指標 '{target}' のDKW評価に失敗しました。有効なサンプルが不足しています。",
            }
        results["metrics"][target] = {
            "estimate": bounds_result["estimate"],
            "lower_bound": bounds_result["lower_bound"],
            "upper_bound": bounds_result["upper_bound"],
            "interval_width": bounds_result["upper_bound"] - bounds_result["lower_bound"],
            "sample_size": bounds_result["sample_size"],
            "filtered_df": bounds_result["filtered_df"],
        }
    return results


def evaluate_statistical_request(
    data: pd.DataFrame | Sequence[EvaluationRecord] | None,
    request: StatisticalRequest,
    *,
    feature_names: Sequence[str] | None = None,
) -> StatisticalReport:
    return DKWService(
        feature_names=list(feature_names) if feature_names is not None else None
    ).evaluate_request(data, request)


def evaluate_statistical_request_multiple(
    data: pd.DataFrame | Sequence[EvaluationRecord] | None,
    *,
    metrics: Sequence[str],
    request: StatisticalRequest,
    feature_names: Sequence[str] | None = None,
) -> dict[str, StatisticalReport]:
    return DKWService(
        feature_names=list(feature_names) if feature_names is not None else None
    ).evaluate_request_multiple(
        data,
        metrics=metrics,
        request=request,
    )


def evaluate_statistical_request_multiple_summary(
    data: pd.DataFrame | Sequence[EvaluationRecord] | None,
    *,
    metrics: Sequence[str],
    request: StatisticalRequest,
    feature_names: Sequence[str] | None = None,
) -> dict[str, object]:
    return DKWService(
        feature_names=list(feature_names) if feature_names is not None else None
    ).evaluate_request_multiple_summary(
        data,
        metrics=metrics,
        request=request,
    )


__all__ = [
    "DKWService",
    "calculate_dkw_bounds",
    "calculate_quantile_with_dkw",
    "evaluate_and_summarize_dkw",
    "evaluate_and_summarize_dkw_multiple",
    "evaluate_statistical_request",
    "evaluate_statistical_request_multiple",
    "evaluate_statistical_request_multiple_summary",
    "records_to_data_frame",
]
