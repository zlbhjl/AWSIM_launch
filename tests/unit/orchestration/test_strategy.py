from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pandas as pd

from targets.awsim.statistical import AWSIM_REGION_VOCABULARY
import pytest

import orchestration.exploration.active_learning as strategy_module
from targets.awsim import point_rules as point_extractors  # AWSIM's point rules (the spec uses this module)
from contracts.statistics import StatisticalReport
from orchestration.strategy import (
    FixedCaseStrategy,
    FixedCaseStrategyConfig,
    ParameterCaseStrategy,
    ParameterCaseStrategyConfig,
)
from targets.awsim.strategies.active_learning import ActiveLearningStrategist
from targets.awsim.strategies.replay import ReplayCsvStrategy, ReplayCsvStrategyConfig
from runtime.repository.consistency_classification import (
    ConsistencyClassificationRepository,
)


def test_fixed_case_strategy_returns_one_test_case() -> None:
    strategy = FixedCaseStrategy(
        FixedCaseStrategyConfig(
            fixture="tests/fixtures/awsim/timeout_trace.txt",
            tags=["smoke"],
        )
    )

    test_case = strategy.next_test_case()

    assert test_case is not None
    assert test_case.case_id == "timeout_trace"
    assert test_case.target == "awsim"
    assert test_case.case_kind == "uturn"
    assert test_case.input["fixture_path"].endswith("timeout_trace.txt")
    assert test_case.tags == ["smoke"]
    assert test_case.meta["source_module"] == "orchestration.strategy"


def test_fixed_case_strategy_returns_none_after_first_case() -> None:
    strategy = FixedCaseStrategy(
        FixedCaseStrategyConfig(fixture="tests/fixtures/awsim/timeout_trace.txt")
    )

    assert strategy.next_test_case() is not None
    assert strategy.next_test_case() is None


