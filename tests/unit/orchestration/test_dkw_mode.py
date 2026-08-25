from types import SimpleNamespace

import pandas as pd

from contracts.statistics import StatisticalReport
from orchestration.dkw_mode import DKWModeRunner, DKWModeState


def _make_runner(**overrides) -> DKWModeRunner:
    class HistoryRepo:
        def append_dkw_record(self, _record):
            return None

        def append_dkw_records(self, _records):
            return None

    class SamplesRepo:
        def __init__(self):
            self.saved_dkw_samples = None

        def save_dkw_samples(self, df):
            self.saved_dkw_samples = df.copy()
            return True

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

        def evaluate_request_multiple_summary(self, _data, *, metrics, request):
            report = self.evaluate_request(_data, request)
            return {
                "status": "success",
                "next_action": "collect_more_samples",
                "reports": {metric: report for metric in metrics},
                "sample_count": report.sample_count,
            }

    return DKWModeRunner.from_runtime_config(
        param_names=overrides.get("param_names", ["dx0", "ego_speed", "npc_speed"]),
        target_metric=overrides.get("target_metric", "min_ttc"),
        target_metrics=overrides.get("target_metrics", ["min_ttc", "min_distance"]),
        total_delta=overrides.get("total_delta", 0.05),
        target_epsilon=overrides.get("target_epsilon", 0.15),
        base_samples=overrides.get("base_samples", 50),
        region=overrides.get("region", "custom"),
        pure_smc=overrides.get("pure_smc", False),
        simultaneous=overrides.get("simultaneous", False),
        max_samples=overrides.get("max_samples", 5),
        dkw_service=overrides.get("dkw_service", FakeDKWService()),
        statistical_history_repository=overrides.get("history_repo", HistoryRepo()),
        statistical_samples_repository=overrides.get("samples_repo", SamplesRepo()),
        config=overrides.get("config", SimpleNamespace()),
        case_kind=overrides.get("case_kind", "uturn"),
        config_module_name=overrides.get("config_module_name", None),
    )


def test_dkw_mode_runner_auto_derives_bounds_from_region() -> None:
    runner = _make_runner(region="intersect_safe")

    bounds = runner.initialize_bounds(
        pd.DataFrame(
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
        ),
        None,
    )

    assert bounds == {
        "dx0": (11.0, 14.0),
        "ego_speed": (31.0, 34.0),
        "npc_speed": (13.0, 16.0),
    }


def test_dkw_mode_runner_rejects_points_outside_region(monkeypatch) -> None:
    monkeypatch.setattr(
        "orchestration.dkw_mode.build_theory_metrics",
        lambda **kwargs: {
            "theory_margin_a_human": 1.0
            if float(kwargs["values"].get("dx0", 0.0)) >= 15.0
            else -1.0
        },
    )
    runner = _make_runner(region="jama_safe")

    payload, random_index, dispatched_count = runner.issue_region_aware_random_task(
        reason="SMC: Sequential-DKW Sampling (Stage 1)",
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


def test_dkw_mode_runner_handles_sequential_sampling() -> None:
    runner = _make_runner()
    state = DKWModeState(dispatched_task_count=1)

    payload = runner.handle_sequential(
        pd.DataFrame([{"loop_num": 1, "min_ttc": 1.0}]),
        state=state,
        get_random_point=lambda index: {
            "dx0": 10.0 + index,
            "ego_speed": 35.0,
            "npc_speed": 15.0,
        },
    )

    assert payload["reason"] == "SMC: Sequential-DKW Sampling (Stage 1)"
    assert state.random_index == 1
    assert state.dispatched_task_count == 2


def test_dkw_mode_runner_saves_samples_on_stop() -> None:
    class SamplesRepo:
        def __init__(self):
            self.saved_dkw_samples = None

        def save_dkw_samples(self, df):
            self.saved_dkw_samples = df.copy()
            return True

    class StopDKWService:
        def evaluate_request(self, _data, _request):
            return StatisticalReport(
                method="dkw",
                metric="min_ttc",
                sample_count=80,
                estimate=1.0,
                interval=(0.95, 1.05),
                sufficient=True,
                next_action="stop",
                diagnostics={
                    "status": "success",
                    "filtered_df": pd.DataFrame([{"dx0": 14.0, "min_ttc": 1.0}]),
                },
            )

        def evaluate_request_multiple_summary(self, _data, *, metrics, request):
            report = self.evaluate_request(_data, request)
            return {
                "status": "success",
                "next_action": "stop",
                "reports": {metric: report for metric in metrics},
                "sample_count": report.sample_count,
            }

    samples_repo = SamplesRepo()
    runner = _make_runner(dkw_service=StopDKWService(), samples_repo=samples_repo)
    state = DKWModeState(dispatched_task_count=4)

    payload = runner.handle_sequential(
        pd.DataFrame([{"loop_num": 4, "min_ttc": 1.0}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert payload["reason"] == "SMC Verification Complete"
    assert samples_repo.saved_dkw_samples is not None
    assert list(samples_repo.saved_dkw_samples["dx0"]) == [14.0]
