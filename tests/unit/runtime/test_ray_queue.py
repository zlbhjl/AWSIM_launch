from runtime.cluster.ray_queue import TaskQueue


def test_task_queue_dispatches_global_loop_numbers() -> None:
    queue = TaskQueue()
    queue.add_task({"dx0": 15.0, "reason": "STEP2"})
    queue.add_task({"dx0": 16.0, "reason": "STEP3"})

    first = queue.get_next_task()
    second = queue.get_next_task()

    assert first is not None
    assert second is not None
    assert first["global_loop_num"] == 1
    assert second["global_loop_num"] == 2


def test_task_queue_tracks_completion_and_worker_status() -> None:
    queue = TaskQueue(time_func=lambda: 123.5)

    queue.update_worker_status("worker-21", "running")
    assert queue.report_completion(1, "success") is True

    queue_size, completed_count, worker_statuses = queue.get_status()
    assert queue_size == 0
    assert completed_count == 1
    assert worker_statuses == {"worker-21": "running"}
    assert queue.get_snapshot()["worker_status_updated_at"] == {"worker-21": 123.5}


def test_task_queue_returns_stop_signal_after_stop_request() -> None:
    queue = TaskQueue()
    queue.set_stop_signal()

    payload = queue.get_next_task()

    assert payload == {
        "system_command": "stop",
        "reason": "Target Reached or Master Stopped",
    }


def test_task_queue_tracks_in_flight_task_until_completion() -> None:
    queue = TaskQueue()
    queue.add_task({"case_id": "case_1", "dx0": 15.0})

    task = queue.get_next_task()

    assert task is not None
    assert task["global_loop_num"] == 1
    assert queue.get_snapshot()["queue_size"] == 0
    assert queue.get_snapshot()["in_flight_count"] == 1
    assert queue.get_in_flight_tasks() == [
        {"case_id": "case_1", "dx0": 15.0, "global_loop_num": 1}
    ]

    assert queue.report_completion(1, "success") is True
    assert queue.get_snapshot()["completed_count"] == 1
    assert queue.get_snapshot()["in_flight_count"] == 0
    assert queue.get_in_flight_tasks() == []


def test_task_queue_ignores_duplicate_completion() -> None:
    queue = TaskQueue()
    queue.add_task({"case_id": "case_1"})
    assert queue.get_next_task() is not None

    assert queue.report_completion(1, "success") is True
    assert queue.report_completion(1, "success") is False

    assert queue.get_snapshot()["completed_count"] == 1
    assert queue.get_snapshot()["in_flight_count"] == 0
