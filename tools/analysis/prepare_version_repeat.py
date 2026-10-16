from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


INPUT_COLUMNS = (
    "ego_init_lane",
    "ego_init_offset",
    "ego_goal_lane",
    "ego_goal_offset",
    "npc_init_lane",
    "npc_init_offset",
    "uturn_next_lane",
    "acceleration",
    "dx0",
    "ego_speed",
    "npc_speed",
)
TTC_COLUMNS = (
    "c_ttc_1.5",
    "c_ttc_1.3",
    "c_ttc_1.2",
    "c_ttc_1.1",
    "c_ttc_0.9",
    "c_ttc_0.7",
    "c_ttc_0.5",
    "c_ttc_0.3",
)
RUN_ORDER = (
    ("autoware171", 1),
    ("autoware180", 1),
    ("autoware180", 2),
    ("autoware171", 2),
    ("autoware171", 3),
    ("autoware180", 3),
    ("autoware180", 4),
    ("autoware171", 4),
    ("autoware171", 5),
    ("autoware180", 5),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare paired Autoware repeat cases and manual replay commands."
    )
    parser.add_argument("--source-csv", required=True, help="Autoware 1.7.1 source CSV")
    parser.add_argument("--replay-csv", required=True, help="Autoware 1.8.0 replay result CSV")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--random-count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=171180)
    return parser


def _validated_key(frame: pd.DataFrame, column: str, csv_path: Path) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"{csv_path} is missing key column: {column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        raise ValueError(f"{csv_path} contains non-numeric {column} values")
    if values.duplicated().any():
        raise ValueError(f"{csv_path} contains duplicate {column} values")
    return values.astype("int64")


def _require_columns(frame: pd.DataFrame, columns: set[str], csv_path: Path) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{csv_path} is missing columns: {', '.join(missing)}")


