"""Regression tests for the first adversarial review of lane B (findings C1-C3, H1-H4, M1-M5, L1-L3).

Each test reproduces the reviewer's attack through the public surface and asserts it now fails.
"""
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
import _bootstrap  # noqa: E402,F401
from ct_applications import approvals as ap  # noqa: E402
from ct_applications import synthetic  # noqa: E402
from ct_applications.core import FixedClock, ValidationError, parse_ts  # noqa: E402
from ct_applications.journal import IntegrityError  # noqa: E402
from ct_applications.resume import claim_refusal  # noqa: E402
from ct_experiments import simulate, stats  # noqa: E402
from ct_experiments.adapter import LaneBApi  # noqa: E402
from ct_experiments.analysis import infer  # noqa: E402
from ct_experiments.assignment import AssignmentError, unit_variant  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.registry import make_design  # noqa: E402
from ct_experiments.simulate import Scenario, simulated_user_grant, submit_application  # noqa: E402

ATTR = {"seniority": "senior", "role_family": "fde"}


def mklab(key="rv", n=15, unit="application", root=None, **kw):
    lab = Lab(root, clock=FixedClock(simulate.START))
    exp = simulate.register(lab, Scenario(key, "review", "n/a", n_per_arm=n, unit=unit, mde=0.5, **kw))
    return lab, LaneBApi(lab), exp


def enrol(lab, exp, i, key="rv", employer=None, submit=True):
    lab.clock.advance(hours=1)
    return submit_application(lab, app_id=f"app-{key}-{i:03d}", job=synthetic.job(f"fake-{key}-{i:03d}"),
                              employer_id=employer or f"emp-{key}-{i:03d}", exp_id=exp, attributes=ATTR,
                              claims=synthetic.claims(), submit=submit)


class TestTimeAttacks(unittest.TestCase):
    def test_c1_future_as_of_is_refused(self):
        lab, api, exp = mklab()
        for i in range(30):
            enrol(lab, exp, i)
        for method, kw in (("experiment_report", {"experiment_id": exp}), ("portfolio", {})):
            res = api.call(method, as_of="2099-01-01T00:00:00Z", **kw)
            self.assertFalse(res["ok"])
            self.assertIn("future", res["error"]["message"])
        self.assertEqual(api.call("experiment_report", experiment_id=exp)["data"]["phase"], "INTERIM")

    def test_c2_submission_cannot_be_backdated(self):
        lab, api, exp = mklab(key="bd")
        r = enrol(lab, exp, 0, key="bd", submit=False)
        tok = simulated_user_grant(lab.ledger, ap.SUBMIT_APPLICATION, "app-bd-000",
                                   lab.package_hash("app-bd-000", r["variant_id"]))
        api.call("approve_package", application_id="app-bd-000", resume_variant_id=r["variant_id"], token_id=tok)
        res = api.call("record_user_submission", application_id="app-bd-000", occurred_at="2026-01-01T00:00:00Z")
        self.assertEqual(res["error"]["type"], "WorkflowError")
        sub = api.call("record_user_submission", application_id="app-bd-000")["data"]
        amend = api.call("correct_event", target_event_id=sub["id"], action="amend", reason="backdate",
                         changes={"occurred_at": "2026-01-01T00:00:00Z"})
        self.assertEqual(amend["error"]["type"], "CorrectionError")

    def test_h2_unsubmitted_drafts_keep_the_experiment_interim_until_their_deadline(self):
        lab, api, exp = mklab(key="dr")
        for i in range(30):
            enrol(lab, exp, i, key="dr", submit=(i < 27))
        lab.clock.advance(days=10)
        rep = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertEqual(rep["phase"], "INTERIM")
        lab.clock.advance(days=40)
        final = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertIn(final["phase"], ("FINAL",))
        self.assertIn("never_exposed", final["outcome_data_flags"])


