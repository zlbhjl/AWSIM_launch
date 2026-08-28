from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence


def build_cutin_scenario(
    *,
    network: object,
    dynamic_params: Mapping[str, float],
    fixed_params: Mapping[str, object],
    scenario_profiles: Sequence[Mapping[str, object]] | None = None,
    lane_offset_factory: Callable[[str, float], object],
    scenario_builder: Callable[..., object],
) -> object:
    _validate_required_params(dynamic_params)

    resolved_params = _resolve_cutin_params(
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
        "npc_init_laneoffset": lane_offset_factory(
            str(resolved_params["npc_init_lane"]),
            float(resolved_params["npc_init_offset"]),
        ),
        "cutin_next_lane": str(resolved_params["cutin_next_lane"]),
        "_ego_speed": dynamic_params["ego_speed"] / 3.6,
        "_npc_speed": dynamic_params["npc_speed"] / 3.6,
        "_cutin_vy": float(dynamic_params.get("cutin_vy", resolved_params["cutin_vy"])),
        "dx0": float(dynamic_params["dx0"]),
        "acceleration": float(resolved_params.get("acceleration", 7.0)),
        "npc_start_speed_ratio": float(resolved_params.get("npc_start_speed_ratio", 1.0)),
    }

    if "body_style" in resolved_params:
        builder_kwargs["body_style"] = resolved_params["body_style"]

    return scenario_builder(**builder_kwargs)


def _validate_required_params(dynamic_params: Mapping[str, float]) -> None:
    missing = [
        key
        for key in ("dx0", "ego_speed", "npc_speed")
        if key not in dynamic_params
    ]
    if missing:
        raise KeyError(f"Missing dynamic params: {', '.join(missing)}")


def _resolve_cutin_params(
    *,
    dynamic_params: Mapping[str, float],
    fixed_params: Mapping[str, object],
    scenario_profiles: Sequence[Mapping[str, object]] | None,
) -> Mapping[str, object]:
    if not scenario_profiles:
        return fixed_params

    profile = _select_profile(
        npc_speed=dynamic_params["npc_speed"],
        scenario_profiles=scenario_profiles,
    )
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
            "npc_init_lane": profile["npc_init_lane"],
            "npc_init_offset": band["npc_init_offset"],
            "cutin_next_lane": profile["cutin_next_lane"],
            "acceleration": profile["acceleration"],
            "npc_start_speed_ratio": band.get("npc_start_speed_ratio", 1.0),
        }
    )
    return resolved


def _select_profile(
    *,
    npc_speed: float,
    scenario_profiles: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    return min(
        scenario_profiles,
        key=lambda profile: abs(float(profile["npc_speed"]) - npc_speed),
    )


def _select_ego_speed_band(
    *,
    ego_speed: float,
    ego_speed_bands: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    for band in ego_speed_bands:
        if ego_speed < float(band["max_ego_speed"]):
            return band
    raise KeyError("No ego speed band matched the provided ego_speed")
