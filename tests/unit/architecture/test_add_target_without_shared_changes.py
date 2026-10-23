"""A new IID-sampling target works by adding only targets/<name>/.

The test writes a throwaway target package ``coin`` into a temporary
``targets`` directory on sys.path (``targets`` is a namespace package) and
runs a statistical mode end to end through the CLI, without touching any
shared module.
"""

import json
from pathlib import Path
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

            def interpret_path(self, path):
                import json
                from pathlib import Path

                saved = json.loads(Path(path).read_text(encoding="utf-8"))
                return self.interpret_raw_run_result(RawRunResult(
                    case_id=Path(path).stem, target="coin", case_kind="coin",
                    status=RunStatus.SUCCESS, meta={"heads": saved["heads"]},
                ))


        class CoinPlugin:
            name = "coin"

            def build_components(self, args):
                return TargetComponents(backend=CoinBackend(), result_interpreter=CoinInterpreter())

            def statistical_summary_extras(self, args):
                return {"coin_seed": args.seed}

            def build_result_interpreter(self, *, case_kind, config_module):
                return CoinInterpreter()


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


def test_a_new_target_interprets_a_saved_result_with_the_shared_cli(coin_target, tmp_path, capsys) -> None:
    from apps.cli.result_interpreter_main import run_result_interpreter

    saved = tmp_path / "run_1.json"
    saved.write_text(json.dumps({"heads": 1}), encoding="utf-8")

    exit_code = run_result_interpreter(["--input", str(saved), "--target", coin_target])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["target"] == "coin"
    assert payload["output"] == {"c_heads": 1}


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
    # The plugin adds its own entries to the summary.
    assert summary["coin_seed"] == 1
    records = output.read_text(encoding="utf-8")
    assert '"case_kind": "coin"' in records
    assert report["sample_count"] > 0
    assert report["interval"][0] <= report["estimate"] <= report["interval"][1]


class _DecaySystem:
    """dX = -X dt + 0.5 dW, a throwaway model for the registry test."""

    state_names = ("x",)
    has_noise = True

    def initial_mode(self):
        return "run"

    def initial_state(self):
        import numpy as np

        return np.array([0.0])

    def drift(self, _t, x, _mode, _entry):
        import numpy as np

        return np.array([-x[0]])

    def noise_channels(self, _mode):
        return ("w",)

    def diffusion(self, _t, _x, _mode, _entry):
        import numpy as np

        return np.array([[0.5]])

    def transitions(self, _mode):
        return ()

    def project(self, state):
        return state


class _DecayAdapter:
    model_id = "decay"
    legacy_execution = False
    default_binary_metric = "c_event"
    default_continuous_metric = "x_terminal"

    def noise_channel_names(self):
        return frozenset({"w"})

    def initial_input_names(self):
        return ()

    def state_names(self):
        return _DecaySystem.state_names

    def resolve(self, inputs):
        from targets.dynamics.models.sde_common import resolve_sde_model

        return resolve_sde_model(model_id=self.model_id, system=_DecaySystem(), inputs=inputs,
                                 channels=self.noise_channel_names(), model_meta={})

    def interpret(self, payload, trace):
        from targets.dynamics.models.sde_common import interpret_sde_model

        return interpret_sde_model(payload, trace, state_names=_DecaySystem.state_names)