class TestAllocation(unittest.TestCase):
    def test_c3_next_arm_is_not_predictable_and_selective_enrolment_does_not_inflate_claims(self):
        V, sims, claims, hits, trials = ["control", "treatment"], 150, 0, 0, 0
        for s in range(sims):
            rng = random.Random(s)
            units, lead = [], 0
            for i in range(80):
                guess_treatment = lead < 0 if lead != 0 else rng.random() < 0.5
                arm = unit_variant(f"seed{s}", "e", "0" * 64, f"u{s}-{i}", V)
                if lead != 0:
                    trials += 1
                    hits += (arm == "treatment") == guess_treatment
                lead += 1 if arm == "treatment" else -1
                y = float(rng.random() < (0.5 if guess_treatment else 0.1))  # warm lead when treatment is "due"
                units.append({"arm": arm, "stratum": "s", "apps": ["success" if y else "no_success"], "y": y,
                              "y_complete": y, "n_missing": 0})
            res = infer(units, variants=V, control="control", minimum_units=10, alpha=0.05, n_perm=199,
                        seed_key=f"k{s}", metric="m", unit_label="u")
            claims += bool(res["claims"])
        self.assertLess(abs(hits / trials - 0.5), 0.03)  # block design gave 0.90
        self.assertLess(claims / sims, 0.11)  # block design gave 0.35; nominal 0.05

    def test_designs_too_small_for_simple_randomisation_are_refused(self):
        lab = Lab(clock=FixedClock(simulate.START))
        spec = {"id": "exp-small", "title": "T", "hypothesis": "H", "variants": ["control", "treatment"],
                "assignment_unit": "application", "strata": [], "primary_metric": "recruiter_screen",
                "stopping_rule": "fixed", "consent_required": False, "outcome_window_days": 30,
                "analysis_plan": "ITT"}
        design = make_design(question="Q?", eligibility_description="d", control="control",
                             variant_definitions={"control": "c", "treatment": "t"}, strata_levels={},
                             planned_units_per_arm=10, assumed_baseline=0.2, minimum_detectable_effect=0.5)
        with self.assertRaises(ValidationError):
            lab.registry.create_draft(spec, design)


class TestFrozenEnrolment(unittest.TestCase):
    def test_h1_no_application_can_join_after_the_final_analysis(self):
        lab, api, exp = mklab(key="cl", unit="employer-cluster", icc=0.2, cluster_size=(1, 2))
        arms = {i: enrol(lab, exp, i, key="cl", employer=f"emp-cl-{i:02d}")["arm"] for i in range(30)}
        lab.clock.advance(days=31)
        first = lab.report(exp)
        self.assertEqual(first["phase"], "FINAL")
        self.assertIsNotNone(lab.registry.final_analysis(exp))
        i = next(k for k, v in arms.items() if v == "treatment")
        with self.assertRaises(AssignmentError):
            enrol(lab, exp, 100 + i, key="cl", employer=f"emp-cl-{i:02d}")

    def test_h1_cluster_units_close_one_window_after_their_first_assignment(self):
        lab, api, exp = mklab(key="cw", unit="employer-cluster", icc=0.2, cluster_size=(1, 2))
        enrol(lab, exp, 0, key="cw", employer="emp-cw-00")
        lab.clock.advance(days=31)
        with self.assertRaises(AssignmentError):
            enrol(lab, exp, 1, key="cw", employer="emp-cw-00")

    def test_result_of_record_is_stable_and_later_changes_are_exploratory(self):
        lab, api, exp = mklab(key="rr")
        for i in range(30):
            enrol(lab, exp, i, key="rr")
        lab.clock.advance(days=31)
        first = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertTrue(first["result_of_record"]["unchanged"])
        lab.clock.advance(days=5)
        again = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertEqual(again["as_of"], first["as_of"])  # always computed at the recorded instant
        self.assertEqual(again["verdict"], first["verdict"])
        late = api.call("record_response", application_id="app-rr-000", event_type="recruiter_screen",
                        occurred_at=first["as_of"].replace("T", "T").replace(first["as_of"][11:13], "00"))
        self.assertTrue(late["ok"])
        after = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertEqual(after["verdict"], "EXPLORATORY_NO_CLAIM")
        self.assertFalse(after["result_of_record"]["unchanged"])
        self.assertEqual(after["result_of_record"]["verdict"], first["verdict"])
        self.assertIn("DATA_CHANGED_AFTER_FINAL_ANALYSIS", after["reasons_withheld"][0])

    def test_l5_override_cannot_be_used_before_the_final_analysis(self):
        lab, api, exp = mklab(key="ov")
        for i in range(5):
            enrol(lab, exp, i, key="ov")
        with self.assertRaises(Exception):
            lab.report(exp, override={"outcome_window_days": 1})


