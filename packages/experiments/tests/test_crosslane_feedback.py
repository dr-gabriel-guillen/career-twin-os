"""Regression tests for review findings from other lanes on #3 (A: sidecar envelopes; E: B-F1, B-F3)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import _bootstrap  # noqa: E402,F401
import e2e_stage  # noqa: E402
from ct_applications import synthetic  # noqa: E402
from ct_applications.core import FixedClock, ValidationError  # noqa: E402
from ct_experiments import simulate  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.registry import make_design  # noqa: E402
from test_integration_stages import envelope  # noqa: E402


def write(d: Path, name: str, payload) -> None:
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


class TestLaneASidecar(unittest.TestCase):
    """A on #3: in E's pipeline jobs.json holds bare postings and jobs_ext.json the envelopes."""

    def test_sidecar_envelopes_are_used_for_identical_postings_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            envs = [envelope(i, members=[f"jp_{i:020d}", f"jp_mirror_{i}"]) for i in range(6)]
            write(d, "jobs.json", [e["posting"] for e in envs])
            tampered = json.loads(json.dumps(envs))
            tampered[1]["posting"]["title"] = "Changed title"          # no longer byte-equal: ignored
            tampered[1]["ext"]["employer"]["id"] = "emp_injected"
            write(d, "jobs_ext.json", tampered)
            jobs = {j["posting"]["id"]: j for j in e2e_stage.load_jobs(d)}
            good, bad = jobs[envs[0]["posting"]["id"]], jobs[envs[1]["posting"]["id"]]
            self.assertEqual(good["employer_source"], "lane-a:ext.employer.id")
            self.assertEqual(good["members"], [envs[0]["posting"]["id"], "jp_mirror_0"])
            self.assertEqual(bad["employer_source"], "fallback:organization-hash")
            self.assertEqual(bad["members"], [])
            self.assertNotEqual(bad["employer_id"], "emp_injected")

    def test_malformed_sidecar_is_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            write(d, "jobs.json", [synthetic.job("a")])
            write(d, "jobs_ext.json", {"not": "a list"})
            with self.assertRaises(e2e_stage.BadInput):
                e2e_stage.load_jobs(d)


class TestInputDrift(unittest.TestCase):
    """E B-F1: rerunning experiment.run on different jobs must not silently return the old experiment."""

    def test_changed_inputs_are_refused_and_identical_inputs_reproduce(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            jobs = [envelope(i) for i in range(40)]
            write(d, "jobs.json", jobs)
            write(d, "claims.json", synthetic.claims())
            write(d, "pin.json", {"job_id": jobs[0]["posting"]["id"]})
            run = lambda: e2e_stage.main(["experiment", "--artifacts", str(d), "--seed", "4"])  # noqa: E731
            self.assertEqual(run(), 0)
            first = (d / "analysis.json").read_text(encoding="utf-8")
            self.assertEqual(run(), 0)  # same inputs: same journaled experiment
            self.assertEqual((d / "analysis.json").read_text(encoding="utf-8"), first)
            write(d, "jobs.json", jobs[::2])
            self.assertEqual(run(), 2)  # InputDrift
            self.assertEqual((d / "analysis.json").read_text(encoding="utf-8"), first)


class TestStrataNames(unittest.TestCase):
    """E B-F3: stratum keys 'f=level|g=level' must stay injective."""

    def test_separator_characters_are_refused(self):
        lab = Lab(clock=FixedClock(simulate.START))
        spec = {"id": "exp-sep", "title": "T", "hypothesis": "H", "variants": ["control", "treatment"],
                "assignment_unit": "application", "strata": ["a"], "primary_metric": "recruiter_screen",
                "stopping_rule": "fixed", "consent_required": False, "outcome_window_days": 30, "analysis_plan": "ITT"}
        for levels in (["x|b=y", "z"], ["x=1", "y"], ["", "y"]):
            design = make_design(question="Q?", eligibility_description="d", control="control",
                                 variant_definitions={"control": "c", "treatment": "t"}, strata_levels={"a": levels},
                                 planned_units_per_arm=20, assumed_baseline=0.2, minimum_detectable_effect=0.4)
            with self.assertRaises(ValidationError, msg=levels):
                lab.registry.create_draft({**spec, "id": f"exp-sep-{len(levels[0])}"}, design)


if __name__ == "__main__":
    unittest.main()
