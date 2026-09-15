from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_MODULE_CACHE: dict[tuple[int, Path], object] = {}


@dataclass(frozen=True)
class PrismMaudeCheckerConfig:
    spec_path: Path = Path(__file__).resolve().parent / "specs" / "prism_trace.maude"
    early_failure_limit: int = 5
    degradation_limit: int = 3


class PrismMaudeChecker:
    """Run the PRISM trace rules with the standard Maude Python binding."""

    def __init__(self, config: PrismMaudeCheckerConfig | None = None):
        self.config = config or PrismMaudeCheckerConfig()
        self._module = None

    def check(self, trace_summary: Mapping[str, object]) -> dict[str, object]:
        try:
            import maude
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Maude Python binding is unavailable; use prism-maude image"
            ) from exc

        module, spec_path = self._load_module(maude)
        first_failure = trace_summary.get("first_failure_step")
        encoded_failure = (
            int(trace_summary["steps_to_failure_capped"])
            if first_failure is None
            else int(first_failure)
        )
        degraded_count = int(trace_summary["degraded_visit_count"])
        absorbing_valid = bool(trace_summary["absorbing_state_valid"])
        verdicts = {
            "early_failure": self._reduce(
                module,
                f"early-failure({encoded_failure}, "
                f"{self.config.early_failure_limit})",
            ),
            "repeated_degradation": self._reduce(
                module,
                f"repeated-degradation({degraded_count}, "
                f"{self.config.degradation_limit})",
            ),
            "absorbing_failure": self._reduce(
                module,
                f"absorbing-valid({'true' if absorbing_valid else 'false'})",
            ),
        }
        return {
            "schema_version": 1,
            "checker": "maude_prism_trace",
            "verdicts": verdicts,
            "rule_violations": [
                rule_id
                for rule_id, verdict in verdicts.items()
                if verdict == "violation"
            ],
            "invalid_rules": [
                rule_id
                for rule_id, verdict in verdicts.items()
                if verdict == "invalid"
            ],
            "spec_path": str(spec_path),
        }

    def evaluate(self, trace_summary: Mapping[str, object]) -> dict[str, object]:
        """Compatibility API returning the former flattened metric payload."""
        from .prism_trace_evaluator import PrismTraceEvaluator

        return PrismTraceEvaluator().evaluate(self.check(trace_summary))

    def _load_module(self, maude):
        spec_path = self.config.spec_path.expanduser().resolve()
        if self._module is not None:
            return self._module, spec_path
        if not spec_path.exists():
            raise FileNotFoundError(
                f"PRISM Maude specification not found: {spec_path}"
            )
        cache_key = (id(maude), spec_path)
        cached_module = _MODULE_CACHE.get(cache_key)
        if cached_module is not None:
            self._module = cached_module
            return cached_module, spec_path
        maude.init()
        if not maude.load(str(spec_path)):
            raise RuntimeError(f"Could not load Maude specification: {spec_path}")
        module = maude.getModule("PRISM-TRACE-CHECKER")
        if module is None:
            raise RuntimeError("Maude module PRISM-TRACE-CHECKER was not loaded")
        _MODULE_CACHE[cache_key] = module
        self._module = module
        return module, spec_path

    @staticmethod
    def _reduce(module, expression: str) -> str:
        term = module.parseTerm(expression)
        if term is None:
            raise RuntimeError(f"Could not parse Maude term: {expression}")
        term.reduce()
        return str(term)


__all__ = ["PrismMaudeChecker", "PrismMaudeCheckerConfig"]
