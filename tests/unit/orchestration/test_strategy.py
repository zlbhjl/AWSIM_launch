from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pandas as pd

import orchestration.binomial_mode as binomial_mode_module
import orchestration.dkw_mode as dkw_mode_module
import orchestration.strategy as strategy_module
from contracts.statistics import StatisticalReport
from orchestration.strategy import (
    ActiveLearningStrategist,
    FixedCaseStrategy,
    FixedCaseStrategyConfig,
    ParameterCaseStrategy,
    ParameterCaseStrategyConfig,
)
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
    assert strategist.binomial_target == "c_collision"
    assert strategist.binomial_method == "wilson"
    assert strategist.binomial_confidence == 0.95


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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
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
        strategy_module.point_extractors.EXTRACTORS,
        "verify_consistency",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )

    def fake_classify(df, param_names, target_metric="min_ttc", threshold=0.2, min_repeats=2):
        classification_inputs.append(df.copy())
        consistent = df.iloc[[0]].copy()
        stochastic = df.iloc[[1]].copy()
        return consistent, stochastic

    monkeypatch.setattr(strategy_module.point_extractors, "classify_consistency", fake_classify)

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

    monkeypatch.setattr(
        strategy_module.point_extractors,
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
        strategy_module.point_extractors.EXTRACTORS,
        "boundary_gap",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )
    monkeypatch.setattr(
        strategy_module.point_extractors,
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
        strategy_module.point_extractors.EXTRACTORS,
        "boundary_gap",
        lambda _df, _param_names, _config: [{"dx0": 15.0, "ego_speed": 35.0}],
    )
    monkeypatch.setattr(
        strategy_module.point_extractors,
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
        strategy_module.point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        strategy_module.point_extractors,
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
        strategy_module.point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        strategy_module.point_extractors,
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
        strategy_module.point_extractors.EXTRACTORS,
        "boundary_gap",
        fake_extract,
    )
    monkeypatch.setattr(
        strategy_module.point_extractors,
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


def test_active_learning_strategist_binomial_ci_stops_when_interval_is_sufficient() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "c_collision": 0}])

    class SamplesRepo:
        def __init__(self):
            self.saved_binomial_samples = None

        def save_binomial_ci_samples(self, df):
            self.saved_binomial_samples = df.copy()
            return True

    class FakeBinomialService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="binomial_ci",
                metric="c_collision",
                sample_count=120,
                estimate=0.1,
                interval=(0.05, 0.06),
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "filtered_df": pd.DataFrame([{"dx0": 13.0, "c_collision": 0}]),
                },
            )

    samples_repo = SamplesRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            BINOMIAL_CI_TARGET="c_collision",
            BINOMIAL_CI_TARGET_WIDTH=0.02,
            BINOMIAL_CI_MIN_SAMPLES=100,
        ),
        dataset_repository=Repo(),
        statistical_samples_repository=samples_repo,
        binomial_ci_service=FakeBinomialService(),
        run_mode="binomial_ci",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["system_command"] == "stop"
    assert "Binomial CI Complete" in payload["reason"]
    assert strategist.latest_final_report is not None
    assert strategist.latest_final_report.target == "Binomial CI"
    assert samples_repo.saved_binomial_samples is not None
    assert list(samples_repo.saved_binomial_samples["dx0"]) == [13.0]


def test_active_learning_strategist_binomial_ci_writes_history_record() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 3, "c_collision": 0}])

    class HistoryRepo:
        def __init__(self):
            self.binomial_records: list[dict[str, object]] = []

        def append_binomial_ci_record(self, record):
            self.binomial_records.append(dict(record))

    class FakeBinomialService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="binomial_ci",
                metric="c_collision",
                sample_count=120,
                estimate=0.1,
                interval=(0.05, 0.06),
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "method": "wilson",
                    "success_count": 12,
                },
            )

    history_repo = HistoryRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            BINOMIAL_CI_TARGET="c_collision",
            BINOMIAL_CI_METHOD="wilson",
            BINOMIAL_CI_TARGET_WIDTH=0.02,
            BINOMIAL_CI_MIN_SAMPLES=100,
        ),
        dataset_repository=Repo(),
        statistical_history_repository=history_repo,
        binomial_ci_service=FakeBinomialService(),
        run_mode="binomial_ci",
        num_candidates=5,
    )

    strategist.decide_next_target()

    assert history_repo.binomial_records == [
        {
            "task_count": 3,
            "metric": "c_collision",
            "method": "wilson",
            "confidence_level": 0.95,
            "sample_size": 120,
            "success_count": 12,
            "estimate": 0.1,
            "lower_bound": 0.05,
            "upper_bound": 0.06,
            "interval_width": 0.009999999999999995,
            "target_width": 0.02,
        }
    ]


