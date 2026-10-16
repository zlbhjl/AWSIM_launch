from types import SimpleNamespace

import pandas as pd

from contracts.statistics import StatisticalReport
from orchestration.sprt_mode import SPRTModeRunner, SPRTModeState


def _make_runner(**overrides) -> SPRTModeRunner:
    class HistoryRepo:
        def append_sprt_record(self, _record):
            return None

    class SamplesRepo:
        def __init__(self):
            self.saved_sprt_samples = None

        def save_sprt_samples(self, df):
            self.saved_sprt_samples = df.copy()
            return True

    class FakeSPRTService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="sprt",
                metric="c_collision",
                sample_count=12,
                estimate=0.1,
                interval=None,
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success", "verdict": "continue"},
            )

    return SPRTModeRunner.from_runtime_config(
        param_names=overrides.get("param_names", ["dx0", "ego_speed", "npc_speed"]),
        target=overrides.get("target", "c_collision"),
        p0=overrides.get("p0", 0.1),
        p1=overrides.get("p1", 0.01),
        beta=overrides.get("beta", 0.05),
        confidence=overrides.get("confidence", 0.95),
        min_samples=overrides.get("min_samples", 0),
        max_samples=overrides.get("max_samples", 1000),
        region=overrides.get("region", "custom"),
        sprt_service=overrides.get("sprt_service", FakeSPRTService()),
        statistical_history_repository=overrides.get("history_repo", HistoryRepo()),
        statistical_samples_repository=overrides.get("samples_repo", SamplesRepo()),
        config=overrides.get("config", SimpleNamespace()),
        case_kind=overrides.get("case_kind", "uturn"),
        config_module_name=overrides.get("config_module_name", None),
        region_policy=overrides.get("region_policy"),
    )


def test_sprt_mode_runner_stops_when_verdict_reached() -> None:
    class SamplesRepo:
        def __init__(self):
            self.saved_sprt_samples = None

        def save_sprt_samples(self, df):
            self.saved_sprt_samples = df.copy()
            return True

    class StopSPRTService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="sprt",
                metric="c_collision",
                sample_count=42,
                estimate=0.02,
                interval=None,
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "verdict": "h0",
                    "filtered_df": pd.DataFrame([{"dx0": 13.0, "c_collision": 0}]),
                },
            )

    samples_repo = SamplesRepo()
    runner = _make_runner(sprt_service=StopSPRTService(), samples_repo=samples_repo)
    state = SPRTModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "c_collision": 0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert "SPRT Complete" in payload["reason"]
    assert "verdict=h0" in payload["reason"]
    assert samples_repo.saved_sprt_samples is not None
    assert list(samples_repo.saved_sprt_samples["dx0"]) == [13.0]


def test_sprt_mode_runner_requests_more_samples_when_undecided() -> None:
    runner = _make_runner()
    state = SPRTModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "c_collision": 0}]),
        state=state,
        get_random_point=lambda index: {
            "dx0": 10.0 + index,
            "ego_speed": 35.0,
            "npc_speed": 15.0,
        },
    )

    assert payload["reason"] == "SPRT: Sampling (13)"
    assert state.random_index == 1
    assert state.dispatched_task_count == 2


def test_sprt_mode_runner_stops_at_max_samples_without_decision() -> None:
    class MaxSamplesSPRTService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="sprt",
                metric="c_collision",
                sample_count=1000,
                estimate=0.05,
                interval=None,
                sufficient=False,
                next_action="stop_max_samples",
                diagnostics={"status": "success", "verdict": "continue"},
            )

    runner = _make_runner(sprt_service=MaxSamplesSPRTService())
    state = SPRTModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "c_collision": 0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert "max_samples=1000" in payload["reason"]


def test_sprt_mode_runner_uses_region_aware_rejection_sampling() -> None:
    class ThresholdRegionPolicy:
        def build_filter_frame(self, point):
            row = dict(point)
            row["theory_margin_a_human"] = (
                1.0 if float(point.get("dx0", 0.0)) >= 15.0 else -1.0
            )
            return pd.DataFrame([row])

    runner = _make_runner(
        region="jama_safe",
        region_policy=ThresholdRegionPolicy(),
    )

    payload, random_index, dispatched_count = runner.issue_region_aware_random_task(
        reason="SPRT: Sampling (13)",
        get_random_point=lambda index: {
            1: {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0},
            2: {"dx0": 18.0, "ego_speed": 35.0, "npc_speed": 15.0},
        }[index],
        random_index=1,
        base_index=0,
        dispatched_task_count=0,
    )

    assert payload["dx0"] == 18.0
    assert random_index == 3
    assert dispatched_count == 1


def test_sprt_mode_runner_writes_history_record() -> None:
    class HistoryRepo:
        def __init__(self):
            self.records = []

        def append_sprt_record(self, record):
            self.records.append(dict(record))

    class StopSPRTService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="sprt",
                metric="c_collision",
                sample_count=120,
                estimate=0.02,
                interval=None,
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "verdict": "h1",
                    "log_likelihood_ratio": 3.2,
                    "lower_log_threshold": -2.9,
                    "upper_log_threshold": 3.0,
                    "p0": 0.1,
                    "p1": 0.01,
                    "alpha": 0.05,
                    "beta": 0.05,
                },
            )

    history_repo = HistoryRepo()
    runner = _make_runner(
        sprt_service=StopSPRTService(),
        history_repo=history_repo,
    )
    state = SPRTModeState(dispatched_task_count=3)

    runner.handle(
        pd.DataFrame([{"loop_num": 3, "c_collision": 0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert history_repo.records == [
        {
            "task_count": 3,
            "metric": "c_collision",
            "verdict": "h1",
            "sample_size": 120,
            "estimate": 0.02,
            "log_likelihood_ratio": 3.2,
            "lower_log_threshold": -2.9,
            "upper_log_threshold": 3.0,
            "p0": 0.1,
            "p1": 0.01,
            "alpha": 0.05,
            "beta": 0.05,
        }
    ]
