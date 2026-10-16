from types import SimpleNamespace

import pandas as pd

from contracts.statistics import StatisticalReport
from orchestration.ebstop_mode import EBStopModeRunner, EBStopModeState


def _make_runner(**overrides) -> EBStopModeRunner:
    class HistoryRepo:
        def append_ebstop_record(self, _record):
            return None

    class SamplesRepo:
        def __init__(self):
            self.saved_ebstop_samples = None

        def save_ebstop_samples(self, df):
            self.saved_ebstop_samples = df.copy()
            return True

    class FakeEBStopService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="ebstop",
                metric="min_ttc",
                sample_count=12,
                estimate=2.0,
                interval=(1.9, 2.1),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    return EBStopModeRunner.from_runtime_config(
        param_names=overrides.get("param_names", ["dx0", "ego_speed", "npc_speed"]),
        target=overrides.get("target", "min_ttc"),
        epsilon=overrides.get("epsilon", 0.1),
        value_range=overrides.get("value_range", 2.0),
        confidence=overrides.get("confidence", 0.95),
        max_samples=overrides.get("max_samples", 1000),
        region=overrides.get("region", "custom"),
        ebstop_service=overrides.get("ebstop_service", FakeEBStopService()),
        statistical_history_repository=overrides.get("history_repo", HistoryRepo()),
        statistical_samples_repository=overrides.get("samples_repo", SamplesRepo()),
        config=overrides.get("config", SimpleNamespace()),
        case_kind=overrides.get("case_kind", "uturn"),
        config_module_name=overrides.get("config_module_name", None),
        region_policy=overrides.get("region_policy"),
    )


def test_ebstop_mode_runner_stops_when_converged() -> None:
    class SamplesRepo:
        def __init__(self):
            self.saved_ebstop_samples = None

        def save_ebstop_samples(self, df):
            self.saved_ebstop_samples = df.copy()
            return True

    class StopEBStopService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="ebstop",
                metric="min_ttc",
                sample_count=398,
                estimate=2.0,
                interval=(1.95, 2.05),
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "filtered_df": pd.DataFrame([{"dx0": 13.0, "min_ttc": 2.0}]),
                },
            )

    samples_repo = SamplesRepo()
    runner = _make_runner(ebstop_service=StopEBStopService(), samples_repo=samples_repo)
    state = EBStopModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "min_ttc": 2.0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert "EBStop Complete" in payload["reason"]
    assert samples_repo.saved_ebstop_samples is not None
    assert list(samples_repo.saved_ebstop_samples["dx0"]) == [13.0]


def test_ebstop_mode_runner_requests_more_samples_when_not_converged() -> None:
    runner = _make_runner()
    state = EBStopModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "min_ttc": 2.0}]),
        state=state,
        get_random_point=lambda index: {
            "dx0": 10.0 + index,
            "ego_speed": 35.0,
            "npc_speed": 15.0,
        },
    )

    assert payload["reason"] == "EBSTOP: Sampling (13)"
    assert state.random_index == 1
    assert state.dispatched_task_count == 2


def test_ebstop_mode_runner_stops_at_max_samples_without_convergence() -> None:
    class MaxSamplesEBStopService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="ebstop",
                metric="min_ttc",
                sample_count=1000,
                estimate=2.0,
                interval=(0.5, 3.5),
                sufficient=False,
                next_action="stop_max_samples",
                diagnostics={"status": "success"},
            )

    runner = _make_runner(ebstop_service=MaxSamplesEBStopService())
    state = EBStopModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "min_ttc": 2.0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert "max_samples=1000" in payload["reason"]


def test_ebstop_mode_runner_uses_region_aware_rejection_sampling() -> None:
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
        reason="EBSTOP: Sampling (13)",
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


def test_ebstop_mode_runner_writes_history_record() -> None:
    class HistoryRepo:
        def __init__(self):
            self.records = []

        def append_ebstop_record(self, record):
            self.records.append(dict(record))

    class StopEBStopService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="ebstop",
                metric="min_ttc",
                sample_count=398,
                estimate=2.0,
                interval=(1.95, 2.05),
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "epsilon": 0.1,
                    "value_range": 2.0,
                    "mean": 2.0,
                    "std": 0.01,
                },
            )

    history_repo = HistoryRepo()
    runner = _make_runner(
        ebstop_service=StopEBStopService(),
        history_repo=history_repo,
    )
    state = EBStopModeState(dispatched_task_count=3)

    runner.handle(
        pd.DataFrame([{"loop_num": 3, "min_ttc": 2.0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert history_repo.records == [
        {
            "task_count": 3,
            "metric": "min_ttc",
            "sample_size": 398,
            "estimate": 2.0,
            "lower_bound": 1.95,
            "upper_bound": 2.05,
            "epsilon": 0.1,
            "value_range": 2.0,
            "mean": 2.0,
            "std": 0.01,
        }
    ]
