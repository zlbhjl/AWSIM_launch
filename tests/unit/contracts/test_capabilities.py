import math
from types import SimpleNamespace

import pytest

from contracts.capabilities import (
    INTERNAL_DENSITY,
    CapabilityError,
    require_capabilities,
    target_capabilities,
)
from contracts.sampling import SamplingMetadataValidationError, normalize_internal_report


def test_a_target_without_declaration_is_a_pure_black_box() -> None:
    assert target_capabilities(SimpleNamespace()) == frozenset()
    with pytest.raises(CapabilityError, match="'walk' does not declare internal_density"):
        require_capabilities("walk", SimpleNamespace(), {INTERNAL_DENSITY}, algorithm="internal importance sampling")


def test_declared_capabilities_are_checked() -> None:
    plugin = SimpleNamespace(capabilities={INTERNAL_DENSITY})
    assert target_capabilities(plugin) == {INTERNAL_DENSITY}
    require_capabilities("gauss", plugin, {INTERNAL_DENSITY}, algorithm="x")
    with pytest.raises(ValueError, match="unknown capabilities"):
        target_capabilities(SimpleNamespace(capabilities={"teleport"}))
    with pytest.raises(TypeError):
        target_capabilities(SimpleNamespace(capabilities="internal_density"))


def test_internal_report_is_normalized() -> None:
    report = normalize_internal_report({"log_p": -1, "log_q": [-2, -math.inf], "sampled_component": 0}, components=2)
    assert report == {"log_p": -1.0, "log_q": [-2.0, -math.inf], "sampled_component": 0, "support": "positive"}
    assert normalize_internal_report(
        {"log_p": None, "log_q": [-3.0], "sampled_component": 0, "support": "zero"})["log_p"] is None
    assert normalize_internal_report({"log_p": -1.0, "log_q": [-2.0], "sampled_component": "target"})


@pytest.mark.parametrize("report", [
    {"log_p": -1.0, "log_q": [], "sampled_component": 0},
    {"log_p": -1.0, "log_q": [-2.0], "sampled_component": 1},
    {"log_p": -1.0, "log_q": [-math.inf], "sampled_component": 0},
    {"log_p": math.nan, "log_q": [-2.0], "sampled_component": 0},
    {"log_p": -1.0, "log_q": [math.inf], "sampled_component": 0},
    {"log_p": -1.0, "log_q": [-2.0], "sampled_component": 0, "support": "zero"},
    {"log_p": None, "log_q": [-2.0], "sampled_component": "target", "support": "zero"},
    {"log_p": -1.0, "log_q": [-2.0], "sampled_component": True},
])
def test_invalid_internal_reports_are_rejected(report) -> None:
    with pytest.raises(SamplingMetadataValidationError):
        normalize_internal_report(report)


def test_component_count_is_checked() -> None:
    with pytest.raises(SamplingMetadataValidationError, match="one per component"):
        normalize_internal_report({"log_p": -1.0, "log_q": [-2.0], "sampled_component": 0}, components=2)