def _load_pairs(source_path: Path, replay_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = pd.read_csv(source_path, low_memory=False)
    replay = pd.read_csv(replay_path, low_memory=False)
    _require_columns(
        source,
        {"loop_num", "status", "reason", "c_collision", *INPUT_COLUMNS},
        source_path,
    )
    _require_columns(
        replay,
        {"meta_replay_source_loop_num", "status", "c_collision", *INPUT_COLUMNS},
        replay_path,
    )
    source["_source_loop_num"] = _validated_key(source, "loop_num", source_path)
    replay["_source_loop_num"] = _validated_key(
        replay, "meta_replay_source_loop_num", replay_path
    )

    source_for_merge = source.add_suffix("_171").rename(
        columns={"_source_loop_num_171": "_source_loop_num"}
    )
    replay_for_merge = replay.add_suffix("_180").rename(
        columns={"_source_loop_num_180": "_source_loop_num"}
    )
    pairs = source_for_merge.merge(
        replay_for_merge,
        on="_source_loop_num",
        how="inner",
        validate="one_to_one",
    )
    return source, pairs


def _classify_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    source_collision = pd.to_numeric(pairs["c_collision_171"], errors="coerce")
    replay_collision = pd.to_numeric(pairs["c_collision_180"], errors="coerce")
    complete = (
        pairs["status_180"].astype(str).str.lower().eq("success")
        & source_collision.isin([0, 1])
        & replay_collision.isin([0, 1])
    )
    classified = pairs.loc[complete].copy()
    source_collision = source_collision.loc[complete].astype(int)
    replay_collision = replay_collision.loc[complete].astype(int)
    classified["original_transition"] = (
        source_collision.astype(str) + "_to_" + replay_collision.astype(str)
    )
    return classified


def _build_manifest(
    classified: pd.DataFrame,
    *,
    random_count: int,
    seed: int,
) -> pd.DataFrame:
    if random_count <= 0:
        raise ValueError("--random-count must be positive")
    if random_count > len(classified):
        raise ValueError(
            f"--random-count {random_count} exceeds complete pair count {len(classified)}"
        )

    discordant_ids = set(
        classified.loc[
            classified["original_transition"].isin(["0_to_1", "1_to_0"]),
            "_source_loop_num",
        ].astype(int)
    )
    random_ids = set(
        classified.sample(n=random_count, random_state=seed)["_source_loop_num"].astype(int)
    )
    selected_ids = discordant_ids | random_ids
    manifest = classified[classified["_source_loop_num"].isin(selected_ids)].copy()
    manifest["cohort_discordant"] = manifest["_source_loop_num"].isin(discordant_ids)
    manifest["cohort_random_validation"] = manifest["_source_loop_num"].isin(random_ids)
    manifest = manifest.sample(frac=1.0, random_state=seed + 1).reset_index(drop=True)
    manifest.insert(0, "selection_order", range(1, len(manifest) + 1))

    output_columns = [
        "selection_order",
        "_source_loop_num",
        "cohort_discordant",
        "cohort_random_validation",
        "original_transition",
        "worker_id_171",
        "worker_id_180",
        "status_171",
        "status_180",
        "c_collision_171",
        "c_collision_180",
    ]
    output_columns.extend(
        f"{name}_171" for name in INPUT_COLUMNS if f"{name}_171" in manifest.columns
    )
    output_columns.extend(
        column
        for name in TTC_COLUMNS
        for column in (f"{name}_171", f"{name}_180")
        if column in manifest.columns
    )
    return manifest.loc[:, output_columns].rename(
        columns={"_source_loop_num": "source_loop_num"}
    )


def _render_command(
    *,
    output_dir: Path,
    cases_csv: Path,
    expected_count: int,
    version: str,
    repetition: int,
) -> str:
    suffix = f"r{repetition:02d}"
    run_dir = output_dir / "runs" / f"{version}_{suffix}"
    if version == "autoware171":
        profile = "autoware171"
        sync_flag = "--sync-awsim-script-py"
    else:
        profile = "autoware180_ekfdiagfix"
        sync_flag = "--sync-autoware180-map"
    run_id = f"version_repeat_{version}_{suffix}"
    return "\n".join(
        [
            "```bash",
            "cd /home/passd/AWSIM_launch",
            "python3 run_orchestrator_cluster_v2.py \\",
            f"  --output {run_dir / 'uturn_records.jsonl'} \\",
            f"  --dataset-csv {run_dir / 'uturn_dataset.csv'} \\",
            "  --case-kind uturn \\",
            "  --mode replay \\",
            f"  --replay-csv {cases_csv} \\",
            f"  --replay-expected-count {expected_count} \\",
            f"  --run-id {run_id} \\",
            f"  --container-profile {profile} \\",
            "  --scenario-profile autoware171 \\",
            "  --headless \\",
            f"  {sync_flag} \\",
            "  --auto-restart-stale-workers \\",
            "  --auto-restart-missing-workers \\",
            "  --auto-restart-gpu-workers \\",
            "  --worker-queue-connect-retries 6 \\",
            "  --worker-queue-connect-retry-interval-sec 15",
            "```",
        ]
    )


def _write_commands(output_dir: Path, cases_csv: Path, expected_count: int) -> Path:
    commands_path = output_dir / "manual_commands.md"
    sections = [
        "# Autoware 1.7.1 / 1.8.0 repeat experiment commands",
        "",
        f"- Replay cases: `{expected_count}`",
        "- Repetitions: `5` per version",
        "- Execute one block at a time in the listed order.",
        "- Wait for `queue=0` and the final summary before stopping containers.",
        "- Between blocks, run `cd /home/passd/AWSIM_launch && python3 stop_containers.py`.",
        "- To resume an interrupted block, run exactly the same block again.",
        "",
    ]
    for sequence, (version, repetition) in enumerate(RUN_ORDER, start=1):
        sections.extend(
            [
                f"## {sequence}. {version} repetition {repetition}",
                "",
                _render_command(
                    output_dir=output_dir,
                    cases_csv=cases_csv,
                    expected_count=expected_count,
                    version=version,
                    repetition=repetition,
                ),
                "",
            ]
        )
    commands_path.write_text("\n".join(sections), encoding="utf-8")
    return commands_path


def prepare_repeat_experiment(
    source_csv: str,
    replay_csv: str,
    output_dir: str,
    *,
    random_count: int = 500,
    seed: int = 171180,
) -> dict[str, object]:
    source_path = Path(source_csv).expanduser().resolve()
    replay_path = Path(replay_csv).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    source, pairs = _load_pairs(source_path, replay_path)
    classified = _classify_pairs(pairs)
    manifest = _build_manifest(classified, random_count=random_count, seed=seed)
    selected_ids = manifest["source_loop_num"].astype(int).tolist()
    source_indexed = source.set_index("_source_loop_num", drop=False)
    cases = source_indexed.loc[selected_ids].drop(columns=["_source_loop_num"])

    cases_path = destination / "autoware_repeat_cases.csv"
    manifest_path = destination / "autoware_repeat_manifest.csv"
    cases.to_csv(cases_path, index=False)
    manifest.to_csv(manifest_path, index=False)
    commands_path = _write_commands(destination, cases_path, len(cases))

    result = {
        "matched_count": int(len(pairs)),
        "complete_count": int(len(classified)),
        "discordant_count": int(manifest["cohort_discordant"].sum()),
        "random_validation_count": int(manifest["cohort_random_validation"].sum()),
        "cohort_overlap_count": int(
            (manifest["cohort_discordant"] & manifest["cohort_random_validation"]).sum()
        ),
        "replay_case_count": int(len(cases)),
        "total_planned_runs": int(len(cases) * len(RUN_ORDER)),
        "cases_csv": str(cases_path),
        "manifest_csv": str(manifest_path),
        "commands_file": str(commands_path),
        "seed": int(seed),
    }
    for key, value in result.items():
        print(f"{key}: {value}")
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prepare_repeat_experiment(
        args.source_csv,
        args.replay_csv,
        args.output_dir,
        random_count=args.random_count,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