def test_a_new_dynamics_model_runs_a_statistical_mode_end_to_end(tmp_path, monkeypatch) -> None:
    """Registering a model is the only change; no CLI, orchestration or evaluation code is touched."""
    from apps.cli.orchestrator_main import run_orchestrator
    from targets.dynamics.models import registry

    original = registry._adapters
    monkeypatch.setattr(registry, "_adapters", lambda: {**original(), "decay": _DecayAdapter()})
    output = tmp_path / "records.jsonl"
    exit_code = run_orchestrator([
        "--target", "dynamics", "--case-kind", "decay", "--mode", "binomial_ci",
        "--binomial-method", "clopper-pearson", "--binomial-target-width", "1.0", "--binomial-min-samples", "40",
        "--max-samples", "40", "--seed", "3",
        "--param", "sde_dt_sec=0.05", "--param", "horizon_sec=1.0",
        "--param", 'event={"kind": "terminal_exceedance", "threshold": 0.3}',
        "--output", str(output), "--dataset-csv", str(tmp_path / "dataset.csv"),
        "--dynamics-output-root", str(tmp_path / "artifacts"),
    ])

    report = json.loads(output.with_name("records.jsonl.summary.json").read_text(encoding="utf-8"))["statistical_report"]
    assert exit_code == 0
    assert report["metric"] == "c_event"
    assert report["sample_count"] == 40
    assert '"case_kind": "decay"' in output.read_text(encoding="utf-8")


COIN_IS_FILE = '''
from math import log
from pathlib import Path

from contracts.execution import TestCase
from orchestration.fixed_importance_sampling import run_fixed_importance_sampling_comparison
from orchestration.sampling_plan import load_sampling_plan
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from targets.coin.plugin import CoinBackend, CoinInterpreter


class CoinImportanceSampling:
    """P: u ~ U(0, 1). Q: 0.5 U(0, 1) + 0.5 U(0, focus), so P/Q <= 2."""

    def add_arguments(self, parser):
        parser.add_argument("--focus", type=float, default=0.3)

    def run(self, args):
        import random

        plan = load_sampling_plan(args.sampling_plan)
        focus = args.focus

        def build_case(run_kind, index):
            rng = random.Random((args.sampling_seed or 0) * 1_000_000 + index)
            sampling = {"plan_id": plan.experiment_id, "target_measure_id": plan.target_measure_id}
            if run_kind == "ordinary":
                u = rng.random()
                sampling.update(sampling_mode="uniform", importance_weight_status="not_applicable")
            else:
                u = rng.random() if rng.random() < 0.5 else focus * rng.random()
                log_q = log(0.5 + (0.5 / focus if u < focus else 0.0))
                sampling.update(sampling_mode="manual_proposal", proposal_id=plan.proposal_id,
                                importance_weight_status="available", log_p=0.0, log_q=log_q,
                                log_importance_weight=-log_q, importance_weight_upper_bound=2.0)
            return TestCase(case_id=f"coin-{run_kind}-{index}", target="coin", case_kind="coin",
                            input={"u": u, "p_heads": 0.1}, meta={"sampling": sampling})

        return run_fixed_importance_sampling_comparison(
            backend=CoinBackend(), result_interpreter=CoinInterpreter(),
            repository=ImportanceSamplingResultsRepository(Path(args.output_root)),
            max_samples=args.max_samples, build_case=build_case, metric=args.metric or "c_heads",
            confidence=args.confidence, plan_id=plan.experiment_id,
        )
'''


COIN_IS_HOOK = """

def _importance_sampling(self):
    from targets.coin.importance_sampling import CoinImportanceSampling

    return CoinImportanceSampling()


CoinPlugin.importance_sampling = property(_importance_sampling)
"""


@pytest.fixture
def coin_target_with_importance_sampling(tmp_path, monkeypatch):
    import sys

    package = tmp_path / "plugins" / "targets" / "coin"
    package.mkdir(parents=True)
    files = {**COIN_FILES, "importance_sampling.py": COIN_IS_FILE}
    files["plugin.py"] = textwrap.dedent(COIN_FILES["plugin.py"]) + COIN_IS_HOOK
    for name, source in files.items():
        (package / name).write_text(textwrap.dedent(source), encoding="utf-8")
    for module in [name for name in sys.modules if name == "targets.coin" or name.startswith("targets.coin.")]:
        monkeypatch.delitem(sys.modules, module)
    monkeypatch.syspath_prepend(str(tmp_path / "plugins"))
    reload_target_plugins()
    yield "coin"
    for module in [name for name in sys.modules if name == "targets.coin" or name.startswith("targets.coin.")]:
        sys.modules.pop(module, None)
    reload_target_plugins()


