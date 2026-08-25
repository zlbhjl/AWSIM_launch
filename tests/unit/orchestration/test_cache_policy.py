from orchestration.cache_policy import resolve_strategy_cache_size


def test_resolve_strategy_cache_size_uses_legacy_worker_multiplier() -> None:
    assert resolve_strategy_cache_size(worker_count=3) == 6


def test_resolve_strategy_cache_size_honors_explicit_override() -> None:
    assert resolve_strategy_cache_size(worker_count=3, explicit_cache_size=5) == 5
