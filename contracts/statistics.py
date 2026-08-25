from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias


BoundsRange: TypeAlias = tuple[float, float]
BoundsMap: TypeAlias = dict[str, BoundsRange]


def _normalize_bounds(bounds: BoundsMap | None) -> BoundsMap | None:
    if bounds is None:
        return None

    normalized: BoundsMap = {}
    for name, raw_range in dict(bounds).items():
        lower, upper = float(raw_range[0]), float(raw_range[1])
        if lower > upper:
            raise ValueError(f"Bounds for {name} must satisfy lower <= upper")
        normalized[str(name)] = (lower, upper)
    return normalized


def _normalize_interval(interval: BoundsRange | None) -> BoundsRange | None:
    if interval is None:
        return None
    lower, upper = float(interval[0]), float(interval[1])
    if lower > upper:
        raise ValueError("interval must satisfy lower <= upper")
    return (lower, upper)


@dataclass(slots=True)
class StatisticalRequest:
    method: str
    metric: str
    bounds: BoundsMap | None = None
    confidence: float = 0.95
    target_width: float | None = None
    options: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.method = str(self.method)
        self.metric = str(self.metric)
        self.bounds = _normalize_bounds(self.bounds)
        self.confidence = float(self.confidence)
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
        if self.target_width is not None:
            self.target_width = float(self.target_width)
            if self.target_width < 0.0:
                raise ValueError("target_width must be non-negative")
        self.options = dict(self.options)


@dataclass(slots=True)
class StatisticalReport:
    method: str
    metric: str
    sample_count: int
    estimate: float | None
    interval: BoundsRange | None
    sufficient: bool
    next_action: str
    diagnostics: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.method = str(self.method)
        self.metric = str(self.metric)
        self.sample_count = int(self.sample_count)
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        self.estimate = None if self.estimate is None else float(self.estimate)
        self.interval = _normalize_interval(self.interval)
        self.sufficient = bool(self.sufficient)
        self.next_action = str(self.next_action)
        self.diagnostics = dict(self.diagnostics)

    @property
    def interval_width(self) -> float | None:
        if self.interval is None:
            return None
        return float(self.interval[1] - self.interval[0])


__all__ = [
    "BoundsMap",
    "BoundsRange",
    "StatisticalReport",
    "StatisticalRequest",
]