def test_a_new_target_supports_importance_sampling_from_its_package_only(
    coin_target_with_importance_sampling, tmp_path,
) -> None:
    """targets/coin/ holds the hook and the experiment; the generic CLI needs nothing else."""
    from apps.cli.importance_main import main

    plan = tmp_path / "plan.yaml"
    plan.write_text(
        "schema_version: 1\nexperiment_id: coin_is\ntarget: coin\n"
        "target_measure: {id: coin_p, definition: {}}\n"
        "proposal: {id: coin_q, mode: manual_proposal, definition: {}}\n",
        encoding="utf-8",
    )

    exit_code = main(["--sampling-plan", str(plan), "--output-root", str(tmp_path / "run"),
                      "--max-samples", "300", "--sampling-seed", "2", "--focus", "0.2"])

    comparison = json.loads((tmp_path / "run" / "comparison.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert comparison["target"] == "coin"
    importance = comparison["diagnostic"]["importance"]
    assert importance["sample_size"] == 300
    assert importance["lower_bound"] <= 0.1 <= importance["upper_bound"]
    assert comparison["guarantee"]["hoeffding"]["importance"]["max_importance_weight"] == 2.0


# --- AWSIM-type, PRISM-type and container-profile targets --------------------

COMMON_PLUGIN_HEAD = """
from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RawRunResult, RunStatus, TestCase
from contracts.target_plugin import TargetComponents


class Backend:
    def run(self, test_case):
        return RawRunResult(case_id=test_case.case_id, target=test_case.target, case_kind=test_case.case_kind,
                            status=RunStatus.SUCCESS, meta={"output": self.output(test_case.input)})


class Interpreter:
    def interpret_raw_run_result(self, raw):
        return EvaluationRecord(case_id=raw.case_id, target=raw.target, case_kind=raw.case_kind, status=raw.status,
                                output=dict(raw.meta["output"]),
                                meta=ensure_evaluation_meta({}, source_module="targets.plugin"))
"""

WALK_PLUGIN = COMMON_PLUGIN_HEAD + """

class WalkBackend(Backend):
    def output(self, inputs):
        return {"position": float(inputs["step"])}


class WalkStrategy:
    \"\"\"A strategy of this target only: five steps, then stop.\"\"\"

    def __init__(self, case_kind):
        self.case_kind, self.step, self.stop_reason = case_kind, 0, ""

    def next_test_case(self):
        if self.step >= 5:
            self.stop_reason = "walk finished"
            return None
        self.step += 1
        return TestCase(case_id=f"walk-{self.step}", target="walk", case_kind=self.case_kind,
                        input={"step": self.step})


class WalkPlugin:
    name = "walk"
    default_case_kind = "walk"

    def build_components(self, args):
        return TargetComponents(backend=WalkBackend(), result_interpreter=Interpreter())

    def select_strategy(self, config):
        return None if config.params else "walk_steps"

    @property
    def strategies(self):
        from orchestration.strategy_registry import StrategySpec

        return {"walk_steps": StrategySpec(lambda orchestrator, config: WalkStrategy(config.case_kind),
                                           target_total=lambda config, last: last + 5)}


PLUGIN = WalkPlugin()
"""

DICE_PLUGIN = COMMON_PLUGIN_HEAD + """

class DiceBackend(Backend):
    def output(self, inputs):
        if inputs.get("execution_kind") == "model_check":
            return {"dice_exact_six": 1.0 / 6.0}
        import random

        return {"c_six": int(random.Random(inputs.get("sample_index", 0)).randint(1, 6) == 6)}


class DicePlugin:
    name = "dice"
    default_case_kind = "dice"
    fixed_parameter_reason_prefix = "DICE_FIXED_SAMPLING"
    run_model_check_once = True
    exact_probability_columns = {"bounded_failure_probability": "dice_exact_six"}
    exact_probability_metric = "c_six"
    exact_model_check_failure_reason = "DICE exact check failed"

    def build_components(self, args):
        return TargetComponents(backend=DiceBackend(), result_interpreter=Interpreter())

    def select_strategy(self, config):
        return "fixed_parameter_sampling" if config.params and config.run_mode == "binomial_ci" else None


PLUGIN = DicePlugin()
"""

PROFILE_FILE = """
from runtime.container.profile import ContainerLaunchProfile

CONTAINER_LAUNCH_PROFILE = ContainerLaunchProfile(name="walk_cpu", image="example/walk:1", target="walk",
                                                  requires_gpu=False, requires_ros=False,
                                                  requires_runtime_monitor=False)
"""


@pytest.fixture
def make_target(tmp_path, monkeypatch):
    import sys

    from runtime.container.profile_registry import reload_container_profiles

    root = tmp_path / "plugins"
    created: list[str] = []

    def make(name: str, files: dict[str, str]) -> str:
        for relative, source in files.items():
            path = root / "targets" / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source), encoding="utf-8")
        created.append(name)
        if str(root) not in sys.path:
            monkeypatch.syspath_prepend(str(root))
        reload_target_plugins()
        reload_container_profiles()
        return name

    yield make
    for module in [m for m in sys.modules if any(m == f"targets.{n}" or m.startswith(f"targets.{n}.") for n in created)]:
        sys.modules.pop(module, None)
    reload_target_plugins()
    reload_container_profiles()


