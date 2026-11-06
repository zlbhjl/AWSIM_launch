"""The target side of shared importance sampling (docs/blackbox_importance_sampling_plan.md §4).

A target supports importance sampling by giving its plugin an attribute
``importance_sampling`` that returns an object with the hooks below. The shared
runner (``orchestration/importance/runner.py``) and CLI
(``python3 -m apps.cli.importance_main``) do everything else: drawing the
inputs, choosing the proposal component, the random streams, the manifest,
resume, audit, composing the weights, comparison and stopping.

Required hooks:

- ``prepare(plan, config) -> PreparedTarget``: read and check the plan for this
  target (its inputs, internal proposal, seed policy, default metric).
- ``build_case(request) -> TestCase``: the case that runs one sample as requested.
- ``build_components(args) -> TargetComponents``: backend and result interpreter
  for a CLI run (may also have ``close``).

Optional hooks: ``add_arguments(parser)`` (the target's own CLI options),
``run_options(args) -> dict`` (target values for ``ImportanceRunConfig.options``
and ``params``), ``alternative_run(args) -> dict | None`` (a target-specific
workflow, such as PRISM's proposal suggestions).

A target whose ``prepare`` returns an internal proposal must declare the
capability ``internal_density`` (contracts/capabilities.py) and report the
section ``internal`` for each importance sample (contracts/sampling.py).

The hook object is read with ``getattr`` and is deliberately not part of
``TargetPlugin``: a ``runtime_checkable`` protocol would reject every target
that does not support importance sampling.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

# Seed policies. "streams": every sample's streams are spawned from one recorded
# root (manifest, sample ids, completion audit; orchestration/seeding.py). "legacy":
# the outer seed rule seed * 1_000_000 + index with count-based resume, kept for
# the plans that were run with it (dynamics v1, AWSIM, PRISM).
SEED_POLICIES = ("streams", "legacy")


@dataclass(frozen=True)
class InternalProposal:
    """A proposal over a target's internal randomness, opaque to the shared code.

    ``components`` names the components in order (the order of ``log_q`` in the
    ``internal`` report; ``"target"`` marks P itself); ``weights`` are their
    mixture weights (summing to 1); ``defensive_weight`` is beta of the defensive
    mixture with P, if any. ``definition`` is what the target needs to draw;
    ``fingerprint_source`` is what identifies it.
    """

    components: tuple[str, ...]
    weights: tuple[float, ...]
    definition: object
    fingerprint_source: Mapping[str, object]
    defensive_weight: float | None = None
    # Upper bound of p/q of a single component, if the target knows one.
    component_bound: float | None = None


@dataclass(frozen=True)
class PreparedTarget:
    """What a target found in a sampling plan (returned by ``prepare``)."""

    default_metric: str
    seed_policy: str = "streams"
    # The input laws (orchestration/importance/input_law.py), if the plan samples inputs.
    input_laws: object | None = None
    internal_proposal: InternalProposal | None = None
    # Random streams the target uses (policy "streams"); the shared code seeds them.
    random_streams: tuple[str, ...] = ()
    # Manifest (policy "streams"): the definitions that are fingerprinted and the numerics.
    target_definition: Mapping[str, object] | None = None
    proposal_definition: Mapping[str, object] | None = None
    numerics: Mapping[str, object] = field(default_factory=dict)
    # Policy "legacy": without --sampling-seed, draw one and record it in the comparison.
    draw_seed_when_missing: bool = False
    # Anything the target keeps for build_case (for example its parsed plan).
    state: object = None

    def __post_init__(self) -> None:
        if self.seed_policy not in SEED_POLICIES:
            raise ValueError(f"seed_policy must be one of {list(SEED_POLICIES)}")


@dataclass(frozen=True)
class CaseRequest:
    """What the shared runner asks a target to run for one sample."""

    run_kind: str                       # "ordinary", "importance" (or "candidate")
    sample_index: int                   # 1-based
    plan: object                        # the SamplingPlan
    prepared: PreparedTarget
    inputs: Mapping[str, object]        # drawn by the shared input laws (may be empty)
    # One seed per random stream the target declared (policy "streams"), or
    # {"sample": seed} under the legacy policy (None if unseeded).
    seeds: Mapping[str, int | None]
    # The sampling metadata of the record (weights of the inputs, ids). A target
    # may use it as it is or build its own.
    sampling: Mapping[str, object] | None = None
    # The proposal component to draw the internal randomness from: an index,
    # "target" for P, or None (no internal proposal, or the target chooses).
    component: int | str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    options: Mapping[str, object] = field(default_factory=dict)
    sampling_plan_path: str = ""


class ImportanceSamplingHooks(Protocol):
    def prepare(self, plan: object, config: object) -> PreparedTarget:
        """Read and check ``plan`` for this target."""

    def build_case(self, request: CaseRequest) -> object:
        """The TestCase that runs ``request``."""

    def build_components(self, args: object) -> object:
        """Backend and result interpreter for a CLI run."""


def importance_sampling_hooks(plugin: object) -> ImportanceSamplingHooks | None:
    return getattr(plugin, "importance_sampling", None)


__all__ = [
    "SEED_POLICIES",
    "CaseRequest",
    "ImportanceSamplingHooks",
    "InternalProposal",
    "PreparedTarget",
    "importance_sampling_hooks",
]
