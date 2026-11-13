"""Outcome model: turn an application's event history into one honest outcome.

status is exactly one of
  success      the metric event (or a later funnel stage) happened inside the window
  no_success   the window closed, or the application ended, without it
  censored     the window is still open: the outcome is not known yet (right-censored)
  missing      the unit was never exposed (never submitted) or its dates are unusable

Unknown stays unknown: a silent employer is `censored` until the window closes
and is never recorded as a rejection in the CRM. Ambiguous recruiter feedback
(outcome_status == "unknown") never counts as success. Late responses are kept
as a flag, not folded into the preregistered metric.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ct_applications.core import parse_ts

# reaching a later stage implies the earlier one happened
IMPLIES = {"recruiter_screen": ("recruiter_screen", "interview", "offer"),
           "interview": ("interview", "offer"), "offer": ("offer",)}


def _when(ev: dict[str, Any]) -> tuple[datetime, bool]:
    if ev.get("occurred_at"):
        return parse_ts(ev["occurred_at"]), False
    return parse_ts(ev["recorded_at"]), True


def application_outcome(events: list[dict[str, Any]], *, metric: str, window_days: int,
                        as_of: datetime, assigned_at: datetime | None = None) -> dict[str, Any]:
    """`events` is the effective (post-correction) history of one application.

    With `assigned_at` (experiments), exposure must happen within `window_days`
    of assignment. Until that deadline an unsubmitted application is pending
    (censored), afterwards it is missing for good: a later submission is
    ignored, so a final analysis cannot change after the fact.
    """
    flags: list[str] = []
    out: dict[str, Any] = {"metric": metric, "status": "missing", "exposed_at": None, "window_end": None,
                           "event_at": None, "counted_event_id": None, "flags": flags}
    deadline = assigned_at + timedelta(days=window_days) if assigned_at is not None else None
    submitted = next((e for e in events if e["event_type"] == "submitted" and _when(e)[0] <= as_of), None)
    if submitted is not None and deadline is not None and _when(submitted)[0] > deadline:
        flags.append("exposed_after_deadline_ignored")
        return out
    if submitted is not None and assigned_at is not None and _when(submitted)[0] < assigned_at:
        flags.append("exposure_dated_before_assignment")  # cannot happen through the gated workflow
        return out
    if submitted is None:
        if any(e["event_type"] == "withdrawn" and _when(e)[0] <= as_of for e in events):
            flags.append("withdrawn_before_exposure")
        elif deadline is not None and as_of < deadline:
            flags.append("awaiting_exposure")
            out["status"] = "censored"
        else:
            flags.append("never_exposed")
        return out
    exposed_at, fallback = _when(submitted)
    if fallback:
        flags.append("exposure_time_from_recorded_at")
    window_end = exposed_at + timedelta(days=window_days)
    out.update(exposed_at=exposed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
               window_end=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    in_window, late, seen = [], [], set()
    for ev in events:
        if ev["event_type"] not in IMPLIES[metric]:
            continue
        at, fb = _when(ev)
        key = (ev["event_type"], at)
        if key in seen:  # the same response reported twice (e.g. mail + manual entry)
            flags.append("duplicate_response_collapsed")
            continue
        seen.add(key)
        if at > as_of:
            continue  # not knowable at analysis time
        if ev["outcome_status"] == "unknown":
            flags.append("ambiguous_feedback_not_counted")
            continue
        if at < exposed_at:
            flags.append("response_dated_before_exposure_ignored")
            continue
        if fb:
            flags.append("response_time_from_recorded_at")
        (in_window if at <= window_end else late).append((at, ev.get("id")))
    if in_window:
        at, event_id = min(in_window, key=lambda x: x[0])
        out.update(status="success", event_at=at.strftime("%Y-%m-%dT%H:%M:%SZ"), counted_event_id=event_id)
        return out
    if late:
        flags.append("late_success_outside_window")
    ended = next((e for e in events if e["event_type"] in ("rejected", "withdrawn")
                  and exposed_at <= _when(e)[0] <= as_of), None)
    if ended is not None:
        out["counted_event_id"] = ended.get("id")
    if ended is not None:
        flags.append(f"ended_by_{ended['event_type']}")
        out["status"] = "no_success"
    elif as_of >= window_end:
        out["status"] = "no_success"
        if not late:
            flags.append("no_response_within_window")
    else:
        out["status"] = "censored"
    return out
