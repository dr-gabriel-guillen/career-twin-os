"""Application event store: immutable, attributable, contract-conformant.

Journal body = {"event": <frozen ApplicationEvent v0.1>, "ext": {...}}.
`ext` is lane B's own envelope (employer link, historical flag, correction
linkage). The frozen contract is never extended; consumers that only know the
contract can read `event` and ignore `ext`.

Nothing is ever edited in place. A mistake is fixed by appending a correction
(a contract-valid `note` event whose ext names the target), and the projection
is recomputed from the effective history.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Callable

from . import approvals as ap
from .core import CareerTwinError, Clock, ValidationError, clean_text, content_hash, parse_ts, require_id
from .journal import Journal
from .schema import validate
from .workflow import POST_SUBMISSION, STAGE_OF, TERMINAL, WorkflowError, check_transition, replay

EXT_SCHEMA = "careertwin.b.application-event-ext.v1"
AMENDABLE = {"occurred_at", "outcome_status", "notes"}
DEFAULT_POLICY = {"stale_draft_days": 30, "no_response_days": 45, "retention_review_days": 365}


class ReplayError(CareerTwinError):
    pass


class CorrectionError(CareerTwinError):
    pass


def submission_package_hash(application_id: str, job_id: str, resume_variant_id: str,
                            variant_content_hash: str) -> str:
    """What the user approves before submitting: this résumé, for this job, in this application."""
    return content_hash({"application_id": application_id, "job_id": job_id,
                         "resume_variant_id": resume_variant_id,
                         "variant_content_hash": variant_content_hash})


class ApplicationStore:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, clock: Clock | None = None,
                 approvals: ap.ApprovalLedger | None = None,
                 variant_hash: Callable[[str], str] | None = None):
        self.clock = clock or Clock()
        self.approvals = approvals
        self.variant_hash = variant_hash
        # set by the experiment lab: application_id -> journaled assignment (or None)
        self.assignment_lookup: Callable[[str], dict[str, Any] | None] | None = None
        self.journal = Journal(path, name="applications")
        self._events: dict[str, dict[str, Any]] = {}      # event id -> journal body
        self._order: dict[str, list[str]] = {}            # application id -> event ids
        self._meta: dict[str, dict[str, Any]] = {}        # application id -> job/employer/historical
        self._voided: dict[str, str] = {}                 # target event id -> correcting event id
        self._amended: dict[str, dict[str, Any]] = {}     # target event id -> merged changes
        self._import_keys: set[tuple[str, str, str, str | None]] = set()
        self._job_apps: dict[str, set[str]] = {}            # posting id (or duplicate id) -> applications
        self.duplicates_ignored = 0
        for rec in self.journal:
            if rec["kind"] == "event":
                self._index(rec["body"])
            elif rec["kind"] == "duplicate_ignored":
                self.duplicates_ignored += 1

    # -- indexing --------------------------------------------------------
    def _index(self, body: dict[str, Any]) -> None:
        ev, ext = body["event"], body["ext"]
        self._events[ev["id"]] = body
        self._order.setdefault(ev["application_id"], []).append(ev["id"])
        if ev["event_type"] == "saved":
            self._meta[ev["application_id"]] = {
                "job_id": ev["job_id"], "employer_id": ext.get("employer_id"),
                "historical": bool(ext.get("historical")),
                "job_members": list(ext.get("job_members") or [ev["job_id"]])}
            for key in self._meta[ev["application_id"]]["job_members"]:
                self._job_apps.setdefault(key, set()).add(ev["application_id"])
        if ev["actor"] == "import":
            self._import_keys.add((ev["application_id"], ev["event_type"], ev["source"], ev.get("occurred_at")))
        corr = ext.get("correction")
        if corr:
            if corr["action"] == "void":
                self._voided[corr["target"]] = ev["id"]
            else:
                self._amended.setdefault(corr["target"], {}).update(corr["changes"])

    def _effective(self, application_id: str) -> list[dict[str, Any]]:
        out = []
        for eid in self._order.get(application_id, []):
            if eid in self._voided:
                continue
            ev = dict(self._events[eid]["event"])
            ev.update(self._amended.get(eid, {}))
            out.append(ev)
        return out

    # -- writing ---------------------------------------------------------
    def open_application(self, *, application_id: str, job_id: str, employer_id: str,
                         actor: str = "user", source: str = "user:manual", historical: bool = False,
                         occurred_at: str | None = None, notes: str = "",
                         job_members: list[str] | None = None) -> dict[str, Any]:
        """First event of every application. `historical` marks an imported past
        application: it bypasses the approval gate (the user already acted) and is
        permanently barred from randomized experiments (observational only).

        `employer_id` should be lane A's `ext.employer.id`; `job_members` lane A's
        `ext.dedup.members` at this moment (ids of the same job on other boards).
        Both are snapshotted, so later changes on A's side cannot move them."""
        require_id(employer_id, "employer_id")
        members = sorted({require_id(m, "job_members[]") for m in (job_members or [])} | {job_id})
        if historical and actor != "import":
            raise WorkflowError("historical applications are recorded by the 'import' actor")
        if actor == "import" and not historical:
            raise WorkflowError("the 'import' actor may only open historical applications")
        return self._append(application_id=application_id, job_id=job_id, event_type="saved",
                            actor=actor, source=source, outcome_status="observed",
                            occurred_at=occurred_at, notes=notes,
                            ext={"employer_id": employer_id, "historical": historical,
                                 "job_members": members})  # type: ignore[return-value]

    def append_event(self, *, application_id: str, job_id: str, event_type: str, actor: str,
                     source: str, outcome_status: str, occurred_at: str | None = None,
                     resume_variant_id: str | None = None, experiment_id: str | None = None,
                     approval_token_ref: str | None = None, notes: str = "",
                     event_id: str | None = None, on_duplicate: str = "raise") -> dict[str, Any] | None:
        """Record one event. The opening event goes through open_application() and
        corrections through correct_event(); neither can be forged from here."""
        if event_type == "saved":
            raise WorkflowError("use open_application() so the employer link is recorded")
        return self._append(application_id=application_id, job_id=job_id, event_type=event_type, actor=actor,
                            source=source, outcome_status=outcome_status, occurred_at=occurred_at,
                            resume_variant_id=resume_variant_id, experiment_id=experiment_id,
                            approval_token_ref=approval_token_ref, notes=notes, event_id=event_id,
                            on_duplicate=on_duplicate, ext=None)

    def _append(self, *, application_id: str, job_id: str, event_type: str, actor: str,
                source: str, outcome_status: str, occurred_at: str | None = None,
                resume_variant_id: str | None = None, experiment_id: str | None = None,
                approval_token_ref: str | None = None, notes: str = "",
                event_id: str | None = None, on_duplicate: str = "raise",
                ext: dict[str, Any] | None = None) -> dict[str, Any] | None:
        _ext = ext
        if self.assignment_lookup is not None and event_type != "saved":
            assigned = self.assignment_lookup(application_id)
            if assigned is None and experiment_id is not None:
                raise WorkflowError("no journaled assignment for this application; assign it before "
                                    "linking events to an experiment")
            if assigned is not None:
                if experiment_id not in (None, assigned["experiment_id"]):
                    raise WorkflowError("application is assigned to a different experiment (crossover refused)")
                experiment_id = assigned["experiment_id"]  # the journaled assignment is the source of truth
        require_id(application_id, "application_id")
        require_id(job_id, "job_id")
        source = clean_text(source, field="source", max_len=200)
        if not source:
            raise ValidationError("source is required: every event must be attributable")
        now = self.clock.now_ts()
        seq = len(self.journal)
        eid = require_id(event_id, "event_id") if event_id is not None else None
        if eid is not None and eid.startswith("evt-"):
            raise ValidationError("event ids starting with 'evt-' are reserved for generated ids")
        event = {
            "id": eid or f"evt-{seq:07d}", "application_id": application_id, "job_id": job_id,
            "resume_variant_id": resume_variant_id, "event_type": event_type, "recorded_at": now,
            "occurred_at": occurred_at, "actor": actor, "source": source,
            "outcome_status": outcome_status, "experiment_id": experiment_id,
            "approval_token_ref": approval_token_ref, "notes": clean_text(notes, field="notes"),
        }
        validate(event, "application-event")
        for ref, name in ((resume_variant_id, "resume_variant_id"), (experiment_id, "experiment_id")):
            if ref is not None:
                require_id(ref, name)
        if event["id"] in self._events:
            raise ReplayError(f"event id {event['id']!r} already recorded (replay refused)")
        if occurred_at is not None and parse_ts(occurred_at) > parse_ts(now) + timedelta(minutes=5):
            raise ValidationError("occurred_at is in the future relative to recorded_at")
        if actor == "import":
            key = (application_id, event_type, source, occurred_at)
            if key in self._import_keys:
                if on_duplicate == "ignore":
                    self.journal.append("duplicate_ignored", {"key": list(key)}, now)
                    self.duplicates_ignored += 1
                    return None
                raise ReplayError(f"duplicate imported event {key!r}")

        ext: dict[str, Any] = {"schema": EXT_SCHEMA}
        ext.update(_ext or {})
        history = self._effective(application_id)
        meta = self._meta.get(application_id)
        if history or meta:
            if meta is None or meta["job_id"] != job_id:
                raise WorkflowError("job_id of an application is immutable")
        stage = replay(history)
        check_transition(stage, event_type, actor)
        historical = bool(meta["historical"]) if meta else bool(ext.get("historical"))
        if actor == "import" and not historical:
            if event_type not in POST_SUBMISSION and event_type != "note":
                raise WorkflowError("imports may only add recruiter responses to a live application")
        self._guard_experiment(history, experiment_id, historical)
        if event_type == "approved":
            self._guard_approved(event, historical)
        elif event_type == "submitted":
            self._guard_submitted(event, history, historical)
        elif event_type == "drafted" and resume_variant_id is not None and self.variant_hash and not historical:
            try:
                self.variant_hash(resume_variant_id)
            except KeyError:
                raise WorkflowError(f"unknown résumé variant {resume_variant_id!r}") from None
        body = {"event": event, "ext": ext}
        self.journal.append("event", body, now)
        self._index(body)
        return dict(event)

    def _guard_experiment(self, history: list[dict[str, Any]], experiment_id: str | None,
                          historical: bool) -> None:
        if experiment_id is None:
            return
        if historical:
            raise WorkflowError("historical applications cannot be enrolled in an experiment")
        prior = {e["experiment_id"] for e in history if e.get("experiment_id")}
        if prior and prior != {experiment_id}:
            raise WorkflowError("application is already linked to a different experiment (crossover refused)")
        if any(e["event_type"] == "submitted" for e in history) and not prior:
            raise WorkflowError("an experiment cannot be attached after submission")

    def _guard_approved(self, event: dict[str, Any], historical: bool) -> None:
        if historical:
            return
        if event["actor"] != "user":
            raise WorkflowError("only the user can approve an application package")
        if not event["resume_variant_id"]:
            raise WorkflowError("approval must name the exact résumé variant")
        if self.approvals is None or self.variant_hash is None:
            raise WorkflowError("approval gate is not configured; refusing to record approval")
        try:
            digest = self.variant_hash(event["resume_variant_id"])
        except KeyError:
            raise WorkflowError(f"unknown résumé variant {event['resume_variant_id']!r}") from None
        package = submission_package_hash(event["application_id"], event["job_id"],
                                          event["resume_variant_id"], digest)
        self.approvals.consume(event["approval_token_ref"], action=ap.SUBMIT_APPLICATION,
                               subject_id=event["application_id"], content_hash=package,
                               used_for=event["id"])

    def _guard_submitted(self, event: dict[str, Any], history: list[dict[str, Any]],
                         historical: bool) -> None:
        if historical:
            return
        if event["actor"] != "user":
            raise WorkflowError("only the user can report a submission; nothing is submitted programmatically")
        if event["outcome_status"] != "self-reported":
            raise WorkflowError("a submission is the user's own report: outcome_status must be 'self-reported'")
        approved = [e for e in history if e["event_type"] != "note"][-1]  # stage == approved
        if event["resume_variant_id"] != approved["resume_variant_id"]:
            raise WorkflowError("submitted résumé variant differs from the approved one")
        if event["approval_token_ref"] != approved["approval_token_ref"]:
            raise WorkflowError("submission must reference the approval that authorised it")
        if event["occurred_at"] and parse_ts(event["occurred_at"]) < parse_ts(approved["recorded_at"]):
            raise WorkflowError("a submission cannot be dated before the approval that authorised it")

    def record_user_submission(self, *, application_id: str, occurred_at: str | None = None,
                               notes: str = "", source: str = "user:manual-submission-report") -> dict[str, Any]:
        """The user tells CareerTwin they submitted the approved package themselves.

        ``source`` names who produced the report. Simulations that drive this gate on the user's behalf
        (the experiment stage) must say so with a ``simulation:`` source; the gate itself is unchanged.
        """
        history = self._effective(application_id)
        if replay(history) != "approved":
            raise WorkflowError("the application package has not been approved by the user")
        last = [e for e in history if e["event_type"] != "note"][-1]
        return self.append_event(
            application_id=application_id, job_id=last["job_id"], event_type="submitted",
            actor="user", source=source, outcome_status="self-reported",
            occurred_at=occurred_at, resume_variant_id=last["resume_variant_id"],
            experiment_id=last.get("experiment_id"), approval_token_ref=last["approval_token_ref"],
            notes=notes)

    def correct_event(self, *, target_event_id: str, action: str, reason: str, actor: str = "user",
                      changes: dict[str, Any] | None = None, source: str = "user:correction") -> dict[str, Any]:
        """Append an audit event that voids or amends an earlier event."""
        if actor != "user":
            raise CorrectionError("corrections must be attributable to the user")
        reason = clean_text(reason, field="reason", max_len=500)
        if not reason:
            raise CorrectionError("a correction needs a reason")
        body = self._events.get(target_event_id)
        if body is None:
            raise CorrectionError("unknown target event")
        target = body["event"]
        if body["ext"].get("correction"):
            raise CorrectionError("a correction cannot itself be corrected; append a new correction of the original")
        if target_event_id in self._voided:
            raise CorrectionError("target event is already voided")
        app_id = target["application_id"]
        if changes is not None and not isinstance(changes, dict):
            raise CorrectionError("changes must be an object")
        changes = dict(changes or {})
        if action == "void":
            if changes:
                raise CorrectionError("void takes no changes")
            if target["event_type"] == "saved":
                raise CorrectionError("the opening event cannot be voided; withdraw the application instead")
            remaining = [e for e in self._effective(app_id) if e["id"] != target_event_id]
            try:
                replay(remaining)
            except WorkflowError as exc:
                raise CorrectionError(f"voiding would leave an impossible history: {exc}") from exc
        elif action == "amend":
            if not changes or set(changes) - AMENDABLE:
                raise CorrectionError(f"amend may change only {sorted(AMENDABLE)}")
            if "notes" in changes:
                changes["notes"] = clean_text(changes["notes"], field="notes")
            merged = dict(target)
            merged.update(self._amended.get(target_event_id, {}))
            merged.update(changes)
            validate(merged, "application-event")
            if merged["event_type"] == "submitted" and merged["outcome_status"] != "self-reported" \
                    and not self._meta[app_id]["historical"]:
                raise CorrectionError("a user submission stays 'self-reported'")
            if merged.get("occurred_at") and parse_ts(merged["occurred_at"]) > self.clock.now() + timedelta(minutes=5):
                raise CorrectionError("occurred_at cannot be in the future")
            if merged["event_type"] == "submitted" and not self._meta[app_id]["historical"]:
                approved = [e for e in self._effective(app_id) if e["event_type"] == "approved"][-1]
                if merged.get("occurred_at") and parse_ts(merged["occurred_at"]) < parse_ts(approved["recorded_at"]):
                    raise CorrectionError("a submission cannot be dated before the approval that authorised it")
        else:
            raise CorrectionError("action must be 'void' or 'amend'")
        return self._append(
            application_id=app_id, job_id=target["job_id"], event_type="note", actor="user",
            source=source, outcome_status="observed",
            notes=f"CORRECTION {action} of {target_event_id}: {reason}"[:4000],
            ext={"correction": {"target": target_event_id, "action": action,
                                "changes": changes, "reason": reason}})  # type: ignore[return-value]

    # -- reading ---------------------------------------------------------
    def application_ids(self) -> list[str]:
        return list(self._order)

    def raw_events(self, application_id: str) -> list[dict[str, Any]]:
        """Every recorded event, including voided ones and corrections (audit view)."""
        return [dict(self._events[e]["event"]) for e in self._order.get(application_id, [])]

    def effective_events(self, application_id: str) -> list[dict[str, Any]]:
        return self._effective(application_id)

    def uncorrected_events(self, application_id: str) -> list[dict[str, Any]]:
        """History as it stood before the user's first correction of this application.

        Everything recorded from the first correction onwards is left out, including
        new events entered after a void (e.g. void a rejection, then record a screen)."""
        out = []
        for e in self._order.get(application_id, []):
            if self._events[e]["ext"].get("correction"):
                break
            out.append(dict(self._events[e]["event"]))
        return out

    def applications_for_jobs(self, job_keys: list[str]) -> set[str]:
        """Every application opened for any of these posting ids (lane A duplicates included)."""
        return set().union(*(self._job_apps.get(k, set()) for k in job_keys)) if job_keys else set()

    def event_ext(self, event_id: str) -> dict[str, Any]:
        return dict(self._events[event_id]["ext"])

    def meta(self, application_id: str) -> dict[str, Any]:
        if application_id not in self._meta:
            raise KeyError(application_id)
        return dict(self._meta[application_id])

    def projection(self, application_id: str, policy: dict[str, int] | None = None) -> dict[str, Any]:
        if application_id not in self._meta:
            raise KeyError(application_id)
        policy = {**DEFAULT_POLICY, **(policy or {})}
        eff = self._effective(application_id)
        staged = [e for e in eff if e["event_type"] != "note"]
        stage = STAGE_OF[staged[-1]["event_type"]]
        meta = self._meta[application_id]
        now = self.clock.now()

        def when(ev: dict[str, Any]) -> str:
            return ev.get("occurred_at") or ev["recorded_at"]

        def first(kind: str) -> str | None:
            return next((when(e) for e in staged if e["event_type"] == kind), None)

        last_at = parse_ts(when(staged[-1]))
        age_days = (now - last_at).total_seconds() / 86400
        flags = []
        if stage in ("saved", "draft", "approved") and age_days > policy["stale_draft_days"]:
            flags.append("stale_before_submission")
        if stage == "submitted" and age_days > policy["no_response_days"]:
            # never converted into a rejection: the outcome stays unknown
            flags.append("no_response_outcome_unknown")
        if age_days > policy["retention_review_days"]:
            flags.append("retention_review_due")
        variant = next((e["resume_variant_id"] for e in reversed(staged) if e.get("resume_variant_id")), None)
        experiment = next((e["experiment_id"] for e in eff if e.get("experiment_id")), None)
        return {
            "application_id": application_id, "job_id": meta["job_id"],
            "employer_id": meta["employer_id"], "historical": meta["historical"],
            "stage": stage, "terminal": stage in TERMINAL, "stage_since": when(staged[-1]),
            "resume_variant_id": variant, "experiment_id": experiment,
            "submitted_at": first("submitted"),
            "milestones": {k: first(k) for k in ("recruiter_screen", "interview", "offer", "rejected", "withdrawn")},
            "event_count": len(self._order[application_id]),
            "voided_event_ids": [e for e in self._order[application_id] if e in self._voided],
            "amended_event_ids": [e for e in self._order[application_id] if e in self._amended],
            "lifecycle_flags": flags,
            "last_changed_by": {"actor": staged[-1]["actor"], "source": staged[-1]["source"]},
        }
