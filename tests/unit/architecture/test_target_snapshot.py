import os

from target_snapshot import build_snapshot, load_snapshot, write_snapshot


def test_shared_code_builds_the_same_components_and_strategies(tmp_path) -> None:
    snapshot = build_snapshot(tmp_path)
    if os.environ.get("UPDATE_TARGET_SNAPSHOT") == "1":
        write_snapshot(snapshot)

    expected = load_snapshot()

    for section in expected:
        assert snapshot[section] == expected[section], section
    assert set(snapshot) == set(expected)