class TestHistoryAttacks(unittest.TestCase):
    def test_h4_voiding_exposure_does_not_make_an_application_assignable(self):
        lab, api, exp = mklab(key="vx")
        job = synthetic.job("fake-vx")
        r = submit_application(lab, app_id="vx1", job=job, employer_id="emp-vx", exp_id=None, attributes=ATTR,
                               claims=synthetic.claims())
        ev = api.call("record_response", application_id="vx1", event_type="recruiter_screen")["data"]
        sub = [e for e in lab.store.raw_events("vx1") if e["event_type"] == "submitted"][0]
        api.call("correct_event", target_event_id=ev["id"], action="void", reason="x")
        api.call("correct_event", target_event_id=sub["id"], action="void", reason="x")
        lab.store.append_event(application_id="vx1", job_id=job["id"], event_type="drafted", actor="system",
                               source="system:x", outcome_status="observed", resume_variant_id=r["variant_id"])
        with self.assertRaises(AssignmentError):
            lab.engine.assign(experiment_id=exp, application_id="vx1", attributes=ATTR)

    def test_m1_claims_that_exist_only_because_of_corrections_are_withheld(self):
        lab, api, exp = mklab(key="co", n=20)
        arms = {i: enrol(lab, exp, i, key="co")["arm"] for i in range(40)}
        lab.clock.advance(days=2)
        evs = {i: api.call("record_response", application_id=f"app-co-{i:03d}",
                           event_type="recruiter_screen")["data"]["id"] for i in range(40)}
        for i, arm in arms.items():
            if arm == "control":
                api.call("correct_event", target_event_id=evs[i], action="amend", reason="unsure",
                         changes={"outcome_status": "unknown"})
        lab.clock.advance(days=40)
        rep = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertEqual(rep["phase"], "FINAL")
        self.assertIsNone(rep["claim"])
        self.assertTrue(any("NOT_ROBUST_TO_CORRECTIONS" in r for r in rep["reasons_withheld"]))

    def test_m4_reserved_event_ids(self):
        lab, api, exp = mklab(key="id")
        lab.store.open_application(application_id="b1", job_id="fake-b1", employer_id="emp-b1")
        with self.assertRaises(ValidationError):
            lab.store.append_event(application_id="b1", job_id="fake-b1", event_type="note", actor="user",
                                   source="user:x", outcome_status="observed", event_id="evt-0000002")
        lab.store.open_application(application_id="b2", job_id="fake-b2", employer_id="emp-b2")  # still works


