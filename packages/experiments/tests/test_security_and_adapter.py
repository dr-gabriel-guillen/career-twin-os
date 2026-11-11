import ast
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
import _bootstrap  # noqa: E402,F401
from ct_applications import approvals as ap  # noqa: E402
from ct_applications import schema, synthetic  # noqa: E402
from ct_applications.core import FixedClock  # noqa: E402
from ct_experiments import simulate  # noqa: E402
from ct_experiments.adapter import LaneBApi  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402

PACKAGES = Path(__file__).resolve().parents[2]
SOURCES = sorted(p for pkg in ("applications/ct_applications", "experiments/ct_experiments", "experiments/tools")
                 for p in (PACKAGES / pkg).glob("*.py"))
FIXTURES = PACKAGES / "experiments" / "fixtures" / "v1"
FORBIDDEN_IMPORTS = {"socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "smtplib", "imaplib",
                     "poplib", "ftplib", "telnetlib", "subprocess", "webbrowser", "asyncio", "ctypes",
                     "multiprocessing", "xmlrpc", "boto3", "selenium", "playwright", "pickle", "marshal"}
ATTR = {"seniority": "senior", "role_family": "fde"}


def setup_lab(root=None):
    lab = Lab(root, clock=FixedClock(simulate.START))
    exp = simulate.register(lab, simulate.Scenario("sec", "Sec", "n/a", n_per_arm=15, mde=0.5))
    return lab, LaneBApi(lab), exp


class TestNoOutboundCapability(unittest.TestCase):
    def test_sources_import_nothing_that_can_reach_out(self):
        self.assertGreaterEqual(len(SOURCES), 17)
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertNotIn(name.split(".")[0], FORBIDDEN_IMPORTS, f"{path.name} imports {name}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "__import__", "compile"}, path.name)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                    self.assertNotIn(node.attr, {"system", "popen", "startfile", "remove", "unlink", "rmdir"}, path.name)

    def test_no_real_endpoints_or_automation_hooks_in_source(self):
        url = re.compile(r"https?://[^\s\"')]+")
        for path in SOURCES:
            text = path.read_text(encoding="utf-8")
            for found in url.findall(text):
                self.assertTrue(".invalid" in found, f"{path.name}: non-reserved URL {found}")
            lowered = text.lower()
            for needle in ("linkedin.com", "mailto:", "smtp", "auto_apply", "autoapply", "api_key", "apikey",
                           "bearer ", "password"):
                self.assertNotIn(needle, lowered, f"{path.name} contains {needle!r}")

    def test_full_roundtrip_with_networking_disabled(self):
        real = socket.socket

        def blocked(*a, **k):
            raise AssertionError("lane B attempted to open a socket")

        socket.socket = blocked  # type: ignore[assignment]
        socket.create_connection, real_cc = blocked, socket.create_connection  # type: ignore[assignment]
        try:
            out = simulate.run_cohort(only=["tiny-01"])
            self.assertEqual(out["results"][0]["report"]["phase"], "FINAL")
            lab, api, exp = setup_lab()
            done = simulate.submit_application(lab, app_id="a1", job=synthetic.job("fake-a1"), employer_id="emp-a1",
                                               exp_id=exp, attributes=ATTR, claims=synthetic.claims())
            tok = simulate.simulated_user_grant(lab.ledger, ap.EXPORT_RESUME, done["variant_id"],
                                                lab.resumes.content_hash_of(done["variant_id"]))
            res = api.call("export_resume", variant_id=done["variant_id"], fmt="html", token_id=tok,
                           current_claims=synthetic.claims())
            self.assertTrue(res["ok"])
        finally:
            socket.socket, socket.create_connection = real, real_cc  # type: ignore[misc]

    def test_nothing_is_written_outside_the_workspace_and_exports_are_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            root, elsewhere = Path(d) / "workspace", Path(d) / "elsewhere"
            elsewhere.mkdir()
            lab, api, exp = setup_lab(root)
            done = simulate.submit_application(lab, app_id="a1", job=synthetic.job("fake-a1"), employer_id="emp-a1",
                                               exp_id=exp, attributes=ATTR, claims=synthetic.claims())
            tok = simulate.simulated_user_grant(lab.ledger, ap.EXPORT_RESUME, done["variant_id"],
                                                lab.resumes.content_hash_of(done["variant_id"]))
            res = api.call("export_resume", variant_id=done["variant_id"], fmt="txt", token_id=tok,
                           current_claims=synthetic.claims())
            self.assertTrue(res["ok"])
            self.assertIsNone(res["data"]["path"])
            self.assertFalse(res["data"]["written_to_disk"])
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ["applications.jsonl", "approvals.jsonl", "assignments.jsonl", "experiments.jsonl",
                              "variants.jsonl"])
            self.assertEqual(list(elsewhere.iterdir()), [])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["elsewhere", "workspace"])


