from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PLOT_AXES = ("dx0", "npc_speed", "ego_speed")
TTC_PATTERN = re.compile(r"^c_ttc_(\d+(?:\.\d+)?)$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize paired outcome changes between two Autoware datasets."
    )
    parser.add_argument("source_csv", help="Baseline dataset CSV (for example, Autoware 1.7.1)")
    parser.add_argument("replay_csv", help="Replay dataset CSV (for example, Autoware 1.8.0)")
    parser.add_argument("--source-key", default="loop_num")
    parser.add_argument("--replay-key", default="meta_replay_source_loop_num")
    parser.add_argument("--source-label", default="Autoware 1.7.1")
    parser.add_argument("--replay-label", default="Autoware 1.8.0")
    parser.add_argument("--output", default="autoware_version_comparison.png")
    return parser


def _load_with_suffix(csv_path: str, key: str, suffix: str) -> pd.DataFrame:
    frame = pd.read_csv(Path(csv_path).expanduser(), low_memory=False)
    if key not in frame.columns:
        raise ValueError(f"Pair key '{key}' is missing from {csv_path}")

    pair_key = pd.to_numeric(frame[key], errors="coerce")
    if pair_key.isna().any():
        raise ValueError(f"Pair key '{key}' contains non-numeric values in {csv_path}")
    if pair_key.duplicated().any():
        raise ValueError(f"Pair key '{key}' contains duplicates in {csv_path}")

    frame = frame.drop(columns=[key]).rename(columns=lambda column: f"{column}_{suffix}")
    frame.insert(0, "_pair_key", pair_key.astype("int64"))
    return frame


def _require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {', '.join(missing)}")


def build_comparison(
    source_csv: str,
    replay_csv: str,
    *,
    source_key: str = "loop_num",
    replay_key: str = "meta_replay_source_loop_num",
) -> tuple[pd.DataFrame, dict[str, object]]:
    source = _load_with_suffix(source_csv, source_key, "source")
    replay = _load_with_suffix(replay_csv, replay_key, "replay")
    merged = source.merge(replay, on="_pair_key", how="inner", validate="one_to_one")

    required = [
        *(f"{column}_source" for column in PLOT_AXES),
        "c_collision_source",
        "c_collision_replay",
        "status_replay",
    ]
    _require_columns(merged, list(required))

    for column in (*PLOT_AXES, "c_collision"):
        merged[f"{column}_source"] = pd.to_numeric(
            merged[f"{column}_source"], errors="coerce"
        )
    merged["c_collision_replay"] = pd.to_numeric(
        merged["c_collision_replay"], errors="coerce"
    )

    source_binary = merged["c_collision_source"].isin([0, 1])
    replay_binary = merged["c_collision_replay"].isin([0, 1])
    complete = merged["status_replay"].eq("success") & source_binary & replay_binary

    source_collision = merged["c_collision_source"].eq(1)
    replay_collision = merged["c_collision_replay"].eq(1)
    merged["transition"] = "unresolved"
    merged.loc[complete & ~source_collision & ~replay_collision, "transition"] = "0_to_0"
    merged.loc[complete & ~source_collision & replay_collision, "transition"] = "0_to_1"
    merged.loc[complete & source_collision & ~replay_collision, "transition"] = "1_to_0"
    merged.loc[complete & source_collision & replay_collision, "transition"] = "1_to_1"

    counts = merged["transition"].value_counts().to_dict()
    summary: dict[str, object] = {
        "source_rows": int(len(source)),
        "replay_rows": int(len(replay)),
        "matched_rows": int(len(merged)),
        "complete_rows": int(complete.sum()),
        "changed_rows": int(merged["transition"].isin(["0_to_1", "1_to_0"]).sum()),
        "unresolved_rows": int((merged["transition"] == "unresolved").sum()),
        "transition_counts": {
            name: int(counts.get(name, 0))
            for name in ("0_to_0", "0_to_1", "1_to_0", "1_to_1", "unresolved")
        },
    }
    return merged, summary


def _plot_transition_points(ax, frame: pd.DataFrame) -> None:
    styles = {
        "0_to_0": ("#8ca58c", ".", 5, 0.05, "No collision in both"),
        "1_to_1": ("#694f44", ".", 7, 0.08, "Collision in both"),
        "0_to_1": ("#d73027", "o", 42, 0.9, "New collision in 1.8.0"),
        "1_to_0": ("#2474b5", "^", 48, 0.9, "Collision removed in 1.8.0"),
        "unresolved": ("#e5a000", "x", 32, 0.75, "1.8.0 unresolved"),
    }
    for transition in ("0_to_0", "1_to_1", "unresolved", "0_to_1", "1_to_0"):
        subset = frame[frame["transition"] == transition]
        if subset.empty:
            continue
        color, marker, size, alpha, label = styles[transition]
        ax.scatter(
            subset["dx0_source"],
            subset["npc_speed_source"],
            subset["ego_speed_source"],
            c=color,
            marker=marker,
            s=size,
            alpha=alpha,
            label=f"{label} ({len(subset):,})",
            depthshade=False,
        )
    ax.set_xlabel("dx0 [m]")
    ax.set_ylabel("NPC speed [km/h]")
    ax.set_zlabel("Ego speed [km/h]")
    ax.set_title("Paired collision changes")
    ax.legend(loc="upper left", fontsize=8)