def test_active_learning_strategist_binomial_ci_requests_more_samples_when_needed() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "c_collision": 0}])

    class FakeBinomialService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="binomial_ci",
                metric="c_collision",
                sample_count=12,
                estimate=0.1,
                interval=(0.0, 0.2),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            BINOMIAL_CI_TARGET="c_collision",
            BINOMIAL_CI_TARGET_WIDTH=0.02,
            BINOMIAL_CI_MIN_SAMPLES=100,
        ),
        dataset_repository=Repo(),
        binomial_ci_service=FakeBinomialService(),
        run_mode="binomial_ci",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"] == "BINOMIAL_CI: Sampling (13)"
    assert 10.0 <= payload["dx0"] <= 20.0


def test_active_learning_strategist_binomial_ci_uses_region_aware_rejection_sampling(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "c_collision": 0}])

    class FakeBinomialService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="binomial_ci",
                metric="c_collision",
                sample_count=12,
                estimate=0.1,
                interval=(0.0, 0.2),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    monkeypatch.setattr(
        binomial_mode_module,
        "build_theory_metrics",
        lambda **kwargs: {
            "theory_margin_a_human": (
                1.0 if float(kwargs["values"]["dx0"]) >= 15.0 else -1.0
            )
        },
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={
                "dx0": (10.0, 20.0),
                "ego_speed": (30.0, 40.0),
                "npc_speed": (10.0, 20.0),
            },
            BINOMIAL_CI_TARGET="c_collision",
            BINOMIAL_CI_TARGET_WIDTH=0.02,
            BINOMIAL_CI_MIN_SAMPLES=100,
        ),
        dataset_repository=Repo(),
        binomial_ci_service=FakeBinomialService(),
        run_mode="binomial_ci",
        dkw_region="jama_safe",
        num_candidates=5,
    )
    strategist.get_random_point = lambda index: {
        1: {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0},
        2: {"dx0": 18.0, "ego_speed": 35.0, "npc_speed": 15.0},
    }[index]

    payload = strategist.decide_next_target()

    assert payload["reason"] == "BINOMIAL_CI: Sampling (13)"
    assert payload["dx0"] == 18.0
    assert strategist.binomial_random_index == 2


def test_active_learning_strategist_dkw_stops_when_report_is_sufficient() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "min_ttc": 1.0}])

    class FakeDKWService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="dkw",
                metric="min_ttc",
                sample_count=80,
                estimate=1.0,
                interval=(0.95, 1.05),
                sufficient=True,
                next_action="stop",
                diagnostics={"status": "success"},
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        dkw_service=FakeDKWService(),
        run_mode="dkw",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["system_command"] == "stop"
    assert payload["reason"] == "SMC Verification Complete"


def test_active_learning_strategist_dkw_writes_history_record() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 4, "min_ttc": 1.0}])

    class HistoryRepo:
        def __init__(self):
            self.dkw_records: list[dict[str, object]] = []

        def append_dkw_record(self, record):
            self.dkw_records.append(dict(record))

    class FakeDKWService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="dkw",
                metric="min_ttc",
                sample_count=80,
                estimate=1.0,
                interval=(0.95, 1.05),
                sufficient=True,
                next_action="stop",
                diagnostics={"status": "success"},
            )

    history_repo = HistoryRepo()
    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        statistical_history_repository=history_repo,
        dkw_service=FakeDKWService(),
        run_mode="dkw",
        num_candidates=5,
    )

    strategist.decide_next_target()

    assert history_repo.dkw_records == [
        {
            "stage": 1,
            "task_count": 4,
            "metric": "min_ttc",
            "ess": 80,
            "estimate": 1.0,
            "lower_bound": 0.95,
            "upper_bound": 1.05,
            "interval_width": 0.10000000000000009,
            "target_epsilon": 0.15,
        }
    ]


