"""Analysis: intention-to-treat randomisation inference with withheld claims.

A "difference" claim is made only when ALL of these hold:
  * the design is preregistered, locked and unaltered; the assignment journal
    re-derives exactly from the seed (no crossover, no edits)
  * the fixed horizon was reached and every outcome window has closed
    (interim looks and early stops never yield a claim — no peeking)
  * every arm has at least the preregistered minimum number of units
  * the Holm-adjusted stratified permutation p-value is below alpha AND the
    confidence interval excludes zero
  * the conclusion survives worst-case imputation of missing outcomes, and
    does not depend on the user's own corrections to recorded events
  * the analysis time is not in the future, and it is the first terminal
    analysis (it is journaled as the result of record)
Otherwise the verdict is INCONCLUSIVE (never "no effect") with the reasons.

Units are analysed as randomised: an employer cluster is one observation.
Retrospective, non-randomised comparisons go through observational_summary()
and can only ever return OBSERVATIONAL_ONLY.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from ct_applications.core import CareerTwinError, content_hash, fmt_ts, parse_ts, sha256_hex
from ct_applications.events import ApplicationStore

from . import stats
from .assignment import AssignmentEngine
from .outcomes import application_outcome
from .registry import METRICS, ExperimentRegistry

REPORT_SCHEMA = "careertwin.b.analysis-report.v1"
LIMITS = [
    "An effect estimate is a difference in response rates inside this experiment's eligibility universe; "
    "it is not a probability of being hired and does not transfer to other roles or employers.",
    "Outcomes are self-reported or imported by the user; they are not verified with employers.",
    "One candidate, one job market period: results describe this candidate's applications only.",
]
DIFFERENTIAL_ATTRITION = 0.10


def _seed_int(*parts: str) -> int:
    return int(sha256_hex("|".join(parts))[:16], 16)


def _unit_table(assignments: list[dict[str, Any]], store: ApplicationStore, metric: str, window: int,
                as_of: datetime, uncorrected: bool = False) -> dict[str, dict[str, Any]]:
    units: dict[str, dict[str, Any]] = {}
    events_of = store.uncorrected_events if uncorrected else store.effective_events
    for a in assignments:
        outcome = application_outcome(events_of(a["application_id"]), metric=metric, window_days=window,
                                      as_of=as_of, assigned_at=parse_ts(a["assigned_at"]))
        unit = units.setdefault(a["unit_id"], {"arm": a["variant"], "stratum": a["stratum"], "apps": []})
        unit["apps"].append({"application_id": a["application_id"], "status": outcome["status"],
                             "flags": outcome["flags"], "counted_event_id": outcome["counted_event_id"],
                             "exposure_deadline": fmt_ts(parse_ts(a["assigned_at"]) + timedelta(days=window)),
                             "withdrawn": any(n["kind"] == "withdrawn" for n in a["annotations"]),
                             "deviation": any(n["kind"] == "deviation" for n in a["annotations"])})
    for unit in units.values():
        statuses = [x["status"] for x in unit["apps"]]
        unit["censored"] = "censored" in statuses
        unit["y"] = sum(s == "success" for s in statuses) / len(statuses)       # missing -> 0 (preregistered)
        known = [s for s in statuses if s in ("success", "no_success")]
        unit["y_complete"] = sum(s == "success" for s in known) / len(known) if known else None
        unit["n_missing"] = statuses.count("missing")
    return units


def _strata(units: Iterable[dict[str, Any]], treatment: str, control: str,
            value: Callable[[dict[str, Any]], float | None]) -> tuple[list[stats.Stratum], list[str]]:
    by: dict[str, tuple[list[float], list[float]]] = {}
    for u in units:
        v = value(u)
        if v is None or u["arm"] not in (treatment, control):
            continue
        by.setdefault(u["stratum"], ([], []))[0 if u["arm"] == treatment else 1].append(v)
    used = [(t, c) for s, (t, c) in sorted(by.items()) if t and c]
    dropped = [s for s, (t, c) in sorted(by.items()) if not (t and c)]
    return used, dropped


def _interval(strata: list[stats.Stratum]) -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    binary = all(v in (0.0, 1.0) for t, c in strata for v in list(t) + list(c))
    if binary and len(strata) == 1:
        t, c = strata[0]
        low, high = stats.newcombe_interval(int(sum(t)), len(t), int(sum(c)), len(c))
        return {"low": low, "high": high, "method": "newcombe_hybrid_score"}, notes
    ci = stats.stratified_t_interval(strata)
    method = "stratified_t_on_unit_means"
    if ci is None:
        pooled = [([v for t, _ in strata for v in t], [v for _, c in strata for v in c])]
        ci = stats.stratified_t_interval(pooled)
        method = "pooled_t_on_unit_means"
        notes.append("CI_POOLED_OVER_STRATA: a stratum had fewer than 2 units in an arm")
    if ci is None:
        return None, notes + ["CI_NOT_ESTIMABLE"]
    notes.append("CI_APPROXIMATE: t-interval on unit means; the permutation p-value is the primary test")
    return {"low": ci["low"], "high": ci["high"], "method": method}, notes


def infer(units: list[dict[str, Any]], *, variants: list[str], control: str, minimum_units: int,
          alpha: float, n_perm: int, seed_key: str, metric: str, unit_label: str) -> dict[str, Any]:
    """Decision core shared by analyze() and the calibration simulations.

    `units`: dicts with arm, stratum, y, y_complete, n_missing, apps (list).
    Returns comparisons, claims and the reasons any claim was withheld.
    """
    n_units = {v: sum(u["arm"] == v for u in units) for v in variants}
    n_apps = {v: sum(len(u["apps"]) for u in units if u["arm"] == v) for v in variants}
    n_missing = {v: sum(u["n_missing"] for u in units if u["arm"] == v) for v in variants}
    comparisons: list[dict[str, Any]] = []
    withheld: list[str] = []
    treatments = [v for v in variants if v != control]
    raw_p: list[float] = []
    for trt in treatments:
        comp: dict[str, Any] = {"treatment": trt, "control": control, "units_treatment": n_units[trt],
                                "units_control": n_units[control], "blocking": [], "notes": []}
        comparisons.append(comp)
        strata, dropped = _strata(units, trt, control, lambda u: u["y"])
        comp["strata_used"], comp["strata_dropped"] = len(strata), dropped
        compared = {trt: sum(len(t) for t, _ in strata), control: sum(len(c) for _, c in strata)}
        comp["units_compared_treatment"], comp["units_compared_control"] = compared[trt], compared[control]
        for arm in (trt, control):
            if compared[arm] < minimum_units:
                comp["blocking"].append(f"TOO_FEW_UNITS: arm {arm!r} has {compared[arm]} comparable units; "
                                        f"preregistered minimum is {minimum_units}")
        if dropped:
            comp["notes"].append("STRATA_DROPPED: no within-stratum comparison possible in " + ", ".join(dropped))
            lost = n_units[trt] + n_units[control] - compared[trt] - compared[control]
            if lost > 0.10 * (n_units[trt] + n_units[control]):
                comp["blocking"].append(f"TOO_MANY_UNITS_DROPPED: {lost} units sit in strata without both arms")
        if not strata:
            comp["blocking"].append("NO_COMPARABLE_STRATUM")
            raw_p.append(1.0)
            continue
        test = stats.stratified_permutation_test(
            strata, seed=_seed_int(seed_key, trt, "primary"), n_perm=n_perm)
        ci, notes = _interval(strata)
        comp.update(estimate=test["statistic"], p_value=test["p_value"], n_permutations=test["n_perm"],
                    interval_95=ci)
        comp["notes"] += notes
        raw_p.append(test["p_value"])
        # sensitivity analyses
        cc, _ = _strata(units, trt, control, lambda u: u["y_complete"])
        comp["sensitivity"] = {"complete_case_estimate": stats.stratified_difference(cc) if cc else None}
        miss = {arm: n_missing[arm] / n_apps[arm] for arm in (trt, control)}
        comp["missing_fraction"] = miss
        if abs(miss[trt] - miss[control]) > DIFFERENTIAL_ATTRITION:
            comp["notes"].append("DIFFERENTIAL_ATTRITION: missing outcomes differ between arms by more than "
                                 f"{DIFFERENTIAL_ATTRITION:.0%}")
        if any(u["n_missing"] for u in units if u["arm"] in (trt, control)):
            trailing = control if test["statistic"] > 0 else trt

            def worst(u: dict[str, Any]) -> float:  # missing counts as success only in the trailing arm
                if u["arm"] != trailing or not u["n_missing"]:
                    return u["y"]
                return u["y"] + u["n_missing"] / len(u["apps"])

            ws, _ = _strata(units, trt, control, worst)
            wtest = stats.stratified_permutation_test(
                ws, seed=_seed_int(seed_key, trt, "worst"), n_perm=n_perm)
            comp["sensitivity"].update(worst_case_estimate=wtest["statistic"], worst_case_p_value=wtest["p_value"])

    adjusted = stats.holm(raw_p)
    claims = []
    for comp, p_adj in zip(comparisons, adjusted):
        if "p_value" not in comp:
            withheld.extend(f"{comp['treatment']} vs {comp['control']}: {x}" for x in comp["blocking"])
            continue
        comp["p_value_holm"] = p_adj
        ci = comp["interval_95"]
        if p_adj >= alpha:
            comp["blocking"].append(f"NOT_SIGNIFICANT: adjusted p = {p_adj:.3f} >= alpha {alpha}")
        if ci is None:
            comp["blocking"].append("NO_CONFIDENCE_INTERVAL")
        elif ci["low"] <= 0 <= ci["high"]:
            comp["blocking"].append("INTERVAL_INCLUDES_ZERO: the data are compatible with no difference, "
                                    f"and with differences from {ci['low']:+.3f} to {ci['high']:+.3f}")
        worst_p = comp["sensitivity"].get("worst_case_p_value")
        if worst_p is not None and (worst_p * len(treatments) >= alpha or
                                    comp["sensitivity"]["worst_case_estimate"] * comp["estimate"] <= 0):
            comp["blocking"].append("NOT_ROBUST_TO_MISSING_OUTCOMES: the result disappears under worst-case "
                                    "imputation of missing outcomes")
        if not comp["blocking"]:
            direction = "higher" if comp["estimate"] > 0 else "lower"
            claims.append(
                f"Variant {comp['treatment']!r} had a {direction} {metric} rate than control {comp['control']!r} "
                f"(difference {comp['estimate']:+.3f}, 95% interval {ci['low']:+.3f} to {ci['high']:+.3f}, "
                f"adjusted p = {p_adj:.4f}, {comp['units_compared_treatment']}+{comp['units_compared_control']} "
                f"{unit_label} units).")
        withheld.extend(f"{comp['treatment']} vs {comp['control']}: {b}" for b in comp["blocking"])

    return {"comparisons": comparisons, "claims": claims, "withheld": withheld}


def analyze(experiment_id: str, *, registry: ExperimentRegistry, engine: AssignmentEngine,
            store: ApplicationStore, as_of: datetime, override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report on an experiment.

    * `as_of` may not lie in the future: an open window can never be scored as closed.
    * The first terminal analysis (FINAL or STOPPED_EARLY) is journaled as the
      result of record. From then on every report is computed at that same
      instant. If the data changed afterwards (late entries, corrections), the
      new numbers are shown as EXPLORATORY_NO_CLAIM next to the result of record.
    * Deviating analyses (`override`) are available only after the result of
      record exists, so they cannot be used to peek.
    """
    now = store.clock.now().replace(microsecond=0)
    as_of = as_of.replace(microsecond=0)  # reports are computed and recorded at whole seconds
    if as_of > now:
        raise CareerTwinError("as_of lies in the future; outcomes that have not happened cannot be analysed")
    final = registry.final_analysis(experiment_id)
    if final is None and as_of < now - timedelta(seconds=60):
        raise CareerTwinError("until the result of record exists, reports are computed at the current time only; "
                              "a past as_of would let the analysis instant be chosen after seeing the data")
    if final is None:
        if override:
            raise CareerTwinError("exploratory analyses are available only after the preregistered "
                                  "final analysis has been recorded")
        report = _analyze_core(experiment_id, registry=registry, engine=engine, store=store, as_of=as_of)
        if report["phase"] in ("FINAL", "STOPPED_EARLY"):
            body = registry.record_final_analysis(experiment_id, report)
            report = {**report, "result_of_record": {
                "report_hash": body["report_hash"], "recorded_at": body["recorded_at"], "as_of": report["as_of"],
                "verdict": report["verdict"], "claim": report["claim"], "unchanged": True}}
        return report
    record = {"report_hash": final["report_hash"], "recorded_at": final["recorded_at"],
              "as_of": final["as_of"], "verdict": final["report"]["verdict"], "claim": final["report"]["claim"]}
    current = _analyze_core(experiment_id, registry=registry, engine=engine, store=store,
                            as_of=parse_ts(final["as_of"]), override=override)
    if override or current["phase"] == "NOT_ANALYZABLE":
        return {**current, "result_of_record": record}
    if content_hash(final["report"]) != final["report_hash"]:
        raise CareerTwinError("result of record does not match its own hash")
    if content_hash(current) == final["report_hash"]:
        return {**current, "result_of_record": {**record, "unchanged": True}}  # recomputed, never the stored body
    current.update(verdict="EXPLORATORY_NO_CLAIM", claim=None)
    current["reasons_withheld"].insert(
        0, "DATA_CHANGED_AFTER_FINAL_ANALYSIS: events were added or corrected after the final analysis was "
           "recorded; the recorded result stands and this re-analysis supports no claim")
    return {**current, "result_of_record": {**record, "unchanged": False}}


