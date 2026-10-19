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
        region_policy=overrides.get("region_policy"),
        minimum_value=overrides.get("minimum_value"),
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


def test_dkw_mode_runner_rejects_points_outside_region() -> None:
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


def test_dkw_mode_runner_sequential_stops_at_max_samples_without_converging() -> None:
    # handle_sequential previously never checked max_samples at all: as long
    # as the FakeDKWService kept returning "collect_more_samples", it would
    # dispatch tasks forever. It must stop once dispatched_task_count reaches
    # the configured max_samples.
    runner = _make_runner(max_samples=5)
    state = DKWModeState(dispatched_task_count=5)

    payload = runner.handle_sequential(
        pd.DataFrame([{"loop_num": 5, "min_ttc": 1.0}]),
        state=state,
        get_random_point=lambda index: {
            "dx0": 10.0 + index,
            "ego_speed": 35.0,
            "npc_speed": 15.0,
        },
    )

    assert payload["system_command"] == "stop"
    assert "max_samples=5" in payload["reason"]
    assert "without converging" in payload["reason"]
    assert state.dispatched_task_count == 5


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
    runner = _make_runner(dkw_service=StopDKWService(), samples_repo=samples_repo, base_samples=1)
    state = DKWModeState(dispatched_task_count=4)

    payload = runner.handle_sequential(
        pd.DataFrame([{"loop_num": 4, "min_ttc": 1.0, "reason": "SMC: Sequential-DKW Sampling (Stage 1)"}]),
        state=state,
        get_random_point=lambda _index: {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0},
    )

    assert payload["system_command"] == "stop"
    assert payload["reason"] == "SMC Verification Complete"
    assert samples_repo.saved_dkw_samples is not None
    assert list(samples_repo.saved_dkw_samples["dx0"]) == [14.0]


def _report_with_rows(rows, *, next_action="collect_more_samples") -> StatisticalReport:
    return StatisticalReport(
        method="dkw",
        metric="min_ttc",
        sample_count=len(rows),
        estimate=1.0,
        interval=(0.9, 1.1),
        sufficient=next_action == "stop",
        next_action=next_action,
        diagnostics={"status": "success", "filtered_df": pd.DataFrame(rows)},
    )


class _FixedReportService:
    def __init__(self, report):
        self.report = report

    def evaluate_request(self, _data, _request):
        return self.report

    def evaluate_request_multiple_summary(self, _data, *, metrics, request):
        return {
            "status": "success",
            "next_action": self.report.next_action,
            "reports": {metric: self.report for metric in metrics},
            "sample_count": self.report.sample_count,
        }


def _point(_index):
    return {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 15.0}


def test_dkw_fixed_with_uniform_samples_has_the_dkw_guarantee_and_no_notice(capsys) -> None:
    rows = [{"min_ttc": 1.0, "reason": "SMC: Fixed Sampling (1/2)"},
            {"min_ttc": 2.0, "reason": "SMC: Fixed Sampling (2/2)"}]
    report = _report_with_rows(rows, next_action="stop")
    runner = _make_runner(dkw_service=_FixedReportService(report), max_samples=2)

    runner.handle_fixed(pd.DataFrame(rows), state=DKWModeState(dispatched_task_count=2), get_random_point=_point)

    assert report.diagnostics["guarantee"] == "dkw"
    assert capsys.readouterr().err == ""


def test_dkw_fixed_with_exploration_rows_has_no_guarantee(capsys) -> None:
    rows = [{"min_ttc": 1.0, "reason": "SMC: Fixed Sampling (1/2)"},
            {"min_ttc": 2.0, "reason": "Boundary exploration"}]
    report = _report_with_rows(rows, next_action="stop")
    runner = _make_runner(dkw_service=_FixedReportService(report), max_samples=2)

    runner.handle_fixed(pd.DataFrame(rows), state=DKWModeState(dispatched_task_count=2), get_random_point=_point)

    assert report.diagnostics["guarantee"] == "none_mixed_samples"
    assert "--dkw-pure-smc" in capsys.readouterr().err


class _CountingService(_FixedReportService):
    def __init__(self, report):
        super().__init__(report)
        self.datasets = []

    def evaluate_request(self, data, request):
        self.datasets.append((data.copy(), request.confidence))
        return self.report


def _smc_rows(values, start=1):
    return [{"loop_num": start + i, "min_ttc": v, "status": "success",
             "reason": "SMC: Sequential-DKW Sampling (Stage 1)"} for i, v in enumerate(values)]


