import types

import pandas as pd

import point_extractors


def test_extract_jama_edge_returns_all_param_names_for_extended_scenarios() -> None:
    df = pd.DataFrame(
        [
            {
                "dx0": 12.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "cutin_vy": 1.4,
                "c_collision": 1,
                "theory_margin_a_human": 0.5,
            }
        ]
    )

    points = point_extractors.extract_jama_edge(
        df,
        ["dx0", "ego_speed", "npc_speed", "cutin_vy"],
        types.SimpleNamespace(),
    )

    assert points == [
        {"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0, "cutin_vy": 1.4}
    ]


def test_filter_by_region_and_bounds_returns_empty_when_theory_columns_are_missing() -> None:
    df = pd.DataFrame(
        [
            {
                "dx0": 12.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "c_collision": 0,
            }
        ]
    )

    filtered = point_extractors.filter_by_region_and_bounds(df, region="jama_safe")

    assert filtered is not None
    assert filtered.empty
    assert list(filtered.columns) == list(df.columns)


def test_extract_verify_consistency_supports_extra_param_axes() -> None:
    df = pd.DataFrame(
        [
            {
                "dx0": 12.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "cutin_vy": 1.2,
                "c_collision": 0,
                "min_ttc": 1.1,
            },
            {
                "dx0": 13.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "cutin_vy": 1.4,
                "c_collision": 0,
                "min_ttc": 1.0,
            },
        ]
    )

    points = point_extractors.extract_verify_consistency(
        df,
        ["dx0", "ego_speed", "npc_speed", "cutin_vy"],
        types.SimpleNamespace(TTC_EDGE_THRESHOLD=1.5, WORST_TTC_CASES=10),
    )

    assert points == [
        {"dx0": 13.0, "ego_speed": 30.0, "npc_speed": 10.0, "cutin_vy": 1.4},
        {"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0, "cutin_vy": 1.2},
    ]
