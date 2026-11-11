"""Experiment registry: preregistration with an immutable design lock.

A record = frozen-contract ExperimentSpec (`spec`) + lane B's structured design
(`design`, schema careertwin.b.experiment-design.v1) + a secret randomisation
seed (stored in clear in the local journal: it protects against accidental
unblinding through the API, not against a user who reads their own files).
`design_hash` covers everything that defines the experiment. Once the
user preregisters (approval bound to that hash) the design can never change:
not through the API, and not on disk without breaking the journal chain.
Changing your mind means closing the experiment and registering a new one.
"""
from __future__ import annotations

import json
import math
import os
import secrets
from typing import Any

from ct_applications import approvals as ap
from ct_applications.core import CareerTwinError, Clock, ValidationError, clean_text, content_hash, require_id, sha256_hex
from ct_applications.journal import IntegrityError, Journal
from ct_applications.schema import validate

from .stats import two_proportion_power

DESIGN_SCHEMA = "careertwin.b.experiment-design.v1"
METRICS = {"recruiter_screen", "interview", "offer"}
GLOBAL_MIN_UNITS_PER_ARM = 10
_DESIGN_KEYS = {"schema", "question", "eligibility", "control", "variant_definitions", "strata_levels",
                "planned_units_per_arm", "minimum_units_per_arm", "alpha", "assumed_baseline",
                "minimum_detectable_effect", "stopping", "missing_outcome_policy", "analysis_method",
                "assumed_icc", "assumed_cluster_size", "n_permutations"}


MAX_SHORTFALL_RISK = 0.2


def shortfall_risk(planned_per_arm: int, minimum_per_arm: int, n_arms: int) -> float:
    """Upper bound on P(some arm ends below the minimum) under equal simple randomisation."""
    total, p = planned_per_arm * n_arms, 1.0 / n_arms
    below = sum(math.comb(total, x) * p ** x * (1 - p) ** (total - x) for x in range(minimum_per_arm))
    return min(1.0, n_arms * below)


class DesignLockedError(CareerTwinError):
    pass


class RegistryError(CareerTwinError):
    pass


def compute_design_hash(spec: dict[str, Any], design: dict[str, Any]) -> str:
    frozen = {k: v for k, v in spec.items() if k not in ("status", "design_locked_at")}
    return content_hash({"spec": frozen, "design": design})