class TestAliasingAndIntegrity(unittest.TestCase):
    def test_h3_read_models_cannot_alter_stored_variants_or_specs(self):
        lab, api, exp = mklab(key="al")
        claims = synthetic.claims()
        v = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=synthetic.job("fake-al"), claims=claims)
        got = api.call("get_resume_variant", variant_id=v["id"])["data"]
        got["bullets"][0]["text"] = "CEO of Fictional Megacorp; PhD; 40 patents"
        lab.resumes.get(v["id"])["bullets"][0]["text"] = "also mutated"
        tok = simulated_user_grant(lab.ledger, ap.EXPORT_RESUME, v["id"], v["content_hash"])
        out = api.call("export_resume", variant_id=v["id"], fmt="txt", token_id=tok, current_claims=claims)
        self.assertTrue(out["ok"])
        self.assertNotIn("Megacorp", out["data"]["document"])
        self.assertNotIn("mutated", out["data"]["document"])
        spec = api.call("get_experiment", experiment_id=exp)["data"]["spec"]
        spec["variants"].sort(reverse=True)
        self.assertTrue(lab.registry.verify(exp))

    def test_h3_a_variant_whose_content_no_longer_matches_its_hash_is_refused(self):
        lab, api, exp = mklab(key="hh")
        v = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=synthetic.job("fake-hh"),
                                      claims=synthetic.claims())
        lab.resumes._variants[v["id"]]["bullets"][0]["text"] = "tampered in memory"
        with self.assertRaises(IntegrityError):
            lab.resumes.get(v["id"])

    def test_m3_hostile_inputs_return_error_envelopes(self):
        lab, api, exp = mklab(key="er")
        cases = [("experiment_report", {"experiment_id": exp, "as_of": "0001-01-01T00:00:00+23:59"}),
                 ("record_response", {"application_id": "x", "event_type": "rejected",
                                      "occurred_at": "0001-01-01T00:00:00+23:59"}),
                 ("correct_event", {"target_event_id": "evt-0000000", "action": "amend", "reason": "r",
                                    "changes": "abc"}),
                 ("export_resume", {"variant_id": "rv-x", "fmt": "txt", "token_id": "t",
                                    "current_claims": [{**synthetic.claims()[0], "claim_text": float("nan")}]})]
        for method, kw in cases:
            res = api.call(method, **kw)
            self.assertFalse(res["ok"], method)
        lab.store.open_application(application_id="s1", job_id="fake-s1", employer_id="emp-s1")
        res = api.call("record_response", application_id="s1", event_type="rejected", notes="\ud800 lone surrogate")
        self.assertIn("ok", res)  # either refused or stored cleaned, never an exception

    def test_m5_ledger_truncation_on_disk_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d, clock=FixedClock(simulate.START))
            v = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=synthetic.job("fake-m5"),
                                          claims=synthetic.claims())
            tok = simulated_user_grant(lab.ledger, ap.EXPORT_RESUME, v["id"], v["content_hash"])
            lab.resumes.export(v["id"], fmt="txt", token_id=tok, current_claims=synthetic.claims())
            self.assertTrue(lab.verify_all())
            self.assertTrue(Lab(d, clock=FixedClock(simulate.START)).verify_all())
            p = Path(d) / "approvals.jsonl"
            lines = p.read_text(encoding="utf-8").splitlines()
            p.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                lab.verify_all()
            with self.assertRaises(IntegrityError):
                Lab(d, clock=FixedClock(simulate.START)).verify_all()


class TestLowFindings(unittest.TestCase):
    def test_l1_bullets_are_single_line_and_markdown_cannot_start_lists_or_links(self):
        lab, api, exp = mklab(key="lo")
        c = synthetic.claim("c-x", "Shipped a fictional API\n- Fictional VP \n\n1. See https://evil.invalid/x")
        v = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=synthetic.job("fake-lo", skills=[]), claims=[c])
        txt, md = lab.resumes.render(v["id"], "txt"), lab.resumes.render(v["id"], "md")
        self.assertEqual(txt.count("\n"), 1)
        self.assertEqual(md.count("\n"), 1)
        self.assertNotIn("https://", md)
        self.assertNotIn(" ", txt)

    def test_l2_blank_evidence_ids_are_not_evidence(self):
        blank = synthetic.claim("c-b", "x", evidence=["", "  "])
        self.assertEqual(claim_refusal(blank, synthetic.SUBJECT, parse_ts(simulate.START)),
                         "supported_without_evidence")

    def test_l3_t_interval_uses_satterthwaite_df(self):
        rng = random.Random(1)
        misses = 0
        for _ in range(400):
            t = [rng.gauss(0, 1) for _ in range(4)]
            c = [rng.gauss(0, 0.1) for _ in range(30)]
            ci = stats.stratified_t_interval([(t, c)])
            misses += not (ci["low"] <= 0 <= ci["high"])
        self.assertLess(misses / 400, 0.09)  # was 0.13 with pooled df


if __name__ == "__main__":
    unittest.main()
