from orchestration.seeding import INPUT_STREAM, SDE_NOISE_STREAM, derive_seed


def test_derive_seed_is_reproducible_and_32_bit() -> None:
    value = derive_seed(42, 7, INPUT_STREAM)

    assert value == derive_seed(42, 7, INPUT_STREAM)
    assert 0 <= value < 2**32
    assert float(value) == value


def test_derive_seed_separates_seeds_samples_and_streams() -> None:
    seeds = {
        derive_seed(42, 7, INPUT_STREAM),
        derive_seed(43, 6, INPUT_STREAM),
        derive_seed(42, 8, INPUT_STREAM),
        derive_seed(42, 7, SDE_NOISE_STREAM),
    }

    assert len(seeds) == 4


def test_stream_seed_is_reproducible_and_separates_purposes() -> None:
    from orchestration.seeding import stream_seed

    keys = dict(phase="final", run_kind="importance", sample_index=3)
    assert stream_seed(42, stream="innovation", **keys) == stream_seed(42, stream="innovation", **keys)
    seeds = {stream_seed(42, stream=stream, **keys) for stream in ("input", "mixture_choice", "innovation")}
    assert len(seeds) == 3
    assert stream_seed(42, stream="input", **keys) < 2 ** 128


def test_stream_seed_separates_phase_run_kind_and_sample() -> None:
    from orchestration.seeding import stream_seed

    base = stream_seed(42, phase="final", run_kind="importance", sample_index=3, stream="input")
    assert base != stream_seed(42, phase="pilot", run_kind="importance", sample_index=3, stream="input")
    assert base != stream_seed(42, phase="final", run_kind="ordinary", sample_index=3, stream="input")
    assert base != stream_seed(42, phase="final", run_kind="importance", sample_index=4, stream="input")
    assert base != stream_seed(43, phase="final", run_kind="importance", sample_index=3, stream="input")


def test_paired_design_shares_streams_between_run_kinds() -> None:
    from orchestration.seeding import stream_seed

    ordinary = stream_seed(42, phase="final", run_kind="ordinary", sample_index=1, stream="innovation", pairing="paired")
    importance = stream_seed(42, phase="final", run_kind="importance", sample_index=1, stream="innovation", pairing="paired")
    assert ordinary == importance


def test_stream_seed_rejects_unknown_keys() -> None:
    import pytest

    from orchestration.seeding import stream_seed

    with pytest.raises(ValueError, match="phase"):
        stream_seed(1, phase="trial", run_kind="ordinary", sample_index=1, stream="input")
    with pytest.raises(ValueError, match="sample_index"):
        stream_seed(1, phase="final", run_kind="ordinary", sample_index=0, stream="input")
