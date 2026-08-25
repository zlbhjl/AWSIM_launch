from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd

from tools.plot.common import (
    apply_slice_filters,
    format_slice_suffix,
    load_dataset,
    parse_slice_filters,
    resolve_dataset_path,
    resolve_output_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize JAMA theoretical zones in 3D."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces")
    parser.add_argument("--type", default="uturn")
    parser.add_argument("--output-a", default="jama_zone_a_3d.png")
    parser.add_argument("--output-b", default="jama_zone_b_3d.png")
    parser.add_argument(
        "--slice",
        action="append",
        default=[],
        help="Optional slice filter in key=value form for extra scenario axes.",
    )
    return parser


def _plot_jama_zone(data_df, zone_column, title, output_path):
    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    color_map = {"A": "#2ecc71", "B": "#f39c12", "C": "#e74c3c", "D": "#9b59b6"}
    label_map = {
        "A": "Zone A: Both Safe",
        "B": "Zone B: AI Safe, Human Danger",
        "C": "Zone C: Both Danger",
        "D": "Zone D: Human Safe, AI Danger (Rare)",
    }

    for zone in sorted(data_df[zone_column].unique()):
        subset = data_df[data_df[zone_column] == zone]
        ax.scatter(
            subset["dx0"],
            subset["npc_speed"],
            subset["ego_speed"],
            c=color_map.get(zone, "#95a5a6"),
            label=label_map.get(zone, f"Zone {zone}"),
            alpha=0.8 if zone in ["B", "C"] else 0.2,
            s=60 if zone in ["B", "C"] else 20,
        )

    ax.set_xlabel("dx0 (Initial Distance [m])", fontsize=12)
    ax.set_ylabel("npc_speed (NPC Speed [km/h])", fontsize=12)
    ax.set_zlabel("ego_speed (Ego Speed [km/h])", fontsize=12)
    ax.set_title(title, fontsize=16, fontweight="bold")
    ax.legend(loc="upper left", bbox_to_anchor=(1.05, 1), fontsize=12)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[Success] グラフを {output_path} に保存しました！")


def run_plot(
    target: str,
    scenario_type: str,
    output_a: str,
    output_b: str,
    *,
    slice_filters: dict[str, float] | None = None,
) -> dict[str, object]:
    csv_file, target_dir = resolve_dataset_path(target, scenario_type)
    print(f"[{csv_file}] を読み込み中...")
    df = load_dataset(csv_file)
    df, applied_filters = apply_slice_filters(df, slice_filters)

    required_cols = ["dx0", "npc_speed", "ego_speed", "theory_zone_a", "theory_zone_b"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(
            f"データセットに必要な列が見つかりません: {missing_cols}\n"
            "JAMA理論値が記録されているデータセットを使用してください。"
        )

    df = df.copy()
    for col in ["dx0", "npc_speed", "ego_speed"]:
        df.loc[:, col] = pd.to_numeric(df[col], errors="coerce")
    valid_df = df.dropna(subset=required_cols).copy()

    print("\nJAMA理論に基づく3D可視化を開始します...")
    output_path_a = resolve_output_path(target_dir, output_a)
    output_path_b = resolve_output_path(target_dir, output_b)
    title_suffix = format_slice_suffix(applied_filters)
    _plot_jama_zone(
        valid_df,
        "theory_zone_a",
        "JAMA Theoretical Zones - Approach A (Wall Assumption)" + title_suffix,
        output_path_a,
    )
    _plot_jama_zone(
        valid_df,
        "theory_zone_b",
        "JAMA Theoretical Zones - Approach B (NPC Forward Movement)" + title_suffix,
        output_path_b,
    )
    print("完了しました。")
    return {
        "output_a": output_path_a,
        "output_b": output_path_b,
        "applied_slices": applied_filters,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(
        args.target,
        args.type,
        args.output_a,
        args.output_b,
        slice_filters=parse_slice_filters(args.slice),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
