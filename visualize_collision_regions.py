#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import importlib
import os
import sys
from typing import Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from estimator import SafetyEstimator

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None


def resolve_dataset_path(target_path: str, scenario_type: str) -> Tuple[str, str]:
    target_path = os.path.expanduser(target_path)
    if target_path.endswith(".csv"):
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"CSV not found: {target_path}")
        return target_path, os.path.dirname(target_path)

    csv_file_fixed = os.path.join(target_path, f"{scenario_type}_dataset_fixed.csv")
    csv_file_normal = os.path.join(target_path, f"{scenario_type}_dataset.csv")

    if os.path.exists(csv_file_fixed):
        return csv_file_fixed, target_path
    if os.path.exists(csv_file_normal):
        return csv_file_normal, target_path
    raise FileNotFoundError(
        f"Dataset not found: {csv_file_normal} (or _fixed.csv)"
    )


def load_clean_dataset(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file, engine="python", on_bad_lines="skip")
    required = ["dx0", "npc_speed", "ego_speed", "c_collision"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    df = df[df["c_collision"].isin([0, 1])].copy()
    return df


def build_voxel_masks(
    df: pd.DataFrame,
    x_edges: np.ndarray,
    y_edges: np.ndarray,
    z_edges: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    shape = (len(x_edges) - 1, len(y_edges) - 1, len(z_edges) - 1)
    sampled = np.zeros(shape, dtype=bool)
    collision = np.zeros(shape, dtype=bool)

    x_idx = np.clip(np.digitize(df["dx0"], x_edges) - 1, 0, shape[0] - 1)
    y_idx = np.clip(np.digitize(df["npc_speed"], y_edges) - 1, 0, shape[1] - 1)
    z_idx = np.clip(np.digitize(df["ego_speed"], z_edges) - 1, 0, shape[2] - 1)

    for xi, yi, zi, ccol in zip(x_idx, y_idx, z_idx, df["c_collision"]):
        sampled[xi, yi, zi] = True
        if int(ccol) == 1:
            collision[xi, yi, zi] = True

    safe_only = sampled & ~collision
    return collision, safe_only


def plot_jama_surfaces(ax, cfg, x_bounds, y_bounds, z_bounds) -> None:
    if TheoreticalSafetyCalculator is None:
        return

    calc = TheoreticalSafetyCalculator(cfg)
    ego_grid = np.linspace(z_bounds[0], z_bounds[1], 28)
    npc_grid = np.linspace(y_bounds[0], y_bounds[1], 28)
    y_npc, z_ego = np.meshgrid(npc_grid, ego_grid)
    x_human = np.zeros_like(y_npc)
    x_ai = np.zeros_like(y_npc)

    for i in range(z_ego.shape[0]):
        for j in range(z_ego.shape[1]):
            res = calc.evaluate(0.0, z_ego[i, j], y_npc[i, j])
            x_human[i, j] = res["theory_d_total_human"]
            x_ai[i, j] = res["theory_d_total_ai"]

    x_human = np.clip(x_human, x_bounds[0], x_bounds[1])
    x_ai = np.clip(x_ai, x_bounds[0], x_bounds[1])

    ax.plot_surface(
        x_human, y_npc, z_ego,
        color="#f6c27a", alpha=0.18, linewidth=0, shade=False
    )
    ax.plot_surface(
        x_ai, y_npc, z_ego,
        color="#f3a0a0", alpha=0.18, linewidth=0, shade=False
    )


def style_axes(ax, title: str, x_bounds, y_bounds, z_bounds) -> None:
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel("dx0 (Initial Distance [m])")
    ax.set_ylabel("npc_speed (NPC Speed [km/h])")
    ax.set_zlabel("ego_speed (Ego Speed [km/h])")
    ax.set_xlim(*x_bounds)
    ax.set_ylim(*y_bounds)
    ax.set_zlim(*z_bounds)
    ax.view_init(elev=26, azim=-60)


def add_legend(ax, include_ai: bool) -> None:
    handles = [
        Patch(facecolor=(0.90, 0.23, 0.18, 0.70), edgecolor="none",
              label="Collision area (red point exists)"),
        Patch(facecolor=(0.20, 0.65, 0.38, 0.10), edgecolor="none",
              label="Sampled non-collision area"),
        Patch(facecolor=(0.96, 0.76, 0.48, 0.30), edgecolor="none",
              label="Theory Zone B boundary (Human limit)"),
        Patch(facecolor=(0.95, 0.63, 0.63, 0.30), edgecolor="none",
              label="Theory Zone C boundary (AI limit)"),
    ]
    if include_ai:
        handles.insert(
            1,
            Patch(facecolor=(0.18, 0.50, 0.95, 0.14), edgecolor="none",
                  label="AI-predicted safe area"),
        )
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))