def test_dkw_sequential_evaluates_each_stage_once_and_has_the_dkw_guarantee(capsys) -> None:
    rows = _smc_rows([1.0, 2.0])
    report = _report_with_rows(rows)
    service = _CountingService(report)
    runner = _make_runner(dkw_service=service, pure_smc=True, base_samples=2, max_samples=100)
    state = DKWModeState(dispatched_task_count=2)

    runner.handle_sequential(pd.DataFrame(rows), state=state, get_random_point=_point)
    runner.handle_sequential(pd.DataFrame(rows), state=state, get_random_point=_point)

    # Stage 1 (n=2) is evaluated once at confidence 1 - 0.05/2; stage 2 waits for 8 samples.
    assert len(service.datasets) == 1
    assert service.datasets[0][1] == 1 - 0.05 / 2
    assert state.stage == 2
    assert report.diagnostics["guarantee"] == "dkw"
    assert capsys.readouterr().err == ""


def test_dkw_sequential_waits_for_the_stage_and_uses_its_first_samples() -> None:
    report = _report_with_rows(_smc_rows([1.0]))
    service = _CountingService(report)
    runner = _make_runner(dkw_service=service, pure_smc=True, base_samples=3, max_samples=100)
    state = DKWModeState(dispatched_task_count=5)

    runner.handle_sequential(pd.DataFrame(_smc_rows([5.0, 4.0])), state=state, get_random_point=_point)
    assert service.datasets == []

    # Results arrive out of order; the stage uses the first three by issue order.
    rows = list(reversed(_smc_rows([5.0, 4.0, 3.0, 2.0, 1.0])))
    runner.handle_sequential(pd.DataFrame(rows), state=state, get_random_point=_point)

    assert len(service.datasets) == 1
    assert sorted(service.datasets[0][0]["min_ttc"]) == [3.0, 4.0, 5.0]


def test_dkw_sequential_without_pure_smc_is_marked_as_kde_approximation(capsys) -> None:
    rows = [{"min_ttc": 1.0, "reason": "Boundary exploration"},
            {"min_ttc": 2.0, "reason": "SMC: Sequential-DKW Sampling (Stage 1)"}]
    report = _report_with_rows(rows)
    runner = _make_runner(dkw_service=_FixedReportService(report), pure_smc=False, base_samples=1, max_samples=10)

    runner.handle_sequential(pd.DataFrame(rows), state=DKWModeState(), get_random_point=_point)

    assert report.diagnostics["guarantee"] == "approximate_kde"
    assert "KDE" in capsys.readouterr().err


def test_dkw_guarantee_is_recorded_for_each_simultaneous_metric() -> None:
    rows = [{"min_ttc": 1.0, "min_distance": 2.0, "reason": "SMC: Fixed Sampling (1/1)"}]
    report = _report_with_rows(rows, next_action="stop")
    runner = _make_runner(dkw_service=_FixedReportService(report), simultaneous=True, max_samples=1)

    runner.handle_fixed(pd.DataFrame(rows), state=DKWModeState(dispatched_task_count=1), get_random_point=_point)

    assert report.diagnostics["guarantee"] == "dkw"


def test_awsim_prism_runner_and_iid_rule_make_the_same_decisions() -> None:
    import numpy as np

    from evaluation.dkw import DKWService
    from evaluation.stopping_rules import DKWSequentialStagewise

    class History:
        def __init__(self):
            self.last = None

        def append_dkw_record(self, record):
            self.last = dict(record)

    for seed in range(5):
        values = np.random.default_rng(seed).exponential(size=2000)
        history = History()
        runner = DKWModeRunner.from_runtime_config(
            param_names=["dx0"], target_metric="t", target_metrics=["t"], total_delta=0.05,
            target_epsilon=0.15, base_samples=20, region="custom", pure_smc=True, simultaneous=False,
            max_samples=2000, dkw_service=DKWService(), statistical_history_repository=history,
            minimum_value=0.0,
        )
        rule = DKWSequentialStagewise(metric="t", target_width=0.15, max_samples=2000, base_samples=20,
                                      lower_support=0.0)
        state, rows = DKWModeState(), []
        while True:
            payload = runner.handle_sequential(pd.DataFrame(rows) if rows else None, state=state,
                                               get_random_point=lambda index: {"dx0": float(index)})
            if payload.get("system_command") == "stop":
                break
            rows.append({"loop_num": len(rows) + 1, "t": values[len(rows)], "status": "success",
                         "reason": payload["reason"]})
        decision = rule.decide(pd.DataFrame(rows), issued=len(rows))

        assert decision.stop
        assert history.last["ess"] == decision.report.sample_count
        assert (history.last["lower_bound"], history.last["upper_bound"]) == decision.report.interval
        assert history.last["estimate"] == decision.report.estimate
