"""Versioned job store, cross-source dedup and hash-chained provenance ledger.

All timestamps written here come from capture metadata or an explicit ``as_of``;
the wall clock is never read, so identical inputs give byte-identical state.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ct_connectors import ConnectorError, ErrorClass, ParseResult, parse_envelope
from ct_connectors.base import canonical_json, sha256_hex
from ct_connectors.textsafe import fold

from . import schema
from .normalize import normalize, status_at, title_key, utc

STORE_FORMAT = "a-store/0.1"
DEDUP_THRESHOLD = 0.6
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 1, "ashby": 2, "user-import": 3, "other-permitted": 4, "synthetic": 5}
GENESIS = "0" * 64


class Ledger:
    """Append-only, hash-chained event log. Tampering with any entry breaks ``verify``."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])

    @staticmethod
    def _digest(entry: dict[str, Any]) -> str:
        return sha256_hex(canonical_json({k: entry[k] for k in ("seq", "at", "kind", "subject", "detail", "prev")}))

    def append(self, at: str, kind: str, subject: str, detail: dict[str, Any]) -> dict[str, Any]:
        entry = {"seq": len(self.entries), "at": at, "kind": kind, "subject": subject, "detail": detail,
                 "prev": self.entries[-1]["hash"] if self.entries else GENESIS}
        entry["hash"] = self._digest(entry)
        self.entries.append(entry)
        return entry

    def verify(self) -> tuple[bool, int | None]:
        """(ok, index of first bad entry)."""
        prev = GENESIS
        for idx, entry in enumerate(self.entries):
            try:
                ok = entry["seq"] == idx and entry["prev"] == prev and entry["hash"] == self._digest(entry)
            except (KeyError, TypeError):
                ok = False
            if not ok:
                return False, idx
            prev = entry["hash"]
        return True, None

    def for_subject(self, subject: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["subject"] == subject]

    @property
    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z0-9+#]{3,}", fold(text)))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


