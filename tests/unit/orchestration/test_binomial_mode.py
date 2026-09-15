from types import SimpleNamespace

import pandas as pd

from contracts.statistics import StatisticalReport
from orchestration.binomial_mode import BinomialModeRunner, BinomialModeState


def _make_runner(**overrides) -> BinomialModeRunner:
    class HistoryRepo:
        def append_binomial_ci_record(self, _record):
            return None

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
                sample_count=12,
                estimate=0.1,
                interval=(0.0, 0.2),
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={"status": "success"},
            )

    return BinomialModeRunner.from_runtime_config(
        param_names=overrides.get("param_names", ["dx0", "ego_speed", "npc_speed"]),
        target=overrides.get("target", "c_collision"),
        method=overrides.get("method", "wilson"),
        confidence=overrides.get("confidence", 0.95),
        target_width=overrides.get("target_width", 0.02),
        min_samples=overrides.get("min_samples", 100),
        max_samples=overrides.get("max_samples", 1000),
        region=overrides.get("region", "custom"),
        binomial_ci_service=overrides.get("binomial_ci_service", FakeBinomialService()),
        statistical_history_repository=overrides.get("history_repo", HistoryRepo()),
        statistical_samples_repository=overrides.get("samples_repo", SamplesRepo()),
        config=overrides.get("config", SimpleNamespace()),
        case_kind=overrides.get("case_kind", "uturn"),
        config_module_name=overrides.get("config_module_name", None),
        region_policy=overrides.get("region_policy"),
    )


def test_binomial_mode_runner_stops_when_interval_is_sufficient() -> None:
    class SamplesRepo:
        def __init__(self):
            self.saved_binomial_samples = None

        def save_binomial_ci_samples(self, df):
            self.saved_binomial_samples = df.copy()
            return True

    class StopBinomialService:
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
    runner = _make_runner(binomial_ci_service=StopBinomialService(), samples_repo=samples_repo)
    state = BinomialModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "c_collision": 0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert "Binomial CI Complete" in payload["reason"]
    assert samples_repo.saved_binomial_samples is not None
    assert list(samples_repo.saved_binomial_samples["dx0"]) == [13.0]


def test_binomial_mode_runner_requests_more_samples_when_needed() -> None:
    runner = _make_runner()
    state = BinomialModeState(dispatched_task_count=1)

    payload = runner.handle(
        pd.DataFrame([{"loop_num": 1, "c_collision": 0}]),
        state=state,
        get_random_point=lambda index: {
            "dx0": 10.0 + index,
            "ego_speed": 35.0,
            "npc_speed": 15.0,
        },
    )

    assert payload["reason"] == "BINOMIAL_CI: Sampling (13)"
    assert state.random_index == 1
    assert state.dispatched_task_count == 2


def test_binomial_mode_runner_uses_region_aware_rejection_sampling() -> None:
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
        reason="BINOMIAL_CI: Sampling (13)",
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


def test_binomial_mode_runner_writes_history_record() -> None:
    class HistoryRepo:
        def __init__(self):
            self.records = []

        def append_binomial_ci_record(self, record):
            self.records.append(dict(record))

    class StopBinomialService:
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
    runner = _make_runner(
        binomial_ci_service=StopBinomialService(),
        history_repo=history_repo,
    )
    state = BinomialModeState(dispatched_task_count=3)

    runner.handle(
        pd.DataFrame([{"loop_num": 3, "c_collision": 0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert history_repo.records == [
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
