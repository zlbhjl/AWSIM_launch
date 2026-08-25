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
        description="Split dataset by worker and render separate 3D plots."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces")
    parser.add_argument("--type", default="uturn")
    parser.add_argument("--host-output", default="safety_boundaries_host_3d.png")
    parser.add_argument("--container-output", default="safety_boundaries_container_3d.png")
    parser.add_argument(
        "--slice",
        action="append",
        default=[],
        help="Optional slice filter in key=value form for extra scenario axes.",
    )
    return parser


def _plot_3d_scatter(data_df, title, output_path, *, scenario_type: str):
    if data_df.empty:
        print(f"[Info] 該当するデータが存在しないため、{output_path} の生成をスキップしました。")
        return

    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    color_map = {0: "#2ecc71", 1: "#3498db", 2: "#9b59b6", 3: "#f39c12", 4: "#e67e22", 5: "#e74c3c"}
    label_map = {
        0: "Level 0: Safe",
        1: "Level 1: Warning (TTC 1.1s)",
        2: "Level 2: Danger (TTC 0.9s)",
        3: "Level 3: Extreme Near Miss (TTC 0.5s)",
        4: "Level 4: Fatal Near Miss (TTC 0.3s)",
        5: "Level 5: Collision",
    }

    for severity in sorted(data_df["severity"].unique()):
        subset = data_df[data_df["severity"] == severity]
        ax.scatter(
            subset["dx0"],
            subset["npc_speed"],
            subset["ego_speed"],
            c=color_map[severity],
            label=label_map.get(severity, f"Level {severity}"),
            alpha=0.9 if severity > 0 else 0.15,
            s=60 if severity > 0 else 15,
        )

    if scenario_type == "uturn" and TheoreticalSafetyCalculator is not None and not data_df.empty:
        calc = TheoreticalSafetyCalculator()
        jama_color_map = {"B": "#f39c12", "C": "#e74c3c"}

        ego_min, ego_max = data_df["ego_speed"].min(), data_df["ego_speed"].max()
        npc_min, npc_max = data_df["npc_speed"].min(), data_df["npc_speed"].max()
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
    ax.set_title(title, fontsize=16, fontweight="bold")
    ax.legend(loc="upper left", bbox_to_anchor=(1.05, 1), fontsize=12)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[Success] {output_path} を保存しました！ (データ数: {len(data_df)} 件)")


def run_plot(
    target: str,
    scenario_type: str,
    host_output: str,
    container_output: str,
    *,
    slice_filters: dict[str, float] | None = None,
) -> dict[str, object]:
    csv_file, target_dir = resolve_dataset_path(target, scenario_type)
    print(f"[{csv_file}] を読み込み中...")
    df = load_dataset(csv_file)
    df, applied_filters = apply_slice_filters(df, slice_filters)

    if "worker_id" not in df.columns:
        raise ValueError("データセットに 'worker_id' 列がありません。分割して描画できません。")

    target_columns = ["c_collision", "c_ttc_0.3", "c_ttc_0.5", "c_ttc_0.9", "c_ttc_1.1"]
    valid_df = df.copy()
    for col in target_columns:
        if col in valid_df.columns:
            valid_df = valid_df[valid_df[col].isin([0, 1])]

    plot_cols = ["dx0", "npc_speed", "ego_speed"]
    for col in plot_cols:
        if col in valid_df.columns:
            valid_df[col] = pd.to_numeric(valid_df[col], errors="coerce")
    valid_df = valid_df.dropna(subset=plot_cols)

    if "c_collision" in valid_df.columns:
        collision_mask = valid_df["c_collision"] == 1
        for col in target_columns:
            if col.startswith("c_ttc_"):
                valid_df.loc[collision_mask, col] = 1

    ttc_cols = sorted(
        [c for c in target_columns if c.startswith("c_ttc_")],
        key=lambda x: float(x.split("_")[-1]),
    )
    for i in range(len(ttc_cols) - 1):
        valid_df.loc[valid_df[ttc_cols[i]] == 1, ttc_cols[i + 1]] = 1

    def get_severity(row):
        if row.get("c_collision", 0) == 1:
            return 5
        if row.get("c_ttc_0.3", 0) == 1:
            return 4
        if row.get("c_ttc_0.5", 0) == 1:
            return 3
        if row.get("c_ttc_0.9", 0) == 1:
            return 2
        if row.get("c_ttc_1.1", 0) == 1:
            return 1
        return 0

    valid_df["severity"] = valid_df.apply(get_severity, axis=1)
    valid_df["worker_id"] = valid_df["worker_id"].astype(str).str.replace(r"\.0$", "", regex=True)

    print(f"[Info] クリーニング後の有効データ数: {len(valid_df)} 件")
    print(f"[Info] データセット内に存在する worker_id 一覧: {valid_df['worker_id'].unique().tolist()}")

    host_df = valid_df[valid_df["worker_id"] == "21"]
    container_df = valid_df[valid_df["worker_id"].isin(["22", "23"])]

    print("\n=== 分割3Dグラフの生成を開始します ===")
    host_output_path = resolve_output_path(target_dir, host_output)
    container_output_path = resolve_output_path(target_dir, container_output)
    title_suffix = format_slice_suffix(applied_filters)
    _plot_3d_scatter(
        host_df,
        "Safety Boundaries: Host Execution (Node 21)" + title_suffix,
        host_output_path,
        scenario_type=scenario_type,
    )
    _plot_3d_scatter(
        container_df,
        "Safety Boundaries: Container Execution (Nodes 22 & 23)" + title_suffix,
        container_output_path,
        scenario_type=scenario_type,
    )

    print("\n=== データ分布サマリー ===")
    print("【Host (Node 21)】")
    counts_host = host_df["severity"].value_counts().sort_index()
    print(counts_host.to_string() if not counts_host.empty else "データなし")

    print("\n【Container (Nodes 22 & 23)】")
    counts_container = container_df["severity"].value_counts().sort_index()
    print(counts_container.to_string() if not counts_container.empty else "データなし")
    return {
        "host_output": host_output_path,
        "container_output": container_output_path,
        "applied_slices": applied_filters,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(
        args.target,
        args.type,
        args.host_output,
        args.container_output,
        slice_filters=parse_slice_filters(args.slice),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
