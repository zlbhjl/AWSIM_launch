"""The statistical modes of exploration targets on the shared region sampling (docs/statistics_single_path_plan.md).

Moved from the GP exploration's tests (test_strategy.py, test_exploration_statistical_numbering.py)
when the statistical modes left the exploration engine; the assertions are unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from contracts.statistics import StatisticalReport
from orchestration.region_sampling import RegionSamplingStrategy
from targets.awsim.statistical import AWSIM_REGION_VOCABULARY


def _region_strategy(
    scenario_name, config, *, run_mode, num_candidates=None, binomial_ci_service=None, dkw_service=None,
    sprt_service=None, ebstop_service=None, dkw_bounds=None, dkw_region="custom", dkw_pure_smc=False,
    dkw_simultaneous=False, **kwargs,
):
    """Built with the arguments the GP exploration took (num_candidates is not used)."""
    del num_candidates
    return RegionSamplingStrategy(
        scenario_name=scenario_name, config=config, target="awsim", run_mode=run_mode,
        services={"binomial_ci": binomial_ci_service, "dkw": dkw_service, "sprt": sprt_service,
                  "ebstop": ebstop_service},
        bounds=dkw_bounds, region=dkw_region, pure_smc=dkw_pure_smc, simultaneous=dkw_simultaneous,
        region_vocabulary=AWSIM_REGION_VOCABULARY, **kwargs,
    )


def test_region_sampling_binomial_ci_stops_when_interval_is_sufficient() -> None:
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
    strategist = _region_strategy(
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


def test_region_sampling_binomial_ci_writes_history_record() -> None:
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
    strategist = _region_strategy(
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


def test_region_sampling_binomial_ci_requests_more_samples_when_needed() -> None:
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

    strategist = _region_strategy(
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


def test_region_sampling_binomial_ci_uses_region_aware_rejection_sampling() -> None:
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

    class ThresholdRegionPolicy:
        vocabulary = AWSIM_REGION_VOCABULARY  # the region names (jama_safe, ...) are AWSIM's

        def build_filter_frame(self, point):
            row = dict(point)
            row["theory_margin_a_human"] = (
                1.0 if float(point["dx0"]) >= 15.0 else -1.0
            )
            return pd.DataFrame([row])

    strategist = _region_strategy(
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
        statistical_region_policy=ThresholdRegionPolicy(),
        num_candidates=5,
    )
    # Draws are numbered from the run's own start (0), not from the highest loop_num.
    strategist.sampler.point = lambda index: {
        0: {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0},
        1: {"dx0": 18.0, "ego_speed": 35.0, "npc_speed": 15.0},
    }[index]

    payload = strategist.decide_next_target()

    assert payload["reason"] == "BINOMIAL_CI: Sampling (13)"
    assert payload["dx0"] == 18.0
    assert strategist.draw == 2


def test_region_sampling_dkw_stops_when_report_is_sufficient() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 1, "min_ttc": 1.0, "reason": "SMC: Sequential-DKW Sampling (Stage 1)"}])

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

    strategist = _region_strategy(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=1,  # stage 1 is complete with the one sample
        ),
        dataset_repository=Repo(),
        dkw_service=FakeDKWService(),
        run_mode="dkw",
        num_candidates=5,
    )

    payload = strategist.decide_next_target()

    assert payload["system_command"] == "stop"
    assert payload["reason"] == "SMC Verification Complete"


def test_region_sampling_dkw_writes_history_record() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame([{"loop_num": 4, "min_ttc": 1.0, "reason": "SMC: Sequential-DKW Sampling (Stage 1)"}])

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
    strategist = _region_strategy(
        "uturn",
        SimpleNamespace(
            PARAM_RANGES={"dx0": (10.0, 20.0)},
            DKW_TARGET_METRIC="min_ttc",
            DKW_TOTAL_DELTA=0.05,
            DKW_TARGET_EPSILON=0.15,
            DKW_BASE_SAMPLES=1,  # stage 1 is complete with the one sample
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


def test_region_sampling_dkw_requests_more_samples_when_needed() -> None:
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

    strategist = _region_strategy(
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


def test_region_sampling_auto_derives_dkw_bounds_from_region() -> None:
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

    strategist = _region_strategy(
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

    assert strategist.bounds == {
        "dx0": (11.0, 14.0),
        "ego_speed": (31.0, 34.0),
        "npc_speed": (13.0, 16.0),
    }
    assert strategist.active_bounds == strategist.bounds


def test_region_sampling_dkw_uses_region_aware_rejection_sampling() -> None:
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

    class ThresholdRegionPolicy:
        vocabulary = AWSIM_REGION_VOCABULARY  # the region names (jama_safe, ...) are AWSIM's

        def build_filter_frame(self, point):
            row = dict(point)
            row["theory_margin_a_human"] = (
                1.0 if float(point["dx0"]) >= 15.0 else -1.0
            )
            return pd.DataFrame([row])

    strategist = _region_strategy(
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
        statistical_region_policy=ThresholdRegionPolicy(),
        num_candidates=5,
    )
    # Draws are numbered from the run's own start (0), not from the highest loop_num.
    strategist.sampler.point = lambda index: {
        0: {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0},
        1: {"dx0": 18.0, "ego_speed": 35.0, "npc_speed": 15.0},
    }[index]

    payload = strategist.decide_next_target()

    assert payload["reason"] == "SMC: Sequential-DKW Sampling (Stage 1)"
    assert payload["dx0"] == 18.0
    assert strategist.dkw_state.random_index == 2


def test_region_sampling_dkw_fixed_pure_smc_syncs_only_smc_rows() -> None:
    class Repo:
        def load_dataset(self):
            return pd.DataFrame(
                [
                    {"loop_num": 101, "reason": "STEP1: Global Search (V:0)"},
                    {"loop_num": 102, "reason": "STEP2: Boundary 0.5"},
                    {"loop_num": 3, "reason": "SMC: Fixed Sampling (1/2000)"},
                ]
            )

    strategist = _region_strategy(
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


CONFIG = SimpleNamespace(
    PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0), "npc_speed": (10.0, 20.0)},
    BINOMIAL_CI_TARGET="c_collision", BINOMIAL_CI_TARGET_WIDTH=0.02, BINOMIAL_CI_MIN_SAMPLES=100,
)


class Repo:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def load_dataset(self):
        return pd.DataFrame(self.rows) if self.rows else None


class RecordingService:
    def __init__(self):
        self.seen = []

    def evaluate_request(self, data, request):
        own = data[data.get("experiment_id", pd.Series(dtype=object)).notna()] if data is not None and "experiment_id" in data else None
        self.seen.append([] if own is None else own["sample_index"].tolist())
        return StatisticalReport(method="binomial_ci", metric="c_collision", sample_count=0, estimate=None,
                                 interval=(0.0, 1.0), sufficient=False, next_action="collect_more_samples",
                                 diagnostics={"status": "success"})


def _strategist(repo, service):
    return _region_strategy("uturn", CONFIG, dataset_repository=repo, binomial_ci_service=service,
                                    run_mode="binomial_ci", num_candidates=5)


def test_samples_are_numbered_and_judged_in_order() -> None:
    repo, service = Repo([{"loop_num": 1, "c_collision": 0, "reason": "EXPLORATION"}]), RecordingService()
    strategist = _strategist(repo, service)
    cases = [strategist.next_test_case() for _ in range(3)]
    assert [case.input["sample_index"] for case in cases] == [1, 2, 3]
    assert [case.input["sampling_draw_index"] for case in cases] == [0, 1, 2]
    run = cases[0].input["experiment_id"]
    # Samples 3 and 2 finished first; sample 1 is still running.
    repo.rows += [{**cases[2].input, "c_collision": 1, "status": "success", "loop_num": 2, "reason": cases[2].reason},
                  {**cases[1].input, "c_collision": 0, "status": "success", "loop_num": 3, "reason": cases[1].reason}]
    strategist.next_test_case()
    assert service.seen[-1] == []                    # nothing before sample 1 is complete
    repo.rows.append({**cases[0].input, "c_collision": 0, "status": "success", "loop_num": 4,
                      "reason": cases[0].reason})
    strategist.next_test_case()
    assert service.seen[-1] == [1, 2, 3]
    assert run.startswith("binomial_ci-")


def test_a_resumed_run_continues_its_numbering_and_draws() -> None:
    repo, service = Repo(), RecordingService()
    first = _strategist(repo, service)
    issued = [first.next_test_case() for _ in range(2)]
    repo.rows = [{**case.input, "c_collision": 0, "status": "success", "loop_num": n + 1, "reason": case.reason}
                 for n, case in enumerate(issued)]

    resumed = _strategist(repo, RecordingService()).next_test_case()

    assert resumed.input["sample_index"] == 3 and resumed.input["sampling_draw_index"] == 2


def test_failed_runs_are_not_replaced_by_nearby_points_in_statistical_modes() -> None:
    repo = Repo([{"loop_num": 1, "c_collision": -1, "min_ttc": -1, "dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0}])
    case = _strategist(repo, RecordingService()).next_test_case()
    assert "Error Recovery" not in case.reason


def test_an_undeclared_region_name_fails_before_running() -> None:
    with pytest.raises(ValueError, match="unknown region 'safe_zone'.*emp_safe"):
        _region_strategy("uturn", CONFIG, dataset_repository=Repo(), binomial_ci_service=RecordingService(),
                                 run_mode="binomial_ci", dkw_region="safe_zone", num_candidates=5)
    # A condition is still accepted as it is.
    _region_strategy("uturn", CONFIG, dataset_repository=Repo(), binomial_ci_service=RecordingService(),
                             run_mode="binomial_ci", dkw_region="(c_collision == 0)", num_candidates=5)


# --- The method table (former binomial_mode.py, sprt_mode.py, ebstop_mode.py tests) ---

THREE = SimpleNamespace(PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0), "npc_speed": (10.0, 20.0)},
                        EBSTOP_TARGET="c_collision")


class _Reporting:
    def __init__(self, report):
        self.report = report
        self.requests = []

    def evaluate_request(self, _data, request):
        self.requests.append(request)
        return self.report


class _Repositories:
    def __init__(self):
        self.records, self.saved = [], {}

    def __getattr__(self, name):
        if name.startswith("append_"):
            return lambda record: self.records.append((name, dict(record)))
        if name.startswith("save_"):
            return lambda frame: self.saved.setdefault(name, frame.copy())
        raise AttributeError(name)


def _method_strategy(mode, report, *, rows=({"loop_num": 3, "c_collision": 0},), **kwargs):
    service = _Reporting(report)
    repositories = _Repositories()
    strategy = _region_strategy("uturn", THREE, run_mode=mode, dataset_repository=Repo(rows),
                                **{f"{'binomial_ci' if mode == 'binomial_ci' else mode}_service": service},
                                statistical_history_repository=repositories,
                                statistical_samples_repository=repositories, **kwargs)
    return strategy, service, repositories


def _report(mode, next_action, **diagnostics):
    return StatisticalReport(method=mode, metric="c_collision", sample_count=42, estimate=0.02,
                             interval=(0.01, 0.03), sufficient=next_action == "stop", next_action=next_action,
                             diagnostics={"status": "success", **diagnostics})


@pytest.mark.parametrize(("mode", "diagnostics", "complete", "exhausted"), [
    ("binomial_ci", {}, "Binomial CI Complete: c_collision CI width 0.02000", "Binomial CI reached max_samples=1000"),
    ("sprt", {"verdict": "h0"}, "SPRT Complete: c_collision verdict=h0 (n=42)", "SPRT reached max_samples=1000 undecided"),
    ("ebstop", {}, "EBStop Complete: c_collision estimate=0.02000 (n=42)", "EBStop reached max_samples=2000 unconverged"),
])
def test_each_method_reports_its_stop_reasons_and_saves_its_samples(mode, diagnostics, complete, exhausted) -> None:
    frame = pd.DataFrame([{"dx0": 13.0, "c_collision": 0}])
    strategy, _, repositories = _method_strategy(mode, _report(mode, "stop", filtered_df=frame, **diagnostics),
                                                 max_samples=1000)
    assert strategy.decide_next_target() == {"system_command": "stop", "reason": complete}
    assert list(repositories.saved[f"save_{mode}_samples"]["dx0"]) == [13.0]
    assert strategy.latest_final_report.reason == complete

    strategy, _, _ = _method_strategy(mode, _report(mode, "stop_max_samples", **diagnostics), max_samples=1000)
    assert strategy.decide_next_target()["reason"] == exhausted


@pytest.mark.parametrize(("mode", "record"), [
    ("sprt", {"task_count": 3, "metric": "c_collision", "verdict": "h1", "sample_size": 42, "estimate": 0.02,
              "log_likelihood_ratio": 3.2, "lower_log_threshold": -2.9, "upper_log_threshold": 3.0, "p0": 0.1,
              "p1": 0.01, "alpha": 0.05, "beta": 0.05}),
    ("ebstop", {"task_count": 3, "metric": "c_collision", "sample_size": 42, "estimate": 0.02, "lower_bound": 0.01,
                "upper_bound": 0.03, "epsilon": 0.1, "value_range": [0.0, 1.0], "mean": 0.02, "std": 0.1}),
])
def test_each_method_writes_its_history_record(mode, record) -> None:
    diagnostics = {key: value for key, value in record.items()
                   if key not in {"task_count", "metric", "sample_size", "estimate", "lower_bound", "upper_bound"}}
    strategy, _, repositories = _method_strategy(mode, _report(mode, "collect_more_samples", **diagnostics))
    strategy.decide_next_target()
    assert repositories.records == [(f"append_{mode}_record", record)]


@pytest.mark.parametrize(("mode", "tag"), [("binomial_ci", "BINOMIAL_CI"), ("sprt", "SPRT"), ("ebstop", "EBSTOP")])
def test_each_method_samples_in_the_region_and_tags_its_samples(mode, tag) -> None:
    class ThresholdRegionPolicy:
        vocabulary = AWSIM_REGION_VOCABULARY

        def build_filter_frame(self, point):
            return pd.DataFrame([{**point, "theory_margin_a_human": 1.0 if point["dx0"] >= 15.0 else -1.0}])

    strategy, service, _ = _method_strategy(mode, _report(mode, "collect_more_samples"), dkw_region="jama_safe",
                                            statistical_region_policy=ThresholdRegionPolicy())
    strategy.sampler.point = lambda draw: {0: {"dx0": 12.0}, 1: {"dx0": 18.0}}[draw]
    payload = strategy.decide_next_target()
    assert payload == {"dx0": 18.0, "reason": f"{tag}: Sampling (43)"}
    assert strategy.draw == 2 and strategy.task_count == 4
    assert service.requests[0].options["reason_pattern"] == f"{tag}:"
    assert service.requests[0].options["region"] == "jama_safe"


def test_the_binomial_request_carries_the_anytime_option() -> None:
    for anytime in (False, True):
        strategy, service, _ = _method_strategy("binomial_ci", _report("binomial_ci", "collect_more_samples"),
                                                binomial_anytime_valid=anytime)
        strategy.decide_next_target()
        assert service.requests[0].options["anytime_valid"] is anytime


def test_the_gp_exploration_refuses_the_statistical_modes() -> None:
    from orchestration.exploration.active_learning import ActiveLearningStrategist

    with pytest.raises(ValueError, match="shared statistical path"):
        ActiveLearningStrategist("uturn", THREE, dataset_repository=Repo(), run_mode="binomial_ci")


def test_grouped_settings_give_the_statistical_defaults() -> None:
    config = SimpleNamespace(
        get_case_definition=lambda: {"scenario_type": "uturn", "param_ranges": {"dx0": (10.0, 20.0)},
                                     "fixed_params": {"ego_init_lane": "514"}},
        get_strategy_settings=lambda: {"max_samples": 33},
    )
    strategy = _region_strategy("uturn", config, run_mode="binomial_ci", dataset_repository=Repo())
    assert strategy.max_samples == 33
    assert strategy.dkw_target_metric == "min_ttc" and strategy.dkw_target_metrics == ["min_ttc", "min_distance"]
    settings = strategy.settings
    assert (settings["binomial_target"], settings["binomial_method"], settings["binomial_confidence"]) == (
        "c_collision", "wilson", 0.95)


# --- The end of a run: wait for the last results, decide once (verification V4, 2026-11-03) ---

DKW_CONFIG = SimpleNamespace(
    PARAM_RANGES={"dx0": (10.0, 20.0), "ego_speed": (30.0, 40.0), "npc_speed": (10.0, 20.0)},
    DKW_TARGET_METRIC="min_ttc",
)


def _answer(repo, cases):
    for case in cases:
        margin = case.input["dx0"] - 0.35 * case.input["ego_speed"] + 0.2 * case.input["npc_speed"]
        repo.rows.append({**case.input, "min_ttc": max(0.0, 0.25 * margin + 1.5), "status": "success",
                          "reason": case.reason, "loop_num": len(repo.rows) + 1})


@pytest.mark.parametrize("mode", ["dkw", "dkw_fixed"])
def test_dkw_waits_for_the_last_results_and_decides_once(mode) -> None:
    repo, history = Repo(), _Repositories()
    strategy = _region_strategy("uturn", DKW_CONFIG, run_mode=mode, dataset_repository=repo, dkw_pure_smc=True,
                                max_samples=12, statistical_history_repository=history)
    issued = []
    while (case := strategy.next_test_case()) is not None:
        issued.append(case)
    assert len(issued) == 12

    _answer(repo, issued[:5])
    # The orchestrator keeps asking while 7 samples still run (this used to raise at about the 50th call).
    answers = [strategy.next_test_case() for _ in range(200)]
    assert answers == [None] * 200 and strategy.waiting_for_result
    assert strategy.latest_final_report is None
    assert history.records == []  # nothing is evaluated while waiting

    _answer(repo, issued[5:])
    assert strategy.next_test_case() is None and not strategy.waiting_for_result
    final = strategy.latest_final_report
    evaluations = len(history.records)
    for _ in range(50):  # the decision is kept, not evaluated again
        assert strategy.next_test_case() is None
    assert strategy.latest_final_report == final and len(history.records) == evaluations
    assert strategy.dkw_state.stage <= 2


def test_a_stop_decision_is_kept_without_evaluating_again() -> None:
    strategy, service, repositories = _method_strategy("binomial_ci", _report("binomial_ci", "stop"))
    first = strategy.decide_next_target()
    for _ in range(20):
        assert strategy.decide_next_target() == first
    assert len(service.requests) == 1 and len(repositories.records) == 1


def test_at_most_max_samples_are_issued_and_a_lost_sample_is_waited_for() -> None:
    """The run decides on the full sample: a lost sample's retry is waited for (verification
    V10b, 2026-11-05: the run stopped at the case count and the retry was cancelled)."""
    repo, service = Repo(), RecordingService()
    strategy = _region_strategy("uturn", CONFIG, dataset_repository=repo, binomial_ci_service=service,
                                run_mode="binomial_ci", max_samples=6)
    issued = [case for case in (strategy.next_test_case() for _ in range(20)) if case is not None]
    assert len(issued) == 6 and strategy.waiting_for_result

    def answer(case):
        repo.rows.append({**case.input, "c_collision": 0, "status": "success", "loop_num": len(repo.rows) + 1})

    for case in issued[:2] + issued[3:]:  # sample 3 is lost on a stopped container
        answer(case)
    evaluations = len(service.seen)
    for _ in range(50):
        assert strategy.next_test_case() is None and strategy.waiting_for_result
    assert len(service.seen) <= evaluations + 1  # not evaluated again at every poll
    assert strategy.latest_final_report is None

    answer(issued[2])  # the retry comes back
    assert strategy.next_test_case() is None and not strategy.waiting_for_result
    assert service.seen[-1] == [1, 2, 3, 4, 5, 6]
    assert strategy.latest_final_report.reason == "Binomial CI reached max_samples=6"
