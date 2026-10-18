import os

from coverage_baseline import build_baseline, load_baseline, write_baseline


def test_coverage_repetitions_reproduce_their_recorded_results(tmp_path) -> None:
    baseline = build_baseline(tmp_path)
    if os.environ.get("UPDATE_COVERAGE_BASELINE") == "1":
        write_baseline(baseline)

    expected = load_baseline()

    assert set(baseline) == set(expected)
    for key in expected:
        assert baseline[key] == expected[key], key
