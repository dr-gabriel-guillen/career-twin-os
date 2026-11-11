"""Regenerate lane A's own JSON Schemas and re-vendor the frozen contracts.

Run: ``python packages/data-matching/tools/build_schemas.py``. The frozen contracts are
copied byte-for-byte from ``contracts/``; a test fails if the vendored copy drifts.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
sys.path[:0] = [str(PKG / "src"), str(REPO / "connectors")]

from ct_matching import taxonomy  # noqa: E402

OUT = PKG / "src" / "ct_matching" / "schemas"
STR = {"type": "string"}
NSTR = {"type": ["string", "null"]}
BOOL = {"type": "boolean"}
NUM01 = {"type": ["number", "null"], "minimum": 0, "maximum": 1}
PCT = {"type": ["number", "null"], "minimum": 0, "maximum": 100}
STRS = {"type": "array", "items": STR}
LADDER = {"type": "string", "enum": list(taxonomy.SENIORITY_LADDER)}
SIGNALS = ["skill_coverage", "proof_strength", "seniority_alignment", "workstyle", "transition_utility",
           "customer_proximity", "enterprise_delivery"]


def obj(required: list[str], props: dict, extra: bool = False) -> dict:
    return {"type": "object", "additionalProperties": extra, "required": required, "properties": props}


PREFERENCES = obj([], {
    "target_roles": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 120}},
    "target_families": {"type": "array", "maxItems": 20, "items": {"type": "string", "enum": sorted(taxonomy.FAMILIES)}},
    "remote": {"type": "array", "maxItems": 3, "uniqueItems": True, "items": {"type": "string", "enum": ["remote", "hybrid", "onsite"]}},
    "jurisdictions": {"type": "array", "maxItems": 50, "items": {"type": "string", "pattern": "^[A-Z]{2}$"}},
    "employment_types": {"type": "array", "items": {"type": "string", "enum": ["full-time", "part-time", "contract", "internship", "temporary"]}},
    "seniority_min": LADDER, "seniority_max": LADDER, "seniority_target": LADDER,
    "compensation_floor": obj(["currency", "amount"], {"currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                                                       "amount": {"type": "number", "minimum": 0, "maximum": 1e9}}),
    "require_known_compensation": BOOL, "include_unknown_status": BOOL, "exclude_phantom_risk": BOOL,
    "allow_flagged_content": BOOL,
    "excluded_employers": {"type": "array", "maxItems": 200, "items": {"type": "string", "maxLength": 200}},
    "weights": obj([], {name: {"type": "number", "minimum": 0, "maximum": 1000} for name in SIGNALS}),
    "strategy_weight": {"type": "number", "minimum": 0, "maximum": 1},
})
PREFERENCES.update({"$id": "https://careertwin.local/lane-a/a-preferences.v0.1.schema.json", "title": "LaneAPreferences"})

EXT = obj(["ext_version", "synthetic", "board", "employer", "timestamps", "status_reason", "location", "compensation",
           "skills_required", "skills_preferred", "cues", "security_flags", "quality_flags", "content_hash",
           "version", "dedup", "taxonomy_version"], {
    "ext_version": {"type": "string", "const": "a-ext/0.1"},
    "synthetic": BOOL, "board": STR,
    "employer": obj(["name", "key", "org_uid", "alias_ambiguous"], {"name": STR, "key": STR, "org_uid": NSTR, "alias_ambiguous": BOOL}),
    "timestamps": obj(["captured_at", "posted_at", "updated_at", "expires_at"],
                      {k: {"type": ["string", "null"], "format": "date-time"} for k in ("captured_at", "posted_at", "updated_at", "expires_at")}),
    "status_reason": STR, "status_as_of": {"type": "string", "format": "date-time"},
    "listed": {"type": ["boolean", "null"]}, "revoked": NSTR, "revoked_at": {"type": "string", "format": "date-time"},
    "apply_url": NSTR, "department": NSTR,
    "location": obj(["raw", "parts", "jurisdictions", "jurisdiction_basis", "multi"], {
        "raw": NSTR, "parts": {"type": "array", "items": obj(["city", "region", "country"], {"city": NSTR, "region": NSTR, "country": NSTR})},
        "jurisdictions": {"type": "array", "items": {"type": "string", "pattern": "^[A-Z]{2}$"}},
        "jurisdiction_basis": NSTR, "multi": BOOL, "notes": STRS}),
    "compensation": obj(["state", "period", "origin", "stated"], {
        "state": {"type": "string", "enum": ["stated", "absent", "conflict:inverted-range", "conflict:multiple-ranges",
                                             "conflict:structured-vs-text", "multiple-ranges", "unparseable:zero",
                                             "ambiguous-currency", "period-unknown", "non-annual"]},
        "period": NSTR, "origin": NSTR,
        "stated": {"type": "array", "items": obj(["min", "max", "currency", "period", "basis", "origin"], {
            "min": {"type": ["number", "null"]}, "max": {"type": ["number", "null"]}, "currency": NSTR,
            "period": {"type": "string", "enum": ["year", "hour", "month", "week", "day", "unknown"]},
            "basis": {"type": "string", "enum": ["base", "total", "unknown"]},
            "origin": {"type": "string", "enum": ["structured-field", "text-parsed"]}})}}),
    "skills_required": STRS, "skills_preferred": STRS, "skills_unmapped": STRS,
    "cues": obj(["customer", "enterprise"], {"customer": {"type": "integer", "minimum": 0}, "enterprise": {"type": "integer", "minimum": 0}}),
    "security_flags": STRS, "quality_flags": STRS, "description_text": STR,
    "taxonomy_version": STR, "payload_sha256": STR, "content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "version": {"type": "integer", "minimum": 1}, "supersedes": NSTR,
    "dedup": obj(["cluster_id", "canonical_id", "is_canonical", "members", "duplicate_of", "repost_within_board"], {
        "cluster_id": STR, "canonical_id": STR, "is_canonical": BOOL, "members": STRS, "duplicate_of": NSTR,
        "repost_within_board": BOOL}),
})
JOB_RECORD = obj(["posting", "ext"], {"posting": {"type": "object", "description": "MUST validate against contracts/job-posting.schema.json (frozen v0.1)"},
                                      "ext": EXT})
JOB_RECORD.update({"$id": "https://careertwin.local/lane-a/a-job-record.v0.1.schema.json", "title": "LaneAJobRecord"})

CONSTRAINT = obj(["constraint", "outcome", "detail"], {"constraint": STR, "outcome": {"type": "string", "enum": ["pass", "fail", "unknown"]}, "detail": STR})
COMPONENT = obj(["group", "value", "configured_weight", "effective_weight", "contribution", "basis"], {
    "group": {"type": "string", "enum": ["near_term_fit", "strategic_utility"]}, "value": NUM01,
    "configured_weight": {"type": "number", "minimum": 0}, "effective_weight": {"type": "number", "minimum": 0, "maximum": 1},
    "contribution": PCT, "basis": STR})
EXPLANATION = obj(["job_id", "rank", "score", "score_type", "score_interval", "title", "organization", "role_family",
                   "composites", "components", "matched_skills", "missing_skills", "missing_preferred_skills",
                   "unsupported_evidence", "constraints", "unknown_inputs", "availability", "compensation",
                   "provenance", "cautions", "sensitivity", "counterfactuals"], {
    "job_id": STR, "rank": {"type": "integer", "minimum": 1}, "score": {"type": "number", "minimum": 0, "maximum": 100},
    "score_type": {"type": "string", "const": "relative_heuristic_not_offer_probability"},
    "score_interval": obj(["low", "high", "meaning"], {"low": PCT, "high": PCT, "meaning": STR}),
    "title": STR, "organization": STR, "role_family": STR,
    "composites": obj(["near_term_fit", "strategic_utility", "strategy_weight", "rank_by_near_term_fit", "rank_by_strategic_utility"], {
        "near_term_fit": PCT, "strategic_utility": PCT, "strategy_weight": {"type": "number", "minimum": 0, "maximum": 1},
        "rank_by_near_term_fit": {"type": ["integer", "null"]}, "rank_by_strategic_utility": {"type": ["integer", "null"]}}),
    "components": obj(SIGNALS, {name: COMPONENT for name in SIGNALS}),
    "matched_skills": {"type": "array", "items": obj(["skill", "requirement", "proof_status", "claim_ids"], {
        "skill": STR, "requirement": {"type": "string", "enum": ["required", "preferred"]},
        "proof_status": {"type": "string", "enum": ["supported", "self-reported", "unverified"]}, "claim_ids": STRS})},
    "missing_skills": STRS, "missing_preferred_skills": STRS,
    "unsupported_evidence": {"type": "array", "items": obj(["skill", "reason"], {"skill": STR, "reason": STR})},
    "constraints": {"type": "array", "items": CONSTRAINT}, "unknown_inputs": STRS,
    "availability": obj(["status", "reason", "as_of", "fetched_at"], {
        "status": {"type": "string", "enum": ["active", "unknown"]}, "reason": STR,
        "as_of": {"type": "string", "format": "date-time"}, "fetched_at": {"type": "string", "format": "date-time"}}),
    "compensation": obj(["stated", "state", "note"], {"stated": {"type": ["object", "null"]}, "state": STR, "note": STR}),
    "provenance": obj(["source", "source_id", "url", "synthetic", "content_hash", "version", "duplicates_collapsed"], {
        "source": STR, "source_id": STR, "url": STR, "synthetic": BOOL, "content_hash": STR,
        "version": {"type": "integer", "minimum": 1}, "duplicates_collapsed": STRS}),
    "cautions": STRS,
    "sensitivity": obj(["rank_best", "rank_worst", "method"], {"rank_best": {"type": "integer", "minimum": 1},
                                                              "rank_worst": {"type": "integer", "minimum": 1}, "method": STR}),
    "counterfactuals": {"type": "array", "items": obj(["kind", "change", "score", "score_delta", "rank", "status"], {
        "kind": {"type": "string", "enum": ["skill-acquired", "evidence-added"]}, "change": STR, "score": PCT,
        "score_delta": {"type": "number"}, "rank": {"type": "integer", "minimum": 1}, "status": STR})},
})
MATCH_RESULT = obj(["api_version", "score_type", "as_of", "subject_id", "taxonomy_version", "policy", "corpus",
                    "shortlist", "refusals", "strategy_comparison", "disclaimers", "result_hash"], {
    "api_version": {"type": "string", "const": "a-match/0.1"},
    "score_type": {"type": "string", "const": "relative_heuristic_not_offer_probability"},
    "as_of": {"type": "string", "format": "date-time"}, "subject_id": STR, "taxonomy_version": STR,
    "policy": {"type": "object"},
    "corpus": obj(["considered", "eligible", "refused", "refusal_reasons"], {
        "considered": {"type": "integer", "minimum": 0}, "eligible": {"type": "integer", "minimum": 0},
        "refused": {"type": "integer", "minimum": 0}, "refusal_reasons": {"type": "object"}}),
    "shortlist": {"type": "array", "items": EXPLANATION},
    "refusals": {"type": "array", "items": obj(["job_id", "reasons"], {"job_id": STR, "reasons": {"type": "array", "minItems": 1, "items": STR}})},
    "strategy_comparison": {"type": "object"}, "disclaimers": {"type": "array", "minItems": 1, "items": STR},
    "result_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
})
MATCH_RESULT.update({"$id": "https://careertwin.local/lane-a/a-match-result.v0.1.schema.json", "title": "LaneAMatchResult"})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("job-posting", "profile-claim"):
        shutil.copyfile(REPO / "contracts" / f"{name}.schema.json", OUT / f"{name}.schema.json")
    for name, doc in (("a-preferences", PREFERENCES), ("a-job-record", JOB_RECORD), ("a-match-result", MATCH_RESULT)):
        doc = {"$schema": "https://json-schema.org/draft/2020-12/schema", **doc}
        (OUT / f"{name}.schema.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("wrote", sorted(p.name for p in OUT.glob("*.json")))


if __name__ == "__main__":
    main()