class TestGateBypassAttempts(unittest.TestCase):
    """Regression tests for weaknesses found in lane B's own adversarial review."""

    def setUp(self):
        self.lab, self.api, self.exp = setup_lab()

    def test_historical_flag_and_corrections_cannot_be_forged(self):
        store = self.lab.store
        with self.assertRaises(TypeError):  # the envelope is not caller-controlled
            store.append_event(application_id="x1", job_id="fake-x1", event_type="saved", actor="user",
                               source="t", outcome_status="observed", _ext={"employer_id": "e", "historical": True})
        with self.assertRaises(TypeError):
            store.append_event(application_id="x1", job_id="fake-x1", event_type="note", actor="user", source="t",
                               outcome_status="observed", ext={"correction": {"target": "evt-0", "action": "void"}})
        from ct_applications.workflow import WorkflowError
        with self.assertRaises(WorkflowError):  # the opening event only through open_application()
            store.append_event(application_id="x1", job_id="fake-x1", event_type="saved", actor="import",
                               source="t", outcome_status="observed")
        # a live application cannot be pushed through the gate by the import actor
        store.open_application(application_id="x1", job_id="fake-x1", employer_id="emp-x1")
        for kind in ("drafted", "approved", "submitted"):
            with self.assertRaises(WorkflowError):
                store.append_event(application_id="x1", job_id="fake-x1", event_type=kind, actor="import",
                                   source="import:forged", outcome_status="observed")

    def test_events_cannot_claim_an_experiment_without_a_journaled_assignment(self):
        from ct_applications.workflow import WorkflowError
        store = self.lab.store
        store.open_application(application_id="x1", job_id="fake-x1", employer_id="emp-x1")
        with self.assertRaises(WorkflowError):
            store.append_event(application_id="x1", job_id="fake-x1", event_type="drafted", actor="system",
                               source="t", outcome_status="observed", experiment_id=self.exp)
        self.lab.engine.assign(experiment_id=self.exp, application_id="x1", attributes=ATTR)
        with self.assertRaises(WorkflowError):
            store.append_event(application_id="x1", job_id="fake-x1", event_type="drafted", actor="system",
                               source="t", outcome_status="observed", experiment_id="exp-other")
        ev = store.append_event(application_id="x1", job_id="fake-x1", event_type="drafted", actor="system",
                                source="t", outcome_status="observed")  # link is filled from the journal
        self.assertEqual(ev["experiment_id"], self.exp)

    def test_only_the_adapter_and_the_simulated_user_can_grant_approvals(self):
        callers = {p.name for p in SOURCES if re.search(r"\.grant\(", p.read_text(encoding="utf-8"))}
        self.assertEqual(callers, {"adapter.py", "simulate.py"})
        product = [p for p in SOURCES if "simulated_user_grant" in p.read_text(encoding="utf-8")]
        # simulate.py defines the synthetic user; e2e_stage.py plays it for lane E's integration run
        self.assertEqual({p.name for p in product}, {"simulate.py", "e2e_stage.py"})

    def test_corrections_to_enrolled_applications_are_surfaced_in_the_report(self):
        simulate.submit_application(self.lab, app_id="a1", job=synthetic.job("fake-a1"), employer_id="emp-a1",
                                    exp_id=self.exp, attributes=ATTR, claims=synthetic.claims())
        res = self.api.call("record_response", application_id="a1", event_type="rejected")
        self.api.call("correct_event", target_event_id=res["data"]["id"], action="void", reason="wrong posting")
        report = self.api.call("experiment_report", experiment_id=self.exp)["data"]
        self.assertEqual(report["flow"]["corrected_events"], 1)
        self.assertTrue(any(w.startswith("OUTCOMES_CORRECTED") for w in report["warnings"]))


