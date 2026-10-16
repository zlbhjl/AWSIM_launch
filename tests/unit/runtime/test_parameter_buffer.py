from datetime import datetime, timedelta

from runtime.repository.parameter_buffer import ParameterBuffer


def test_parameter_buffer_put_get_and_pop() -> None:
    buffer = ParameterBuffer(timeout_sec=10)
    now = datetime(2026, 8, 3, 12, 0, 0)
    buffer.put(7, {"dx0": 15.0}, reason="explore", timestamp=now)

    entry = buffer.get(7)
    assert entry is not None
    assert entry.params == {"dx0": 15.0}
    assert entry.reason == "explore"
    assert buffer.pop(7) is not None
    assert buffer.get(7) is None


def test_parameter_buffer_cleanup_stale_returns_removed_keys() -> None:
    buffer = ParameterBuffer(timeout_sec=10)
    now = datetime(2026, 8, 3, 12, 0, 0)
    buffer.put(1, {"dx0": 10.0}, timestamp=now)
    buffer.put(2, {"dx0": 20.0}, timestamp=now + timedelta(seconds=9))

    stale = buffer.cleanup_stale(now=now + timedelta(seconds=11))
    assert stale == [1]
    assert buffer.get(1) is None
    assert buffer.get(2) is not None
