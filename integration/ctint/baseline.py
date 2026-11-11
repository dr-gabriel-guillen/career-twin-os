"""The shared bootstrap baseline on ``main``: reference engine and fixtures.

Baseline code is owned by no lane. Lane E imports it read-only, characterises
its behaviour, and records every defect in ``known_findings.json``. The
characterisation is exact: a finding that appears or disappears fails a test,
so the ledger can never drift from reality.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType
from typing import Any

from . import contracts
from .paths import integration_dir, repo_root


def load_reference_engine(root: Path | None = None) -> ModuleType:
    path = (root or repo_root()) / "tools" / "reference_engine.py"
    spec = importlib.util.spec_from_file_location("ct_reference_engine", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load reference engine from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_job() -> dict[str, Any]:
    return {
        "id": "job-syn-000001", "source": "synthetic", "source_id": "syn:1", "title": "Engineer",
        "organization": "Fictional Meridian Systems", "url": "https://jobs.fictional-meridian.example/1",
        "fetched_at": "2026-01-05T09:00:00Z", "status": "active", "skills": ["python", "api-design"],
        "seniority": "senior", "employment_type": "full-time",
    }  # fmt: skip


def characterize_baseline(root: Path | None = None) -> dict[str, str]:
    """Observed baseline defects as ``{finding key: one-line evidence}``. No assertions here."""
    base = root or repo_root()
    engine = load_reference_engine(base)
    found: dict[str, str] = {}
    profile = {"skills": ["Python", "api-design"], "preferences": {"remote": "remote"}}

    jobs = json.loads((base / "fixtures" / "synthetic_jobs.json").read_text(encoding="utf-8"))
    invalid = [j.get("id") for j in jobs if contracts.schema_errors("job-posting", j, base)]
    if invalid:
        found["REF-FIXTURES-NOT-CONTRACT-VALID"] = (
            f"{len(invalid)}/{len(jobs)} records in fixtures/synthetic_jobs.json fail job-posting.schema.json"
        )
    fixture_profile = json.loads((base / "fixtures" / "synthetic_profile.json").read_text(encoding="utf-8"))
    if contracts.schema_errors("profile-claim", fixture_profile, base):
        found["REF-NO-PROFILE-CLAIM-FIXTURE"] = (
            "fixtures/synthetic_profile.json is a profile summary; main ships no ProfileClaim fixture"
        )

    job = _valid_job()
    if not contracts.schema_errors("job-posting", job, base):
        try:
            engine.score_job(profile, job)
        except KeyError as exc:
            found["REF-SCORE-KEYERROR-OPTIONAL-FIELD"] = (
                f"score_job raises KeyError({exc}) on a contract-valid posting without remote_policy"
            )
    with_policy = dict(job, remote_policy="remote")
    low = engine.score_job(profile, dict(with_policy, target_trajectory=0.0))["score"]
    high = engine.score_job(profile, dict(with_policy, target_trajectory=1.0))["score"]
    if high != low and contracts.schema_errors("job-posting", dict(with_policy, target_trajectory=1.0), base):
        found["REF-SCORE-USES-NONCONTRACT-FIELD"] = (
            f"45% of the score comes from target_trajectory ({low} -> {high}), a field the contract rejects"
        )
    nan_score = engine.score_job(profile, dict(with_policy, target_trajectory=math.nan))
    if nan_score["score"] == high:
        found["REF-SCORE-NAN-TRAJECTORY-MAXED"] = (
            "target_trajectory=NaN (accepted by json.loads) is scored exactly like the maximum 1.0"
        )
    scored = engine.score_job(profile, with_policy)
    gap = engine.skill_gap(profile, with_policy)
    if "python" not in scored["missing_skills"] and "python" in gap["missing_skills"]:
        found["REF-GAP-CASE-INCONSISTENT"] = (
            "profile skill 'Python' counts as covered in score_job but as missing in skill_gap"
        )
    from .experiment_integrity import probe_assignment_function  # local import: avoids a module cycle

    for finding in probe_assignment_function(engine.assign_variant, 1200):
        found.setdefault(f"REF-{finding.code}", finding.detail)
    return found


def load_ledger() -> dict[str, Any]:
    data = json.loads((integration_dir() / "known_findings.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("known_findings.json must be an object")
    return data


def ledger_evidence_keys(prefix: str) -> set[str]:
    return {key for item in load_ledger()["findings"] for key in item["evidence"] if key.startswith(prefix)}
