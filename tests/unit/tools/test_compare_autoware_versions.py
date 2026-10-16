import json

import pandas as pd
import pytest

from tools.analysis import compare_autoware_versions as cav


def _base_rows(collisions):
    return pd.DataFrame(
        {
            "loop_num": range(1, len(collisions) + 1),
            "status": "success",
            "reason": "BINOMIAL_CI: Sampling",
            "c_collision": collisions,
            "dx0": [10.0 + i for i in range(len(collisions))],
            "ego_speed": 39.0,
            "npc_speed": 12.0,
            "worker_id": "worker_21",
        }
    )


def _replay_rows(collisions, statuses):
    return pd.DataFrame(
        {
            "meta_replay_source_loop_num": range(1, len(collisions) + 1),
            "status": statuses,
            "c_collision": collisions,
            "worker_id": "worker_22",
        }
    )


def test_mcnemar_counts_discordant_pairs_and_paired_odds_ratio() -> None:
    result = cav.mcnemar(pd.Series([0, 0, 0, 1, 1]), pd.Series([1, 1, 0, 0, 1]))

    assert result["zero_to_one"] == 2
    assert result["one_to_zero"] == 1
    assert result["paired_odds_ratio"] == 2.0
    assert result["agree_rate"] == pytest.approx(0.4)


def test_cochran_q_is_zero_when_versions_agree() -> None:
    matrix = pd.DataFrame({"a": [0, 1, 1], "b": [0, 1, 1], "c": [0, 1, 1]}).to_numpy()

    assert cav.cochran_q(matrix)["q"] == 0.0


def test_holm_adjustment_is_monotone_and_capped() -> None:
    adjusted = cav.holm({"x": 0.01, "y": 0.04, "z": 0.5})

    assert adjusted == {"x": pytest.approx(0.03), "y": pytest.approx(0.08), "z": pytest.approx(0.5)}


def test_main_keeps_failed_replays_as_results(tmp_path) -> None:
    base = tmp_path / "base.csv"
    _base_rows([1, 0, 1, 0]).to_csv(base, index=False)
    replay_a = tmp_path / "a.csv"
    _replay_rows([None, 0, 1, 0], ["timeout", "success", "success", "success"]).to_csv(replay_a, index=False)
    replay_b = tmp_path / "b.csv"
    _replay_rows([None, 1, 1, 0], ["timeout", "success", "success", "analysis_error"]).to_csv(replay_b, index=False)

    cav.main(
        [
            "--base-csv", str(base),
            "--replay", f"180={replay_a}",
            "--replay", f"190={replay_b}",
            "--output-dir", str(tmp_path / "out"),
        ]
    )

    stats = json.loads((tmp_path / "out" / "autoware_versions_stats.json").read_text())
    paired = pd.read_csv(tmp_path / "out" / "autoware_versions_paired_data.csv")
    assert len(paired) == 4
    assert stats["collision"]["complete_cases"] == 2
    assert stats["failures"]["overlap_180_190"]["shared_failure_states"] == {"timeout/timeout": 1}
    assert stats["collision"]["sensitivity_all_cases"]["180"]["failures_as_collision"] == pytest.approx(0.5)


def test_main_attaches_reextracted_kinematics(tmp_path) -> None:
    collisions = [0] * 12 + [1] * 3
    base = tmp_path / "base.csv"
    _base_rows(collisions).to_csv(base, index=False)
    replay = tmp_path / "a.csv"
    _replay_rows(collisions, ["success"] * len(collisions)).to_csv(replay, index=False)
    kinematics = tmp_path / "kin.csv"
    rows = []
    for experiment, shift in (("exp171", 0.0), ("exp180", -0.1)):
        for case in range(1, len(collisions) + 1):
            rows.append(
                {
                    "experiment": experiment,
                    "source_loop_num": case,
                    "min_ttc": float("inf") if case % 5 == 0 else 1.0 + shift,
                    "min_distance": 1.0 + case * 0.1 + shift,
                    "min_ttb": -1.0,
                    "z_margin": 0.5,
                    "kinematics_c_collision": collisions[case - 1],
                }
            )
    pd.DataFrame(rows).to_csv(kinematics, index=False)

    cav.main(
        [
            "--base-csv", str(base),
            "--replay", f"180={replay}",
            "--kinematics-csv", str(kinematics),
            "--kinematics-experiment", "171=exp171",
            "--kinematics-experiment", "180=exp180",
            "--output-dir", str(tmp_path / "out"),
        ]
    )

    stats = json.loads((tmp_path / "out" / "autoware_versions_stats.json").read_text())
    distance = stats["kinematics"]["paired"]["min_distance|all|171->180"]
    assert distance["n"] == 15
    assert distance["median_diff"] == pytest.approx(-0.1)
    assert distance["share_lower_after"] == 1.0
    assert stats["kinematics"]["extractor_vs_maude"]["180"]["agree_rate"] == 1.0
    assert (tmp_path / "out" / "kinematics_distributions.png").exists()


def test_severity_levels_count_violated_thresholds_and_collision() -> None:
    frame = pd.DataFrame(
        {
            "c_collision_x": [0, 0, 1],
            **{f"{column}_x": [0, 1 if column in ("c_ttc_1.5", "c_ttc_1.3") else 0, 1] for column in cav.TTC_COLUMNS},
        }
    )

    assert cav.severity_levels(frame, "x").tolist() == [0, 2, cav.COLLISION_LEVEL]