def test_parameter_case_strategy_returns_one_test_case() -> None:
    strategy = ParameterCaseStrategy(
        ParameterCaseStrategyConfig(
            params={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            tags=["direct"],
            simulation_output_dir="/tmp/awsim-output",
            local_loop_num=3,
        )
    )

    test_case = strategy.next_test_case()

    assert test_case is not None
    assert test_case.case_id == "uturn_direct"
    assert test_case.input["dx0"] == 15.0
    assert test_case.input["scenario_type"] == "uturn"
    assert test_case.input["output_dir"] == "/tmp/awsim-output"
    assert test_case.input["local_loop_num"] == 3
    assert test_case.tags == ["direct"]
    assert test_case.meta["source_module"] == "orchestration.strategy"


def test_parameter_case_strategy_returns_none_after_first_case() -> None:
    strategy = ParameterCaseStrategy(
        ParameterCaseStrategyConfig(params={"dx0": 15.0})
    )

    assert strategy.next_test_case() is not None
    assert strategy.next_test_case() is None


def test_replay_csv_strategy_keeps_only_binomial_success_rows(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "loop_num,status,reason,c_collision,case_id,ego_init_lane,acceleration,dx0\n"
        "1,success,BINOMIAL_CI: Sampling (1),0,old_1,514,7.0,12.5\n"
        "2,success,[FOCUS] Error Recovery,1,old_2,514,7.0,13.5\n"
        "3,timeout,BINOMIAL_CI: Sampling (2),0,old_3,514,7.0,14.5\n"
        "4,success,BINOMIAL_CI: Sampling (3),-1,old_4,514,7.0,15.5\n"
        "5,success,BINOMIAL_CI: Sampling (4),1,old_5,282,7.0,16.5\n",
        encoding="utf-8",
    )
    strategy = ReplayCsvStrategy(
        ReplayCsvStrategyConfig(
            source_csv=source,
            input_types={"ego_init_lane": "514", "acceleration": 7.0, "dx0": 10.0},
            expected_count=2,
        )
    )

    first = strategy.next_test_case()
    second = strategy.next_test_case()

    assert strategy.eligible_count == 2
    assert first is not None
    assert first.input == {
        "ego_init_lane": "514",
        "acceleration": 7.0,
        "dx0": 12.5,
        "scenario_type": "uturn",
    }
    assert first.meta["replay_source_loop_num"] == 1
    assert first.meta["replay_source_collision"] == 0
    assert second is not None
    assert second.meta["replay_source_loop_num"] == 5
    assert second.meta["replay_source_collision"] == 1
    assert strategy.next_test_case() is None


def test_replay_csv_strategy_skips_completed_source_loops(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "loop_num,status,reason,c_collision,dx0\n"
        "10,success,BINOMIAL_CI: Sampling (1),0,12.5\n"
        "20,success,BINOMIAL_CI: Sampling (2),1,15.0\n",
        encoding="utf-8",
    )
    strategy = ReplayCsvStrategy(
        ReplayCsvStrategyConfig(
            source_csv=source,
            input_types={"dx0": 10.0},
            expected_count=2,
            completed_source_loop_nums=frozenset({10}),
        )
    )

    test_case = strategy.next_test_case()

    assert strategy.skipped_completed_count == 1
    assert test_case is not None
    assert test_case.meta["replay_source_loop_num"] == 20
    assert strategy.next_test_case() is None


def test_replay_csv_strategy_rejects_unexpected_eligible_count(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "loop_num,status,reason,c_collision,dx0\n"
        "1,success,BINOMIAL_CI: Sampling (1),0,12.5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected 9066, found 1"):
        ReplayCsvStrategy(
            ReplayCsvStrategyConfig(
                source_csv=source,
                input_types={"dx0": 10.0},
                expected_count=9066,
            )
        )


def test_active_learning_strategist_prefers_grouped_case_definition_and_settings() -> None:
    config = SimpleNamespace(
        get_case_definition=lambda: {
            "scenario_type": "uturn",
            "repeat_count": 10,
            "timeout_sec": 120.0,
            "target_npcs": ["npc1"],
            "param_ranges": {
                "dx0": (10.0, 20.0),
                "ego_speed": (30.0, 40.0),
            },
            "fixed_params": {"ego_init_lane": "514"},
        },
        get_strategy_settings=lambda: {
            "target_priorities": ["c_collision"],
            "initial_exploration_limit": 11,
            "min_samples": 22,
            "max_samples": 33,
            "stability_reference_points": 7,
            "stability_history_length": 8,
            "stability_hysteresis": (0.2, 0.8),
            "stability_shift_threshold": 0.05,
            "stability_required_streak": 4,
            "step2_max_exploration": 55,
            "margin_range": (0.25, 0.45),
            "margin_max_uncertainty": 0.07,
            "focus_noise": 0.15,
        },
    )

    strategist = ActiveLearningStrategist("uturn", config, num_candidates=5)

    assert strategist.param_ranges == {
        "dx0": (10.0, 20.0),
        "ego_speed": (30.0, 40.0),
    }
    assert strategist.param_names == ["dx0", "ego_speed"]
    assert strategist.target_priorities == ["c_collision"]
    assert strategist.INITIAL_EXPLORATION_LIMIT == 11
    assert strategist.MAX_SAMPLES == 33
    assert strategist.FOCUS_NOISE == 0.15
    assert strategist.dkw_target_metric == "min_ttc"
    assert strategist.dkw_target_metrics == ["min_ttc", "min_distance"]
    # The statistical settings are read by the region sampling (test_region_sampling.py).


def test_active_learning_strategist_explore_returns_global_search_when_dataset_empty() -> None:
    class Repo:
        def load_dataset(self):
            return None

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"].startswith("STEP1: Global Search")
    assert 10.0 <= payload["dx0"] <= 20.0
    assert 30.0 <= payload["ego_speed"] <= 40.0


def test_active_learning_strategist_focus_returns_exact_points_first() -> None:
    class Repo:
        def load_dataset(self):
            return None

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        focus_points=[{"dx0": 15.0, "ego_speed": 35.0}],
        run_mode="focus",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["dx0"] == 15.0
    assert second["dx0"] == 15.0
    assert first["reason"] == "[FOCUS] Exact Point 1/1 (Repeat 1/2)"
    assert second["reason"] == "[FOCUS] Exact Point 1/1 (Repeat 2/2)"


def test_active_learning_strategist_recovers_new_timeout_row_before_normal_sampling() -> None:
    class Repo:
        def __init__(self):
            self.df = pd.DataFrame(
                [
                    {
                        "loop_num": 1,
                        "dx0": 12.0,
                        "ego_speed": 32.0,
                        "c_collision": 0,
                        "min_ttc": 1.2,
                    }
                ]
            )

        def load_dataset(self):
            return self.df

    repo = Repo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=repo,
        num_candidates=5,
        random_seed=7,
    )

    repo.df = pd.DataFrame(
        [
            {
                "loop_num": 1,
                "dx0": 12.0,
                "ego_speed": 32.0,
                "c_collision": 0,
                "min_ttc": 1.2,
            },
            {
                "loop_num": 2,
                "dx0": 15.0,
                "ego_speed": 35.0,
                "c_collision": -1,
                "min_ttc": -1.0,
            },
        ]
    )

    payload = strategist.decide_next_target()

    assert payload["reason"] == "Error Recovery (Shifted from Loop 2)"
    assert 10.0 <= payload["dx0"] <= 20.0
    assert 30.0 <= payload["ego_speed"] <= 40.0
    assert payload["dx0"] != 15.0 or payload["ego_speed"] != 35.0
    assert strategist.last_recovered_loop == 2


