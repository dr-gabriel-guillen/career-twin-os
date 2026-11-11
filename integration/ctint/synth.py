"""Seeded, fully fictional data generators for scale, fuzz and E2E harness tests.

Everything here is synthetic by construction: employers are "Fictional ...",
hosts are under the RFC 2606 ``.example`` TLD, timestamps derive from a fixed
epoch (never the wall clock) and the single candidate is ``candidate-synthetic-001``.
These generators exist so lane E can test its own harness and stress contracts;
they are NOT lane A/B/C deliverables and are labelled as stub output when used in E2E.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

EPOCH = datetime(2026, 1, 5, 9, 0, 0, tzinfo=UTC)
SUBJECT = "candidate-synthetic-001"

ROLE_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "fde": (
        "Forward Deployed Engineer",
        ("python", "typescript", "api-design", "customer-delivery", "ai-evaluation", "solution-scoping"),
    ),
    "ai-architect": (
        "Applied AI Architect",
        ("python", "aws", "mlops", "ai-evaluation", "api-design", "retrieval-systems"),
    ),
    "field-cto": (
        "Field CTO",
        (
            "enterprise-sales",
            "cloud-architecture",
            "executive-communication",
            "customer-delivery",
            "ai-strategy",
        ),
    ),
    "ai-director": (
        "AI Director",
        ("ai-strategy", "people-management", "budgeting", "executive-communication", "roadmapping"),
    ),
    "ai-eng-lead": (
        "AI Engineering Lead",
        ("python", "mlops", "people-management", "code-review", "ai-evaluation", "roadmapping"),
    ),
    "backend": (
        "Backend Engineer",
        ("python", "api-design", "distributed-systems", "sql", "observability"),
    ),
    "platform": (
        "Platform Engineer",
        ("kubernetes", "aws", "observability", "distributed-systems", "security", "ci-cd"),
    ),
    "privacy-legal-ai": (
        "Privacy & Legal AI Specialist",
        ("privacy-law", "ai-governance", "risk-assessment", "policy-writing", "security"),
    ),
}
GENERAL_SKILLS = ("communication", "documentation", "testing", "mentoring")
VOCABULARY: tuple[str, ...] = tuple(
    sorted({s for _, skills in ROLE_FAMILIES.values() for s in skills} | set(GENERAL_SKILLS))
)
SENIORITIES = ("mid", "senior", "staff", "principal", "director")
_ORG_NAMES = ("Meridian", "Quartz", "Aurora", "Nebula", "Halcyon", "Tessellate", "Brightwater", "Kestrel")
_ORG_SUFFIX = ("Systems", "Labs", "Enterprises", "Software", "Collective", "Works")
_LOCATIONS = ("Fictional City, Nowhere", "Imaginary Harbor, Nowhere", "Placeholder Springs, Nowhere")


def ts(minutes: int) -> str:
    """UTC RFC 3339 timestamp ``minutes`` after the fixed synthetic epoch."""
    return (EPOCH + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_jobs(n: int, seed: int = 0) -> list[dict[str, Any]]:
    rng = random.Random(f"jobs:{seed}")
    jobs: list[dict[str, Any]] = []
    families = list(ROLE_FAMILIES)
    for i in range(n):
        family = families[i % len(families)] if i < len(families) else rng.choice(families)
        title, core = ROLE_FAMILIES[family]
        skills = set(rng.sample(core, rng.randint(3, len(core))))
        skills.update(rng.sample(GENERAL_SKILLS, rng.randint(0, 2)))
        org_name = rng.choice(_ORG_NAMES)
        org = f"Fictional {org_name} {rng.choice(_ORG_SUFFIX)}"
        job: dict[str, Any] = {
            "id": f"job-syn-s{seed}-{i:06d}",
            "source": "synthetic",
            "source_id": f"syn-feed-{seed}:{i:06d}",
            "title": title
            if rng.random() < 0.7
            else f"{rng.choice(('Senior', 'Lead', 'Principal'))} {title}",
            "organization": org,
            "url": f"https://jobs.fictional-{org_name.lower()}.example/postings/s{seed}-{i:06d}",
            "fetched_at": ts(i),
            "status": rng.choices(("active", "expired", "unknown"), weights=(6, 2, 2))[0],
            "skills": sorted(skills),
            "seniority": rng.choice(SENIORITIES),
            "employment_type": rng.choice(("full-time", "contract", "part-time")),
            "role_family": family,
            "license_note": f"Synthetic fixture (lane E generator, seed {seed}). Fictional employer; not a real opening.",
            "requirements": [f"Demonstrated work with {s}" for s in sorted(skills)[:3]],
            "source_evidence": [f"syn-feed-{seed}#{i:06d}"],
        }
        job["location"] = None if rng.random() < 0.2 else rng.choice(_LOCATIONS)
        if rng.random() < 0.9:
            job["remote_policy"] = rng.choice(("remote", "hybrid", "onsite", "unknown"))
        roll = rng.random()
        if roll < 0.35:
            job["compensation"] = None
        elif roll < 0.45:
            job["compensation"] = {
                "currency": "USD", "min": None, "max": None, "basis": "unknown", "verified": False,
            }  # fmt: skip
        else:
            low = rng.randrange(90, 260) * 1000
            job["compensation"] = {
                "currency": rng.choice(("USD", "EUR", "GBP")),
                "min": low,
                "max": low + rng.randrange(0, 60) * 1000,
                "basis": rng.choice(("base", "total")),
                "verified": False,
            }
        jobs.append(job)
    return jobs


def gen_claims(seed: int = 0, subject: str = SUBJECT, n: int = 14) -> list[dict[str, Any]]:
    rng = random.Random(f"claims:{seed}")
    statuses = ("supported", "self-reported", "unverified", "contradicted")
    skills = list(VOCABULARY)
    rng.shuffle(skills)
    claims: list[dict[str, Any]] = []
    for i in range(n):
        # The first six claims are always usable so a résumé variant can be built.
        status = "supported" if i < 4 else "self-reported" if i < 6 else rng.choice(statuses)
        skill = skills[i % len(skills)]
        claim: dict[str, Any] = {
            "id": f"claim-syn-s{seed}-{i:04d}",
            "subject_id": subject,
            "claim_type": "skill" if i % 3 else "project",
            "claim_text": f"Synthetic claim: practical experience with {skill}.",
            "status": status,
            "evidence_ids": [f"evidence-syn-s{seed}-{i:04d}"]
            if status in ("supported", "contradicted")
            else [],
            # the first three claims are always shareable so a résumé can be built without private data
            "visibility": "shareable" if i < 3 else rng.choice(("private", "shareable")),
            "updated_at": ts(i * 7),
            "skills": [skill],
        }
        if i % 4 == 0:
            claim["valid_to"] = None if i % 8 == 0 else ts(60 * 24 * 365 + i)
        claims.append(claim)
    return claims


def gen_application_events(
    jobs: list[dict[str, Any]], n_applications: int = 100, seed: int = 0
) -> list[dict[str, Any]]:
    """Immutable event streams for ``n_applications`` synthetic applications."""
    rng = random.Random(f"apps:{seed}")
    events: list[dict[str, Any]] = []
    clock = 10_000
    for a in range(n_applications):
        job = jobs[rng.randrange(len(jobs))]
        app_id = f"app-syn-s{seed}-{a:04d}"
        variant = f"rv-syn-s{seed}-{a:04d}"
        token = f"approval-syn-s{seed}-{a:04d}"
        path = ["saved", "drafted", "approved", "submitted"][: rng.choice((1, 2, 4, 4, 4))]
        if len(path) == 4:
            path += rng.choice((
                [], ["rejected"], ["recruiter_screen"], ["recruiter_screen", "interview"],
                ["recruiter_screen", "interview", "offer"], ["recruiter_screen", "rejected"], ["withdrawn"],
            ))  # fmt: skip
        for step in path:
            clock += rng.randrange(1, 240)
            outcome = step in ("recruiter_screen", "interview", "rejected", "offer")
            event: dict[str, Any] = {
                "id": f"evt-syn-s{seed}-{len(events):06d}",
                "application_id": app_id,
                "job_id": job["id"],
                "resume_variant_id": None if step == "saved" else variant,
                "event_type": step,
                "recorded_at": ts(clock),
                "occurred_at": ts(clock - rng.randrange(0, 30)) if outcome else None,
                "actor": "user",
                "source": "synthetic-generator",
                "outcome_status": rng.choice(("observed", "self-reported")) if outcome else "observed",
                "experiment_id": None,
                "approval_token_ref": token if step in ("approved", "submitted") else None,
            }
            events.append(event)
    return events


def gen_experiments(n: int = 20, seed: int = 0) -> list[dict[str, Any]]:
    rng = random.Random(f"experiments:{seed}")
    specs: list[dict[str, Any]] = []
    for i in range(n):
        status = ("draft", "preregistered", "running", "paused", "closed")[i % 5]
        spec: dict[str, Any] = {
            "id": f"exp-syn-s{seed}-{i:04d}",
            "title": f"Synthetic experiment {i}",
            "hypothesis": f"Résumé variant B changes recruiter-screen rate versus A (synthetic #{i}).",
            "status": status,
            "variants": ["A", "B"] if i % 4 else ["A", "B", "C"],
            "assignment_unit": rng.choice(("job", "employer-cluster", "application")),
            "strata": ["role_family"] if i % 2 else ["role_family", "seniority"],
            "primary_metric": "recruiter_screen_within_window",
            "design_locked_at": None if status == "draft" else ts(i * 60),
            "stopping_rule": f"Fixed sample: stop at {rng.choice((80, 120, 200))} assigned units.",
            "consent_required": True,
            "outcome_window_days": rng.choice((14, 21, 30)),
            "analysis_plan": "Intention-to-treat difference in proportions with a 95% interval.",
        }
        specs.append(spec)
    return specs


def gen_scenarios(n: int = 10, seed: int = 0, subject: str = SUBJECT) -> list[dict[str, Any]]:
    rng = random.Random(f"scenarios:{seed}")
    families = list(ROLE_FAMILIES)
    scenarios: list[dict[str, Any]] = []
    for i in range(n):
        title, core = ROLE_FAMILIES[families[i % len(families)]]
        gaps = rng.sample(core, 2)
        scenarios.append({
            "id": f"scn-syn-s{seed}-{i:04d}",
            "candidate_ref": subject,
            "target_role": title,
            "planned_actions": [
                {"skill": g, "effort_hours": rng.randrange(20, 200), "evidence_goal": f"Synthetic project using {g}"}
                for g in gaps
            ],
            "time_horizon_months": rng.randrange(3, 25),
            "status": "hypothetical",
            "assumptions": ["Effort hours are synthetic placeholders, not measurements."],
            "outputs": {"missing_evidence": gaps, "status": "hypothetical_gap_not_probability"},
            "risks": ["Effort may be underestimated."],
        })  # fmt: skip
    return scenarios


def gen_vectors(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Transparent binary skill vectors over the fixed vocabulary plus a seniority ordinal."""
    vectors = []
    for job in jobs:
        have = set(job["skills"])
        values = [1.0 if skill in have else 0.0 for skill in VOCABULARY]
        level = SENIORITIES.index(job["seniority"]) if job["seniority"] in SENIORITIES else 0
        values.append(level / (len(SENIORITIES) - 1))
        vectors.append({"ref": f"vec:{job['id']}", "entity_id": job["id"], "values": values})
    return {
        "vocabulary": [*VOCABULARY, "seniority_ordinal"],
        "missing_value": "absent=0.0",
        "vectors": vectors,
    }
