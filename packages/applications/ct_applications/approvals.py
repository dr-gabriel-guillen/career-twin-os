"""User-approval ledger: the manual gate in front of every sensitive action.

An approval is a journaled record created only by the `user` actor, bound to
(action, subject_id, content_hash), time-limited and — except for standing
consent — single-use. Changing the approved content changes its hash, so an
approval can never be carried over to different content, and a consumed token
cannot be replayed.

The gate also needs a typed confirmation phrase (see expected_confirmation).
This is not cryptography: it exists so that no code path can satisfy the gate
by accident, and so the UI (lane D) must show the user what is being approved.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from .core import CareerTwinError, Clock, clean_text, fmt_ts, parse_ts, require_id
from .journal import Journal

SUBMIT_APPLICATION = "submit_application"
EXPORT_RESUME = "export_resume"
EXPORT_DATA = "export_data"
PREREGISTER_EXPERIMENT = "preregister_experiment"
EXPERIMENT_CONSENT = "experiment_consent"
ACTIONS = {SUBMIT_APPLICATION, EXPORT_RESUME, EXPORT_DATA, PREREGISTER_EXPERIMENT, EXPERIMENT_CONSENT}
STANDING = {EXPERIMENT_CONSENT}  # checked repeatedly, revocable, never "consumed"
MAX_TTL_HOURS = {SUBMIT_APPLICATION: 72, EXPORT_RESUME: 72, EXPORT_DATA: 24,
                 PREREGISTER_EXPERIMENT: 72, EXPERIMENT_CONSENT: 24 * 365}


class ApprovalError(CareerTwinError):
    pass


def expected_confirmation(action: str, subject_id: str, content_hash: str) -> str:
    return f"I approve {action} for {subject_id} [{content_hash[:12]}]"


class ApprovalLedger:
    def __init__(self, path: str | os.PathLike[str] | None = None, clock: Clock | None = None):
        self.clock = clock or Clock()
        self.journal = Journal(path, name="approvals")
        self._tokens: dict[str, dict[str, Any]] = {}
        self._state: dict[str, str] = {}  # token_id -> active|consumed|revoked
        for rec in self.journal:
            self._apply(rec)

    def _apply(self, rec: dict[str, Any]) -> None:
        body = rec["body"]
        if rec["kind"] == "granted":
            self._tokens[body["token_id"]] = body
            self._state[body["token_id"]] = "active"
        elif rec["kind"] in ("consumed", "revoked"):
            self._state[body["token_id"]] = rec["kind"]

    def grant(self, *, action: str, subject_id: str, content_hash: str, actor: str,
              confirmation: str, ttl_hours: float = 24, statement: str = "") -> dict[str, Any]:
        if actor != "user":
            raise ApprovalError(f"only the user can approve; actor {actor!r} refused")
        if action not in ACTIONS:
            raise ApprovalError(f"unknown approval action {action!r}")
        require_id(subject_id, "subject_id")
        if not isinstance(content_hash, str) or len(content_hash) != 64:
            raise ApprovalError("approval must be bound to a sha256 content hash")
        if confirmation != expected_confirmation(action, subject_id, content_hash):
            raise ApprovalError("confirmation phrase does not match what is being approved")
        if not (0 < ttl_hours <= MAX_TTL_HOURS[action]):
            raise ApprovalError(f"ttl_hours must be in (0, {MAX_TTL_HOURS[action]}] for {action}")
        now = self.clock.now()
        token = {
            "token_id": f"apr-{len(self._tokens) + 1:06d}-{content_hash[:8]}",
            "action": action, "subject_id": subject_id, "content_hash": content_hash,
            "actor": "user", "granted_at": fmt_ts(now),
            "expires_at": fmt_ts(now + timedelta(hours=ttl_hours)),
            "statement": clean_text(statement, field="statement", max_len=500),
        }
        self._apply(self.journal.append("granted", token, fmt_ts(now)))
        return dict(token)

    def _check(self, token_id: Any, action: str, subject_id: str, content_hash: str) -> dict[str, Any]:
        token = self._tokens.get(token_id) if isinstance(token_id, str) else None
        if token is None:
            raise ApprovalError("approval token is missing or unknown")
        state = self._state[token_id]
        if state != "active":
            raise ApprovalError(f"approval token already {state} (replay refused)")
        if token["action"] != action:
            raise ApprovalError(f"approval is for {token['action']!r}, not {action!r}")
        if token["subject_id"] != subject_id:
            raise ApprovalError("approval was granted for a different subject")
        if token["content_hash"] != content_hash:
            raise ApprovalError("content changed after approval; a new approval is required")
        if self.clock.now() >= parse_ts(token["expires_at"]):
            raise ApprovalError("approval has expired")
        return token

    def consume(self, token_id: Any, *, action: str, subject_id: str, content_hash: str,
                used_for: str) -> dict[str, Any]:
        if action in STANDING:
            raise ApprovalError(f"{action} is standing consent; use check()")
        token = self._check(token_id, action, subject_id, content_hash)
        self._apply(self.journal.append(
            "consumed", {"token_id": token_id, "used_for": used_for}, self.clock.now_ts()))
        return dict(token)

    def check(self, token_id: Any, *, action: str, subject_id: str, content_hash: str) -> dict[str, Any]:
        return dict(self._check(token_id, action, subject_id, content_hash))

    def revoke(self, token_id: str, *, actor: str, reason: str = "") -> None:
        if actor != "user":
            raise ApprovalError("only the user can revoke an approval")
        if self._state.get(token_id) != "active":
            raise ApprovalError("only an active approval can be revoked")
        self._apply(self.journal.append(
            "revoked", {"token_id": token_id, "reason": clean_text(reason, max_len=500)},
            self.clock.now_ts()))

    def status(self, token_id: str) -> str:
        if token_id not in self._tokens:
            return "unknown"
        if self._state[token_id] == "active" and self.clock.now() >= parse_ts(self._tokens[token_id]["expires_at"]):
            return "expired"
        return self._state[token_id]

    def get(self, token_id: str) -> dict[str, Any] | None:
        token = self._tokens.get(token_id)
        return dict(token) if token else None
