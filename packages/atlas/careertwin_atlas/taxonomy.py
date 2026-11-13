"""Synthetic taxonomy of role families and a deterministic synthetic posting generator.

EVERYTHING HERE IS FICTIONAL. The skill prevalences below are design inputs that lane C
invented so that the atlas has something structured to project. They are not survey results,
not scraped postings and not a description of any real employer or labour market. Generated
postings use ``source = "synthetic"``, organisations named "Fictional ...", the reserved
``.invalid`` top-level domain and compensation (when present) in the non-currency unit "SYN".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import numpy as np

from .errors import InvalidInputError

GENERATOR_VERSION = "atlas-synth-0.1.0"
REFERENCE_DATE = datetime(2026, 10, 31, tzinfo=timezone.utc)

_CORE_P = 0.85
_SECONDARY_P = 0.45
_BACKGROUND_P = 0.04


@dataclass(frozen=True)
class RoleFamily:
    family_id: str
    label: str
    titles: tuple[str, ...]
    core: tuple[str, ...]
    secondary: tuple[str, ...]
    seniority: tuple[tuple[str, float], ...]     # (level name, sampling weight)


FAMILIES: tuple[RoleFamily, ...] = (
    RoleFamily("fde", "Forward Deployed Engineer",
               ("Forward Deployed Engineer", "Forward Deployed AI Engineer", "Deployment Engineer"),
               ("python", "api-design", "customer-delivery", "llm-integration",
                "technical-discovery", "solution-design"),
               ("typescript", "ai-evaluation", "rag", "stakeholder-management", "sql", "aws",
                "prompt-engineering"),
               (("mid", 0.3), ("senior", 0.5), ("staff", 0.2))),
    RoleFamily("ai-architect", "Applied AI Architect",
               ("Applied AI Architect", "AI Solutions Architect", "LLM Systems Architect"),
               ("python", "ai-evaluation", "llm-integration", "system-design",
                "cloud-architecture", "mlops", "rag"),
               ("aws", "api-design", "solution-design", "agent-orchestration", "data-modeling",
                "security", "ai-strategy"),
               (("senior", 0.3), ("staff", 0.45), ("principal", 0.25))),
    RoleFamily("field-cto", "Field CTO",
               ("Field CTO", "Field Chief Technology Officer", "Regional Field CTO"),
               ("executive-communication", "enterprise-sales", "cloud-architecture", "ai-strategy",
                "customer-delivery", "stakeholder-management", "solution-design"),
               ("pre-sales-demos", "technical-leadership", "roadmap-planning",
                "technical-discovery", "system-design"),
               (("principal", 0.25), ("director", 0.5), ("vp", 0.25))),
    RoleFamily("ai-director", "AI Director",
               ("Director of AI", "AI Director", "Head of Applied AI"),
               ("people-management", "ai-strategy", "roadmap-planning", "hiring",
                "budget-ownership", "stakeholder-management", "technical-leadership"),
               ("org-design", "executive-communication", "ai-governance", "mlops",
                "ai-evaluation", "ml-modeling"),
               (("director", 0.7), ("vp", 0.3))),
    RoleFamily("ai-eng-lead", "AI Engineering Lead",
               ("AI Engineering Lead", "Lead AI Engineer", "AI Engineering Manager"),
               ("technical-leadership", "python", "llm-integration", "mentoring", "system-design",
                "ai-evaluation", "mlops"),
               ("people-management", "hiring", "roadmap-planning", "rag", "agent-orchestration",
                "ci-cd", "testing"),
               (("staff", 0.35), ("lead", 0.4), ("manager", 0.25))),
    RoleFamily("backend", "Backend Engineer",
               ("Backend Engineer", "Senior Backend Engineer", "Backend Software Engineer"),
               ("python", "api-design", "distributed-systems", "sql", "testing", "system-design"),
               ("go", "java", "data-modeling", "backend-performance", "ci-cd", "observability",
                "aws", "typescript"),
               (("mid", 0.35), ("senior", 0.5), ("staff", 0.15))),
    RoleFamily("platform", "Platform Engineer",
               ("Platform Engineer", "Infrastructure Engineer", "Site Reliability Engineer"),
               ("kubernetes", "infrastructure-as-code", "ci-cd", "observability", "aws", "sre"),
               ("cloud-architecture", "security", "cost-optimization", "go", "python",
                "distributed-systems"),
               (("mid", 0.3), ("senior", 0.5), ("staff", 0.2))),
    RoleFamily("privacy-legal-ai", "Privacy / Legal AI Specialist",
               ("Privacy Engineer, AI", "Legal AI Specialist", "AI Governance Counsel-Engineer"),
               ("privacy-engineering", "regulatory-compliance", "ai-governance", "data-governance",
                "risk-assessment", "legal-reasoning"),
               ("contract-analysis", "ai-safety", "security", "stakeholder-management",
                "ai-evaluation", "python"),
               (("senior", 0.5), ("staff", 0.3), ("principal", 0.2))),
)
FAMILY_IDS: tuple[str, ...] = tuple(f.family_id for f in FAMILIES)
FAMILY_LABELS: dict[str, str] = {f.family_id: f.label for f in FAMILIES}

_ORG_A = ("Meridian", "Quartz", "Aurora", "Nebula", "Lattice", "Harbor", "Juniper", "Solstice",
          "Tessellate", "Cobalt", "Verdant", "Halcyon")
_ORG_B = ("Systems", "Labs", "Enterprises", "Software", "Dynamics", "Works", "Collective")
_ALIAS_FORMS = {"kubernetes": "k8s", "infrastructure-as-code": "Terraform", "ai-evaluation": "LLM evals",
                "rag": "retrieval augmented generation", "aws": "Amazon Web Services",
                "ml-modeling": "machine learning", "go": "golang", "sql": "PostgreSQL",
                "people-management": "team management", "sre": "site reliability"}
_OOV_TERMS = ("synergy-mindset", "fictional-tool-zeta", "blue-sky-thinking", "widget-wrangling")
_OPAQUE_REQUIREMENTS = (
    "Fictional clearance level Zeta required",
    "Must align with the Fictional Values Charter",
    "Travel as needed within the fictional region",
)


def prevalence_table(skill_names: tuple[str, ...]) -> dict[str, dict[str, float]]:
    """Synthetic P(skill is stated | family) used by the generator. Design input, not data."""
    table: dict[str, dict[str, float]] = {}
    for fam in FAMILIES:
        row = {s: _BACKGROUND_P for s in skill_names}
        for s in fam.secondary:
            row[s] = _SECONDARY_P
        for s in fam.core:
            row[s] = _CORE_P
        missing = [s for s in fam.core + fam.secondary if s not in row]
        if missing:
            raise InvalidInputError(f"family {fam.family_id} references unknown skills {missing}")
        table[fam.family_id] = row
    return table


def generate_postings(
    n: int,
    skill_names: tuple[str, ...],
    seed: int = 7,
    drift: Mapping[str, Any] | None = None,
    family_weights: Mapping[str, float] | None = None,
    window_days: int = 180,
) -> list[dict[str, Any]]:
    """Generate ``n`` fictional postings that validate against ``job-posting.schema.json``.

    ``drift`` (optional) makes one family's requirements change part-way through the window::

        {"family": "backend", "after_day": 90, "prevalence": {"llm-integration": 0.7}}

    Postings fetched on or after ``after_day`` use the overridden prevalences. Deterministic
    for a given ``(n, skill_names, seed, drift, family_weights, window_days)``.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise InvalidInputError("n must be a positive integer")
    rng = np.random.default_rng(seed)
    table = prevalence_table(skill_names)
    weights = np.array([float((family_weights or {}).get(f.family_id, 1.0)) for f in FAMILIES])
    if np.any(weights < 0) or weights.sum() <= 0:
        raise InvalidInputError("family_weights must be non-negative with a positive sum")
    weights = weights / weights.sum()
    jobs: list[dict[str, Any]] = []
    for i in range(n):
        fam = FAMILIES[int(rng.choice(len(FAMILIES), p=weights))]
        day = int(rng.integers(0, window_days))
        prev = dict(table[fam.family_id])
        if drift and drift.get("family") == fam.family_id and day >= int(drift.get("after_day", 0)):
            for s, p in dict(drift.get("prevalence", {})).items():
                if s not in prev:
                    raise InvalidInputError(f"drift references unknown skill {s!r}")
                prev[s] = float(p)
        draws = rng.random(len(skill_names))
        chosen = [s for s, u in zip(skill_names, draws) if u < prev[s]]
        while len(chosen) < 2:
            extra = fam.core[int(rng.integers(0, len(fam.core)))]
            if extra not in chosen:
                chosen.append(extra)
        raw_skills: list[str] = []
        for s in chosen:
            raw_skills.append(_ALIAS_FORMS[s] if s in _ALIAS_FORMS and rng.random() < 0.15 else s)
        if rng.random() < 0.06:
            raw_skills.append(_OOV_TERMS[int(rng.integers(0, len(_OOV_TERMS)))])
        core_stated = [s for s in fam.core if s in chosen]
        requirements: list[str] = []
        for s in core_stated[:2]:
            requirements.append(f"Minimum qualification: demonstrated experience with {s.replace('-', ' ')}")
        if rng.random() < 0.3:
            requirements.append(_OPAQUE_REQUIREMENTS[int(rng.integers(0, len(_OPAQUE_REQUIREMENTS)))])
        levels = [lv for lv, _ in fam.seniority]
        lw = np.array([w for _, w in fam.seniority])
        seniority = levels[int(rng.choice(len(levels), p=lw / lw.sum()))]
        if rng.random() < 0.05:
            seniority = "unspecified"
        compensation = None
        if rng.random() < 0.4:
            base = 100.0 + 40.0 * {"mid": 1, "senior": 2, "staff": 3, "lead": 3, "manager": 3,
                                   "principal": 4, "director": 5, "vp": 6}.get(seniority, 2)
            lo = round(base * (0.9 + 0.2 * float(rng.random())), 1)
            compensation = {"currency": "SYN", "min": lo, "max": round(lo * 1.25, 1),
                            "basis": "unknown", "verified": False}
        fetched = REFERENCE_DATE - timedelta(days=window_days - 1 - day)
        org = f"Fictional {_ORG_A[int(rng.integers(0, len(_ORG_A)))]} {_ORG_B[int(rng.integers(0, len(_ORG_B)))]}"
        job_id = f"synthetic-job-{seed}-{i:05d}"
        jobs.append({
            "id": job_id, "source": "synthetic", "source_id": f"{GENERATOR_VERSION}:{seed}:{i}",
            "title": fam.titles[int(rng.integers(0, len(fam.titles)))],
            "organization": org, "url": f"https://jobs.example.invalid/{job_id}",
            "fetched_at": fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": ("active", "active", "active", "expired", "unknown")[int(rng.integers(0, 5))],
            "skills": raw_skills, "seniority": seniority, "employment_type": "full-time",
            "location": None,
            "remote_policy": ("remote", "hybrid", "onsite", "unknown")[int(rng.integers(0, 4))],
            "compensation": compensation,
            "license_note": "Synthetic fixture. Fictional employer; not a real vacancy.",
            "role_family": fam.family_id, "requirements": requirements,
            "source_evidence": [f"synthetic-generator:{GENERATOR_VERSION}:seed={seed}"],
        })
    return jobs
