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


def test_a_failed_case_is_queued_again_until_its_attempts_run_out() -> None:
    queue = TaskQueue()
    queue.configure_retries(3)
    queue.add_task({"case_id": "c1", "sample_index": 1})
    queue.add_task({"case_id": "c2", "sample_index": 2})

    first = queue.get_next_task()
    queue.report_completion(first["global_loop_num"], "timeout")
    retry = queue.get_next_task()  # the retry goes first
    assert retry["case_id"] == "c1" and retry["attempt_id"] == 1 and retry["retry_reason"] == "timeout"
    queue.report_completion(retry["global_loop_num"], "execution_error")
    last = queue.get_next_task()
    assert last["attempt_id"] == 2
    queue.report_completion(last["global_loop_num"], "execution_error")

    assert queue.get_next_task()["case_id"] == "c2"
    snapshot = queue.get_snapshot()
    assert snapshot["retried_count"] == 2
    assert [task["sample_index"] for task in snapshot["abandoned_tasks"]] == [1]


def test_a_case_not_back_in_time_is_queued_again_and_counted_finished() -> None:
    now = [0.0]
    queue = TaskQueue(time_func=lambda: now[0])
    queue.configure_retries(2, case_timeout_sec=100.0)
    queue.add_task({"case_id": "c1", "sample_index": 1})
    taken = queue.get_next_task()
    now[0] = 50.0
    assert queue.requeue_stale_tasks() == []
    now[0] = 150.0
    assert queue.requeue_stale_tasks() == [taken["global_loop_num"]]
    assert queue.completed_count == queue.dispatched_count == 1

    again = queue.get_next_task()
    assert again["attempt_id"] == 1 and again["retry_reason"] == "lost"
    now[0] = 300.0
    queue.requeue_stale_tasks()
    assert queue.get_snapshot()["abandoned_tasks"][0]["abandon_reason"] == "lost"
    assert queue.report_completion(taken["global_loop_num"], "success") is False  # the late report is ignored


def test_without_retries_the_queue_behaves_as_before() -> None:
    queue = TaskQueue()
    queue.add_task({"case_id": "c1"})
    task = queue.get_next_task()
    queue.report_completion(task["global_loop_num"], "timeout")
    assert queue.get_next_task() is None and queue.get_snapshot()["abandoned_tasks"] == []
