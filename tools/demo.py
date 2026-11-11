"""No external calls. Run: python tools/demo.py"""
import json
from pathlib import Path
from reference_engine import score_job, assign_variant, skill_gap

ROOT = Path(__file__).resolve().parents[1]
profile = json.loads((ROOT / "fixtures/synthetic_profile.json").read_text())
jobs = json.loads((ROOT / "fixtures/synthetic_jobs.json").read_text())
ranked = sorted((score_job(profile, j) for j in jobs), key=lambda x: x["score"], reverse=True)
result = {
    "ranking": ranked,
    "experiment_assignment": assign_variant("exp-01", "fde-senior", "employer-fake-a", ["A", "B"], "synthetic-seed"),
    "skill_gap_to_field_cto": skill_gap(profile, next(j for j in jobs if j["role_family"] == "field-cto"))
}
print("SYNTHETIC CAREERTWIN DEMO — all employers and jobs are fictional.")
print(json.dumps(result, indent=2))
