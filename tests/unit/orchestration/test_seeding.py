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
