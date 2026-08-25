from runtime.cluster.ray_client import RayActorLocator, RayConnectionConfig


class FakeRayModule:
    def __init__(self):
        self.init_calls: list[dict[str, object]] = []
        self.get_actor_calls: list[tuple[str, str | None]] = []
        self.actors: dict[str, object] = {}

    def init(self, **kwargs):
        self.init_calls.append(dict(kwargs))
        return True

    def get_actor(self, actor_name: str, namespace: str | None = None):
        self.get_actor_calls.append((actor_name, namespace))
        if actor_name not in self.actors:
            raise ValueError(actor_name)
        return self.actors[actor_name]


def test_ray_actor_locator_connects_and_returns_actor() -> None:
    ray_module = FakeRayModule()
    ray_module.actors["TaskQueueActor"] = object()
    locator = RayActorLocator(
        ray_module=ray_module,
        monotonic_fn=lambda: 0.0,
        sleep_fn=lambda _seconds: None,
    )

    resolved_ray, actor = locator.connect_and_get_actor(
        "TaskQueueActor",
        config=RayConnectionConfig(
            address="ray://127.0.0.1:10001",
            namespace="awsim_cluster",
            actor_lookup_timeout_sec=1.0,
            actor_lookup_poll_interval_sec=0.1,
        ),
    )

    assert resolved_ray is ray_module
    assert actor is ray_module.actors["TaskQueueActor"]
    assert ray_module.init_calls == [
        {
            "address": "ray://127.0.0.1:10001",
            "namespace": "awsim_cluster",
            "ignore_reinit_error": True,
        }
    ]
    assert ray_module.get_actor_calls == [("TaskQueueActor", "awsim_cluster")]


def test_ray_actor_locator_times_out_when_actor_never_appears() -> None:
    time_values = iter([0.0, 0.2, 0.4, 0.6])
    sleep_calls: list[float] = []
    ray_module = FakeRayModule()
    locator = RayActorLocator(
        ray_module=ray_module,
        monotonic_fn=lambda: next(time_values),
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    try:
        locator.connect_and_get_actor(
            "TaskQueueActor",
            config=RayConnectionConfig(
                namespace="awsim_cluster",
                actor_lookup_timeout_sec=0.5,
                actor_lookup_poll_interval_sec=0.1,
            ),
        )
    except TimeoutError as exc:
        assert "TaskQueueActor" in str(exc)
    else:
        raise AssertionError("TimeoutError was not raised")

    assert sleep_calls == [0.1, 0.1]
