from pathlib import Path

from runtime.container.infra_tasks import build_awsim_infra_tasks
from runtime.container.profile import build_runtime_profile


def test_build_awsim_infra_tasks_returns_expected_roles() -> None:
    tasks = build_awsim_infra_tasks(
        case_kind="uturn",
        output_dir="/tmp/sim-out",
        launch_dir="/tmp/awsim-launch",
        ext_mode="maude",
        home_dir="/tmp/home",
        is_master=False,
    )

    assert [task.name for task in tasks] == [
        "AWSIM Labs",
        "Autoware",
        "Runtime Monitor",
        "AW Checker (Safety Evaluator)",
    ]
    assert tasks[0].work_dir == Path("/tmp/home/awsim_labs")
    assert tasks[0].command == "./awsim_labs.x86_64 -noise false"
    assert tasks[1].source_setup is True
    assert tasks[1].delay_sec == 90.0
    assert tasks[2].command == "python3 main.py -o /tmp/sim-out/uturn_test -n {sim_num}"
    assert tasks[2].log_filename == "runtime_monitor.log"
    assert tasks[3].resident is True
    assert tasks[3].command == "python3 awchecker.py --type uturn --ext_mode maude"


def test_build_awsim_infra_tasks_uses_master_delay_for_autoware() -> None:
    tasks = build_awsim_infra_tasks(
        case_kind="uturn",
        output_dir="/tmp/sim-out",
        launch_dir="/tmp/awsim-launch",
        home_dir="/tmp/home",
        is_master=True,
    )

    autoware_task = next(task for task in tasks if task.name == "Autoware")
    assert autoware_task.delay_sec == 40.0


def test_build_awsim_infra_tasks_can_use_runtime_profile() -> None:
    profile = build_runtime_profile(
        case_kind="uturn",
        machine_role="master",
        home_dir="/tmp/home",
        launch_dir="/tmp/awsim-launch",
    )

    tasks = build_awsim_infra_tasks(
        case_kind="uturn",
        output_dir="/tmp/sim-out",
        ext_mode="ctrv",
        runtime_profile=profile,
    )

    assert tasks[0].work_dir == Path("/tmp/home/awsim_labs").resolve()
    assert tasks[1].delay_sec == 40.0
    assert tasks[3].work_dir == Path("/tmp/awsim-launch").resolve()
    assert tasks[3].command == "python3 awchecker.py --type uturn --ext_mode ctrv"


def test_build_awsim_infra_tasks_can_skip_awchecker() -> None:
    tasks = build_awsim_infra_tasks(
        case_kind="uturn",
        output_dir="/tmp/sim-out",
        include_awchecker=False,
        home_dir="/tmp/home",
    )

    assert [task.name for task in tasks] == [
        "AWSIM Labs",
        "Autoware",
        "Runtime Monitor",
    ]
