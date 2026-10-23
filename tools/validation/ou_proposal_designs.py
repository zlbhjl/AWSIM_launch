"""Generate the target plans and the pilot candidates Q1-Q5 for models A and B.

docs/sde_importance_sampling_plan.md §12-13 compares these proposal groups:

    Q0  ordinary MC (Q = P)
    Q1  time-dependent Gaussian mean (the existing gaussian_drift with a schedule)
    Q2  Q1 with an inflated covariance
    Q3  state-dependent mean, unit covariance
    Q4  Student-t (dof 5) with the Q3 rule, covariance matched to 1
    Q5  a two-component path-level mixture
    Q1d Q1 inside a defensive mixture with beta = 0.2 (weight bound 5)

The shapes come from exponential tilting of the linear Gaussian model. The
terminal value is X_K = mean + sum_k g_k xi_k with sensitivities g_k, so the
tilt that moves E[X_K] to the threshold c shifts innovation k by
m_k = (c - mean) g_k / Var(X_K). Q1 uses a 4-piece time average of m_k; Q3
uses the state-feedback form m(z) = g (c - E[X_K | z]) / Var(X_K | z),
affine in the states within each time window (for two-sided events, the
window's tilt away from 0 on the current side). Q1, Q2 and Q1d push one way even for two-sided events;
the two-sided mixture is Q5. Pilot trials only
scale these shapes; nothing is fitted to the final runs.

    python3 -m tools.validation.ou_proposal_designs --output-dir configs/is_tuning
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import yaml

from targets.dynamics.models.exact import ou_augmented_terminal_moments, ou_euler_terminal_variance

DT = 0.01
HORIZON = 1.0
STEPS = 100
PIECES = 4
FAMILIES = ("q1", "q2", "q3", "q4", "q5", "q1d")


def studies() -> dict[str, dict[str, object]]:
    """Target settings of every (model, event) study."""
    sd_a = math.sqrt(ou_euler_terminal_variance(theta=1.0, sigma=1.0, h=DT, steps=STEPS))
    _, cov_b = ou_augmented_terminal_moments(theta_c=2.0, theta_x=1.0, coupling=1.0, sigma=1.0, h=DT, steps=STEPS)
    sd_b = math.sqrt(cov_b[1, 1])
    result = {}
    for k in (3, 4):
        for side in ("upper", "two_sided"):
            result[f"ou_a_{k}sd_{side}"] = {"model": "A", "sd": sd_a, "k": k, "side": side}
    result["ou_b_3sd_upper"] = {"model": "B", "sd": sd_b, "k": 3, "side": "upper"}
    return result


def linear_model(model: str) -> tuple[np.ndarray, np.ndarray, int, list[str]]:
    """Euler transition matrix, noise loading, index of the judged state, state names."""
    if model == "A":
        return np.array([[1.0 - DT]]), np.array([math.sqrt(DT)]), 0, ["x"]
    return (np.array([[1.0 - 2.0 * DT, 0.0], [DT, 1.0 - DT]]), np.array([math.sqrt(DT), 0.0]), 1, ["c", "x"])


def sensitivities(model: str) -> np.ndarray:
    """g_k = dX_K / d xi_k for k = 0..K-1."""
    transition, loading, judged, _ = linear_model(model)
    return np.asarray([
        (np.linalg.matrix_power(transition, STEPS - 1 - k) @ loading)[judged] for k in range(STEPS)
    ])


def target_plan(name: str, study: dict[str, object]) -> dict[str, object]:
    threshold = float(study["k"]) * float(study["sd"])
    if study["model"] == "A":
        definition = {"dynamics_schema_version": 2, "model_id": "ou_scalar",
                      "model_params": {"theta": 1.0, "sigma": 1.0},
                      "initial_law": {"kind": "point_mass", "value": [0.0]}}
        event = {"kind": "terminal_exceedance", "threshold": threshold, "side": study["side"]}
    else:
        definition = {"dynamics_schema_version": 2, "model_id": "ou_augmented",
                      "model_params": {"theta_c": 2.0, "theta_x": 1.0, "coupling": 1.0, "sigma": 1.0},
                      "initial_law": {"kind": "point_mass", "value": [0.0, 0.0]}}
        event = {"kind": "terminal_exceedance", "threshold": threshold, "side": study["side"], "state": "x"}
    definition["solver"] = {"kind": "sde", "dt_sec": DT, "horizon_sec": HORIZON}
    definition["event"] = event
    return {
        "schema_version": 1,
        "experiment_id": name,
        "target": "dynamics",
        "target_measure": {"id": f"{name}_p", "definition": definition},
        "proposal": {"id": "q0_p", "mode": "manual_proposal",
                     "definition": {"dynamics_schema_version": 2, "input": {"kind": "same_as_target"}}},
    }


def _piecewise_means(g: np.ndarray, threshold: float, variance: float, scale: float, sign: float = 1.0):
    tilt = sign * scale * threshold * g / variance
    edges = np.linspace(0, STEPS, PIECES + 1).astype(int)
    return [
        (round(float(edges[i]) * DT, 10), None if i == PIECES - 1 else round(float(edges[i + 1]) * DT, 10),
         float(np.mean(tilt[edges[i]:edges[i + 1]])))
        for i in range(PIECES)
    ]


def _q1(g, threshold, variance, scale, sign=1.0):
    """The existing gaussian_drift with a time schedule (drift units: u = m / sqrt(dt))."""
    return {"noise_path_q": {"distribution": "gaussian_drift", "drift_schedule": [
        {"time": [start, end], "channel_drifts": {"w": mean / math.sqrt(DT)}}
        for start, end, mean in _piecewise_means(g, threshold, variance, scale, sign)
    ]}}


def _gaussian_piecewise(g, threshold, variance, scale, sign=1.0, covariance=None):
    law = {"kind": "gaussian", "mean_rule": {"kind": "piecewise", "pieces": [
        {"time": [start, end], "values": {"w": mean}}
        for start, end, mean in _piecewise_means(g, threshold, variance, scale, sign)
    ]}}
    if covariance is not None:
        law["covariance"] = {"channels": ["w"], "matrix": [[covariance]]}
    return law


def _state_rule(study, g, threshold, variance, scale):
    """Q3: state feedback m_k(z) = g_k (c - E[X_K | z_k]) / Var(X_K | z_k), per time window.

    E[X_K | z_k] = e^T A^(K-k) z_k is linear in the state, so within each of
    the time windows the rule is affine in the states, with coefficients
    averaged over the window with weights g_k^2. For two-sided events the
    window's Q1 tilt is applied away from 0 on the current side.
    """
    transition, _, judged, names = linear_model(str(study["model"]))
    edges = np.linspace(0, STEPS, PIECES + 1).astype(int)
    if study["side"] != "upper":
        pieces = []
        for start, end, mean in _piecewise_means(g, threshold, variance, scale):
            pieces.append({"time": [start, end], "rule": {
                "kind": "state_threshold", "state": names[judged], "threshold": 0.0,
                "below": {"w": -mean}, "above": {"w": mean}}})
        return {"kind": "piecewise", "pieces": pieces}
    offsets, rows = [], []
    for k in range(STEPS):
        remaining_variance = float(np.sum(g[k:] ** 2))
        gain = np.linalg.matrix_power(transition, STEPS - k)[judged]
        if remaining_variance == 0.0:
            # Noise from here on no longer reaches X_K (model B's last step).
            offsets.append(0.0)
            rows.append(np.zeros_like(gain))
            continue
        offsets.append(g[k] * threshold / remaining_variance)
        rows.append(-g[k] * gain / remaining_variance)
    offsets_array, rows_array = np.asarray(offsets), np.asarray(rows)
    pieces = []
    for i in range(PIECES):
        window = slice(edges[i], edges[i + 1])
        weights = g[window] ** 2
        weights = weights / np.sum(weights)
        pieces.append({
            "time": [round(float(edges[i]) * DT, 10), None if i == PIECES - 1 else round(float(edges[i + 1]) * DT, 10)],
            "rule": {"kind": "affine_state", "channels": ["w"], "states": names,
                     "offset": [float(np.sum(weights * offsets_array[window])) * scale],
                     "matrix": [(np.sum(weights[:, None] * rows_array[window], axis=0) * scale).tolist()]},
        })
    return {"kind": "piecewise", "pieces": pieces}


def candidates(study: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Pilot trials per proposal group; at most 10 per group (plan §13 budget)."""
    g = sensitivities(str(study["model"]))
    threshold = float(study["k"]) * float(study["sd"])
    variance = float(study["sd"]) ** 2
    two_sided = study["side"] == "two_sided"
    groups: dict[str, list[dict[str, object]]] = {family: [] for family in FAMILIES}
    for scale in (0.8, 1.0, 1.2):
        # Q1 pushes one way even for two-sided events: it is the existing
        # one-direction schedule; the two-sided remedy is the mixture Q5.
        groups["q1"].append({"name": f"q1_s{scale}", **_q1(g, threshold, variance, scale)})
        groups["q3"].append({"name": f"q3_s{scale}", "noise": {
            "kind": "gaussian", "mean_rule": _state_rule(study, g, threshold, variance, scale)}})
    for inflation in (1.2, 1.5):
        groups["q2"].append({"name": f"q2_var{inflation}", "noise": _gaussian_piecewise(
            g, threshold, variance, 1.0, covariance=inflation)})
    for scale in (0.8, 1.0):
        rule = _state_rule(study, g, threshold, variance, scale)
        groups["q4"].append({"name": f"q4_t5_s{scale}", "noise": {
            "kind": "student_t", "dof": 5, "location_rule": rule,
            "covariance": {"channels": ["w"], "matrix": [[1.0]]}}})
    if two_sided:
        for scale in (0.8, 1.0, 1.2):
            groups["q5"].append({"name": f"q5_pm_s{scale}", "noise": {"kind": "mixture", "components": [
                {"weight": 0.5, "law": _gaussian_piecewise(g, threshold, variance, scale, +1.0)},
                {"weight": 0.5, "law": _gaussian_piecewise(g, threshold, variance, scale, -1.0)}]}})
    else:
        for low, high in ((0.8, 1.2), (0.9, 1.3)):
            groups["q5"].append({"name": f"q5_s{low}_{high}", "noise": {"kind": "mixture", "components": [
                {"weight": 0.5, "law": _gaussian_piecewise(g, threshold, variance, low)},
                {"weight": 0.5, "law": _gaussian_piecewise(g, threshold, variance, high)}]}})
    components = [{"weight": 1.0, "law": _gaussian_piecewise(g, threshold, variance, 1.0)}]
    groups["q1d"].append({"name": "q1d_b0.2", "noise": {"kind": "mixture", "defensive_weight": 0.2,
                                                        "components": components}})
    return groups


def write(output_dir: Path) -> list[Path]:
    written = []
    for name, study in studies().items():
        folder = output_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        base = folder / "base.yaml"
        base.write_text(yaml.safe_dump(target_plan(name, study), sort_keys=False), encoding="utf-8")
        written.append(base)
        for family, trials in candidates(study).items():
            path = folder / f"{family}.yaml"
            path.write_text(yaml.safe_dump({"budget": 10, "trials": trials}, sort_keys=False), encoding="utf-8")
            written.append(path)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", type=Path, default=Path("configs/is_tuning"))
    args = parser.parse_args(argv)
    for path in write(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