class TestAdapter(unittest.TestCase):
    def setUp(self):
        self.lab, self.api, self.exp = setup_lab()
        self.done = simulate.submit_application(
            self.lab, app_id="a1", job=synthetic.job("fake-a1"), employer_id="emp-a1", exp_id=self.exp,
            attributes=ATTR, claims=synthetic.claims(), submit=False)

    def ok(self, method, **kw):
        res = self.api.call(method, **kw)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["api_version"], "b.v1")
        json.dumps(res)  # every response is plain JSON
        return res["data"]

    def err(self, method, **kw):
        res = self.api.call(method, **kw)
        self.assertFalse(res["ok"], res)
        json.dumps(res)
        return res["error"]["type"]

    def test_gated_submission_flow_through_the_adapter(self):
        vid = self.done["variant_id"]
        self.assertEqual(self.err("approve_package", application_id="a1", resume_variant_id=vid, token_id="apr-x"),
                         "ApprovalError")
        self.assertEqual(self.err("record_user_submission", application_id="a1"), "WorkflowError")
        req = self.ok("approval_request", action=ap.SUBMIT_APPLICATION, subject_id="a1", resume_variant_id=vid)
        self.assertIn("resume", req["must_show_user"])
        self.assertEqual(self.err("grant_approval", action=ap.SUBMIT_APPLICATION, subject_id="a1",
                                  content_hash=req["content_hash"], confirmation="ok"), "ApprovalError")
        tok = self.ok("grant_approval", action=ap.SUBMIT_APPLICATION, subject_id="a1",
                      content_hash=req["content_hash"], confirmation=req["confirmation_phrase"])["token_id"]
        self.ok("approve_package", application_id="a1", resume_variant_id=vid, token_id=tok)
        sub = self.ok("record_user_submission", application_id="a1")
        schema.validate(sub, "application-event")
        self.assertEqual(self.err("record_response", application_id="a1", event_type="submitted"), "CareerTwinError")
        unsure = self.ok("record_response", application_id="a1", event_type="recruiter_screen", certain=False)
        self.assertEqual(unsure["outcome_status"], "unknown")
        app = self.ok("get_application", application_id="a1")
        self.assertEqual(app["projection"]["stage"], "recruiter_screen")
        self.assertEqual(app["assignment"]["experiment_id"], self.exp)
        for event in app["events"]:
            schema.validate(event, "application-event")
            # the opening event predates assignment; everything after it carries the experiment link
            self.assertEqual(event["experiment_id"], None if event["event_type"] == "saved" else self.exp)
        fix = self.ok("correct_event", target_event_id=unsure["id"], action="void", reason="not a screen after all")
        self.assertEqual(fix["event_type"], "note")
        self.assertEqual(self.ok("get_application", application_id="a1")["projection"]["stage"], "submitted")

    def test_bad_requests_never_raise(self):
        self.assertEqual(self.err("launch_missiles"), "UnknownMethod")
        self.assertEqual(self.err("get_application", application_id="nope"), "BadRequest")
        self.assertEqual(self.err("get_application"), "BadRequest")
        self.assertEqual(self.err("get_experiment", experiment_id="nope"), "BadRequest")
        self.assertEqual(self.err("approval_request", action="auto_apply", subject_id="a1"), "ApprovalError")
        self.assertEqual(self.err("approval_request", action=ap.EXPORT_DATA, subject_id="a1"), "ApprovalError")
        self.assertEqual(self.err("experiment_report", experiment_id=self.exp, as_of="tomorrow"), "ValidationError")
        self.assertEqual(self.err("resume_preview", variant_id=self.done["variant_id"], fmt="exe"), "ResumeError")

    def test_seed_and_private_internals_are_never_exposed(self):
        blob = json.dumps([self.ok("get_experiment", experiment_id=self.exp), self.ok("list_experiments"),
                           self.ok("experiment_report", experiment_id=self.exp), self.ok("portfolio"),
                           self.ok("get_application", application_id="a1"), self.ok("integrity")])
        self.assertNotIn("synthetic-seed-sec", blob)
        self.assertNotIn('"seed"', blob)
        self.assertNotIn("consent_token_ref", blob)
        self.assertEqual(self.api.methods(), sorted(self.api.methods()))
        self.assertFalse(any("delete" in m or "send" in m or "apply" in m or "email" in m for m in self.api.methods()))

    def test_preview_is_not_an_export_and_escapes_markup(self):
        claims = synthetic.claims() + [synthetic.claim("claim-x", "<script>alert(1)</script>", skills=["python"])]
        v = self.lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=synthetic.job("fake-x"), claims=claims,
                                           emphasize_skills=["python"])
        prev = self.ok("resume_preview", variant_id=v["id"], fmt="html")
        self.assertFalse(prev["is_export"])
        self.assertNotIn("<script", prev["document"])
        self.assertEqual(self.lab.resumes.exports, [])
        self.assertEqual(self.err("export_resume", variant_id=v["id"], fmt="html", token_id="apr-none",
                                  current_claims=claims), "ApprovalError")

    def test_preregistration_and_consent_requests_bind_to_the_design(self):
        req = self.ok("approval_request", action=ap.PREREGISTER_EXPERIMENT, subject_id=self.exp)
        self.assertEqual(req["content_hash"], self.lab.registry.design_hash(self.exp))
        self.assertTrue(self.ok("integrity")["verified"])


