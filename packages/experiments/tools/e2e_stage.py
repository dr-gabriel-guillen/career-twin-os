"""Lane B stages for lane E's disposable integration run (manifest: ../integration-manifest.json).

    python packages/experiments/tools/e2e_stage.py resume      --artifacts DIR
    python packages/experiments/tools/e2e_stage.py application --artifacts DIR
    python packages/experiments/tools/e2e_stage.py experiment  --artifacts DIR --seed N

Reads from DIR: jobs.json (JobPosting[] or lane A envelopes {"posting", "ext"}), claims.json
(ProfileClaim[] or {"claim", ...} envelopes), pin.json ({"job_id": ...}).
Writes to DIR: resume_variant.json | events.json | experiment.json, assignments.json,
experiment_events.json, analysis.json. State shared by the stages lives in DIR/lane-b/; a stage
works on a copy and swaps it in only on success, so a failed stage leaves the state untouched.

The randomisation seed is derived from --seed and therefore predictable: this stage is a synthetic
negative control for integration testing, not a concealed allocation.

Offline and synthetic. The human user is simulated (simulate.simulated_user_grant) and employer
responses are drawn from a seeded RNG with a true effect of exactly zero, so the expected
analysis is never a claimed difference. Exit status: 0 ok, 2 bad input, 3 refused by a lane B rule.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
for pkg in ("applications", "experiments"):
    sys.path.insert(0, str(HERE.parents[1] / pkg))

from ct_applications import approvals as ap  # noqa: E402
from ct_applications.core import CareerTwinError, FixedClock, content_hash, fmt_ts  # noqa: E402
from ct_applications.schema import validate  # noqa: E402
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.registry import make_design  # noqa: E402
from ct_experiments.simulate import simulated_user_grant  # noqa: E402

START = "2026-03-02T09:00:00Z"
WINDOW = 30
SUBJECT_FALLBACK = "candidate-synthetic-001"


class BadInput(Exception):
    pass


def _load(dir_: Path, name: str):
    path = dir_ / name
    if not path.exists():
        raise BadInput(f"missing input {name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BadInput(f"{name} is not valid JSON: {exc}") from exc


def _dump(dir_: Path, name: str, payload) -> None:
    (dir_ / name).write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="\n")


def _contract(obj, contract: str, where: str) -> None:
    try:
        validate(obj, contract)
    except CareerTwinError as exc:
        raise BadInput(f"{where}: {exc}") from None


def _obj(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BadInput(f"{where} must be an object")
    return value


def load_jobs(dir_: Path) -> list[dict]:
    """Returns [{posting, employer_id, members, employer_source}] for every contract-valid record."""
    raw = _load(dir_, "jobs.json")
    if isinstance(raw, dict):
        raw = raw.get("jobs") or raw.get("records") or []
    if not isinstance(raw, list):
        raise BadInput("jobs.json must be a list of postings or lane A records")
    out, seen = [], set()
    for i, rec in enumerate(raw):
        if not isinstance(rec, dict):
            raise BadInput(f"jobs.json[{i}] must be an object")
        posting = rec.get("posting", rec)
        if not isinstance(posting, dict):
            raise BadInput(f"jobs.json[{i}].posting must be an object")
        _contract(posting, "job-posting", f"jobs.json[{i}]")
        if posting["id"] in seen:
            raise BadInput(f"jobs.json lists posting {posting['id']!r} twice")
        seen.add(posting["id"])
        ext = _obj(rec.get("ext"), f"jobs.json[{i}].ext") if "posting" in rec else {}
        emp = _obj(ext.get("employer"), f"jobs.json[{i}].ext.employer").get("id")
        members = _obj(ext.get("dedup"), f"jobs.json[{i}].ext.dedup").get("members") or []
        if not isinstance(members, list):
            raise BadInput(f"jobs.json[{i}].ext.dedup.members must be a list")
        if emp is not None and (not isinstance(emp, str) or not emp):
            raise BadInput(f"jobs.json[{i}].ext.employer.id must be a non-empty string")
        out.append({"posting": posting,
                    # lane A's employer.id when present; otherwise the raw organization string, unnormalised
                    "employer_id": emp or "org:" + content_hash(posting["organization"])[:16],
                    "employer_source": "lane-a:ext.employer.id" if emp else "fallback:organization-hash",
                    "members": [m for m in members if isinstance(m, str) and m]})
    if not out:
        raise BadInput("jobs.json contains no job postings")
    return out


def load_claims(dir_: Path) -> list[dict]:
    raw = _load(dir_, "claims.json")
    if isinstance(raw, dict):
        raw = raw.get("claims") or []
    if not isinstance(raw, list):
        raise BadInput("claims.json must be a list of ProfileClaim objects")
    claims = [c.get("claim", c) if isinstance(c, dict) else c for c in raw]
    ids = set()
    for i, c in enumerate(claims):
        _contract(c, "profile-claim", f"claims.json[{i}]")
        if c["id"] in ids:
            raise BadInput(f"claims.json lists claim {c['id']!r} twice")
        ids.add(c["id"])
    return claims


class Stage:
    def __init__(self, artifacts: Path, state: Path):
        self.dir = artifacts
        self.state = state
        self.state.mkdir(parents=True, exist_ok=True)
        clock_file = self.state / "clock.json"
        start = json.loads(clock_file.read_text(encoding="utf-8"))["now"] if clock_file.exists() else START
        self.clock = FixedClock(start)
        self.lab = Lab(self.state / "workspace", clock=self.clock)
        self.lab.verify_all()

    def save(self) -> None:
        self.lab.verify_all()
        (self.state / "clock.json").write_text(json.dumps({"now": self.clock.now_ts()}), encoding="utf-8")

    def pinned(self, jobs: list[dict]) -> dict:
        pin = _load(self.dir, "pin.json")
        job_id = pin.get("job_id") if isinstance(pin, dict) else None
        match = [j for j in jobs if j["posting"]["id"] == job_id]
        if not match:
            raise BadInput(f"pinned job {job_id!r} is not in jobs.json")
        return match[0]

    @staticmethod
    def subject(claims: list[dict]) -> str:
        subjects = {c["subject_id"] for c in claims}
        if len(subjects) > 1:
            raise BadInput("claims.json mixes several subjects")
        return subjects.pop() if subjects else SUBJECT_FALLBACK

    # -- stage: resume.variant ----------------------------------------------
    def resume(self) -> dict:
        jobs, claims = load_jobs(self.dir), load_claims(self.dir)
        job = self.pinned(jobs)["posting"]
        variant = self.lab.resumes.build_variant(subject_id=self.subject(claims), job=job, claims=claims,
                                                 label=f"pinned:{job['id']}")
        _dump(self.dir, "resume_variant.json", variant)
        return {"resume_variant_id": variant["id"], "bullets": len(variant["bullets"]),
                "refused_claims": len(variant["refused_claims"])}

    # -- stage: application.record ------------------------------------------
    def application(self) -> dict:
        jobs, claims = load_jobs(self.dir), load_claims(self.dir)
        pinned = self.pinned(jobs)
        job = pinned["posting"]
        variant = self.lab.resumes.build_variant(subject_id=self.subject(claims), job=job, claims=claims,
                                                 label=f"pinned:{job['id']}")  # same content -> same id as stage 1
        app_id = f"app-e2e-{job['id']}"
        store = self.lab.store
        if app_id not in store.application_ids():
            store.open_application(application_id=app_id, job_id=job["id"], employer_id=pinned["employer_id"],
                                   job_members=pinned["members"])
            self.clock.advance(minutes=5)
            store.append_event(application_id=app_id, job_id=job["id"], event_type="drafted", actor="system",
                               source="system:resume-engine", outcome_status="observed",
                               resume_variant_id=variant["id"])
            self.clock.advance(minutes=5)
            token = simulated_user_grant(self.lab.ledger, ap.SUBMIT_APPLICATION, app_id,
                                         self.lab.package_hash(app_id, variant["id"]))
            store.append_event(application_id=app_id, job_id=job["id"], event_type="approved", actor="user",
                               source="user:approval-dialog", outcome_status="observed",
                               resume_variant_id=variant["id"], approval_token_ref=token)
            self.clock.advance(minutes=5)
            store.record_user_submission(application_id=app_id)
        _dump(self.dir, "events.json", store.raw_events(app_id))
        return {"application_id": app_id, "application_stage": store.projection(app_id)["stage"],
                "employer_id_source": pinned["employer_source"]}

    # -- stage: experiment.run ----------------------------------------------
    def experiment(self, seed: int) -> dict:
        jobs, claims = load_jobs(self.dir), load_claims(self.dir)
        subject = self.subject(claims)
        active = [j for j in jobs if j["posting"]["status"] == "active"]
        if not active:
            raise BadInput("no active job postings to enrol")
        exp_id = f"exp-e2e-{seed}"
        reg, rng = self.lab.registry, random.Random(seed)
        other = [e for e in reg.ids() if e.startswith("exp-e2e-") and e != exp_id]
        if other:
            raise BadInput(f"this artifacts directory already holds experiment {other[0]!r}; "
                           "use a fresh directory for another seed")
        levels = sorted({j["posting"]["seniority"] for j in active})
        employers = {j["employer_id"] for j in active}
        # cluster by employer when there are enough employers to analyse clusters; otherwise randomise postings
        # and say so (the design notes then carry NOT_EMPLOYER_CLUSTERED)
        unit = "employer-cluster" if len(employers) >= 40 else "job"
        n_units = len(employers) if unit == "employer-cluster" else len(active)
        per_arm = n_units // 2
        if per_arm < 15:
            raise BadInput(f"only {n_units} randomisable units in jobs.json; an experiment needs at least 30")
        mean_cluster = len(active) / len(employers)
        if exp_id not in reg.ids():
            spec = {"id": exp_id, "title": "Integration A/A: two labels, identical résumé selection",
                    "hypothesis": "There is no difference: both arms send the same evidence-backed résumé.",
                    "variants": ["control", "relabelled"], "assignment_unit": unit,
                    "strata": ["seniority"], "primary_metric": "recruiter_screen",
                    "secondary_metrics": ["interview"], "consent_required": True,
                    "outcome_window_days": WINDOW,
                    "stopping_rule": f"Fixed horizon: {per_arm * 2} {unit} units in total or the end of the job "
                                     "list, whichever comes first; analyse once every window has closed.",
                    "analysis_plan": f"ITT on {unit} units; stratified permutation test; Holm; missing = no "
                                     "success; worst-case and uncorrected-history sensitivity analyses."}
            design = make_design(
                question="Does relabelling an identical résumé change the recruiter-screen rate? (negative control)",
                eligibility_description="Active fictional postings supplied by lane A in this integration run.",
                control="control", variant_definitions={"control": "evidence-backed résumé",
                                                        "relabelled": "the same résumé under another label"},
                strata_levels={"seniority": levels}, planned_units_per_arm=per_arm, assumed_baseline=0.2,
                minimum_detectable_effect=0.3, assumed_icc=0.1 if unit == "employer-cluster" else 0.0,
                assumed_cluster_size=mean_cluster if unit == "employer-cluster" else 1.0, n_permutations=1999)
            reg.create_draft(spec, design, seed=f"lane-b-e2e-secret-{seed}")
            digest = reg.design_hash(exp_id)
            reg.preregister(exp_id, token_id=simulated_user_grant(self.lab.ledger, ap.PREREGISTER_EXPERIMENT,
                                                                  exp_id, digest))
            reg.record_consent(exp_id, token_id=simulated_user_grant(self.lab.ledger, ap.EXPERIMENT_CONSENT,
                                                                     exp_id, digest, ttl_hours=24 * 200))
            refused, pending = [], []
            for j in active:
                post = j["posting"]
                app_id = f"app-exp-{seed}-{post['id']}"
                self.clock.advance(hours=3)
                self.lab.store.open_application(application_id=app_id, job_id=post["id"],
                                                employer_id=j["employer_id"], job_members=j["members"])
                try:
                    a = self.lab.engine.assign(experiment_id=exp_id, application_id=app_id,
                                               attributes={"seniority": post["seniority"]})
                except CareerTwinError as exc:  # e.g. duplicate posting, sample complete
                    refused.append({"job_id": post["id"], "reason": f"{type(exc).__name__}: {exc}"[:200]})
                    continue
                variant = self.lab.resumes.build_variant(subject_id=subject, job=post, claims=claims,
                                                         label="e2e")  # identical selection in both arms
                common = dict(application_id=app_id, job_id=post["id"], resume_variant_id=variant["id"])
                self.lab.store.append_event(event_type="drafted", actor="system", source="system:resume-engine",
                                            outcome_status="observed", **common)
                token = simulated_user_grant(self.lab.ledger, ap.SUBMIT_APPLICATION, app_id,
                                             self.lab.package_hash(app_id, variant["id"]))
                self.lab.store.append_event(event_type="approved", actor="user", source="user:approval-dialog",
                                            outcome_status="observed", approval_token_ref=token, **common)
                self.clock.advance(minutes=10)
                self.lab.store.record_user_submission(application_id=app_id)
                if rng.random() < 0.2:  # true effect 0: the arm is never consulted
                    pending.append((app_id, post["id"], self.clock.now() + timedelta(days=1 + rng.random() * 25)))
            _dump(self.state, "refused_enrolments.json", refused)
            if reg.get(exp_id)["spec"]["status"] == "running" and \
                    len({x["unit_id"] for x in self.lab.engine.assignments(exp_id)}) < per_arm * 2:
                reg.set_status(exp_id, "closed", actor="user", reason="job list exhausted before the planned sample")
            self.clock.advance(days=WINDOW + 1)
            for app_id, job_id, at in pending:  # the inbox as it looks once every window has closed
                self.lab.store.append_event(application_id=app_id, job_id=job_id, event_type="recruiter_screen",
                                            actor="import", source=f"import:synthetic-mail-{app_id}",
                                            outcome_status="observed", occurred_at=fmt_ts(at))
        report = self.lab.report(exp_id)
        assignments = self.lab.engine.assignments(exp_id)
        apps = [a["application_id"] for a in assignments]
        if not apps:
            raise CareerTwinError("no posting could be enrolled; nothing to report")
        _dump(self.dir, "experiment.json", reg.spec(exp_id))
        _dump(self.dir, "assignments.json", [{k: v for k, v in a.items() if k != "consent_token_ref"}
                                             for a in assignments])
        _dump(self.dir, "experiment_events.json", [e for a in apps for e in self.lab.store.raw_events(a)])
        _dump(self.dir, "analysis.json", report)
        return {"experiment_id": exp_id, "phase": report["phase"], "verdict": report["verdict"],
                "assigned_applications": len(apps),
                "refused_enrolments": len(json.loads((self.state / "refused_enrolments.json").read_text()))
                if (self.state / "refused_enrolments.json").exists() else 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["resume", "application", "experiment"])
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    artifacts = Path(args.artifacts)
    state, scratch = artifacts / "lane-b", artifacts / "lane-b.partial"
    try:
        if not artifacts.is_dir():
            raise BadInput(f"--artifacts {artifacts} is not a directory")
        # all-or-nothing: work on a copy of the state, swap it in only if the stage succeeds
        shutil.rmtree(scratch, ignore_errors=True)
        if state.exists():
            shutil.copytree(state, scratch)
        stage = Stage(artifacts, scratch)
        summary = {"resume": stage.resume, "application": stage.application,
                   "experiment": lambda: stage.experiment(args.seed)}[args.stage]()
        stage.save()
        shutil.rmtree(state, ignore_errors=True)
        scratch.rename(state)
    except BadInput as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        print(json.dumps({"ok": False, "stage": args.stage, "error": "BadInput", "message": str(exc)}))
        return 2
    except OSError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        print(json.dumps({"ok": False, "stage": args.stage, "error": "BadInput", "message": f"{type(exc).__name__}: {exc}"}))
        return 2
    except CareerTwinError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        print(json.dumps({"ok": False, "stage": args.stage, "error": type(exc).__name__, "message": str(exc)}))
        return 3
    print(json.dumps({"ok": True, "stage": args.stage, **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
