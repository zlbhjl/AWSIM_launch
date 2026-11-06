"""The new framework does not import the legacy root modules (docs/point_extractors_split_plan.md §3.4).

The legacy scripts at the repository root will be removed together at the end;
nothing in the new framework may depend on them.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NEW_PACKAGES = ("apps", "contracts", "evaluation", "orchestration", "runtime", "targets")
LEGACY_MODULES = {"point_extractors", "strategist", "estimator", "master_orchestrator", "run_manager"}
# Entry points of the legacy paths themselves, removed together with them.
LEGACY_ENTRY_POINTS = {"apps/cli/bbsl_local_ft4d_main.py"}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_new_packages_do_not_import_legacy_root_modules() -> None:
    offenders = {
        str(path.relative_to(ROOT)): sorted(_imports(path) & LEGACY_MODULES)
        for package in NEW_PACKAGES
        for path in (ROOT / package).rglob("*.py")
        if _imports(path) & LEGACY_MODULES and str(path.relative_to(ROOT)) not in LEGACY_ENTRY_POINTS
    }
    assert offenders == {}
