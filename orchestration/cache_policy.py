from __future__ import annotations


def resolve_strategy_cache_size(
    *,
    worker_count: int | None,
    run_mode: str | None = None,
    resume_from: str | None = None,
    max_samples: int | None = None,
    explicit_cache_size: int | None = None,
) -> int:
    del run_mode
    del resume_from
    del max_samples

    if explicit_cache_size is not None:
        return max(int(explicit_cache_size), 1)

    effective_worker_count = max(int(worker_count or 1), 1)
    return max(effective_worker_count * 2, 1)


__all__ = ["resolve_strategy_cache_size"]
