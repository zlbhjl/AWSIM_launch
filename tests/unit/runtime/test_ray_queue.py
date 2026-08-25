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
    queue = TaskQueue()

    queue.update_worker_status("worker-21", "running")
    assert queue.report_completion(1, "success") is True

    queue_size, completed_count, worker_statuses = queue.get_status()
    assert queue_size == 0
    assert completed_count == 1
    assert worker_statuses == {"worker-21": "running"}


def test_task_queue_returns_stop_signal_after_stop_request() -> None:
    queue = TaskQueue()
    queue.set_stop_signal()

    payload = queue.get_next_task()

    assert payload == {
        "system_command": "stop",
        "reason": "Target Reached or Master Stopped",
    }
