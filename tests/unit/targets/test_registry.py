from types import SimpleNamespace

from targets.registry import build_target_components


def test_build_target_components_disables_awchecker_for_v2_awsim() -> None:
    args = SimpleNamespace(
        target="awsim",
        case_kind="uturn",
        headless=False,
        ext_mode="maude",
        config_module="targets.awsim.case_kinds.uturn",
    )

    components = build_target_components(args)

    assert components.backend.config.manage_infra is True
    assert components.backend.config.include_awchecker is False
