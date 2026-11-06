import signal
import subprocess

from runtime.container.xvfb import XvfbConfig, XvfbController


class FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def test_xvfb_controller_start_sets_env_and_launches_process() -> None:
    commands: list[str] = []
    popen_calls: list[dict[str, object]] = []
    env: dict[str, str] = {}
    sleeper_calls: list[float] = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess(pid=199)

    controller = XvfbController(
        popen_factory=fake_popen,
        system_runner=lambda command: commands.append(command) or 0,
        sleeper=lambda seconds: sleeper_calls.append(seconds),
        env=env,
        lock_dir=None,
    )

    session = controller.start(XvfbConfig(enabled=True))

    assert session is not None
    assert session.display == ":199"
    assert env["DISPLAY"] == ":199"
    assert env["VK_ICD_FILENAMES"] == "/usr/share/vulkan/icd.d/nvidia_icd.json"
    assert commands == ["pkill -9 -f 'Xvfb :199' > /dev/null 2>&1"]
    assert sleeper_calls == [2.0]
    assert popen_calls == [
        {
            "command": ["Xvfb", ":199", "-screen", "0", "1920x1080x24"],
            "kwargs": {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            },
        }
    ]


def test_xvfb_controller_start_is_noop_when_disabled() -> None:
    controller = XvfbController(
        popen_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected popen")),
        system_runner=lambda _command: (_ for _ in ()).throw(AssertionError("unexpected cleanup")),
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("unexpected sleep")),
        env={},
    )

    session = controller.start(XvfbConfig(enabled=False))

    assert session is None


def test_xvfb_controller_stop_sends_sigkill_for_active_session() -> None:
    calls: list[tuple[int, int]] = []
    controller = XvfbController(
        signal_sender=lambda process, sig: calls.append((process.pid, sig)),
    )

    controller.stop(
        controller.start(XvfbConfig(enabled=False))  # type: ignore[arg-type]
    )
    controller.stop(
        type(
            "Session",
            (),
            {"process": FakeProcess(pid=321), "display": ":199", "vk_icd_filenames": None},
        )()
    )

    assert calls == [(321, signal.SIGKILL)]


def test_xvfb_controller_apply_environment_returns_updated_copy() -> None:
    original_env = {"PATH": "/usr/bin"}
    controller = XvfbController(env={"SHOULD_NOT": "LEAK"})

    updated_env = controller.apply_environment(
        XvfbConfig(enabled=True, display=":88", vk_icd_filenames=None),
        env=original_env,
    )

    assert updated_env == {"PATH": "/usr/bin", "DISPLAY": ":88"}
    assert original_env == {"PATH": "/usr/bin"}


def test_xvfb_controller_uses_process_supervisor() -> None:
    calls: list[tuple[str, object]] = []

    class FakeSupervisor:
        def run(self, command, **kwargs):
            calls.append(("run", command))
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        def spawn(self, command, **kwargs):
            calls.append(("spawn", command))
            process = FakeProcess(pid=199)
            process.process_id = "xvfb-1"
            return process

        def signal(self, process_id, signal_number):
            calls.append(("signal", (process_id, signal_number)))

        def release(self, process_id):
            calls.append(("release", process_id))

    controller = XvfbController(
        env={"HOME": "/home/passd"},
        sleeper=lambda _seconds: None,
        supervisor_client=FakeSupervisor(),  # type: ignore[arg-type]
        lock_dir=None,
    )
    session = controller.start(XvfbConfig(enabled=True))
    controller.stop(session)

    assert calls[0][0] == "run"
    assert calls[1] == (
        "spawn",
        ["Xvfb", ":199", "-screen", "0", "1920x1080x24"],
    )
    assert calls[2] == ("signal", ("xvfb-1", signal.SIGKILL))
    assert calls[3] == ("release", "xvfb-1")


def _controller_with_lock(tmp_path, owner_state, *, sleeps=None):
    (tmp_path / ".X11-unix").mkdir()
    (tmp_path / ".X199-lock").write_text("      4242\n")
    (tmp_path / ".X11-unix" / "X199").write_text("")
    states = list(owner_state)
    return XvfbController(
        popen_factory=lambda *_a, **_k: FakeProcess(pid=1),
        system_runner=lambda _command: 0,
        sleeper=lambda seconds: (sleeps if sleeps is not None else []).append(seconds),
        env={},
        lock_dir=tmp_path,
        process_state=lambda pid: states.pop(0) if len(states) > 1 else states[0],
    )


def test_a_lock_held_by_an_unreaped_xvfb_is_freed_before_starting(tmp_path) -> None:
    """A killed Xvfb its parent has not reaped (a zombie) kept the lock, so the new Xvfb
    exited and AWSIM started with no display (verification V8, 2026-11-06)."""
    controller = _controller_with_lock(tmp_path, ["Z"])

    assert controller.start(XvfbConfig(enabled=True)) is not None
    assert not (tmp_path / ".X199-lock").exists()
    assert not (tmp_path / ".X11-unix" / "X199").exists()


def test_a_lock_of_a_dead_owner_is_freed(tmp_path) -> None:
    controller = _controller_with_lock(tmp_path, [None])

    controller.start(XvfbConfig(enabled=True))

    assert not (tmp_path / ".X199-lock").exists()


def test_a_dying_owner_is_waited_for(tmp_path) -> None:
    sleeps: list[float] = []
    controller = _controller_with_lock(tmp_path, ["S", "S", None], sleeps=sleeps)

    controller.start(XvfbConfig(enabled=True))

    assert sleeps[:2] == [0.2, 0.2]  # waited twice, then the owner was gone
    assert not (tmp_path / ".X199-lock").exists()
