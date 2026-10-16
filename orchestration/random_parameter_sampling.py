"""Reproducible IID parameter sampling for continuous-dynamics statistics."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

from contracts.execution import TestCase
from .fixed_parameter_sampling import (
    FixedParameterSamplingStrategy,
    FixedParameterSamplingStrategyConfig,
    _safe_identifier,
    build_sampling_signature,
)


@dataclass(frozen=True)
class RandomParameterSamplingConfig(FixedParameterSamplingStrategyConfig):
    input_distribution: Mapping[str, Mapping[str, object]] | None = None
    seed: int = 42


class RandomParameterSamplingStrategy(FixedParameterSamplingStrategy):
    """IID uniform samples, reproducible from their seed and sample index."""

    def __init__(self, config: RandomParameterSamplingConfig, **kwargs: object) -> None:
        super().__init__(config, **kwargs)
        self.input_distribution = normalize_input_distribution(config.input_distribution)
        self.seed = int(config.seed)
        self.sampling_signature = build_sampling_signature(
            target=config.target,
            case_kind=config.case_kind,
            params={"fixed_params": self.params, "input_distribution": self.input_distribution, "seed": self.seed},
        )
        self.experiment_id = str(config.experiment_id or f"random-{self.sampling_signature[:12]}")

    def next_test_case(self) -> TestCase | None:
        self.waiting_for_result = False
        completed = self._load_matching_records("sample")
        report = self._evaluate(completed)
        if report is not None and report.next_action in {"stop", "stop_max_samples", "error"}:
            self.stop_reason = self._report_stop_reason(report)
            return None
        completed_count = 0 if completed is None else int(len(completed))
        if self._baseline_completed_count is None:
            self._baseline_completed_count = completed_count
        if self._baseline_completed_count + self.issued_count >= int(self.config.max_samples):
            self.stop_reason = f"Statistical sampling reached max_samples={self.config.max_samples}"
            return None
        self.issued_count += 1
        sample_index = self._baseline_completed_count + self.issued_count
        sampled = self.sample_parameters(sample_index)
        if str(self.params.get("solver_kind", "ode")) == "sde":
            sampled["sde_seed"] = int(self.params.get("sde_seed", self.seed)) + sample_index
        prefix = self.config.case_id_prefix or f"{self.config.case_kind}-{self.experiment_id}"
        return TestCase(
            case_id=f"{_safe_identifier(prefix)}-{sample_index:04d}",
            target=self.config.target, case_kind=self.config.case_kind,
            input={**self.params, **sampled, "execution_kind": "sample_path", "record_kind": "sample",
                   "experiment_id": self.experiment_id, "sampling_signature": self.sampling_signature,
                   "sample_index": sample_index, "sampling_seed": self.seed,
                   "sampling_distribution": self.input_distribution, "sampled_parameters": sampled},
            tags=list(self.config.tags),
            reason=f"{self.config.reason_prefix}: {self.experiment_id} sample={sample_index}",
            meta={"source_module": "orchestration.random_parameter_sampling", "experiment_id": self.experiment_id,
                  "sampling_signature": self.sampling_signature, "sample_index": sample_index, "sampling_seed": self.seed},
        )

    def sample_parameters(self, sample_index: int) -> dict[str, float]:
        rng = random.Random(self.seed + int(sample_index))
        return {name: rng.uniform(float(spec["min"]), float(spec["max"])) for name, spec in self.input_distribution.items()}

    def statistical_report_payload(self) -> dict[str, object] | None:
        # The final worker batch may have completed after the last scheduling
        # decision; refresh from the persisted dataset before emitting summary.
        self._evaluate(self._load_matching_records("sample"))
        payload = super().statistical_report_payload()
        if payload is not None:
            payload.update(input_distribution=self.input_distribution, seed=self.seed, stop_reason=self.stop_reason)
        return _json_safe(payload)


def normalize_input_distribution(raw: Mapping[str, Mapping[str, object]] | None) -> dict[str, dict[str, float | str]]:
    if not raw:
        raise ValueError("dynamics input distribution must not be empty")
    normalized: dict[str, dict[str, float | str]] = {}
    for name, value in raw.items():
        if not isinstance(value, Mapping) or str(value.get("distribution", "uniform")) != "uniform":
            raise ValueError(f"Distribution for {name!r} must be a uniform-distribution object")
        try:
            lower, upper = float(value["min"]), float(value["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Distribution for {name!r} requires numeric min/max") from exc
        if lower > upper:
            raise ValueError(f"Distribution for {name!r} must satisfy min <= max")
        normalized[str(name)] = {"distribution": "uniform", "min": lower, "max": upper}
    return normalized


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    item = getattr(value, "item", None)
    return item() if callable(item) else value


__all__ = ["RandomParameterSamplingConfig", "RandomParameterSamplingStrategy", "normalize_input_distribution"]
