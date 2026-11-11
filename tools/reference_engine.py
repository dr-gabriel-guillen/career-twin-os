"""Reference-only, synthetic, auditable heuristics. NOT hiring probabilities."""
from __future__ import annotations
import hashlib
from typing import Any


def score_job(profile: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Weighted 0..100 score; never interpret as a hiring probability."""
    user_skills = {s.lower().strip() for s in profile["skills"]}
    job_skills = {s.lower().strip() for s in job["skills"]}
    overlap = len(user_skills & job_skills) / len(job_skills) if job_skills else 0.0
    trajectory = max(0.0, min(1.0, float(job.get("target_trajectory", 0))))
    workstyle = (1.0 if job["remote_policy"] == profile["preferences"]["remote"]
                 else (0.5 if job["remote_policy"] == "hybrid" else 0.1))
    weights = {"skills": 0.42, "trajectory": 0.45, "workstyle": 0.13}
    score = 100 * sum((weights["skills"] * overlap, weights["trajectory"] * trajectory,
                       weights["workstyle"] * workstyle))
    return {"job_id": job["id"], "score": round(score, 1),
            "score_type": "relative_heuristic_not_hiring_probability",
            "explanation": {"skill_coverage": round(overlap, 3),
                            "trajectory_score": trajectory, "workstyle": workstyle},
            "missing_skills": sorted(job_skills - user_skills),
            "unknown_inputs": ["compensation_verified", "recruiter_preferences", "job_activity_status"]}


def assign_variant(experiment_id: str, stratum: str, unit_id: str,
                   variants: list[str], salt: str) -> str:
    """Deterministic pseudo-random strata assignment; persist assignment in production."""
    if len(set(variants)) < 2 or len(variants) != len(set(variants)) or not unit_id or not experiment_id or not salt:
        raise ValueError("Need a valid preregistered experiment, >=2 unique variants, unit and salt")
    key = "\u241f".join((salt, experiment_id, stratum, unit_id)).encode("utf-8")
    n = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    return variants[n % len(variants)]


def skill_gap(profile: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    have = set(profile["skills"])
    wanted = set(job["skills"])
    return {"target_role": job["title"], "missing_skills": sorted(wanted - have),
            "already_evidenced": sorted(wanted & have),
            "status": "hypothetical_gap_not_probability"}
