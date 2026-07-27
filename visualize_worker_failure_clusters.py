#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import subprocess
import sys
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D


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

    with open(target, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def parse_worker_log(log_text: str) -> Dict[str, List[int]]:
    outcomes = {
        "timeout": [],
        "success": [],
    }
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


def load_dataset(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file, engine="python", on_bad_lines="skip")
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
    labels = {
        "timeout": "Timeout",
        "shifted_success": "Shifted Success",
        "normal_success": "Normal Success",
    }
    print("\n=== Worker Outcome Summary ===")
    for key in ["timeout", "shifted_success", "normal_success"]:
        frame = frames[key]
        print(f"\n[{labels[key]}] {len(frame)} points")
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            print(
                f"  loop={int(row['loop_num'])} | "
                f"dx0={row['dx0']:.4f} | npc={row['npc_speed']:.4f} | ego={row['ego_speed']:.4f} | "
                f"reason={row['reason']}"
            )


def plot_frames(
    frames: Dict[str, pd.DataFrame],
    title: str,
    output_image: str,
    annotate: bool,
) -> None:
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
                ax.text(
                    row["dx0"],
                    row["npc_speed"],
                    row["ego_speed"],
                    str(int(row["loop_num"])),
                    fontsize=8,
                    color=style["color"],
                )

    ax.set_title(title, fontsize=16, fontweight="bold")
    ax.set_xlabel("dx0 (Initial Distance [m])")
    ax.set_ylabel("npc_speed (NPC Speed [km/h])")
    ax.set_zlabel("ego_speed (Ego Speed [km/h])")
    ax.view_init(elev=24, azim=-61)

    legend_handles = [
        Line2D([0], [0], marker=styles[key]["marker"], color="w", label=styles[key]["label"],
               markerfacecolor=styles[key]["color"], markeredgecolor="black", markersize=10)
        for key in ["timeout", "shifted_success", "normal_success"]
    ]
    ax.legend(handles=legend_handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))

    plt.tight_layout()
    plt.savefig(output_image, dpi=300, bbox_inches="tight")
    print(f"\n[Success] Saved plot to: {output_image}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot one worker's timeout / shifted-success / normal-success points in 3D."
    )
    parser.add_argument("target", nargs="?", default="~/simulation_traces",
                        help="Dataset directory or CSV file")
    parser.add_argument("--type", default="uturn", help="Scenario type")
    parser.add_argument("--worker-log", required=True,
                        help="Worker log path. Supports local path or user@host:/abs/path")
    parser.add_argument("--output", default="worker_failure_clusters_3d.png",
                        help="Output image filename")
    parser.add_argument("--title", default=None,
                        help="Custom plot title")
    parser.add_argument("--annotate", action="store_true",
                        help="Annotate points with loop numbers")
    args = parser.parse_args()

    csv_file, target_dir = resolve_dataset_path(args.target, args.type)
    df = load_dataset(csv_file)
    log_text = read_text_target(args.worker_log)
    parsed = parse_worker_log(log_text)
    frames = build_category_frames(df, parsed)

    title = args.title or "Worker Result Clusters: Timeout vs Shifted Success vs Normal Success"
    output_image = args.output
    if not os.path.isabs(output_image):
        output_image = os.path.join(target_dir, output_image)

    print_summary(frames)
    plot_frames(frames, title, output_image, args.annotate)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[Error] {exc}")
        sys.exit(1)
