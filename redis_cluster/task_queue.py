import ray


@ray.remote
class TaskQueueActor:
    """
    司令塔 (TaskQueueActor)
    ワーカーからのアクセスをスレッドセーフに受け付けるキュー管理役。
    マスター機(21号機)のローカルで起動され、全ワーカーがタスクを取得する。
    """

    def __init__(self):
        self.queue = []
        self.completed_count = 0
        self.stop_signal = False
        self.dispatched_count = 0  # マスターが発行する絶対的なループ番号
        self.worker_statuses = {}  # 各ワーカーのリアルタイムな状態

    def update_worker_status(self, worker_id: str, status: str):
        self.worker_statuses[worker_id] = status

    def add_task(self, task: dict):
        self.queue.append(task)

    def get_next_task(self):
        if self.stop_signal:
            return {"system_command": "stop", "reason": "Target Reached or Master Stopped"}
        if len(self.queue) > 0:
            task = self.queue.pop(0)
            self.dispatched_count += 1
            task["global_loop_num"] = self.dispatched_count
            return task
        return None

    def set_start_counts(self, count: int):
        if self.dispatched_count == 0:
            self.dispatched_count = count
            self.completed_count = count

    def report_completion(self, loop_num: int, status: str):
        self.completed_count += 1
        return True

    def get_status(self):
        return len(self.queue), self.completed_count, self.worker_statuses

    def set_stop_signal(self):
        self.stop_signal = True
