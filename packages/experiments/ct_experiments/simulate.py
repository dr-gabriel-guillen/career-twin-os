"""Synthetic world with known ground truth. Everything here is fictional.

The simulator drives the *real* stores, workflow, approval gates, assignment
engine and analysis — it only replaces the two things that cannot exist
offline: the human user (simulated_user_grant) and employers' responses
(drawn from a seeded RNG with a known true effect).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ct_applications import approvals as ap
from ct_applications import synthetic
from ct_applications.core import FixedClock, fmt_ts

from . import stats
from .analysis import infer, observational_summary, portfolio_summary
from .assignment import unit_variant
from .lab import Lab
from .registry import make_design

START = "2026-03-02T09:00:00Z"
WINDOW = 30


def simulated_user_grant(ledger: ap.ApprovalLedger, action: str, subject: str, digest: str, **kw: Any) -> str:
    """Stand-in for the human in synthetic runs and tests ONLY. Product code never calls this."""
    return ledger.grant(action=action, subject_id=subject, content_hash=digest, actor="user",
                        confirmation=ap.expected_confirmation(action, subject, digest),
                        statement="synthetic user approval", **kw)["token_id"]


@dataclass
class Scenario:
    key: str
    title: str
    truth: str                       # plain-language ground truth
    unit: str = "application"
    arms: tuple[str, ...] = ("control", "treatment")
    effects: dict[str, float] = field(default_factory=dict)   # true absolute effect per non-control arm
    n_per_arm: int = 40
    baseline: float = 0.20
    staff_shift: float = -0.06       # staff-level postings respond less: a prognostic stratum
    icc: float = 0.0
    cluster_size: tuple[int, int] = (1, 1)
    never_submitted: dict[str, float] = field(default_factory=dict)
    analyze_after_days: int = 45     # after the last submission; < WINDOW leaves outcomes censored
    enrol_fraction: float = 1.0      # < 1 with close_early simulates stopping early
    close_early: bool = False
    consent: bool = False
    late_rate: float = 0.04
    ambiguous_rate: float = 0.03
    duplicate_rate: float = 0.05
    mde: float = 0.20


def scenarios() -> list[Scenario]:
    S = Scenario
    return [
        S("aa-01", "A/A: identical résumé, two labels", "no true difference (negative control)"),
        S("aa-02", "A/A: larger sample", "no true difference (negative control)", n_per_arm=60),
        S("aa-03", "A/A: employer clusters", "no true difference (negative control)", unit="employer-cluster",
          n_per_arm=30, icc=0.3, cluster_size=(2, 4)),
        S("aa-04", "A/A: per-posting unit", "no true difference (negative control)", unit="job", n_per_arm=45),
        S("big-01", "Evaluation-first bullet order", "true effect +0.30", effects={"treatment": 0.30}, n_per_arm=60),
        S("big-02", "Quantified impact bullets", "true effect +0.30", effects={"treatment": 0.30}, n_per_arm=70,
          baseline=0.15),
        S("big-03", "Role-matched summary line", "true effect +0.25", effects={"treatment": 0.25}, n_per_arm=80),
        S("small-01", "Shorter résumé", "true effect +0.04, far below what this sample can detect",
          effects={"treatment": 0.04}, n_per_arm=30),
        S("small-02", "Skills section first", "true effect +0.05, underpowered", effects={"treatment": 0.05},
          n_per_arm=35),
        S("small-03", "Certification line", "true effect -0.03, underpowered", effects={"treatment": -0.03},
          n_per_arm=30),
        S("cl-null-01", "Clustered A/A, strong employer effect", "no true difference; strong within-employer "
          "correlation that would fool an application-level test", unit="employer-cluster", n_per_arm=25,
          icc=0.5, cluster_size=(3, 5), baseline=0.3),
        S("cl-null-02", "Clustered A/A, moderate employer effect", "no true difference", unit="employer-cluster",
          n_per_arm=30, icc=0.25, cluster_size=(2, 5)),
        S("cl-big-01", "Clustered: project-led résumé", "true effect +0.30", unit="employer-cluster",
          effects={"treatment": 0.30}, n_per_arm=40, icc=0.15, cluster_size=(2, 4)),
        S("attr-01", "Differential attrition, no effect", "no true difference; treatment arm is abandoned "
          "before submission far more often", never_submitted={"control": 0.03, "treatment": 0.30}, n_per_arm=50),
        S("attr-02", "Real effect, heavy missingness", "true effect +0.22 but a quarter of both arms was never "
          "submitted", effects={"treatment": 0.22}, never_submitted={"control": 0.25, "treatment": 0.25},
          n_per_arm=50),
        S("interim-01", "Still waiting for replies", "true effect +0.30; analysed before windows closed",
          effects={"treatment": 0.30}, analyze_after_days=6),
        S("interim-02", "Enrolment unfinished", "no true difference; only 60% enrolled, experiment still running",
          enrol_fraction=0.6),
        S("early-01", "Closed early after a lucky streak", "true effect +0.10; user closed it at 50% enrolment",
          effects={"treatment": 0.10}, enrol_fraction=0.5, close_early=True, n_per_arm=50),
        S("three-01", "Three-arm: two rewrites vs control", "arm 'evals' +0.30, arm 'brevity' 0.00",
          arms=("control", "brevity", "evals"), effects={"brevity": 0.0, "evals": 0.30}, n_per_arm=60),
        S("tiny-01", "Minimum-size experiment", "true effect +0.15 with 20 units per arm",
          effects={"treatment": 0.15}, n_per_arm=20, mde=0.4),
        S("consent-01", "Consent-gated experiment", "true effect +0.25", effects={"treatment": 0.25},
          consent=True, n_per_arm=60),
        S("strat-01", "Strong stratum difference, no effect", "no true difference; staff postings respond far "
          "less (stratification must absorb it)", staff_shift=-0.25, baseline=0.35, n_per_arm=50),
        S("noisy-01", "Messy inbox, no effect", "no true difference; many late, ambiguous and duplicate replies",
          late_rate=0.25, ambiguous_rate=0.2, duplicate_rate=0.4, n_per_arm=45),
        S("mid-01", "Adequately powered moderate effect", "true effect +0.20", effects={"treatment": 0.20},
          n_per_arm=90, baseline=0.2),
    ]


def _clamp(p: float) -> float:
    return min(0.97, max(0.01, p))


def _cluster_rate(rng: random.Random, p: float, icc: float) -> float:
    if icc <= 0:
        return p
    k = (1 - icc) / icc  # beta-binomial: ICC = 1 / (a + b + 1)
    return rng.betavariate(p * k, (1 - p) * k)


def register(lab: Lab, sc: Scenario) -> str:
    exp_id = f"exp-{sc.key}"
    control = sc.arms[0]
    mean_size = sum(sc.cluster_size) / 2
    spec = {"id": exp_id, "title": sc.title, "hypothesis": f"Variant changes the recruiter-screen rate ({sc.title}).",
            "variants": list(sc.arms), "assignment_unit": sc.unit, "strata": ["seniority"],
            "primary_metric": "recruiter_screen", "secondary_metrics": ["interview"],
            "stopping_rule": f"Fixed horizon: stop enrolling at {sc.n_per_arm} units per arm; analyse once every "
                             f"{WINDOW}-day window has closed. No interim tests.",
            "consent_required": sc.consent, "outcome_window_days": WINDOW,
            "analysis_plan": "Intention-to-treat; stratified permutation test on unit means; Holm across arms; "
                             "missing outcomes count as no success; worst-case sensitivity analysis."}
    design = make_design(
        question=f"Does the treatment résumé change the recruiter-screen rate within {WINDOW} days?",
        eligibility_description="Fictional senior/staff postings in synthetic role families; live applications only.",
        eligibility_require={"role_family": ["fde", "ai-architect"]}, control=control,
        variant_definitions={a: ("baseline résumé ordering" if a == control else f"résumé variant '{a}'")
                             for a in sc.arms},
        strata_levels={"seniority": ["senior", "staff"]}, planned_units_per_arm=sc.n_per_arm,
        assumed_baseline=sc.baseline,
        minimum_detectable_effect=sc.mde, assumed_icc=sc.icc if sc.unit == "employer-cluster" else 0.0,
        assumed_cluster_size=mean_size if sc.unit == "employer-cluster" else 1.0, n_permutations=1999)
    lab.registry.create_draft(spec, design, seed=f"synthetic-seed-{sc.key}")
    token = simulated_user_grant(lab.ledger, ap.PREREGISTER_EXPERIMENT, exp_id, lab.registry.design_hash(exp_id))
    lab.registry.preregister(exp_id, token_id=token)
    if sc.consent:
        consent = simulated_user_grant(lab.ledger, ap.EXPERIMENT_CONSENT, exp_id,
                                       lab.registry.design_hash(exp_id), ttl_hours=24 * 300)
        lab.registry.record_consent(exp_id, token_id=consent)
    return exp_id


EMPHASIS = {"control": ["python"], "treatment": ["ai-evaluation"], "brevity": ["aws"], "evals": ["ai-evaluation"]}


def submit_application(lab: Lab, *, app_id: str, job: dict[str, Any], employer_id: str, exp_id: str | None,
                       attributes: dict[str, str], claims: list[dict[str, Any]], submit: bool = True,
                       withdraw: bool = False) -> dict[str, Any]:
    """The complete gated path for one live application. Returns what happened."""
    lab.store.open_application(application_id=app_id, job_id=job["id"], employer_id=employer_id)
    arm = None
    if exp_id is not None:
        arm = lab.engine.assign(experiment_id=exp_id, application_id=app_id, attributes=attributes)["variant"]
    variant = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=job, claims=claims, max_bullets=4,
                                        label=arm or "default", emphasize_skills=EMPHASIS.get(arm or "control", []))
    common = dict(application_id=app_id, job_id=job["id"], resume_variant_id=variant["id"], experiment_id=exp_id)
    lab.store.append_event(event_type="drafted", actor="system", source="system:resume-engine",
                           outcome_status="observed", **common)
    if not submit:
        if withdraw:
            lab.store.append_event(application_id=app_id, job_id=job["id"], event_type="withdrawn", actor="user",
                                   source="user:manual", outcome_status="observed", experiment_id=exp_id)
        return {"arm": arm, "variant_id": variant["id"], "submitted": False}
    token = simulated_user_grant(lab.ledger, ap.SUBMIT_APPLICATION, app_id, lab.package_hash(app_id, variant["id"]))
    lab.store.append_event(event_type="approved", actor="user", source="user:approval-dialog",
                           outcome_status="observed", approval_token_ref=token, **common)
    lab.store.record_user_submission(application_id=app_id)
    return {"arm": arm, "variant_id": variant["id"], "submitted": True, "approval_token_ref": token}


def run_scenario(lab: Lab, sc: Scenario, rng: random.Random) -> dict[str, Any]:
    clock: FixedClock = lab.clock  # type: ignore[assignment]
    exp_id = register(lab, sc)
    claims = synthetic.claims()
    planned_units = sc.n_per_arm * len(sc.arms)
    pending: list[dict[str, Any]] = []
    for u in range(int(round(planned_units * sc.enrol_fraction))):
        seniority = rng.choice(["senior", "staff"])
        employer = f"emp-{sc.key}-{u:03d}"
        base = _clamp(sc.baseline + (sc.staff_shift if seniority == "staff" else 0.0))
        cluster_logit_shift = _cluster_rate(rng, base, sc.icc) - base
        for k in range(rng.randint(*sc.cluster_size)):
            clock.advance(hours=7)
            job = synthetic.job(f"fake-{sc.key}-{u:03d}-{k}", org=f"Fictional Employer {sc.key}-{u:03d}",
                                seniority=seniority)
            app_id = f"app-{sc.key}-{u:03d}-{k}"
            # the decision to abandon is drawn before the arm is known to the simulator...
            abandon_draw, withdraw_draw = rng.random(), rng.random()
            lab.store.open_application(application_id=app_id, job_id=job["id"], employer_id=employer)
            arm = lab.engine.assign(experiment_id=exp_id, application_id=app_id,
                                    attributes={"seniority": seniority, "role_family": "fde"})["variant"]
            # ...and then compared with the arm-specific abandonment rate
            submit = abandon_draw >= sc.never_submitted.get(arm, 0.0)
            variant = lab.resumes.build_variant(subject_id=synthetic.SUBJECT, job=job, claims=claims, max_bullets=4,
                                                label=arm, emphasize_skills=EMPHASIS.get(arm, []))
            common = dict(application_id=app_id, job_id=job["id"], resume_variant_id=variant["id"],
                          experiment_id=exp_id)
            lab.store.append_event(event_type="drafted", actor="system", source="system:resume-engine",
                                   outcome_status="observed", **common)
            if not submit:
                if withdraw_draw < 0.5:
                    lab.store.append_event(application_id=app_id, job_id=job["id"], event_type="withdrawn",
                                           actor="user", source="user:manual", outcome_status="observed",
                                           experiment_id=exp_id)
                    lab.engine.withdraw(lab.engine.for_application(app_id)["assignment_id"], actor="user",
                                        reason="synthetic: lost interest before submitting")
                continue
            token = simulated_user_grant(lab.ledger, ap.SUBMIT_APPLICATION, app_id,
                                         lab.package_hash(app_id, variant["id"]))
            lab.store.append_event(event_type="approved", actor="user", source="user:approval-dialog",
                                   outcome_status="observed", approval_token_ref=token, **common)
            clock.advance(minutes=20)
            lab.store.record_user_submission(application_id=app_id)
            p = _clamp(base + cluster_logit_shift + sc.effects.get(arm, 0.0))
            pending.append({"app": app_id, "job": job["id"], "exp": exp_id, "submitted": clock.now(),
                            "responds": rng.random() < p, "u": [rng.random() for _ in range(8)]})
    last_submission = clock.now()
    as_of = last_submission + timedelta(days=sc.analyze_after_days)
    clock.set(as_of)
    for item in pending:  # the inbox as it looks at analysis time
        _record_responses(lab, sc, item, as_of)
    if sc.close_early:
        lab.registry.set_status(exp_id, "closed", actor="user", reason="synthetic: closed early")
    report = lab.report(exp_id, as_of=as_of)
    return {"experiment_id": exp_id, "scenario": sc.key, "ground_truth": sc.truth,
            "true_effects": dict(sc.effects), "report": report}


def _record_responses(lab: Lab, sc: Scenario, item: dict[str, Any], as_of) -> None:
    u = item["u"]
    base = dict(application_id=item["app"], job_id=item["job"], experiment_id=item["exp"])

    def add(kind: str, at, status: str = "observed", tag: str = "mail") -> bool:
        if at > as_of:
            return False  # has not happened yet
        kw = dict(event_type=kind, actor="import", source=f"import:synthetic-{tag}-{item['app']}-{kind}",
                  outcome_status=status, occurred_at=fmt_ts(at), on_duplicate="ignore", **base)
        lab.store.append_event(**kw)
        if u[5] < sc.duplicate_rate:
            lab.store.append_event(**kw)  # the same mail imported twice
        return True

    if item["responds"]:
        late = u[0] < sc.late_rate
        delay = WINDOW + 1 + u[1] * 20 if late else 1 + u[1] * (WINDOW - 2)
        when = item["submitted"] + timedelta(days=delay)
        if add("recruiter_screen", when) and u[2] < 0.5:
            if add("interview", when + timedelta(days=5)) and u[3] < 0.3:
                add("offer", when + timedelta(days=12))
    elif u[0] < sc.ambiguous_rate:
        add("recruiter_screen", item["submitted"] + timedelta(days=3 + u[1] * 10), status="unknown", tag="unclear")
    elif u[4] < 0.4:
        add("rejected", item["submitted"] + timedelta(days=2 + u[1] * 25))


def run_observational(lab: Lab, rng: random.Random, n: int = 240) -> dict[str, Any]:
    """History with a deliberate confounder and a true effect of exactly zero.

    The synthetic user sent the 'polished' résumé mostly to warm employers
    (high response rate regardless of résumé) and the 'plain' one to cold ones.
    """
    clock: FixedClock = lab.clock  # type: ignore[assignment]
    start = clock.now()
    ids = []
    for i in range(n):
        warm = rng.random() < 0.5
        polished = rng.random() < (0.8 if warm else 0.2)
        app_id, job_id = f"hist-{i:03d}", f"fake-hist-{i:03d}"
        employer = f"emp-hist-{'warm' if warm else 'cold'}-{i:03d}"
        submitted = start - timedelta(days=200 - i * 0.5)
        lab.store.open_application(application_id=app_id, job_id=job_id, employer_id=employer, actor="import",
                                   source="import:synthetic-history", historical=True)
        kw = dict(application_id=app_id, job_id=job_id, actor="import", outcome_status="self-reported",
                  resume_variant_id="rv-hist-polished" if polished else "rv-hist-plain")
        lab.store.append_event(event_type="drafted", source="import:synthetic-history-draft", **kw)
        lab.store.append_event(event_type="approved", source="import:synthetic-history-approved", **kw)
        lab.store.append_event(event_type="submitted", source="import:synthetic-history-submitted",
                               occurred_at=fmt_ts(submitted), **kw)
        if rng.random() < (0.45 if warm else 0.10):  # depends on warmth only: true résumé effect is 0
            lab.store.append_event(event_type="recruiter_screen", source="import:synthetic-history-screen",
                                   occurred_at=fmt_ts(submitted + timedelta(days=1 + rng.random() * 20)), **kw)
        ids.append(app_id)
    summary = observational_summary(
        lab.store, application_ids=ids, group_of=lambda p: p["resume_variant_id"], metric="recruiter_screen",
        window_days=WINDOW, as_of=clock.now(),
        covariate_of=lambda p: "warm-employer" if "-warm-" in p["employer_id"] else "cold-employer")
    summary["ground_truth"] = ("true résumé effect is exactly 0; the gap between groups is produced entirely by "
                               "which employers received which résumé")
    return summary


def run_cohort(seed: int = 20261110, root: str | None = None, only: list[str] | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    lab = Lab(root, clock=FixedClock(START))
    results = []
    for sc in scenarios():
        sub = random.Random(rng.getrandbits(64))  # one stream per scenario: adding scenarios never shifts others
        if only is None or sc.key in only:
            results.append(run_scenario(lab, sc, sub))
    obs = run_observational(lab, random.Random(rng.getrandbits(64))) if only is None else None
    return {"lab": lab, "results": results, "observational": obs,
            "portfolio": portfolio_summary([r["report"] for r in results])}


# -- calibration: how often does the decision rule cry wolf? ---------------------------------

def _sim_units(rng: random.Random, *, n_per_arm: int, baseline: float, effect: float, icc: float,
               cluster_size: tuple[int, int], seed: str, missing: float = 0.0) -> list[dict[str, Any]]:
    variants = ["control", "treatment"]
    units = []
    for i in range(2 * n_per_arm):
        stratum = rng.choice(["seniority=senior", "seniority=staff"])
        arm = unit_variant(seed, "calibration", "0" * 64, f"unit-{i}", variants)
        base = _clamp(baseline - (0.06 if stratum.endswith("staff") else 0.0))
        p = _clamp(_cluster_rate(rng, base, icc) + (effect if arm == "treatment" else 0.0))
        m = rng.randint(*cluster_size)
        statuses = ["missing" if rng.random() < missing else ("success" if rng.random() < p else "no_success")
                    for _ in range(m)]
        known = [s for s in statuses if s != "missing"]
        units.append({"arm": arm, "stratum": stratum, "apps": statuses,
                      "y": statuses.count("success") / m,
                      "y_complete": known.count("success") / len(known) if known else None,
                      "n_missing": statuses.count("missing")})
    return units


def _naive_application_level_p(units: list[dict[str, Any]]) -> float:
    """The WRONG analysis for clustered data, kept only to show how badly it miscalibrates."""
    x = {a: sum(u["apps"].count("success") for u in units if u["arm"] == a) for a in ("control", "treatment")}
    n = {a: sum(len(u["apps"]) for u in units if u["arm"] == a) for a in ("control", "treatment")}
    pooled = (x["control"] + x["treatment"]) / (n["control"] + n["treatment"])
    se = math.sqrt(pooled * (1 - pooled) * (1 / n["control"] + 1 / n["treatment"]))
    if se == 0:
        return 1.0
    z = (x["treatment"] / n["treatment"] - x["control"] / n["control"]) / se
    return 2 * (1 - stats.norm_cdf(abs(z)))


def calibrate(*, n_sims: int, n_per_arm: int, baseline: float = 0.2, effect: float = 0.0, icc: float = 0.0,
              cluster_size: tuple[int, int] = (1, 1), seed: int = 7, n_perm: int = 999, alpha: float = 0.05,
              missing: float = 0.0) -> dict[str, Any]:
    """Monte-Carlo rate at which the real decision core (analysis.infer) issues a claim."""
    rng = random.Random(seed)
    claims = naive = 0
    for i in range(n_sims):
        units = _sim_units(rng, n_per_arm=n_per_arm, baseline=baseline, effect=effect, icc=icc,
                           cluster_size=cluster_size, seed=f"calibration-{seed}-{i}", missing=missing)
        result = infer(units, variants=["control", "treatment"], control="control",
                       minimum_units=max(10, n_per_arm // 2), alpha=alpha, n_perm=n_perm,
                       seed_key=f"cal-{seed}-{i}", metric="recruiter_screen", unit_label="unit")
        claims += bool(result["claims"])
        naive += _naive_application_level_p(units) < alpha
    lo, hi = stats.wilson_interval(claims, n_sims)  # type: ignore[misc]
    nlo, nhi = stats.wilson_interval(naive, n_sims)  # type: ignore[misc]
    return {"n_sims": n_sims, "n_per_arm": n_per_arm, "baseline": baseline, "true_effect": effect, "icc": icc,
            "cluster_size": list(cluster_size), "missing_rate": missing, "alpha": alpha, "n_perm": n_perm,
            "seed": seed, "claims": claims, "claim_rate": claims / n_sims, "claim_rate_interval_95": [lo, hi],
            "naive_application_level_rejections": naive, "naive_rejection_rate": naive / n_sims,
            "naive_rejection_rate_interval_95": [nlo, nhi],
            "interpretation": ("false-alarm rate (true effect is 0)" if effect == 0 else "detection rate (power)")}
