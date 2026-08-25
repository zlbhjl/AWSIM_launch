from contracts.execution import TestCase
from runtime.cluster.task_queue_gateway import TaskQueueGateway, TaskQueueGatewayConfig


class FakeActor:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.status_updates = []
        self.completions = []
        self.added_tasks = []
        self.start_counts = []
        self.stop_reasons = []

    def get_next_task(self):
        if not self.payloads:
            return None
        return self.payloads.pop(0)

    def update_worker_status(self, worker_id: str, status: str):
        self.status_updates.append((worker_id, status))
        return True

    def report_completion(self, loop_num: int, status: str):
        self.completions.append((loop_num, status))
        return True

    def add_task(self, payload):
        self.added_tasks.append(dict(payload))
        return True

    def get_status(self):
        return len(self.payloads), len(self.completions), {"worker-21": "waiting"}

    def set_start_counts(self, count: int):
        self.start_counts.append(count)
        return True

    def set_stop_signal(self, reason: str = "Target Reached or Master Stopped"):
        self.stop_reasons.append(reason)
        return True


def test_task_queue_gateway_builds_test_case_from_payload() -> None:
    gateway = TaskQueueGateway(
        actor=FakeActor(
            [
                {
                    "dx0": 15.0,
                    "dy0": -1.0,
                    "reason": "STEP2: Exploration",
                    "global_loop_num": 7,
                    "tags": ["queue", "explore"],
                }
            ]
        ),
        config=TaskQueueGatewayConfig(case_kind="uturn"),
    )

    test_case = gateway.fetch_next()

    assert isinstance(test_case, TestCase)
    assert test_case.case_id == "queue_case_7"
    assert test_case.target == "awsim"
    assert test_case.case_kind == "uturn"
    assert test_case.reason == "STEP2: Exploration"
    assert test_case.tags == ["queue", "explore"]
    assert test_case.input == {"dx0": 15.0, "dy0": -1.0}
    assert test_case.meta["global_loop_num"] == 7


def test_task_queue_gateway_returns_none_when_queue_is_empty() -> None:
    gateway = TaskQueueGateway(actor=FakeActor([]))

    assert gateway.fetch_next() is None


def test_task_queue_gateway_raises_stop_iteration_on_stop_command() -> None:
    gateway = TaskQueueGateway(
        actor=FakeActor([{"system_command": "stop", "reason": "master_stopped"}])
    )

    try:
        gateway.fetch_next()
    except StopIteration as exc:
        assert str(exc) == "master_stopped"
    else:
        raise AssertionError("StopIteration was not raised")

    assert gateway.last_stop_reason == "master_stopped"


def test_task_queue_gateway_proxies_status_and_completion_calls() -> None:
    actor = FakeActor([])
    gateway = TaskQueueGateway(actor=actor)

    assert gateway.update_worker_status("worker-21", "running") is True
    assert gateway.report_completion(11, "success") is True
    assert gateway.add_task({"case_id": "case_1"}) is True
    assert gateway.set_start_counts(7) is True
    assert gateway.set_stop_signal("done") is True
    assert gateway.get_status() == (0, 1, {"worker-21": "waiting"})
    assert gateway.get_snapshot()["dispatched_count"] == 1
    assert actor.status_updates == [("worker-21", "running")]
    assert actor.completions == [(11, "success")]
    assert actor.added_tasks == [{"case_id": "case_1"}]
    assert actor.start_counts == [7]
    assert actor.stop_reasons == ["done"]
