from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence


def build_deceleration_scenario(
    *,
    network: object,
    dynamic_params: Mapping[str, float],
    fixed_params: Mapping[str, object],
    scenario_profiles: Sequence[Mapping[str, object]] | None = None,
    lane_offset_factory: Callable[[str, float], object],
    scenario_builder: Callable[..., object],
) -> object:
    _validate_required_params(dynamic_params)

    resolved_params = _resolve_deceleration_params(
        dynamic_params=dynamic_params,
        fixed_params=fixed_params,
        scenario_profiles=scenario_profiles,
    )

    builder_kwargs = {
        "network": network,
        "ego_init_laneoffset": lane_offset_factory(
            str(resolved_params["ego_init_lane"]),
            float(resolved_params["ego_init_offset"]),
        ),
        "ego_goal_laneoffset": lane_offset_factory(
            str(resolved_params["ego_goal_lane"]),
            float(resolved_params["ego_goal_offset"]),
        ),
        "_speed": float(dynamic_params["ego_speed"]) / 3.6,
        "spawn_headway_sec": float(resolved_params["spawn_headway_sec"]),
        "spawn_trigger_speed_ratio": float(resolved_params["spawn_trigger_speed_ratio"]),
        "npc_cruise_acceleration": float(resolved_params["npc_cruise_acceleration"]),
        "deceleration": float(resolved_params["npc_deceleration"]),
        "decel_trigger_speed_ratio": float(resolved_params["decel_trigger_speed_ratio"]),
    }

    if "body_style" in resolved_params:
        builder_kwargs["body_style"] = resolved_params["body_style"]

    return scenario_builder(**builder_kwargs)


def _validate_required_params(dynamic_params: Mapping[str, float]) -> None:
    if "ego_speed" not in dynamic_params:
        raise KeyError("Missing dynamic params: ego_speed")


def _resolve_deceleration_params(
    *,
    dynamic_params: Mapping[str, float],
    fixed_params: Mapping[str, object],
    scenario_profiles: Sequence[Mapping[str, object]] | None,
) -> Mapping[str, object]:
    if not scenario_profiles:
        return fixed_params

    profile = scenario_profiles[0]
    band = _select_ego_speed_band(
        ego_speed=dynamic_params["ego_speed"],
        ego_speed_bands=profile["ego_speed_bands"],
    )

    resolved = dict(fixed_params)
    resolved.update(
        {
            "ego_init_lane": band["ego_init_lane"],
            "ego_init_offset": band["ego_init_offset"],
            "ego_goal_lane": band["ego_goal_lane"],
            "ego_goal_offset": band["ego_goal_offset"],
            "spawn_headway_sec": band.get("spawn_headway_sec", fixed_params["spawn_headway_sec"]),
            "spawn_trigger_speed_ratio": band.get(
                "spawn_trigger_speed_ratio",
                fixed_params["spawn_trigger_speed_ratio"],
            ),
            "npc_cruise_acceleration": band.get(
                "npc_cruise_acceleration",
                fixed_params["npc_cruise_acceleration"],
            ),
            "npc_deceleration": band.get("npc_deceleration", fixed_params["npc_deceleration"]),
            "decel_trigger_speed_ratio": band.get(
                "decel_trigger_speed_ratio",
                fixed_params["decel_trigger_speed_ratio"],
            ),
        }
    )
    return resolved


def _select_ego_speed_band(
    *,
    ego_speed: float,
    ego_speed_bands: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    for band in ego_speed_bands:
        if ego_speed < float(band["max_ego_speed"]):
            return band
    raise KeyError("No ego speed band matched the provided ego_speed")
