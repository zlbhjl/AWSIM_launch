from targets.awsim.theory import build_theory_metrics


def test_build_theory_metrics_uses_cutin_specific_theory_default_lateral_speed() -> None:
    metrics_without_vy = build_theory_metrics(
        case_kind="cutin",
        values={"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0},
        config_module_name="targets.awsim.case_kinds.cutin",
    )
    metrics_with_default_vy = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.4,
        },
        config_module_name="targets.awsim.case_kinds.cutin",
    )

    assert metrics_without_vy == metrics_with_default_vy
    assert metrics_with_default_vy["theory_d_total_human"] > 0.0
    assert metrics_with_default_vy["theory_zone_a"] in {"A", "B", "C", "D"}


def test_cutin_theory_slower_lateral_speed_requires_larger_gap() -> None:
    fast_merge = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.6,
        },
    )
    slow_merge = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.2,
        },
    )

    assert slow_merge["theory_d_total_human"] > fast_merge["theory_d_total_human"]
    assert slow_merge["theory_margin_a_human"] < fast_merge["theory_margin_a_human"]


def test_cutin_theory_higher_ego_speed_reduces_margin() -> None:
    slower_ego = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.4,
        },
    )
    faster_ego = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 40.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.4,
        },
    )

    assert faster_ego["theory_d_total_human"] > slower_ego["theory_d_total_human"]
    assert faster_ego["theory_margin_a_human"] < slower_ego["theory_margin_a_human"]


def test_cutin_theory_keeps_ai_less_conservative_than_human() -> None:
    metrics = build_theory_metrics(
        case_kind="cutin",
        values={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.4,
        },
    )

    assert metrics["theory_d_total_ai"] < metrics["theory_d_total_human"]
    assert metrics["theory_margin_a_ai"] > metrics["theory_margin_a_human"]


def test_cutout_theory_uses_default_lateral_speed() -> None:
    metrics_without_vy = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "dx_f": 10.0},
        config_module_name="targets.awsim.case_kinds.cutout",
    )
    metrics_with_default_vy = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 10.0},
        config_module_name="targets.awsim.case_kinds.cutout",
    )

    assert metrics_without_vy == metrics_with_default_vy
    assert metrics_with_default_vy["theory_d_total_human"] > 0.0
    assert metrics_with_default_vy["theory_zone_a"] in {"A", "B", "C", "D"}


def test_cutout_theory_slower_lane_change_requires_more_gap() -> None:
    fast_cutout = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "cutout_vy": 1.8, "dx_f": 10.0},
    )
    slow_cutout = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "cutout_vy": 1.2, "dx_f": 10.0},
    )

    assert slow_cutout["theory_d_total_human"] > fast_cutout["theory_d_total_human"]
    assert slow_cutout["theory_margin_a_human"] < fast_cutout["theory_margin_a_human"]


def test_cutout_theory_larger_dx_f_improves_margin() -> None:
    tighter_gap = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 6.0},
    )
    wider_gap = build_theory_metrics(
        case_kind="cutout",
        values={"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 12.0},
    )

    assert wider_gap["theory_margin_a_human"] > tighter_gap["theory_margin_a_human"]