def test_active_learning_strategist_does_not_recover_old_timeout_rows_on_startup() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {
                        "loop_num": 7,
                        "dx0": 15.0,
                        "ego_speed": 35.0,
                        "c_collision": -1,
                        "min_ttc": -1.0,
                    }
                ]
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        num_candidates=5,
        random_seed=7,
    )

    payload = strategist.decide_next_target()

    assert strategist.last_recovered_loop == 7
    assert payload["reason"].startswith("STEP1: Global Search")


def test_active_learning_strategist_verify_consistency_uses_extracted_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "verify_consistency",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        run_mode="verify_consistency",
        num_candidates=5,
    )

    first = strategist.decide_next_target()

    assert strategist.FOCUS_POINTS == [{"dx0": 15.0, "ego_speed": 35.0}]
    assert first["reason"] == "[CONSISTENCY] Exact Point 1/1 (Repeat 1/2)"


def test_active_learning_strategist_jama_edge_uses_extracted_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "jama_edge",
        lambda _df, _param_names, _config: [{"dx0": 16.0, "ego_speed": 36.0}],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        run_mode="jama_edge",
        num_candidates=5,
    )

    first = strategist.decide_next_target()

    assert strategist.FOCUS_POINTS == [{"dx0": 16.0, "ego_speed": 36.0}]
    assert first["reason"] == "[FOCUS] Exact Point 1/1 (Repeat 1/2)"


def test_active_learning_strategist_jama_edge_stops_when_no_target_points_exist(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "jama_edge",
        lambda _df, _param_names, _config: [],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        run_mode="jama_edge",
        num_candidates=5,
    )

    assert strategist.decide_next_target() == {
        "system_command": "stop",
        "reason": "No target points found for mode 'jama_edge'",
    }


def test_active_learning_strategist_jama_edge_ignores_config_focus_points_when_empty(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "jama_edge",
        lambda _df, _param_names, _config: [],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_POINTS=[{"dx0": 11.0, "ego_speed": 31.0}],
        ),
        dataset_repository=Repo(),
        run_mode="jama_edge",
        num_candidates=5,
    )

    assert strategist.FOCUS_POINTS == []
    assert strategist.decide_next_target() == {
        "system_command": "stop",
        "reason": "No target points found for mode 'jama_edge'",
    }


def test_active_learning_strategist_ttc_edge_uses_extracted_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "ttc_edge",
        lambda _df, _param_names, _config: [{"dx0": 17.0, "ego_speed": 37.0}],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        run_mode="ttc_edge",
        num_candidates=5,
    )

    first = strategist.decide_next_target()

    assert strategist.FOCUS_POINTS == [{"dx0": 17.0, "ego_speed": 37.0}]
    assert first["reason"] == "[FOCUS] Exact Point 1/1 (Repeat 1/2)"


def test_active_learning_strategist_ttc_edge_stops_when_no_target_points_exist(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "ttc_edge",
        lambda _df, _param_names, _config: [],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        run_mode="ttc_edge",
        num_candidates=5,
    )

    assert strategist.decide_next_target() == {
        "system_command": "stop",
        "reason": "No target points found for mode 'ttc_edge'",
    }


def test_active_learning_strategist_worst_ttc_uses_extracted_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "worst_ttc",
        lambda _df, _param_names, _config: [{"dx0": 18.0, "ego_speed": 38.0}],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        run_mode="worst_ttc",
        num_candidates=5,
    )

    first = strategist.decide_next_target()

    assert strategist.FOCUS_POINTS == [{"dx0": 18.0, "ego_speed": 38.0}]
    assert first["reason"] == "[FOCUS] Exact Point 1/1 (Repeat 1/2)"


