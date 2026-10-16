from __future__ import annotations

from targets.awsim.case_kinds import uturn as base

SCENARIO_TYPE = base.SCENARIO_TYPE
REPEAT_COUNT = base.REPEAT_COUNT
TIMEOUT_SEC = 5

RESULT_LABELS = list(base.RESULT_LABELS)
FORMULAS = list(base.FORMULAS)
INVALID_CONDITIONS = dict(base.INVALID_CONDITIONS)
TARGET_NPCS = list(base.TARGET_NPCS)
PARAM_RANGES = dict(base.PARAM_RANGES)
FIXED_PARAMS = dict(base.FIXED_PARAMS)
FOCUS_POINTS = [dict(point) for point in base.FOCUS_POINTS]
