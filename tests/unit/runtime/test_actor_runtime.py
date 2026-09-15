from pathlib import Path

from runtime.cluster.actor_runtime import ActorRuntime, DetachedActorConfig


class FakeNodeAffinitySchedulingStrategy:
    def __init__(self, node_id: str, soft: bool) -> None:
        self.node_id = node_id
        self.soft = soft


class FakeRuntimeContext:
    def get_node_id(self) -> str:
        return "node-1"


class FakeRemoteClass:
    def __init__(self, ray_module, backend_class):
        self.ray_module = ray_module
        self.backend_class = backend_class
        self.option_kwargs = {}

    def options(self, **kwargs):
        self.option_kwargs = dict(kwargs)
        return self

    def remote(self, *args, **kwargs):
        instance = self.backend_class(*args, **kwargs)
        name = self.option_kwargs["name"]
        self.ray_module.actors[name] = instance
        self.ray_module.actor_options[name] = dict(self.option_kwargs)
        return instance


class FakeRayModule:
    class util:
        class scheduling_strategies:
            NodeAffinitySchedulingStrategy = FakeNodeAffinitySchedulingStrategy

    def __init__(self) -> None:
        self.actors = {}
        self.actor_options = {}
        self.init_calls = []
        self.killed = []

    def init(self, **kwargs):
        self.init_calls.append(dict(kwargs))

    def get_actor(self, actor_name: str, namespace: str | None = None):
        actor = self.actors.get(actor_name)
        if actor is None:
            raise ValueError(actor_name)
        return actor

    def remote(self, backend_class):
        return FakeRemoteClass(self, backend_class)

    def get_runtime_context(self):
        return FakeRuntimeContext()

    def kill(self, actor, no_restart=True):
        for name, current in list(self.actors.items()):
            if current is actor:
                self.killed.append((name, no_restart))
                self.actors.pop(name, None)


def test_actor_runtime_creates_and_reuses_detached_task_queue_actor() -> None:
    ray_module = FakeRayModule()
    runtime = ActorRuntime(ray_module=ray_module)
    config = DetachedActorConfig(name="TaskQueueActor", namespace="awsim_cluster")

    first = runtime.ensure_task_queue_actor(config)
    second = runtime.ensure_task_queue_actor(config)

    assert first is second
    assert ray_module.init_calls[0]["namespace"] == "awsim_cluster"
    assert ray_module.actor_options["TaskQueueActor"]["lifetime"] == "detached"
    first.add_task({"case_id": "case_1"})
    assert first.get_snapshot()["queue_size"] == 1


def test_actor_runtime_shared_store_actor_writes_dataset_rows(tmp_path: Path) -> None:
    ray_module = FakeRayModule()
    runtime = ActorRuntime(ray_module=ray_module)
    dataset_csv = tmp_path / "uturn_dataset.csv"
    config = DetachedActorConfig(name="SharedStoreActor", namespace="awsim_cluster")

    actor = runtime.ensure_shared_store_actor(config, dataset_csv_path=str(dataset_csv))
    actor.buffer_parameters(3, {"dx0": 15.0}, reason="boundary")
    merged = actor.merge_result({"loop_num": 3, "status": "success"})

    assert merged is not None
    assert dataset_csv.read_text(encoding="utf-8").startswith("dx0,loop_num,status,reason")


def test_actor_runtime_shared_store_actor_writes_central_jsonl(tmp_path: Path) -> None:
    ray_module = FakeRayModule()
    runtime = ActorRuntime(ray_module=ray_module)
    dataset_csv = tmp_path / "prism_dataset.csv"
    records_jsonl = tmp_path / "records.jsonl"
    config = DetachedActorConfig(name="PrismSharedStoreActor", namespace="awsim_cluster")

    actor = runtime.ensure_shared_store_actor(
        config,
        dataset_csv_path=str(dataset_csv),
        records_jsonl_path=str(records_jsonl),
    )
    written = actor.append_evaluation_record(
        {
            "case_id": "prism_1",
            "target": "prism",
            "status": "success",
        }
    )

    assert written is True
    assert actor.get_records_jsonl_path() == str(records_jsonl.resolve())
    assert records_jsonl.read_text(encoding="utf-8").strip() == (
        '{"case_id": "prism_1", "target": "prism", "status": "success"}'
    )


def test_actor_runtime_can_stop_actor() -> None:
    ray_module = FakeRayModule()
    runtime = ActorRuntime(ray_module=ray_module)
    config = DetachedActorConfig(name="TaskQueueActor", namespace="awsim_cluster")
    runtime.ensure_task_queue_actor(config)

    stopped = runtime.stop_actor(config)

    assert stopped is True
    assert ray_module.killed == [("TaskQueueActor", True)]
