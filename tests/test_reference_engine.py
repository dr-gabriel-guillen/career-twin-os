import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from reference_engine import score_job, assign_variant, skill_gap


class TestReferenceEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((ROOT / "fixtures/synthetic_profile.json").read_text())
        cls.jobs = json.loads((ROOT / "fixtures/synthetic_jobs.json").read_text())

    def test_no_probability_claim(self):
        result = score_job(self.profile, self.jobs[0])
        self.assertIn("not_hiring_probability", result["score_type"])
        self.assertTrue(0 <= result["score"] <= 100)

    def test_gap_and_overlap(self):
        gap = skill_gap(self.profile, self.jobs[0])
        self.assertIn("customer-delivery", gap["missing_skills"])
        self.assertIn("python", gap["already_evidenced"])

    def test_assignment_is_deterministic(self):
        xs = [assign_variant("a", "s", "u", ["A", "B"], "secret") for _ in range(100)]
        self.assertEqual(len(set(xs)), 1)

    def test_assignment_uses_stratum(self):
        self.assertIn(assign_variant("a", "other", "u", ["A", "B"], "secret"), ["A", "B"])

    def test_reject_bad_variants(self):
        for options in [["A"], ["A", "A"], []]:
            with self.assertRaises(ValueError):
                assign_variant("a", "s", "u", options, "salt")

    def test_compensation_not_fabricated(self):
        result = score_job(self.profile, self.jobs[1])
        self.assertIn("compensation_verified", result["unknown_inputs"])


if __name__ == "__main__":
    unittest.main()
