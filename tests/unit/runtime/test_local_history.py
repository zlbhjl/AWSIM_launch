from pathlib import Path

from runtime.repository.local_history import LocalHistory


def test_local_history_append_and_load(tmp_path: Path) -> None:
    history = LocalHistory(tmp_path / "processed_loops_history.csv")
    history.append(3)
    history.append(7)

    assert history.load() == {3, 7}


def test_local_history_ignores_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "processed_loops_history.csv"
    path.write_text("1\nabc\n2\n\n", encoding="utf-8")

    history = LocalHistory(path)
    assert history.load() == {1, 2}


def test_local_history_contains_loop_num(tmp_path: Path) -> None:
    history = LocalHistory(tmp_path / "processed_loops_history.csv")
    history.append(5)

    assert history.contains(5) is True
    assert history.contains(6) is False
