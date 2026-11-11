"""WS8 — experiment integrity attacks.

Two independent tools:

``probe_assignment_function``
    Black-box attacks on any ``assign(experiment_id, stratum, unit_id, variants, salt)``
    function: determinism, balance, cross-experiment independence, sensitivity to a
    post-assignment design change, key-encoding collisions and blank inputs.

``check_experiment_bundle``
    Validates what lane B *emits* (ExperimentSpec + assignment journal + application
    events + analysis) against the invariants in CONTRACTS.md: immutable design after
    first assignment, no double enrolment / crossover, assignment before exposure,
    sample-ratio mismatch, intention-to-treat counts, late outcomes and false certainty.

The assignment-journal and analysis shapes are NOT frozen contracts yet. The
shapes used here are lane E's proposal (see RFC on Issue #3) and every check that
depends on an unratified field says so in its finding code.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from . import contracts, synth
from .stats import chi_square_independence, chi_square_uniform, newcombe_diff_interval
from .timeutil import parse_rfc3339

AssignFn = Callable[[str, str, str, list[str], str], str]

SUCCESS_EVENTS = {"recruiter_screen_within_window": {"recruiter_screen", "interview", "offer"}}
DESIGN_FIELDS = (
    "id", "hypothesis", "variants", "assignment_unit", "strata", "primary_metric", "stopping_rule",
    "consent_required", "outcome_window_days", "secondary_metrics", "analysis_plan",
)  # fmt: skip
DECISIONS = ("INCONCLUSIVE", "DIFFERENCE-DETECTED", "NO-MEANINGFUL-DIFFERENCE")
MIN_ARM_N = 30
SRM_ALPHA = 0.001


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # high | medium | low
    detail: str

    def __str__(self) -> str:
        return f"{self.code} [{self.severity}] {self.detail}"


def codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings}


# --------------------------------------------------------------------------- assignment probes


def probe_assignment_function(assign: AssignFn, n_units: int = 3000) -> list[Finding]:
    """Black-box integrity probes. Deterministic: uses fixed unit ids, no randomness."""
    findings: list[Finding] = []
    units = [f"unit-{i:05d}" for i in range(n_units)]
    ab = ["A", "B"]

    def run(
        exp: str = "exp-1", stratum: str = "s1", variants: list[str] | None = None, salt: str = "salt-1"
    ) -> list[str]:
        chosen = variants if variants is not None else ab
        return [assign(exp, stratum, u, list(chosen), salt) for u in units]

    base = run()
    if run() != base:
        findings.append(
            Finding("ASSIGN-NONDETERMINISTIC", "high", "same inputs produced different assignments")
        )
        return findings
    if not set(base) <= set(ab):
        findings.append(Finding("ASSIGN-UNKNOWN-VARIANT", "high", f"returned {sorted(set(base) - set(ab))}"))

    for variants in (ab, ["A", "B", "C"]):
        for stratum in ("fde", "backend", "field-cto", "platform"):
            got = Counter(run(stratum=stratum, variants=variants))
            _, _, p = chi_square_uniform([got.get(v, 0) for v in variants])
            if p < 1e-4:
                findings.append(
                    Finding(
                        "ASSIGN-IMBALANCE",
                        "high",
                        f"{len(variants)} arms, stratum {stratum}: {dict(got)} p={p:.2e}",
                    )
                )

    other = run(exp="exp-2")
    table = [[sum(a == x and b == y for a, b in zip(base, other, strict=True)) for y in ab] for x in ab]
    if chi_square_independence(table)[2] < 1e-4:
        findings.append(Finding("ASSIGN-CORRELATED-ACROSS-EXPERIMENTS", "high", f"2x2 table {table}"))
    if run(salt="salt-2") == base:
        findings.append(Finding("ASSIGN-SALT-IGNORED", "high", "assignment is predictable without the salt"))
    if run(stratum="s2") == base:
        findings.append(Finding("ASSIGN-STRATUM-IGNORED", "low", "stratum does not enter the assignment key"))

    flipped = run(variants=["B", "A"])
    moved = sum(a != b for a, b in zip(base, flipped, strict=True)) / n_units
    if moved > 0.02:
        findings.append(
            Finding(
                "ASSIGN-VARIANT-ORDER-SENSITIVE",
                "medium",
                f"reordering the variant list changes {moved:.0%} of assignments: the list order is part of the "
                "design and must be frozen with it",
            )
        )
    widened = run(variants=["A", "B", "C"])
    crossed = sum(a != b and b in ab for a, b in zip(base, widened, strict=True)) / n_units
    if crossed > 0.02:
        findings.append(
            Finding(
                "ASSIGN-RESHUFFLE-ON-DESIGN-CHANGE",
                "high",
                f"adding a variant and recomputing moves {crossed:.0%} of units between the EXISTING arms: "
                "assignments must be journaled at first exposure, never recomputed",
            )
        )

    probe_units = units[:300]
    for sep in ("␟", "|", ":", "/", "\x00", "-", "_", " ", "\t"):
        try:
            left = [assign(f"exp{sep}x", "y", u, ab, "salt-1") for u in probe_units]
            right = [assign("exp", f"x{sep}y", u, ab, "salt-1") for u in probe_units]
        except (ValueError, TypeError):
            continue
        if left == right:
            findings.append(
                Finding(
                    "ASSIGN-KEY-COLLISION",
                    "medium",
                    f"key encoding is not injective: (experiment_id='exp<SEP>x', stratum='y') and "
                    f"(experiment_id='exp', stratum='x<SEP>y') give identical assignments for all "
                    f"{len(probe_units)} units when <SEP> is U+{ord(sep):04X}",
                )
            )

    blank_probes: tuple[tuple[str, tuple[str, str, str, list[str], str]], ...] = (
        ("STRATUM", ("exp-1", "", "unit-1", ab, "salt-1")),
        ("UNIT", ("exp-1", "s1", "   ", ab, "salt-1")),
        ("SALT", ("exp-1", "s1", "unit-1", ab, " ")),
        ("EXPERIMENT", (" ", "s1", "unit-1", ab, "salt-1")),
        ("VARIANT", ("exp-1", "s1", "unit-1", ["A", " "], "salt-1")),
    )
    for label, arguments in blank_probes:
        try:
            assign(*arguments)
        except (ValueError, TypeError):
            continue
        findings.append(
            Finding(f"ASSIGN-ACCEPTS-BLANK-{label}", "low", "blank/whitespace input accepted without error")
        )
    return findings


# --------------------------------------------------------------------------- bundle checks


def design_digest(spec: dict[str, Any]) -> str:
    """Proposed canonical digest of the frozen design (order of ``variants`` matters)."""
    design = {k: spec.get(k) for k in DESIGN_FIELDS}
    blob = json.dumps(design, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _success_types(spec: dict[str, Any]) -> set[str]:
    return SUCCESS_EVENTS.get(str(spec.get("primary_metric")), {"recruiter_screen", "interview", "offer"})


def itt_counts(
    spec: dict[str, Any], assignments: list[dict[str, Any]], events: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Lane E's own intention-to-treat tally: per variant ``n_assigned`` / ``n_success`` and the counted event ids.

    One success per assigned unit at most; only events inside the outcome window,
    with a known outcome status and not imported history, are counted.
    """
    window = timedelta(days=int(spec.get("outcome_window_days") or 30))
    success = _success_types(spec)
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("experiment_id") == spec.get("id"):
            by_app[str(event.get("application_id"))].append(event)
    arms: dict[str, dict[str, int]] = {
        str(v): {"n_assigned": 0, "n_success": 0} for v in spec.get("variants", [])
    }
    counted: list[str] = []
    for assignment in assignments:
        arm = arms.setdefault(str(assignment.get("variant")), {"n_assigned": 0, "n_success": 0})
        arm["n_assigned"] += 1
        assigned_at = parse_rfc3339(assignment.get("assigned_at"))
        hit: str | None = None
        for app_id in assignment.get("application_ids", []):
            for event in sorted(by_app.get(str(app_id), []), key=lambda e: str(e.get("recorded_at"))):
                when = parse_rfc3339(event.get("occurred_at") or event.get("recorded_at"))
                eligible = (
                    event.get("event_type") in success
                    and event.get("outcome_status") != "unknown"
                    and event.get("actor") != "import"
                    and assigned_at is not None
                    and when is not None
                    and assigned_at <= when <= assigned_at + window
                )
                if eligible and hit is None:
                    hit = str(event.get("id"))
        if hit is not None:
            arm["n_success"] += 1
            counted.append(hit)
    return arms, sorted(counted)