def _plot_transition_matrix(ax, summary: dict[str, object], source_label: str, replay_label: str) -> None:
    counts = summary["transition_counts"]
    matrix = np.array(
        [
            [counts["0_to_0"], counts["0_to_1"]],
            [counts["1_to_0"], counts["1_to_1"]],
        ]
    )
    ax.imshow(matrix, cmap="Blues")
    for row in range(2):
        for column in range(2):
            ax.text(column, row, f"{matrix[row, column]:,}", ha="center", va="center", fontsize=13)
    ax.set_xticks([0, 1], ["No collision", "Collision"])
    ax.set_yticks([0, 1], ["No collision", "Collision"])
    ax.set_xlabel(replay_label)
    ax.set_ylabel(source_label)
    ax.set_title("Collision transition counts")


def _ttc_differences(frame: pd.DataFrame) -> list[tuple[float, float]]:
    thresholds = []
    for column in frame.columns:
        if not column.endswith("_source"):
            continue
        base_name = column[: -len("_source")]
        match = TTC_PATTERN.match(base_name)
        replay_column = f"{base_name}_replay"
        if not match or replay_column not in frame.columns:
            continue
        source_values = pd.to_numeric(frame[column], errors="coerce")
        replay_values = pd.to_numeric(frame[replay_column], errors="coerce")
        valid = (
            frame["transition"].ne("unresolved")
            & source_values.isin([0, 1])
            & replay_values.isin([0, 1])
        )
        if valid.any():
            difference = 100.0 * float((replay_values[valid] - source_values[valid]).mean())
            thresholds.append((float(match.group(1)), difference))
    return sorted(thresholds, reverse=True)


def _plot_ttc_differences(ax, frame: pd.DataFrame, source_label: str, replay_label: str) -> None:
    differences = _ttc_differences(frame)
    if not differences:
        ax.text(0.5, 0.5, "No paired TTC flags", ha="center", va="center")
        ax.set_axis_off()
        return
    labels = [f"<{threshold:g}s" for threshold, _ in differences]
    values = [difference for _, difference in differences]
    colors = ["#d73027" if value > 0 else "#2474b5" for value in values]
    bars = ax.barh(labels, values, color=colors, alpha=0.85)
    ax.axvline(0.0, color="#333333", linewidth=1)
    ax.bar_label(bars, labels=[f"{value:+.2f} pp" for value in values], padding=3, fontsize=8)
    ax.set_xlabel(f"Risk-rate change ({replay_label} - {source_label})")
    ax.set_title("Paired TTC-risk change")
    ax.grid(axis="x", alpha=0.2)


def run_plot(
    source_csv: str,
    replay_csv: str,
    output: str,
    *,
    source_key: str = "loop_num",
    replay_key: str = "meta_replay_source_loop_num",
    source_label: str = "Autoware 1.7.1",
    replay_label: str = "Autoware 1.8.0",
) -> dict[str, object]:
    frame, summary = build_comparison(
        source_csv,
        replay_csv,
        source_key=source_key,
        replay_key=replay_key,
    )

    fig = plt.figure(figsize=(21, 7.5))
    grid = fig.add_gridspec(1, 3, width_ratios=[1.55, 0.8, 1.0])
    _plot_transition_points(fig.add_subplot(grid[0], projection="3d"), frame)
    _plot_transition_matrix(fig.add_subplot(grid[1]), summary, source_label, replay_label)
    _plot_ttc_differences(fig.add_subplot(grid[2]), frame, source_label, replay_label)
    fig.suptitle(
        f"Paired Autoware comparison | matched={summary['matched_rows']:,}, "
        f"changed={summary['changed_rows']:,}, unresolved={summary['unresolved_rows']:,}",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout()

    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=250, bbox_inches="tight")
    plt.close(fig)
    summary["output_image"] = str(output_path)

    print(f"[Success] Saved paired comparison to {output_path}")
    print(f"Matched: {summary['matched_rows']:,}")
    print(f"Complete: {summary['complete_rows']:,}")
    print(f"Changed: {summary['changed_rows']:,}")
    print(f"Unresolved: {summary['unresolved_rows']:,}")
    print(f"Transitions: {summary['transition_counts']}")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_plot(
        args.source_csv,
        args.replay_csv,
        args.output,
        source_key=args.source_key,
        replay_key=args.replay_key,
        source_label=args.source_label,
        replay_label=args.replay_label,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
