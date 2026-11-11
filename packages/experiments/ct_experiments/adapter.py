"""Lane B adapter for the UI (lane D) and the integration harness (lane E).

Plain-JSON in, plain-JSON out, versioned (`api_version: "b.v1"`). Use
`LaneBApi.call("method", **params)`; it never raises for domain refusals and
returns {"ok": false, "error": {"type", "message"}} instead.

Contract for consumers:
  * every string that originated from a job posting, a claim or the user is
    UNTRUSTED PLAIN TEXT. Render it as text, never as HTML.
  * `grant_approval` must be called only as the direct result of the human
    typing/confirming the phrase returned by `approval_request`. Nothing in
    lane B calls it on its own.
  * nothing here submits an application, sends a message or opens a socket.
  * the randomisation seed is never returned.
"""
from __future__ import annotations

from typing import Any

from ct_applications import approvals as ap
from ct_applications.core import CareerTwinError, parse_ts

from . import API_VERSION
from .analysis import portfolio_summary
from .lab import Lab

RESPONSE_EVENTS = {"recruiter_screen", "interview", "rejected", "offer"}


class LaneBApi:
    def __init__(self, lab: Lab):
        self.lab = lab

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        fn = getattr(self, f"_m_{method}", None)
        base = {"api_version": API_VERSION, "method": method, "text_fields": "untrusted_plaintext"}
        if fn is None:
            return {**base, "ok": False, "error": {"type": "UnknownMethod", "message": method}}
        try:
            return {**base, "ok": True, "data": fn(**params)}
        except CareerTwinError as exc:
            return {**base, "ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        except (KeyError, TypeError) as exc:
            return {**base, "ok": False, "error": {"type": "BadRequest", "message": str(exc)}}

    def methods(self) -> list[str]:
        return sorted(name[3:] for name in dir(self) if name.startswith("_m_"))

    # -- read models -----------------------------------------------------
    def _m_list_applications(self, stage: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = (self.lab.store.projection(a) for a in self.lab.store.application_ids())
        return [r for r in rows if stage is None or r["stage"] == stage][:limit]

    def _m_get_application(self, application_id: str) -> dict[str, Any]:
        store = self.lab.store
        return {"projection": store.projection(application_id),
                "events": store.raw_events(application_id),          # frozen ApplicationEvent v0.1 objects
                "effective_events": store.effective_events(application_id),
                "assignment": self._public_assignment(self.lab.engine.for_application(application_id))}

    @staticmethod
    def _public_assignment(a: dict[str, Any] | None) -> dict[str, Any] | None:
        if a is None:
            return None
        keys = ("assignment_id", "experiment_id", "application_id", "unit_kind", "unit_id", "stratum",
                "variant", "assigned_at", "reused_unit", "design_hash")
        return {k: a[k] for k in keys}

    def _m_get_resume_variant(self, variant_id: str) -> dict[str, Any]:
        return self.lab.resumes.get(variant_id)

    def _m_resume_lineage(self, variant_id: str) -> list[dict[str, Any]]:
        return self.lab.resumes.lineage(variant_id)

    def _m_resume_diff(self, old_id: str, new_id: str) -> dict[str, Any]:
        return self.lab.resumes.diff(old_id, new_id)

    def _m_resume_preview(self, variant_id: str, fmt: str = "txt") -> dict[str, Any]:
        v = self.lab.resumes.get(variant_id)
        return {"variant_id": variant_id, "format": fmt, "document": self.lab.resumes.render(variant_id, fmt),
                "warnings": v["warnings"], "is_export": False}

    def _m_list_experiments(self) -> list[dict[str, Any]]:
        out = []
        for exp_id in self.lab.registry.ids():
            rec = self.lab.registry.get(exp_id)
            out.append({"id": exp_id, "title": rec["spec"]["title"], "status": rec["spec"]["status"],
                        "design_locked_at": rec["spec"]["design_locked_at"],
                        "assigned_applications": len(self.lab.engine.assignments(exp_id))})
        return out

    def _m_get_experiment(self, experiment_id: str) -> dict[str, Any]:
        rec = self.lab.registry.get(experiment_id)
        return {"spec": self.lab.registry.spec(experiment_id),   # frozen ExperimentSpec v0.1
                "design": rec["design"], "design_hash": rec["design_hash"],
                "design_notes": rec.get("design_notes", []), "locked": "locked_design_hash" in rec,
                "consent_recorded": "consent_token_ref" in rec}

    def _m_experiment_report(self, experiment_id: str, as_of: str | None = None) -> dict[str, Any]:
        return self.lab.report(experiment_id, as_of=parse_ts(as_of) if as_of else self.lab.clock.now())

    def _m_portfolio(self, as_of: str | None = None) -> dict[str, Any]:
        locked = [e for e in self.lab.registry.ids() if "locked_design_hash" in self.lab.registry.get(e)]
        return portfolio_summary([self._m_experiment_report(e, as_of) for e in locked])

    # -- approvals -------------------------------------------------------
    def _m_approval_request(self, action: str, subject_id: str, resume_variant_id: str | None = None) -> dict[str, Any]:
        """What the UI must show the user, and the phrase the user must confirm."""
        if action == ap.SUBMIT_APPLICATION:
            if not resume_variant_id:
                raise ap.ApprovalError("resume_variant_id is required to approve a submission package")
            digest = self.lab.package_hash(subject_id, resume_variant_id)
            showing = {"application": self.lab.store.projection(subject_id),
                       "resume": self.lab.resumes.get(resume_variant_id)}
        elif action == ap.EXPORT_RESUME:
            showing = {"resume": self.lab.resumes.get(subject_id)}
            digest = showing["resume"]["content_hash"]
        elif action in (ap.PREREGISTER_EXPERIMENT, ap.EXPERIMENT_CONSENT):
            showing = {"experiment": self._m_get_experiment(subject_id)}
            digest = showing["experiment"]["design_hash"]
        else:
            raise ap.ApprovalError(f"approval action {action!r} is not available through the adapter")
        return {"action": action, "subject_id": subject_id, "content_hash": digest, "must_show_user": showing,
                "confirmation_phrase": ap.expected_confirmation(action, subject_id, digest)}

    def _m_grant_approval(self, action: str, subject_id: str, content_hash: str, confirmation: str,
                          ttl_hours: float = 24) -> dict[str, Any]:
        token = self.lab.ledger.grant(action=action, subject_id=subject_id, content_hash=content_hash,
                                      actor="user", confirmation=confirmation, ttl_hours=ttl_hours)
        return {"token_id": token["token_id"], "expires_at": token["expires_at"]}

    # -- commands (all local bookkeeping; none leaves the machine) -----------------------
    def _m_approve_package(self, application_id: str, resume_variant_id: str, token_id: str) -> dict[str, Any]:
        proj = self.lab.store.projection(application_id)
        return self.lab.store.append_event(
            application_id=application_id, job_id=proj["job_id"], event_type="approved", actor="user",
            source="user:approval-dialog", outcome_status="observed", resume_variant_id=resume_variant_id,
            approval_token_ref=token_id, experiment_id=proj["experiment_id"])  # type: ignore[return-value]

    def _m_record_user_submission(self, application_id: str, occurred_at: str | None = None) -> dict[str, Any]:
        return self.lab.store.record_user_submission(application_id=application_id, occurred_at=occurred_at)

    def _m_record_response(self, application_id: str, event_type: str, occurred_at: str | None = None,
                           certain: bool = True, notes: str = "") -> dict[str, Any]:
        if event_type not in RESPONSE_EVENTS:
            raise CareerTwinError(f"event_type must be one of {sorted(RESPONSE_EVENTS)}")
        proj = self.lab.store.projection(application_id)
        return self.lab.store.append_event(
            application_id=application_id, job_id=proj["job_id"], event_type=event_type, actor="user",
            source="user:manual-response-entry", outcome_status="self-reported" if certain else "unknown",
            occurred_at=occurred_at, notes=notes, experiment_id=proj["experiment_id"])  # type: ignore[return-value]

    def _m_correct_event(self, target_event_id: str, action: str, reason: str,
                         changes: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.lab.store.correct_event(target_event_id=target_event_id, action=action, reason=reason,
                                            changes=changes, actor="user")

    def _m_export_resume(self, variant_id: str, fmt: str, token_id: str,
                         current_claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Returns the document to the caller. Lane B writes no file here and sends nothing."""
        return self.lab.resumes.export(variant_id, fmt=fmt, token_id=token_id, current_claims=current_claims)

    def _m_integrity(self) -> dict[str, Any]:
        self.lab.verify_all()
        return {"verified": True, "anchors": self.lab.anchors()}
