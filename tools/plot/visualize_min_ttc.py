from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tools.plot.common import (
    apply_slice_filters,
    format_slice_suffix,
    load_dataset,
    parse_slice_filters,
    resolve_dataset_path,
    resolve_output_path,
)

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot minimum TTC severity levels in 3D."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces")
    parser.add_argument("--type", default="uturn")
    parser.add_argument("--output", default="min_ttc_levels_3d.png")
    parser.add_argument(
        "--slice",
        action="append",
        default=[],
        help="Optional slice filter in key=value form for extra scenario axes.",
    )
    return parser


def run_plot(
    target: str,
    scenario_type: str,
    output: str,
    *,
    slice_filters: dict[str, float] | None = None,
) -> dict[str, object]:
    csv_file, target_dir = resolve_dataset_path(target, scenario_type)
    print(f"[{csv_file}] を読み込み中...")
    valid_df = load_dataset(csv_file).copy()
    valid_df, applied_filters = apply_slice_filters(valid_df, slice_filters)

    if "min_ttc" not in valid_df.columns:
        raise ValueError("データセットに 'min_ttc' 列がありません。")

    plot_cols = ["dx0", "npc_speed", "ego_speed", "min_ttc"]
    if "c_collision" in valid_df.columns:
        plot_cols.append("c_collision")

    for col in plot_cols:
        if col in valid_df.columns:
            valid_df[col] = pd.to_numeric(valid_df[col], errors="coerce")

    valid_df = valid_df.dropna(subset=plot_cols)
    valid_df = valid_df[valid_df["min_ttc"] >= 0]
    if "c_collision" in valid_df.columns:
        valid_df = valid_df[valid_df["c_collision"].isin([0, 1])]

    def get_ttc_severity(row):
        ttc = row["min_ttc"]
        if row.get("c_collision", 0) == 1:
            return 5
        if ttc <= 0.3:
            return 4
        if ttc <= 0.5:
            return 3
        if ttc <= 0.9:
            return 2
        if ttc <= 1.1:
            return 1
        return 0

    valid_df["severity"] = valid_df.apply(get_ttc_severity, axis=1)

    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    color_map = {0: "#2ecc71", 1: "#3498db", 2: "#9b59b6", 3: "#f39c12", 4: "#e67e22", 5: "#e74c3c"}
    label_map = {
        0: "Level 0: Safe (TTC > 1.1s)",
        1: "Level 1: Warning (TTC <= 1.1s)",
        2: "Level 2: Danger (TTC <= 0.9s)",
        3: "Level 3: Extreme Near Miss (TTC <= 0.5s)",
        4: "Level 4: Fatal Near Miss (TTC <= 0.3s)",
        5: "Level 5: Collision (TTC = 0.0s)",
    }

    for severity in sorted(valid_df["severity"].unique()):
        subset = valid_df[valid_df["severity"] == severity]
        ax.scatter(
            subset["dx0"], subset["npc_speed"], subset["ego_speed"],
            c=color_map[severity], label=label_map[severity],
            alpha=0.9 if severity > 0 else 0.15, s=60 if severity > 0 else 15
        )

    if scenario_type == "uturn" and TheoreticalSafetyCalculator is not None and not valid_df.empty:
        calc = TheoreticalSafetyCalculator()
        jama_color_map = {"B": "#f39c12", "C": "#e74c3c"}

        ego_min, ego_max = valid_df["ego_speed"].min(), valid_df["ego_speed"].max()
        npc_min, npc_max = valid_df["npc_speed"].min(), valid_df["npc_speed"].max()
        if ego_min == ego_max:
            ego_max = ego_min + 10
        if npc_min == npc_max:
            npc_max = npc_min + 10

        ego_grid = np.linspace(ego_min, ego_max, 30)
        npc_grid = np.linspace(npc_min, npc_max, 30)
        y_npc, z_ego = np.meshgrid(npc_grid, ego_grid)
        x_dx0_human = np.zeros_like(z_ego)
        x_dx0_ai = np.zeros_like(z_ego)

        for i in range(z_ego.shape[0]):
            for j in range(z_ego.shape[1]):
                res = calc.evaluate(0.0, z_ego[i, j], y_npc[i, j])
                x_dx0_human[i, j] = res["theory_d_total_human"]
                x_dx0_ai[i, j] = res["theory_d_total_ai"]

        ax.plot_surface(x_dx0_ai, y_npc, z_ego, color=jama_color_map["C"], alpha=0.15, shade=False)
        ax.plot_surface(x_dx0_human, y_npc, z_ego, color=jama_color_map["B"], alpha=0.15, shade=False)
        ax.plot([], [], [], color=jama_color_map["B"], alpha=0.3, linewidth=5, label="Theory Zone B (AI Safe, Human Danger)")
        ax.plot([], [], [], color=jama_color_map["C"], alpha=0.3, linewidth=5, label="Theory Zone C (Both Danger)")

    ax.set_xlabel("dx0 (Initial Distance [m])", fontsize=12)
    ax.set_ylabel("npc_speed (NPC Speed [km/h])", fontsize=12)
    ax.set_zlabel("ego_speed (Ego Speed [km/h])", fontsize=12)
    ax.set_title(
        "Minimum TTC Levels in 3D Space" + format_slice_suffix(applied_filters),
        fontsize=16,
        fontweight="bold",
    )
    ax.legend(loc="upper left", bbox_to_anchor=(1.05, 1), fontsize=12)

    output_image = resolve_output_path(target_dir, output)
    plt.tight_layout()
    plt.savefig(output_image, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[Success] グラフを {output_image} に保存しました！")

    counts = valid_df["severity"].value_counts().sort_index().rename(index=label_map)
    print("\n=== min_TTC 段階別データ分布サマリー ===")
    print(counts)
    print(f"\n有効データ合計: {counts.sum()} 件")
    print(f"(参考) 記録されたTTC最小値: {valid_df['min_ttc'].min():.4f} 秒")
    return {
        "output_image": output_image,
        "valid_rows": int(len(valid_df)),
        "applied_slices": applied_filters,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(
        args.target,
        args.type,
        args.output,
        slice_filters=parse_slice_filters(args.slice),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
