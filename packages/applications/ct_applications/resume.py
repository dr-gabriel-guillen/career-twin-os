"""Truthful résumé variant engine.

A variant is an immutable snapshot of bullets, each of which is the verbatim
text of one ProfileClaim (lane A's frozen contract) that is `supported` with
evidence or explicitly `self-reported`. The engine selects and orders existing
claims; it never writes, paraphrases or embellishes a qualification.

- contradicted / unverified / expired / private / evidence-less claims are refused
- a requirement nobody can prove is reported as unproven, or refused outright
  when the caller insists on it (MissingProofError)
- a user may overwrite bullet text by hand; the bullet is then flagged
  `manual_override` and every export carries a warning
- export needs a user approval bound to the exact content hash and is refused
  if the underlying claims have since changed or lost their support
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from . import approvals as ap
from .core import (CareerTwinError, Clock, clean_text, content_hash, escape_html, looks_like_markup,
                   parse_ts, require_id, sha256_hex)
from .journal import Journal
from .schema import validate

VARIANT_SCHEMA = "careertwin.b.resume-variant.v1"
EXPORTABLE_VISIBILITY = {"shareable", "public-approved"}
MANUAL_WARNING = ("MANUAL_OVERWRITE: this bullet was edited by hand and is no longer the verbatim "
                  "text of an evidence-backed claim; the user alone vouches for it")
SELF_REPORTED_WARNING = "SELF_REPORTED: this bullet rests on the user's own statement, not on evidence"
FORMATS = {"txt", "md", "html"}


class MissingProofError(CareerTwinError):
    pass


class StaleEvidenceError(CareerTwinError):
    pass


class ResumeError(CareerTwinError):
    pass


def evidence_hash(claim: dict[str, Any]) -> str:
    return content_hash({"id": claim["id"], "claim_text": claim["claim_text"], "status": claim["status"],
                         "evidence_ids": sorted(claim["evidence_ids"]), "updated_at": claim["updated_at"]})


def claim_refusal(claim: dict[str, Any], subject_id: str, now, include_self_reported: bool = True) -> str | None:
    """Reason this claim may not appear on a résumé, or None if it may."""
    if claim["subject_id"] != subject_id:
        return "belongs_to_another_subject"
    if claim["status"] == "contradicted":
        return "contradicted_by_evidence"
    if claim["status"] == "unverified":
        return "unverified"
    if claim["status"] == "supported" and not claim["evidence_ids"]:
        return "supported_without_evidence"
    if claim["status"] == "self-reported" and not include_self_reported:
        return "self_reported_excluded"
    if claim["visibility"] not in EXPORTABLE_VISIBILITY:
        return "private_visibility"
    if claim.get("valid_to") and parse_ts(claim["valid_to"]) <= now:
        return "expired"
    if not clean_text(claim["claim_text"], field="claim_text"):
        return "empty_text"
    return None


_MD_SPECIAL = re.compile("([" + re.escape(chr(92) + "`*_{}[]()#+-!|>~") + "])")


def escape_markdown(text: str) -> str:
    """Neutralise both HTML and Markdown syntax (links, images, emphasis)."""
    return escape_html(_MD_SPECIAL.sub(lambda m: chr(92) + m.group(1), text))


def _norm(skill: str) -> str:
    return skill.strip().lower()


class ResumeEngine:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, clock: Clock | None = None,
                 approvals: ap.ApprovalLedger | None = None):
        self.clock = clock or Clock()
        self.approvals = approvals
        self.journal = Journal(path, name="resume-variants")
        self._variants: dict[str, dict[str, Any]] = {}
        self._lineage_head: dict[str, str] = {}
        self.exports: list[dict[str, Any]] = []
        for rec in self.journal:
            if rec["kind"] == "variant":
                self._index(rec["body"])
            elif rec["kind"] == "export":
                self.exports.append(rec["body"])

    def _index(self, variant: dict[str, Any]) -> None:
        self._variants[variant["id"]] = variant
        self._lineage_head[variant["lineage_id"]] = variant["id"]

    # -- construction ----------------------------------------------------
    def _bullet(self, claim: dict[str, Any]) -> dict[str, Any]:
        text = clean_text(claim["claim_text"], field="claim_text")
        return {"claim_id": claim["id"], "text": text, "claim_status": claim["status"],
                "evidence_ids": sorted(claim["evidence_ids"]), "evidence_hash": evidence_hash(claim),
                "skills": sorted({_norm(s) for s in claim.get("skills", [])}),
                "manual_override": False, "claim_text_hash": sha256_hex(text),
                "contains_markup_like_text": looks_like_markup(text)}

    def _store(self, *, lineage_id: str | None, version: int, parent_id: str | None, subject_id: str,
               job_id: str, label: str, bullets: list[dict[str, Any]], unproven: list[str],
               refused: list[dict[str, str]], created_by: str, change_note: str) -> dict[str, Any]:
        warnings = []
        if any(b["manual_override"] for b in bullets):
            warnings.append(MANUAL_WARNING)
        if any(b["claim_status"] == "self-reported" for b in bullets):
            warnings.append(SELF_REPORTED_WARNING)
        if unproven:
            warnings.append("UNPROVEN_REQUIREMENTS: no evidence-backed claim covers: " + ", ".join(unproven))
        core = {"schema": VARIANT_SCHEMA, "subject_id": subject_id, "job_id": job_id,
                "label": clean_text(label, field="label", max_len=120), "version": version,
                "parent_id": parent_id, "bullets": bullets, "unproven_requirements": unproven,
                "refused_claims": refused, "warnings": warnings}
        digest = content_hash(core)
        vid = f"rv-{digest[:20]}"
        if vid in self._variants:
            return dict(self._variants[vid])  # identical content: same immutable variant
        variant = {**core, "id": vid, "lineage_id": lineage_id or vid, "content_hash": digest,
                   "created_at": self.clock.now_ts(), "created_by": created_by,
                   "change_note": clean_text(change_note, field="change_note", max_len=500)}
        self.journal.append("variant", variant, variant["created_at"])
        self._index(variant)
        return dict(variant)

    def build_variant(self, *, subject_id: str, job: dict[str, Any], claims: Iterable[dict[str, Any]],
                      label: str = "default", max_bullets: int = 8, include_self_reported: bool = True,
                      must_cover_skills: Iterable[str] = (), emphasize_skills: Iterable[str] = (),
                      created_by: str = "system") -> dict[str, Any]:
        require_id(subject_id, "subject_id")
        job_id = require_id(job.get("id"), "job.id")
        if not (1 <= max_bullets <= 30):
            raise ResumeError("max_bullets must be between 1 and 30")
        now = self.clock.now()
        job_skills = [_norm(s) for s in job.get("skills", [])]
        emphasis = {_norm(s) for s in emphasize_skills}
        eligible, refused, seen = [], [], set()
        for claim in claims:
            validate(claim, "profile-claim")
            if claim["id"] in seen:
                raise ResumeError(f"duplicate claim id {claim['id']!r}")
            seen.add(claim["id"])
            reason = claim_refusal(claim, subject_id, now, include_self_reported)
            if reason:
                refused.append({"claim_id": claim["id"], "reason": reason})
            else:
                eligible.append(claim)

        def rank(claim: dict[str, Any]):
            skills = {_norm(s) for s in claim.get("skills", [])}
            return (-len(skills & emphasis), -len(skills & set(job_skills)),
                    0 if claim["status"] == "supported" else 1, claim["id"])

        chosen = sorted(eligible, key=rank)[:max_bullets]
        covered = {s for c in chosen for s in map(_norm, c.get("skills", []))}
        provable = {s for c in eligible for s in map(_norm, c.get("skills", []))}
        missing = sorted({_norm(s) for s in must_cover_skills} - provable)
        if missing:
            raise MissingProofError(
                "refusing to build a résumé that asserts skills without proof: " + ", ".join(missing))
        dropped = sorted({_norm(s) for s in must_cover_skills} - covered)
        if dropped:
            raise ResumeError(f"max_bullets too small to cover required skills: {', '.join(dropped)}")
        if not chosen:
            raise MissingProofError("no evidence-backed or self-reported claim is eligible for a résumé")
        unproven = sorted(set(job_skills) - provable)
        return self._store(lineage_id=None, version=1, parent_id=None, subject_id=subject_id,
                           job_id=job_id, label=label, bullets=[self._bullet(c) for c in chosen],
                           unproven=unproven, refused=sorted(refused, key=lambda r: r["claim_id"]),
                           created_by=created_by, change_note="initial selection")

    def _next(self, parent: dict[str, Any], bullets: list[dict[str, Any]], created_by: str,
              change_note: str) -> dict[str, Any]:
        if self._lineage_head[parent["lineage_id"]] != parent["id"]:
            raise ResumeError("revise the latest version of this résumé lineage, not an older one")
        if not bullets:
            raise ResumeError("a résumé variant needs at least one bullet")
        return self._store(lineage_id=parent["lineage_id"], version=parent["version"] + 1,
                           parent_id=parent["id"], subject_id=parent["subject_id"], job_id=parent["job_id"],
                           label=parent["label"], bullets=bullets, unproven=parent["unproven_requirements"],
                           refused=parent["refused_claims"], created_by=created_by, change_note=change_note)

    def revise(self, variant_id: str, *, claims: Iterable[dict[str, Any]] = (), add_claim_ids: Iterable[str] = (),
               remove_claim_ids: Iterable[str] = (), order: list[str] | None = None,
               created_by: str = "user", change_note: str = "") -> dict[str, Any]:
        parent = self.get(variant_id)
        by_id = {c["id"]: c for c in claims}
        bullets = [b for b in parent["bullets"] if b["claim_id"] not in set(remove_claim_ids)]
        have = {b["claim_id"] for b in bullets}
        now = self.clock.now()
        for cid in add_claim_ids:
            if cid in have:
                continue
            claim = by_id.get(cid)
            if claim is None:
                raise MissingProofError(f"claim {cid!r} was not supplied; cannot add an unbacked bullet")
            validate(claim, "profile-claim")
            reason = claim_refusal(claim, parent["subject_id"], now)
            if reason:
                raise MissingProofError(f"claim {cid!r} refused: {reason}")
            bullets.append(self._bullet(claim))
            have.add(cid)
        if order is not None:
            if sorted(order) != sorted(have):
                raise ResumeError("order must list exactly the bullets of the new version")
            pos = {cid: i for i, cid in enumerate(order)}
            bullets.sort(key=lambda b: pos[b["claim_id"]])
        return self._next(parent, bullets, created_by, change_note or "revision")

    def manual_overwrite(self, variant_id: str, *, claim_id: str, new_text: str, actor: str) -> dict[str, Any]:
        if actor != "user":
            raise ResumeError("only the user may overwrite résumé text by hand")
        parent = self.get(variant_id)
        new_text = clean_text(new_text, field="new_text", max_len=600)
        if not new_text:
            raise ResumeError("replacement text is empty")
        bullets, hit = [], False
        for b in parent["bullets"]:
            b = dict(b)
            if b["claim_id"] == claim_id:
                hit = True
                b.update(text=new_text, manual_override=True,
                         contains_markup_like_text=looks_like_markup(new_text))
            bullets.append(b)
        if not hit:
            raise ResumeError(f"variant has no bullet for claim {claim_id!r}")
        return self._next(parent, bullets, "user", f"manual overwrite of bullet {claim_id}")

    # -- reading ---------------------------------------------------------
    def get(self, variant_id: str) -> dict[str, Any]:
        variant = self._variants.get(variant_id)
        if variant is None:
            raise KeyError(f"unknown résumé variant {variant_id!r}")
        return dict(variant)

    def content_hash_of(self, variant_id: str) -> str:
        return self.get(variant_id)["content_hash"]

    def lineage(self, variant_id: str) -> list[dict[str, Any]]:
        lineage_id = self.get(variant_id)["lineage_id"]
        return sorted((dict(v) for v in self._variants.values() if v["lineage_id"] == lineage_id),
                      key=lambda v: v["version"])

    def diff(self, old_id: str, new_id: str) -> dict[str, Any]:
        old, new = self.get(old_id), self.get(new_id)
        ob = {b["claim_id"]: b for b in old["bullets"]}
        nb = {b["claim_id"]: b for b in new["bullets"]}
        common = [c for c in ob if c in nb]
        return {
            "from": old_id, "to": new_id, "same_lineage": old["lineage_id"] == new["lineage_id"],
            "added": [nb[c] for c in nb if c not in ob],
            "removed": [ob[c] for c in ob if c not in nb],
            "text_changed": [{"claim_id": c, "before": ob[c]["text"], "after": nb[c]["text"],
                              "manual_override": nb[c]["manual_override"]}
                             for c in common if ob[c]["text"] != nb[c]["text"]],
            "evidence_changed": [c for c in common if ob[c]["evidence_hash"] != nb[c]["evidence_hash"]],
            "reordered": [c for c in ob if c in nb] != [c for c in nb if c in ob],
        }

    def staleness(self, variant_id: str, current_claims: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
        """Compare the snapshot with the claims as they are now."""
        variant = self.get(variant_id)
        current = {c["id"]: c for c in current_claims}
        now = self.clock.now()
        problems = []
        for b in variant["bullets"]:
            claim = current.get(b["claim_id"])
            if claim is None:
                problems.append({"claim_id": b["claim_id"], "problem": "claim_no_longer_exists", "blocking": "yes"})
                continue
            reason = claim_refusal(claim, variant["subject_id"], now)
            if reason:
                problems.append({"claim_id": b["claim_id"], "problem": reason, "blocking": "yes"})
            elif evidence_hash(claim) != b["evidence_hash"]:
                problems.append({"claim_id": b["claim_id"], "problem": "claim_changed_since_snapshot", "blocking": "yes"})
        return problems

    # -- export (approval-gated) -----------------------------------------
    def render(self, variant_id: str, fmt: str = "txt") -> str:
        """Pure rendering for on-screen preview. Returns text; writes nothing."""
        if fmt not in FORMATS:
            raise ResumeError(f"format must be one of {sorted(FORMATS)}")
        v = self.get(variant_id)
        lines = [b["text"] for b in v["bullets"]]
        if fmt == "txt":
            return "\n".join(f"- {t}" for t in lines) + "\n"
        if fmt == "md":
            return "\n".join(f"- {escape_markdown(t)}" for t in lines) + "\n"
        items = "\n".join(f"  <li>{escape_html(t)}</li>" for t in lines)
        return f"<!doctype html>\n<meta charset=\"utf-8\">\n<ul>\n{items}\n</ul>\n"

    def export(self, variant_id: str, *, fmt: str, token_id: str | None,
               current_claims: Iterable[dict[str, Any]],
               dest_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Produce a document that can leave the system. Requires a user approval
        for this exact content; refused when evidence went stale."""
        if self.approvals is None:
            raise ap.ApprovalError("approval gate is not configured; export refused")
        variant = self.get(variant_id)
        blocking = [p for p in self.staleness(variant_id, current_claims) if p["blocking"] == "yes"]
        if blocking:
            raise StaleEvidenceError("export refused; claims changed or lost support: "
                                     + ", ".join(f"{p['claim_id']} ({p['problem']})" for p in blocking))
        document = self.render(variant_id, fmt)
        self.approvals.consume(token_id, action=ap.EXPORT_RESUME, subject_id=variant_id,
                               content_hash=variant["content_hash"], used_for=f"export:{fmt}")
        record = {"variant_id": variant_id, "content_hash": variant["content_hash"], "format": fmt,
                  "document_sha256": sha256_hex(document), "approval_token_ref": token_id,
                  "exported_at": self.clock.now_ts(), "written_to_disk": dest_dir is not None,
                  "warnings": list(variant["warnings"])}
        self.journal.append("export", record, record["exported_at"])
        self.exports.append(record)
        path = None
        if dest_dir is not None:
            dest = Path(dest_dir)
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"resume-{variant_id}.{fmt}"  # name is built here, never from user text
            path.write_text(document, encoding="utf-8", newline="\n")
        return {**record, "document": document, "path": str(path) if path else None}
