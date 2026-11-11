"""Assignment engine: seeded randomisation keyed on the identity of the unit.

- The variant of a unit is a pure function of (secret seed, experiment id,
  locked design hash, unit id). It does NOT depend on enrolment order, on the
  arms already handed out, or on the caller-supplied stratum label, so nothing
  the user can observe or choose predicts or steers the next arm.
  (A permuted-block scheme was implemented first and withdrawn: with visible
  arms the lagging arm is predictable about 90% of the time, and selective
  enrolment then produced false claims in 35% of null simulations.)
  The price is that arms are balanced in expectation, not exactly; the
  analysis conditions on the realised arm sizes and reports the balance.
- Every assignment is journaled before the variant is returned (i.e. before any
  exposure), and the journal alone suffices to re-derive every variant
  (verify()).
- Enrolment is frozen once the preregistered number of units is reached or a
  final analysis has been recorded. An existing unit (employer cluster or
  posting) accepts further applications only for one outcome window after its
  first assignment, and never after the final analysis.
- One application belongs to at most one experiment; one posting and one
  employer belong to at most one open experiment; a unit keeps its arm for
  ever (no crossover). Units that never get an outcome stay in the journal and
  in the intention-to-treat denominators.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import random
from datetime import timedelta
from typing import Any

from ct_applications import approvals as ap
from ct_applications.core import CareerTwinError, Clock, clean_text, parse_ts
from ct_applications.events import ApplicationStore
from ct_applications.journal import IntegrityError, Journal

from .registry import ExperimentRegistry

ASSIGNABLE_STAGES = {"saved", "draft"}  # strictly before the user approves/sees the final package


class AssignmentError(CareerTwinError):
    pass


class IneligibleError(AssignmentError):
    pass


class DoubleEnrollmentError(AssignmentError):
    pass


class CrossoverError(AssignmentError):
    pass


class ConsentError(AssignmentError):
    pass


def _draw(seed: str, *parts: str) -> int:
    msg = "␟".join(parts).encode("utf-8")
    return int.from_bytes(hmac.new(seed.encode("utf-8"), msg, hashlib.sha256).digest(), "big")


def unit_variant(seed: str, experiment_id: str, design_hash: str, unit_id: str, variants: list[str]) -> str:
    """Variant of a unit: an HMAC-keyed draw on the unit's identity (equal allocation)."""
    return random.Random(_draw(seed, "unit", experiment_id, design_hash, unit_id)).choice(sorted(variants))


def stratum_key(strata: list[str], attributes: dict[str, str]) -> str:
    return "|".join(f"{factor}={attributes[factor]}" for factor in sorted(strata)) or "(all)"


