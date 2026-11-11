"""Artifact-exchange stages for lane E's integration runner (see ``integration-manifest.json``).

Every stage computes from its inputs. Interchange files are the *frozen* contract objects
(``jobs.json`` = JobPosting[], ``claims.json`` = ProfileClaim[]); lane A's richer records travel
in sibling files and are only trusted when they agree exactly with the frozen object.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ct_connectors import Capture, NativeAdapter
from ct_connectors.base import parse_ts
from ct_connectors.textsafe import FLAG_INJECTION, detect_injection

from . import corpus, schema, taxonomy
from .matching import rank
from .normalize import normalize, utc
from .retrieval import skill_vector
from .service import RequestError, Service, vault_from_profile
from .store import JobStore
from .vault import PROOF_WEIGHT, EvidenceVault, VaultError, _contradiction_dominates

E2E_VERSION = "a-e2e/0.1"
REBUILT_FLAG = "rebuilt-from-bare-posting"
PRODUCER_UNKNOWN_FLAG = "producer-status-unknown"


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RequestError("file-not-found", f"no such file: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RequestError("malformed-json", f"{path.name}: {exc}") from exc


def _sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.stem + suffix + path.suffix)


# ------------------------------------------------------------------ jobs.import
def stage_jobs(seed: int, count: int, out: Path) -> dict[str, Any]:
    if count < 80:
        raise RequestError("bad-parameter", "count must be >= 80 (every adversarial case needs a slot)")
    service = Service()
    report = service.import_doc(corpus.generate(seed, count))
    records = service.store.view(corpus.AS_OF)
    _write(out, [r["posting"] for r in records])
    _write(_sibling(out, "_ext"), records)
    _write(_sibling(out, "_import_report"), report)
    return {"stage": "jobs.import", "postings": len(records), "rejected": report["counts"]["rejected"]}


# --------------------------------------------------------------- profile.claims
def stage_claims(out: Path) -> dict[str, Any]:
    vault, _ = vault_from_profile(corpus.SYNTHETIC_PROFILE, corpus.AS_OF)
    exported = vault.export("internal-matching", corpus.AS_OF)
    _write(out, exported["claims"])
    _write(_sibling(out, "_vault"), {"export": exported, "vault": vault.to_dict()})
    return {"stage": "profile.claims", "claims": len(exported["claims"]), "withheld": exported["withheld"]}


# ---------------------------------------------------------------- shared loader
def record_from_posting(posting: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Re-normalize a bare frozen JobPosting through the native adapter (all sanitization applies)."""
    problems = schema.errors(posting, schema.load("job-posting"))
    if problems:
        return None, "schema_violation: " + "; ".join(problems[:2])
    note = posting.get("license_note", "")
    synthetic = posting["source"] == "synthetic" or any(w in (note + posting["organization"]).lower() for w in ("synthetic", "fictional"))
    capture = Capture(source=posting["source"], board="bare-posting", captured_at=posting["fetched_at"],
                      organization=posting["organization"], license_note=note, synthetic=synthetic)
    comp = posting.get("compensation")
    job = {"id": posting["source_id"], "title": posting["title"], "organization": posting["organization"], "url": posting["url"],
           "location": posting.get("location"),
           "remote": {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(posting.get("remote_policy", "unknown")),
           "employment_type": None if posting["employment_type"] == "unknown" else posting["employment_type"],
           "seniority": None if posting["seniority"] == "unknown" else posting["seniority"],
           "skills": posting["skills"],
           "description": "Requirements\n" + "\n".join("- " + r for r in posting.get("requirements", [])),
           # the frozen object carries no pay period, so a bare posting's pay can never be treated as annual
           "compensation": [{"min": comp["min"], "max": comp["max"], "currency": comp["currency"], "period": "unknown",
                             "basis": comp["basis"]}] if comp else [],
           "revoked": posting["status"] == "expired"}
    result = NativeAdapter(posting["source"]).parse({"postings": [job]}, capture)
    if not result.records:
        issue = result.rejected[0] if result.rejected else None
        return None, f"rejected-on-import: {issue.error_class if issue else 'unknown'}"
    env = normalize(result.records[0])
    env["posting"]["id"] = posting["id"]
    env["ext"]["quality_flags"] = sorted(set(env["ext"]["quality_flags"]) | {REBUILT_FLAG} |
                                         ({PRODUCER_UNKNOWN_FLAG} if posting["status"] == "unknown" else set()))
    env["ext"]["version"], env["ext"]["supersedes"] = 1, None
    return env, None


def _checked_ext(rec: dict[str, Any], posting: dict[str, Any]) -> dict[str, Any] | None:
    """Use a sidecar record only where it cannot override the frozen object; otherwise return None (rebuild)."""
    env = json.loads(json.dumps(rec))
    ext = env["ext"]
    try:
        if set(ext["skills_required"]) | set(ext["skills_preferred"]) != set(posting["skills"]):
            return None  # sidecar skills disagree with the frozen posting: do not let it inflate a match
        ext.pop("dedup", None)
        ext.pop("status_as_of", None)
        # The frozen status is authoritative: the sidecar may only make a posting *less* available.
        if posting["status"] == "expired" and not ext.get("revoked"):
            ext["revoked"], ext["revoked_at"] = "producer-status-expired", posting["fetched_at"]
        if posting["status"] == "unknown":
            ext["quality_flags"] = sorted(set(ext.get("quality_flags", [])) | {PRODUCER_UNKNOWN_FLAG})
        # Security flags are re-derived from the text, never taken on trust (they can only be added).
        text = "\n".join([posting["title"], posting["organization"], *posting.get("requirements", []),
                          str(ext.get("description_text", ""))])
        if detect_injection(text):
            ext["security_flags"] = sorted(set(ext.get("security_flags", [])) | {FLAG_INJECTION})
        if not isinstance(ext.get("security_flags"), list) or not isinstance(ext.get("quality_flags", []), list):
            return None
    except (KeyError, TypeError):
        return None
    return env


def load_jobs(path: Path) -> tuple[JobStore, list[dict[str, Any]], dict[str, int]]:
    """Build a throw-away store from jobs.json (+ jobs_ext.json when it agrees exactly)."""
    postings = _read(path)
    if not isinstance(postings, list):
        raise RequestError("bad-artifact", "jobs.json must be an array of JobPosting objects")
    ext_path = _sibling(path, "_ext")
    trusted: dict[str, dict[str, Any]] = {}
    if ext_path.exists():
        for rec in _read(ext_path):
            if isinstance(rec, dict) and isinstance(rec.get("posting"), dict) and isinstance(rec.get("ext"), dict):
                trusted[rec["posting"].get("id")] = rec
    store, refused, stats = JobStore(), [], {"from_ext": 0, "rebuilt": 0, "refused": 0}
    for posting in postings:
        if not isinstance(posting, dict) or not isinstance(posting.get("id"), str):
            refused.append({"job_id": "?", "reasons": ["rejected-on-import: malformed_record"]})
            continue
        if posting["id"] in store.versions:
            refused.append({"job_id": posting["id"], "reasons": ["rejected-on-import: duplicate_source_id"]})
            continue
        rec = trusted.get(posting["id"])
        env = _checked_ext(rec, posting) if rec is not None and rec["posting"] == posting else None
        if env is not None:
            stats["from_ext"] += 1
        else:
            env, why = record_from_posting(posting)
            if env is None:
                refused.append({"job_id": posting["id"], "reasons": [why]})
                continue
            stats["rebuilt"] += 1
        store.versions[posting["id"]] = [env]
    store._dedup_dirty = True
    stats["refused"] = len(refused)
    return store, refused, stats


def _default_as_of(store: JobStore) -> str:
    stamps = [utc(chain[-1]["posting"]["fetched_at"]) for chain in store.versions.values()]
    if not stamps:
        raise RequestError("bad-artifact", "jobs.json contains no usable posting")
    return max(stamps).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------- match.rank
class ClaimsProof:
    """Skill proof computed from frozen ProfileClaim objects alone (status as stated by the producer)."""

    def __init__(self, claims: list[dict[str, Any]], verified: bool = False) -> None:
        subjects = {c.get("subject_id") for c in claims if isinstance(c, dict)}
        if len(subjects) != 1:
            raise RequestError("bad-artifact", "claims.json must hold claims for exactly one subject")
        for claim in claims:
            problems = schema.errors(claim, schema.load("profile-claim"))
            if problems:
                raise RequestError("bad-artifact", f"claim {claim.get('id')!r}: " + "; ".join(problems[:2]))
        self.subject_id = subjects.pop()
        self.claims = claims
        # Without a verifiable vault, a caller-stated "supported" is not evidence: count it as self-reported.
        self.verified = verified

    def skill_proof(self, as_of: str) -> dict[str, dict[str, Any]]:
        moment = utc(as_of)
        out: dict[str, dict[str, Any]] = {}
        for claim in sorted(self.claims, key=lambda c: c["id"]):
            status = claim["status"]
            if status == "supported" and not claim["evidence_ids"]:
                status = "unverified"  # defensive: a supported claim must point at evidence
            elif status == "supported" and not self.verified:
                status = "self-reported"
            expired = claim.get("valid_to") is not None and parse_ts(claim["valid_to"]) is not None and parse_ts(claim["valid_to"]) <= moment
            for raw in claim.get("skills") or []:
                skill = taxonomy.canonical_skill(raw)
                if skill is None:
                    continue
                slot = out.setdefault(skill, {"status": None, "weight": 0.0, "claim_ids": [], "contradicted_claim_ids": [],
                                              "expired_claim_ids": []})
                if status == "contradicted":
                    slot["contradicted_claim_ids"].append(claim["id"])
                elif expired:
                    slot["expired_claim_ids"].append(claim["id"])
                else:
                    slot["claim_ids"].append(claim["id"])
                    if slot["status"] is None or PROOF_WEIGHT[status] > slot["weight"]:
                        slot["status"], slot["weight"] = status, PROOF_WEIGHT[status]
        return _contradiction_dominates(out)


def _claims_verified(claims: list[dict[str, Any]], vault_path: Path) -> bool:
    """True only if a ledger-verified vault re-exports exactly these claims (status derived, not asserted)."""
    if not vault_path.exists():
        return False
    try:
        doc = _read(vault_path)
        vault = EvidenceVault.from_dict(doc["vault"])
        return vault.export("internal-matching", doc["export"]["as_of"])["claims"] == claims
    except (RequestError, VaultError, KeyError, TypeError, ValueError):
        return False


def stage_rank(jobs_path: Path, claims_path: Path, out: Path, as_of: str | None = None,
               preferences: dict[str, Any] | None = None, top_n: int = 500) -> dict[str, Any]:
    store, import_refusals, stats = load_jobs(jobs_path)
    claims = _read(claims_path)
    if not isinstance(claims, list):
        raise RequestError("bad-artifact", "claims.json must be an array of ProfileClaim objects")
    proof = ClaimsProof(claims, verified=_claims_verified(claims, _sibling(claims_path, "_vault")))
    moment = as_of or _default_as_of(store)
    prefs = corpus.SYNTHETIC_PROFILE["preferences"] if preferences is None else preferences
    result = rank(store.view(moment), proof, prefs, moment, top_n=top_n)  # type: ignore[arg-type]
    result["refusals"] = sorted(result["refusals"] + import_refusals, key=lambda r: r["job_id"])
    result["corpus"]["refused"] += len(import_refusals)
    result["corpus"]["considered"] += len(import_refusals)
    if import_refusals:
        result["corpus"]["refusal_reasons"]["rejected-on-import"] = len(import_refusals)
    result["e2e"] = {"version": E2E_VERSION, "as_of_source": "argument" if as_of else "max(fetched_at) in jobs.json",
                     "records": stats, "claims_subject": proof.subject_id,
                     "claims_status_source": "derived-by-verified-vault" if proof.verified
                     else "producer-asserted; 'supported' counted as self-reported"}
    _write(out, result["shortlist"])
    _write(_sibling(out, "_full"), result)
    return {"stage": "match.rank", "ranked": len(result["shortlist"]), "refused": result["corpus"]["refused"], "as_of": moment}


# ------------------------------------------------------------- features.vectors
def stage_vectors(jobs_path: Path, out: Path) -> dict[str, Any]:
    store, refused, stats = load_jobs(jobs_path)
    version = taxonomy.taxonomy_version()
    vectors = []
    for jid in sorted(store.versions):
        ext = store.versions[jid][-1]["ext"]
        vec = skill_vector(ext["skills_required"], ext["skills_preferred"])
        posting = store.versions[jid][-1]["posting"]
        vectors.append({"ref": f"a-skillvec/{version}/{jid}", "entity_id": jid, "values": vec["values"], "observed": vec["observed"],
                        "fetched_at": posting["fetched_at"], "seniority": posting["seniority"], "synthetic": ext["synthetic"]})
    doc = {"version": E2E_VERSION, "vocabulary": list(taxonomy.SKILL_IDS), "vocabulary_version": version,
           "encoding": "1=required,0.5=preferred,0=not-listed; observed=false means zeros are unknown",
           "vectors": vectors, "refused": refused, "records": stats,
           "warnings": ["Representation for visualization only; distances are NOT fit scores or hiring likelihoods."]}
    _write(out, doc)
    return {"stage": "features.vectors", "vectors": len(vectors), "refused": len(refused)}
