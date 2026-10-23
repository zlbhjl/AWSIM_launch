"""Experiment manifest for fixed-N importance-sampling runs.

The manifest records what was evaluated (P, the numerical map and the event),
with which proposal Q, under which random-number policy and planned N. It is
written before the first sample. Resuming checks the identity fields against
the stored manifest and refuses a run whose settings differ, so results from
different P, Q or random roots are never mixed in one evaluation.

See docs/sde_importance_sampling_plan.md §7.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1
# Fields that must match when a run is resumed. Environment and session
# history may change between sessions and are recorded, not compared.
IDENTITY_FIELDS = (
    "manifest_version",
    "experiment_id",
    "target_fingerprint",
    "proposal_fingerprint",
    "numerics",
    "rng",
    "planned_samples",
    "metric",
)


class ManifestMismatchError(ValueError):
    """Raised when a resumed run does not match the stored manifest."""


def fingerprint(value: object) -> str:
    """sha256 of canonical JSON; equal definitions give equal fingerprints."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ExperimentManifest:
    experiment_id: str
    target_definition: dict[str, object]
    proposal_definition: dict[str, object]
    numerics: dict[str, object]
    rng: dict[str, object]
    planned_samples: int
    metric: str
    manifest_version: int = MANIFEST_VERSION
    target_fingerprint: str = ""
    proposal_fingerprint: str = ""
    environment: dict[str, object] = field(default_factory=dict)
    sessions: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.planned_samples < 1:
            raise ValueError("planned_samples must be positive")
        if not self.target_fingerprint:
            self.target_fingerprint = fingerprint(
                {"target": self.target_definition, "numerics": self.numerics}
            )
        if not self.proposal_fingerprint:
            self.proposal_fingerprint = fingerprint(self.proposal_definition)

    def identity(self) -> dict[str, object]:
        data = asdict(self)
        return {key: data[key] for key in IDENTITY_FIELDS}

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExperimentManifest":
        return cls(**data)


def save_or_verify_manifest(root: str | Path, manifest: ExperimentManifest) -> ExperimentManifest:
    """Write the manifest for a new run, or check it against a resumed one.

    Returns the stored manifest with this session appended to ``sessions``.
    """
    path = Path(root) / MANIFEST_FILENAME
    session = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **environment_info(),
    }
    if path.exists():
        stored = ExperimentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
        differences = [
            key for key in IDENTITY_FIELDS if stored.identity()[key] != manifest.identity()[key]
        ]
        if differences:
            raise ManifestMismatchError(
                f"{path} was written for different settings ({', '.join(differences)}); "
                "use a new output root or the original settings"
            )
        stored.sessions.append(session)
        _write(path, stored)
        return stored
    manifest.environment = environment_info()
    manifest.sessions = [session]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, manifest)
    return manifest


def load_manifest(root: str | Path) -> ExperimentManifest | None:
    path = Path(root) / MANIFEST_FILENAME
    if not path.exists():
        return None
    return ExperimentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def environment_info() -> dict[str, object]:
    import numpy
    import scipy

    return {
        "git_revision": _git_revision(),
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
    }


def _git_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "unknown"


def _write(path: Path, manifest: ExperimentManifest) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


__all__ = [
    "ExperimentManifest",
    "IDENTITY_FIELDS",
    "MANIFEST_FILENAME",
    "ManifestMismatchError",
    "environment_info",
    "fingerprint",
    "load_manifest",
    "save_or_verify_manifest",
]