def test_active_learning_strategist_worst_ttc_stops_when_no_target_points_exist(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "worst_ttc",
        lambda _df, _param_names, _config: [],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        run_mode="worst_ttc",
        num_candidates=5,
    )

    assert strategist.decide_next_target() == {
        "system_command": "stop",
        "reason": "No target points found for mode 'worst_ttc'",
    }


def test_active_learning_strategist_verify_consistency_stops_when_no_mode_rows_exist(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "verify_consistency",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
        ),
        dataset_repository=Repo(),
        run_mode="verify_consistency",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["reason"] == "[CONSISTENCY] Exact Point 1/1 (Repeat 1/1)"
    assert second == {
        "system_command": "stop",
        "reason": "No consistency data found",
    }


def test_active_learning_strategist_verify_consistency_classifies_and_saves_results(
    monkeypatch,
    tmp_path,
) -> None:
    class Repo:
        def __init__(self):
            self.paths = SimpleNamespace(traces_dir=str(tmp_path))
            self.df = pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

        def load_dataset(self):
            return self.df

    class DKWStub:
        def __init__(self):
            self.sample_sizes: list[int] = []

        def evaluate_request(self, data, request):
            sample_count = 0 if data is None else len(data)
            self.sample_sizes.append(sample_count)
            return StatisticalReport(
                method=request.method,
                metric=request.metric,
                sample_count=sample_count,
                estimate=None,
                interval=None,
                sufficient=False,
                next_action="error" if sample_count == 0 else "stop",
                diagnostics={"status": "success" if sample_count else "error"},
            )

    class ConsistencyRepo:
        def __init__(self):
            self.saved: list[tuple[str, int]] = []

        def save_consistent(self, df):
            self.saved.append(("consistent", len(df)))
            return True

        def save_stochastic(self, df):
            self.saved.append(("stochastic", len(df)))
            return True

    class ConsistencyDkwSummaryRepo:
        def __init__(self):
            self.rows: list[dict[str, object]] = []

        def append_report(self, *, classification, report, confidence_level, target_epsilon):
            self.rows.append(
                {
                    "classification": classification,
                    "sample_count": report.sample_count,
                    "metric": report.metric,
                    "confidence_level": confidence_level,
                    "target_epsilon": target_epsilon,
                    "next_action": report.next_action,
                }
            )

    repo = Repo()
    dkw_stub = DKWStub()
    consistency_repo = ConsistencyRepo()
    consistency_dkw_summary_repo = ConsistencyDkwSummaryRepo()
    classification_inputs: list[pd.DataFrame] = []

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "verify_consistency",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )

    def fake_classify(df, param_names, target_metric="min_ttc", threshold=0.2, min_repeats=2):
        classification_inputs.append(df.copy())
        consistent = df.iloc[[0]].copy()
        stochastic = df.iloc[[1]].copy()
        return consistent, stochastic

    monkeypatch.setattr(point_extractors, "classify_consistency", fake_classify)

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
            CONSISTENCY_THRESHOLD=0.3,
        ),
        dataset_repository=repo,
        consistency_classification_repository=consistency_repo,
        consistency_dkw_summary_repository=consistency_dkw_summary_repo,
        dkw_service=dkw_stub,
        run_mode="verify_consistency",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    assert first["reason"] == "[CONSISTENCY] Exact Point 1/1 (Repeat 1/1)"

    repo.df = pd.DataFrame(
        [
            {
                "loop_num": 1,
                "dx0": 15.0,
                "ego_speed": 35.0,
                "c_collision": 0,
                "min_ttc": 0.8,
                "reason": "[CONSISTENCY] Exact Point 1/1 (Repeat 1/1)",
            },
            {
                "loop_num": 2,
                "dx0": 15.0,
                "ego_speed": 35.0,
                "c_collision": 0,
                "min_ttc": 0.9,
                "reason": "[CONSISTENCY] Exact Point 1/1 (Repeat 1/1)",
            },
            {
                "loop_num": 3,
                "dx0": 18.0,
                "ego_speed": 38.0,
                "c_collision": 0,
                "min_ttc": 1.5,
                "reason": "STEP1: Global Search (V:0)",
            },
        ]
    )

    completion = strategist.decide_next_target()

    assert completion == {
        "system_command": "stop",
        "reason": "Consistency Verification Complete",
    }
    assert len(classification_inputs) == 1
    assert list(classification_inputs[0]["loop_num"]) == [1, 2]
    assert consistency_repo.saved == [("consistent", 1), ("stochastic", 1)]
    assert consistency_dkw_summary_repo.rows == [
        {
            "classification": "consistent",
            "sample_count": 1,
            "metric": "min_ttc",
            "confidence_level": 0.95,
            "target_epsilon": 0.15,
            "next_action": "stop",
        },
        {
            "classification": "stochastic",
            "sample_count": 1,
            "metric": "min_ttc",
            "confidence_level": 0.95,
            "target_epsilon": 0.15,
            "next_action": "stop",
        },
    ]
    assert dkw_stub.sample_sizes == [1, 1]


