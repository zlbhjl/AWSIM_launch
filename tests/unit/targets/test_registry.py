from types import SimpleNamespace

from targets.registry import build_target_components


def test_build_target_components_disables_awchecker_for_v2_awsim() -> None:
    args = SimpleNamespace(
        target="awsim",
        case_kind="uturn",
        headless=False,
        container_profile="autoware171",
        scenario_profile="autoware171",
        ext_mode="maude",
        config_module="targets.awsim.case_kinds.uturn",
    )

    components = build_target_components(args)

    assert components.backend.config.manage_infra is True
    assert components.backend.config.include_awchecker is False
    assert components.backend.config.runtime_profile.container_profile == "autoware171"
    assert components.backend.config.reuse_infra_between_runs is True


def test_build_target_components_can_use_fresh_awsim_infra_per_sample() -> None:
    args = SimpleNamespace(
        target="awsim",
        case_kind="uturn",
        headless=False,
        container_profile="autoware171",
        scenario_profile="autoware171",
        ext_mode="cvm",
        config_module=None,
        fresh_infra_per_sample=True,
    )

    components = build_target_components(args)

    assert components.backend.config.manage_infra is True
    assert components.backend.config.reuse_infra_between_runs is False


def test_omitted_case_kind_uses_the_target_default() -> None:
    from apps.cli import orchestrator_main, worker_main

    def resolved(module, target):
        argv = ["--target", target, "--output", "out.jsonl"]
        if module is worker_main:
            argv = ["--target", target, "--param", "u_failure=0.1", "--output", "out.jsonl"]
        args = module.build_parser().parse_args(argv)
        return module.normalize_args(args, argv=argv)

    for module in (orchestrator_main, worker_main):
        bernoulli = resolved(module, "bernoulli")
        assert (bernoulli.case_kind, bernoulli.config_module) == ("bernoulli", "targets.bernoulli.plugin")
        dynamics = resolved(module, "dynamics")
        assert (dynamics.case_kind, dynamics.config_module) == ("uturn", "scenario_specs.uturn")
        assert resolved(module, "awsim").case_kind == "uturn"


def test_explicit_case_kind_is_kept() -> None:
    from apps.cli import orchestrator_main

    argv = ["--target", "bernoulli", "--case-kind", "my_case", "--output", "out.jsonl"]
    args = orchestrator_main.normalize_args(orchestrator_main.build_parser().parse_args(argv), argv=argv)

    assert args.case_kind == "my_case"
