from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from targets.awsim.scenario_builders._uturn_common import build_uturn_scenario_common


def build_uturn_scenario(
    *,
    network: object,
    dynamic_params: Mapping[str, float],
    fixed_params: Mapping[str, object],
    scenario_profiles: Sequence[Mapping[str, object]] | None = None,
    lane_offset_factory: Callable[[str, float], object],
    scenario_builder: Callable[..., object],
) -> object:
    return build_uturn_scenario_common(
        network=network,
        dynamic_params=dynamic_params,
        fixed_params=fixed_params,
        scenario_profiles=scenario_profiles,
        lane_offset_factory=lane_offset_factory,
        scenario_builder=scenario_builder,
        include_npc_start_speed_ratio=True,
    )
