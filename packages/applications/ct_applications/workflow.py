"""Application workflow state machine (pure functions, no I/O).

Event types are the frozen ApplicationEvent.event_type enum. `submitted` means
"the user reports having submitted the application themselves"; lane B has no
code path that submits, emails or contacts anyone.
"""
from __future__ import annotations

from typing import Any, Iterable

from .core import CareerTwinError

STAGE_OF = {
    "saved": "saved", "drafted": "draft", "approved": "approved", "submitted": "submitted",
    "recruiter_screen": "recruiter_screen", "interview": "interview", "rejected": "rejected",
    "offer": "offer", "withdrawn": "withdrawn",
}
TERMINAL = {"rejected", "withdrawn"}
# stage -> event types that may follow ("note" never changes the stage and is handled separately)
ALLOWED: dict[str | None, set[str]] = {
    None: {"saved"},
    "saved": {"drafted", "withdrawn"},
    "draft": {"drafted", "approved", "withdrawn"},
    "approved": {"drafted", "submitted", "withdrawn"},
    "submitted": {"recruiter_screen", "interview", "rejected", "withdrawn"},
    "recruiter_screen": {"interview", "rejected", "offer", "withdrawn"},
    "interview": {"interview", "rejected", "offer", "withdrawn"},
    "offer": {"rejected", "withdrawn"},  # rescinded / declined
    "rejected": set(),
    "withdrawn": set(),
}
# who may record what. `system` can never approve, submit or withdraw.
ACTOR_MAY = {
    "user": set(STAGE_OF) | {"note"},
    "system": {"saved", "drafted", "note"},
    "import": {"saved", "drafted", "approved", "submitted", "recruiter_screen", "interview",
               "rejected", "offer", "withdrawn", "note"},
}
POST_SUBMISSION = {"recruiter_screen", "interview", "rejected", "offer"}


class WorkflowError(CareerTwinError):
    pass


def check_transition(stage: str | None, event_type: str, actor: str) -> str | None:
    """Return the resulting stage or raise WorkflowError."""
    if event_type not in ACTOR_MAY.get(actor, set()):
        raise WorkflowError(f"actor {actor!r} may not record {event_type!r}")
    if event_type == "note":
        if stage is None:
            raise WorkflowError("a note needs an existing application (first event must be 'saved')")
        return stage
    if event_type not in ALLOWED[stage]:
        allowed = sorted(ALLOWED[stage]) or ["(terminal: corrections only)"]
        raise WorkflowError(f"{event_type!r} is not allowed from stage {stage!r}; allowed: {allowed}")
    return STAGE_OF[event_type]


def replay(events: Iterable[dict[str, Any]]) -> str | None:
    """Validate a full effective history and return the final stage."""
    stage: str | None = None
    for ev in events:
        stage = check_transition(stage, ev["event_type"], ev["actor"])
    return stage
