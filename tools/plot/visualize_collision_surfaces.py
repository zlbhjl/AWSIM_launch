from __future__ import annotations

import argparse
import importlib

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from scipy.ndimage import gaussian_filter

from tools.plot.common import (
    load_dataset,
    resolve_dataset_path,
    resolve_output_path,
)

from estimator import SafetyEstimator

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None


def load_clean_dataset(csv_file: str) -> pd.DataFrame:
    df = load_dataset(csv_file)
    required = ["dx0", "npc_speed", "ego_speed", "c_collision"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    df = df[df["c_collision"].isin([0, 1])].copy()
    return df


def weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    if len(values) == 0:
        return np.nan
    sorter = np.argsort(values)
    values = values[sorter]
    weights = weights[sorter]
    cumulative = np.cumsum(weights)
    if cumulative[-1] == 0:
        return np.nan
    normalized = cumulative / cumulative[-1]
    return float(np.interp(quantile, normalized, values))


def nearest_weighted_frontier(
    subset: pd.DataFrame,
    npc_val: float,
    ego_val: float,
    quantile: float,
    k_neighbors: int,
    sigma: float,
) -> tuple[float, int, float]:
    if subset.empty:
        return np.nan, 0, np.inf
    dy = subset["npc_speed"].to_numpy() - npc_val
    dz = subset["ego_speed"].to_numpy() - ego_val
    dist = np.sqrt(dy * dy + dz * dz)
    order = np.argsort(dist)[: min(k_neighbors, len(dist))]
    local_dist = dist[order]
    local_dx0 = subset["dx0"].to_numpy()[order]
    weights = np.exp(-(local_dist**2) / (2 * sigma * sigma))
    return (
        weighted_quantile(local_dx0, weights, quantile),
        len(local_dx0),
        float(local_dist[0]) if len(local_dist) > 0 else np.inf,
    )


def fill_nan_with_neighbors(surface: np.ndarray, iterations: int = 8) -> np.ndarray:
    filled = surface.copy()
    for _ in range(iterations):
        mask = np.isnan(filled)
        if not mask.any():
            break
        work = filled.copy()
        neighbor_sum = np.zeros_like(work, dtype=float)
        neighbor_count = np.zeros_like(work, dtype=float)
        for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            shifted = np.roll(np.roll(work, di, axis=0), dj, axis=1)
            valid = ~np.isnan(shifted)
            neighbor_sum[mask & valid] += shifted[mask & valid]
            neighbor_count[mask & valid] += 1
        update_mask = mask & (neighbor_count > 0)
        filled[update_mask] = neighbor_sum[update_mask] / neighbor_count[update_mask]
    return filled


def smooth_surface(
    surface: np.ndarray,
    sigma: float,
    preserve_nan_mask: bool = False,
) -> np.ndarray:
    original_nan_mask = np.isnan(surface)
    base = surface.copy() if preserve_nan_mask else fill_nan_with_neighbors(surface)
    valid = ~np.isnan(base)
    if not valid.any():
        return base
    values = np.where(valid, base, 0.0)
    weights = valid.astype(float)
    smooth_values = gaussian_filter(values, sigma=sigma, mode="nearest")
    smooth_weights = gaussian_filter(weights, sigma=sigma, mode="nearest")
    result = np.where(smooth_weights > 1e-6, smooth_values / smooth_weights, np.nan)
    if preserve_nan_mask:
        result[original_nan_mask] = np.nan
    return result


def build_empirical_surface(
    df: pd.DataFrame,
    npc_grid: np.ndarray,
    ego_grid: np.ndarray,
    k_neighbors: int,
    sigma: float,
    min_support_points: int,
    support_radius: float,
) -> np.ndarray:
    collision_df = df[df["c_collision"] == 1]

    surface = np.full((len(ego_grid), len(npc_grid)), np.nan)
    for i, ego_val in enumerate(ego_grid):
        for j, npc_val in enumerate(npc_grid):
            collision_edge, support_count, nearest_dist = nearest_weighted_frontier(
                collision_df,
                npc_val,
                ego_val,
                0.95,
                k_neighbors,
                sigma,
            )
            _ = support_count, nearest_dist, min_support_points, support_radius
            surface[i, j] = collision_edge
    return smooth_surface(surface, sigma=1.0, preserve_nan_mask=False)


def build_ai_surface(
    scenario_type: str,
    cfg,
    traces_dir: str,
    dx0_bounds: tuple[float, float],
    npc_grid: np.ndarray,
    ego_grid: np.ndarray,
    unsafe_threshold: float,
) -> np.ndarray:
    estimator = SafetyEstimator(scenario_type, cfg, traces_dir=traces_dir)
    if not estimator.train("c_collision"):
        raise RuntimeError("Failed to train GP model on c_collision")

    dx0_line = np.linspace(dx0_bounds[0], dx0_bounds[1], 120)
    surface = np.full((len(ego_grid), len(npc_grid)), np.nan)

    for i, ego_val in enumerate(ego_grid):
        query = np.column_stack(
            [
                dx0_line,
                np.full_like(dx0_line, ego_val),
                np.zeros_like(dx0_line),
            ]
        )
        for j, npc_val in enumerate(npc_grid):
            query[:, 2] = npc_val
            mean, _ = estimator.predict_uncertainty(query)
            if mean is None:
                continue
            idx = int(np.argmin(np.abs(mean - unsafe_threshold)))
            surface[i, j] = dx0_line[idx]
    return smooth_surface(surface, sigma=1.0)


def plot_jama_surfaces(ax, cfg, dx0_bounds, npc_grid, ego_grid) -> None:
    if TheoreticalSafetyCalculator is None:
        return
    calc = TheoreticalSafetyCalculator(cfg)
    y_npc, z_ego = np.meshgrid(npc_grid, ego_grid)
    x_human = np.zeros_like(y_npc)
    x_ai = np.zeros_like(y_npc)
    for i in range(z_ego.shape[0]):
        for j in range(z_ego.shape[1]):
            res = calc.evaluate(0.0, z_ego[i, j], y_npc[i, j])
            x_human[i, j] = res["theory_d_total_human"]
            x_ai[i, j] = res["theory_d_total_ai"]

    x_human = np.clip(x_human, dx0_bounds[0], dx0_bounds[1])
    x_ai = np.clip(x_ai, dx0_bounds[0], dx0_bounds[1])

    ax.plot_surface(
        x_human,
        y_npc,
        z_ego,
        color="#f6c27a",
        alpha=0.14,
        linewidth=0,
        shade=False,
    )
    ax.plot_surface(
        x_ai,
        y_npc,
        z_ego,
        color="#f2a2a2",
        alpha=0.05,
        linewidth=0,
        shade=False,
    )


def style_axes(ax, title: str, dx0_bounds, npc_bounds, ego_bounds) -> None:
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel("dx0 (Initial Distance [m])")
    ax.set_ylabel("npc_speed (NPC Speed [km/h])")
    ax.set_zlabel("ego_speed (Ego Speed [km/h])")
    ax.set_xlim(*dx0_bounds)
    ax.set_ylim(*npc_bounds)
    ax.set_zlim(*ego_bounds)
    ax.view_init(elev=24, azim=-63)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize smooth empirical / AI collision boundaries as 3D surfaces."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="~/simulation_traces",
        help="Dataset directory or CSV file",
    )
    parser.add_argument("--type", default="uturn", help="Scenario type")
    parser.add_argument(
        "--grid",
        type=int,
        default=28,
        help="Grid resolution on speed axes",
    )
    parser.add_argument(
        "--neighbors",
        type=int,
        default=90,
        help="Neighbor count for empirical smoothing",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=1.8,
        help="Neighborhood width on speed plane",
    )
    parser.add_argument(
        "--min-support-points",
        type=int,
        default=8,
        help="Minimum nearby collision points required to draw the empirical surface",
    )
    parser.add_argument(
        "--support-radius",
        type=float,
        default=1.6,
        help="Maximum speed-plane distance (km/h units) to nearest collision point for empirical surface",
    )
    parser.add_argument(
        "--unsafe-threshold",
        type=float,
        default=0.5,
        help="GP threshold for AI unsafe region",
    )
    parser.add_argument(
        "--output",
        default="collision_boundary_surfaces.png",
        help="Output image filename",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    cfg = importlib.import_module(f"configs.{args.type}")
    csv_file, target_dir = resolve_dataset_path(args.target, args.type)
    df = load_clean_dataset(csv_file)

    dx0_bounds = cfg.PARAM_RANGES["dx0"]
    ego_bounds = cfg.PARAM_RANGES["ego_speed"]
    npc_bounds = cfg.PARAM_RANGES["npc_speed"]

    npc_grid = np.linspace(npc_bounds[0], npc_bounds[1], args.grid)
    ego_grid = np.linspace(ego_bounds[0], ego_bounds[1], args.grid)
    y_npc, z_ego = np.meshgrid(npc_grid, ego_grid)

    empirical_surface = build_empirical_surface(
        df,
        npc_grid,
        ego_grid,
        args.neighbors,
        args.sigma,
        args.min_support_points,
        args.support_radius,
    )
    ai_surface = build_ai_surface(
        args.type,
        cfg,
        target_dir,
        dx0_bounds,
        npc_grid,
        ego_grid,
        args.unsafe_threshold,
    )

    fig = plt.figure(figsize=(18, 9))
    ax_emp = fig.add_subplot(121, projection="3d")
    ax_ai = fig.add_subplot(122, projection="3d")

    ax_emp.plot_surface(
        empirical_surface,
        y_npc,
        z_ego,
        color="#e74c3c",
        alpha=0.50,
        linewidth=0,
        shade=True,
    )
    ax_emp.contourf(
        empirical_surface,
        y_npc,
        z_ego,
        zdir="x",
        offset=dx0_bounds[0],
        levels=20,
        cmap="Reds",
        alpha=0.28,
    )
    style_axes(
        ax_emp,
        "Empirical Collision Frontier\nUnsafe area is left of the red surface",
        dx0_bounds,
        npc_bounds,
        ego_bounds,
    )
    plot_jama_surfaces(ax_emp, cfg, dx0_bounds, npc_grid, ego_grid)

    ax_ai.plot_surface(
        ai_surface,
        y_npc,
        z_ego,
        color="#3498db",
        alpha=0.42,
        linewidth=0,
        shade=True,
    )
    ax_ai.contourf(
        ai_surface,
        y_npc,
        z_ego,
        zdir="x",
        offset=dx0_bounds[0],
        levels=20,
        cmap="Blues",
        alpha=0.24,
    )
    style_axes(
        ax_ai,
        (
            f"AI Learned Safety Frontier (mean={args.unsafe_threshold:.2f})\n"
            "Unsafe area is left of the blue surface"
        ),
        dx0_bounds,
        npc_bounds,
        ego_bounds,
    )
    plot_jama_surfaces(ax_ai, cfg, dx0_bounds, npc_grid, ego_grid)

    legend_items = [
        Patch(
            facecolor="#e74c3c",
            edgecolor="none",
            alpha=0.68,
            label="Empirical collision frontier",
        ),
        Patch(
            facecolor="#3498db",
            edgecolor="none",
            alpha=0.56,
            label="AI learned frontier",
        ),
        Patch(
            facecolor="#f6c27a",
            edgecolor="none",
            alpha=0.30,
            label="JAMA human boundary",
        ),
        Patch(
            facecolor="#f2a2a2",
            edgecolor="none",
            alpha=0.30,
            label="JAMA AI boundary",
        ),
    ]
    fig.legend(handles=legend_items, loc="upper center", ncol=4, frameon=True)

    plt.tight_layout(rect=(0, 0, 1, 0.93))
    output_path = resolve_output_path(target_dir, args.output)
    plt.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)

    print(f"[Success] Saved smooth boundary visualization to: {output_path}")
    print(f"[Info] Valid samples: {len(df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
