from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class GPUHealthResult:
    healthy: bool
    detail: str = ""


def probe_nvidia_smi(
    *,
    runner: Callable[..., object] | None = None,
    timeout_sec: float = 5.0,
) -> GPUHealthResult:
    run = runner or subprocess.run
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version",
        "--format=csv,noheader",
    ]
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GPUHealthResult(False, str(exc))

    if int(getattr(result, "returncode", 1)) == 0:
        output = str(getattr(result, "stdout", "") or "").strip()
        return GPUHealthResult(True, output)
    stderr = str(getattr(result, "stderr", "") or "").strip()
    stdout = str(getattr(result, "stdout", "") or "").strip()
    return GPUHealthResult(False, stderr or stdout or "nvidia-smi failed")


__all__ = ["GPUHealthResult", "probe_nvidia_smi"]
