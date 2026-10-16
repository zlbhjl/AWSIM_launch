from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from functools import lru_cache

from theoretical_calculator import TheoreticalSafetyCalculator


THEORY_SPEC_PACKAGE = "targets.awsim.theory_specs"


def default_theory_columns() -> dict[str, object]:
    return {
        "theory_d_total_human": float("nan"),
        "theory_d_total_ai": float("nan"),
        "theory_margin_a_human": float("nan"),
        "theory_margin_a_ai": float("nan"),
        "theory_zone_a": "",
        "theory_margin_b_human": float("nan"),
        "theory_margin_b_ai": float("nan"),
        "theory_zone_b": "",
    }


def build_theory_metrics(
    *,
    case_kind: str,
    values: Mapping[str, object],
    config_module_name: str | None = None,
) -> dict[str, object]:
    scenario_builder = _resolve_scenario_theory_builder(
        case_kind=case_kind,
        config_module_name=config_module_name,
    )
    if scenario_builder is not None:
        try:
            return dict(scenario_builder(values))
        except Exception:
            return {}

    calculator = _resolve_legacy_theoretical_calculator(
        case_kind=case_kind,
        config_module_name=config_module_name,
    )
    if calculator is None:
        return {}

    try:
        dx0 = float(values["dx0"])
        ego_speed = float(values["ego_speed"])
        npc_speed = float(values["npc_speed"])
    except (KeyError, TypeError, ValueError):
        return {}

    try:
        return dict(calculator.evaluate(dx0, ego_speed, npc_speed))
    except Exception:
        return {}


def _resolve_scenario_theory_builder(
    *,
    case_kind: str,
    config_module_name: str | None,
) -> Callable[[Mapping[str, object]], Mapping[str, object]] | None:
    candidate_modules: list[str] = []
    if isinstance(config_module_name, str) and config_module_name:
        candidate_modules.append(config_module_name)
    candidate_modules.append(f"{THEORY_SPEC_PACKAGE}.{case_kind}")

    for module_name in candidate_modules:
        builder = _load_theory_builder(module_name)
        if builder is not None:
            return builder
    return None


@lru_cache(maxsize=None)
def _load_theory_builder(
    module_name: str,
) -> Callable[[Mapping[str, object]], Mapping[str, object]] | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None

    builder = getattr(module, "build_theory_metrics", None)
    if callable(builder):
        return builder
    return None


def _resolve_legacy_theoretical_calculator(
    *,
    case_kind: str,
    config_module_name: str | None,
) -> TheoreticalSafetyCalculator | None:
    candidate_modules: list[str] = []
    if isinstance(config_module_name, str) and config_module_name:
        candidate_modules.append(config_module_name)
    candidate_modules.append(f"configs.{case_kind}")
    candidate_modules.append(f"targets.awsim.case_kinds.{case_kind}")

    for module_name in candidate_modules:
        calculator = _load_theoretical_calculator(module_name)
        if calculator is not None:
            return calculator
    return TheoreticalSafetyCalculator(None)


@lru_cache(maxsize=None)
def _load_theoretical_calculator(
    module_name: str,
) -> TheoreticalSafetyCalculator | None:
    try:
        config_module = importlib.import_module(module_name)
    except ImportError:
        return None
    return TheoreticalSafetyCalculator(config_module)
