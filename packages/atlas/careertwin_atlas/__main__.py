"""Command line entry point. Offline and synthetic only.

    python -m careertwin_atlas demo --out analytics/reports
    python -m careertwin_atlas simulate --target field-cto --action cloud-architecture:120:portfolio-project
    python -m careertwin_atlas stress --n 5000
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .api import build_atlas_bundle
from .engine import AtlasEngine
from .errors import AtlasError
from .features import claims_to_vector
from .report import AS_OF, CANDIDATE_REF, default_claims, run_demo
from .simulator import simulate_transition
from .taxonomy import generate_postings
from .vocabulary import default_vocabulary


def _parse_action(text: str) -> dict:
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("action must look like skill:hours[:evidence-goal]")
    try:
        hours = float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("hours must be a number") from exc
    action = {"skill": parts[0], "effort_hours": hours}
    if len(parts) == 3:
        action["evidence_goal"] = parts[2]
    return action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="careertwin_atlas",
                                     description="Synthetic, offline career atlas (lane C).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo", help="run the full offline demo and write the diagnostics report")
    demo.add_argument("--n", type=int, default=1500)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--out", default=None)
    demo.add_argument("--stress-n", type=int, default=5000)
    sim = sub.add_parser("simulate", help="simulate a hypothetical transition for the synthetic candidate")
    sim.add_argument("--target", required=True)
    sim.add_argument("--action", action="append", type=_parse_action, default=[])
    sim.add_argument("--months", type=int, default=12)
    sim.add_argument("--hours-per-week", type=float, default=6.0)
    sim.add_argument("--n", type=int, default=1500)
    sim.add_argument("--seed", type=int, default=7)
    stress = sub.add_parser("stress", help="time fit + diagnostics on N synthetic vectors")
    stress.add_argument("--n", type=int, default=5000)
    stress.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "demo":
            summary = run_demo(args.n, args.seed, args.out, stress_n=args.stress_n)
            d = summary["bundle"]["diagnostics"]
            print("SYNTHETIC CAREER ATLAS DEMO - all postings, employers and claims are fictional.")
            print(summary["bundle"]["information_loss"]["statement"])
            print(json.dumps({
                "postings": summary["n_postings"],
                "variance_shown_2d": round(d["explained_variance_cumulative"], 4),
                "trustworthiness": round(d["trustworthiness"], 4),
                "continuity": round(d["continuity"], 4),
                "knn_recall": round(d["knn_recall_mean"], 4),
                "umap": summary["comparison"]["methods"]["umap"]["status"],
                "scenario_id": summary["scenario"]["id"],
                "scenario_shortfall": [summary["scenario"]["outputs"]["baseline"]["requirement_shortfall"],
                                       summary["scenario"]["outputs"]["hypothetical"]["requirement_shortfall"]],
                "sensitive_to": summary["sensitivity"]["summary"]["sensitive_to"],
                "stress": summary["stress"],
                "written_to": args.out,
            }, indent=2))
            return 0
        vocab = default_vocabulary()
        if args.cmd == "simulate":
            engine = AtlasEngine.fit(generate_postings(args.n, vocab.skill_names, seed=args.seed), vocab, seed=args.seed)
            cand = claims_to_vector(default_claims(), vocab, CANDIDATE_REF, as_of=AS_OF)
            scenario = simulate_transition(engine, cand, args.target, args.action, args.months, args.hours_per_week)
            print(json.dumps(scenario, indent=2, sort_keys=True))
            return 0
        if args.cmd == "stress":
            t0 = time.perf_counter()
            engine = AtlasEngine.fit(generate_postings(args.n, vocab.skill_names, seed=args.seed), vocab,
                                     seed=args.seed, n_boot=5)
            t1 = time.perf_counter()
            bundle = build_atlas_bundle(engine, None, include_clusters=False)
            t2 = time.perf_counter()
            print(json.dumps({"n": args.n, "features": len(vocab.features), "fit_seconds": round(t1 - t0, 2),
                              "bundle_seconds": round(t2 - t1, 2), "points": len(bundle["points"]),
                              "trustworthiness": round(bundle["diagnostics"]["trustworthiness"], 4)}, indent=2))
            return 0
    except AtlasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