def test_consistency_classification_repository_saves_expected_paths(
    tmp_path,
    monkeypatch,
) -> None:
    saved_paths: list[tuple[int, str]] = []
    repo = ConsistencyClassificationRepository(
        scenario_name="uturn",
        traces_dir=tmp_path,
    )
    df = pd.DataFrame([{"dx0": 15.0, "min_ttc": 0.8}])

    import runtime.repository.consistency_classification as consistency_module

    monkeypatch.setattr(
        consistency_module,
        "save_dataframe_to_csv",
        lambda frame, output_path, success_msg=None: saved_paths.append((len(frame), output_path)) or True,
    )

    assert repo.save_consistent(df) is True
    assert repo.save_stochastic(df) is True
    assert {Path(path).name for _, path in saved_paths} == {
        "uturn_consistent_risk.csv",
        "uturn_stochastic_risk.csv",
    }


def test_active_learning_strategist_margin_falls_back_when_no_violations_exist() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0, "c_collision": 0},
                    {"loop_num": 2, "dx0": 13.0, "ego_speed": 33.0, "c_collision": 0},
                ]
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        run_mode="margin",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"] == "STEP3 Fallback: Global Search (No Violations)"


def test_active_learning_strategist_next_test_case_builds_awsim_case() -> None:
    class Repo:
        def load_dataset(self):
            return None

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            FIXED_PARAMS={"ego_init_lane": "514"},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        num_candidates=5,
    )

    test_case = strategist.next_test_case()

    assert test_case is not None
    assert test_case.case_kind == "uturn"
    assert test_case.target == "awsim"
    assert test_case.input["ego_init_lane"] == "514"
    assert "dx0" in test_case.input
    assert test_case.input["scenario_type"] == "uturn"
    assert test_case.meta["run_mode"] == "explore"


def test_active_learning_strategist_margin_uses_gp_predictions_after_violations_exist(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0, "c_collision": 0},
                    {"loop_num": 2, "dx0": 18.0, "ego_speed": 38.0, "c_collision": 1},
                ]
            )

    class StubBoundaryModel:
        def predict_uncertainty(self, x_new):
            mean = np.full((len(x_new),), 0.4)
            std = np.full((len(x_new),), 0.2)
            return mean, std

    monkeypatch.setattr(
        strategy_module,
        "fit_gp_boundary_model",
        lambda *args, **kwargs: StubBoundaryModel(),
    )
    monkeypatch.setattr(
        strategy_module,
        "predict_gp_boundary_uncertainty",
        lambda model, x_new: model.predict_uncertainty(x_new),
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            MARGIN_MAX_UNCERTAINTY=0.05,
        ),
        dataset_repository=Repo(),
        run_mode="margin",
        num_candidates=10,
        cache_size=2,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"].startswith("STEP3: Safe Area Cleanup")


