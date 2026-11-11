"""One wired-up, offline lane-B workspace (in memory, or JSONL files in a directory)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ct_applications import approvals as ap
from ct_applications.core import Clock
from ct_applications.events import ApplicationStore, submission_package_hash
from ct_applications.journal import IntegrityError, Journal
from ct_applications.resume import ResumeEngine

from .analysis import analyze
from .assignment import AssignmentEngine
from .registry import ExperimentRegistry

FILES = ("approvals", "variants", "applications", "experiments", "assignments")


class Lab:
    def __init__(self, root: str | os.PathLike[str] | None = None, *, clock: Clock | None = None):
        self.root = Path(root) if root is not None else None
        self.clock = clock or Clock()
        path = (lambda name: self.root / f"{name}.jsonl") if self.root else (lambda name: None)
        self.ledger = ap.ApprovalLedger(path("approvals"), clock=self.clock)
        self.resumes = ResumeEngine(path("variants"), clock=self.clock, approvals=self.ledger)
        self.store = ApplicationStore(path("applications"), clock=self.clock, approvals=self.ledger,
                                      variant_hash=self.resumes.content_hash_of)
        self.registry = ExperimentRegistry(path("experiments"), clock=self.clock, approvals=self.ledger)
        self.engine = AssignmentEngine(path("assignments"), clock=self.clock, registry=self.registry,
                                       store=self.store)
        self.store.assignment_lookup = self.engine.for_application

    def journals(self) -> dict[str, Any]:
        return {"approvals": self.ledger.journal, "variants": self.resumes.journal,
                "applications": self.store.journal, "experiments": self.registry.journal,
                "assignments": self.engine.journal}

    def anchors(self) -> dict[str, dict[str, Any]]:
        """Publishable chain heads; compare later to detect tail truncation."""
        return {name: j.anchor() for name, j in self.journals().items()}

    def verify_all(self, anchors: dict[str, dict[str, Any]] | None = None) -> bool:
        for name, journal in self.journals().items():
            a = (anchors or {}).get(name)
            journal.verify(a["head"] if a else None, a["length"] if a else None)
            if journal.path is not None:  # the file on disk must still be exactly what is in memory
                on_disk = Journal(journal.path, name=journal.name) if journal.path.exists() else Journal(name=journal.name)
                if (len(on_disk), on_disk.head()) != (len(journal), journal.head()):
                    raise IntegrityError(f"{name}: file on disk differs from the journal in memory")
        # every approval that was acted upon must be recorded as consumed in the ledger
        used = [e["approval_token_ref"] for e in self.resumes.exports]
        used += [rec["body"]["approval_token_ref"] for rec in self.registry.journal.records("locked")]
        for app in self.store.application_ids():
            if not self.store.meta(app)["historical"]:
                used += [e["approval_token_ref"] for e in self.store.raw_events(app) if e["event_type"] == "approved"]
        if len(used) != len(set(used)):
            raise IntegrityError("an approval token was acted upon more than once")
        stale = [t for t in used if self.ledger.status(t) != "consumed"]
        if stale:
            raise IntegrityError(f"approval ledger does not show {len(stale)} used token(s) as consumed "
                                 "(ledger truncated or rewritten)")
        for exp in self.registry.ids():
            if "locked_design_hash" in self.registry._require(exp):
                self.engine.verify(exp)
        return True

    def package_hash(self, application_id: str, resume_variant_id: str) -> str:
        meta = self.store.meta(application_id)
        return submission_package_hash(application_id, meta["job_id"], resume_variant_id,
                                       self.resumes.content_hash_of(resume_variant_id))

    def report(self, experiment_id: str, **kw: Any) -> dict[str, Any]:
        kw.setdefault("as_of", self.clock.now())
        return analyze(experiment_id, registry=self.registry, engine=self.engine, store=self.store, **kw)
