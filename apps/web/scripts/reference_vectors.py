"""Generate golden vectors from the FROZEN reference engine on main.

Run from apps/web:  python scripts/reference_vectors.py
Reads tools/reference_engine.py and fixtures/*.json at the repo root (read-only)
and writes tests/unit/golden/reference-vectors.json. No network, no clock.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
from reference_engine import assign_variant, score_job, skill_gap  # noqa: E402

profile = json.loads((ROOT / "fixtures/synthetic_profile.json").read_text(encoding="utf-8"))
jobs = json.loads((ROOT / "fixtures/synthetic_jobs.json").read_text(encoding="utf-8"))

assign_cases = []
variants_sets = [["A", "B"], ["impact-first", "skills-first"], ["a", "b", "c"], ["x", "y", "z", "w", "v"]]
for i in range(60):
    variants = variants_sets[i % len(variants_sets)]
    args = {
        "experiment_id": f"exp-{i % 7}",
        "stratum": ["fde", "ai-architect", "", "senior|remote", "rôle-família"][i % 5],
        "unit_id": f"employer-{i}-é中",
        "variants": variants,
        "salt": ["synthetic-seed", "careertwin-synthetic-fixture-salt-v1", "s␟p"][i % 3],
    }
    assign_cases.append({**args, "expected": assign_variant(args["experiment_id"], args["stratum"], args["unit_id"], variants, args["salt"])})

sha_cases = [{"text": t, "sha256": hashlib.sha256(t.encode("utf-8")).hexdigest()}
             for t in ["", "abc", "a" * 55, "a" * 56, "a" * 64, "a" * 119, "a" * 1000, "résumé 中文 \U0001F600"]]

out = {
    "source": "tools/reference_engine.py on main (frozen baseline)",
    "reference_engine_sha256": hashlib.sha256((ROOT / "tools/reference_engine.py").read_bytes()).hexdigest(),
    "assign": assign_cases,
    "sha256": sha_cases,
    "score_job": [score_job(profile, j) for j in jobs],
    "skill_gap": [skill_gap(profile, j) for j in jobs],
    "profile_skills": profile["skills"],
}
target = Path(__file__).resolve().parents[1] / "tests/unit/golden/reference-vectors.json"
target.write_text(json.dumps(out, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
print(f"wrote {target.relative_to(ROOT)}: {len(assign_cases)} assign, {len(sha_cases)} sha256, {len(jobs)} score_job")
