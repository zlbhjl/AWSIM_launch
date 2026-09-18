from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.dkw import records_to_data_frame
import point_extractors


def sprt_decide(
    *,
    samples: int,
    successes: int,
    p0: float,
    p1: float,
    alpha: float,
    beta: float,
) -> dict[str, object]:
    """Wald's Sequential Probability Ratio Test for two simple Bernoulli hypotheses.

    H0: p = p0, H1: p = p1 (Wald 1945; formalized for probabilistic model
    checking in Younes 2006, eq. 9). Computed in log space for numerical
    stability: log(f_m) = dm*log(p1/p0) + (m-dm)*log((1-p1)/(1-p0)).

    Accepts H0 if log(f_m) <= log(beta/(1-alpha)); accepts H1 if
    log(f_m) >= log((1-beta)/alpha); otherwise more samples are required.
    """
    if samples < 0:
        raise ValueError("samples must be non-negative")
    if not 0 <= successes <= samples:
        raise ValueError("successes must satisfy 0 <= successes <= samples")
    if not 0.0 < p0 < 1.0:
        raise ValueError("p0 must satisfy 0.0 < p0 < 1.0")
    if not 0.0 < p1 < 1.0:
        raise ValueError("p1 must satisfy 0.0 < p1 < 1.0")
    if p0 == p1:
        raise ValueError("p0 and p1 must differ")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must satisfy 0.0 < alpha < 1.0")
    if not 0.0 < beta < 1.0:
        raise ValueError("beta must satisfy 0.0 < beta < 1.0")

    failures = samples - successes
    log_likelihood_ratio = successes * math.log(p1 / p0) + failures * math.log(
        (1.0 - p1) / (1.0 - p0)
    )
    lower_log_threshold = math.log(beta / (1.0 - alpha))
    upper_log_threshold = math.log((1.0 - beta) / alpha)

    if log_likelihood_ratio <= lower_log_threshold:
        verdict = "h0"
    elif log_likelihood_ratio >= upper_log_threshold:
        verdict = "h1"
    else:
        verdict = "continue"

    return {
        "samples": samples,
        "successes": successes,
        "p0": p0,
        "p1": p1,
        "alpha": alpha,
        "beta": beta,
        "log_likelihood_ratio": log_likelihood_ratio,
        "lower_log_threshold": lower_log_threshold,
        "upper_log_threshold": upper_log_threshold,
        "verdict": verdict,
    }


def _ensure_data_frame(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
) -> pd.DataFrame | None:
    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        return data
    return records_to_data_frame(data)


