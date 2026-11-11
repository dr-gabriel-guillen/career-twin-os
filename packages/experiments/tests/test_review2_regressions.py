"""Regression tests for the SECOND adversarial review of lane B (code at ea01acb).

Findings C-1, H-1..H-3, M-1..M-4, L-a, L-b, L-d, L-e. Each test replays the reviewer's attack.
"""
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import _bootstrap  # noqa: E402,F401
import e2e_stage  # noqa: E402
from ct_applications import synthetic  # noqa: E402
from ct_applications.core import FixedClock, content_hash, fmt_ts, parse_ts  # noqa: E402
from ct_applications.journal import IntegrityError, Journal  # noqa: E402
from ct_experiments.analysis import _analyze_core  # noqa: E402
from ct_experiments.assignment import AssignmentError, CrossoverError, IneligibleError  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.simulate import submit_application  # noqa: E402
from test_integration_stages import envelope  # noqa: E402
from test_review_regressions import ATTR, enrol, mklab  # noqa: E402


def respond(api, app, kind="recruiter_screen", at=None):
    res = api.call("record_response", application_id=app, event_type=kind, occurred_at=at)
    assert res["ok"], res
    return res["data"]


class TestOutcomeDependentEnrolment(unittest.TestCase):
    def test_c1_a_unit_closes_at_its_first_exposure(self):
        lab, api, exp = mklab(key="g1", unit="employer-cluster", icc=0.2, cluster_size=(1, 3))
        enrol(lab, exp, 0, key="g1", employer="emp-g1")  # submitted
        with self.assertRaises(AssignmentError):
            enrol(lab, exp, 1, key="g1", employer="emp-g1")
        for i in (10, 11):  # applications enrolled together before any exposure are allowed
            lab.clock.advance(hours=1)
            lab.store.open_application(application_id=f"x{i}", job_id=f"fake-x{i}", employer_id="emp-g1b")
            lab.engine.assign(experiment_id=exp, application_id=f"x{i}", attributes=ATTR)

    def test_h3_one_posting_one_application_for_every_unit_kind(self):
        for unit in ("job", "employer-cluster"):
            clustered = unit == "employer-cluster"
            lab, api, exp = mklab(key=f"h3{unit[:3]}", unit=unit, icc=0.2 if clustered else 0.0,
                                  cluster_size=(1, 2) if clustered else (1, 1))
            lab.store.open_application(application_id="a1", job_id="jp_one", employer_id="emp-1",
                                       job_members=["jp_one", "jp_mirror"])
            lab.engine.assign(experiment_id=exp, application_id="a1", attributes=ATTR)
            lab.store.open_application(application_id="a2", job_id="jp_mirror", employer_id="emp-2")
            with self.assertRaises(CrossoverError):
                lab.engine.assign(experiment_id=exp, application_id="a2", attributes=ATTR)
            self.assertTrue(lab.verify_all())  # nothing was journaled that verify() would reject

    def test_le_posting_already_applied_to_outside_the_experiment_is_ineligible(self):
        lab, api, exp = mklab(key="le")
        submit_application(lab, app_id="out-1", job=synthetic.job("fake-le-1"), employer_id="emp-le",
                           exp_id=None, attributes=ATTR, claims=synthetic.claims())
        lab.store.open_application(application_id="in-1", job_id="fake-le-1", employer_id="emp-le2")
        with self.assertRaises(IneligibleError):
            lab.engine.assign(experiment_id=exp, application_id="in-1", attributes=ATTR)


class TestCorrectionLaundering(unittest.TestCase):
    def test_h1_void_then_record_cannot_launder_a_claim(self):
        lab, api, exp = mklab(key="mb", n=20)
        arms = {i: enrol(lab, exp, i, key="mb")["arm"] for i in range(40)}
        lab.clock.advance(days=2)
        rej = {i: respond(api, f"app-mb-{i:03d}", "rejected")["id"] for i in range(40)}
        lab.clock.advance(days=1)
        for i, arm in arms.items():
            if arm == "treatment":
                api.call("correct_event", target_event_id=rej[i], action="void", reason="mis-click")
                respond(api, f"app-mb-{i:03d}", at=fmt_ts(lab.clock.now() - timedelta(days=1)))
        lab.clock.advance(days=40)
        rep = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertIsNone(rep["claim"])
        self.assertTrue(any("NOT_ROBUST_TO_CORRECTIONS" in r for r in rep["reasons_withheld"]))

    def test_m2_corrections_that_flip_the_direction_are_caught(self):
        lab, api, exp = mklab(key="fl", n=20)
        arms, sub_at = {}, {}
        for i in range(40):
            arms[i] = enrol(lab, exp, i, key="fl")["arm"]
            sub_at[i] = lab.clock.now()
        lab.clock.advance(days=40)
        ev = {}
        for i, arm in arms.items():
            at = sub_at[i] + timedelta(days=5 if arm == "treatment" else 35)
            ev[i] = respond(api, f"app-fl-{i:03d}", at=fmt_ts(at))["id"]
        for i, arm in arms.items():
            new = sub_at[i] + timedelta(days=35 if arm == "treatment" else 5)
            api.call("correct_event", target_event_id=ev[i], action="amend", reason="wrong date",
                     changes={"occurred_at": fmt_ts(new)})
        lab.clock.advance(days=1)
        rep = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertIsNone(rep["claim"])
        self.assertTrue(any("NOT_ROBUST_TO_CORRECTIONS" in r for r in rep["reasons_withheld"]))


