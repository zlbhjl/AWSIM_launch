"""U-turn scenario as an instance of the generic hybrid dynamics model.

``UTurnSystem`` supplies the modes, drift, diffusion, guards, and resets of
``models.base.HybridSystem``; the generic runners integrate it.  The module
has no AWSIM, CLI, persistence, or target-registry dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from scenario_specs.uturn import JAMA_PROFILES, should_start_npc

from ..runners.ode import ODESolverSettings, solve_hybrid_ode
from .base import HybridTrajectory, Transition


_EGO_X = 0
_EGO_Y = 1
_EGO_HEADING = 2
_EGO_SPEED = 3
_NPC_X = 4
_NPC_Y = 5
_NPC_HEADING = 6
_NPC_SPEED = 7
_TURN_ANGLE = 8
_STATE_SIZE = 9


@dataclass(frozen=True)
class VehicleState:
    x_m: float
    y_m: float
    heading_rad: float
    speed_mps: float


@dataclass(frozen=True)
class UTurnODEConfig:
    """All inputs required to simulate one deterministic U-turn trajectory.

    The initial coordinate system is intentionally generic: the shared U-turn
    trigger is evaluated as `npc_x - ego_x <= dx0_m`.  The default setup puts
    ego on the positive x direction and the oncoming NPC on heading pi.
    """

    ego_initial: VehicleState
    npc_initial: VehicleState
    dx0_m: float
    ego_target_speed_mps: float
    npc_target_speed_mps: float
    longitudinal_clearance_offset_m: float = 0.0
    ego_cruise_acceleration_mps2: float = 0.0
    npc_acceleration_mps2: float = 7.0
    npc_start_speed_ratio: float = 1.0
    turn_radius_m: float = 10.0
    turn_angle_rad: float = np.pi
    # Forward distance from the NPC's turning reference point (AWSIM pose
    # origin, where the path curvature applies) to its simulated geometric
    # center.  A non-zero offset makes the center sweep outward while turning.
    npc_turn_reference_offset_m: float = 0.0
    turn_direction: float = -1.0
    horizon_sec: float = 10.0
    max_step_sec: float = 0.02
    jama_profile: Mapping[str, float] = field(
        default_factory=lambda: dict(JAMA_PROFILES["ai_aeb"])
    )

    def validate(self) -> None:
        positive = {
            "dx0_m": self.dx0_m,
            "ego_target_speed_mps": self.ego_target_speed_mps,
            "npc_target_speed_mps": self.npc_target_speed_mps,
            "turn_radius_m": self.turn_radius_m,
            "horizon_sec": self.horizon_sec,
            "max_step_sec": self.max_step_sec,
        }
        for name, value in positive.items():
            if not float(value) > 0.0:
                raise ValueError(f"{name} must be positive")
        if float(self.ego_cruise_acceleration_mps2) < 0.0:
            raise ValueError("ego_cruise_acceleration_mps2 must be non-negative")
        if float(self.longitudinal_clearance_offset_m) < 0.0:
            raise ValueError("longitudinal_clearance_offset_m must be non-negative")
        if float(self.npc_turn_reference_offset_m) < 0.0:
            raise ValueError("npc_turn_reference_offset_m must be non-negative")
        if float(self.npc_acceleration_mps2) < 0.0:
            raise ValueError("npc_acceleration_mps2 must be non-negative")
        if self.ego_initial.speed_mps < 0.0 or self.npc_initial.speed_mps < 0.0:
            raise ValueError("initial vehicle speeds must be non-negative")
        if not 0.0 < float(self.turn_angle_rad) <= 2.0 * np.pi:
            raise ValueError("turn_angle_rad must be in (0, 2*pi]")
        if float(self.turn_direction) not in {-1.0, 1.0}:
            raise ValueError("turn_direction must be -1.0 or 1.0")
        required_jama = ("t_delay", "t_jerk", "a_max")
        for key in required_jama:
            try:
                value = float(self.jama_profile[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"jama_profile requires a numeric {key}") from exc
            if value < 0.0:
                raise ValueError(f"jama_profile.{key} must be non-negative")
        if float(self.jama_profile["a_max"]) <= 0.0:
            raise ValueError("jama_profile.a_max must be positive")


@dataclass(frozen=True)
class UTurnSimulationResult:
    times_sec: np.ndarray
    states: np.ndarray
    npc_start_time_sec: float | None
    uturn_start_time_sec: float | None
    uturn_end_time_sec: float | None

    @property
    def ego_positions_m(self) -> np.ndarray:
        return self.states[:, [_EGO_X, _EGO_Y]]

    @property
    def npc_positions_m(self) -> np.ndarray:
        return self.states[:, [_NPC_X, _NPC_Y]]

    @property
    def ego_speeds_mps(self) -> np.ndarray:
        return self.states[:, _EGO_SPEED]

    @property
    def npc_speeds_mps(self) -> np.ndarray:
        return self.states[:, _NPC_SPEED]


class UTurnSystem:
    """U-turn scenario as a ``HybridSystem`` (see ``models.base``).

    State (n=9): ego center x, y, heading, speed; NPC turning reference point
    (AWSIM pose origin) x, y, heading, speed; accumulated turn angle.

    Modes and drift f_q:

    * ``lane``: both vehicles drive straight; ego cruises, NPC starts once ego
      reaches its start-speed ratio.
    * ``turn``: NPC heading rate is ``turn_direction * v / R`` (constant
      curvature at the reference point); ego follows the braking profile.
    * ``exit``: NPC drives straight again; ego keeps braking.

    Guards: ``lane -> turn`` when the NPC-center gap reaches ``dx0``;
    ``turn -> exit`` when the turn angle reaches ``turn_angle_rad``.

    Diffusion G_q (only with ``noise``): ego speed while braking, NPC speed
    after it starts, and NPC heading while turning.

    The NPC geometric center is an output, ``reference + d (cos psi, sin psi)``
    with ``d = npc_turn_reference_offset_m``; see ``npc_centers``.
    """

    state_names = (
        "ego_x_m",
        "ego_y_m",
        "ego_heading_rad",
        "ego_speed_mps",
        "npc_reference_x_m",
        "npc_reference_y_m",
        "npc_heading_rad",
        "npc_speed_mps",
        "turn_angle_rad",
    )

    def __init__(self, config: UTurnODEConfig, noise: object | None = None) -> None:
        config.validate()
        self.config = config
        self.noise = noise
        self._transitions = {
            "lane": (
                Transition("turn", self._uturn_trigger_guard, -1.0, self._reset_turn_angle(0.0)),
            ),
            "turn": (
                Transition(
                    "exit",
                    self._turn_completion_guard,
                    1.0,
                    self._reset_turn_angle(config.turn_angle_rad),
                ),
            ),
            "exit": (),
        }

    # -- HybridSystem -----------------------------------------------------
    def initial_mode(self) -> str:
        return "lane"

    def initial_state(self) -> np.ndarray:
        config = self.config
        offset = config.npc_turn_reference_offset_m
        heading = config.npc_initial.heading_rad
        return np.array(
            [
                config.ego_initial.x_m,
                config.ego_initial.y_m,
                config.ego_initial.heading_rad,
                config.ego_initial.speed_mps,
                config.npc_initial.x_m - offset * np.cos(heading),
                config.npc_initial.y_m - offset * np.sin(heading),
                heading,
                config.npc_initial.speed_mps,
                0.0,
            ],
            dtype=float,
        )

    def drift(self, time_sec, state, mode, mode_entry_times) -> np.ndarray:
        config = self.config
        derivatives = np.zeros(_STATE_SIZE, dtype=float)
        ego_speed = max(float(state[_EGO_SPEED]), 0.0)
        npc_speed = max(float(state[_NPC_SPEED]), 0.0)
        derivatives[_EGO_X] = ego_speed * np.cos(state[_EGO_HEADING])
        derivatives[_EGO_Y] = ego_speed * np.sin(state[_EGO_HEADING])
        derivatives[_NPC_X] = npc_speed * np.cos(state[_NPC_HEADING])
        derivatives[_NPC_Y] = npc_speed * np.sin(state[_NPC_HEADING])

        braking_start_sec = mode_entry_times.get("turn")
        if braking_start_sec is None:
            derivatives[_EGO_SPEED] = _accelerate_to_target(
                ego_speed,
                config.ego_target_speed_mps,
                config.ego_cruise_acceleration_mps2,
            )
        else:
            derivatives[_EGO_SPEED] = _jama_braking_acceleration(
                speed_mps=ego_speed,
                elapsed_sec=time_sec - braking_start_sec,
                profile=config.jama_profile,
            )
        if self._npc_has_started(ego_speed):
            derivatives[_NPC_SPEED] = _accelerate_to_target(
                npc_speed,
                config.npc_target_speed_mps,
                config.npc_acceleration_mps2,
            )
        if mode == "turn":
            derivatives[_NPC_HEADING] = config.turn_direction * npc_speed / config.turn_radius_m
            derivatives[_TURN_ANGLE] = npc_speed / config.turn_radius_m
        return derivatives

    def noise_channels(self, mode: str) -> tuple[str, ...]:
        if not self.has_noise:
            return ()
        channels = ("ego_brake_acceleration", "npc_acceleration")
        return channels + ("npc_heading",) if mode == "turn" else channels

    def diffusion(self, time_sec, state, mode, mode_entry_times) -> np.ndarray:
        channels = self.noise_channels(mode)
        matrix = np.zeros((_STATE_SIZE, len(channels)), dtype=float)
        if not channels:
            return matrix
        noise = self.noise
        if "turn" in mode_entry_times:
            matrix[_EGO_SPEED, 0] = noise.ego_brake_acceleration_std
        if self._npc_has_started(max(float(state[_EGO_SPEED]), 0.0)):
            matrix[_NPC_SPEED, 1] = noise.npc_acceleration_std
        if mode == "turn":
            matrix[_NPC_HEADING, 2] = noise.npc_heading_std_rad_per_sqrt_sec
        return matrix

    def transitions(self, mode: str) -> tuple[Transition, ...]:
        return self._transitions[mode]

    def project(self, state: np.ndarray) -> np.ndarray:
        projected = np.array(state, dtype=float, copy=True)
        projected[[_EGO_SPEED, _NPC_SPEED]] = np.maximum(projected[[_EGO_SPEED, _NPC_SPEED]], 0.0)
        return projected

    @property
    def has_noise(self) -> bool:
        return self.noise is not None and any(
            float(value) != 0.0 for value in vars(self.noise).values()
        )

    # -- outputs ----------------------------------------------------------
    def npc_centers(self, states: np.ndarray) -> np.ndarray:
        offset = self.config.npc_turn_reference_offset_m
        heading = states[:, _NPC_HEADING]
        return states[:, [_NPC_X, _NPC_Y]] + offset * np.column_stack(
            (np.cos(heading), np.sin(heading))
        )

    def to_result(self, trajectory: HybridTrajectory) -> UTurnSimulationResult:
        """Convert to the public result whose NPC columns are geometric centers."""
        states = np.array(trajectory.states, dtype=float, copy=True)
        states[:, [_NPC_X, _NPC_Y]] = self.npc_centers(trajectory.states)
        # solve_ivp can overshoot a zero-speed discontinuity by machine epsilon.
        # A vehicle cannot reverse merely because the brake profile reached zero.
        states[:, [_EGO_SPEED, _NPC_SPEED]] = np.maximum(states[:, [_EGO_SPEED, _NPC_SPEED]], 0.0)
        return UTurnSimulationResult(
            times_sec=np.asarray(trajectory.times_sec, dtype=float),
            states=states,
            npc_start_time_sec=_resolve_npc_start_time(self.config),
            uturn_start_time_sec=trajectory.mode_entry_times.get("turn"),
            uturn_end_time_sec=trajectory.mode_entry_times.get("exit"),
        )

    # -- guards -----------------------------------------------------------
    def _uturn_trigger_guard(self, _time_sec: float, state: np.ndarray) -> float:
        offset = self.config.npc_turn_reference_offset_m
        npc_center_x = state[_NPC_X] + offset * np.cos(state[_NPC_HEADING])
        center_gap = npc_center_x - state[_EGO_X]
        return center_gap - self.config.longitudinal_clearance_offset_m - self.config.dx0_m

    def _turn_completion_guard(self, _time_sec: float, state: np.ndarray) -> float:
        return state[_TURN_ANGLE] - self.config.turn_angle_rad

    @staticmethod
    def _reset_turn_angle(value: float):
        def reset(state: np.ndarray) -> np.ndarray:
            updated = np.array(state, dtype=float, copy=True)
            updated[_TURN_ANGLE] = value
            return updated

        return reset

    def _npc_has_started(self, ego_speed_mps: float) -> bool:
        return should_start_npc(
            ego_speed_mps=ego_speed_mps,
            ego_target_speed_mps=self.config.ego_target_speed_mps,
            npc_start_speed_ratio=self.config.npc_start_speed_ratio,
        )


def simulate_uturn(config: UTurnODEConfig) -> UTurnSimulationResult:
    """Deterministic U-turn trajectory (``UTurnSystem`` with G_q = 0)."""
    system = UTurnSystem(config)
    trajectory = solve_hybrid_ode(
        system,
        ODESolverSettings(horizon_sec=config.horizon_sec, max_step_sec=config.max_step_sec),
    )
    return system.to_result(trajectory)


def _resolve_npc_start_time(config: UTurnODEConfig) -> float | None:
    threshold = min(max(config.npc_start_speed_ratio, 0.0), 1.0) * config.ego_target_speed_mps
    initial_speed = config.ego_initial.speed_mps
    if initial_speed >= threshold:
        return 0.0
    acceleration = config.ego_cruise_acceleration_mps2
    if acceleration <= 0.0:
        return None
    time_sec = (threshold - initial_speed) / acceleration
    return time_sec if time_sec <= config.horizon_sec else None


def _accelerate_to_target(speed_mps: float, target_speed_mps: float, acceleration_mps2: float) -> float:
    if speed_mps >= target_speed_mps or acceleration_mps2 <= 0.0:
        return 0.0
    return acceleration_mps2


def _jama_braking_acceleration(
    *,
    speed_mps: float,
    elapsed_sec: float,
    profile: Mapping[str, float],
) -> float:
    if speed_mps <= 0.0:
        return 0.0
    delay_sec = float(profile["t_delay"])
    jerk_sec = float(profile["t_jerk"])
    max_deceleration = float(profile["a_max"])
    if elapsed_sec <= delay_sec:
        return 0.0
    if jerk_sec <= 0.0:
        return -max_deceleration
    ramp_fraction = min((elapsed_sec - delay_sec) / jerk_sec, 1.0)
    return -max_deceleration * ramp_fraction


__all__ = [
    "UTurnODEConfig",
    "UTurnSystem",
    "UTurnSimulationResult",
    "VehicleState",
    "simulate_uturn",
]
