from __future__ import annotations

import csv
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .model_catalog import PrismModelDefinition


PROPERTY_IDS = ("eventual_failure", "bounded_failure")


@dataclass(frozen=True)
class PrismExecution:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float


@dataclass(frozen=True)
class PrismRunArtifacts:
    property_results: dict[str, float]
    property_execution: PrismExecution
    trace_execution: PrismExecution
    trace_csv: Path


@dataclass(frozen=True)
class PrismModelCheckArtifacts:
    property_results: dict[str, float]
    execution: PrismExecution
    result_csv: Path


@dataclass(frozen=True)
class PrismSamplePathArtifacts:
    execution: PrismExecution
    trace_csv: Path


def run_prism(
    definition: PrismModelDefinition,
    *,
    constants: Mapping[str, float],
    horizon: int,
    output_dir: Path,
    prism_executable: str = "prism",
    timeout_sec: float = 30.0,
) -> PrismRunArtifacts:
    model_check = run_prism_model_check(
        definition,
        constants=constants,
        horizon=horizon,
        output_dir=output_dir,
        prism_executable=prism_executable,
        timeout_sec=timeout_sec,
    )
    sample = run_prism_sample_path(
        definition,
        constants=constants,
        horizon=horizon,
        output_dir=output_dir,
        prism_executable=prism_executable,
        timeout_sec=timeout_sec,
    )
    return PrismRunArtifacts(
        property_results=model_check.property_results,
        property_execution=model_check.execution,
        trace_execution=sample.execution,
        trace_csv=sample.trace_csv,
    )


def run_prism_model_check(
    definition: PrismModelDefinition,
    *,
    constants: Mapping[str, float],
    horizon: int,
    output_dir: Path,
    prism_executable: str = "prism",
    timeout_sec: float = 30.0,
) -> PrismModelCheckArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    const_argument = _format_constants(constants, horizon)
    result_csv = output_dir / "property_results.csv"
    property_command = (
        prism_executable,
        str(definition.model_path),
        str(definition.properties_path),
        "-const",
        const_argument,
        "-exportresults",
        f"{result_csv}:csv",
    )
    property_execution = _run(property_command, timeout_sec=timeout_sec)
    _write_log(output_dir / "prism_properties.log", property_execution)
    if property_execution.returncode != 0:
        raise PrismExecutionError("PRISM property evaluation failed", property_execution)
    property_results = parse_property_results(result_csv, property_execution.stdout)
    return PrismModelCheckArtifacts(
        property_results=property_results,
        execution=property_execution,
        result_csv=result_csv,
    )


def run_prism_sample_path(
    definition: PrismModelDefinition,
    *,
    constants: Mapping[str, float],
    horizon: int,
    output_dir: Path,
    prism_executable: str = "prism",
    timeout_sec: float = 30.0,
) -> PrismSamplePathArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    const_argument = _format_constants(constants, horizon)
    trace_csv = output_dir / "trace.csv"
    trace_command = (
        prism_executable,
        str(definition.model_path),
        "-const",
        const_argument,
        "-simpath",
        f"{horizon},vars=(state),sep=comma,loopcheck=false",
        str(trace_csv),
    )
    trace_execution = _run(trace_command, timeout_sec=timeout_sec)
    _write_log(output_dir / "prism_trace.log", trace_execution)
    if trace_execution.returncode != 0:
        raise PrismExecutionError("PRISM path simulation failed", trace_execution)
    if not trace_csv.exists():
        raise FileNotFoundError(f"PRISM did not produce trace CSV: {trace_csv}")
    return PrismSamplePathArtifacts(
        execution=trace_execution,
        trace_csv=trace_csv,
    )


class PrismExecutionError(RuntimeError):
    def __init__(self, message: str, execution: PrismExecution):
        super().__init__(message)
        self.execution = execution


def parse_property_results(path: Path, stdout: str = "") -> dict[str, float]:
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        values = _numeric_cells(rows)
        if len(values) >= len(PROPERTY_IDS):
            return dict(zip(PROPERTY_IDS, values[: len(PROPERTY_IDS)]))

    values = [float(match) for match in re.findall(r"Result:\s*([0-9.eE+-]+)", stdout)]
    if len(values) < len(PROPERTY_IDS):
        raise ValueError("Could not parse both PRISM property results")
    return dict(zip(PROPERTY_IDS, values[: len(PROPERTY_IDS)]))


def _numeric_cells(rows: list[list[str]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for cell in reversed(row):
            try:
                value = float(cell.strip())
            except ValueError:
                continue
            values.append(value)
            break
    return values


def _format_constants(constants: Mapping[str, float], horizon: int) -> str:
    values = {name: float(value) for name, value in constants.items()}
    values["STEPS"] = int(horizon)
    return ",".join(f"{name}={value}" for name, value in sorted(values.items()))


def _run(command: tuple[str, ...], *, timeout_sec: float) -> PrismExecution:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        return PrismExecution(
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"timeout after {timeout_sec} seconds",
            elapsed_sec=time.monotonic() - started,
        )
    return PrismExecution(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        elapsed_sec=time.monotonic() - started,
    )


def _write_log(path: Path, execution: PrismExecution) -> None:
    path.write_text(
        "command: " + " ".join(execution.command) + "\n\n"
        + "stdout:\n" + execution.stdout + "\n\n"
        + "stderr:\n" + execution.stderr,
        encoding="utf-8",
    )