def calculate_sprt_decision(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    p0: float,
    p1: float,
    alpha: float,
    beta: float,
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

    target = pd.to_numeric(working_df[target_column], errors="coerce")
    valid_mask = target.isin([0, 1])
    valid_df = working_df[valid_mask].copy()
    if valid_df.empty:
        return None

    target_valid = pd.to_numeric(valid_df[target_column], errors="coerce")
    samples = int(len(target_valid))
    successes = int((target_valid == 1).sum())

    decision = sprt_decide(
        samples=samples,
        successes=successes,
        p0=p0,
        p1=p1,
        alpha=alpha,
        beta=beta,
    )

    return {
        "target_column": target_column,
        "sample_size": samples,
        "success_count": successes,
        "estimate": successes / samples,
        "filtered_df": valid_df,
        **decision,
    }


def _resolve_two_sided_decision(
    df: pd.DataFrame | None,
    *,
    target_column: str,
    theta: float,
    delta: float,
    alpha: float,
    beta: float,
    gamma: float,
    bounds: dict[str, tuple[float, float]] | None,
    region: str,
    reason_pattern: str | None,
) -> dict[str, object] | None:
    """Younes (2006) sec. 5: two simultaneous one-sided SPRTs with an
    undecided outcome, guaranteeing bounded error for any true p (not only
    outside the indifference region as with the classic two-outcome test).
    """
    plus = calculate_sprt_decision(
        df,
        target_column=target_column,
        p0=theta,
        p1=theta - delta,
        alpha=alpha,
        beta=gamma,
        bounds=bounds,
        region=region,
        reason_pattern=reason_pattern,
    )
    minus = calculate_sprt_decision(
        df,
        target_column=target_column,
        p0=theta + delta,
        p1=theta,
        alpha=gamma,
        beta=beta,
        bounds=bounds,
        region=region,
        reason_pattern=reason_pattern,
    )
    if plus is None or minus is None:
        return None

    plus_verdict = plus["verdict"]
    minus_verdict = minus["verdict"]
    if plus_verdict == "h0" and minus_verdict == "h0":
        two_sided_verdict = "true"
    elif plus_verdict == "h1" and minus_verdict == "h1":
        two_sided_verdict = "false"
    elif plus_verdict == "continue" or minus_verdict == "continue":
        two_sided_verdict = "continue"
    else:
        two_sided_verdict = "undecided"

    return {
        "target_column": target_column,
        "sample_size": min(plus["sample_size"], minus["sample_size"]),
        "estimate": plus["estimate"],
        "filtered_df": plus["filtered_df"],
        "verdict": two_sided_verdict,
        "test_plus": plus,
        "test_minus": minus,
        "theta": theta,
        "delta": delta,
        "gamma": gamma,
        "alpha": alpha,
        "beta": beta,
    }


@dataclass(slots=True)
class SPRTService:
    def evaluate_request(
        self,
        data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        region = str(request.options.get("region", "custom"))
        min_samples = int(request.options.get("min_samples", 0))
        max_samples = request.options.get("max_samples")
        resolved_max_samples = int(max_samples) if max_samples is not None else None
        reason_pattern = request.options.get("reason_pattern")
        if reason_pattern is not None:
            reason_pattern = str(reason_pattern)
        alpha = 1.0 - request.confidence
        gamma = request.options.get("gamma")

        df = _ensure_data_frame(data)
        has_any_rows = df is not None and not df.empty
        has_target_column = bool(has_any_rows and request.metric in df.columns)

        if gamma is not None:
            theta = float(request.options["theta"])
            delta = float(request.options["delta"])
            beta = float(request.options["beta"])
            result = _resolve_two_sided_decision(
                df,
                target_column=request.metric,
                theta=theta,
                delta=delta,
                alpha=alpha,
                beta=beta,
                gamma=float(gamma),
                bounds=request.bounds,
                region=region,
                reason_pattern=reason_pattern,
            )
            decided_verdicts = {"true", "false", "undecided"}
        else:
            p0 = float(request.options["p0"])
            p1 = float(request.options["p1"])
            beta = float(request.options["beta"])
            result = calculate_sprt_decision(
                df,
                target_column=request.metric,
                p0=p0,
                p1=p1,
                alpha=alpha,
                beta=beta,
                bounds=request.bounds,
                region=region,
                reason_pattern=reason_pattern,
            )
            decided_verdicts = {"h0", "h1"}

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
                        "message": "No SPRT samples are available yet.",
                        "confidence": request.confidence,
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
                    "message": "SPRT decision could not be computed.",
                    "confidence": request.confidence,
                    "region": region,
                    "reason_pattern": reason_pattern,
                },
            )

        verdict = result["verdict"]
        sample_count = int(result["sample_size"])
        meets_min_samples = sample_count >= min_samples
        sufficient = verdict in decided_verdicts and meets_min_samples
        if sufficient:
            next_action = "stop"
        elif resolved_max_samples is not None and sample_count >= resolved_max_samples:
            next_action = "stop_max_samples"
        else:
            next_action = "collect_more_samples"

        diagnostics: dict[str, object] = {
            "status": "success",
            "verdict": verdict,
            "confidence": request.confidence,
            "alpha": alpha,
            "region": region,
            "reason_pattern": reason_pattern,
            "min_samples": min_samples,
            "max_samples": resolved_max_samples,
            "meets_min_samples": meets_min_samples,
            "filtered_df": result["filtered_df"],
        }
        if gamma is not None:
            diagnostics.update(
                {
                    "gamma": result["gamma"],
                    "theta": result["theta"],
                    "delta": result["delta"],
                    "beta": result["beta"],
                    "test_plus": result["test_plus"],
                    "test_minus": result["test_minus"],
                }
            )
        else:
            diagnostics.update(
                {
                    "p0": result["p0"],
                    "p1": result["p1"],
                    "beta": result["beta"],
                    "success_count": result["success_count"],
                    "log_likelihood_ratio": result["log_likelihood_ratio"],
                    "lower_log_threshold": result["lower_log_threshold"],
                    "upper_log_threshold": result["upper_log_threshold"],
                }
            )

        return StatisticalReport(
            method=request.method,
            metric=request.metric,
            sample_count=sample_count,
            estimate=result["estimate"],
            interval=None,
            sufficient=sufficient,
            next_action=next_action,
            diagnostics=diagnostics,
        )


def evaluate_sprt_request(
    data: pd.DataFrame | list[EvaluationRecord] | tuple[EvaluationRecord, ...] | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return SPRTService().evaluate_request(data, request)


__all__ = [
    "SPRTService",
    "calculate_sprt_decision",
    "evaluate_sprt_request",
    "sprt_decide",
]