def test_active_learning_strategist_boundary_gap_uses_extracted_focus_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    class ProgressRepo:
        def __init__(self):
            self.snapshots: list[dict[str, object]] = []

        def append_snapshot(self, *, cycle, summary, snapshot_kind):
            self.snapshots.append(
                {
                    "cycle": cycle,
                    "summary": dict(summary),
                    "snapshot_kind": snapshot_kind,
                }
            )

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "boundary_gap",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )
    monkeypatch.setattr(
        point_extractors,
        "summarize_boundary_gap_progress",
        lambda _df, _param_names, _config, target_points=None: {
            "candidate_cells": 1,
            "target_cells": list(target_points or []),
        },
    )

    progress_repo = ProgressRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=2,
        ),
        dataset_repository=Repo(),
        boundary_gap_progress_repository=progress_repo,
        run_mode="boundary_gap",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert strategist.FOCUS_POINTS == [{"dx0": 15.0, "ego_speed": 35.0}]
    assert first["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 1/2)"
    assert second["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 2/2)"
    assert progress_repo.snapshots[0]["snapshot_kind"] == "initial"
    assert progress_repo.snapshots[0]["cycle"] == 1


def test_active_learning_strategist_boundary_gap_ignores_config_focus_points(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "boundary_gap",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )
    monkeypatch.setattr(
        point_extractors,
        "summarize_boundary_gap_progress",
        lambda _df, _param_names, _config, target_points=None: {
            "candidate_cells": 1,
            "target_cells": list(target_points or []),
        },
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_POINTS=[{"dx0": 11.0, "ego_speed": 31.0}],
        ),
        dataset_repository=Repo(),
        run_mode="boundary_gap",
        num_candidates=5,
    )

    assert strategist.FOCUS_POINTS == [{"dx0": 15.0, "ego_speed": 35.0}]


def test_active_learning_strategist_boundary_gap_refreshes_targets_after_cycle(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    class ProgressRepo:
        def __init__(self):
            self.snapshots: list[dict[str, object]] = []

        def append_snapshot(self, *, cycle, summary, snapshot_kind):
            self.snapshots.append(
                {
                    "cycle": cycle,
                    "summary": dict(summary),
                    "snapshot_kind": snapshot_kind,
                }
            )

    points_by_call = [
        [{"dx0": 15.0, "ego_speed": 35.0}],
        [{"dx0": 16.0, "ego_speed": 36.0}],
    ]

    def fake_extract(_df, _param_names, _config):
        return points_by_call.pop(0) if points_by_call else []

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        point_extractors,
        "summarize_boundary_gap_progress",
        lambda _df, _param_names, _config, target_points=None: {
            "candidate_cells": 1,
            "target_cells": [{"status": "needs_more_data"} for _ in (target_points or [])],
        },
    )

    progress_repo = ProgressRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
        ),
        dataset_repository=Repo(),
        boundary_gap_progress_repository=progress_repo,
        run_mode="boundary_gap",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 1/1)"
    assert second["reason"] == "[BOUNDARY_GAP] Cycle 2 Point 1/1 (Repeat 1/1)"
    assert second["dx0"] == 16.0
    assert [entry["snapshot_kind"] for entry in progress_repo.snapshots] == [
        "initial",
        "cycle_complete",
        "initial",
    ]
    assert [entry["cycle"] for entry in progress_repo.snapshots] == [1, 1, 2]


def test_active_learning_strategist_boundary_gap_stops_when_no_targets_remain(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    points_by_call = [
        [{"dx0": 15.0, "ego_speed": 35.0}],
        [],
    ]

    def fake_extract(_df, _param_names, _config):
        return points_by_call.pop(0) if points_by_call else []

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        point_extractors,
        "summarize_boundary_gap_progress",
        lambda _df, _param_names, _config, target_points=None: {
            "candidate_cells": 0,
            "target_cells": [{"status": "clarified"} for _ in (target_points or [])],
        },
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
        ),
        dataset_repository=Repo(),
        run_mode="boundary_gap",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 1/1)"
    assert second["system_command"] == "stop"
    assert "boundary_gap" in second["reason"]


def test_active_learning_strategist_boundary_gap_records_cycle_complete_before_stop(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "dx0": 12.0, "ego_speed": 32.0}])

    class ProgressRepo:
        def __init__(self):
            self.snapshots: list[dict[str, object]] = []

        def append_snapshot(self, *, cycle, summary, snapshot_kind):
            self.snapshots.append(
                {
                    "cycle": cycle,
                    "snapshot_kind": snapshot_kind,
                    "candidate_cells": int(summary.get("candidate_cells", 0)),
                }
            )

    points_by_call = [
        [{"dx0": 15.0, "ego_speed": 35.0}],
        [],
    ]

    def fake_extract(_df, _param_names, _config):
        return points_by_call.pop(0) if points_by_call else []

    monkeypatch.setitem(
        point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        point_extractors,
        "summarize_boundary_gap_progress",
        lambda _df, _param_names, _config, target_points=None: {
            "candidate_cells": 0,
            "target_cells": [{"status": "clarified"} for _ in (target_points or [])],
        },
    )

    progress_repo = ProgressRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0)},
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
        ),
        dataset_repository=Repo(),
        boundary_gap_progress_repository=progress_repo,
        run_mode="boundary_gap",
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 1/1)"
    assert second["system_command"] == "stop"
    assert progress_repo.snapshots == [
        {
            "cycle": 1,
            "snapshot_kind": "initial",
            "candidate_cells": 0,
        },
        {
            "cycle": 1,
            "snapshot_kind": "cycle_complete",
            "candidate_cells": 0,
        },
    ]


