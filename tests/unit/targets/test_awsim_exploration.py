"""AWSIM's exploration spec keeps the GP training rules gp_boundary used to embed."""

import pandas as pd

from evaluation.gp_boundary import prepare_training_data_frame
from orchestration.exploration.active_learning import ActiveLearningStrategist as Shared
from targets.awsim.exploration import AWSIM_EXPLORATION
from targets.awsim.strategies.active_learning import ActiveLearningStrategist


def _frame():
    return pd.DataFrame({
        "loop_num": [1, 2, 3, 4, 5],
        "dx0": [10.0, 11.0, 12.0, 13.0, 14.0],
        "c_collision": [1, 0, -1, 0, 0],
        "c_ttc_1.0": [0, 1, 0, 0, 0],
        "c_ttc_2.0": [0, 0, 0, 1, 0],
        "min_ttc": [0.0, 1.2, -1, 3.0, 4.0],
    })


def test_awsim_rules_drop_failed_judgements_and_propagate_ttc_labels() -> None:
    rows = prepare_training_data_frame(_frame(), target_column="c_ttc_2.0", feature_names=["dx0"],
                                       training_rules=AWSIM_EXPLORATION.training_rules)
    assert rows["loop_num"].tolist() == [1, 2, 4, 5]       # the -1 judgement is dropped
    assert rows["c_ttc_2.0"].tolist() == [1, 1, 1, 0]      # collision and c_ttc_1.0 imply c_ttc_2.0


def test_without_rules_rows_are_used_as_they_are() -> None:
    rows = prepare_training_data_frame(_frame(), target_column="c_ttc_2.0", feature_names=["dx0"])
    assert rows["c_ttc_2.0"].tolist() == [0, 0, 0, 1, 0]


def test_awsim_failed_runs_and_strategist_default_spec() -> None:
    assert AWSIM_EXPLORATION.failed_rows(_frame()).tolist() == [False, False, True, False, False]
    assert issubclass(ActiveLearningStrategist, Shared)
