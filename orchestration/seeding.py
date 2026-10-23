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


# Random-number policy version 2 for fixed-N importance-sampling experiments
# (docs/sde_importance_sampling_plan.md §7). Every sample draws from streams
# spawned from one recorded 128-bit root, keyed by phase, run kind, sample
# index and purpose. Version 1 is the legacy ``seed * 1_000_000 + index`` rule.
RNG_POLICY_VERSION = 2
PHASES = {"pilot": 0, "final": 1, "reference": 2}
RUN_KINDS = {"ordinary": 0, "importance": 1}
PAIRED_RUN_KIND = 2  # ordinary and importance share streams in a paired design
STREAMS = {"input": 0, "mixture_choice": 1, "innovation": 2}
PAIRINGS = ("independent", "paired")


def new_root_entropy() -> int:
    """Fresh 128-bit entropy for an experiment root; record it before sampling."""
    return int(np.random.SeedSequence().entropy)


def stream_seed(
    root_entropy: int,
    *,
    phase: str,
    run_kind: str,
    sample_index: int,
    stream: str,
    pairing: str = "independent",
) -> int:
    """Return a reproducible 128-bit integer seed for one sample's stream.

    The result seeds both ``random.Random`` and ``numpy.random.default_rng``.
    With ``pairing="paired"`` the ordinary and importance runs of a sample use
    the same streams (common random numbers); by default they are independent.
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {sorted(PHASES)}")
    if run_kind not in RUN_KINDS:
        raise ValueError(f"run_kind must be one of {sorted(RUN_KINDS)}")
    if stream not in STREAMS:
        raise ValueError(f"stream must be one of {sorted(STREAMS)}")
    if pairing not in PAIRINGS:
        raise ValueError(f"pairing must be one of {list(PAIRINGS)}")
    if sample_index < 1:
        raise ValueError("sample_index must be positive")
    run_key = PAIRED_RUN_KIND if pairing == "paired" else RUN_KINDS[run_kind]
    sequence = np.random.SeedSequence(
        int(root_entropy),
        spawn_key=(PHASES[phase], run_key, int(sample_index), STREAMS[stream]),
    )
    words = sequence.generate_state(4, dtype=np.uint32)
    return sum(int(word) << (32 * position) for position, word in enumerate(words))


__all__ = [
    "INPUT_STREAM",
    "PAIRINGS",
    "PHASES",
    "RNG_POLICY_VERSION",
    "RUN_KINDS",
    "SDE_NOISE_STREAM",
    "STREAMS",
    "derive_seed",
    "new_root_entropy",
    "stream_seed",
]