def test_active_learning_strategist_boundary_gap_stops_after_dataset_growth_resolves_candidate() -> None:
    class Repo:
        def __init__(self):
            initial_rows = [
                {
                    "loop_num": 1,
                    "dx0": 0.5,
                    "ego_speed": 0.5,
                    "npc_speed": 0.5,
                    "c_collision": 0,
                    "min_ttc": 1.0,
                },
                {
                    "loop_num": 2,
                    "dx0": 0.5,
                    "ego_speed": 0.5,
                    "npc_speed": 0.5,
                    "c_collision": 0,
                    "min_ttc": 2.0,
                },
            ]
            grown_rows = initial_rows + [
                {
                    "loop_num": loop_num,
                    "dx0": 0.5,
                    "ego_speed": 0.5,
                    "npc_speed": 0.5,
                    "c_collision": 0,
                    "min_ttc": 1.0,
                }
                for loop_num in range(3, 14)
            ]
            self.frames = [
                pd.DataFrame(initial_rows),
                pd.DataFrame(grown_rows),
            ]
            self.index = 0

        def load_dataset(self):
            frame = self.frames[min(self.index, len(self.frames) - 1)]
            self.index += 1
            return frame.copy()

    class ProgressRepo:
        def __init__(self):
            self.snapshots: list[dict[str, object]] = []

        def append_snapshot(self, *, cycle, summary, snapshot_kind):
            self.snapshots.append(
                {
                    "cycle": cycle,
                    "snapshot_kind": snapshot_kind,
                    "candidate_cells": int(summary.get("candidate_cells", 0)),
                    "densified_cells": int(summary.get("densified_cells", 0)),
                }
            )

    repo = Repo()
    progress_repo = ProgressRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={
                "dx0": (0.0, 8.0),
                "ego_speed": (0.0, 8.0),
                "npc_speed": (0.0, 8.0),
            },
            TARGET_PRIORITIES=["c_collision"],
            FOCUS_EXACT_REPEATS=1,
            BOUNDARY_GAP_GRID_SIZE=8,
            BOUNDARY_GAP_MIN_SAMPLES=2,
            BOUNDARY_GAP_MAX_SAMPLES=12,
            BOUNDARY_GAP_COLLISION_RATIO_RANGE=(0.15, 0.85),
            BOUNDARY_GAP_TTC_THRESHOLD=1.1,
            BOUNDARY_GAP_MAX_CASES=12,
        ),
        dataset_repository=repo,
        boundary_gap_progress_repository=progress_repo,
        run_mode="boundary_gap",
        focus_points=[],
        num_candidates=5,
    )

    first = strategist.decide_next_target()
    second = strategist.decide_next_target()

    assert first["reason"] == "[BOUNDARY_GAP] Cycle 1 Point 1/1 (Repeat 1/1)"
    assert first["dx0"] == 0.5
    assert second["system_command"] == "stop"
    assert "boundary_gap" in second["reason"]
    assert progress_repo.snapshots == [
        {
            "cycle": 1,
            "snapshot_kind": "initial",
            "candidate_cells": 1,
            "densified_cells": 0,
        },
        {
            "cycle": 1,
            "snapshot_kind": "cycle_complete",
            "candidate_cells": 0,
            "densified_cells": 1,
        },
    ]


def test_active_learning_strategist_sync_dispatched_task_count_ignores_invalid_loop_num() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {"loop_num": "not-a-number", "reason": "STEP1: Global Search (V:0)"},
                ]
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            TARGET_PRIORITIES=["c_collision"],
        ),
        dataset_repository=Repo(),
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert "reason" in payload
