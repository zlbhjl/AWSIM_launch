from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tools.plot.common import load_dataset, resolve_dataset_path, resolve_output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare violation probabilities across workers."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces")
    parser.add_argument("--type", default="uturn")
    parser.add_argument("--output", default="worker_stats_comparison.png")
    return parser


def run_plot(target: str, scenario_type: str, output: str) -> dict[str, object]:
    csv_file, target_dir = resolve_dataset_path(target, scenario_type)
    print(f"[{csv_file}] を読み込み中...")
    df = load_dataset(csv_file)

    if "worker_id" not in df.columns:
        raise ValueError("データセットに 'worker_id' 列がありません。")

    target_metrics = ["c_collision"] + sorted(
        [col for col in df.columns if col.startswith("c_ttc_")]
    )
    if not target_metrics:
        raise ValueError("衝突またはTTCの指標が見つかりません。")

    for metric in target_metrics:
        df[metric] = pd.to_numeric(df[metric], errors="coerce")

    stats = []
    for worker in sorted(df["worker_id"].dropna().unique()):
        worker_df = df[df["worker_id"] == worker]
        worker_str = str(worker)
        worker_stat = {"Worker": worker_str}
        if worker_str == "21":
            worker_stat["Label"] = "Node 21 (Host)"
        elif worker_str in ["22", "23"]:
            worker_stat["Label"] = f"Node {worker_str} (Container)"
        else:
            worker_stat["Label"] = f"Node {worker_str}"

        for metric in target_metrics:
            valid_data = worker_df[worker_df[metric].isin([0, 1])][metric]
            prob = (valid_data == 1).sum() / len(valid_data) * 100 if len(valid_data) > 0 else 0.0
            worker_stat[metric] = prob

        worker_stat["Total_Samples"] = len(worker_df)
        stats.append(worker_stat)

    stats_df = pd.DataFrame(stats)

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(target_metrics))
    width = 0.8 / len(stats_df)

    for i, row in stats_df.iterrows():
        probs = [row[m] for m in target_metrics]
        offset = width * i - (width * len(stats_df)) / 2 + width / 2
        ax.bar(x + offset, probs, width, label=f"{row['Label']} (N={row['Total_Samples']})")
        for j, prob in enumerate(probs):
            ax.text(x[j] + offset, prob + 1, f"{prob:.1f}%", ha="center", va="bottom", fontsize=9)

    ax.set_xlabel("Safety Metrics (Collision & TTC)")
    ax.set_ylabel("Violation Probability (%)")
    ax.set_title("Violation Probability Comparison: Host vs Container Execution")
    ax.set_xticks(x)
    ax.set_xticklabels([m.replace("c_", "").replace("_", " ").title() for m in target_metrics])
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.7)

    output_image = resolve_output_path(target_dir, output)
    plt.tight_layout()
    plt.savefig(output_image, dpi=300)
    plt.close(fig)
    print(f"\n[Success] ワーカー別の比較グラフを {output_image} に保存しました！")

    print("\n=== ワーカー別 違反確率サマリー (%) ===")
    display_cols = ["Label", "Total_Samples"] + target_metrics
    print(stats_df[display_cols].to_string(index=False))
    return {"output_image": output_image, "row_count": int(len(stats_df))}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(args.target, args.type, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
