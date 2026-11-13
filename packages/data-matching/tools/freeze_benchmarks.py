"""Re-freeze the deterministic benchmark baselines and record a (machine-dependent) throughput run.

Run: ``python packages/data-matching/tools/freeze_benchmarks.py [--perf 1000 10000]``.
``benchmarks/frozen_metrics.json`` is asserted exactly by the test-suite; ``benchmarks/perf_*.json``
is evidence of one run on one machine and is never asserted.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PKG / "src"), str(PKG.parents[1] / "connectors")]

from ct_matching import evalbench  # noqa: E402

OUT = PKG / "benchmarks"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--perf", nargs="*", type=int, default=[])
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    frozen = {"ranking": evalbench.ranking_benchmark(), "retrieval": evalbench.retrieval_benchmark()}
    (OUT / "frozen_metrics.json").write_text(json.dumps(frozen, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: {m: v for m, v in frozen[k].items() if m != "limits"} for k in frozen}, indent=1))
    for n in args.perf:
        run = evalbench.perf_benchmark(n)
        run["environment"] = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                              "machine": platform.machine(), "system": platform.system()}
        (OUT / f"perf_n{n}.json").write_text(json.dumps(run, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(run, indent=1))


if __name__ == "__main__":
    main()
