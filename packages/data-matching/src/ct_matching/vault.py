"""Profile Evidence Vault: claims, evidence pointers, derived support state, export gates.

Invariants enforced here (and tested):
* A claim's ``status`` is *derived* from evidence; callers cannot set it.
* Résumé prose only ever yields ``self-reported`` claims. Credentials, education and
  employment become ``supported`` only with issuer/document evidence carrying a hash.
* The vault stores pointers and hashes, never document contents or prose.
* ``public-approved`` visibility needs an explicit approval reference (manual gate).
"""
from __future__ import annotations

import re
from typing import Any

from ct_connectors.base import canonical_json, parse_ts, sha256_hex
from ct_connectors.textsafe import clean_text, fold

from . import schema, taxonomy
from .store import Ledger

VAULT_FORMAT = "a-vault/0.1"
EVIDENCE_KINDS = ("issuer-verification", "document-hash", "work-sample", "third-party-attestation",
                  "self-statement", "resume-prose")
VERIFYING_KINDS = frozenset({"issuer-verification", "document-hash", "work-sample", "third-party-attestation"})
#: Claim types where a work sample or peer attestation is not enough to call the claim supported.
STRICT_TYPES = frozenset({"credential", "education", "employment"})
STRICT_KINDS = frozenset({"issuer-verification", "document-hash"})
EXPORT_MODES = ("internal-matching", "resume-draft", "public")
#: Disclosed policy weights: how much a claim's support state counts as proof. Not empirical.
PROOF_WEIGHT = {"supported": 1.0, "self-reported": 0.5, "unverified": 0.25, "contradicted": 0.0}
_CREDENTIAL_CUE = re.compile(r"\b(certified|certification|certificate|licensed|license[d]?\b|degree|ph\.?d|m\.?sc|"
                             r"b\.?sc|mba|bachelor|master'?s|doctorate|accredited|bar admission)\b", re.I)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class VaultError(ValueError):
    """A refused vault operation; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class EvidenceVault:
    def __init__(self, subject_id: str) -> None:
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise VaultError("bad-subject", "subject_id must be a non-empty string")
        self.subject_id = subject_id
        self.claims: dict[str, dict[str, Any]] = {}
        self.meta: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.ledger = Ledger()

    # ---------------------------------------------------------------- writes
    def add_evidence(self, kind: str, pointer: str, at: str, *, sha256: str | None = None, note: str = "") -> str:
        if kind not in EVIDENCE_KINDS:
            raise VaultError("bad-evidence-kind", f"{kind!r} not in {EVIDENCE_KINDS}")
        if not isinstance(pointer, str) or not re.fullmatch(r"vault://[A-Za-z0-9._~/-]{1,200}", pointer):
            raise VaultError("bad-pointer", "evidence pointer must be an opaque vault:// locator (no URLs, no content)")
        if sha256 is not None and not (isinstance(sha256, str) and _SHA256.fullmatch(sha256)):
            raise VaultError("bad-hash", "sha256 must be 64 lowercase hex characters")
        stamp = _stamp(at)
        eid = "ev_" + sha256_hex(canonical_json([self.subject_id, kind, pointer, sha256]))[:16]
        if eid not in self.evidence:
            self.evidence[eid] = {"id": eid, "kind": kind, "pointer": pointer, "sha256": sha256, "added_at": stamp,
                                  "note": clean_text(note, limit=200)[0], "revoked": False}
            self.ledger.append(stamp, "evidence-added", eid, {"kind": kind, "pointer": pointer, "sha256": sha256})
        return eid

    def revoke_evidence(self, evidence_id: str, at: str, reason: str) -> None:
        item = self._evidence(evidence_id)
        if not item["revoked"]:
            item["revoked"] = True
            self.ledger.append(_stamp(at), "evidence-revoked", evidence_id, {"reason": clean_text(reason, limit=200)[0]})
            for cid, claim in self.claims.items():
                if evidence_id in claim["evidence_ids"]:
                    self._rederive(cid, _stamp(at))

    def add_claim(self, claim_type: str, claim_text: str, at: str, *, skills: list[str] | None = None,
                  evidence_ids: list[str] | None = None, valid_to: str | None = None, origin: str = "manual") -> str:
        text, _ = clean_text(claim_text, limit=500)
        text = re.sub(r"\s+", " ", text)
        if not text:
            raise VaultError("empty-claim", "claim_text is empty after sanitization")
        if claim_type not in schema.load("profile-claim")["properties"]["claim_type"]["enum"]:
            raise VaultError("bad-claim-type", f"{claim_type!r} is not a contract claim_type")
        stamp = _stamp(at)
        if valid_to is not None:
            valid_to = _stamp(valid_to)
        mapped = sorted({s for s in (taxonomy.canonical_skill(x) for x in (skills or []) if isinstance(x, str)) if s})
        ev = sorted(set(evidence_ids or []))
        for eid in ev:
            self._evidence(eid)
        cid = "cl_" + sha256_hex(canonical_json([self.subject_id, claim_type, fold(text)]))[:16]
        if cid in self.claims:
            claim = self.claims[cid]
            claim["evidence_ids"] = sorted(set(claim["evidence_ids"]) | set(ev))
            claim["skills"] = sorted(set(claim["skills"]) | set(mapped))
            claim["updated_at"] = max(claim["updated_at"], stamp)
            self.ledger.append(stamp, "claim-merged", cid, {"evidence_ids": ev})
        else:
            self.claims[cid] = {"id": cid, "subject_id": self.subject_id, "claim_type": claim_type, "claim_text": text,
                                "status": "unverified", "evidence_ids": ev, "visibility": "private",
                                "updated_at": stamp, "skills": mapped, "valid_to": valid_to}
            self.meta[cid] = {"origin": origin, "conflicts": [], "approval_ref": None}
            self.ledger.append(stamp, "claim-added", cid, {"claim_type": claim_type, "origin": origin, "evidence_ids": ev})
        self._rederive(cid, stamp)
        return cid

    def attach_evidence(self, claim_id: str, evidence_id: str, at: str) -> str:
        claim = self._claim(claim_id)
        self._evidence(evidence_id)
        if evidence_id not in claim["evidence_ids"]:
            claim["evidence_ids"] = sorted(claim["evidence_ids"] + [evidence_id])
            self.ledger.append(_stamp(at), "evidence-attached", claim_id, {"evidence_id": evidence_id})
        return self._rederive(claim_id, _stamp(at))

    def ingest_resume_prose(self, prose: str, at: str) -> list[str]:
        """Turn free-text résumé prose into claims. Everything is ``self-reported``; the prose is not stored."""
        text, _ = clean_text(prose, limit=20000)
        if not text:
            return []
        stamp = _stamp(at)
        eid = self.add_evidence("resume-prose", "vault://prose/" + sha256_hex(text)[:32], stamp, sha256=sha256_hex(text))
        out: list[str] = []
        for skill in taxonomy.find_skills(text):
            out.append(self.add_claim("skill", f"Skill: {skill}", stamp, skills=[skill], evidence_ids=[eid],
                                      origin="resume-prose"))
        for line in text.split("\n"):
            line = line.strip(" -•*\t")
            if 8 <= len(line) <= 300 and _CREDENTIAL_CUE.search(line):
                out.append(self.add_claim("credential", line, stamp, evidence_ids=[eid], origin="resume-prose"))
        return sorted(set(out))

    def register_conflict(self, claim_id: str, at: str, reason: str, other_claim_id: str | None = None) -> None:
        """Record that a claim is contradicted (by another claim or by external counter-evidence)."""
        self._claim(claim_id)
        note = clean_text(reason, limit=200)[0]
        if not note:
            raise VaultError("empty-reason", "a conflict needs a reason")
        targets = [claim_id] + ([other_claim_id] if other_claim_id else [])
        for cid in targets:
            self._claim(cid)
        for cid in targets:
            other = next((t for t in targets if t != cid), None)
            self.meta[cid]["conflicts"].append({"with": other, "reason": note, "resolved": False})
            self.ledger.append(_stamp(at), "conflict-registered", cid, {"with": other, "reason": note})
            self._rederive(cid, _stamp(at))

    def resolve_conflicts(self, claim_id: str, at: str, resolution: str) -> str:
        self._claim(claim_id)
        note = clean_text(resolution, limit=200)[0]
        if not note:
            raise VaultError("empty-reason", "a resolution needs an explanation")
        for conflict in self.meta[claim_id]["conflicts"]:
            conflict["resolved"] = True
        self.ledger.append(_stamp(at), "conflicts-resolved", claim_id, {"resolution": note})
        return self._rederive(claim_id, _stamp(at))

    def set_visibility(self, claim_id: str, visibility: str, at: str, *, approval_ref: str | None = None) -> None:
        claim = self._claim(claim_id)
        if visibility not in ("private", "shareable", "public-approved"):
            raise VaultError("bad-visibility", visibility)
        if visibility == "public-approved":
            if not (isinstance(approval_ref, str) and approval_ref.strip()):
                raise VaultError("approval-required", "public-approved visibility needs an explicit user approval reference")
            if claim["status"] != "supported":
                raise VaultError("not-supported", f"only supported claims can be made public (status={claim['status']})")
            self.meta[claim_id]["approval_ref"] = approval_ref.strip()[:100]
        else:
            self.meta[claim_id]["approval_ref"] = None
        claim["visibility"] = visibility
        claim["updated_at"] = max(claim["updated_at"], _stamp(at))
        self.ledger.append(_stamp(at), "visibility-set", claim_id, {"visibility": visibility,
                                                                    "approval_ref": self.meta[claim_id]["approval_ref"]})

    # ------------------------------------------------------------ derivation
    def _derive(self, claim_id: str) -> str:
        claim, meta = self.claims[claim_id], self.meta[claim_id]
        if any(not c["resolved"] for c in meta["conflicts"]):
            return "contradicted"
        live = [self.evidence[e] for e in claim["evidence_ids"] if not self.evidence[e]["revoked"]]
        needed = STRICT_KINDS if claim["claim_type"] in STRICT_TYPES else VERIFYING_KINDS
        if any(e["kind"] in needed and e["sha256"] for e in live):
            return "supported"
        if any(e["kind"] in ("self-statement", "resume-prose") for e in live):
            return "self-reported"
        return "unverified"

    def _rederive(self, claim_id: str, at: str) -> str:
        claim = self.claims[claim_id]
        new = self._derive(claim_id)
        if new != claim["status"]:
            self.ledger.append(at, "status-derived", claim_id, {"from": claim["status"], "to": new})
            claim["status"] = new
            claim["updated_at"] = max(claim["updated_at"], at)
            if new != "supported" and claim["visibility"] == "public-approved":
                claim["visibility"] = "private"  # fail closed: a claim that lost support is no longer public
                self.meta[claim_id]["approval_ref"] = None
                self.ledger.append(at, "visibility-set", claim_id, {"visibility": "private", "approval_ref": None,
                                                                    "cause": "support-lost"})
        return new

    # ----------------------------------------------------------------- reads
    def claim(self, claim_id: str) -> dict[str, Any]:
        return dict(self._claim(claim_id))

    def is_expired(self, claim_id: str, as_of: str) -> bool:
        valid_to = self._claim(claim_id)["valid_to"]
        return valid_to is not None and parse_ts(valid_to) <= parse_ts(_stamp(as_of))  # type: ignore[operator]

    def export(self, mode: str, as_of: str) -> dict[str, Any]:
        """Contract-valid ProfileClaim list for a consumer, filtered by the export mode's gate."""
        if mode not in EXPORT_MODES:
            raise VaultError("bad-export-mode", f"{mode!r} not in {EXPORT_MODES}")
        claims, withheld = [], {"contradicted": 0, "expired": 0, "visibility": 0, "status": 0}
        for cid in sorted(self.claims):
            claim = self.claims[cid]
            if claim["status"] == "contradicted":
                withheld["contradicted"] += 1
            elif self.is_expired(cid, as_of):
                withheld["expired"] += 1
            elif mode == "resume-draft" and claim["visibility"] == "private":
                withheld["visibility"] += 1
            elif mode == "resume-draft" and claim["status"] == "unverified":
                withheld["status"] += 1
            elif mode == "public" and claim["visibility"] != "public-approved":
                withheld["visibility"] += 1
            elif mode == "public" and not (claim["status"] == "supported" and self.meta[cid]["approval_ref"]):
                withheld["status"] += 1
            else:
                item = dict(claim)
                schema.check(item, "profile-claim")
                claims.append(item)
        return {"subject_id": self.subject_id, "mode": mode, "as_of": _stamp(as_of), "claims": claims,
                "withheld": withheld,
                "notice": "status is derived from evidence pointers; 'self-reported' claims are not verified facts"}

    def skill_proof(self, as_of: str) -> dict[str, dict[str, Any]]:
        """Per canonical skill: strongest usable support state and the claims behind it."""
        out: dict[str, dict[str, Any]] = {}
        for cid in sorted(self.claims):
            claim = self.claims[cid]
            expired = self.is_expired(cid, as_of)
            for skill in claim["skills"]:
                slot = out.setdefault(skill, {"status": None, "weight": 0.0, "claim_ids": [], "contradicted_claim_ids": [],
                                              "expired_claim_ids": []})
                if claim["status"] == "contradicted":
                    slot["contradicted_claim_ids"].append(cid)
                elif expired:
                    slot["expired_claim_ids"].append(cid)
                else:
                    slot["claim_ids"].append(cid)
                    if PROOF_WEIGHT[claim["status"]] > slot["weight"] or slot["status"] is None:
                        slot["status"], slot["weight"] = claim["status"], PROOF_WEIGHT[claim["status"]]
        return out

    def provenance(self, claim_id: str) -> dict[str, Any]:
        claim = self._claim(claim_id)
        ok, bad = self.ledger.verify()
        return {"claim": dict(claim), "meta": self.meta[claim_id],
                "evidence": [dict(self.evidence[e]) for e in claim["evidence_ids"]],
                "events": self.ledger.for_subject(claim_id),
                "ledger": {"verified": ok, "first_bad_entry": bad, "head": self.ledger.head}}

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict[str, Any]:
        return {"format": VAULT_FORMAT, "subject_id": self.subject_id, "claims": self.claims, "meta": self.meta,
                "evidence": self.evidence, "ledger": self.ledger.entries}

    @classmethod
    def from_dict(cls, doc: Any) -> "EvidenceVault":
        if not isinstance(doc, dict) or doc.get("format") != VAULT_FORMAT:
            raise VaultError("bad-format", "not a lane-A vault document")
        vault = cls(doc["subject_id"])
        vault.claims, vault.meta, vault.evidence = doc["claims"], doc["meta"], doc["evidence"]
        vault.ledger = Ledger(doc["ledger"])
        ok, bad = vault.ledger.verify()
        if not ok:
            raise VaultError("ledger-tampered", f"vault ledger fails verification at entry {bad}")
        for cid, claim in vault.claims.items():
            schema.check(claim, "profile-claim")
            if claim["status"] != vault._derive(cid):
                raise VaultError("status-not-derivable", f"{cid}: stored status {claim['status']!r} is not what the evidence supports")
        return vault

    def _claim(self, claim_id: str) -> dict[str, Any]:
        if claim_id not in self.claims:
            raise VaultError("unknown-claim", str(claim_id)[:60])
        return self.claims[claim_id]

    def _evidence(self, evidence_id: str) -> dict[str, Any]:
        if evidence_id not in self.evidence:
            raise VaultError("unknown-evidence", str(evidence_id)[:60])
        return self.evidence[evidence_id]


def _stamp(value: Any) -> str:
    dt = parse_ts(value)
    if dt is None:
        raise VaultError("bad-timestamp", f"{value!r} is not a tz-aware timestamp")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
