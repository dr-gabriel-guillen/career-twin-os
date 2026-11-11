import json
import random
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "tests"))
import _bootstrap  # noqa: E402,F401
from ct_applications import approvals as ap  # noqa: E402
from ct_applications import schema, synthetic  # noqa: E402
from ct_applications.core import CareerTwinError, FixedClock, ValidationError, parse_ts  # noqa: E402
from ct_applications.journal import IntegrityError  # noqa: E402
from ct_experiments import simulate, stats  # noqa: E402
from ct_experiments.analysis import infer, portfolio_summary  # noqa: E402
from ct_experiments.assignment import (AssignmentError, ConsentError, CrossoverError,  # noqa: E402
                                       DoubleEnrollmentError, IneligibleError, unit_variant)
from ct_experiments.lab import Lab  # noqa: E402
from ct_experiments.outcomes import application_outcome  # noqa: E402
from ct_experiments.registry import DesignLockedError, RegistryError, make_design  # noqa: E402
from ct_experiments.simulate import Scenario, simulated_user_grant  # noqa: E402

ATTR = {"seniority": "senior", "role_family": "fde"}


def new_lab(root=None):
    return Lab(root, clock=FixedClock(simulate.START))


def open_app(lab, app, job=None, employer=None):
    job = job or f"fake-{app}"
    lab.store.open_application(application_id=app, job_id=job, employer_id=employer or f"emp-{app}")
    return app


