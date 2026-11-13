"""Command line entry point. Offline and synthetic only.

    python -m careertwin_atlas demo --out analytics/reports
    python -m careertwin_atlas simulate --target field-cto --action cloud-architecture:120:portfolio-project
    python -m careertwin_atlas stress --n 5000
    python -m careertwin_atlas export --out DIR --generated-at 2026-11-10T12:00:00Z
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from pathlib import Path

from .api import build_atlas_bundle, projection_view
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
    exp = sub.add_parser("export", help="write atlas.pca.json, atlas.axes.json, scenarios.json for lane D")
    exp.add_argument("--out", required=True)
    exp.add_argument("--generated-at", required=True, help="ISO-8601 UTC timestamp; the package never reads the clock")
    exp.add_argument("--n", type=int, default=600)
    exp.add_argument("--seed", type=int, default=7)
    exp.add_argument("--max-job-points", type=int, default=150)
    stress = sub.add_parser("stress", help="time fit + diagnostics on N synthetic vectors")
    stress.add_argument("--n", type=int, default=5000)
    stress.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if getattr(args, "seed", 0) < 0:
        parser.error("--seed must be >= 0")
    if getattr(args, "n", 100) < 20 or getattr(args, "n", 100) > 200_000:
        parser.error("--n must be within [20, 200000]")

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
        if args.cmd == "export":
            engine = AtlasEngine.fit(generate_postings(args.n, vocab.skill_names, seed=args.seed), vocab,
                                     seed=args.seed, n_boot=20)
            cand = claims_to_vector(default_claims(), vocab, CANDIDATE_REF, as_of=AS_OF)
            plans = {
                "fde": [{"skill": "customer-delivery", "effort_hours": 80, "evidence_goal": "client-engagement: one fictional integration"},
                        {"skill": "llm-integration", "effort_hours": 60, "evidence_goal": "portfolio-project"}],
                "ai-architect": [{"skill": "mlops", "effort_hours": 100, "evidence_goal": "portfolio-project"},
                                 {"skill": "rag", "effort_hours": 60, "evidence_goal": "portfolio-project"}],
                "field-cto": [{"skill": "cloud-architecture", "effort_hours": 120, "evidence_goal": "portfolio-project"},
                              {"skill": "ai-strategy", "effort_hours": 80, "evidence_goal": "portfolio-project"},
                              {"skill": "client-exposure", "effort_hours": 90, "evidence_goal": "coursework"}],
            }
            scenarios = [simulate_transition(engine, cand, role, plan, 12) for role, plan in plans.items()]
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            pca = build_atlas_bundle(engine, cand, seed=args.seed, include_clusters=False,
                                     max_job_points=args.max_job_points)
            axes = build_atlas_bundle(engine, cand, method="interpretable-axes", seed=args.seed,
                                      include_clusters=False, max_job_points=args.max_job_points)
            docs = {"atlas.pca.json": projection_view(pca, args.generated_at, scenarios),
                    "atlas.axes.json": projection_view(axes, args.generated_at),
                    "scenarios.json": scenarios}
            for name, doc in docs.items():
                (out / name).write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True,
                                                   allow_nan=False) + "\n", encoding="utf-8", newline="\n")
            print(json.dumps({"written": sorted(docs), "out": str(out), "scenarios": [s["id"] for s in scenarios]},
                             indent=2))
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
