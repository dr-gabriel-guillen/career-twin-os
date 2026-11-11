"""Lane E integration stages (tools/e2e_stage.py) and lane A interface semantics (#2)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
import _bootstrap  # noqa: E402,F401
from ct_applications import schema, synthetic  # noqa: E402
from ct_applications.core import FixedClock  # noqa: E402
from ct_experiments import simulate  # noqa: E402
from ct_experiments.assignment import CrossoverError, DoubleEnrollmentError  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.simulate import Scenario  # noqa: E402

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import e2e_stage  # noqa: E402

ATTR = {"seniority": "senior", "role_family": "fde"}


def envelope(i, employer=None, members=None, status="active"):
    """Shape of lane A's job records: {posting: <frozen JobPosting>, ext: {employer, dedup}}."""
    posting = synthetic.job(f"jp_{i:020d}", org=f"Fictional Employer {i % 50:02d}",
                            seniority="senior" if i % 3 else "staff")
    posting["status"] = status
    return {"posting": posting,
            "ext": {"employer": {"id": employer or f"emp_{i % 50:016d}"},
                    "dedup": {"members": members or [posting["id"]]}}}


class TestLaneAInterface(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(clock=FixedClock(simulate.START))
        self.exp = simulate.register(self.lab, Scenario("dd", "D", "n/a", n_per_arm=20, mde=0.4))
        self.exp2 = simulate.register(self.lab, Scenario("dd2", "D2", "n/a", n_per_arm=20, mde=0.4))

    def test_duplicate_postings_cannot_cross_over(self):
        """A #2 point 1: the same job on two boards has two ids; overlap of dedup members means same posting."""
        store = self.lab.store
        store.open_application(application_id="a1", job_id="jp_greenhouse", employer_id="emp_x",
                               job_members=["jp_greenhouse", "jp_lever"])
        self.lab.engine.assign(experiment_id=self.exp, application_id="a1", attributes=ATTR)
        store.open_application(application_id="a2", job_id="jp_lever", employer_id="emp_x2",
                               job_members=["jp_lever"])
        with self.assertRaises(CrossoverError):
            self.lab.engine.assign(experiment_id=self.exp, application_id="a2", attributes=ATTR)
        store.open_application(application_id="a3", job_id="jp_lever", employer_id="emp_x3", job_members=[])
        with self.assertRaises(DoubleEnrollmentError):
            self.lab.engine.assign(experiment_id=self.exp2, application_id="a3", attributes=ATTR)
        self.assertEqual(store.meta("a1")["job_members"], ["jp_greenhouse", "jp_lever"])  # snapshot kept


class TestStages(unittest.TestCase):
    def write_inputs(self, d, n_jobs=60, claims=None, pin=None):
        jobs = [envelope(i) for i in range(n_jobs)]
        claims = claims if claims is not None else synthetic.claims()
        (d / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (d / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
        (d / "pin.json").write_text(json.dumps({"job_id": pin or jobs[0]["posting"]["id"]}), encoding="utf-8")
        return jobs

    def run_stage(self, d, stage, seed=3):
        return e2e_stage.main([stage, "--artifacts", str(d), "--seed", str(seed)])

    def test_three_stages_produce_contract_valid_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            jobs = self.write_inputs(d)
            for stage in ("resume", "application", "experiment"):
                self.assertEqual(self.run_stage(d, stage), 0, stage)
            variant = json.loads((d / "resume_variant.json").read_text(encoding="utf-8"))
            self.assertEqual(variant["job_id"], jobs[0]["posting"]["id"])
            claims = {c["id"]: c for c in synthetic.claims()}
            for b in variant["bullets"]:
                self.assertIn(claims[b["claim_id"]]["status"], ("supported", "self-reported"))
            events = json.loads((d / "events.json").read_text(encoding="utf-8"))
            self.assertEqual([e["event_type"] for e in events], ["saved", "drafted", "approved", "submitted"])
            sub = events[-1]
            self.assertEqual((sub["actor"], sub["resume_variant_id"]), ("user", variant["id"]))
            self.assertTrue(sub["approval_token_ref"])
            for e in events + json.loads((d / "experiment_events.json").read_text(encoding="utf-8")):
                schema.validate(e, "application-event")
            schema.validate(json.loads((d / "experiment.json").read_text(encoding="utf-8")), "experiment")
            assignments = json.loads((d / "assignments.json").read_text(encoding="utf-8"))
            self.assertEqual(len({a["design_hash"] for a in assignments}), 1)
            self.assertFalse(any("consent_token_ref" in a for a in assignments))
            analysis = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["phase"], "FINAL")
            self.assertNotEqual(analysis["verdict"], "EVIDENCE_FOR_DIFFERENCE")  # A/A by construction
            counted = {r["counted_event_id"] for r in analysis["counted_outcomes"] if r["counted_event_id"]}
            event_ids = {e["id"] for e in json.loads((d / "experiment_events.json").read_text(encoding="utf-8"))}
            self.assertTrue(counted <= event_ids)
            self.assertNotIn("lane-b-e2e-secret", (d / "analysis.json").read_text(encoding="utf-8"))  # seed never written out
            # stages are idempotent: re-running the application stage records nothing new
            self.assertEqual(self.run_stage(d, "application"), 0)
            self.assertEqual(json.loads((d / "events.json").read_text(encoding="utf-8")), events)

    def test_output_depends_on_inputs(self):
        outs = []
        for n in (60, 30):
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                self.write_inputs(d, n_jobs=n)
                self.assertEqual(self.run_stage(d, "experiment"), 0)
                outs.append((d / "assignments.json").read_text(encoding="utf-8"))
        self.assertNotEqual(*outs)

    def test_refusals_and_bad_input_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            private = [dict(c, visibility="private") for c in synthetic.claims()]
            self.write_inputs(d, claims=private)
            self.assertEqual(self.run_stage(d, "resume"), 3)  # no shareable claim: refused, not faked
            self.assertFalse((d / "resume_variant.json").exists())
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.write_inputs(d, pin="jp_not_in_jobs")
            self.assertEqual(self.run_stage(d, "resume"), 2)
            (d / "jobs.json").write_text('[{"posting": {"id": "x"}}]', encoding="utf-8")
            self.assertEqual(self.run_stage(d, "resume"), 3)  # contract violation in A's payload
            self.write_inputs(d, n_jobs=10)
            self.assertEqual(self.run_stage(d, "experiment"), 2)  # too few units for any valid experiment


if __name__ == "__main__":
    unittest.main()
