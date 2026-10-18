import os

from decision_traces import build_traces, load_traces, write_traces


def test_strategies_make_the_same_decisions(tmp_path) -> None:
    traces = build_traces(tmp_path)
    if os.environ.get("UPDATE_DECISION_TRACES") == "1":
        write_traces(traces)

    expected = load_traces()

    assert set(traces) == set(expected)
    for key in expected:
        assert traces[key] == expected[key], key
