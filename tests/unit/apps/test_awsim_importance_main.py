from __future__ import annotations

from types import SimpleNamespace

import apps.cli.awsim_importance_main as awsim_importance_main
import targets.awsim.importance_sampling as awsim_importance_sampling
from apps.cli.awsim_importance_main import build_parser


def test_cli_uses_fresh_infra_per_sample_by_default() -> None:
    args = build_parser().parse_args(
        [
            "--sampling-plan",
            "plan.yaml",
            "--output-root",
            "out",
            "--max-samples",
            "10",
        ]
    )

    assert args.reuse_infra_between_samples is False


def test_cli_allows_verified_infrastructure_reuse() -> None:
    args = build_parser().parse_args(
        [
            "--sampling-plan",
            "plan.yaml",
            "--output-root",
            "out",
            "--max-samples",
            "10",
            "--reuse-infra-between-samples",
        ]
    )

    assert args.reuse_infra_between_samples is True


def test_cli_passes_fresh_infrastructure_default_to_component_assembly(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeBackend:
        def close(self) -> None:
            captured["closed"] = True

    class FakeRunner:
        def __init__(self, *, backend, result_interpreter) -> None:
            captured["backend"] = backend
            captured["result_interpreter"] = result_interpreter

        def run_fixed_samples(self, config):
            captured["run_config"] = config
            return {"status": "ok"}

    def fake_build_target_components(args):
        captured["assembly_args"] = args
        return SimpleNamespace(backend=FakeBackend(), result_interpreter=object())

    monkeypatch.setattr(
        awsim_importance_sampling,
        "build_target_components",
        fake_build_target_components,
    )
    monkeypatch.setattr(awsim_importance_sampling, "AWSIMImportanceSamplingRunner", FakeRunner)

    exit_code = awsim_importance_main.main(
        [
            "--sampling-plan",
            "plan.yaml",
            "--output-root",
            "out",
            "--max-samples",
            "1",
        ]
    )

    assert exit_code == 0
    assert captured["assembly_args"].fresh_infra_per_sample is True
    assert captured["closed"] is True


def test_cli_can_pass_reuse_opt_in_to_component_assembly(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeBackend:
        def close(self) -> None:
            pass

    class FakeRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_fixed_samples(self, _config):
            return {"status": "ok"}

    def fake_build_target_components(args):
        captured["assembly_args"] = args
        return SimpleNamespace(backend=FakeBackend(), result_interpreter=object())

    monkeypatch.setattr(
        awsim_importance_sampling,
        "build_target_components",
        fake_build_target_components,
    )
    monkeypatch.setattr(awsim_importance_sampling, "AWSIMImportanceSamplingRunner", FakeRunner)

    awsim_importance_main.main(
        [
            "--sampling-plan",
            "plan.yaml",
            "--output-root",
            "out",
            "--max-samples",
            "1",
            "--reuse-infra-between-samples",
        ]
    )

    assert captured["assembly_args"].fresh_infra_per_sample is False
