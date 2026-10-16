import signal
import sys
import threading
import time
from pathlib import Path

from runtime.container.supervised_process import SupervisorClient
from runtime.container.supervised_process.server import ProcessSupervisorServer


def _start_server(socket_path: Path):
    server = ProcessSupervisorServer(socket_path)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert socket_path.exists()
    return server, thread


def test_process_supervisor_runs_and_tracks_processes(tmp_path: Path) -> None:
    socket_path = tmp_path / "process-supervisor.sock"
    _server, thread = _start_server(socket_path)
    client = SupervisorClient(socket_path)

    completed = client.run(
        [sys.executable, "-c", "print('probe-ok')"],
        cwd=tmp_path,
        env={},
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "probe-ok"

    log_path = tmp_path / "child.log"
    child = client.spawn(
        [sys.executable, "-c", "print('child-ok')"],
        cwd=tmp_path,
        env={},
        name="test child",
        log_path=log_path,
    )
    deadline = time.monotonic() + 2.0
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child.returncode == 0
    assert log_path.read_text(encoding="utf-8").strip() == "child-ok"
    client.release(child.process_id)

    client.shutdown()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_process_supervisor_signals_process_group(tmp_path: Path) -> None:
    socket_path = tmp_path / "process-supervisor.sock"
    _server, thread = _start_server(socket_path)
    client = SupervisorClient(socket_path)
    child = client.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={},
        name="sleeping child",
    )

    client.signal(child.process_id, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child.returncode == -signal.SIGTERM
    client.release(child.process_id)

    client.shutdown()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_long_command_does_not_block_other_supervisor_requests(tmp_path: Path) -> None:
    socket_path = tmp_path / "process-supervisor.sock"
    _server, server_thread = _start_server(socket_path)
    client = SupervisorClient(socket_path)
    slow_thread = threading.Thread(
        target=lambda: client.run(
            [sys.executable, "-c", "import time; time.sleep(0.5)"],
            cwd=tmp_path,
            env={},
        )
    )
    slow_thread.start()
    time.sleep(0.05)

    started_at = time.monotonic()
    completed = client.run(
        [sys.executable, "-c", "print('parallel-ok')"],
        cwd=tmp_path,
        env={},
    )
    elapsed = time.monotonic() - started_at

    assert completed.stdout.strip() == "parallel-ok"
    assert elapsed < 0.4
    slow_thread.join(timeout=2.0)
    client.shutdown()
    server_thread.join(timeout=2.0)