def _analyze_core(experiment_id: str, *, registry: ExperimentRegistry, engine: AssignmentEngine,
                  store: ApplicationStore, as_of: datetime, override: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = registry.get(experiment_id)
    spec, design = rec["spec"], rec["design"]
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "experiment_id": experiment_id, "title": spec["title"],
        "as_of": fmt_ts(as_of), "evidence_type": "randomized_controlled", "design_hash": rec["design_hash"],
        "phase": "NOT_ANALYZABLE", "verdict": "NOT_ANALYZABLE", "claim": None, "reasons_withheld": [],
        "warnings": [n for n in rec.get("design_notes", []) if not n.startswith("planned_power")],
        "interpretation_limits": list(LIMITS), "comparisons": [], "preregistered": {
            "primary_metric": spec["primary_metric"], "outcome_window_days": spec["outcome_window_days"],
            "alpha": design["alpha"], "planned_units_per_arm": design["planned_units_per_arm"],
            "minimum_units_per_arm": design["minimum_units_per_arm"], "control": design["control"],
            "assignment_unit": spec["assignment_unit"], "strata": spec["strata"]},
    }
    withheld: list[str] = report["reasons_withheld"]
    try:
        registry.verify(experiment_id)
        engine.verify(experiment_id)
        store.journal.verify()
    except CareerTwinError as exc:
        withheld.append(f"INTEGRITY_FAILURE: {exc}")
        return report

    metric, window, alpha = spec["primary_metric"], spec["outcome_window_days"], design["alpha"]
    exploratory = bool(override)
    if override:
        if set(override) - {"primary_metric", "outcome_window_days", "alpha"}:
            raise CareerTwinError("override may change only primary_metric, outcome_window_days or alpha")
        metric = override.get("primary_metric", metric)
        window = override.get("outcome_window_days", window)
        alpha = override.get("alpha", alpha)
        if metric not in METRICS or not isinstance(window, int) or window < 1 or not (0 < alpha < 1):
            raise CareerTwinError("invalid override")
        report["deviation_from_preregistration"] = dict(override)

    assignments = [a for a in engine.assignments(experiment_id) if parse_ts(a["assigned_at"]) <= as_of]
    units = _unit_table(assignments, store, metric, window, as_of)
    variants, control = spec["variants"], design["control"]
    apps = [x for u in units.values() for x in u["apps"]]
    report["flow"] = {
        "screened_out_applications": len(engine.screened_out(experiment_id)),
        "assigned_applications": len(assignments), "assigned_units": len(units),
        "planned_units": design["planned_units_per_arm"] * len(variants),
        "by_arm": {v: {
            "units": sum(u["arm"] == v for u in units.values()),
            "applications": sum(len(u["apps"]) for u in units.values() if u["arm"] == v),
            **{s: sum(x["status"] == s for u in units.values() if u["arm"] == v for x in u["apps"])
               for s in ("success", "no_success", "censored", "missing")},
            "withdrawn": sum(x["withdrawn"] for u in units.values() if u["arm"] == v for x in u["apps"]),
            "deviations": sum(x["deviation"] for u in units.values() if u["arm"] == v for x in u["apps"]),
        } for v in variants}}
    flow = report["flow"]["by_arm"]
    corrected = sum(len(p["voided_event_ids"]) + len(p["amended_event_ids"])
                    for p in (store.projection(a["application_id"]) for a in assignments))
    report["flow"]["corrected_events"] = corrected
    if corrected:
        report["warnings"].append(f"OUTCOMES_CORRECTED: {corrected} event(s) of enrolled applications were voided "
                                  "or amended by the user; every correction is in the audit trail")
    data_flags = sorted({f for x in apps for f in x["flags"]})
    report["outcome_data_flags"] = data_flags

    any_censored = any(u["censored"] for u in units.values())
    enrolled_out = len(units) >= report["flow"]["planned_units"]
    if not units:
        report.update(phase="INTERIM", verdict="INTERIM_NO_CLAIM")
        withheld.append("NO_ASSIGNMENTS_YET")
        return report
    if any_censored or (not enrolled_out and spec["status"] != "closed"):
        report.update(phase="INTERIM", verdict="INTERIM_NO_CLAIM")
        withheld.append("OUTCOME_WINDOWS_STILL_OPEN: some outcomes are right-censored" if any_censored
                        else "ENROLMENT_INCOMPLETE: the planned sample has not been reached")
        withheld.append("NO_PEEKING: a fixed-horizon design gives no valid interim test; "
                        "arm-level results are withheld until the final analysis")
        known = [x for x in apps if x["status"] in ("success", "no_success")]
        report["blinded_progress"] = {
            "applications_with_known_outcome": len(known),
            "pooled_success_rate": (sum(x["status"] == "success" for x in known) / len(known)) if known else None}
        for arm in flow.values():  # blind every arm-level outcome count (differencing would unblind them)
            for key in ("success", "no_success", "censored", "missing", "withdrawn", "deviations"):
                arm[key] = None
        return report

    stopped_early = not enrolled_out
    report["phase"] = "STOPPED_EARLY" if stopped_early else "FINAL"
    # outcome evidence (terminal reports only: per-application rows would unblind an interim report)
    report["counted_outcomes"] = sorted(
        ({"application_id": x["application_id"], "unit_id": uid, "arm": u["arm"], "status": x["status"],
          "counted_event_id": x["counted_event_id"], "flags": x["flags"]}
         for uid, u in units.items() for x in u["apps"]), key=lambda r: r["application_id"])
    if stopped_early:
        withheld.append("STOPPED_BEFORE_PLANNED_SAMPLE: the experiment was closed early; with a fixed-horizon "
                        "design a test at a data-dependent stopping point is not valid, so none is reported")
    report["arms"] = {v: {
        "units": flow[v]["units"],
        "mean_unit_success_rate": (sum(u["y"] for u in units.values() if u["arm"] == v) / flow[v]["units"])
        if flow[v]["units"] else None,
        "application_rate_interval_95": (list(stats.wilson_interval(flow[v]["success"], flow[v]["applications"]))
                                         if spec["assignment_unit"] != "employer-cluster" and flow[v]["applications"]
                                         else None),
    } for v in variants}
    report["by_stratum_descriptive_only"] = [
        {"stratum": s, "arm": v, "units": len(ys), "mean_unit_success_rate": sum(ys) / len(ys)}
        for s in sorted({u["stratum"] for u in units.values()}) for v in variants
        for ys in [[u["y"] for u in units.values() if u["stratum"] == s and u["arm"] == v]] if ys]
    report["secondary_descriptive_only"] = {}
    for m in spec.get("secondary_metrics", []):
        sec = _unit_table(assignments, store, m, window, as_of)
        report["secondary_descriptive_only"][m] = {
            v: (sum(u["y"] for u in sec.values() if u["arm"] == v) / flow[v]["units"]) if flow[v]["units"] else None
            for v in variants}
    if spec["assignment_unit"] == "employer-cluster":
        clusters = [[1.0 if x["status"] == "success" else 0.0 for x in u["apps"]] for u in units.values()]
        icc = stats.anova_icc(clusters)
        mean_size = len(apps) / len(units)
        report["clustering"] = {
            "clusters": len(units), "mean_cluster_size": mean_size, "icc_estimate": icc,
            "design_effect": None if icc is None else 1 + (mean_size - 1) * icc,
            "note": "clusters (employers) are the unit of analysis; applications are not treated as independent"}

    total_events = sum(x["status"] == "success" for x in apps)
    if total_events < 5 or len(apps) - total_events < 5:
        report["warnings"].append("SPARSE_OUTCOMES: fewer than 5 successes or 5 non-successes overall")
    if any(flow[v]["deviations"] for v in variants):
        report["warnings"].append("PROTOCOL_DEVIATIONS_REPORTED: analysis is by assigned arm (intention to treat)")
    if any(f in data_flags for f in ("ambiguous_feedback_not_counted", "late_success_outside_window")):
        report["warnings"].append("SOME_RESPONSES_NOT_COUNTED: ambiguous or late responses exist; see outcome_data_flags")

    if stopped_early:
        report["verdict"] = "INCONCLUSIVE"
        return report

    result = infer(list(units.values()), variants=variants, control=control,
                   minimum_units=design["minimum_units_per_arm"], alpha=alpha, n_perm=design["n_permutations"],
                   seed_key="|".join((rec["design_hash"], metric, str(window))), metric=metric,
                   unit_label=spec["assignment_unit"])
    report["comparisons"] = result["comparisons"]
    claims = result["claims"]
    withheld.extend(result["withheld"])
    if corrected and claims:
        # would the conclusion exist had the user never voided or amended anything?
        raw_units = _unit_table(assignments, store, metric, window, as_of, uncorrected=True)
        raw = infer(list(raw_units.values()), variants=variants, control=control,
                    minimum_units=design["minimum_units_per_arm"], alpha=alpha, n_perm=design["n_permutations"],
                    seed_key="|".join((rec["design_hash"], metric, str(window))), metric=metric,
                    unit_label=spec["assignment_unit"])
        def signature(res: dict[str, Any]) -> set[tuple[str, int]]:
            return {(c["treatment"], 1 if c["estimate"] > 0 else -1) for c in res["comparisons"]
                    if not c["blocking"] and "p_value" in c}

        report["sensitivity_uncorrected_history"] = {"claims": sorted(map(list, signature(raw)))}
        if signature(raw) != signature(result) or any(u["censored"] for u in raw_units.values()):
            claims = []
            withheld.append("NOT_ROBUST_TO_CORRECTIONS: the difference appears only after the user's own "
                            "corrections to recorded events; no claim is made")

    if exploratory:
        report["verdict"] = "EXPLORATORY_NO_CLAIM"
        withheld.insert(0, "DEVIATES_FROM_PREREGISTRATION: this analysis does not follow the locked plan; "
                           "it is exploratory and supports no claim")
    elif claims:
        report["verdict"] = "EVIDENCE_FOR_DIFFERENCE"
        report["claim"] = " ".join(claims) + " This is evidence about response rates in this experiment only."
    else:
        report["verdict"] = "INCONCLUSIVE"
    return report


