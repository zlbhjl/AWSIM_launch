from pathlib import Path

import pandas as pd

from tools.analysis.prepare_version_repeat import prepare_repeat_experiment


INPUT_COLUMNS = {
    "ego_init_lane": 1,
    "ego_init_offset": 0.0,
    "ego_goal_lane": 2,
    "ego_goal_offset": 0.0,
    "npc_init_lane": 3,
    "npc_init_offset": 0.0,
    "uturn_next_lane": 4,
    "acceleration": 1.0,
    "dx0": 10.0,
    "ego_speed": 30.0,
    "npc_speed": 20.0,
}


def _source_row(loop_num: int, collision: int) -> dict[str, object]:
    return {
        **INPUT_COLUMNS,
        "loop_num": loop_num,
        "case_id": f"source-{loop_num}",
        "worker_id": "worker_21",
        "status": "success",
        "reason": f"BINOMIAL_CI: Sampling ({loop_num})",
        "c_collision": collision,
        "c_ttc_0.5": collision,
    }


def _replay_row(loop_num: int, collision: int) -> dict[str, object]:
    return {
        **INPUT_COLUMNS,
        "meta_replay_source_loop_num": loop_num,
        "worker_id": "worker_22",
        "status": "success",
        "c_collision": collision,
        "c_ttc_0.5": collision,
    }


def test_prepare_repeat_experiment_writes_unique_union_and_commands(tmp_path: Path):
    source_csv = tmp_path / "source.csv"
    replay_csv = tmp_path / "replay.csv"
    output_dir = tmp_path / "repeat"
    pd.DataFrame(
        [
            _source_row(1, 0),
            _source_row(2, 0),
            _source_row(3, 1),
            _source_row(4, 1),
            _source_row(5, 0),
        ]
    ).to_csv(source_csv, index=False)
    pd.DataFrame(
        [
            _replay_row(1, 0),
            _replay_row(2, 1),
            _replay_row(3, 0),
            _replay_row(4, 1),
            _replay_row(5, 0),
        ]
    ).to_csv(replay_csv, index=False)

    result = prepare_repeat_experiment(
        str(source_csv),
        str(replay_csv),
        str(output_dir),
        random_count=2,
        seed=7,
    )

    cases = pd.read_csv(result["cases_csv"])
    manifest = pd.read_csv(result["manifest_csv"])
    commands = Path(result["commands_file"]).read_text(encoding="utf-8")
    assert result["discordant_count"] == 2
    assert result["random_validation_count"] == 2
    assert result["replay_case_count"] == len(set(cases["loop_num"]))
    assert result["total_planned_runs"] == result["replay_case_count"] * 10
    assert set(manifest.loc[manifest["cohort_discordant"], "source_loop_num"]) == {2, 3}
    assert commands.count("python3 run_orchestrator_cluster_v2.py") == 10
    assert "--container-profile autoware171" in commands
    assert "--container-profile autoware180_ekfdiagfix" in commands
