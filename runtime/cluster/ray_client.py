from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any, Callable


RAY_CONTROL_PLANE_ERROR_MARKERS = (
    "Ray Client is not connected",
    "Failed to reconnect",
    "Failed to connect to GCS",
    "GCS may have been killed",
    "Starting Ray client server failed",
    "Initialization failure from server",
    "ConnectionAbortedError",
    "Failed to connect to Ray",
    "raylet",
    "gcs",
)


class RayControlPlaneError(RuntimeError):
    pass


def is_ray_control_plane_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in RAY_CONTROL_PLANE_ERROR_MARKERS)


@dataclass(frozen=True)
class RayConnectionConfig:
    address: str | None = None
    namespace: str | None = None
    actor_lookup_timeout_sec: float = 30.0
    actor_lookup_poll_interval_sec: float = 5.0
    connect_retries: int = 6
    connect_retry_interval_sec: float = 15.0


class RayActorLocator:
    def __init__(
        self,
        *,
        ray_module: Any | None = None,
        monotonic_fn: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.ray_module = ray_module
        self.monotonic = monotonic_fn
        self.sleep = sleep_fn

    def connect(self, config: RayConnectionConfig) -> Any:
        resolved_ray = self._resolve_ray_module()
        init_kwargs: dict[str, object] = {
            "ignore_reinit_error": True,
        }
        if config.address:
            init_kwargs["address"] = config.address
        if config.namespace:
            init_kwargs["namespace"] = config.namespace
        max_attempts = max(int(config.connect_retries), 1)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resolved_ray.init(**init_kwargs)
                return resolved_ray
            except Exception as exc:
                last_error = exc
                shutdown = getattr(resolved_ray, "shutdown", None)
                if callable(shutdown):
                    shutdown()
                if attempt >= max_attempts:
                    break
                self.sleep(max(float(config.connect_retry_interval_sec), 0.0))
        raise RayControlPlaneError(
            f"Failed to connect to Ray after {max_attempts} attempt(s)"
        ) from last_error

    def connect_and_get_actor(
        self,
        actor_name: str,
        *,
        config: RayConnectionConfig,
    ) -> tuple[Any, Any]:
        resolved_ray = self.connect(config)
        actor = self.get_actor(
            actor_name,
            namespace=config.namespace,
            timeout_sec=config.actor_lookup_timeout_sec,
            poll_interval_sec=config.actor_lookup_poll_interval_sec,
            ray_module=resolved_ray,
        )
        return resolved_ray, actor

    def get_actor(
        self,
        actor_name: str,
        *,
        namespace: str | None = None,
        timeout_sec: float = 30.0,
        poll_interval_sec: float = 5.0,
        ray_module: Any | None = None,
    ) -> Any:
        resolved_ray = ray_module or self._resolve_ray_module()
        deadline = self.monotonic() + max(float(timeout_sec), 0.0)
        last_error: Exception | None = None

        while True:
            try:
                return resolved_ray.get_actor(actor_name, namespace=namespace)
            except ValueError as exc:
                last_error = exc
                if self.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for Ray actor '{actor_name}'"
                    ) from exc
                self.sleep(max(float(poll_interval_sec), 0.0))
            except Exception as exc:  # pragma: no cover - defensive branch
                last_error = exc
                if is_ray_control_plane_error(exc):
                    raise RayControlPlaneError(
                        f"Failed to resolve Ray actor '{actor_name}' due to control-plane loss"
                    ) from exc
                raise RuntimeError(
                    f"Failed to resolve Ray actor '{actor_name}'"
                ) from exc

        if last_error is not None:  # pragma: no cover - unreachable guard
            raise last_error

    def _resolve_ray_module(self) -> Any:
        if self.ray_module is not None:
            return self.ray_module

        import ray as resolved_ray  # type: ignore

        self.ray_module = resolved_ray
        return resolved_ray


__all__ = [
    "RayControlPlaneError",
    "RayActorLocator",
    "RayConnectionConfig",
    "is_ray_control_plane_error",
]
