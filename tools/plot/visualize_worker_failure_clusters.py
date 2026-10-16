from __future__ import annotations

import argparse
import os
import re
import subprocess
from typing import Dict, List

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

from tools.plot.common import load_dataset, resolve_dataset_path, resolve_output_path


def read_text_target(target: str) -> str:
    target = os.path.expanduser(target)
    remote_match = re.match(r"^(?P<hostspec>[^:]+):(?P<path>/.*)$", target)
    if remote_match:
        hostspec = remote_match.group("hostspec")
        remote_path = remote_match.group("path")
        cmd = ["ssh", hostspec, f"cat {remote_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to read remote file via ssh: {target}\n{result.stderr}"
            )
        return result.stdout
    with open(target, "r", encoding="utf-8", errors="ignore") as file:
        return file.read()


def parse_worker_log(log_text: str) -> Dict[str, List[int]]:
    outcomes = {"timeout": [], "success": []}
    blocks = re.split(r"--- Global Task ID (\d+) ---", log_text)
    if len(blocks) < 3:
        return outcomes
    for i in range(1, len(blocks), 2):
        loop_num = int(blocks[i])
        body = blocks[i + 1]
        if "[警告] タイムアウト" in body:
            outcomes["timeout"].append(loop_num)
        if "[成功]" in body:
            outcomes["success"].append(loop_num)
    return outcomes


def load_failure_dataset(csv_file: str) -> pd.DataFrame:
    df = load_dataset(csv_file)
    required = ["loop_num", "dx0", "npc_speed", "ego_speed", "reason"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    for col in ["loop_num", "dx0", "npc_speed", "ego_speed"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["loop_num", "dx0", "npc_speed", "ego_speed"]).copy()
    df["loop_num"] = df["loop_num"].astype(int)
    df["reason"] = df["reason"].fillna("").astype(str)
    return df


def build_category_frames(df: pd.DataFrame, parsed: Dict[str, List[int]]) -> Dict[str, pd.DataFrame]:
    timeout_loops = set(parsed["timeout"])
    success_loops = set(parsed["success"])
    timeout_df = df[df["loop_num"].isin(timeout_loops)].copy()
    success_df = df[df["loop_num"].isin(success_loops)].copy()
    shifted_success_df = success_df[success_df["reason"].str.contains("Error Recovery \\(Shifted", regex=True)].copy()
    normal_success_df = success_df[~success_df["reason"].str.contains("Error Recovery \\(Shifted", regex=True)].copy()
    return {
        "timeout": timeout_df.sort_values("loop_num"),
        "shifted_success": shifted_success_df.sort_values("loop_num"),
        "normal_success": normal_success_df.sort_values("loop_num"),
    }


def print_summary(frames: Dict[str, pd.DataFrame]) -> None:
    labels = {"timeout": "Timeout", "shifted_success": "Shifted Success", "normal_success": "Normal Success"}
    print("\n=== Worker Outcome Summary ===")
    for key in ["timeout", "shifted_success", "normal_success"]:
        frame = frames[key]
        print(f"\n[{labels[key]}] {len(frame)} points")
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            print(
                f"  loop={int(row['loop_num'])} | dx0={row['dx0']:.4f} | "
                f"npc={row['npc_speed']:.4f} | ego={row['ego_speed']:.4f} | "
                f"reason={row['reason']}"
            )


def plot_frames(frames: Dict[str, pd.DataFrame], title: str, output_image: str, annotate: bool) -> None:
    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection="3d")

    styles = {
        "timeout": {"color": "#e74c3c", "label": "Timeout", "marker": "o", "size": 90, "alpha": 0.92},
        "shifted_success": {"color": "#f39c12", "label": "Shifted Success", "marker": "^", "size": 88, "alpha": 0.95},
        "normal_success": {"color": "#3498db", "label": "Normal Success", "marker": "o", "size": 72, "alpha": 0.82},
    }

    for key in ["timeout", "shifted_success", "normal_success"]:
        frame = frames[key]
        if frame.empty:
            continue
        style = styles[key]
        ax.scatter(
            frame["dx0"],
            frame["npc_speed"],
            frame["ego_speed"],
            c=style["color"],
            marker=style["marker"],
            s=style["size"],
            alpha=style["alpha"],
            edgecolors="black",
            linewidths=0.35,
            depthshade=False,
        )
        if annotate:
            for _, row in frame.iterrows():
                ax.text(row["dx0"], row["npc_speed"], row["ego_speed"], str(int(row["loop_num"])), fontsize=8, color=style["color"])

    ax.set_title(title, fontsize=16, fontweight="bold")
    ax.set_xlabel("dx0 (Initial Distance [m])")
    ax.set_ylabel("npc_speed (NPC Speed [km/h])")
    ax.set_zlabel("ego_speed (Ego Speed [km/h])")
    ax.view_init(elev=24, azim=-61)

    legend_handles = [
        Line2D([0], [0], marker=styles[key]["marker"], color="w", label=styles[key]["label"], markerfacecolor=styles[key]["color"], markeredgecolor="black", markersize=10)
        for key in ["timeout", "shifted_success", "normal_success"]
    ]
    ax.legend(handles=legend_handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))

    plt.tight_layout()
    plt.savefig(output_image, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"\n[Success] Saved plot to: {output_image}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot one worker's timeout / shifted-success / normal-success points in 3D."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces", help="Dataset directory or CSV file")
    parser.add_argument("--type", default="uturn", help="Scenario type")
    parser.add_argument("--worker-log", required=True, help="Worker log path. Supports local path or user@host:/abs/path")
    parser.add_argument("--output", default="worker_failure_clusters_3d.png", help="Output image filename")
    parser.add_argument("--title", default=None, help="Custom plot title")
    parser.add_argument("--annotate", action="store_true", help="Annotate points with loop numbers")
    return parser


def run_plot(target: str, scenario_type: str, worker_log: str, output: str, title: str | None, annotate: bool) -> dict[str, object]:
    csv_file, target_dir = resolve_dataset_path(target, scenario_type)
    df = load_failure_dataset(csv_file)
    log_text = read_text_target(worker_log)
    parsed = parse_worker_log(log_text)
    frames = build_category_frames(df, parsed)
    resolved_title = title or "Worker Result Clusters: Timeout vs Shifted Success vs Normal Success"
    output_image = resolve_output_path(target_dir, output)
    print_summary(frames)
    plot_frames(frames, resolved_title, output_image, annotate)
    return {"output_image": output_image}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(args.target, args.type, args.worker_log, args.output, args.title, args.annotate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
