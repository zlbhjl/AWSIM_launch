#!/usr/bin/env python3

from __future__ import annotations

import sys

from apps.cli.bbsl_local_ft4d_main import (
    _build_execution_profile_from_args,
    _evaluate_bbsl_batch_outputs_via_new_pipeline,
    _evaluate_bbsl_output_via_new_pipeline,
    _rebuild_batch_loop_result_via_new_pipeline,
    main,
    parse_args,
)


if __name__ == "__main__":
    sys.exit(main())