def test_active_learning_strategist_dkw_requests_more_samples_when_needed() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "min_ttc": 1.0}])

    class FakeDKWService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="dkw",
                metric="min_ttc",
                sample_count=20,
                estimate=1.0,
                interval=(0.5, 1.5),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        dkw_service=FakeDKWService(),
        run_mode="dkw",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"] == "SMC: Sequential-DKW Sampling (Stage 1)"
    assert 10.0 <= payload["dx0"] <= 20.0


def test_active_learning_strategist_auto_derives_dkw_bounds_from_region() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {
                        "loop_num": 1,
                        "dx0": 11.0,
                        "ego_speed": 31.0,
                        "npc_speed": 13.0,
                        "c_collision": 0,
                        "theory_margin_a_human": 0.2,
                    },
                    {
                        "loop_num": 2,
                        "dx0": 14.0,
                        "ego_speed": 34.0,
                        "npc_speed": 16.0,
                        "c_collision": 0,
                        "theory_margin_a_human": 0.5,
                    },
                    {
                        "loop_num": 3,
                        "dx0": 19.0,
                        "ego_speed": 39.0,
                        "npc_speed": 18.0,
                        "c_collision": 1,
                        "theory_margin_a_human": 0.5,
                    },
                ]
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={
                "dx0": (10.0, 20.0),
                "ego_speed": (30.0, 40.0),
                "npc_speed": (10.0, 20.0),
            },
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        run_mode="dkw",
        dkw_region="intersect_safe",
        num_candidates=5,
    )

    assert strategist.dkw_bounds == {
        "dx0": (11.0, 14.0),
        "ego_speed": (31.0, 34.0),
        "npc_speed": (13.0, 16.0),
    }
    assert strategist.active_bounds == strategist.dkw_bounds


def test_active_learning_strategist_dkw_uses_region_aware_rejection_sampling(
    monkeypatch,
) -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "min_ttc": 1.0}])

    class FakeDKWService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="dkw",
                metric="min_ttc",
                sample_count=20,
                estimate=1.0,
                interval=(0.5, 1.5),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    monkeypatch.setattr(
        dkw_mode_module,
        "build_theory_metrics",
        lambda **kwargs: {
            "theory_margin_a_human": (
                1.0 if float(kwargs["values"]["dx0"]) >= 15.0 else -1.0
            )
        },
    )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={
                "dx0": (10.0, 20.0),
                "ego_speed": (30.0, 40.0),
                "npc_speed": (10.0, 20.0),
            },
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        dkw_service=FakeDKWService(),
        run_mode="dkw",
        dkw_region="jama_safe",
        num_candidates=5,
    )
    strategist.get_random_point = lambda index: {
        1: {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0},
        2: {"dx0": 18.0, "ego_speed": 35.0, "npc_speed": 15.0},
    }[index]

    payload = strategist.decide_next_target()

    assert payload["reason"] == "SMC: Sequential-DKW Sampling (Stage 1)"
    assert payload["dx0"] == 18.0
    assert strategist.dkw_random_index == 2


def test_active_learning_strategist_dkw_fixed_pure_smc_syncs_only_smc_rows() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {"loop_num": 101, "reason": "STEP1: Global Search (V:0)"},
                    {"loop_num": 102, "reason": "STEP2: Boundary 0.5"},
                    {"loop_num": 3, "reason": "SMC: Fixed Sampling (1/2000)"},
                ]
            )

    strategist = ActiveLearningStrategist(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=50,
        ),
        dataset_repository=Repo(),
        run_mode="dkw_fixed",
        dkw_pure_smc=True,
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["reason"] == "SMC: Fixed Sampling (4/2000)"


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