class JobStore:
    def __init__(self) -> None:
        self.versions: dict[str, list[dict[str, Any]]] = {}
        self.quarantine: list[dict[str, Any]] = []
        self.ledger = Ledger()
        self._dedup: dict[str, dict[str, Any]] = {}
        self._dedup_dirty = True

    # ------------------------------------------------------------------ ingest
    def import_envelope(self, doc: Any) -> dict[str, Any]:
        """Import one capture envelope. Never raises for bad data: failures are classified in the report."""
        try:
            result = parse_envelope(doc)
        except ConnectorError as exc:
            issue = {"error_class": exc.error_class, "severity": "reject", "source": "?", "source_ref": "payload",
                     "detail": exc.detail}
            self.quarantine.append(issue)
            return {"ok": False, "capture": None, "rejected": [issue], "warnings": [],
                    "counts": _counts(rejected=1), "job_ids": []}
        return self.import_result(result)

    def import_result(self, result: ParseResult) -> dict[str, Any]:
        cap = result.capture
        counts = _counts()
        rejected = [i.to_dict() for i in result.rejected]
        warnings = [i.to_dict() for i in result.issues if i.severity == "warn"]
        job_ids: list[str] = []
        for record in result.records:
            env = normalize(record)
            problems = schema.errors(env["posting"], schema.load("job-posting"))
            if problems:
                rejected.append({"error_class": "schema_violation", "severity": "reject", "source": cap.source,
                                 "source_ref": record.source_id, "detail": "; ".join(problems[:3])})
                continue
            outcome = self._upsert(env)
            counts[outcome] += 1
            job_ids.append(env["posting"]["id"])
        if cap.full_snapshot:
            if result.seen_source_ids or result.records:
                counts["revoked"] = self._revoke_missing(cap.source, cap.board, set(result.seen_source_ids), cap.captured_at)
            else:
                self.ledger.append(cap.captured_at, "empty-snapshot-ignored", f"{cap.source}/{cap.board}",
                                   {"note": "an empty feed is not treated as proof that every posting closed"})
        counts["rejected"] = len(rejected)
        counts["warnings"] = len(warnings)
        self.quarantine.extend(rejected)
        self._dedup_dirty = True
        return {"ok": True, "capture": {"source": cap.source, "board": cap.board, "captured_at": cap.captured_at,
                                        "synthetic": cap.synthetic, "full_snapshot": cap.full_snapshot},
                "counts": counts, "rejected": rejected, "warnings": warnings, "job_ids": sorted(job_ids)}

    def _upsert(self, env: dict[str, Any]) -> str:
        posting, ext = env["posting"], env["ext"]
        jid, at = posting["id"], posting["fetched_at"]
        chain = self.versions.get(jid)
        if chain is None:
            ext["version"], ext["supersedes"] = 1, None
            self.versions[jid] = [env]
            self.ledger.append(at, "created", jid, {"content_hash": ext["content_hash"], "source": posting["source"], "wire_format": ext["wire_format"],
                                                    "source_id": posting["source_id"], "payload_sha256": ext["payload_sha256"]})
            return "created"
        latest = chain[-1]
        if utc(at) < utc(latest["posting"]["fetched_at"]):
            self.ledger.append(at, "out-of-order-capture-ignored", jid,
                               {"latest_fetched_at": latest["posting"]["fetched_at"], "content_hash": ext["content_hash"]})
            return "ignored_stale"
        if ext["content_hash"] == latest["ext"]["content_hash"]:
            latest["posting"]["fetched_at"] = at
            latest["posting"]["source_evidence"] = posting["source_evidence"]
            latest["ext"]["timestamps"]["captured_at"] = at
            if latest["ext"].get("revoked"):
                latest["ext"]["revoked"] = None
                self.ledger.append(at, "relisted", jid, {})
            latest["posting"]["status"], latest["ext"]["status_reason"] = status_at(latest["ext"], at, utc(at))
            self.ledger.append(at, "refreshed", jid, {"content_hash": ext["content_hash"]})
            return "unchanged"
        changed = sorted(k for k in posting if k not in ("fetched_at", "source_evidence", "status")
                         and posting[k] != latest["posting"].get(k))
        if ext["description_text"] != latest["ext"]["description_text"]:
            changed.append("description_text")
        ext["version"], ext["supersedes"] = latest["ext"]["version"] + 1, latest["ext"]["content_hash"]
        chain.append(env)
        self.ledger.append(at, "version-added", jid, {"version": ext["version"], "content_hash": ext["content_hash"],
                                                      "changed": changed, "payload_sha256": ext["payload_sha256"]})
        return "updated"

    def _revoke_missing(self, source: str, board: str, seen: set[str], at: str) -> int:
        revoked = 0
        for jid in sorted(self.versions):
            latest = self.versions[jid][-1]
            p, e = latest["posting"], latest["ext"]
            if e["wire_format"] != source or e["board"] != board or p["source_id"] in seen or e.get("revoked"):
                continue
            if utc(at) <= utc(p["fetched_at"]):
                continue  # an older snapshot cannot revoke a newer observation
            e["revoked"] = "absent-from-source-snapshot"
            e["revoked_at"] = at
            p["status"], e["status_reason"] = "expired", e["revoked"]
            self.ledger.append(at, "revoked", jid, {"reason": e["revoked"], "last_seen": p["fetched_at"]})
            revoked += 1
        return revoked

    # ------------------------------------------------------------------- dedup
    def _recompute_dedup(self) -> None:
        latest = {jid: chain[-1] for jid, chain in self.versions.items()}
        uids_by_employer: dict[str, set[str]] = {}
        groups: dict[tuple[str, str, str, str], list[str]] = {}
        for jid, env in latest.items():
            p, e = env["posting"], env["ext"]
            emp = e["employer"]["key"]
            if e["employer"].get("org_uid"):
                uids_by_employer.setdefault(emp, set()).add(e["employer"]["org_uid"])
            groups.setdefault((emp, title_key(p["title"]), fold(p["location"] or ""), p["remote_policy"]), []).append(jid)
        info: dict[str, dict[str, Any]] = {}
        for key in sorted(groups):
            members = sorted(groups[key], key=lambda j: (SOURCE_PRIORITY.get(latest[j]["ext"]["wire_format"], 9), j))
            clusters: list[list[str]] = []
            for jid in members:
                tok = _tokens(latest[jid]["ext"]["description_text"]) or frozenset(latest[jid]["posting"]["skills"])
                uid = latest[jid]["ext"]["employer"].get("org_uid")
                for cluster in clusters:
                    head = latest[cluster[0]]
                    head_uid = head["ext"]["employer"].get("org_uid")
                    if uid and head_uid and uid != head_uid:
                        continue  # same display name, different registered employer: never merge
                    head_tok = _tokens(head["ext"]["description_text"]) or frozenset(head["posting"]["skills"])
                    if _jaccard(tok, head_tok) >= DEDUP_THRESHOLD:
                        cluster.append(jid)
                        break
                else:
                    clusters.append([jid])
            for cluster in clusters:
                canonical = cluster[0]
                for jid in cluster:
                    same_board = sum(1 for other in cluster
                                     if latest[other]["ext"]["wire_format"] == latest[jid]["ext"]["wire_format"]
                                     and latest[other]["ext"]["board"] == latest[jid]["ext"]["board"]) > 1
                    info[jid] = {"cluster_id": "dc_" + canonical[3:], "canonical_id": canonical,
                                 "is_canonical": jid == canonical, "members": list(cluster),
                                 "duplicate_of": None if jid == canonical else canonical,
                                 "repost_within_board": same_board}
        for jid, env in latest.items():
            env["ext"]["employer"]["alias_ambiguous"] = len(uids_by_employer.get(env["ext"]["employer"]["key"], ())) > 1
        self._dedup = info
        self._dedup_dirty = False

    # ------------------------------------------------------------------- reads
    def _present(self, env: dict[str, Any], as_of: datetime, ttl_days: int | None = None) -> dict[str, Any]:
        posting, ext = dict(env["posting"]), dict(env["ext"])
        args = (ext, posting["fetched_at"], as_of) + ((ttl_days,) if ttl_days is not None else ())
        posting["status"], ext["status_reason"] = status_at(*args)
        ext["status_as_of"] = as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
        ext["dedup"] = self._dedup[posting["id"]]
        return {"posting": posting, "ext": ext}

    def view(self, as_of: str, *, canonical_only: bool = False, ttl_days: int | None = None) -> list[dict[str, Any]]:
        """Latest version of every job with status evaluated at ``as_of``. Sorted by id. Treat as read-only."""
        if self._dedup_dirty:
            self._recompute_dedup()
        moment = utc(as_of)
        out = [self._present(self.versions[jid][-1], moment, ttl_days) for jid in sorted(self.versions)]
        return [e for e in out if e["ext"]["dedup"]["is_canonical"]] if canonical_only else out

    def get(self, jid: str, as_of: str) -> dict[str, Any] | None:
        if jid not in self.versions:
            return None
        if self._dedup_dirty:
            self._recompute_dedup()
        return self._present(self.versions[jid][-1], utc(as_of))

    def provenance(self, jid: str) -> dict[str, Any] | None:
        chain = self.versions.get(jid)
        if chain is None:
            return None
        if self._dedup_dirty:
            self._recompute_dedup()
        ok, bad = self.ledger.verify()
        return {"job_id": jid, "source": chain[-1]["posting"]["source"], "wire_format": chain[-1]["ext"]["wire_format"], "source_id": chain[-1]["posting"]["source_id"],
                "board": chain[-1]["ext"]["board"], "url": chain[-1]["posting"]["url"],
                "license_note": chain[-1]["posting"].get("license_note", ""), "synthetic": chain[-1]["ext"]["synthetic"],
                "versions": [{"version": e["ext"]["version"], "fetched_at": e["posting"]["fetched_at"],
                              "content_hash": e["ext"]["content_hash"], "supersedes": e["ext"]["supersedes"],
                              "payload_sha256": e["ext"]["payload_sha256"], "source_evidence": e["posting"]["source_evidence"]}
                             for e in chain],
                "dedup": self._dedup[jid], "events": self.ledger.for_subject(jid),
                "ledger": {"verified": ok, "first_bad_entry": bad, "head": self.ledger.head, "length": len(self.ledger.entries)}}

    def stats(self) -> dict[str, Any]:
        ok, _ = self.ledger.verify()
        return {"jobs": len(self.versions), "versions": sum(len(c) for c in self.versions.values()),
                "quarantined": len(self.quarantine), "ledger_entries": len(self.ledger.entries), "ledger_verified": ok,
                "ledger_head": self.ledger.head}

    # ------------------------------------------------------------- persistence
    def to_dict(self) -> dict[str, Any]:
        return {"format": STORE_FORMAT, "versions": {k: self.versions[k] for k in sorted(self.versions)},
                "quarantine": self.quarantine, "ledger": self.ledger.entries}

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)

    @classmethod
    def load(cls, path: str | Path) -> "JobStore":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != STORE_FORMAT:
            raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, "not a lane-A job store file")
        store = cls()
        store.versions = doc["versions"]
        store.quarantine = doc["quarantine"]
        store.ledger = Ledger(doc["ledger"])
        ok, bad = store.ledger.verify()
        if not ok:
            raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, f"provenance ledger fails verification at entry {bad}")
        return store


def _counts(**overrides: int) -> dict[str, int]:
    base = {"created": 0, "updated": 0, "unchanged": 0, "ignored_stale": 0, "revoked": 0, "rejected": 0, "warnings": 0}
    base.update(overrides)
    return base
