"""Derive independent random seeds for individual samples.

Seeding sample ``i`` of an experiment with ``seed + i`` makes experiments with
nearby seeds reuse almost the same samples (seed S uses S+1, S+2, ... and seed
S+1 uses S+2, S+3, ...). ``derive_seed`` mixes the experiment seed and the
sample keys with numpy's SeedSequence instead, so every (seed, keys) pair gets
an unrelated stream while staying reproducible.
"""

from __future__ import annotations

import numpy as np

# Streams that share (seed, sample_index) but must not share random numbers.
INPUT_STREAM = 0
SDE_NOISE_STREAM = 1


def derive_seed(seed: int, *keys: int) -> int:
    """Return a reproducible 32-bit seed for (seed, keys).

    32 bits keep the value exact when it is written to a CSV and read back as a
    float.
    """
    entropy = [int(seed) % (1 << 64), *(int(key) % (1 << 64) for key in keys)]
    return int(np.random.SeedSequence(entropy).generate_state(1, dtype=np.uint32)[0])


__all__ = ["INPUT_STREAM", "SDE_NOISE_STREAM", "derive_seed"]