class TestResultOfRecord(unittest.TestCase):
    def test_h2_an_appended_result_of_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            lab, api, exp = mklab(key="fg", root=d)
            for i in range(30):
                enrol(lab, exp, i, key="fg")
            lab.clock.advance(days=40)
            real = api.call("experiment_report", experiment_id=exp)["data"]
            now = lab.clock.now()
            cur = _analyze_core(exp, registry=lab.registry, engine=lab.engine, store=lab.store,
                                as_of=parse_ts(real["as_of"]))
            fake = dict(cur, verdict="EVIDENCE_FOR_DIFFERENCE", claim="forged")
            at = real["result_of_record"]["recorded_at"]
            Journal(Path(d) / "experiments.jsonl", name="experiments").append(
                "final_analysis", {"id": exp, "as_of": real["as_of"], "report_hash": content_hash(cur),
                                   "report": fake, "recorded_at": at}, at)
            with self.assertRaises(IntegrityError):
                Lab(d, clock=FixedClock(now))

    def test_h2_a_stored_body_that_no_longer_matches_its_hash_is_refused(self):
        lab, api, exp = mklab(key="rb")
        for i in range(30):
            enrol(lab, exp, i, key="rb")
        lab.clock.advance(days=40)
        self.assertTrue(api.call("experiment_report", experiment_id=exp)["ok"])
        lab.registry._records[exp]["final_analysis"]["report"]["claim"] = "edited in memory"
        self.assertFalse(api.call("experiment_report", experiment_id=exp)["ok"])

    def test_m1_m3_la_reports_before_the_record_are_computed_now_only_and_fully_blinded(self):
        lab, api, exp = mklab(key="pa")
        for i in range(30):
            enrol(lab, exp, i, key="pa")
        past = fmt_ts(lab.clock.now() - timedelta(hours=2))
        soon = fmt_ts(lab.clock.now() + timedelta(seconds=30))
        for as_of in (past, soon):
            self.assertFalse(api.call("experiment_report", experiment_id=exp, as_of=as_of)["ok"], as_of)
        self.assertFalse(api.call("portfolio", as_of=past)["ok"])
        interim = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertEqual(interim["phase"], "INTERIM")
        for arm in interim["flow"]["by_arm"].values():
            for key in ("success", "no_success", "censored", "missing", "withdrawn", "deviations"):
                self.assertIsNone(arm[key])
        self.assertNotIn("counted_outcomes", interim)

    def test_lb_sub_second_clock_keeps_the_record_stable(self):
        lab, api, exp = mklab(key="ss")
        for i in range(30):
            enrol(lab, exp, i, key="ss")
        lab.clock.advance(days=40, microseconds=654321)
        first = api.call("experiment_report", experiment_id=exp)["data"]
        lab.clock.advance(days=1)
        again = api.call("experiment_report", experiment_id=exp)["data"]
        self.assertTrue(again["result_of_record"]["unchanged"])
        self.assertEqual(again["verdict"], first["verdict"])


class TestE2EStageRobustness(unittest.TestCase):
    @staticmethod
    def run_stage(d, stage, seed=3):
        return e2e_stage.main([stage, "--artifacts", str(d), "--seed", str(seed)])

    def test_m4_malformed_inputs_exit_2_and_leave_no_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "claims.json").write_text(json.dumps(synthetic.claims()), encoding="utf-8")
            (d / "pin.json").write_text('{"job_id": "x"}', encoding="utf-8")
            bad_jobs = ["5", "null",
                        json.dumps([{"posting": synthetic.job("a"), "ext": 3}]),
                        json.dumps([synthetic.job("a"), synthetic.job("a")])]
            for jobs in bad_jobs:
                (d / "jobs.json").write_text(jobs, encoding="utf-8")
                self.assertEqual(self.run_stage(d, "experiment"), 2, jobs[:40])
                self.assertFalse((d / "lane-b").exists())
                self.assertFalse((d / "lane-b.partial").exists())
            not_a_dir = d / "a-file"
            not_a_dir.write_text("x", encoding="utf-8")
            self.assertEqual(self.run_stage(not_a_dir, "resume"), 2)

    def test_ld_reusing_an_artifacts_directory_for_another_seed_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            jobs = [envelope(i) for i in range(40)]
            (d / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
            (d / "claims.json").write_text(json.dumps(synthetic.claims()), encoding="utf-8")
            (d / "pin.json").write_text(json.dumps({"job_id": jobs[0]["posting"]["id"]}), encoding="utf-8")
            self.assertEqual(self.run_stage(d, "experiment", seed=1), 0)
            before = (d / "analysis.json").read_text(encoding="utf-8")
            self.assertEqual(self.run_stage(d, "experiment", seed=2), 2)
            self.assertEqual((d / "analysis.json").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