def _check_design(spec: dict[str, Any], design: dict[str, Any]) -> list[str]:
    unknown = set(design) - _DESIGN_KEYS
    if unknown:
        raise ValidationError(f"unknown design field(s): {sorted(unknown)}")
    missing = _DESIGN_KEYS - set(design)
    if missing:
        raise ValidationError(f"design is missing: {sorted(missing)}")
    if design["schema"] != DESIGN_SCHEMA:
        raise ValidationError("unsupported design schema")
    for key in ("question",):
        if not clean_text(design[key], field=key):
            raise ValidationError(f"design.{key} must be an explicit, non-empty statement")
    elig = design["eligibility"]
    if not isinstance(elig, dict) or set(elig) != {"description", "require"} or not clean_text(elig["description"]):
        raise ValidationError("eligibility needs a description and a 'require' map")
    if not isinstance(elig["require"], dict) or not all(
            isinstance(v, list) and v and all(isinstance(x, str) for x in v) for v in elig["require"].values()):
        raise ValidationError("eligibility.require maps attribute -> non-empty list of allowed values")
    if design["control"] not in spec["variants"]:
        raise ValidationError("control must be one of the variants")
    if set(design["variant_definitions"]) != set(spec["variants"]) or not all(
            isinstance(v, str) and v.strip() for v in design["variant_definitions"].values()):
        raise ValidationError("every variant needs a written definition")
    if set(design["strata_levels"]) != set(spec["strata"]) or not all(
            isinstance(v, list) and v and len(set(v)) == len(v) for v in design["strata_levels"].values()):
        raise ValidationError("strata_levels must list the allowed levels of every stratification factor")
    if spec["primary_metric"] not in METRICS:
        raise ValidationError(f"primary_metric must be one of {sorted(METRICS)}")
    if spec["primary_metric"] in spec.get("secondary_metrics", []) or set(spec.get("secondary_metrics", [])) - METRICS:
        raise ValidationError("secondary metrics must be known metrics distinct from the primary one")
    n, floor = design["planned_units_per_arm"], design["minimum_units_per_arm"]
    if not (isinstance(n, int) and isinstance(floor, int) and GLOBAL_MIN_UNITS_PER_ARM <= floor <= n):
        raise ValidationError(f"need {GLOBAL_MIN_UNITS_PER_ARM} <= minimum_units_per_arm <= planned_units_per_arm")
    risk = shortfall_risk(n, floor, len(spec["variants"]))
    if risk > MAX_SHORTFALL_RISK:
        raise ValidationError(
            f"with simple randomisation an arm would fall below minimum_units_per_arm={floor} with probability "
            f"up to {risk:.2f}; plan more units per arm or a lower minimum (>= {GLOBAL_MIN_UNITS_PER_ARM})")
    if not (0 < design["alpha"] <= 0.1):
        raise ValidationError("alpha must be in (0, 0.1]")
    p0, mde = design["assumed_baseline"], design["minimum_detectable_effect"]
    if not (0 < p0 < 1 and 0 < mde and p0 + mde < 1):
        raise ValidationError("assumed_baseline and minimum_detectable_effect must describe valid rates")
    if design["stopping"] != {"type": "fixed_horizon"}:
        raise ValidationError("v1 supports only a fixed-horizon stopping rule (no interim claims)")
    if design["missing_outcome_policy"] != "missing_counts_as_no_success":
        raise ValidationError("unsupported missing_outcome_policy")
    if design["analysis_method"] != "stratified_permutation_itt":
        raise ValidationError("unsupported analysis_method")
    if not (isinstance(design["n_permutations"], int) and 999 <= design["n_permutations"] <= 100_000):
        raise ValidationError("n_permutations must be between 999 and 100000")
    icc, size = design["assumed_icc"], design["assumed_cluster_size"]
    if not (0 <= icc <= 1 and size >= 1):
        raise ValidationError("assumed_icc in [0,1] and assumed_cluster_size >= 1 required")
    if spec["assignment_unit"] != "employer-cluster" and (icc != 0 or size != 1):
        raise ValidationError("ICC / cluster size assumptions only apply to employer-cluster designs")
    if not (1 <= spec.get("outcome_window_days", 0) <= 365):
        raise ValidationError("outcome_window_days (reporting window) is required and must be 1..365")
    # clusters are randomised; power uses the effective number of applications per arm
    n_eff = n * size / (1 + (size - 1) * icc)
    power = two_proportion_power(p0, p0 + mde, n_eff, design["alpha"])
    warnings = []
    if power < 0.8:
        warnings.append(f"UNDERPOWERED_BY_DESIGN: planned power is about {power:.2f} for the stated "
                        f"minimum detectable effect; an inconclusive result is the likely outcome")
    if spec["assignment_unit"] != "employer-cluster":
        warnings.append("NOT_EMPLOYER_CLUSTERED: several applications to one employer may be correlated; "
                        "crossover within a posting is blocked, but employer-level contamination is possible")
    return [f"planned_power={power:.3f}"] + warnings