class AssignmentEngine:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, clock: Clock | None = None,
                 registry: ExperimentRegistry, store: ApplicationStore):
        self.clock = clock or Clock()
        self.registry = registry
        self.store = store
        self.journal = Journal(path, name="assignments")
        self._by_app: dict[str, dict[str, Any]] = {}
        self._by_exp: dict[str, list[dict[str, Any]]] = {}
        self._unit: dict[tuple[str, str], dict[str, Any]] = {}      # (experiment, unit) -> first assignment
        self._stratum_n: dict[tuple[str, str], int] = {}            # (experiment, stratum) -> units drawn
        self._job_exp: dict[str, str] = {}
        self._employer_exp: dict[str, set[str]] = {}
        self._notes: dict[str, list[dict[str, Any]]] = {}           # assignment id -> withdrawals/deviations
        self._screened: dict[str, list[dict[str, Any]]] = {}
        for rec in self.journal:
            self._apply(rec["kind"], rec["body"])

    def _apply(self, kind: str, body: dict[str, Any]) -> None:
        if kind == "assigned":
            exp = body["experiment_id"]
            self._by_app[body["application_id"]] = body
            self._by_exp.setdefault(exp, []).append(body)
            if (exp, body["unit_id"]) not in self._unit:
                self._unit[(exp, body["unit_id"])] = body
                self._stratum_n[(exp, body["stratum"])] = body["stratum_index"] + 1
            for key in body.get("job_keys", [body["job_id"]]):
                self._job_exp[key] = exp
            self._employer_exp.setdefault(body["employer_id"], set()).add(exp)
        elif kind == "screened_out":
            self._screened.setdefault(body["experiment_id"], []).append(body)
        else:  # withdrawn | deviation
            self._notes.setdefault(body["assignment_id"], []).append({"kind": kind, **body})

    # -- assignment ------------------------------------------------------
    def assign(self, *, experiment_id: str, application_id: str, attributes: dict[str, str]) -> dict[str, Any]:
        existing = self._by_app.get(application_id)
        if existing is not None:
            if existing["experiment_id"] == experiment_id:
                return dict(existing)  # idempotent: the journaled assignment stands
            raise DoubleEnrollmentError(
                f"application already enrolled in {existing['experiment_id']!r}; double enrolment refused")
        design_hash = self.registry.verify(experiment_id)
        rec = self.registry.get(experiment_id)
        spec, design = rec["spec"], rec["design"]
        if spec["status"] not in ("preregistered", "running"):
            raise AssignmentError(f"experiment is {spec['status']!r}; assignment needs a preregistered or running experiment")
        try:
            consent_ref = self.registry.check_consent(experiment_id)
        except ap.ApprovalError as exc:
            raise ConsentError(f"valid consent is required before assignment: {exc}") from exc
        try:
            proj = self.store.projection(application_id)
        except KeyError:
            raise AssignmentError("unknown application; open it in the CRM first") from None
        if proj["historical"]:
            raise IneligibleError("historical (imported) applications are observational and cannot be randomised")
        if proj["stage"] not in ASSIGNABLE_STAGES:
            raise AssignmentError(f"application is already at stage {proj['stage']!r}; "
                                  "assignment must be journaled before approval and exposure")
        ever = {e["event_type"] for e in self.store.raw_events(application_id)} - {"saved", "drafted", "note"}
        if ever:  # voided events still happened: the employer may already have seen something
            raise AssignmentError("application has already been approved, submitted or answered at some point "
                                  f"({sorted(ever)}); voiding those events does not make it eligible")
        if rec.get("final_analysis"):
            raise AssignmentError("a final analysis has been recorded; the experiment accepts no more applications")
        planned = design["planned_units_per_arm"] * len(spec["variants"])
        n_units = sum(1 for (e, _u) in self._unit if e == experiment_id)
        if proj["experiment_id"] not in (None, experiment_id):
            raise DoubleEnrollmentError("application events already reference another experiment")
        if not isinstance(attributes, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()):
            raise AssignmentError("attributes must map names to string values")
        now = self.clock.now_ts()
        reasons = [f"{k}={attributes.get(k)!r} not in {allowed}"
                   for k, allowed in design["eligibility"]["require"].items() if attributes.get(k) not in allowed]
        reasons += [f"stratum factor {f}={attributes.get(f)!r} is not a preregistered level"
                    for f in spec["strata"] if attributes.get(f) not in design["strata_levels"][f]]
        job_id, employer_id = proj["job_id"], proj["employer_id"]
        job_keys = self.store.meta(application_id)["job_members"]  # the posting and its known duplicates
        if reasons:
            body = {"experiment_id": experiment_id, "application_id": application_id, "reasons": reasons, "at": now}
            self.journal.append("screened_out", body, now)
            self._apply("screened_out", body)
            raise IneligibleError("outside the preregistered eligibility universe: " + "; ".join(reasons))
        clash = sorted({self._job_exp[k] for k in job_keys if k in self._job_exp} - {experiment_id})
        if clash:
            raise DoubleEnrollmentError(f"posting {job_id!r} (or a duplicate of it) is already part of {clash}")
        others = {e for e in self._employer_exp.get(employer_id, set()) - {experiment_id}
                  if self.registry.get(e)["spec"]["status"] != "closed"}
        if others:
            raise DoubleEnrollmentError(
                f"employer {employer_id!r} is in open experiment(s) {sorted(others)}; overlapping experiments refused")
        unit_kind = spec["assignment_unit"]
        unit_id = {"application": application_id, "job": job_id, "employer-cluster": employer_id}[unit_kind]
        if unit_kind == "application" and any(self._job_exp.get(k) == experiment_id for k in job_keys):
            raise CrossoverError("this posting already has an application in the experiment; "
                                 "a second arm for the same posting is refused")
        stratum = stratum_key(spec["strata"], attributes)
        first = self._unit.get((experiment_id, unit_id))
        if first is None and n_units >= planned:
            raise AssignmentError("the preregistered sample is complete; enrolling more units would "
                                  "change the design. Close this experiment and register a new one")
        if first is not None and self.clock.now() > parse_ts(first["assigned_at"]) + timedelta(
                days=spec["outcome_window_days"]):
            raise AssignmentError("this unit's enrolment period (one outcome window after its first assignment) "
                                  "is over; no further applications can be added to it")
        if first is not None:  # sticky: the unit keeps its arm and its original stratum
            variant, index, reused = first["variant"], first["stratum_index"], True
            mismatch = first["stratum"] != stratum
            stratum = first["stratum"]
        else:
            index = self._stratum_n.get((experiment_id, stratum), 0)
            variant = unit_variant(self.registry._seed(experiment_id), experiment_id, design_hash,
                                   unit_id, spec["variants"])
            reused, mismatch = False, False
        body = {
            "assignment_id": f"asg-{len(self.journal):07d}", "experiment_id": experiment_id,
            "design_hash": design_hash, "application_id": application_id, "job_id": job_id,
            "job_keys": job_keys,
            "employer_id": employer_id, "unit_kind": unit_kind, "unit_id": unit_id, "stratum": stratum,
            "stratum_index": index, "variant": variant, "assigned_at": now, "reused_unit": reused,
            "stratum_mismatch_with_unit": mismatch, "consent_token_ref": consent_ref,
            "application_stage_at_assignment": proj["stage"],
        }
        self.journal.append("assigned", body, now)  # durable before anyone can act on the variant
        self._apply("assigned", body)
        if spec["status"] == "preregistered":
            self.registry.set_status(experiment_id, "running", actor="system", reason="first assignment journaled")
        return dict(body)

    def _annotate(self, kind: str, assignment_id: str, actor: str, text: str) -> None:
        if actor != "user":
            raise AssignmentError(f"a {kind} must be reported by the user")
        if not any(a["assignment_id"] == assignment_id for a in self._by_app.values()):
            raise AssignmentError("unknown assignment")
        body = {"assignment_id": assignment_id, "note": clean_text(text, max_len=500), "at": self.clock.now_ts()}
        self.journal.append(kind, body, body["at"])
        self._apply(kind, body)

    def withdraw(self, assignment_id: str, *, actor: str, reason: str) -> None:
        """Attrition: the unit leaves, the assignment stays (and stays in the ITT denominator)."""
        self._annotate("withdrawn", assignment_id, actor, reason)

    def record_deviation(self, assignment_id: str, *, actor: str, note: str) -> None:
        """E.g. the user sent the other arm's résumé. Analysis remains by assigned arm."""
        self._annotate("deviation", assignment_id, actor, note)

    # -- reading ---------------------------------------------------------
    def for_application(self, application_id: str) -> dict[str, Any] | None:
        body = self._by_app.get(application_id)
        return dict(body) if body else None

    def assignments(self, experiment_id: str) -> list[dict[str, Any]]:
        return [{**a, "annotations": list(self._notes.get(a["assignment_id"], []))}
                for a in self._by_exp.get(experiment_id, [])]

    def screened_out(self, experiment_id: str) -> list[dict[str, Any]]:
        return [dict(s) for s in self._screened.get(experiment_id, [])]

    def verify(self, experiment_id: str) -> int:
        """Re-derive every assignment from the seed and the journal order. Returns the count."""
        self.journal.verify()
        design_hash = self.registry.verify(experiment_id)
        variants = self.registry.get(experiment_id)["spec"]["variants"]
        seed = self.registry._seed(experiment_id)
        units: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        apps, jobs = set(), {}
        for a in self._by_exp.get(experiment_id, []):
            if a["design_hash"] != design_hash:
                raise IntegrityError(f"{a['assignment_id']} was made under a different design than the locked one")
            if a["application_id"] in apps:
                raise IntegrityError("an application was assigned twice")
            apps.add(a["application_id"])
            first = units.get(a["unit_id"])
            if first is None:
                expected_index = counts.get(a["stratum"], 0)
                counts[a["stratum"]] = expected_index + 1
                expected = unit_variant(seed, experiment_id, design_hash, a["unit_id"], variants)
                if a["reused_unit"] or a["stratum_index"] != expected_index or a["variant"] != expected:
                    raise IntegrityError(f"{a['assignment_id']} does not match the seeded allocation sequence")
                units[a["unit_id"]] = a
            elif not a["reused_unit"] or a["variant"] != first["variant"] or a["stratum"] != first["stratum"]:
                raise IntegrityError(f"{a['assignment_id']}: unit {a['unit_id']!r} crossed over between arms")
            for key in a.get("job_keys", [a["job_id"]]):
                if jobs.setdefault(key, a["variant"]) != a["variant"]:
                    raise IntegrityError(f"posting {key!r} appears in more than one arm")
        return len(apps)