class TestCommittedFixtures(unittest.TestCase):
    """The JSON that lanes D and E consume. Regenerate with tools/build_fixtures.py."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURES / "MANIFEST.json").exists():
            raise unittest.SkipTest("fixtures not built yet")
        cls.manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
        cls.data = {name: json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in cls.manifest["files"]}

    def test_manifest_hashes_match(self):
        from ct_applications.core import sha256_hex
        for name, meta in self.manifest["files"].items():
            # LF-normalised: a Windows checkout with core.autocrlf=true must not fail (fixtures/.gitattributes
            # also marks them -text, so a fresh checkout keeps the committed bytes)
            data = (FIXTURES / name).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(sha256_hex(data), meta["sha256"], name)
        self.assertEqual(sorted(p.name for p in FIXTURES.glob("*.json")), sorted([*self.manifest["files"], "MANIFEST.json"]))

    def test_scale_and_required_verdicts(self):
        apps = self.data["applications_sample.json"]
        self.assertGreaterEqual(apps["total_applications"], 200)
        reports = self.data["experiment_reports.json"]
        self.assertGreaterEqual(len(reports), 20)
        verdicts = {r["report"]["verdict"] for r in reports}
        self.assertIn("INCONCLUSIVE", verdicts)
        self.assertIn("INTERIM_NO_CLAIM", verdicts)
        self.assertTrue(any("negative control" in r["ground_truth"] for r in reports))
        for r in reports:
            rep = r["report"]
            self.assertEqual(rep["schema"], "careertwin.b.analysis-report.v1")
            if rep["verdict"] != "EVIDENCE_FOR_DIFFERENCE":
                self.assertIsNone(rep["claim"])
                self.assertTrue(rep["reasons_withheld"])
        self.assertEqual(self.data["observational.json"]["verdict"], "OBSERVATIONAL_ONLY")

    def test_contract_conformance(self):
        rt = self.data["roundtrip.json"]
        for event in rt["application"]["events"]:
            schema.validate(event, "application-event")
        schema.validate(rt["inputs"]["job_posting"], "job-posting")
        for claim in rt["inputs"]["profile_claims"]:
            schema.validate(claim, "profile-claim")
        for exp in self.data["experiments.json"]:
            schema.validate(exp["spec"], "experiment")
        self.assertFalse(rt["calls"]["replayed_approval_is_refused"]["ok"])
        self.assertEqual(rt["outcome"]["status"], "success")

    def test_fixtures_are_synthetic_and_leak_no_seed(self):
        for name in self.manifest["files"]:
            text = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertNotIn("synthetic-seed-", text, name)
            self.assertNotIn("@", text, name)  # no e-mail addresses of any kind
            for url in re.findall(r"https?://[^\s\"]+", text):
                self.assertIn("example.invalid", url)
        employers = {p["employer_id"] for p in self.data["applications_sample.json"]["sample"]}
        self.assertTrue(all(e.startswith("emp-") for e in employers))
        self.assertTrue(self.manifest["synthetic_only"])


if __name__ == "__main__":
    unittest.main()
