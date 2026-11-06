"""Reference probability of a sampling plan by plain Monte Carlo through the production path.

For models without a closed form and without a vectorised reference (the U-turn; any target),
the Q = P plan is run in chunks on the ``reference`` random phase, which is
separate from the pilot and final phases. With Q = P both the ordinary and the
importance paths are independent draws from P (weight 1), so their event counts
are pooled. The output has the same form as the model C reference and is read
by ``tools.validation.compare_to_reference``.

    python3 -m tools.validation.reference_by_plain_mc \\
        --plan configs/is_final_reference/uturn_dx10p5_q0.yaml \\
        --chunks 50 --samples 10000 --output artifacts/model_d/reference.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

from tools.validation.coverage_experiment import clopper_pearson
from tools.validation.importance_runs import run_plan_fixed


def run_chunk(task: tuple[str, int, int]) -> dict[str, object]:
    plan, samples, root = task
    with tempfile.TemporaryDirectory(prefix="plain-mc-reference-") as tmp:
        comparison = run_plan_fixed(plan, workdir=tmp, max_samples=samples, root_entropy=root, phase="reference")
    diagnostic = comparison.get("diagnostic", {})
    if comparison.get("final_status") != "complete":
        raise RuntimeError(f"chunk root={root} did not complete: {comparison.get('final_status')}")
    hits = 0
    for kind in ("ordinary", "importance"):
        block = diagnostic.get(kind, {})
        if int(block.get("sample_size", -1)) != samples or block.get("excluded_records"):
            raise RuntimeError(f"chunk root={root} {kind} has {block.get('sample_size')} samples "
                               f"(excluded {block.get('excluded_records')}), expected {samples}")
        hits += round(float(block["estimate"]) * samples)
    return {"root": root, "paths": 2 * samples, "hits": hits}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", required=True, help="A Q = P plan of the target measure")
    parser.add_argument("--chunks", type=int, default=50)
    parser.add_argument("--samples", type=int, default=10000, help="Ordinary (and Q = P) paths per chunk")
    parser.add_argument("--seed-base", type=int, default=20261026)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    tasks = [(args.plan, args.samples, args.seed_base + chunk) for chunk in range(args.chunks)]
    with Pool(processes=args.workers) as pool:
        chunks = pool.map(run_chunk, tasks, chunksize=1)
    paths = sum(chunk["paths"] for chunk in chunks)
    hits = sum(chunk["hits"] for chunk in chunks)
    reference = {
        "plan": args.plan,
        "method": "plain Monte Carlo through the production path (Q = P plan, phase 'reference')",
        "paths": paths,
        "hits": hits,
        "estimate": hits / paths,
        "ci95_clopper_pearson": list(clopper_pearson(hits, paths)),
        "seed_base": args.seed_base,
        "chunks": chunks,
        "seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: reference[key] for key in ("paths", "hits", "estimate", "ci95_clopper_pearson", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