def plot_empirical_regions(
    output_path: str,
    collision_mask: np.ndarray,
    safe_mask: np.ndarray,
    x_edges: np.ndarray,
    y_edges: np.ndarray,
    z_edges: np.ndarray,
    cfg,
) -> None:
    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    ax.voxels(
        x_edges[:, None, None],
        y_edges[None, :, None],
        z_edges[None, None, :],
        safe_mask,
        facecolors=(0.20, 0.65, 0.38, 0.08),
        edgecolor=(0.20, 0.65, 0.38, 0.04),
        linewidth=0.2,
    )
    ax.voxels(
        x_edges[:, None, None],
        y_edges[None, :, None],
        z_edges[None, None, :],
        collision_mask,
        facecolors=(0.90, 0.23, 0.18, 0.70),
        edgecolor=(0.65, 0.12, 0.10, 0.35),
        linewidth=0.35,
    )

    x_bounds = (float(x_edges[0]), float(x_edges[-1]))
    y_bounds = (float(y_edges[0]), float(y_edges[-1]))
    z_bounds = (float(z_edges[0]), float(z_edges[-1]))
    plot_jama_surfaces(ax, cfg, x_bounds, y_bounds, z_bounds)
    style_axes(ax, "Empirical Collision / Non-Collision Regions", x_bounds, y_bounds, z_bounds)
    add_legend(ax, include_ai=False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def plot_ai_regions(
    output_path: str,
    mean_grid: np.ndarray,
    sampled_safe_mask: np.ndarray,
    x_edges: np.ndarray,
    y_edges: np.ndarray,
    z_edges: np.ndarray,
    cfg,
    unsafe_threshold: float,
) -> None:
    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    ai_safe = mean_grid < unsafe_threshold
    ai_unsafe = ~ai_safe

    ax.voxels(
        x_edges[:, None, None],
        y_edges[None, :, None],
        z_edges[None, None, :],
        ai_safe,
        facecolors=(0.18, 0.50, 0.95, 0.06),
        edgecolor=(0.18, 0.50, 0.95, 0.03),
        linewidth=0.15,
    )
    ax.voxels(
        x_edges[:, None, None],
        y_edges[None, :, None],
        z_edges[None, None, :],
        ai_unsafe,
        facecolors=(0.90, 0.23, 0.18, 0.22),
        edgecolor=(0.65, 0.12, 0.10, 0.10),
        linewidth=0.15,
    )
    ax.voxels(
        x_edges[:, None, None],
        y_edges[None, :, None],
        z_edges[None, None, :],
        sampled_safe_mask,
        facecolors=(0.20, 0.65, 0.38, 0.10),
        edgecolor=(0.20, 0.65, 0.38, 0.05),
        linewidth=0.15,
    )

    x_bounds = (float(x_edges[0]), float(x_edges[-1]))
    y_bounds = (float(y_edges[0]), float(y_edges[-1]))
    z_bounds = (float(z_edges[0]), float(z_edges[-1]))
    plot_jama_surfaces(ax, cfg, x_bounds, y_bounds, z_bounds)
    style_axes(
        ax,
        f"AI Learned Safe / Unsafe Regions (unsafe >= {unsafe_threshold:.2f})",
        x_bounds, y_bounds, z_bounds
    )
    add_legend(ax, include_ai=True)

    plt.tight_layout()
    plt.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def predict_ai_grid(
    scenario_type: str,
    cfg,
    traces_dir: str,
    x_centers: np.ndarray,
    y_centers: np.ndarray,
    z_centers: np.ndarray,
) -> np.ndarray:
    estimator = SafetyEstimator(scenario_type, cfg, traces_dir=traces_dir)
    if not estimator.train("c_collision"):
        raise RuntimeError("Failed to train GP model on c_collision")

    xg, yg, zg = np.meshgrid(x_centers, y_centers, z_centers, indexing="ij")
    query = np.column_stack([xg.ravel(), zg.ravel(), yg.ravel()])
    # feature order follows PARAM_RANGES: dx0, ego_speed, npc_speed
    mean, _ = estimator.predict_uncertainty(query)
    if mean is None:
        raise RuntimeError("GP prediction failed")
    return mean.reshape((len(x_centers), len(y_centers), len(z_centers)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Visualize empirical collision regions and AI safe regions as 3D voxel maps."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces",
                        help="Dataset directory or CSV file")
    parser.add_argument("--type", default="uturn", help="Scenario type")
    parser.add_argument("--bins", type=int, default=12,
                        help="Number of bins per axis for voxelization")
    parser.add_argument("--unsafe-threshold", type=float, default=0.5,
                        help="GP mean threshold to regard a cell as AI-unsafe")
    parser.add_argument("--output-prefix", default="collision_region_map",
                        help="Prefix for generated image files")
    args = parser.parse_args()

    cfg = importlib.import_module(f"configs.{args.type}")
    csv_file, target_dir = resolve_dataset_path(args.target, args.type)
    df = load_clean_dataset(csv_file)

    bounds = cfg.PARAM_RANGES
    x_edges = np.linspace(bounds["dx0"][0], bounds["dx0"][1], args.bins + 1)
    y_edges = np.linspace(bounds["npc_speed"][0], bounds["npc_speed"][1], args.bins + 1)
    z_edges = np.linspace(bounds["ego_speed"][0], bounds["ego_speed"][1], args.bins + 1)
    collision_mask, safe_mask = build_voxel_masks(df, x_edges, y_edges, z_edges)

    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])
    z_centers = 0.5 * (z_edges[:-1] + z_edges[1:])
    mean_grid = predict_ai_grid(args.type, cfg, target_dir, x_centers, y_centers, z_centers)

    empirical_path = os.path.join(target_dir, f"{args.output_prefix}_empirical.png")
    ai_path = os.path.join(target_dir, f"{args.output_prefix}_ai.png")
    plot_empirical_regions(empirical_path, collision_mask, safe_mask, x_edges, y_edges, z_edges, cfg)
    plot_ai_regions(ai_path, mean_grid, safe_mask, x_edges, y_edges, z_edges, cfg, args.unsafe_threshold)

    print(f"[Success] Empirical region map saved to: {empirical_path}")
    print(f"[Success] AI region map saved to       : {ai_path}")
    print(f"[Info] Valid samples   : {len(df)}")
    print(f"[Info] Collision cells : {int(collision_mask.sum())}")
    print(f"[Info] Safe-only cells : {int(safe_mask.sum())}")


if __name__ == "__main__":
    main()