def portfolio_summary(reports: list[dict[str, Any]], alpha: float = 0.05) -> dict[str, Any]:
    """Multiplicity across experiments: many tests make some false alarms likely."""
    final = [r for r in reports if r["phase"] == "FINAL" and r["evidence_type"] == "randomized_controlled"
             and any("p_value_holm" in c for c in r["comparisons"])]
    p = [min(c["p_value_holm"] for c in r["comparisons"] if "p_value_holm" in c) for r in final]
    holm_p, bh_p = stats.holm(p), stats.benjamini_hochberg(p)
    counts: dict[str, int] = {}
    for r in reports:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    rows = [{"experiment_id": r["experiment_id"], "verdict": r["verdict"], "p_primary": pi,
             "p_holm_across_experiments": hi, "q_benjamini_hochberg": bi,
             "claim_survives_portfolio_holm": r["verdict"] == "EVIDENCE_FOR_DIFFERENCE" and hi < alpha}
            for r, pi, hi, bi in zip(final, p, holm_p, bh_p)]
    untested = [r["experiment_id"] for r in reports if r not in final]
    return {"schema": "careertwin.b.portfolio-summary.v1", "experiments": len(reports),
            "experiments_without_final_test": untested,
            "abandonment_caution": "Experiments closed early or never finished are part of the family of questions "
                                   "asked. A habit of abandoning unpromising experiments makes the remaining "
                                   "'differences' less trustworthy than their p-values suggest.",
            "verdict_counts": counts, "final_tests": len(final),
            "expected_false_alarms_if_all_null": alpha * len(final),
            "caution": "Each experiment controls its own error rate. Across many experiments some "
                       "'differences' are expected by chance; prefer claims that survive the portfolio adjustment.",
            "rows": rows}


