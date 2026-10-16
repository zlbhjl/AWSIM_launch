from __future__ import annotations

import importlib
from types import ModuleType


DEFAULT_CASE_KIND_PACKAGE = "targets.awsim.case_kinds"
SUPPORTED_SCENARIO_PROFILES = ("legacy", "autoware171")


def build_default_case_kind_module_name(
    *,
    case_kind: str,
    scenario_profile: str | None = None,
) -> str:
    if scenario_profile is None:
        return f"{DEFAULT_CASE_KIND_PACKAGE}.{case_kind}"
    if scenario_profile not in SUPPORTED_SCENARIO_PROFILES:
        raise ValueError(f"Unsupported scenario profile: {scenario_profile}")
    return f"{DEFAULT_CASE_KIND_PACKAGE}.{scenario_profile}.{case_kind}"


def resolve_case_kind_module(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
) -> ModuleType:
    resolved_module_name = module_name or build_default_case_kind_module_name(
        case_kind=case_kind,
        scenario_profile=scenario_profile,
    )
    return importlib.import_module(resolved_module_name)


def load_timeout_sec(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
    default: float = 200.0,
) -> float:
    module = resolve_case_kind_module(
        case_kind=case_kind,
        module_name=module_name,
        scenario_profile=scenario_profile,
    )
    return float(getattr(module, "TIMEOUT_SEC", default))


def load_case_definition(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
) -> dict[str, object]:
    module = resolve_case_kind_module(
        case_kind=case_kind,
        module_name=module_name,
        scenario_profile=scenario_profile,
    )
    explicit_definition = getattr(module, "get_case_definition", None)
    if callable(explicit_definition):
        return dict(explicit_definition())

    return {
        "scenario_type": getattr(module, "SCENARIO_TYPE", case_kind),
        "repeat_count": int(getattr(module, "REPEAT_COUNT", 0)),
        "timeout_sec": float(getattr(module, "TIMEOUT_SEC", 200.0)),
        "target_npcs": list(getattr(module, "TARGET_NPCS", [])),
        "param_ranges": dict(getattr(module, "PARAM_RANGES", {})),
        "fixed_params": dict(getattr(module, "FIXED_PARAMS", {})),
    }


def load_rule_spec(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
) -> tuple[list[str], list[str], dict[str, object]]:
    module = resolve_case_kind_module(
        case_kind=case_kind,
        module_name=module_name,
        scenario_profile=scenario_profile,
    )
    explicit_rule_spec = getattr(module, "get_rule_spec", None)
    if callable(explicit_rule_spec):
        rule_spec = dict(explicit_rule_spec())
        return (
            list(rule_spec.get("result_labels", [])),
            list(rule_spec.get("formulas", [])),
            dict(rule_spec.get("invalid_conditions", {})),
        )

    result_labels = list(getattr(module, "RESULT_LABELS", []))
    formulas = list(getattr(module, "FORMULAS", []))
    invalid_conditions = dict(getattr(module, "INVALID_CONDITIONS", {}))
    return result_labels, formulas, invalid_conditions


def load_event_definitions(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
) -> dict[str, dict[str, object]]:
    module = resolve_case_kind_module(
        case_kind=case_kind,
        module_name=module_name,
        scenario_profile=scenario_profile,
    )
    explicit_rule_spec = getattr(module, "get_rule_spec", None)
    if callable(explicit_rule_spec):
        rule_spec = dict(explicit_rule_spec())
        event_definitions = rule_spec.get("event_definitions")
        if event_definitions is not None:
            return {
                str(event_id): dict(event_definition)
                for event_id, event_definition in dict(event_definitions).items()
            }

    explicit_definitions = getattr(module, "EVENT_DEFINITIONS", None)
    if explicit_definitions is not None:
        return {
            str(event_id): dict(event_definition)
            for event_id, event_definition in dict(explicit_definitions).items()
        }

    result_labels = list(getattr(module, "RESULT_LABELS", []))
    return {
        label: {
            "dataset_filter": None,
            "error_filter": f"output.{label}",
            "target_column": label,
        }
        for label in result_labels
        if label.startswith("c_")
    }


def load_focus_points(
    *,
    case_kind: str,
    module_name: str | None = None,
    scenario_profile: str | None = None,
) -> list[dict[str, object]]:
    module = resolve_case_kind_module(
        case_kind=case_kind,
        module_name=module_name,
        scenario_profile=scenario_profile,
    )
    explicit_points = getattr(module, "FOCUS_POINTS", None)
    if explicit_points is None:
        return []
    return [dict(point) for point in explicit_points]