def honest_analysis(
    spec: dict[str, Any], assignments: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Reference ITT analysis in the proposed ExperimentAnalysis shape (two-arm comparison: last vs first variant)."""
    arms, counted = itt_counts(spec, assignments, events)
    variants = [str(v) for v in spec["variants"]]
    a, b = arms[variants[0]], arms[variants[-1]]
    estimate, low, high = newcombe_diff_interval(
        a["n_success"], a["n_assigned"], b["n_success"], b["n_assigned"]
    )
    small = min(a["n_assigned"], b["n_assigned"]) < MIN_ARM_N
    decision = "DIFFERENCE-DETECTED" if (low > 0 or high < 0) and not small else "INCONCLUSIVE"
    warnings = ["Synthetic data. Interval is for a difference in proportions; it is not a hiring forecast."]
    if small:
        warnings.append(f"Small sample: fewer than {MIN_ARM_N} units in an arm.")
    if len(variants) > 2:
        warnings.append("More than two arms: pairwise comparisons need a multiple-testing correction.")
    return {
        "experiment_id": spec["id"],
        "spec_digest": design_digest(spec),
        "estimand": "ITT",
        "arms": [{"variant": v, **arms[v]} for v in variants],
        "effect": {
            "contrast": f"{variants[-1]} - {variants[0]}",
            "estimate": estimate,
            "ci_low": low,
            "ci_high": high,
            "ci_level": 0.95,
            "method": "newcombe-hybrid-score",
        },  # fmt: skip
        "decision": decision,
        "counted_event_ids": counted,
        "warnings": warnings,
    }


def check_experiment_bundle(
    spec: dict[str, Any],
    assignments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    *,
    strict_digest: bool = False,
) -> list[Finding]:
    """Every integrity problem found in one experiment's emitted artifacts."""
    out: list[Finding] = []
    assignments = [assignment_view(a) for a in assignments]
    if analysis is not None:
        analysis = analysis_view(analysis, spec)
    for problem in contracts.check("experiment", spec):
        out.append(Finding("EXP-SPEC-INVALID", "high", problem))
    if spec.get("status") == "draft" and assignments:
        out.append(
            Finding("EXP-ASSIGNED-WHILE-DRAFT", "high", f"{len(assignments)} assignments on a draft design")
        )
    variants = [str(v) for v in spec.get("variants", [])]
    locked_at = parse_rfc3339(spec.get("design_locked_at"))

    digests = Counter(str(a.get("spec_digest")) for a in assignments)
    if len(digests) > 1:
        out.append(
            Finding(
                "EXP-DESIGN-CHANGED-AFTER-ASSIGNMENT",
                "high",
                f"assignments reference {len(digests)} design digests",
            )
        )
    canonical = design_digest(spec)
    if assignments and canonical not in digests:
        out.append(
            Finding(
                "EXP-DESIGN-CHANGED-AFTER-ASSIGNMENT" if strict_digest else "EXP-DIGEST-UNRATIFIED-MISMATCH",
                "high" if strict_digest else "low",
                "current spec does not hash to the digest the assignments were made under "
                "(proposed canonical digest; see RFC)",
            )
        )

    seen_units: Counter[str] = Counter(str(a.get("unit_id")) for a in assignments)
    for unit, count in sorted(seen_units.items()):
        if count > 1:
            out.append(Finding("EXP-DOUBLE-ENROLLMENT", "high", f"unit {unit!r} assigned {count} times"))
    app_owner: dict[str, str] = {}
    app_assignment: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        variant = str(assignment.get("variant"))
        if variant not in variants:
            out.append(Finding("EXP-UNKNOWN-VARIANT", "high", f"{assignment.get('id')}: variant {variant!r}"))
        if assignment.get("experiment_id") != spec.get("id"):
            out.append(
                Finding(
                    "EXP-FOREIGN-ASSIGNMENT", "high", f"{assignment.get('id')} belongs to another experiment"
                )
            )
        assigned_at = parse_rfc3339(assignment.get("assigned_at"))
        if assigned_at is None:
            out.append(
                Finding(
                    "EXP-ASSIGNMENT-NO-TIMESTAMP",
                    "high",
                    f"{assignment.get('id')}: assigned_at missing/invalid",
                )
            )
        elif locked_at is not None and assigned_at < locked_at:
            out.append(
                Finding(
                    "EXP-ASSIGNED-BEFORE-LOCK", "high", f"{assignment.get('id')} predates design_locked_at"
                )
            )
        for app_id in assignment.get("application_ids", []):
            if app_id in app_owner and app_owner[app_id] != variant:
                out.append(
                    Finding(
                        "EXP-MULTIPLE-EXPOSURE", "high", f"application {app_id!r} exposed to two variants"
                    )
                )
            app_owner[str(app_id)] = variant
            app_assignment[str(app_id)] = assignment

    job_variants: dict[str, set[str]] = defaultdict(set)
    by_id = {str(e.get("id")): e for e in events}
    for event in events:
        if event.get("experiment_id") != spec.get("id"):
            continue
        app_id = str(event.get("application_id"))
        if app_id not in app_owner:
            out.append(
                Finding(
                    "EXP-EXPOSURE-WITHOUT-ASSIGNMENT",
                    "high",
                    f"event {event.get('id')} has no journaled assignment",
                )
            )
            continue
        job_variants[str(event.get("job_id"))].add(app_owner[app_id])
        assigned_at = parse_rfc3339(app_assignment[app_id].get("assigned_at"))
        recorded = parse_rfc3339(event.get("recorded_at"))
        if event.get("event_type") == "submitted" and assigned_at and recorded and recorded < assigned_at:
            out.append(
                Finding(
                    "EXP-EXPOSURE-BEFORE-ASSIGNMENT",
                    "high",
                    f"application {app_id!r} was submitted before assignment",
                )
            )
    for job_id, seen in sorted(job_variants.items()):
        if len(seen) > 1:
            out.append(Finding("EXP-CROSSOVER", "high", f"job {job_id!r} received variants {sorted(seen)}"))

    if len(assignments) >= 40 and len(variants) >= 2:
        counts = Counter(str(a.get("variant")) for a in assignments)
        _, _, p = chi_square_uniform([counts.get(v, 0) for v in variants])
        if p < SRM_ALPHA:
            out.append(Finding("EXP-SAMPLE-RATIO-MISMATCH", "high", f"{dict(counts)} p={p:.2e}"))

    if analysis is None:
        return out
    arms, counted = itt_counts(spec, assignments, events)
    reported = {str(arm.get("variant")): arm for arm in analysis.get("arms", [])}
    if analysis.get("spec_digest") not in digests and assignments:
        out.append(
            Finding(
                "EXP-ANALYSIS-DESIGN-MISMATCH", "high", "analysis was run under a different design digest"
            )
        )
    if str(analysis.get("estimand", "")).upper() != "ITT":
        out.append(Finding("EXP-NOT-INTENTION-TO-TREAT", "medium", f"estimand {analysis.get('estimand')!r}"))
    for variant in variants:
        got = reported.get(variant, {})
        if got.get("n_assigned") != arms[variant]["n_assigned"]:
            out.append(
                Finding(
                    "EXP-ANALYSIS-COUNT-MISMATCH",
                    "high",
                    f"arm {variant}: analysis n_assigned={got.get('n_assigned')} but journal has "
                    f"{arms[variant]['n_assigned']} (units dropped after assignment?)",
                )
            )
    window = timedelta(days=int(spec.get("outcome_window_days") or 30))
    for event_id in analysis.get("counted_event_ids", []):
        counted_event = by_id.get(str(event_id))
        if counted_event is None:
            out.append(
                Finding("EXP-COUNTED-UNKNOWN-EVENT", "high", f"{event_id!r} is not in the event stream")
            )
            continue
        owner = app_assignment.get(str(counted_event.get("application_id")))
        assigned_at = parse_rfc3339(owner.get("assigned_at")) if owner else None
        when = parse_rfc3339(counted_event.get("occurred_at") or counted_event.get("recorded_at"))
        if assigned_at and when and when > assigned_at + window:
            out.append(
                Finding(
                    "EXP-LATE-OUTCOME-COUNTED",
                    "high",
                    f"{event_id!r} is outside the {window.days}-day window",
                )
            )
        if counted_event.get("outcome_status") == "unknown":
            out.append(
                Finding("EXP-UNKNOWN-OUTCOME-COUNTED", "high", f"{event_id!r} has outcome_status 'unknown'")
            )
        if counted_event.get("actor") == "import":
            out.append(
                Finding("EXP-OBSERVATIONAL-AS-RANDOMIZED", "high", f"{event_id!r} is imported history")
            )
    effect = analysis.get("effect") or {}
    numeric = all(isinstance(effect.get(k), (int, float)) for k in ("estimate", "ci_low", "ci_high"))
    if not numeric:
        out.append(Finding("EXP-NO-UNCERTAINTY", "high", "analysis reports no interval"))
    decision = analysis.get("decision")
    if decision not in DECISIONS:
        out.append(
            Finding("EXP-DECISION-VOCABULARY", "medium", f"decision {decision!r} is not one of {DECISIONS}")
        )
    if contracts.probability_claims(analysis) or affirmative_winner(analysis):
        out.append(
            Finding("EXP-FALSE-CERTAINTY", "high", "analysis text declares a winner or a hiring probability")
        )
    if decision == "DIFFERENCE-DETECTED" and len(variants) >= 2:
        a, b = arms[variants[0]], arms[variants[-1]]
        _, low, high = newcombe_diff_interval(
            a["n_success"], a["n_assigned"], b["n_success"], b["n_assigned"]
        )
        if low <= 0 <= high:
            out.append(
                Finding(
                    "EXP-FALSE-CERTAINTY",
                    "high",
                    f"claims a difference but lane E's ITT 95% interval is [{low:+.3f}, {high:+.3f}] "
                    f"({b['n_success']}/{b['n_assigned']} vs {a['n_success']}/{a['n_assigned']})",
                )
            )
        if min(a["n_assigned"], b["n_assigned"]) < MIN_ARM_N:
            out.append(
                Finding("EXP-FALSE-CERTAINTY", "high", f"difference claimed with an arm below n={MIN_ARM_N}")
            )
    # Only comparable when the producer publishes which events it counted (asked of B on #3, Q3).
    # Without the list, late/unknown/imported-outcome exclusion is UNKNOWN, not a failure.
    reported_counted = analysis.get("counted_event_ids")
    if isinstance(reported_counted, list) and numeric and sorted(map(str, reported_counted)) != counted:
        out.append(
            Finding(
                "EXP-COUNTED-EVENTS-DIFFER",
                "medium",
                f"analysis counted {len(reported_counted)} events, lane E's ITT tally counts {len(counted)}",
            )
        )
    return out


_NEGATORS = re.compile(r"(?i)\b(?:no|not|never|without|withheld|withhold|withholding|cannot|none)\b")


def affirmative_winner(value: Any) -> bool:
    """True when any string in ``value`` *declares* a winner ("B is the winner").

    "No winner is declared" or "winner claim withheld" are the opposite and are not
    flagged. The first version used a bare substring and would have failed lane B's
    correct ``reasons_withheld`` texts.
    """
    if isinstance(value, dict):
        return any(affirmative_winner(v) for v in value.values())
    if isinstance(value, list):
        return any(affirmative_winner(v) for v in value)
    if not isinstance(value, str):
        return False
    for match in re.finditer(r"(?i)\bwinn(?:er|ing (?:variant|arm))\b", value):
        window = value[max(0, match.start() - 40) : match.end() + 25]
        if not _NEGATORS.search(window):
            return True
    return False


def assignment_view(assignment: dict[str, Any]) -> dict[str, Any]:
    """Accept lane B's journaled ``ExperimentAssignment`` (``assignment_id``, ``design_hash``, one
    ``application_id``) as well as E's proposed shape. Renames only."""
    if not isinstance(assignment, dict):
        return {}
    view = dict(assignment)
    view.setdefault("id", assignment.get("assignment_id"))
    view.setdefault("spec_digest", assignment.get("design_hash"))
    if "application_ids" not in view:
        app = assignment.get("application_id")
        view["application_ids"] = [app] if app else []
    return view


B_REPORT_SCHEMA = "careertwin.b.analysis-report.v1"
B_VERDICT_TO_DECISION = {
    "EVIDENCE_FOR_DIFFERENCE": "DIFFERENCE-DETECTED",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "INTERIM_NO_CLAIM": "INCONCLUSIVE",
    "EXPLORATORY_NO_CLAIM": "INCONCLUSIVE",
    "NOT_ANALYZABLE": "INCONCLUSIVE",
    "OBSERVATIONAL_ONLY": "INCONCLUSIVE",
}


def analysis_view(analysis: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Map a lane-native analysis report onto the fields E checks. Renames only, no recomputation.

    Lane B's ``careertwin.b.analysis-report.v1`` (published on #5): per-arm ``flow.by_arm.<v>.units``
    and ``.success``, effect + interval in ``comparisons[0]``, ``verdict``, ``design_hash``. B does not
    publish counted event ids, so ``counted_event_ids`` is left absent (checks report UNKNOWN).
    Reports already in E's shape pass through unchanged.
    """
    if analysis.get("schema") != B_REPORT_SCHEMA:
        return analysis
    by_arm = (analysis.get("flow") or {}).get("by_arm") or {}
    arms = [
        {
            "variant": str(v),
            "n_assigned": by_arm.get(v, {}).get("units"),
            "n_success": by_arm.get(v, {}).get("success"),
        }
        for v in spec.get("variants", [])
    ]
    comparisons = analysis.get("comparisons") or []
    effect: dict[str, Any] = {}
    if comparisons and isinstance(comparisons[0], dict):
        first = comparisons[0]
        interval = first.get("interval_95") or {}
        effect = {
            "contrast": f"{first.get('treatment')} - {first.get('control')}", "estimate": first.get("estimate"),
            "ci_low": interval.get("low"), "ci_high": interval.get("high"), "ci_level": 0.95,
            "method": interval.get("method"),
        }  # fmt: skip
    plan = str(spec.get("analysis_plan", "")).lower()
    view = {
        "experiment_id": analysis.get("experiment_id"),
        "spec_digest": analysis.get("design_hash"),
        # B @ 84757ed writes "ITT on job units; ..." — the abbreviation counts (first mapping missed it).
        "estimand": "ITT" if re.search(r"intention[- ]to[- ]treat|\bitt\b", plan) else "UNSTATED",
        "arms": arms,
        "effect": effect,
        "decision": B_VERDICT_TO_DECISION.get(str(analysis.get("verdict")), str(analysis.get("verdict"))),
        "warnings": analysis.get("warnings", []),
        "lane_text": [
            analysis.get("claim"),
            analysis.get("reasons_withheld"),
            analysis.get("interpretation_limits"),
        ],
    }
    if analysis.get("phase") not in ("FINAL",) and view["decision"] == "DIFFERENCE-DETECTED":
        view["decision"] = "DIFFERENCE-DETECTED-NOT-FINAL"
    return view


# --------------------------------------------------------------------------- synthetic world


def synth_experiment_world(
    seed: int = 0,
    n_units: int = 160,
    rate_a: float = 0.25,
    rate_b: float = 0.25,
    assign: AssignFn | None = None,
    job_ids: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """An honest synthetic experiment: spec, assignment journal, events, analysis.

    Outcomes are simulated with the stated rates — they are synthetic by design
    and say nothing about real résumés.
    """
    if job_ids is not None and len(job_ids) < n_units:
        raise ValueError("need one distinct job per unit to avoid crossover")
    rng = random.Random(f"world:{seed}")
    spec = synth.gen_experiments(3, seed=seed)[2]  # index 2 is 'running' with a lock
    spec = dict(spec, variants=["A", "B"], assignment_unit="application", outcome_window_days=30)
    digest = design_digest(spec)
    salt = f"synthetic-salt-{seed}"

    def default_assign(exp: str, stratum: str, unit: str, variants: list[str], salt_: str) -> str:
        key = json.dumps([salt_, exp, stratum, unit]).encode("utf-8")
        return variants[int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % len(variants)]

    chooser = assign or default_assign
    assignments: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    families = list(synth.ROLE_FAMILIES)
    for i in range(n_units):
        app_id = f"app-exp-s{seed}-{i:04d}"
        stratum = families[i % len(families)]
        variant = chooser(str(spec["id"]), stratum, app_id, list(spec["variants"]), salt)
        t0 = 200 + i * 50
        assignments.append({
            "id": f"asg-s{seed}-{i:04d}", "experiment_id": spec["id"], "spec_digest": digest, "unit_id": app_id,
            "stratum": stratum, "variant": variant, "assigned_at": synth.ts(t0), "application_ids": [app_id],
        })  # fmt: skip
        steps = ["saved", "drafted", "approved", "submitted"]
        if rng.random() < (rate_a if variant == "A" else rate_b):
            steps.append("recruiter_screen")
        for k, step in enumerate(steps):
            when = t0 + 5 + k * 5 + (60 * 24 * rng.randrange(2, 20) if step == "recruiter_screen" else 0)
            events.append({
                "id": f"evt-exp-s{seed}-{len(events):06d}", "application_id": app_id,
                "job_id": job_ids[i] if job_ids else f"job-syn-s{seed}-{i:06d}", "resume_variant_id": f"rv-{variant}",
                "event_type": step, "recorded_at": synth.ts(when),
                "occurred_at": synth.ts(when) if step == "recruiter_screen" else None,
                "actor": "user", "source": "synthetic-generator", "outcome_status": "observed",
                "experiment_id": spec["id"],
                "approval_token_ref": f"approval-{app_id}" if step in ("approved", "submitted") else None,
            })  # fmt: skip
    return spec, assignments, events, honest_analysis(spec, assignments, events)
