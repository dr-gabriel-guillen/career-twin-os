"""Regenerate the lane B versioned synthetic fixtures (offline, deterministic).

    python packages/experiments/tools/build_fixtures.py            # full (a few minutes)
    python packages/experiments/tools/build_fixtures.py --quick    # skip calibration + held-out cohorts

Everything written is fictional. Output: packages/experiments/fixtures/v1/*.json + MANIFEST.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
for pkg in ("applications", "experiments"):
    sys.path.insert(0, str(HERE.parents[1] / pkg))

from ct_applications import approvals as ap, synthetic  # noqa: E402
from ct_applications.core import FixedClock, fmt_ts, sha256_hex  # noqa: E402
from ct_experiments import API_VERSION, simulate  # noqa: E402
from ct_experiments.adapter import LaneBApi  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.outcomes import application_outcome  # noqa: E402

COHORT_SEED, HELDOUT_SEED = 20261110, 911
_FINAL = [s for s in simulate.scenarios() if s.enrol_fraction == 1.0 and s.analyze_after_days >= simulate.WINDOW]
NULL_KEYS = [s.key for s in _FINAL if not any(s.effects.values())]
EFFECT_KEYS = [s.key for s in _FINAL if any(abs(e) >= 0.2 for e in s.effects.values())]


def roundtrip() -> dict:
    """One complete resume -> application -> assignment -> outcome trace, through the adapter where possible."""
    lab = Lab(clock=FixedClock(simulate.START))
    api = LaneBApi(lab)
    sc = simulate.Scenario("roundtrip", "Roundtrip demonstration", "illustrative only", n_per_arm=15, mde=0.5)
    exp_id = simulate.register(lab, sc)
    job, claims = synthetic.job("fake-roundtrip-1", org="Fictional Roundtrip Works"), synthetic.claims()
    app_id = "app-roundtrip-1"
    lab.store.open_application(application_id=app_id, job_id=job["id"], employer_id="emp-fictional-roundtrip")
    assignment = lab.engine.assign(experiment_id=exp_id, application_id=app_id,
                                   attributes={"seniority": "senior", "role_family": "fde"})
    variant = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=job, claims=claims, max_bullets=4,
                                        label=assignment["variant"],
                                        emphasize_skills=simulate.EMPHASIS[assignment["variant"]])
    lab.store.append_event(application_id=app_id, job_id=job["id"], event_type="drafted", actor="system",
                           source="system:resume-engine", outcome_status="observed",
                           resume_variant_id=variant["id"], experiment_id=exp_id)
    request = api.call("approval_request", action=ap.SUBMIT_APPLICATION, subject_id=app_id,
                       resume_variant_id=variant["id"])
    grant = api.call("grant_approval", action=ap.SUBMIT_APPLICATION, subject_id=app_id,
                     content_hash=request["data"]["content_hash"],
                     confirmation=request["data"]["confirmation_phrase"])  # the synthetic user confirms
    approved = api.call("approve_package", application_id=app_id, resume_variant_id=variant["id"],
                        token_id=grant["data"]["token_id"])
    lab.clock.advance(hours=1)
    submitted = api.call("record_user_submission", application_id=app_id)
    lab.clock.advance(days=6)
    response = api.call("record_response", application_id=app_id, event_type="recruiter_screen",
                        occurred_at=fmt_ts(lab.clock.now() - timedelta(hours=3)))
    refused = api.call("approve_package", application_id=app_id, resume_variant_id=variant["id"],
                       token_id=grant["data"]["token_id"])  # replay: must be refused
    outcome = application_outcome(lab.store.effective_events(app_id), metric="recruiter_screen",
                                  window_days=simulate.WINDOW, as_of=lab.clock.now())
    application = api.call("get_application", application_id=app_id)["data"]
    return {"note": "Synthetic end-to-end trace. All people, employers and postings are fictional.",
            "inputs": {"job_posting": job, "profile_claims": claims},
            "resume_variant": variant, "assignment": application["assignment"],
            "approval_request": {k: v for k, v in request["data"].items() if k != "must_show_user"},
            "calls": {"approve_package": approved, "record_user_submission": submitted,
                      "record_response": response, "replayed_approval_is_refused": refused},
            "application": application, "outcome": outcome,
            "experiment": api.call("get_experiment", experiment_id=exp_id)["data"],
            "interim_report": api.call("experiment_report", experiment_id=exp_id)["data"],
            "integrity": api.call("integrity")["data"]}


def score(results: list[dict]) -> dict:
    """Compare verdicts with the ground truth known to the simulator."""
    rows = []
    for r in results:
        rep, effects = r["report"], r["true_effects"]
        claimed = set()
        if rep["verdict"] == "EVIDENCE_FOR_DIFFERENCE":
            claimed = {c["treatment"] for c in rep["comparisons"] if not c["blocking"] and "p_value" in c}
        false_alarms = sorted(a for a in claimed if effects.get(a, 0.0) == 0.0)
        rows.append({"scenario": r["scenario"], "ground_truth": r["ground_truth"], "phase": rep["phase"],
                     "verdict": rep["verdict"], "claimed_arms": sorted(claimed), "false_alarm_arms": false_alarms})
    return {"rows": rows, "false_alarms": sum(bool(r["false_alarm_arms"]) for r in rows)}


def build(quick: bool = False) -> dict[str, object]:
    cohort = simulate.run_cohort(COHORT_SEED)
    lab, api = cohort["lab"], LaneBApi(cohort["lab"])
    lab.verify_all()
    app_ids = lab.store.application_ids()
    hist = [a for a in app_ids if a.startswith("hist-")]
    stages: dict[str, int] = {}
    for a in app_ids:
        s = lab.store.projection(a)["stage"]
        stages[s] = stages.get(s, 0) + 1
    files: dict[str, object] = {
        "experiment_reports.json": [
            {k: r[k] for k in ("experiment_id", "scenario", "ground_truth", "true_effects", "report")}
            for r in cohort["results"]],
        "experiments.json": [api.call("get_experiment", experiment_id=e)["data"] for e in lab.registry.ids()],
        "portfolio.json": cohort["portfolio"],
        "observational.json": cohort["observational"],
        "scorecard.json": score(cohort["results"]),
        "applications_sample.json": {
            "note": "First 30 live + 10 historical projections of the synthetic cohort; counts cover all of it.",
            "total_applications": len(app_ids),
            "live_randomised_applications": len(app_ids) - len(hist),
            "historical_observational_applications": len(hist),
            "stage_counts": dict(sorted(stages.items())),
            "duplicate_imports_ignored": lab.store.duplicates_ignored,
            "sample": [lab.store.projection(a) for a in app_ids[:30] + hist[:10]]},
        "roundtrip.json": roundtrip(),
        "adapter_methods.json": {"api_version": API_VERSION, "methods": api.methods()},
    }
    if quick:
        return files
    heldout = simulate.run_cohort(HELDOUT_SEED)
    files["heldout_cohort.json"] = {
        "note": "Held-out cohort: same scenarios, a seed never used while developing the analysis.",
        "seed": HELDOUT_SEED, "scorecard": score(heldout["results"]),
        "verdict_counts": heldout["portfolio"]["verdict_counts"]}
    reps, alarms, misses, n_null, n_eff = [], 0, 0, 0, 0
    for seed in range(1001, 1009):  # replicate null and large-effect scenarios through the full pipeline
        sc = score(simulate.run_cohort(seed, only=NULL_KEYS + EFFECT_KEYS)["results"])
        for row in sc["rows"]:
            if row["scenario"] in NULL_KEYS:
                n_null += 1
                alarms += bool(row["false_alarm_arms"])
            else:
                n_eff += 1
                misses += row["verdict"] != "EVIDENCE_FOR_DIFFERENCE"
        reps.append({"seed": seed, "false_alarms": sc["false_alarms"],
                     "verdicts": {r["scenario"]: r["verdict"] for r in sc["rows"]}})
    files["full_pipeline_replication.json"] = {
        "null_scenarios": NULL_KEYS, "large_effect_scenarios": EFFECT_KEYS, "seeds": [r["seed"] for r in reps],
        "null_experiments_run": n_null, "false_alarms": alarms, "false_alarm_rate": alarms / n_null,
        "false_alarm_rate_interval_95": list(simulate.stats.wilson_interval(alarms, n_null)),
        "large_effect_experiments_run": n_eff, "missed_detections": misses, "replications": reps}
    files["calibration.json"] = {
        "note": "Monte-Carlo behaviour of the real decision core (analysis.infer). 'naive' is the wrong "
                "application-level z-test, shown only to quantify what cluster-aware analysis prevents.",
        "runs": [simulate.calibrate(n_sims=1000, n_perm=499, seed=101, **kw) for kw in (
            dict(n_per_arm=40),
            dict(n_per_arm=15, baseline=0.1),
            dict(n_per_arm=30, icc=0.3, cluster_size=(2, 4)),
            dict(n_per_arm=25, icc=0.5, cluster_size=(3, 5), baseline=0.3),
            dict(n_per_arm=40, missing=0.2),
            dict(n_per_arm=60, effect=0.25),
            dict(n_per_arm=30, effect=0.05),
        )]}
    return files


def dump(payload: object) -> str:
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE.parent / "fixtures" / "v1"))
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = build(args.quick)
    manifest = {"schema": "careertwin.b.fixtures-manifest.v1", "api_version": API_VERSION,
                "generator": "packages/experiments/tools/build_fixtures.py", "cohort_seed": COHORT_SEED,
                "synthetic_only": True, "files": {}}
    for name, payload in files.items():
        text = dump(payload)
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        manifest["files"][name] = {"sha256": sha256_hex(text), "bytes": len(text.encode("utf-8"))}
    (out / "MANIFEST.json").write_text(dump(manifest), encoding="utf-8", newline="\n")
    reports = files["experiment_reports.json"]
    print(json.dumps({"out": str(out), "written": sorted(files),
                      "applications": files["applications_sample.json"]["total_applications"],  # type: ignore[index]
                      "experiments": len(reports)}, indent=1))  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