def observational_summary(store: ApplicationStore, *, application_ids: Iterable[str],
                          group_of: Callable[[dict[str, Any]], str | None], metric: str, window_days: int,
                          as_of: datetime, covariate_of: Callable[[dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """Describe non-randomised history. Never produces a causal claim or a p-value."""
    if metric not in METRICS:
        raise CareerTwinError("unknown metric")
    rows: dict[tuple[str, str], list[int]] = {}
    excluded = 0
    for app_id in application_ids:
        proj = store.projection(app_id)
        group = group_of(proj)
        outcome = application_outcome(store.effective_events(app_id), metric=metric, window_days=window_days, as_of=as_of)
        if group is None or outcome["status"] not in ("success", "no_success"):
            excluded += 1
            continue
        cov = covariate_of(proj) if covariate_of else "(all)"
        cell = rows.setdefault((group, cov), [0, 0])
        cell[0] += outcome["status"] == "success"
        cell[1] += 1

    def summarise(cells: list[list[int]]) -> dict[str, Any]:
        x, n = sum(c[0] for c in cells), sum(c[1] for c in cells)
        ci = stats.wilson_interval(x, n)
        return {"applications": n, "successes": x, "rate": x / n if n else None,
                "interval_95": list(ci) if ci else None}

    groups = sorted({g for g, _ in rows})
    return {
        "schema": "careertwin.b.observational-summary.v1", "evidence_type": "observational",
        "verdict": "OBSERVATIONAL_ONLY", "claim": None, "metric": metric, "as_of": fmt_ts(as_of),
        "excluded_applications": excluded,
        "groups": {g: summarise([c for (gg, _), c in rows.items() if gg == g]) for g in groups},
        "by_covariate": {cov: {g: summarise([rows[(g, cov)]]) for g in groups if (g, cov) in rows}
                         for cov in sorted({c for _, c in rows})} if covariate_of else None,
        "warnings": [
            "OBSERVATIONAL: these applications were not randomised. The user chose which résumé went where, "
            "so groups differ in employer, role, seniority and timing. Differences are associations, not effects.",
            "NO_TEST_REPORTED: no p-value or winner is computed for non-randomised comparisons.",
            "To learn whether a variant works, preregister a randomised experiment.",
        ],
    }