def _default_minimum(planned: int, n_arms: int) -> int:
    """Largest minimum with a shortfall risk of at most 5% (capped at 3/4 of plan, floored at the global minimum)."""
    best = GLOBAL_MIN_UNITS_PER_ARM
    for m in range(GLOBAL_MIN_UNITS_PER_ARM, planned + 1):
        if shortfall_risk(planned, m, max(2, n_arms)) <= 0.05:
            best = m
    return min(best, max(GLOBAL_MIN_UNITS_PER_ARM, planned * 3 // 4))


def make_design(*, question: str, eligibility_description: str, control: str, variant_definitions: dict[str, str],
                strata_levels: dict[str, list[str]], planned_units_per_arm: int, assumed_baseline: float,
                minimum_detectable_effect: float, eligibility_require: dict[str, list[str]] | None = None,
                minimum_units_per_arm: int | None = None, alpha: float = 0.05, assumed_icc: float = 0.0,
                assumed_cluster_size: float = 1.0, n_permutations: int = 4999) -> dict[str, Any]:
    return {"schema": DESIGN_SCHEMA, "question": question,
            "eligibility": {"description": eligibility_description, "require": eligibility_require or {}},
            "control": control, "variant_definitions": variant_definitions, "strata_levels": strata_levels,
            "planned_units_per_arm": planned_units_per_arm,
            "minimum_units_per_arm": minimum_units_per_arm or _default_minimum(planned_units_per_arm, len(variant_definitions)),
            "alpha": alpha, "assumed_baseline": assumed_baseline,
            "minimum_detectable_effect": minimum_detectable_effect, "stopping": {"type": "fixed_horizon"},
            "missing_outcome_policy": "missing_counts_as_no_success",
            "analysis_method": "stratified_permutation_itt", "assumed_icc": assumed_icc,
            "assumed_cluster_size": assumed_cluster_size, "n_permutations": n_permutations}


class ExperimentRegistry:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, clock: Clock | None = None,
                 approvals: ap.ApprovalLedger | None = None):
        self.clock = clock or Clock()
        self.approvals = approvals
        self.journal = Journal(path, name="experiments")
        self._records: dict[str, dict[str, Any]] = {}
        for rec in self.journal:
            self._apply(rec["kind"], rec["body"])

    def _apply(self, kind: str, body: dict[str, Any]) -> None:
        if kind in ("draft", "draft_updated"):
            self._records[body["spec"]["id"]] = body
            return
        rec = self._records[body["id"]]
        if kind == "locked":
            rec["spec"]["status"] = "preregistered"
            rec["spec"]["design_locked_at"] = body["locked_at"]
            rec["locked_design_hash"] = body["design_hash"]
            rec["prereg_token_ref"] = body["approval_token_ref"]
        elif kind == "status":
            rec["spec"]["status"] = body["status"]
            rec.setdefault("status_history", []).append(body)
        elif kind == "consent":
            rec["consent_token_ref"] = body["token_id"]
        elif kind == "final_analysis":
            if rec.get("final_analysis"):
                raise IntegrityError(f"{body['id']}: a second result of record was appended to the journal")
            if content_hash(body["report"]) != body["report_hash"]:
                raise IntegrityError(f"{body['id']}: result of record does not match its own hash")
            rec["final_analysis"] = body

    # -- drafting --------------------------------------------------------
    def _build(self, spec: dict[str, Any], design: dict[str, Any], seed: str) -> dict[str, Any]:
        spec = dict(spec)
        spec.setdefault("secondary_metrics", [])
        spec.update(status="draft", design_locked_at=None, seed_digest=sha256_hex(seed))
        for key in ("title", "hypothesis", "stopping_rule", "analysis_plan"):
            if not clean_text(spec.get(key, ""), field=key):
                raise ValidationError(f"{key} must be written down before registration")
        validate(spec, "experiment")
        require_id(spec["id"], "experiment id")
        notes = _check_design(spec, design)
        return {"spec": spec, "design": design, "seed": seed, "design_notes": notes}

    def create_draft(self, spec: dict[str, Any], design: dict[str, Any], *, seed: str | None = None) -> dict[str, Any]:
        if spec.get("id") in self._records:
            raise RegistryError("experiment id already exists; ids are never reused")
        body = self._build(spec, design, seed or secrets.token_hex(32))
        self.journal.append("draft", body, self.clock.now_ts())
        self._apply("draft", body)
        return self.get(spec["id"])

    def update_draft(self, experiment_id: str, *, spec: dict[str, Any] | None = None,
                     design: dict[str, Any] | None = None) -> dict[str, Any]:
        rec = self._require(experiment_id)
        if rec["spec"]["status"] != "draft":
            raise DesignLockedError("design is locked; it cannot be changed after preregistration. "
                                    "Close this experiment and register a new one instead")
        new_spec = dict(spec) if spec is not None else {
            k: v for k, v in rec["spec"].items() if k not in ("status", "design_locked_at", "seed_digest")}
        if new_spec.get("id") != experiment_id:
            raise RegistryError("experiment id cannot change")
        body = self._build(new_spec, design if design is not None else rec["design"], rec["seed"])
        self.journal.append("draft_updated", body, self.clock.now_ts())
        self._apply("draft_updated", body)
        return self.get(experiment_id)

    # -- lifecycle -------------------------------------------------------
    def design_hash(self, experiment_id: str) -> str:
        rec = self._require(experiment_id)
        return compute_design_hash(rec["spec"], rec["design"])

    def preregister(self, experiment_id: str, *, token_id: str | None) -> dict[str, Any]:
        rec = self._require(experiment_id)
        if rec["spec"]["status"] != "draft":
            raise DesignLockedError("experiment is already preregistered")
        if self.approvals is None:
            raise ap.ApprovalError("approval gate is not configured; preregistration refused")
        digest = self.design_hash(experiment_id)
        self.approvals.consume(token_id, action=ap.PREREGISTER_EXPERIMENT, subject_id=experiment_id,
                               content_hash=digest, used_for="preregistration")
        body = {"id": experiment_id, "design_hash": digest, "locked_at": self.clock.now_ts(),
                "approval_token_ref": token_id}
        self.journal.append("locked", body, body["locked_at"])
        self._apply("locked", body)
        return self.get(experiment_id)

    def record_consent(self, experiment_id: str, *, token_id: str | None) -> None:
        rec = self._require(experiment_id)
        if "locked_design_hash" not in rec:
            raise RegistryError("consent is given to a locked design; preregister first")
        if self.approvals is None:
            raise ap.ApprovalError("approval gate is not configured")
        self.approvals.check(token_id, action=ap.EXPERIMENT_CONSENT, subject_id=experiment_id,
                             content_hash=rec["locked_design_hash"])
        body = {"id": experiment_id, "token_id": token_id}
        self.journal.append("consent", body, self.clock.now_ts())
        self._apply("consent", body)

    def check_consent(self, experiment_id: str) -> str | None:
        """Return the consent token ref, None if consent is not required, or raise ApprovalError."""
        rec = self._require(experiment_id)
        if not rec["spec"]["consent_required"]:
            return None
        if self.approvals is None:
            raise ap.ApprovalError("approval gate is not configured")
        self.approvals.check(rec.get("consent_token_ref"), action=ap.EXPERIMENT_CONSENT,
                             subject_id=experiment_id, content_hash=rec["locked_design_hash"])
        return rec["consent_token_ref"]

    _MOVES = {"preregistered": {"running", "closed"}, "running": {"paused", "closed"},
              "paused": {"running", "closed"}}

    def set_status(self, experiment_id: str, status: str, *, actor: str, reason: str = "") -> dict[str, Any]:
        rec = self._require(experiment_id)
        current = rec["spec"]["status"]
        if status not in self._MOVES.get(current, set()):
            raise RegistryError(f"cannot move experiment from {current!r} to {status!r}")
        if actor != "user" and not (actor == "system" and (current, status) == ("preregistered", "running")):
            raise RegistryError("only the user can pause, resume or close an experiment")
        body = {"id": experiment_id, "status": status, "from": current, "actor": actor,
                "reason": clean_text(reason, max_len=500), "at": self.clock.now_ts()}
        self.journal.append("status", body, body["at"])
        self._apply("status", body)
        return self.get(experiment_id)

    def record_final_analysis(self, experiment_id: str, report: dict[str, Any]) -> dict[str, Any]:
        """Journal the first terminal analysis: it becomes the result of record, once."""
        rec = self._require(experiment_id)
        if rec.get("final_analysis"):
            raise RegistryError("a final analysis is already recorded")
        body = {"id": experiment_id, "as_of": report["as_of"], "report_hash": content_hash(report),
                "report": report, "recorded_at": self.clock.now_ts()}
        self.journal.append("final_analysis", body, body["recorded_at"])
        self._apply("final_analysis", json.loads(json.dumps(body)))
        return body

    def final_analysis(self, experiment_id: str) -> dict[str, Any] | None:
        body = self._require(experiment_id).get("final_analysis")
        return json.loads(json.dumps(body)) if body else None

    # -- reading ---------------------------------------------------------
    def _require(self, experiment_id: str) -> dict[str, Any]:
        rec = self._records.get(experiment_id)
        if rec is None:
            raise KeyError(f"unknown experiment {experiment_id!r}")
        return rec

    def ids(self) -> list[str]:
        return list(self._records)

    def get(self, experiment_id: str) -> dict[str, Any]:
        """Public view. The randomisation seed is never returned."""
        rec = self._require(experiment_id)
        view = json.loads(json.dumps({k: v for k, v in rec.items() if k != "seed"}))
        view["design_hash"] = compute_design_hash(rec["spec"], rec["design"])
        return view

    def spec(self, experiment_id: str) -> dict[str, Any]:
        spec = json.loads(json.dumps(self._require(experiment_id)["spec"]))
        validate(spec, "experiment")
        return spec

    def _seed(self, experiment_id: str) -> str:
        return self._require(experiment_id)["seed"]

    def verify(self, experiment_id: str) -> str:
        """Raise unless the current design is exactly the one that was locked. Returns the hash."""
        self.journal.verify()
        rec = self._require(experiment_id)
        locked = rec.get("locked_design_hash")
        if locked is None:
            raise RegistryError("experiment is not preregistered")
        if compute_design_hash(rec["spec"], rec["design"]) != locked:
            raise IntegrityError("experiment design differs from the preregistered, locked design")
        if sha256_hex(rec["seed"]) != rec["spec"]["seed_digest"]:
            raise IntegrityError("randomisation seed does not match its published digest")
        return locked
