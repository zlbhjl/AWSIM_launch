from __future__ import annotations

import sys
import textwrap

import pytest

from runtime.container.profile_registry import (
    get_container_profile,
    list_container_profiles,
    reload_container_profiles,
)

PROFILE = """
from runtime.container.profile import ContainerLaunchProfile

CONTAINER_LAUNCH_PROFILE = ContainerLaunchProfile(name={name!r}, image="example/image:1", target={target!r},
                                                  requires_gpu=False)
"""


@pytest.fixture
def extra_profiles(tmp_path, monkeypatch):
    def write(target: str, module: str, *, name: str | None = None, profile_target: str | None = None) -> None:
        folder = tmp_path / "plugins" / "targets" / target / "container_profiles"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{module}.py").write_text(
            textwrap.dedent(PROFILE.format(name=name or module, target=profile_target or target)), encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path / "plugins"))
    yield write
    for module in [name for name in sys.modules if name.startswith("targets.walk")]:
        sys.modules.pop(module, None)
    reload_container_profiles()


def test_existing_profiles_are_found_in_their_targets() -> None:
    assert "prism_maude" in list_container_profiles("prism")
    assert set(list_container_profiles("awsim")) >= {"legacy", "autoware171", "autoware190_ekfdiagfix"}
    assert get_container_profile("autoware171").target == "awsim"
    with pytest.raises(ValueError, match="Unsupported container profile"):
        get_container_profile("missing")


def test_a_profile_placed_in_a_new_target_is_discovered(extra_profiles) -> None:
    extra_profiles("walk", "walk_cpu")
    reload_container_profiles()

    assert list_container_profiles("walk") == ("walk_cpu",)
    assert get_container_profile("walk_cpu").target == "walk"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "other"}, "must match the module name"),
        ({"profile_target": "awsim"}, "must be 'walk'"),
    ],
)
def test_inconsistent_profiles_are_rejected(extra_profiles, kwargs, message) -> None:
    extra_profiles("walk", "walk_cpu", **kwargs)
    reload_container_profiles()

    with pytest.raises(ValueError, match=message):
        list_container_profiles()


def test_duplicate_names_across_targets_are_rejected(extra_profiles) -> None:
    extra_profiles("walk", "prism_maude")
    reload_container_profiles()

    with pytest.raises(ValueError, match="more than one target"):
        list_container_profiles()
