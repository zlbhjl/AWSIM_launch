"""Euler--Maruyama U-turn model with explicit, reproducible process noise."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..runners.ode import ODESolverSettings
from ..runners.sde import SDESolverSettings, solve_hybrid_sde
from .uturn import UTurnODEConfig, UTurnSimulationResult, UTurnSystem


@dataclass(frozen=True)
class UTurnSDENoise:
    """Diffusion strengths; acceleration terms use m/s/sqrt(s)."""

    npc_acceleration_std: float = 0.0
    ego_brake_acceleration_std: float = 0.0
    npc_heading_std_rad_per_sqrt_sec: float = 0.0

    def validate(self) -> None:
        for name, value in vars(self).items():
            if not np.isfinite(value) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class UTurnSDEConfig:
    ode_config: UTurnODEConfig
    seed: int
    dt_sec: float
    noise: UTurnSDENoise

    def validate(self) -> None:
        self.ode_config.validate()
        if not np.isfinite(self.dt_sec) or self.dt_sec <= 0.0:
            raise ValueError("dt_sec must be finite and positive")
        if self.dt_sec > self.ode_config.max_step_sec:
            raise ValueError("dt_sec must be less than or equal to max_step_sec")
        self.noise.validate()


def simulate_uturn_sde(config: UTurnSDEConfig) -> UTurnSimulationResult:
    """Simulate the U-turn ``UTurnSystem`` with Euler--Maruyama noise.

    The only random quantities are explicitly named diffusion terms.  A zero
    diffusion vector is the deterministic ODE by definition, so the generic
    SDE runner evaluates that case with the ODE runner.  This makes the
    zero-noise limit exact instead of introducing an artificial
    Euler/event-discretisation shift.
    """
    config.validate()
    ode = config.ode_config
    system = UTurnSystem(ode, noise=config.noise)
    trajectory = solve_hybrid_sde(
        system,
        SDESolverSettings(
            horizon_sec=ode.horizon_sec,
            dt_sec=config.dt_sec,
            seed=config.seed,
            ode_fallback=ODESolverSettings(
                horizon_sec=ode.horizon_sec, max_step_sec=ode.max_step_sec
            ),
        ),
    )
    return system.to_result(trajectory)


__all__ = ["UTurnSDEConfig", "UTurnSDENoise", "simulate_uturn_sde"]
