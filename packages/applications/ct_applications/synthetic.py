"""Explicitly fictional fixtures. No real person, employer, posting or résumé."""
from __future__ import annotations

from typing import Any

SUBJECT = "candidate-synthetic-001"
T0 = "2026-01-05T09:00:00Z"


def claim(cid: str, text: str, status: str = "supported", skills: list[str] | None = None,
          evidence: list[str] | None = None, visibility: str = "shareable",
          valid_to: str | None = None, subject: str = SUBJECT) -> dict[str, Any]:
    if evidence is None:
        evidence = [f"evidence-synthetic-{cid}"] if status == "supported" else []
    return {"id": cid, "subject_id": subject, "claim_type": "achievement", "claim_text": text,
            "status": status, "evidence_ids": evidence, "visibility": visibility,
            "updated_at": T0, "skills": skills or [], "valid_to": valid_to}


def claims() -> list[dict[str, Any]]:
    return [
        claim("claim-api", "Designed a fictional partner API used by 3 synthetic teams", skills=["api-design", "python"]),
        claim("claim-eval", "Built a synthetic evaluation harness for model regressions", skills=["ai-evaluation", "python"]),
        claim("claim-aws", "Operated fictional workloads on AWS with infrastructure as code", skills=["aws"]),
        claim("claim-ts", "Shipped a TypeScript dashboard for a fictional client", status="self-reported", skills=["typescript"]),
        claim("claim-sec", "Led a synthetic threat-modelling exercise", skills=["security"]),
        claim("claim-mlops", "Ran an MLOps platform at planetary scale", status="unverified", skills=["mlops"]),
        claim("claim-sales", "Closed enterprise deals worth millions", status="contradicted", skills=["enterprise-sales"]),
        claim("claim-private", "Confidential fictional project", skills=["distributed-systems"], visibility="private"),
        claim("claim-old", "Held a fictional certification", skills=["cloud-architecture"], valid_to="2025-01-01T00:00:00Z"),
        claim("claim-noev", "Claims support but has no evidence attached", skills=["customer-delivery"], evidence=[]),
    ]


def job(jid: str = "fake-fde-1", org: str = "Fictional Meridian Systems",
        skills: list[str] | None = None, seniority: str = "senior") -> dict[str, Any]:
    """A JobPosting v0.1-shaped fictional posting."""
    return {"id": jid, "source": "synthetic", "source_id": jid, "title": "Forward Deployed Engineer",
            "organization": org, "url": f"https://example.invalid/jobs/{jid}", "fetched_at": T0,
            "status": "active", "skills": skills if skills is not None else
            ["python", "typescript", "api-design", "ai-evaluation", "customer-delivery"],
            "seniority": seniority, "employment_type": "full-time", "remote_policy": "remote",
            "role_family": "fde"}
