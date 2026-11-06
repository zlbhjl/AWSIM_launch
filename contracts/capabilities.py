"""What a target can do, declared by its plugin (docs/blackbox_importance_sampling_plan.md §4).

A target plugin may declare capabilities in an optional attribute
``capabilities`` (a set of names). A shared algorithm that needs more than
"take inputs, return a metric" checks the capabilities it needs before it
runs, so an unsupported combination fails with a clear message instead of in
the middle of a run. Each capability has its own section in the per-sample
report; adding a capability never changes the existing sections.

A target that declares nothing is a pure black box: it takes inputs and
returns metric values. Plain Monte Carlo, the binomial / SPRT / DKW / EBStop
statistics and importance sampling over input distributions need nothing more.
"""

from __future__ import annotations

from collections.abc import Iterable

# The target draws its internal randomness under a component of a proposal
# chosen by the shared code and reports the log density of the realised draw
# under P and under every component (report section ``internal``).
INTERNAL_DENSITY = "internal_density"

KNOWN_CAPABILITIES: dict[str, str] = {
    INTERNAL_DENSITY: "draw its internal randomness under a given proposal component and report its log densities",
}


class CapabilityError(ValueError):
    """A target lacks a capability that the chosen algorithm needs."""


def target_capabilities(plugin: object) -> frozenset[str]:
    """The capabilities a plugin declares (none if it declares nothing)."""
    declared = getattr(plugin, "capabilities", None)
    if declared is None:
        return frozenset()
    if isinstance(declared, str) or not isinstance(declared, Iterable):
        raise TypeError("plugin capabilities must be a set of names")
    names = frozenset(declared)
    unknown = sorted(name for name in names if name not in KNOWN_CAPABILITIES)
    if unknown:
        raise ValueError(f"unknown capabilities {unknown}; known: {sorted(KNOWN_CAPABILITIES)}")
    return names


def require_capabilities(target: str, plugin: object, required: Iterable[str], *, algorithm: str) -> None:
    """Raise :class:`CapabilityError` unless the target declares every required capability."""
    missing = sorted(set(required) - target_capabilities(plugin))
    if missing:
        reasons = "; ".join(f"{name} ({KNOWN_CAPABILITIES.get(name, 'unknown')})" for name in missing)
        raise CapabilityError(
            f"target {target!r} does not declare {reasons}, so {algorithm} is not available for it"
        )


__all__ = [
    "INTERNAL_DENSITY",
    "KNOWN_CAPABILITIES",
    "CapabilityError",
    "require_capabilities",
    "target_capabilities",
]
