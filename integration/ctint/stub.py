"""E-REFERENCE-STUB — lane E's labelled stand-in for stages no lane has delivered yet.

This is NOT a product implementation. It exists so that (a) the pipeline
orchestrator and every validator can be exercised end to end today, and (b) other
lanes have a concrete, contract-valid example of each artifact. Whenever it
runs, the stage is reported as ``STUB`` and the overall verdict can be at best
``NOT-INTEGRATED``. There is deliberately no stub for ``ui.e2e``.

    python -m ctint.stub <stage> --artifacts DIR [--seed N] [--count N]
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import atlas_quality, synth
from . import experiment_integrity as ei
from .baseline import load_reference_engine
from .e2e import STAGE_BY_ID, USABLE_CLAIM_STATUSES, RunOutcome, Stage, read_json, write_json

NAME = "E-REFERENCE-STUB"


def _profile(claims: list[dict[str, Any]]) -> dict[str, Any]:
    skills = sorted({s for c in claims if c["status"] in USABLE_CLAIM_STATUSES for s in c.get("skills", [])})
    return {"skills": skills, "preferences": {"remote": "remote"}}


def jobs_import(a: Path, seed: int, count: int) -> None:
    write_json(a / "jobs.json", synth.gen_jobs(count, seed))


def profile_claims(a: Path, seed: int, count: int) -> None:
    write_json(a / "claims.json", synth.gen_claims(seed))


def match_rank(a: Path, seed: int, count: int) -> None:
    engine = load_reference_engine()
    profile = _profile(read_json(a / "claims.json"))
    rows = []
    for job in read_json(a / "jobs.json"):
        # The reference engine indexes job["remote_policy"], which the contract makes optional.
        rows.append(engine.score_job(profile, dict(job, remote_policy=job.get("remote_policy", "unknown"))))
    rows.sort(key=lambda r: (-r["score"], r["job_id"]))
    write_json(a / "matches.json", rows)


def features_vectors(a: Path, seed: int, count: int) -> None:
    write_json(a / "vectors.json", synth.gen_vectors(read_json(a / "jobs.json")))


def resume_variant(a: Path, seed: int, count: int) -> None:
    pin = read_json(a / "pin.json")
    job = next(j for j in read_json(a / "jobs.json") if j["id"] == pin["job_id"])
    # D found (on #5) that the first stub cited a `private` claim; résumé text may leave the device only
    # from shareable claims (B's and D's export gates, A's resume-draft export mode).
    usable = [
        c for c in read_json(a / "claims.json")
        if c["status"] in USABLE_CLAIM_STATUSES and c.get("visibility") != "private"
    ]  # fmt: skip
    relevant = [c for c in usable if set(c.get("skills", [])) & set(job["skills"])] or usable[:3]
    write_json(
        a / "resume_variant.json",
        {
            "id": f"rv-stub-s{seed}-{job['id']}",
            "job_id": job["id"],
            "claim_ids": [c["id"] for c in relevant],
            "bullets": [{"text": c["claim_text"], "claim_ids": [c["id"]]} for c in relevant],
            "truth_note": "Every bullet is a verbatim supported or self-reported claim; nothing is generated.",
            "generated_by": NAME,
        },
    )


def application_record(a: Path, seed: int, count: int) -> None:
    pin = read_json(a / "pin.json")
    variant = read_json(a / "resume_variant.json")
    events = []
    for index, step in enumerate(("saved", "drafted", "approved", "submitted")):
        events.append({
            "id": f"evt-stub-s{seed}-{index:03d}", "application_id": f"app-stub-s{seed}", "job_id": pin["job_id"],
            "resume_variant_id": None if step == "saved" else variant["id"], "event_type": step,
            "recorded_at": synth.ts(50_000 + index * 10), "occurred_at": None, "actor": "user",
            "source": "synthetic-stub", "outcome_status": "observed", "experiment_id": None,
            "approval_token_ref": f"approval-stub-s{seed}" if step in ("approved", "submitted") else None,
            "notes": "Synthetic: no application was sent anywhere.",
        })  # fmt: skip
    write_json(a / "events.json", events)


def experiment_run(a: Path, seed: int, count: int) -> None:
    job_ids = [j["id"] for j in read_json(a / "jobs.json")]
    units = min(len(job_ids), 160)
    spec, assignments, events, analysis = ei.synth_experiment_world(seed, units, job_ids=job_ids[:units])
    write_json(a / "experiment.json", spec)
    write_json(a / "assignments.json", assignments)
    write_json(a / "experiment_events.json", events)
    write_json(a / "analysis.json", analysis)


def atlas_project(a: Path, seed: int, count: int) -> None:
    vectors = read_json(a / "vectors.json")
    jobs_path = a / "jobs.json"
    labels = {j["id"]: j["title"] for j in read_json(jobs_path)} if jobs_path.is_file() else {}
    points, diagnostics = atlas_quality.build_reference_atlas(vectors, labels, f"proj-stub-pca-s{seed}")
    write_json(a / "atlas_points.json", points)
    write_json(a / "atlas_diagnostics.json", diagnostics)


def simulator_scenario(a: Path, seed: int, count: int) -> None:
    engine = load_reference_engine()
    claims = read_json(a / "claims.json")
    pin = read_json(a / "pin.json")
    job = next(j for j in read_json(a / "jobs.json") if j["id"] == pin["job_id"])
    gap = engine.skill_gap(_profile(claims), job)
    write_json(
        a / "scenarios.json",
        [
            {
                "id": f"scn-stub-s{seed}-0001",
                "candidate_ref": claims[0]["subject_id"],
                "target_role": job["title"],
                "planned_actions": [
                    {"skill": s, "effort_hours": 40, "evidence_goal": f"One synthetic project evidencing {s}"}
                    for s in gap["missing_skills"]
                ],
                "time_horizon_months": 6,
                "status": "hypothetical",
                "assumptions": ["40 hours per skill is a placeholder, not an estimate.", f"Generated by {NAME}."],
                "outputs": {"missing_skills": gap["missing_skills"], "already_evidenced": gap["already_evidenced"],
                            "status": gap["status"]},
                "risks": ["A skill list is not a hiring requirement; real requirements are unobserved."],
            }
        ],
    )  # fmt: skip


STUBS: dict[str, Callable[[Path, int, int], None]] = {
    "jobs.import": jobs_import,
    "profile.claims": profile_claims,
    "match.rank": match_rank,
    "features.vectors": features_vectors,
    "resume.variant": resume_variant,
    "application.record": application_record,
    "experiment.run": experiment_run,
    "atlas.project": atlas_project,
    "simulator.scenario": simulator_scenario,
}


class StubProvider:
    """In-process provider. ``mock`` and ``is_stub`` are always true."""

    name = NAME
    mock = True
    is_stub = True

    def run(self, stage: Stage, artifacts: Path, seed: int, count: int) -> RunOutcome:
        STUBS[stage.id](artifacts, seed, count)
        return RunOutcome(True, "stub ran in-process")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one E-REFERENCE-STUB stage (mock output, labelled as such)"
    )
    parser.add_argument("stage", choices=sorted(STUBS))
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--count", type=int, default=150)
    args = parser.parse_args(argv)
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    STUBS[args.stage](artifacts, args.seed, args.count)
    print(f"{NAME}: wrote {', '.join(STAGE_BY_ID[args.stage].writes)} (MOCK OUTPUT — not a lane deliverable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
