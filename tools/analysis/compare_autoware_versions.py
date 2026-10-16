"""Paired comparison of one baseline dataset and several replay datasets.

The baseline (Autoware 1.7.1) is the original binomial_ci run; every replay
(1.8.0, 1.9.0, ...) re-ran the same inputs and is paired by
``meta_replay_source_loop_num``. Failed replay rows are kept as results:
they are reported separately instead of being retried or silently dropped.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import binomtest, chi2, fisher_exact, wilcoxon


PARAM_COLUMNS = ("dx0", "ego_speed", "npc_speed")
TTC_COLUMNS = (
    "c_ttc_1.5",
    "c_ttc_1.3",
    "c_ttc_1.2",
    "c_ttc_1.1",
    "c_ttc_0.9",
    "c_ttc_0.7",
    "c_ttc_0.5",
    "c_ttc_0.3",
)
OUTCOME_COLUMNS = ("c_collision", *TTC_COLUMNS, "c_pos_diff_4.0", "min_ttc", "min_distance")
ZONE_COLUMNS = ("theory_zone_a", "theory_zone_b")
# Severity level per case: number of violated TTC thresholds (0-8); 9 means collision.
COLLISION_LEVEL = len(TTC_COLUMNS) + 1
REGION_BINS = {
    "dx0": np.arange(10.0, 25.0 + 1e-9, 2.5),
    "ego_speed": np.arange(30.0, 40.0 + 1e-9, 2.0),
    "npc_speed": np.arange(10.0, 25.0 + 1e-9, 2.5),
}
KINEMATICS_COLUMNS = ("min_ttc", "min_distance", "min_ttb", "z_margin")
# AW_Kinematics_Extractor projects 5.0 s ahead in 0.1 s steps; inf means no contact
# within the horizon, so it is ranked just beyond the bound for paired rank tests.
TTC_HORIZON_CAP = 5.1
VALID = "valid"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-csv", required=True, help="Original dataset (for example 1.7.1 success CSV)")
    parser.add_argument("--base-label", default="171")
    parser.add_argument(
        "--replay",
        action="append",
        required=True,
        metavar="LABEL=CSV",
        help="Replay dataset paired by meta_replay_source_loop_num. Repeat for each version.",
    )
    parser.add_argument("--repeat-csv", help="Same-version repeat of the base (noise baseline)")
    parser.add_argument(
        "--repeat-manifest-csv",
        help="prepare_version_repeat manifest (source_loop_num and cohort_* columns) for --repeat-csv",
    )
    parser.add_argument(
        "--kinematics-csv",
        help="extract_trace_kinematics output; replaces empty min_ttc/min_distance/min_ttb/z_margin",
    )
    parser.add_argument(
        "--kinematics-experiment",
        action="append",
        default=[],
        metavar="LABEL=EXPERIMENT",
        help="Map a version label to the trace_index experiment name in --kinematics-csv",
    )
    parser.add_argument("--reason-pattern", default="BINOMIAL_CI:")
    parser.add_argument("--high-risk-ego-speed", type=float, default=38.0)
    parser.add_argument("--high-risk-max-dx0", type=float, default=14.0)
    parser.add_argument("--output-dir", required=True)
    return parser


def _outcome_state(frame: pd.DataFrame) -> pd.Series:
    valid = frame["status"].astype(str).str.lower().eq("success") & frame["c_collision"].isin([0, 1])
    return frame["status"].astype(str).where(~valid, VALID)


def load_base(path: str, reason_pattern: str) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    eligible = (
        frame["status"].astype(str).str.lower().eq("success")
        & frame["reason"].astype(str).str.contains(reason_pattern, regex=False)
        & pd.to_numeric(frame["c_collision"], errors="coerce").isin([0, 1])
    )
    frame = frame.loc[eligible].copy()
    frame["case"] = frame["loop_num"].astype(int)
    return frame.set_index("case")


def load_replay(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    frame["case"] = frame["meta_replay_source_loop_num"].astype(int)
    if frame["case"].duplicated().any():
        raise ValueError(f"{path} contains duplicated replay source loops")
    frame["c_collision"] = pd.to_numeric(frame["c_collision"], errors="coerce")
    return frame.set_index("case")


def build_merged(base: pd.DataFrame, replays: dict[str, pd.DataFrame], base_label: str) -> pd.DataFrame:
    merged = base[list(PARAM_COLUMNS)].copy()
    for column in ZONE_COLUMNS:
        if column in base.columns:
            merged[column] = base[column]
    sources = {base_label: base, **replays}
    for label, frame in sources.items():
        frame = frame.reindex(merged.index)
        merged[f"state_{label}"] = VALID if label == base_label else _outcome_state(frame)
        merged[f"worker_{label}"] = frame.get("worker_id")
        if label != base_label:
            merged[f"timeout_reason_{label}"] = frame.get("meta_raw_timeout_reason")
        for column in OUTCOME_COLUMNS:
            if column in frame.columns:
                merged[f"{column}_{label}"] = pd.to_numeric(frame[column], errors="coerce")
    missing = [label for label in replays if merged[f"state_{label}"].isna().any()]
    if missing:
        raise ValueError(f"replay datasets do not cover every base case: {missing}")
    return merged


def mcnemar(before: pd.Series, after: pd.Series) -> dict[str, object]:
    before = before.astype(int)
    after = after.astype(int)
    up = int(((before == 0) & (after == 1)).sum())
    down = int(((before == 1) & (after == 0)).sum())
    result: dict[str, object] = {
        "n": int(len(before)),
        "rate_before": float(before.mean()),
        "rate_after": float(after.mean()),
        "diff_points": float((after.mean() - before.mean()) * 100),
        "agree_rate": float((before == after).mean()),
        "zero_to_one": up,
        "one_to_zero": down,
        "p_value": float(binomtest(up, up + down).pvalue) if up + down else 1.0,
    }
    if up + down:
        # The conditional MLE of the paired odds ratio is b/c; its exact CI follows
        # from the binomial CI of b/(b+c).
        ci = binomtest(up, up + down).proportion_ci(method="exact")
        to_or = lambda q: float("inf") if q >= 1 else q / (1 - q)
        result["paired_odds_ratio"] = up / down if down else float("inf")
        result["paired_odds_ratio_ci95"] = [to_or(ci.low), to_or(ci.high)]
    return result


def cochran_q(matrix: np.ndarray) -> dict[str, float]:
    k = matrix.shape[1]
    col = matrix.sum(axis=0)
    row = matrix.sum(axis=1)
    total = matrix.sum()
    denominator = k * total - (row**2).sum()
    q = (k - 1) * (k * (col**2).sum() - total**2) / denominator if denominator else 0.0
    return {"q": float(q), "df": k - 1, "p_value": float(chi2.sf(q, k - 1))}


def holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, value * (len(ordered) - rank)))
        adjusted[key] = running
    return adjusted


def failure_analysis(merged: pd.DataFrame, labels: list[str], base_label: str, args) -> dict[str, object]:
    replay_labels = [label for label in labels if label != base_label]
    result: dict[str, object] = {
        "status_counts": {
            label: merged[f"state_{label}"].value_counts().to_dict() for label in replay_labels
        }
    }
    high_risk = (merged["ego_speed"] >= args.high_risk_ego_speed) & (merged["dx0"] <= args.high_risk_max_dx0)
    result["high_risk_region"] = {
        "definition": f"ego_speed >= {args.high_risk_ego_speed} and dx0 <= {args.high_risk_max_dx0}",
        "cases": int(high_risk.sum()),
    }
    collided = merged[f"c_collision_{base_label}"].astype(int) == 1
    for label in replay_labels:
        failed = merged[f"state_{label}"] != VALID
        timeout = merged[f"state_{label}"] == "timeout"
        table = [
            [int((failed & collided).sum()), int((~failed & collided).sum())],
            [int((failed & ~collided).sum()), int((~failed & ~collided).sum())],
        ]
        odds, p_value = fisher_exact(table)
        result[f"failure_vs_base_collision_{label}"] = {"table": table, "odds_ratio": odds, "p_value": p_value}
        result["high_risk_region"][f"timeout_rate_{label}"] = {
            "inside": float(timeout[high_risk].mean()),
            "outside": float(timeout[~high_risk].mean()),
        }
    for left, right in itertools.combinations(replay_labels, 2):
        failed_left = merged[f"state_{left}"] != VALID
        failed_right = merged[f"state_{right}"] != VALID
        crosstab = pd.crosstab(merged[f"state_{left}"], merged[f"state_{right}"])
        table = [
            [int((failed_left & failed_right).sum()), int((failed_left & ~failed_right).sum())],
            [int((~failed_left & failed_right).sum()), int((~failed_left & ~failed_right).sum())],
        ]
        odds, p_value = fisher_exact(table)
        shared = merged[failed_left & failed_right]
        result[f"overlap_{left}_{right}"] = {
            "crosstab": {row: crosstab.loc[row].to_dict() for row in crosstab.index},
            "both_failed": table[0][0],
            "expected_if_independent": float(failed_left.sum() * failed_right.sum() / len(merged)),
            "odds_ratio": odds,
            "p_value": p_value,
            "shared_failure_states": shared[[f"state_{left}", f"state_{right}"]]
            .astype(str)
            .agg("/".join, axis=1)
            .value_counts()
            .to_dict(),
            "shared_cases": [int(case) for case in shared.index],
            "shared_params_mean": shared[list(PARAM_COLUMNS)].mean().round(2).to_dict(),
            "shared_base_collision_rate": float(shared[f"c_collision_{base_label}"].mean()),
        }
    return result


def collision_analysis(complete: pd.DataFrame, merged: pd.DataFrame, labels: list[str], base_label: str) -> dict[str, object]:
    result: dict[str, object] = {"complete_cases": int(len(complete))}
    for left, right in itertools.combinations(labels, 2):
        result[f"mcnemar_{left}_{right}"] = mcnemar(
            complete[f"c_collision_{left}"], complete[f"c_collision_{right}"]
        )
    result["cochran_q"] = cochran_q(
        complete[[f"c_collision_{label}" for label in labels]].astype(int).to_numpy()
    )
    pattern = complete[[f"c_collision_{label}" for label in labels]].astype(int).astype(str).agg("".join, axis=1)
    result["pattern_order"] = labels
    result["patterns"] = pattern.value_counts().sort_index().to_dict()

    replay_labels = [label for label in labels if label != base_label]
    if len(replay_labels) >= 2:
        first, second = replay_labels[:2]
        flipped = complete[complete[f"c_collision_{base_label}"] != complete[f"c_collision_{first}"]]
        follows_first = (flipped[f"c_collision_{second}"] == flipped[f"c_collision_{first}"]).sum()
        result[f"{base_label}_{first}_flips_followed_by_{second}"] = {
            "flipped_cases": int(len(flipped)),
            f"{second}_agrees_with_{first}": int(follows_first),
            f"{second}_agrees_with_{base_label}": int(len(flipped) - follows_first),
        }

    sensitivity = {}
    for label in replay_labels:
        observed = merged[f"c_collision_{label}"].where(merged[f"state_{label}"] == VALID)
        sensitivity[label] = {
            "valid_only": float(observed.mean()),
            "failures_as_no_collision": float(observed.fillna(0).mean()),
            "failures_as_collision": float(observed.fillna(1).mean()),
            "failures_as_base_result": float(observed.fillna(merged[f"c_collision_{base_label}"]).mean()),
        }
    sensitivity[base_label] = float(merged[f"c_collision_{base_label}"].mean())
    result["sensitivity_all_cases"] = sensitivity
    return result


def ttc_analysis(complete: pd.DataFrame, labels: list[str]) -> dict[str, object]:
    tests: dict[str, dict[str, object]] = {}
    for column in TTC_COLUMNS:
        for left, right in itertools.combinations(labels, 2):
            names = [f"{column}_{left}", f"{column}_{right}"]
            if not set(names).issubset(complete.columns):
                continue
            pair = complete[names].dropna()
            pair = pair[pair.isin([0, 1]).all(axis=1)]
            tests[f"{column}|{left}->{right}"] = mcnemar(pair.iloc[:, 0], pair.iloc[:, 1])
    adjusted = holm({key: value["p_value"] for key, value in tests.items()})
    for key, value in tests.items():
        value["p_holm"] = adjusted[key]

    return {"binary": tests}


def attach_kinematics(merged: pd.DataFrame, path: str, mapping: dict[str, str]) -> pd.DataFrame:
    extracted = pd.read_csv(path)
    merged = merged.copy()
    for label, experiment in mapping.items():
        rows = extracted[extracted["experiment"] == experiment].set_index("source_loop_num")
        if rows.index.duplicated().any():
            raise ValueError(f"duplicated source_loop_num for {experiment} in {path}")
        rows = rows.reindex(merged.index)
        for column in KINEMATICS_COLUMNS:
            merged[f"{column}_{label}"] = pd.to_numeric(rows[column], errors="coerce")
        merged[f"kinematics_collision_{label}"] = pd.to_numeric(rows["kinematics_c_collision"], errors="coerce")
    return merged


def _continuous_values(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column].astype(float)
    if column.startswith("min_ttc_"):
        return values.replace(np.inf, TTC_HORIZON_CAP)
    return values.replace([np.inf, -np.inf], np.nan)


def kinematics_analysis(complete: pd.DataFrame, labels: list[str]) -> dict[str, object] | None:
    if not all(f"min_distance_{label}" in complete.columns for label in labels):
        return None
    if complete[[f"min_distance_{label}" for label in labels]].isna().all().all():
        return None
    result: dict[str, object] = {"extractor_vs_maude": {}, "summary": {}, "paired": {}}
    for label in labels:
        maude = complete[f"c_collision_{label}"]
        extractor = complete.get(f"kinematics_collision_{label}")
        if extractor is not None:
            known = extractor.notna()
            result["extractor_vs_maude"][label] = {
                "n": int(known.sum()),
                "agree_rate": float((extractor[known] == maude[known]).mean()),
                "extractor_only_collision": int(((extractor == 1) & (maude == 0)).sum()),
                "maude_only_collision": int(((extractor == 0) & (maude == 1)).sum()),
            }
        no_collision = complete[maude == 0]
        result["summary"][label] = {
            "min_distance_median_no_collision": float(_continuous_values(no_collision, f"min_distance_{label}").median()),
            "min_ttc_within_horizon_share_no_collision": float(np.isfinite(no_collision[f"min_ttc_{label}"].astype(float)).mean()),
        }

    tests: dict[str, dict[str, object]] = {}
    for column in ("min_distance", "min_ttc"):
        for left, right in itertools.combinations(labels, 2):
            for subset_name, subset in (
                ("all", complete),
                ("no_collision_both", complete[(complete[f"c_collision_{left}"] == 0) & (complete[f"c_collision_{right}"] == 0)]),
            ):
                before = _continuous_values(subset, f"{column}_{left}")
                after = _continuous_values(subset, f"{column}_{right}")
                pair = pd.concat([before, after], axis=1).dropna()
                diff = pair.iloc[:, 1] - pair.iloc[:, 0]
                entry: dict[str, object] = {
                    "n": int(len(pair)),
                    "median_before": float(pair.iloc[:, 0].median()),
                    "median_after": float(pair.iloc[:, 1].median()),
                    "median_diff": float(diff.median()),
                    "mean_diff": float(diff.mean()),
                    "share_lower_after": float((diff < 0).mean()),
                    "share_higher_after": float((diff > 0).mean()),
                    "share_equal": float((diff == 0).mean()),
                    "wilcoxon_p": float(wilcoxon(diff[diff != 0]).pvalue) if (diff != 0).sum() >= 10 else None,
                }
                tests[f"{column}|{subset_name}|{left}->{right}"] = entry
    adjusted = holm({key: value["wilcoxon_p"] for key, value in tests.items() if value["wilcoxon_p"] is not None})
    for key, value in tests.items():
        value["p_holm"] = adjusted.get(key)
    result["paired"] = tests
    return result


def severity_levels(frame: pd.DataFrame, label: str) -> pd.Series:
    violated = sum(frame[f"{column}_{label}"].astype(int) for column in TTC_COLUMNS)
    return violated.where(frame[f"c_collision_{label}"].astype(int) != 1, COLLISION_LEVEL)


def severity_analysis(complete: pd.DataFrame, labels: list[str]) -> dict[str, object]:
    levels = {label: severity_levels(complete, label) for label in labels}
    result: dict[str, object] = {
        "levels": "0 = no TTC threshold violated, 1-8 = number of violated TTC thresholds (1.5s..0.3s), 9 = collision",
        "distribution": {label: levels[label].value_counts().sort_index().to_dict() for label in labels},
        "paired": {},
    }
    tests = {}
    for left, right in itertools.combinations(labels, 2):
        diff = levels[right] - levels[left]
        transition = pd.crosstab(levels[left], levels[right])
        tests[f"{left}->{right}"] = {
            "n": int(len(diff)),
            "mean_level_before": float(levels[left].mean()),
            "mean_level_after": float(levels[right].mean()),
            "mean_diff": float(diff.mean()),
            "share_worse": float((diff > 0).mean()),
            "share_better": float((diff < 0).mean()),
            "share_same": float((diff == 0).mean()),
            "diff_distribution": diff.value_counts().sort_index().to_dict(),
            "wilcoxon_p": float(wilcoxon(diff[diff != 0]).pvalue) if (diff != 0).sum() >= 10 else None,
            "transition": {int(row): {int(col): int(transition.loc[row, col]) for col in transition.columns} for row in transition.index},
        }
    result["paired"] = tests
    return result


def proximity_analysis(complete: pd.DataFrame, labels: list[str]) -> dict[str, object] | None:
    if not all(f"c_pos_diff_4.0_{label}" in complete.columns for label in labels):
        return None
    result = {}
    for left, right in itertools.combinations(labels, 2):
        pair = complete[[f"c_pos_diff_4.0_{left}", f"c_pos_diff_4.0_{right}"]].dropna()
        pair = pair[pair.isin([0, 1]).all(axis=1)]
        result[f"{left}->{right}"] = mcnemar(pair.iloc[:, 0], pair.iloc[:, 1])
    return result


def region_analysis(complete: pd.DataFrame, labels: list[str], metrics=("c_collision", "c_ttc_1.5")) -> dict[str, object]:
    result: dict[str, object] = {}
    for left, right in itertools.combinations(labels, 2):
        for metric in metrics:
            for param, edges in REGION_BINS.items():
                bins = pd.cut(complete[param], edges, include_lowest=True)
                rows = {}
                for interval, group in complete.groupby(bins, observed=True):
                    rows[f"{interval.left:g}-{interval.right:g}"] = mcnemar(
                        group[f"{metric}_{left}"], group[f"{metric}_{right}"]
                    )
                result[f"{metric}|{param}|{left}->{right}"] = rows
    return result


def boundary_analysis(complete: pd.DataFrame, labels: list[str], bootstrap: int = 200, seed: int = 190) -> dict[str, object]:
    from sklearn.linear_model import LogisticRegression

    features = complete[list(PARAM_COLUMNS)].to_numpy(dtype=float)
    npc_median = float(complete["npc_speed"].median())
    ego_grid = [30.0, 32.5, 35.0, 37.5, 40.0]

    def fit(rows: np.ndarray, label: str) -> np.ndarray:
        model = LogisticRegression(C=1e6, max_iter=2000)
        model.fit(features[rows], complete[f"c_collision_{label}"].to_numpy(dtype=int)[rows])
        return np.concatenate([model.intercept_, model.coef_[0]])

    def boundary_dx0(params: np.ndarray, ego: float) -> float:
        intercept, b_dx0, b_ego, b_npc = params
        return float(-(intercept + b_ego * ego + b_npc * npc_median) / b_dx0)

    everything = np.arange(len(complete))
    fitted = {label: fit(everything, label) for label in labels}
    result: dict[str, object] = {
        "model": "logit P(collision) = b0 + b_dx0*dx0 + b_ego*ego_speed + b_npc*npc_speed",
        "npc_speed_for_boundary": npc_median,
        "coefficients": {label: dict(zip(("intercept", "dx0", "ego_speed", "npc_speed"), map(float, params))) for label, params in fitted.items()},
        "boundary_dx0_at_p50": {
            label: {f"{ego:g}": boundary_dx0(params, ego) for ego in ego_grid} for label, params in fitted.items()
        },
        "paired_bootstrap_diff_at_ego35": {},
    }
    rng = np.random.default_rng(seed)
    samples = [rng.integers(0, len(complete), len(complete)) for _ in range(bootstrap)]
    boot = {label: np.array([boundary_dx0(fit(rows, label), 35.0) for rows in samples]) for label in labels}
    for left, right in itertools.combinations(labels, 2):
        diff = boot[right] - boot[left]
        result["paired_bootstrap_diff_at_ego35"][f"{left}->{right}"] = {
            "point": boundary_dx0(fitted[right], 35.0) - boundary_dx0(fitted[left], 35.0),
            "ci95": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))],
            "bootstrap": bootstrap,
        }
    return result


def zone_analysis(complete: pd.DataFrame, labels: list[str]) -> dict[str, object] | None:
    if "theory_zone_a" not in complete.columns:
        return None
    result = {}
    for zone, group in complete.groupby("theory_zone_a"):
        entry = {"n": int(len(group))}
        for left, right in itertools.combinations(labels, 2):
            for metric in ("c_collision", "c_ttc_1.5"):
                entry[f"{metric}|{left}->{right}"] = mcnemar(group[f"{metric}_{left}"], group[f"{metric}_{right}"])
            diff = severity_levels(group, right) - severity_levels(group, left)
            entry[f"severity|{left}->{right}"] = {
                "mean_diff": float(diff.mean()),
                "wilcoxon_p": float(wilcoxon(diff[diff != 0]).pvalue) if (diff != 0).sum() >= 10 else None,
            }
        result[str(zone)] = entry
    return result


def worker_analysis(complete: pd.DataFrame, left: str, right: str) -> dict[str, object]:
    rows = {}
    for worker, group in complete.groupby(f"worker_{right}"):
        rows[str(worker)] = mcnemar(group[f"c_collision_{left}"], group[f"c_collision_{right}"])
    return rows


def noise_baseline(args, complete: pd.DataFrame, labels: list[str], base_label: str) -> dict[str, object] | None:
    if not args.repeat_csv or not args.repeat_manifest_csv:
        return None
    repeat = load_replay(args.repeat_csv)
    cohorts = pd.read_csv(args.repeat_manifest_csv).set_index("source_loop_num")
    repeat = repeat[_outcome_state(repeat) == VALID]
    result: dict[str, object] = {}
    for cohort in ("cohort_random_validation", "cohort_discordant"):
        cases = cohorts.index[cohorts[cohort].astype(str).str.lower().eq("true")]
        cases = [case for case in cases if case in repeat.index and case in complete.index]
        entry = {
            "repeat_same_version": mcnemar(
                complete.loc[cases, f"c_collision_{base_label}"], repeat.loc[cases, "c_collision"]
            )
        }
        for left, right in itertools.combinations(labels, 2):
            entry[f"{left}->{right}"] = mcnemar(
                complete.loc[cases, f"c_collision_{left}"], complete.loc[cases, f"c_collision_{right}"]
            )
        result[cohort] = entry
    return result


def plot_flips(complete: pd.DataFrame, labels: list[str], path: Path) -> None:
    pairs = list(itertools.combinations(labels, 2))
    fig, axes = plt.subplots(1, len(pairs), figsize=(6 * len(pairs), 5), sharex=True, sharey=True)
    for ax, (left, right) in zip(np.atleast_1d(axes), pairs):
        before = complete[f"c_collision_{left}"].astype(int)
        after = complete[f"c_collision_{right}"].astype(int)
        same = before == after
        ax.scatter(complete.loc[same, "dx0"], complete.loc[same, "ego_speed"], s=2, c="#c8c8c8", label="same")
        for mask, colour, name in (
            ((before == 0) & (after == 1), "#d62728", "0 -> 1"),
            ((before == 1) & (after == 0), "#1f77b4", "1 -> 0"),
        ):
            ax.scatter(complete.loc[mask, "dx0"], complete.loc[mask, "ego_speed"], s=10, c=colour, label=f"{name} ({mask.sum()})")
        ax.set_title(f"collision {left} -> {right}")
        ax.set_xlabel("dx0 [m]")
        ax.legend(loc="lower right", fontsize=8)
    np.atleast_1d(axes)[0].set_ylabel("ego_speed [km/h]")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_failures(merged: pd.DataFrame, labels: list[str], base_label: str, path: Path) -> None:
    replay_labels = [label for label in labels if label != base_label]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(merged["dx0"], merged["ego_speed"], s=2, c="#dddddd", label="all cases")
    failed = [merged[f"state_{label}"] != VALID for label in replay_labels]
    any_failed = np.logical_or.reduce(failed)
    all_timeout = np.logical_and.reduce([merged[f"state_{label}"] == "timeout" for label in replay_labels])
    ax.scatter(merged.loc[any_failed & ~all_timeout, "dx0"], merged.loc[any_failed & ~all_timeout, "ego_speed"], s=12, c="#ff7f0e", label=f"failed in one version ({(any_failed & ~all_timeout).sum()})")
    ax.scatter(merged.loc[all_timeout, "dx0"], merged.loc[all_timeout, "ego_speed"], s=40, c="#d62728", marker="x", label=f"timeout in all replays ({all_timeout.sum()})")
    ax.set_xlabel("dx0 [m]")
    ax.set_ylabel("ego_speed [km/h]")
    ax.set_title("replay failures (" + ", ".join(replay_labels) + ")")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_kinematics(complete: pd.DataFrame, labels: list[str], path: Path) -> None:
    fig, (ax_distance, ax_ttc) = plt.subplots(1, 2, figsize=(13, 4.5))
    edges = np.arange(0, 5.3, 0.1)
    for label in labels:
        no_collision = complete[complete[f"c_collision_{label}"] == 0]
        distance = np.sort(_continuous_values(no_collision, f"min_distance_{label}").dropna())
        ax_distance.plot(distance, np.arange(1, len(distance) + 1) / len(distance), label=label)
        ttc = _continuous_values(no_collision, f"min_ttc_{label}").dropna()
        ax_ttc.hist(ttc, bins=edges, histtype="step", linewidth=1.5, label=label)
    ax_distance.set_xscale("symlog", linthresh=0.1)
    ax_distance.set_xlabel("min_distance [m] (Maude no-collision cases)")
    ax_distance.set_ylabel("cumulative share")
    ax_distance.legend()
    ax_ttc.set_xlabel(f"min_ttc [s] (no contact within 5 s plotted at {TTC_HORIZON_CAP})")
    ax_ttc.set_ylabel("cases")
    ax_ttc.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_severity(stats: dict[str, object], labels: list[str], path: Path) -> None:
    pairs = list(itertools.combinations(labels, 2))
    fig, axes = plt.subplots(1, len(pairs), figsize=(5.5 * len(pairs), 4.8))
    levels = list(range(COLLISION_LEVEL + 1))
    for ax, (left, right) in zip(np.atleast_1d(axes), pairs):
        transition = stats["paired"][f"{left}->{right}"]["transition"]
        matrix = np.array([[transition.get(row, {}).get(col, 0) for col in levels] for row in levels], dtype=float)
        shown = np.where(np.eye(len(levels), dtype=bool), np.nan, matrix)
        image = ax.imshow(shown, origin="lower", cmap="Reds")
        for row in levels:
            for col in levels:
                if matrix[row, col] and row != col:
                    ax.text(col, row, int(matrix[row, col]), ha="center", va="center", fontsize=6)
        ax.set_xticks(levels)
        ax.set_yticks(levels)
        ax.set_xlabel(f"severity level {right}")
        ax.set_ylabel(f"severity level {left}")
        ax.set_title(f"{left} -> {right} (diagonal hidden)")
        fig.colorbar(image, ax=ax, fraction=0.046)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_region(complete: pd.DataFrame, left: str, right: str, metric: str, path: Path) -> None:
    dx0_bins = pd.cut(complete["dx0"], REGION_BINS["dx0"], include_lowest=True)
    ego_bins = pd.cut(complete["ego_speed"], REGION_BINS["ego_speed"], include_lowest=True)
    diff = (complete[f"{metric}_{right}"] - complete[f"{metric}_{left}"]) * 100
    table = diff.groupby([ego_bins, dx0_bins], observed=False).mean().unstack()
    counts = diff.groupby([ego_bins, dx0_bins], observed=False).size().unstack()
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    limit = np.nanmax(np.abs(table.to_numpy())) or 1.0
    image = ax.imshow(table.to_numpy(), origin="lower", cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto")
    for i in range(table.shape[0]):
        for j in range(table.shape[1]):
            value = table.iat[i, j]
            if np.isfinite(value):
                colour = "white" if abs(value) > 0.6 * limit else "black"
                ax.text(j, i, f"{value:+.1f}\n(n={counts.iat[i, j]})", ha="center", va="center", fontsize=7, color=colour)
    ax.set_xticks(range(table.shape[1]), [f"{c.left:g}-{c.right:g}" for c in table.columns])
    ax.set_yticks(range(table.shape[0]), [f"{r.left:g}-{r.right:g}" for r in table.index])
    ax.set_xlabel("dx0 [m]")
    ax.set_ylabel("ego_speed [km/h]")
    ax.set_title(f"{metric} rate difference {right} - {left} [points]")
    fig.colorbar(image, ax=ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_ttc(stats: dict[str, object], labels: list[str], path: Path) -> None:
    thresholds = [float(column.split("_")[-1]) for column in TTC_COLUMNS]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for left, right in itertools.combinations(labels, 2):
        keys = [f"{column}|{left}->{right}" for column in TTC_COLUMNS]
        if not all(key in stats["binary"] for key in keys):
            continue
        ax.plot(thresholds, [stats["binary"][key]["diff_points"] for key in keys], marker="o", label=f"{right} - {left}")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.invert_xaxis()
    ax.set_xlabel("TTC threshold [s]")
    ax.set_ylabel("difference of rate [points]")
    ax.set_title("TTC danger rate difference (paired)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    base = load_base(args.base_csv, args.reason_pattern)
    replays = {}
    for spec in args.replay:
        label, _, path = spec.partition("=")
        replays[label] = load_replay(path)
    labels = [args.base_label, *replays]

    merged = build_merged(base, replays, args.base_label)
    if args.kinematics_csv:
        mapping = dict(spec.partition("=")[::2] for spec in args.kinematics_experiment)
        merged = attach_kinematics(merged, args.kinematics_csv, mapping)
    complete_mask = np.logical_and.reduce([merged[f"state_{label}"] == VALID for label in labels])
    complete = merged[complete_mask]
    replay_labels = list(replays)

    ttc = ttc_analysis(complete, labels)
    has_ttc = all(f"{column}_{label}" in complete.columns for column in TTC_COLUMNS for label in labels)
    can_fit_boundary = len(complete) >= 50 and all(
        complete[f"c_collision_{label}"].nunique() == 2 for label in labels
    )
    stats = {
        "inputs": {"base": args.base_csv, **{label: spec.partition("=")[2] for label, spec in zip(replays, args.replay)}},
        "cases": int(len(merged)),
        "failures": failure_analysis(merged, labels, args.base_label, args),
        "collision": collision_analysis(complete, merged, labels, args.base_label),
        "ttc": ttc,
        "severity": severity_analysis(complete, labels) if has_ttc else None,
        "proximity_4m": proximity_analysis(complete, labels),
        "regions": region_analysis(complete, labels) if has_ttc else None,
        "boundary": boundary_analysis(complete, labels) if can_fit_boundary else None,
        "theory_zones": zone_analysis(complete, labels) if has_ttc else None,
        "kinematics": kinematics_analysis(complete, labels) if args.kinematics_csv else None,
        "workers": {
            f"{left}->{right}": worker_analysis(complete, left, right)
            for left, right in itertools.combinations(replay_labels, 2)
        },
        "noise_baseline": noise_baseline(args, complete, labels, args.base_label),
    }

    merged.reset_index().to_csv(output_dir / "autoware_versions_paired_data.csv", index=False)
    (output_dir / "autoware_versions_stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False, default=float), encoding="utf-8"
    )
    plot_flips(complete, labels, output_dir / "collision_flips.png")
    plot_failures(merged, labels, args.base_label, output_dir / "replay_failures.png")
    plot_ttc(ttc, labels, output_dir / "ttc_rate_difference.png")
    if has_ttc:
        plot_severity(stats["severity"], labels, output_dir / "severity_transitions.png")
        for left, right in itertools.combinations(replay_labels, 2):
            plot_region(complete, left, right, "c_ttc_1.5", output_dir / f"region_c_ttc_1.5_{left}_{right}.png")
    if stats["kinematics"] is not None:
        plot_kinematics(complete, labels, output_dir / "kinematics_distributions.png")
    print(f"cases={len(merged)} complete={len(complete)} output={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