class TestStats(unittest.TestCase):
    def test_reference_values(self):
        self.assertAlmostEqual(stats.t_ppf(0.975, 10), 2.2281, places=3)
        self.assertAlmostEqual(stats.t_ppf(0.975, 1), 12.706, places=2)
        self.assertAlmostEqual(stats.t_ppf(0.95, 30), 1.6973, places=3)
        self.assertAlmostEqual(stats.t_cdf(0.0, 7), 0.5, places=9)
        self.assertAlmostEqual(stats.betainc(2, 3, 0.4), 0.5248, places=4)
        lo, hi = stats.wilson_interval(5, 20)
        self.assertAlmostEqual(lo, 0.1119, places=3)
        self.assertAlmostEqual(hi, 0.4687, places=3)
        self.assertIsNone(stats.wilson_interval(0, 0))
        lo, hi = stats.newcombe_interval(56, 70, 48, 80)  # Newcombe (1998), method 10
        self.assertAlmostEqual(lo, 0.0524, places=3)
        self.assertAlmostEqual(hi, 0.3339, places=3)
        self.assertAlmostEqual(stats.two_proportion_power(0.2, 0.2, 50), 0.05, places=6)
        self.assertGreater(stats.two_proportion_power(0.2, 0.5, 60), 0.9)
        self.assertLess(stats.two_proportion_power(0.2, 0.25, 30), 0.15)

    def test_multiplicity_adjustments(self):
        for got, want in zip(stats.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06]):
            self.assertAlmostEqual(got, want)
        for got, want in zip(stats.benjamini_hochberg([0.01, 0.04, 0.03]), [0.03, 0.04, 0.04]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(stats.holm([]), [])

    def test_permutation_test_behaviour(self):
        same = [([1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0])]
        self.assertGreater(stats.stratified_permutation_test(same, seed=1, n_perm=999)["p_value"], 0.9)
        strong = [([1] * 12, [0] * 12)]
        res = stats.stratified_permutation_test(strong, seed=1, n_perm=999)
        self.assertEqual(res["p_value"], 1 / 1000)  # can never be reported as zero
        self.assertEqual(res, stats.stratified_permutation_test(strong, seed=1, n_perm=999))  # seeded
        with self.assertRaises(ValueError):
            stats.stratified_permutation_test([([1], [])], seed=1)
        # Simpson-style data: pooled difference is large, within-stratum difference is zero
        simpson = [([1] * 9 + [0], [1] * 2 + [0] * 0 + [1] * 0 + [1] * 0)[:1] and ([1, 1, 1, 1, 1, 1, 1, 1, 0, 0], [1, 1, 1, 1, 0]),
                   ([0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 0, 0, 1, 1])]
        self.assertAlmostEqual(stats.stratified_difference(simpson), 0.0)

    def test_icc(self):
        self.assertAlmostEqual(stats.anova_icc([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]]), 1.0)
        self.assertEqual(stats.anova_icc([[1, 0], [0, 1], [1, 0], [0, 1]]), 0.0)
        self.assertIsNone(stats.anova_icc([[1], [0]]))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.lab = new_lab()
        self.sc = Scenario("t", "Test experiment", "n/a", n_per_arm=15, mde=0.5)

    def draft(self, lab=None, key="t", **design_kw):
        lab = lab or self.lab
        spec = {"id": f"exp-{key}", "title": "T", "hypothesis": "H", "variants": ["control", "treatment"],
                "assignment_unit": "application", "strata": ["seniority"], "primary_metric": "recruiter_screen",
                "stopping_rule": "fixed horizon", "consent_required": False, "outcome_window_days": 30,
                "analysis_plan": "ITT permutation"}
        kw = dict(question="Q?", eligibility_description="fictional", control="control",
                  variant_definitions={"control": "c", "treatment": "t"},
                  strata_levels={"seniority": ["senior", "staff"]}, planned_units_per_arm=15,
                  assumed_baseline=0.2, minimum_detectable_effect=0.5)
        kw.update(design_kw)
        return lab.registry.create_draft(spec, make_design(**kw), seed="test-seed"), spec

    def test_incomplete_or_invalid_designs_are_refused(self):
        _, spec = self.draft()
        base = make_design(question="Q?", eligibility_description="d", control="control",
                           variant_definitions={"control": "c", "treatment": "t"},
                           strata_levels={"seniority": ["senior"]}, planned_units_per_arm=15,
                           assumed_baseline=0.2, minimum_detectable_effect=0.3)
        bad_designs = [
            {**base, "question": "  "}, {**base, "control": "nobody"}, {**base, "variant_definitions": {"control": "c"}},
            {**base, "strata_levels": {}}, {**base, "planned_units_per_arm": 5}, {**base, "minimum_units_per_arm": 3},
            {**base, "alpha": 0.5}, {**base, "stopping": {"type": "peek_whenever"}},
            {**base, "minimum_detectable_effect": 0.9}, {**base, "extra_field": 1},
            {k: v for k, v in base.items() if k != "eligibility"}, {**base, "assumed_icc": 0.2},
        ]
        for i, design in enumerate(bad_designs):
            with self.assertRaises(ValidationError, msg=i):
                self.lab.registry.create_draft({**spec, "id": f"exp-bad-{i}"}, design)
        bad_specs = [{**spec, "primary_metric": "vibes"}, {**spec, "variants": ["only-one"]},
                     {**spec, "hypothesis": ""}, {k: v for k, v in spec.items() if k != "outcome_window_days"},
                     {**spec, "secondary_metrics": ["recruiter_screen"]}, {**spec, "assignment_unit": "recruiter"}]
        for i, s in enumerate(bad_specs):
            with self.assertRaises(ValidationError, msg=i):
                self.lab.registry.create_draft({**s, "id": f"exp-bads-{i}"}, base)
        with self.assertRaises(RegistryError):  # ids are never reused
            self.lab.registry.create_draft(spec, base)

    def test_preregistration_needs_user_approval_of_the_exact_design(self):
        self.draft()
        reg, ledger = self.lab.registry, self.lab.ledger
        with self.assertRaises(ap.ApprovalError):
            reg.preregister("exp-t", token_id=None)
        stale = simulated_user_grant(ledger, ap.PREREGISTER_EXPERIMENT, "exp-t", reg.design_hash("exp-t"))
        rec = reg.get("exp-t")
        reg.update_draft("exp-t", design={**rec["design"], "alpha": 0.01})  # draft changed after approval
        with self.assertRaises(ap.ApprovalError):
            reg.preregister("exp-t", token_id=stale)
        tok = simulated_user_grant(ledger, ap.PREREGISTER_EXPERIMENT, "exp-t", reg.design_hash("exp-t"))
        locked = reg.preregister("exp-t", token_id=tok)
        self.assertEqual(locked["spec"]["status"], "preregistered")
        self.assertIsNotNone(locked["spec"]["design_locked_at"])
        schema.validate(reg.spec("exp-t"), "experiment")
        self.assertNotIn("seed", locked)
        self.assertNotIn("test-seed", json.dumps(locked))

    def test_locked_design_cannot_change(self):
        exp = simulate.register(self.lab, self.sc)
        reg = self.lab.registry
        rec = reg.get(exp)
        for kw in ({"design": {**rec["design"], "alpha": 0.1}},
                   {"design": {**rec["design"], "minimum_units_per_arm": 10, "planned_units_per_arm": 10}},
                   {"spec": {**{k: v for k, v in rec["spec"].items() if k not in ("status", "design_locked_at", "seed_digest")},
                             "primary_metric": "offer"}}):
            with self.assertRaises(DesignLockedError):
                reg.update_draft(exp, **kw)
        with self.assertRaises(DesignLockedError):
            reg.preregister(exp, token_id="x")
        self.assertEqual(reg.verify(exp), rec["design_hash"])
        # an in-memory or alternative-loader alteration is caught by verify()
        reg._records[exp]["design"]["alpha"] = 0.1
        with self.assertRaises(IntegrityError):
            reg.verify(exp)
        with self.assertRaises(AssignmentError.__mro__[1]):  # CareerTwinError
            self.lab.engine.assign(experiment_id=exp, application_id=open_app(self.lab, "a1"), attributes=ATTR)

    def test_altered_analysis_plan_on_disk_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            lab = new_lab(d)
            exp = simulate.register(lab, self.sc)
            path = Path(d) / "experiments.jsonl"
            text = path.read_text(encoding="utf-8")
            self.assertIn("Intention-to-treat", text)
            path.write_text(text.replace("Intention-to-treat", "Best-looking subgroup"), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                new_lab(d)
            path.write_text(text, encoding="utf-8")
            self.assertEqual(new_lab(d).registry.verify(exp), lab.registry.verify(exp))

    def test_status_transitions(self):
        exp = simulate.register(self.lab, self.sc)
        reg = self.lab.registry
        with self.assertRaises(RegistryError):
            reg.set_status(exp, "paused", actor="user")  # not running yet
        with self.assertRaises(RegistryError):
            reg.set_status(exp, "closed", actor="system")
        self.lab.engine.assign(experiment_id=exp, application_id=open_app(self.lab, "a1"), attributes=ATTR)
        self.assertEqual(reg.get(exp)["spec"]["status"], "running")
        reg.set_status(exp, "paused", actor="user")
        with self.assertRaises(AssignmentError):
            self.lab.engine.assign(experiment_id=exp, application_id=open_app(self.lab, "a2"), attributes=ATTR)
        reg.set_status(exp, "running", actor="user")
        reg.set_status(exp, "closed", actor="user")
        for status in ("running", "paused", "draft"):
            with self.assertRaises(RegistryError):
                reg.set_status(exp, status, actor="user")
        with self.assertRaises(AssignmentError):
            self.lab.engine.assign(experiment_id=exp, application_id=open_app(self.lab, "a3"), attributes=ATTR)


class TestAssignment(unittest.TestCase):
    def setUp(self):
        self.lab = new_lab()
        self.exp = simulate.register(self.lab, Scenario("t", "T", "n/a", n_per_arm=20, mde=0.4))

    def assign(self, app, lab=None, exp=None, attrs=ATTR, **kw):
        lab = lab or self.lab
        if app not in lab.store.application_ids():
            open_app(lab, app, **kw)
        return lab.engine.assign(experiment_id=exp or self.exp, application_id=app, attributes=attrs)

    def test_allocation_is_seeded_identity_keyed_and_unpredictable(self):
        ids = [f"u{i}" for i in range(2000)]
        seq = [unit_variant("s", "e", "h", u, ["a", "b"]) for u in ids]
        self.assertEqual(seq, [unit_variant("s", "e", "h", u, ["b", "a"]) for u in ids])  # deterministic
        self.assertNotEqual(seq, [unit_variant("s2", "e", "h", u, ["a", "b"]) for u in ids])
        self.assertNotEqual(seq, [unit_variant("s", "e", "h2", u, ["a", "b"]) for u in ids])
        self.assertLess(abs(seq.count("a") - 1000), 90)  # about 4 sd of a fair coin
        # the arms already handed out say nothing about the next one (the flaw of block designs)
        lead, hits, trials = 0, 0, 0
        for v in seq:
            if lead != 0:
                trials += 1
                hits += (v == "a") == (lead < 0)  # guess: the lagging arm comes next
            lead += 1 if v == "a" else -1
        self.assertLess(abs(hits / trials - 0.5), 0.05)
        three = [unit_variant("s", "e", "h", u, ["a", "b", "c"]) for u in ids[:1500]]
        self.assertTrue(all(abs(three.count(v) - 500) < 80 for v in "abc"))

    def test_assignment_does_not_depend_on_enrolment_order_or_stratum_label(self):
        other = new_lab()
        simulate.register(other, Scenario("t", "T", "n/a", n_per_arm=20, mde=0.4))
        names = [f"a{i}" for i in range(40)]
        got = {n: self.assign(n)["variant"] for n in names}
        want = {n: self.assign(n, lab=other, attrs={**ATTR, "seniority": "staff"})["variant"]
                for n in reversed(names)}
        self.assertEqual(got, want)
        self.assertEqual(self.lab.engine.verify(self.exp), 40)
        self.assertEqual(len(set(got.values())), 2)
        with self.assertRaises(AssignmentError):  # preregistered sample is complete
            self.assign("a-extra")

    def test_idempotent_and_no_double_enrolment(self):
        first = self.assign("a1")
        self.assertEqual(self.assign("a1"), first)
        self.assertEqual(len(self.lab.engine.assignments(self.exp)), 1)
        exp2 = simulate.register(self.lab, Scenario("u", "U", "n/a", n_per_arm=15, mde=0.5))
        with self.assertRaises(DoubleEnrollmentError):
            self.assign("a1", exp=exp2)
        with self.assertRaises(DoubleEnrollmentError):  # same posting in another experiment
            self.assign("a2", exp=exp2, job="fake-a1", employer="emp-new")
        with self.assertRaises(DoubleEnrollmentError):  # same employer in another open experiment
            self.assign("a3", exp=exp2, employer="emp-a1")
        self.lab.registry.set_status(self.exp, "closed", actor="user")
        self.assign("a3", exp=exp2)  # allowed once the first experiment is closed

    def test_no_crossover_within_a_posting(self):
        self.assign("a1", job="fake-j1", employer="emp-1")
        with self.assertRaises(CrossoverError):
            self.assign("a2", job="fake-j1", employer="emp-1")

    def test_cluster_units_keep_one_arm_and_stratum(self):
        lab = new_lab()
        exp = simulate.register(lab, Scenario("c", "C", "n/a", unit="employer-cluster", n_per_arm=15, mde=0.5,
                                              icc=0.2, cluster_size=(2, 3)))
        arms = [self.assign(f"a{i}", lab=lab, exp=exp, employer="emp-same",
                            attrs={**ATTR, "seniority": "staff" if i == 2 else "senior"}) for i in range(4)]
        self.assertEqual(len({a["variant"] for a in arms}), 1)
        self.assertEqual([a["reused_unit"] for a in arms], [False, True, True, True])
        self.assertTrue(arms[2]["stratum_mismatch_with_unit"])
        self.assertEqual({a["stratum"] for a in arms}, {"seniority=senior"})
        self.assertEqual(lab.engine.verify(exp), 4)

    def test_must_be_assigned_before_exposure(self):
        env_app = "late-1"
        simulate.submit_application(self.lab, app_id=env_app, job=synthetic.job("fake-late-1"), employer_id="emp-late",
                                    exp_id=None, attributes=ATTR, claims=synthetic.claims())
        with self.assertRaises(AssignmentError):
            self.lab.engine.assign(experiment_id=self.exp, application_id=env_app, attributes=ATTR)
        with self.assertRaises(AssignmentError):
            self.lab.engine.assign(experiment_id=self.exp, application_id="does-not-exist", attributes=ATTR)

    def test_eligibility_and_strata_are_enforced_and_screen_outs_are_kept(self):
        for attrs in ({"seniority": "senior", "role_family": "backend"}, {"seniority": "intern", "role_family": "fde"},
                      {"role_family": "fde"}):
            with self.assertRaises(IneligibleError):
                self.assign(f"x{len(self.lab.engine.screened_out(self.exp))}", attrs=attrs)
        self.assertEqual(len(self.lab.engine.screened_out(self.exp)), 3)
        self.assertEqual(self.lab.engine.assignments(self.exp), [])
        self.lab.store.open_application(application_id="h1", job_id="fake-h1", employer_id="emp-h", actor="import",
                                        source="import:x", historical=True)
        with self.assertRaises(IneligibleError):
            self.lab.engine.assign(experiment_id=self.exp, application_id="h1", attributes=ATTR)

    def test_invalid_consent(self):
        lab = new_lab()
        sc = Scenario("k", "K", "n/a", n_per_arm=15, mde=0.5, consent=True)
        exp = simulate.register(lab, sc)
        self.assign("a1", lab=lab, exp=exp)
        token = lab.registry._records[exp]["consent_token_ref"]
        lab.ledger.revoke(token, actor="user", reason="changed my mind")
        with self.assertRaises(ConsentError):
            self.assign("a2", lab=lab, exp=exp)
        # consent given for a different design does not count
        lab2 = new_lab()
        spec_exp = "exp-k2"
        simulate.register(lab2, Scenario("k1", "K1", "n/a", n_per_arm=15, mde=0.5))
        s = Scenario("k2", "K2", "n/a", n_per_arm=15, mde=0.5, consent=False)
        simulate.register(lab2, s)
        lab2.registry._records[spec_exp]["spec"]["consent_required"] = True  # simulate a consent-required design
        lab2.registry._records[spec_exp]["locked_design_hash"] = lab2.registry.design_hash(spec_exp)
        with self.assertRaises(ConsentError):  # none recorded
            self.assign("b1", lab=lab2, exp=spec_exp)
        wrong = simulated_user_grant(lab2.ledger, ap.EXPERIMENT_CONSENT, "exp-k1", lab2.registry.design_hash("exp-k1"))
        with self.assertRaises(ap.ApprovalError):
            lab2.registry.record_consent(spec_exp, token_id=wrong)
        short = simulated_user_grant(lab2.ledger, ap.EXPERIMENT_CONSENT, spec_exp,
                                     lab2.registry.design_hash(spec_exp), ttl_hours=1)
        lab2.registry.record_consent(spec_exp, token_id=short)
        self.assign("b1", lab=lab2, exp=spec_exp)
        lab2.clock.advance(hours=2)
        with self.assertRaises(ConsentError):  # expired
            self.assign("b2", lab=lab2, exp=spec_exp)

    def test_tampered_assignment_is_detected(self):
        for i in range(6):
            self.assign(f"a{i}")
        rec = self.lab.engine._by_exp[self.exp][2]
        rec["variant"] = "treatment" if rec["variant"] == "control" else "control"
        with self.assertRaises(IntegrityError):
            self.lab.engine.verify(self.exp)
        self.assertEqual(self.lab.report(self.exp)["verdict"], "NOT_ANALYZABLE")

    def test_reload_from_disk_continues_the_same_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            a, mem = new_lab(d), new_lab()
            for lab in (a, mem):
                simulate.register(lab, Scenario("r", "R", "n/a", n_per_arm=15, mde=0.5))
            first = [self.assign(f"a{i}", lab=a, exp="exp-r")["variant"] for i in range(5)]
            b = new_lab(d)  # process restart
            rest = [self.assign(f"a{i}", lab=b, exp="exp-r")["variant"] for i in range(5, 12)]
            want = [self.assign(f"a{i}", lab=mem, exp="exp-r")["variant"] for i in range(12)]
            self.assertEqual(first + rest, want)
            self.assertEqual(b.engine.verify("exp-r"), 12)
            path = Path(d) / "assignments.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text("\n".join(lines[:3] + lines[4:]) + "\n", encoding="utf-8")  # delete one assignment
            with self.assertRaises(IntegrityError):
                new_lab(d)

    def test_attrition_and_deviations_stay_in_the_journal(self):
        a = self.assign("a1")
        with self.assertRaises(AssignmentError):
            self.lab.engine.withdraw(a["assignment_id"], actor="system", reason="x")
        self.lab.engine.withdraw(a["assignment_id"], actor="user", reason="role closed")
        self.lab.engine.record_deviation(a["assignment_id"], actor="user", note="sent the other résumé")
        kept = self.lab.engine.assignments(self.exp)
        self.assertEqual(len(kept), 1)
        self.assertEqual([n["kind"] for n in kept[0]["annotations"]], ["withdrawn", "deviation"])
        flow = self.lab.report(self.exp)["flow"]["by_arm"][a["variant"]]
        self.assertEqual(flow["units"], 1)  # the withdrawn unit stays in the ITT denominator
        self.assertIsNone(flow["withdrawn"])  # interim reports blind every per-arm count


def ev(kind, at, status="observed", recorded=None):
    return {"event_type": kind, "occurred_at": at, "recorded_at": recorded or at or "2026-01-01T00:00:00Z",
            "outcome_status": status}


class TestOutcomes(unittest.TestCase):
    def outcome(self, events, as_of="2026-03-01T00:00:00Z", metric="recruiter_screen"):
        return application_outcome(events, metric=metric, window_days=30, as_of=parse_ts(as_of))

    def test_statuses(self):
        sub = ev("submitted", "2026-01-01T00:00:00Z", "self-reported")
        self.assertEqual(self.outcome([ev("saved", None)])["status"], "missing")
        self.assertIn("withdrawn_before_exposure", self.outcome([ev("saved", None), ev("withdrawn", None)])["flags"])
        self.assertEqual(self.outcome([sub, ev("recruiter_screen", "2026-01-10T00:00:00Z")])["status"], "success")
        self.assertEqual(self.outcome([sub, ev("interview", "2026-01-10T00:00:00Z")])["status"], "success")  # implies screen
        self.assertEqual(self.outcome([sub, ev("recruiter_screen", "2026-01-10T00:00:00Z")], metric="offer")["status"],
                         "no_success")
        silent = self.outcome([sub])
        self.assertEqual((silent["status"], silent["flags"]), ("no_success", ["no_response_within_window"]))
        self.assertEqual(self.outcome([sub], as_of="2026-01-15T00:00:00Z")["status"], "censored")
        rejected = self.outcome([sub, ev("rejected", "2026-01-05T00:00:00Z")], as_of="2026-01-15T00:00:00Z")
        self.assertEqual((rejected["status"], rejected["flags"]), ("no_success", ["ended_by_rejected"]))

    def test_late_ambiguous_duplicate_and_impossible_responses(self):
        sub = ev("submitted", "2026-01-01T00:00:00Z", "self-reported")
        late = self.outcome([sub, ev("recruiter_screen", "2026-02-15T00:00:00Z")])
        self.assertEqual(late["status"], "no_success")
        self.assertIn("late_success_outside_window", late["flags"])
        amb = self.outcome([sub, ev("recruiter_screen", "2026-01-10T00:00:00Z", "unknown")])
        self.assertEqual(amb["status"], "no_success")
        self.assertIn("ambiguous_feedback_not_counted", amb["flags"])
        dup = self.outcome([sub, ev("interview", "2026-01-10T00:00:00Z"), ev("interview", "2026-01-10T00:00:00Z")])
        self.assertEqual(dup["status"], "success")
        self.assertIn("duplicate_response_collapsed", dup["flags"])
        before = self.outcome([sub, ev("recruiter_screen", "2025-12-25T00:00:00Z")])
        self.assertEqual(before["status"], "no_success")
        self.assertIn("response_dated_before_exposure_ignored", before["flags"])
        # a response that has not happened yet at analysis time is invisible
        future = self.outcome([sub, ev("recruiter_screen", "2026-01-20T00:00:00Z")], as_of="2026-01-15T00:00:00Z")
        self.assertEqual(future["status"], "censored")
        fallback = self.outcome([ev("submitted", None, "self-reported", recorded="2026-01-01T00:00:00Z")])
        self.assertIn("exposure_time_from_recorded_at", fallback["flags"])
        boundary = self.outcome([sub, ev("recruiter_screen", "2026-01-31T00:00:00Z")])  # exactly day 30
        self.assertEqual(boundary["status"], "success")


def units(arm_counts, stratum="s", missing=None):
    """arm -> (successes, n) as independent one-application units."""
    out = []
    for arm, (x, n) in arm_counts.items():
        for i in range(n):
            miss = bool(missing and i >= n - missing.get(arm, 0))
            status = "missing" if miss else ("success" if i < x else "no_success")
            out.append({"arm": arm, "stratum": stratum, "apps": [status], "y": float(status == "success"),
                        "y_complete": None if miss else float(status == "success"), "n_missing": int(miss)})
    return out


class TestDecisionCore(unittest.TestCase):
    def run_infer(self, us, variants=("control", "treatment"), minimum=10, alpha=0.05):
        return infer(us, variants=list(variants), control="control", minimum_units=minimum, alpha=alpha,
                     n_perm=1999, seed_key="k", metric="recruiter_screen", unit_label="application")

    def test_clear_difference_is_claimed_with_direction(self):
        res = self.run_infer(units({"control": (6, 40), "treatment": (24, 40)}))
        self.assertEqual(len(res["claims"]), 1)
        self.assertIn("higher", res["claims"][0])
        comp = res["comparisons"][0]
        self.assertEqual(comp["interval_95"]["method"], "newcombe_hybrid_score")
        self.assertGreater(comp["interval_95"]["low"], 0)
        worse = self.run_infer(units({"control": (24, 40), "treatment": (6, 40)}))
        self.assertIn("lower", worse["claims"][0])

    def test_no_claim_without_enough_evidence(self):
        res = self.run_infer(units({"control": (8, 40), "treatment": (11, 40)}))
        self.assertEqual(res["claims"], [])
        self.assertTrue(any("NOT_SIGNIFICANT" in w for w in res["withheld"]))
        self.assertTrue(any("INTERVAL_INCLUDES_ZERO" in w for w in res["withheld"]))
        # a perfect split with too few units is still not a claim
        tiny = self.run_infer(units({"control": (0, 6), "treatment": (6, 6)}))
        self.assertEqual(tiny["claims"], [])
        self.assertTrue(any("TOO_FEW_UNITS" in w for w in tiny["withheld"]))
        self.assertLess(tiny["comparisons"][0]["p_value"], 0.05)  # significant, and still withheld

    def test_missing_outcomes_can_block_a_claim(self):
        solid = self.run_infer(units({"control": (4, 40), "treatment": (26, 40)}, missing={"control": 3}))
        self.assertEqual(len(solid["claims"]), 1)  # survives worst-case imputation
        fragile = self.run_infer(units({"control": (5, 40), "treatment": (16, 40)}, missing={"control": 12}))
        self.assertLess(fragile["comparisons"][0]["p_value_holm"], 0.05)
        self.assertEqual(fragile["claims"], [])
        self.assertTrue(any("NOT_ROBUST_TO_MISSING_OUTCOMES" in w for w in fragile["withheld"]))
        self.assertTrue(any("DIFFERENTIAL_ATTRITION" in n for n in fragile["comparisons"][0]["notes"]))

    def test_multiple_arms_are_holm_adjusted(self):
        us = units({"control": (8, 40), "b": (17, 40), "c": (9, 40)})
        res = self.run_infer(us, variants=("control", "b", "c"))
        b = next(c for c in res["comparisons"] if c["treatment"] == "b")
        self.assertGreater(b["p_value_holm"], b["p_value"])
        self.assertAlmostEqual(b["p_value_holm"], min(1.0, 2 * b["p_value"]))

    def test_strata_without_both_arms_are_dropped_and_reported(self):
        us = units({"control": (5, 20), "treatment": (6, 20)}, "s1") + units({"treatment": (3, 3)}, "s2")
        comp = self.run_infer(us)["comparisons"][0]
        self.assertEqual(comp["strata_dropped"], ["s2"])
        none = self.run_infer(units({"treatment": (3, 12)}, "s1") + units({"control": (3, 12)}, "s2"))
        self.assertTrue(any("NO_COMPARABLE_STRATUM" in w for w in none["withheld"]))

    def test_stratification_removes_a_prognostic_imbalance(self):
        # treatment is over-represented in the easy stratum; within strata there is no difference at all
        us = (units({"control": (4, 10), "treatment": (12, 30)}, "easy")
              + units({"control": (3, 30), "treatment": (1, 10)}, "hard"))
        comp = self.run_infer(us)["comparisons"][0]
        self.assertAlmostEqual(comp["estimate"], 0.0, places=9)
        self.assertEqual(self.run_infer(us)["claims"], [])


class TestAnalysisPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = ["aa-01", "big-01", "cl-null-01", "attr-01", "interim-01", "interim-02", "early-01", "three-01"]
        cls.out = simulate.run_cohort(only=keys)
        cls.lab = cls.out["lab"]
        cls.rep = {r["scenario"]: r["report"] for r in cls.out["results"]}

    def test_cohort_passes_full_integrity_verification(self):
        self.assertTrue(self.lab.verify_all(self.lab.anchors()))
        for app in self.lab.store.application_ids()[:50]:
            for event in self.lab.store.raw_events(app):
                schema.validate(event, "application-event")
        for exp in self.lab.registry.ids():
            schema.validate(self.lab.registry.spec(exp), "experiment")

    def test_large_true_effect_is_detected(self):
        rep = self.rep["big-01"]
        self.assertEqual((rep["phase"], rep["verdict"]), ("FINAL", "EVIDENCE_FOR_DIFFERENCE"))
        self.assertIn("not", " ".join(rep["interpretation_limits"]))
        self.assertNotIn("probability of being hired", rep["claim"])
        self.assertEqual(rep["reasons_withheld"], [])

    def test_null_experiment_returns_inconclusive_not_no_effect(self):
        rep = self.rep["aa-01"]
        self.assertEqual((rep["phase"], rep["verdict"]), ("FINAL", "INCONCLUSIVE"))
        self.assertIsNone(rep["claim"])
        self.assertTrue(rep["reasons_withheld"])
        self.assertNotIn("no effect", json.dumps(rep).lower())

    def test_interim_looks_are_blinded_and_claimless(self):
        for key, reason in (("interim-01", "OUTCOME_WINDOWS_STILL_OPEN"), ("interim-02", "ENROLMENT_INCOMPLETE")):
            rep = self.rep[key]
            self.assertEqual((rep["phase"], rep["verdict"]), ("INTERIM", "INTERIM_NO_CLAIM"))
            self.assertTrue(any(reason in r for r in rep["reasons_withheld"]))
            self.assertEqual(rep["comparisons"], [])
            self.assertNotIn("arms", rep)
            for arm in rep["flow"]["by_arm"].values():
                self.assertIsNone(arm["success"])
            self.assertNotIn("p_value", json.dumps(rep))

    def test_early_stop_yields_no_test(self):
        rep = self.rep["early-01"]
        self.assertEqual((rep["phase"], rep["verdict"]), ("STOPPED_EARLY", "INCONCLUSIVE"))
        self.assertEqual(rep["comparisons"], [])
        self.assertTrue(any("STOPPED_BEFORE_PLANNED_SAMPLE" in r for r in rep["reasons_withheld"]))

    def test_clustered_analysis_uses_clusters_as_units(self):
        rep = self.rep["cl-null-01"]
        comp = rep["comparisons"][0]
        self.assertEqual(comp["units_treatment"] + comp["units_control"], rep["clustering"]["clusters"])
        self.assertGreater(rep["flow"]["assigned_applications"], rep["flow"]["assigned_units"])
        self.assertGreater(rep["clustering"]["icc_estimate"], 0.2)
        self.assertGreater(rep["clustering"]["design_effect"], 1.5)

    def test_three_arm_experiment_compares_each_arm_with_control(self):
        rep = self.rep["three-01"]
        self.assertEqual([(c["treatment"], c["control"]) for c in rep["comparisons"]],
                         [("brevity", "control"), ("evals", "control")])
        for c in rep["comparisons"]:
            self.assertGreaterEqual(c["p_value_holm"], c["p_value"])
            # an arm is named in a claim if and only if nothing blocks it
            self.assertEqual(f"'{c['treatment']}' had" in (rep["claim"] or ""), not c["blocking"])
        self.assertEqual(rep["verdict"] == "EVIDENCE_FOR_DIFFERENCE",
                         any(not c["blocking"] for c in rep["comparisons"]))

    def test_differential_attrition_is_flagged(self):
        comp = self.rep["attr-01"]["comparisons"][0]
        self.assertGreater(comp["missing_fraction"]["treatment"], comp["missing_fraction"]["control"] + 0.1)
        self.assertTrue(any("DIFFERENTIAL_ATTRITION" in n for n in comp["notes"]))
        self.assertIn("worst_case_p_value", comp["sensitivity"])

    def test_deviating_from_the_locked_plan_is_exploratory_only(self):
        as_of = parse_ts(self.rep["big-01"]["as_of"])
        for override in ({"primary_metric": "interview"}, {"alpha": 0.2}, {"outcome_window_days": 10}):
            rep = self.lab.report("exp-big-01", as_of=as_of, override=override)
            self.assertEqual(rep["verdict"], "EXPLORATORY_NO_CLAIM")
            self.assertIsNone(rep["claim"])
            self.assertIn("DEVIATES_FROM_PREREGISTRATION", rep["reasons_withheld"][0])
        with self.assertRaises(CareerTwinError):
            self.lab.report("exp-big-01", as_of=as_of, override={"control": "treatment"})

    def test_analysis_is_reproducible(self):
        as_of = parse_ts(self.rep["big-01"]["as_of"])
        self.assertEqual(self.lab.report("exp-big-01", as_of=as_of), self.rep["big-01"])

    def test_portfolio_summary_counts_and_adjusts(self):
        port = self.out["portfolio"]
        self.assertEqual(port["experiments"], 8)
        self.assertEqual(sum(port["verdict_counts"].values()), 8)
        self.assertEqual(port["final_tests"], sum(r["phase"] == "FINAL" for r in self.rep.values()))
        for row in port["rows"]:
            self.assertGreaterEqual(row["p_holm_across_experiments"], row["p_primary"])
        self.assertEqual(portfolio_summary([])["final_tests"], 0)

    def test_tampering_after_the_fact_makes_the_experiment_not_analyzable(self):
        out = simulate.run_cohort(only=["tiny-01"])
        lab = out["lab"]
        lab.registry._records["exp-tiny-01"]["design"]["alpha"] = 0.1
        rep = lab.report("exp-tiny-01")
        self.assertEqual(rep["verdict"], "NOT_ANALYZABLE")
        self.assertIn("INTEGRITY_FAILURE", rep["reasons_withheld"][0])


class TestObservational(unittest.TestCase):
    def test_history_is_never_presented_as_causal(self):
        lab = new_lab()
        obs = simulate.run_observational(lab, random.Random(5))
        self.assertEqual((obs["evidence_type"], obs["verdict"], obs["claim"]), ("observational", "OBSERVATIONAL_ONLY", None))
        text = json.dumps(obs)
        self.assertNotIn("p_value", text)
        self.assertTrue(any("not randomised" in w for w in obs["warnings"]))
        g = obs["groups"]
        naive_gap = g["rv-hist-polished"]["rate"] - g["rv-hist-plain"]["rate"]
        self.assertGreater(naive_gap, 0.1)  # the confounder manufactures a gap although the true effect is 0
        within = [abs(c["rv-hist-polished"]["rate"] - c["rv-hist-plain"]["rate"]) for c in obs["by_covariate"].values()]
        self.assertLess(max(within), naive_gap)
        # historical applications can never enter a randomised experiment
        exp = simulate.register(lab, Scenario("o", "O", "n/a", n_per_arm=15, mde=0.5))
        with self.assertRaises(IneligibleError):
            lab.engine.assign(experiment_id=exp, application_id="hist-000", attributes=ATTR)


class TestCalibration(unittest.TestCase):
    def test_false_alarm_rate_is_controlled_and_naive_clustered_analysis_is_not(self):
        plain = simulate.calibrate(n_sims=200, n_per_arm=30, n_perm=299, seed=11)
        self.assertLessEqual(plain["claim_rate"], 0.09)  # nominal 0.05; 200 simulations
        clustered = simulate.calibrate(n_sims=200, n_per_arm=25, icc=0.5, cluster_size=(3, 5), baseline=0.3,
                                       n_perm=299, seed=12)
        self.assertLessEqual(clustered["claim_rate"], 0.09)
        self.assertGreater(clustered["naive_rejection_rate"], 0.12)  # what ignoring clusters would do
        self.assertGreater(clustered["naive_rejection_rate"], 2 * clustered["claim_rate"])

    def test_power_for_a_large_effect(self):
        strong = simulate.calibrate(n_sims=100, n_per_arm=60, effect=0.3, n_perm=299, seed=13)
        self.assertGreater(strong["claim_rate"], 0.8)
        weak = simulate.calibrate(n_sims=100, n_per_arm=30, effect=0.05, n_perm=299, seed=14)
        self.assertLess(weak["claim_rate"], 0.3)  # an underpowered design mostly says INCONCLUSIVE

    def test_cohort_is_deterministic(self):
        a = simulate.run_cohort(seed=5, only=["tiny-01", "aa-01"])
        b = simulate.run_cohort(seed=5, only=["tiny-01", "aa-01"])
        self.assertEqual([r["report"] for r in a["results"]], [r["report"] for r in b["results"]])
        self.assertEqual(a["lab"].anchors(), b["lab"].anchors())
        c = simulate.run_cohort(seed=6, only=["tiny-01", "aa-01"])
        self.assertNotEqual(a["lab"].anchors()["applications"], c["lab"].anchors()["applications"])


if __name__ == "__main__":
    unittest.main()
