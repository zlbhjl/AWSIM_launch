"""A new IID-sampling target works by adding only targets/<name>/.

The test writes a throwaway target package ``coin`` into a temporary
``targets`` directory on sys.path (``targets`` is a namespace package) and
runs a statistical mode end to end through the CLI, without touching any
shared module.
"""

import json
import textwrap

import pytest

from targets.registry import list_target_names, reload_target_plugins

COIN_FILES = {
    "plugin.py": """
        from contracts.target_plugin import TargetComponents
        from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
        from contracts.execution import RawRunResult, RunStatus


        class CoinBackend:
            def run(self, test_case):
                heads = int(float(test_case.input["u"]) < float(test_case.input.get("p_heads", 0.3)))
                return RawRunResult(
                    case_id=test_case.case_id, target=test_case.target, case_kind=test_case.case_kind,
                    status=RunStatus.SUCCESS, meta={"heads": heads},
                )


        class CoinInterpreter:
            def interpret_raw_run_result(self, raw):
                return EvaluationRecord(
                    case_id=raw.case_id, target=raw.target, case_kind=raw.case_kind, status=raw.status,
                    output={"c_heads": raw.meta["heads"]},
                    meta=ensure_evaluation_meta({}, source_module="targets.coin.plugin"),
                )


        class CoinPlugin:
            name = "coin"

            def build_components(self, args):
                return TargetComponents(backend=CoinBackend(), result_interpreter=CoinInterpreter())


        PLUGIN = CoinPlugin()
    """,
    "statistical.py": """
        from contracts.statistical_region import PassthroughStatisticalRegionPolicy
        from targets.statistical import StatisticalTargetProfile

        STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
            target="coin",
            binary_metric="c_heads",
            dkw_metric="c_heads",
            iid_sampling=True,
            default_input_distribution={"u": {"distribution": "uniform", "min": 0.0, "max": 1.0}},
        )


        def build_statistical_region_policy(*, case_kind, config_module_name=None):
            return PassthroughStatisticalRegionPolicy()
    """,
}


@pytest.fixture
def coin_target(tmp_path, monkeypatch):
    package = tmp_path / "plugins" / "targets" / "coin"
    package.mkdir(parents=True)
    for name, source in COIN_FILES.items():
        (package / name).write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "plugins"))
    reload_target_plugins()
    yield "coin"
    reload_target_plugins()


def test_a_new_target_is_discovered_from_its_package(coin_target) -> None:
    assert coin_target in list_target_names()


def test_a_new_iid_target_runs_a_statistical_mode_end_to_end(coin_target, tmp_path) -> None:
    from apps.cli.orchestrator_main import run_orchestrator

    output = tmp_path / "records.jsonl"
    exit_code = run_orchestrator([
        "--target", coin_target, "--mode", "binomial_ci",
        "--binomial-method", "clopper-pearson", "--binomial-target-width", "0.3",
        "--max-samples", "200", "--seed", "1",
        "--output", str(output), "--dataset-csv", str(tmp_path / "dataset.csv"),
    ])

    summary = json.loads(output.with_name("records.jsonl.summary.json").read_text(encoding="utf-8"))
    report = summary["statistical_report"]
    assert exit_code == 0
    assert report["metric"] == "c_heads"
    records = output.read_text(encoding="utf-8")
    assert '"case_kind": "coin"' in records
    assert report["sample_count"] > 0
    assert report["interval"][0] <= report["estimate"] <= report["interval"][1]