def test_a_target_with_its_own_strategy_runs_without_shared_changes(make_target, tmp_path) -> None:
    from apps.cli.orchestrator_main import run_orchestrator

    make_target("walk", {"plugin.py": WALK_PLUGIN})
    output = tmp_path / "records.jsonl"

    exit_code = run_orchestrator(["--target", "walk", "--mode", "explore", "--output", str(output),
                                  "--dataset-csv", str(tmp_path / "dataset.csv")])

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert [record["output"]["position"] for record in records] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_fixed_parameter_target_reuses_the_shared_strategy(make_target, tmp_path, capsys) -> None:
    from apps.cli.orchestrator_main import run_orchestrator

    make_target("dice", {"plugin.py": DICE_PLUGIN})
    output = tmp_path / "records.jsonl"

    exit_code = run_orchestrator([
        "--target", "dice", "--mode", "binomial_ci", "--binomial-target", "c_six",
        "--binomial-method", "clopper-pearson", "--binomial-target-width", "1.0", "--binomial-min-samples", "30",
        "--max-samples", "30", "--param", "faces=6",
        "--output", str(output), "--dataset-csv", str(tmp_path / "dataset.csv"),
    ])

    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert sum(1 for record in records if "c_six" in record["output"]) == 30
    assert sum(1 for record in records if "dice_exact_six" in record["output"]) == 1
    # The shared strategy read the exact probability from this target's own column.
    exact = summary["statistical_report"]["exact_model_check"]
    assert exact["bounded_failure_probability"] == pytest.approx(1 / 6, abs=1e-4)  # stored with 4 decimals
    assert exact["comparison_metric"] == "c_six"


def test_a_target_brings_its_own_container_profile(make_target) -> None:
    from apps.cli.orchestrator_main import build_parser
    from runtime.container.profile import resolve_container_launch_profile
    from runtime.container.profile_registry import list_container_profiles

    make_target("walk", {"plugin.py": WALK_PLUGIN, "container_profiles/walk_cpu.py": PROFILE_FILE})

    assert list_container_profiles("walk") == ("walk_cpu",)
    assert resolve_container_launch_profile("walk_cpu").requires_gpu is False
    args = build_parser().parse_args(["--target", "walk", "--container-profile", "walk_cpu", "--output", "o.jsonl"])
    assert args.container_profile == "walk_cpu"
