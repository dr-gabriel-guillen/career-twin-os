"""Sensitivity and fair-measurement checks (workstream 10).

Each check perturbs one input the user or the data pipeline controls and reports how much
the conclusions move. Results are stability measurements ("the ordering stayed the same in
83% of perturbations"), never odds of any outcome. Thresholds that turn numbers into the
words *stable* / *sensitive* are reading conventions and are returned with the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .diagnostics import kendall_tau, knn_overlap
from .engine import AtlasEngine
from .errors import AtlasError, InvalidInputError
from .features import FeatureVector, claims_to_vector, finite_number, parse_utc
from .vocabulary import normalize_token
from .graph import build_transition_graph, evaluate_path
from .vocabulary import Vocabulary, default_vocabulary

COMPONENTS = ("skills", "effort", "compensation", "leadership")
DEFAULT_WEIGHTS = {"skills": 0.4, "effort": 0.3, "compensation": 0.2, "leadership": 0.1}
MIN_COMPENSATION_COVERAGE = 0.2
SENSITIVE_TAU = 0.8
ORDERING_NOTE = ("A preference ordering of roles under the user's declared weights. It ranks options; "
                 "it does not predict outcomes.")


@dataclass(frozen=True)
class SensitivityInputs:
    jobs: Sequence[Mapping[str, Any]]
    claims: Sequence[Mapping[str, Any]]
    candidate_ref: str
    as_of: str | None = None
    vocab: Vocabulary | None = None
    seed: int = 0
    standardize: bool = False
    n_boot: int = 20


def _rows(jobs: Sequence[Any]) -> list[Mapping[str, Any]]:
    """Only mapping rows can be perturbed; others are skipped by AtlasEngine.fit anyway."""
    return [j for j in jobs if isinstance(j, Mapping)]


def _fit(inp: SensitivityInputs, jobs: Sequence[Mapping[str, Any]] | None = None,
         vocab: Vocabulary | None = None, standardize: bool | None = None
         ) -> tuple[AtlasEngine, FeatureVector]:
    vocab = vocab or inp.vocab or default_vocabulary()
    engine = AtlasEngine.fit(jobs if jobs is not None else inp.jobs, vocab,
                             standardize=inp.standardize if standardize is None else standardize,
                             seed=inp.seed, n_boot=inp.n_boot)
    cand = claims_to_vector(inp.claims, vocab, inp.candidate_ref, as_of=inp.as_of)
    return engine, cand


def _order_change(base: list[str], new: list[str]) -> dict[str, Any]:
    tau = kendall_tau(base, new)
    common = [r for r in base if r in new]
    return {
        "kendall_tau": None if tau is None else round(tau, 4),
        "top1_same": bool(base and new and base[0] == new[0]),
        "top3_overlap": len(set(base[:3]) & set(new[:3])),
        "roles_compared": len(common),
        "roles_lost": sorted(set(base) - set(new)),
    }


def _verdict(changes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not changes:
        # Repair note (review cycle 1, F4): nothing assessable used to read as "stable".
        return {"verdict": "not-assessable", "min_kendall_tau": None, "top1_unchanged_share": None,
                "convention": "no perturbation could be assessed, so no stability statement is made"}
    taus = [c["kendall_tau"] for c in changes if c["kendall_tau"] is not None]
    top1 = [c["top1_same"] for c in changes]
    sensitive = (bool(taus) and min(taus) < SENSITIVE_TAU) or (bool(top1) and not all(top1)) \
        or any(c["roles_lost"] for c in changes)
    return {"verdict": "sensitive" if sensitive else "stable",
            "min_kendall_tau": min(taus) if taus else None,
            "top1_unchanged_share": round(float(np.mean(top1)), 4) if top1 else None,
            "convention": f"'sensitive' if any ordering has Kendall tau < {SENSITIVE_TAU}, the "
                          "nearest role changes, or a role disappears"}


def _centroid_displacement(base: AtlasEngine, other: AtlasEngine) -> float | None:
    """Mean centroid move as a share of the mean distance between base centroids."""
    if base.matrix.feature_names != other.matrix.feature_names:
        return None
    fams = sorted(set(base.profiles) & set(other.profiles))
    if len(fams) < 2:
        return None
    moves = [float(np.linalg.norm(base.profiles[f].centroid - other.profiles[f].centroid)) for f in fams]
    seps = [float(np.linalg.norm(base.profiles[a].centroid - base.profiles[b].centroid))
            for i, a in enumerate(fams) for b in fams[i + 1:]]
    mean_sep = float(np.mean(seps))
    return round(float(np.mean(moves)) / mean_sep, 4) if mean_sep > 1e-12 else None


# ---------------------------------------------------------------- utility ordering -----
def utility_components(engine: AtlasEngine, candidate: FeatureVector, hours_per_week: float = 6.0,
                       horizon_months: int = 12) -> dict[str, dict[str, float | None]]:
    """Per-role components in [0, 1] (higher = more preferred). ``None`` means UNKNOWN."""
    if not engine.profiles:
        raise InvalidInputError("no role profiles: postings carry no role_family, so roles cannot be ordered")
    graph = build_transition_graph(engine)
    table = {r["role"]: r for r in engine.role_table(candidate)}
    roles = sorted(table)
    short = {r: table[r]["requirement_shortfall"] for r in roles}
    effort = {}
    for r in roles:
        res = evaluate_path(engine, graph, candidate, (r,), hours_per_week, horizon_months)
        effort[r] = sum(res["effort_hours_range"]) / 2.0
    units = compensation_units(engine)
    comparable = len(units) == 1
    comp_raw = {r: (engine.profiles[r].compensation_mid_median
                    if comparable and engine.profiles[r].compensation_coverage >= MIN_COMPENSATION_COVERAGE
                    else None)
                for r in roles}
    lead = {r: float(graph.nodes[r]["leadership_exposure"]) for r in roles}

    def inverse(d: dict[str, float]) -> dict[str, float]:
        hi = max(d.values())
        return {k: 1.0 - (v / hi if hi > 1e-12 else 0.0) for k, v in d.items()}

    def minmax(d: dict[str, float | None]) -> dict[str, float | None]:
        known = [v for v in d.values() if v is not None]
        if not known:
            return {k: None for k in d}
        lo, hi = min(known), max(known)
        return {k: (None if v is None else (0.5 if hi - lo < 1e-12 else (v - lo) / (hi - lo)))
                for k, v in d.items()}

    s, e, c, l = inverse(short), inverse(effort), minmax(comp_raw), minmax(lead)
    return {r: {"skills": s[r], "effort": e[r], "compensation": c[r], "leadership": l[r]} for r in roles}


def compensation_units(engine: AtlasEngine) -> list[str]:
    """Distinct compensation units (CURRENCY/basis) across all role profiles."""
    return sorted({u for p in engine.profiles.values() for u in p.compensation_units})


def _clean_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if weights is not None:
        if not isinstance(weights, Mapping):
            raise InvalidInputError("weights must be a mapping")
        w = {k: 0.0 for k in COMPONENTS}
        for k, v in weights.items():
            if k not in COMPONENTS:
                raise InvalidInputError(f"unknown weight {k!r}; allowed: {COMPONENTS}")
            fv = finite_number(v)
            if fv is None or fv < 0:
                raise InvalidInputError(f"weight {k!r} must be a finite number >= 0")
            w[k] = fv
    total = sum(w.values())
    if total <= 0:
        raise InvalidInputError("at least one weight must be positive")
    return {k: v / total for k, v in w.items()}


def _utilities(components: Mapping[str, Mapping[str, float | None]], w: Mapping[str, float],
               unknown_as: float | None) -> dict[str, float]:
    """Weighted sum. ``unknown_as=None`` drops unknown components and renormalises per role."""
    out = {}
    for role, comp in components.items():
        num = den = 0.0
        for k in COMPONENTS:
            v = comp[k]
            if v is None:
                if unknown_as is None:
                    continue
                v = unknown_as
            num += w[k] * v
            den += w[k]
        # every weighted component unknown: the role is unranked, never scored 0 (review cycle 1, F8)
        out[role] = num / den if den > 1e-12 else None
    return out


def _ordering(util: Mapping[str, float | None]) -> list[str]:
    """Roles with a known utility, best first. Roles whose utility is unknown are left out."""
    return [r for r, _ in sorted(((k, v) for k, v in util.items() if v is not None),
                                 key=lambda kv: (-kv[1], kv[0]))]


def weight_sensitivity(engine: AtlasEngine, candidate: FeatureVector,
                       weights: Mapping[str, float] | None = None, n_draws: int = 300,
                       concentration: float = 40.0, seed: int = 0) -> dict[str, Any]:
    """Perturb the user's utility weights (Dirichlet around them) and track the role ordering."""
    if n_draws < 1 or concentration <= 0:
        raise InvalidInputError("n_draws must be >= 1 and concentration > 0")
    w = _clean_weights(weights)
    comps = utility_components(engine, candidate)
    base = _ordering(_utilities(comps, w, None))
    if len(base) < 2:
        # one role (or none) cannot be re-ordered: no stability statement (review cycle 2, finding 14)
        return {"weights_normalised": {k: round(v, 4) for k, v in w.items()}, "baseline_ordering": base,
                "n_draws": 0, "verdict": "not-assessable", "unranked_at_baseline": sorted(set(comps) - set(base)),
                "interpretation": "fewer than two ranked roles; ordering stability is not defined",
                "ordering_note": ORDERING_NOTE}
    rng = np.random.default_rng(seed)
    alpha = np.array([w[k] for k in COMPONENTS]) * concentration + 1e-3
    ranks: dict[str, list[int]] = {r: [] for r in base}
    taus, top_same = [], 0
    for _ in range(n_draws):
        draw = dict(zip(COMPONENTS, rng.dirichlet(alpha)))
        order = _ordering(_utilities(comps, draw, None))
        for pos, r in enumerate(order, start=1):
            ranks.setdefault(r, []).append(pos)
        tau = kendall_tau(base, order)
        if tau is not None:
            taus.append(tau)
        top_same += int(bool(order) and bool(base) and order[0] == base[0])
    share = top_same / n_draws
    return {
        "weights_normalised": {k: round(v, 4) for k, v in w.items()},
        "baseline_ordering": base,
        "n_draws": n_draws, "dirichlet_concentration": concentration,
        "top_choice_unchanged_share": round(share, 4),
        "kendall_tau_mean": round(float(np.mean(taus)), 4), "kendall_tau_min": round(float(np.min(taus)), 4),
        "rank_range": {r: {"baseline": base.index(r) + 1 if r in base else None,
                           "min": int(min(v)), "max": int(max(v))} for r, v in ranks.items() if v},
        "unranked_at_baseline": sorted(set(comps) - set(base)),
        "verdict": "stable" if share >= 0.8 and float(np.min(taus)) >= SENSITIVE_TAU else "sensitive",
        "interpretation": "Share of weight perturbations under which the top-ranked role stays on top. "
                          "A stability measure of the ordering, not a likelihood of any outcome.",
        "ordering_note": ORDERING_NOTE,
    }


def missing_salary_sensitivity(engine: AtlasEngine, candidate: FeatureVector,
                               weights: Mapping[str, float] | None = None) -> dict[str, Any]:
    """What can and cannot be concluded when compensation is missing for some or all roles."""
    w = _clean_weights(weights)
    comps = utility_components(engine, candidate)
    observed_util = _utilities(comps, w, None)
    observed = _ordering(observed_util)
    blind = {r: {**c, "compensation": None} for r, c in comps.items()}
    lower = _utilities(blind, w, 0.0)
    upper = _utilities(blind, w, 1.0)
    dropped = _ordering(_utilities(blind, w, None))
    bounds = {}
    for r in comps:
        best = 1 + sum(1 for s in comps if s != r and lower[s] > upper[r])
        worst = 1 + sum(1 for s in comps if s != r and upper[s] > lower[r])
        bounds[r] = {"best_possible_rank": best, "worst_possible_rank": worst}
    units = compensation_units(engine)
    lo_m, up_m = _utilities(comps, w, 0.0), _utilities(comps, w, 1.0)   # unknown pay at worst / best
    coverage = {r: round(engine.profiles[r].compensation_coverage, 4) for r in comps}
    unknown_roles = sorted(r for r, c in comps.items() if c["compensation"] is None)
    return {
        "compensation_units_observed": units,
        "compensation_comparable": len(units) == 1,
        "compensation_unit_note": ("one unit across roles; compared within that unit" if len(units) == 1 else
                                   "no compensation figures" if not units else
                                   "different currencies or bases across roles; compensation is NOT compared "
                                   "(no conversion is attempted)") + ". 'SYN' is the synthetic generator's "
                                  "non-currency unit.",
        "unranked_with_available_compensation": sorted(r for r, u in observed_util.items() if u is None),
        "compensation_coverage_by_role": coverage,
        "roles_with_unknown_compensation": unknown_roles,
        "ordering_with_available_compensation": observed,
        "ordering_with_compensation_dropped": dropped,
        "change_when_dropped": _order_change(observed, dropped),
        "rank_bounds_if_compensation_fully_unknown": bounds,
        "top_choice_identifiable_without_compensation":
            bool(dropped) and bounds[dropped[0]]["worst_possible_rank"] == 1,
        "rank_bounds_for_roles_with_unknown_compensation": {
            r: {"best_possible_rank": 1 + sum(1 for s in comps if s != r and lo_m[s] is not None and up_m[r] is not None
                                              and lo_m[s] > up_m[r]),
                "worst_possible_rank": 1 + sum(1 for s in comps if s != r and up_m[s] is not None
                                               and lo_m[r] is not None and up_m[s] > lo_m[r])}
            for r in unknown_roles},
        "handling": "Unknown compensation is never set to zero. In the reported ordering it is dropped and the role's "
                    "other components are renormalised, which is equivalent to assuming its pay matches its other "
                    "components; it can therefore rank a role with missing pay above one with known low pay. "
                    "rank_bounds_for_roles_with_unknown_compensation shows the full range any pay value could give.",
        "ordering_note": ORDERING_NOTE,
    }


# ---------------------------------------------------------------- data perturbations ---
def seniority_sensitivity(inp: SensitivityInputs) -> dict[str, Any]:
    base, cand = _fit(inp)
    base_order = base.role_order(cand)
    rng = np.random.default_rng(inp.seed)
    ladder = ["mid", "senior", "staff", "principal", "director", "vp"]
    variants = {}
    rows = _rows(inp.jobs)
    unknown = [{**j, "seniority": "unspecified"} for j in rows]
    bumped, n_bumped = [], 0
    for j in rows:
        s = normalize_token(j.get("seniority"))
        if s in ladder[:-1] and rng.random() < 0.2:
            bumped.append({**j, "seniority": ladder[ladder.index(s) + 1]})
            n_bumped += 1
        else:
            bumped.append(dict(j))
    for name, jobs in (("all_seniority_unknown", unknown), ("20pct_bumped_one_level", bumped)):
        eng, c = _fit(inp, jobs=jobs)
        ch = _order_change(base_order, eng.role_order(c))
        ch["centroid_displacement_share"] = _centroid_displacement(base, eng)
        ch["map_knn_overlap_with_baseline"] = round(knn_overlap(base.coords, eng.coords, 10) or 0.0, 4)
        variants[name] = ch
    variants["20pct_bumped_one_level"]["n_bumped"] = n_bumped
    # Repair note (review cycle 1, F3): the role ordering (requirement_shortfall) uses skill features only,
    # so no seniority perturbation can change it. Reporting "stable" was true by construction and
    # therefore uninformative. The map and centroid effects are still reported.
    return {"baseline_ordering": base_order, "variants": variants, "verdict": "not-applicable",
            "min_kendall_tau": None, "top1_unchanged_share": None,
            "convention": "the role ordering does not use seniority, so this check cannot move it; see the "
                          "map overlap and centroid displacement for the effect on the picture"}


def freshness_sensitivity(inp: SensitivityInputs, windows_days: Sequence[int] = (90, 45)) -> dict[str, Any]:
    base, cand = _fit(inp)
    base_order = base.role_order(cand)
    rows = _rows(inp.jobs)
    stamps = [parse_utc(j.get("fetched_at")) for j in rows]
    known = [s for s in stamps if s is not None]
    if not known:
        return {"status": "not-assessable", "reason": "no posting has a usable fetched_at"}
    latest = max(known)
    variants = {}
    for days in windows_days:
        jobs = [j for j, s in zip(rows, stamps) if s is not None and (latest - s).days < days]
        try:
            eng, c = _fit(inp, jobs=jobs)
        except AtlasError as exc:
            variants[f"last_{days}_days"] = {"status": "not-assessable", "reason": str(exc), "n_postings": len(jobs)}
            continue
        ch = _order_change(base_order, eng.role_order(c))
        ch.update({"n_postings": len(jobs),
                   "min_postings_per_role": min((p.n_postings for p in eng.profiles.values()), default=0),
                   "centroid_displacement_share": _centroid_displacement(base, eng),
                   "unstable_roles": sorted(r for r, s in eng.stability.items() if s["unstable"])})
        variants[f"last_{days}_days"] = ch
    assessed = [v for v in variants.values() if "kendall_tau" in v]
    return {"baseline_ordering": base_order, "n_postings_total": len(rows),
            "undated_postings": len(stamps) - len(known), "variants": variants, **_verdict(assessed)}


def source_volume_sensitivity(inp: SensitivityInputs, fractions: Sequence[float] = (0.5, 0.2, 0.05),
                              repeats: int = 5) -> dict[str, Any]:
    base, cand = _fit(inp)
    base_order = base.role_order(cand)
    rng = np.random.default_rng(inp.seed)
    n = len(inp.jobs)
    variants, all_changes = {}, []
    for frac in fractions:
        m = max(2, int(round(n * frac)))
        changes, disp, unstable = [], [], []
        for _ in range(repeats):
            idx = np.sort(rng.choice(n, size=min(m, n), replace=False))
            try:
                eng, c = _fit(inp, jobs=[inp.jobs[int(i)] for i in idx])
            except AtlasError:
                continue
            changes.append(_order_change(base_order, eng.role_order(c)))
            d = _centroid_displacement(base, eng)
            if d is not None:
                disp.append(d)
            unstable.append(sum(1 for s in eng.stability.values() if s["unstable"]))
        all_changes.extend(changes)
        taus = [c["kendall_tau"] for c in changes if c["kendall_tau"] is not None]
        variants[f"{frac:.0%}_of_postings"] = {
            "n_postings": m, "repeats_completed": len(changes),
            "kendall_tau_min": min(taus) if taus else None,
            "kendall_tau_mean": round(float(np.mean(taus)), 4) if taus else None,
            "top1_unchanged_share": round(float(np.mean([c["top1_same"] for c in changes])), 4) if changes else None,
            "centroid_displacement_share_mean": round(float(np.mean(disp)), 4) if disp else None,
            "unstable_roles_mean": round(float(np.mean(unstable)), 2) if unstable else None,
            "roles_lost_any": sorted({r for c in changes for r in c["roles_lost"]}),
        }
    return {"baseline_ordering": base_order, "n_postings_total": n, "variants": variants,
            **_verdict(all_changes)}


def lexicon_sensitivity(inp: SensitivityInputs, drop_share: float = 0.1, repeats: int = 5) -> dict[str, Any]:
    vocab = inp.vocab or default_vocabulary()
    base, cand = _fit(inp, vocab=vocab)
    base_order = base.role_order(cand)
    rng = np.random.default_rng(inp.seed)
    variants: dict[str, Any] = {}
    eng, c = _fit(inp, vocab=vocab.without_aliases())
    no_alias = _order_change(base_order, eng.role_order(c))
    no_alias["unmapped_terms_baseline"] = int(sum(len(v.unmapped_terms) for v in base.matrix.vectors))
    no_alias["unmapped_terms_without_aliases"] = int(sum(len(v.unmapped_terms) for v in eng.matrix.vectors))
    variants["aliases_disabled"] = no_alias
    changes = [no_alias]
    skills = list(vocab.skill_names)
    k = max(1, int(round(len(skills) * drop_share)))
    drops = []
    for _ in range(repeats):
        dropped = sorted(rng.choice(skills, size=k, replace=False).tolist())
        try:
            eng, c = _fit(inp, vocab=vocab.without_features(dropped))
        except AtlasError:
            continue
        ch = _order_change(base_order, eng.role_order(c))
        ch["dropped_features"] = dropped
        drops.append(ch)
    changes.extend(drops)
    variants[f"random_{drop_share:.0%}_of_features_dropped"] = drops
    return {"baseline_ordering": base_order, "variants": variants, **_verdict(changes)}


def normalization_sensitivity(inp: SensitivityInputs) -> dict[str, Any]:
    from .diagnostics import diagnose_projection
    raw, cand_r = _fit(inp, standardize=False)
    std, cand_s = _fit(inp, standardize=True)
    order_r, order_s = raw.role_order(cand_r), std.role_order(cand_s)
    change = _order_change(order_r, order_s)

    def brief(e: AtlasEngine) -> dict[str, Any]:
        d = diagnose_projection(e.Z, e.coords, 10, inp.seed, e.model.explained_variance_ratio)
        return {"explained_variance_cumulative": round(d["explained_variance_cumulative"], 4),
                "trustworthiness": round(d["trustworthiness"], 4) if d["trustworthiness"] is not None else None,
                "knn_recall_mean": round(d["knn_recall_mean"], 4) if d["knn_recall_mean"] is not None else None,
                "top_loadings_axis1": [x["feature"] for x in (e.model.loadings(3)[0]["positive"]
                                                              + e.model.loadings(3)[0]["negative"])]}

    return {
        "ordering_unstandardised": order_r, "ordering_standardised": order_s, "change": change,
        "map_knn_overlap_between_scalings": round(knn_overlap(raw.coords, std.coords, 10) or 0.0, 4),
        "unstandardised": brief(raw), "standardised": brief(std),
        "note": "Standardising gives rare features the same weight as common ones. Neither scaling "
                "is 'correct'; if conclusions differ, they depend on a modelling choice.",
        **_verdict([change]),
    }


def run_sensitivity(inp: SensitivityInputs, weights: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Run every check and summarise which conclusions are fragile."""
    engine, cand = _fit(inp)
    report = {
        "utility_weights": weight_sensitivity(engine, cand, weights, seed=inp.seed),
        "missing_salary": missing_salary_sensitivity(engine, cand, weights),
        "seniority": seniority_sensitivity(inp),
        "job_freshness": freshness_sensitivity(inp),
        "source_volume": source_volume_sensitivity(inp),
        "lexicon": lexicon_sensitivity(inp),
        "normalization": normalization_sensitivity(inp),
    }
    fragile = sorted(k for k, v in report.items() if v.get("verdict") == "sensitive")
    if not report["missing_salary"]["top_choice_identifiable_without_compensation"]:
        fragile.append("missing_salary")
    report["summary"] = {
        "sensitive_to": sorted(set(fragile)),
        "stable_under": sorted(k for k, v in report.items() if isinstance(v, dict) and v.get("verdict") == "stable"
                               and k not in fragile),
        "not_assessable_or_not_applicable": sorted(k for k, v in report.items() if isinstance(v, dict)
                                                   and v.get("verdict") in ("not-assessable", "not-applicable")),
        "note": "Instability findings describe how much modelling choices and data volume move the "
                "analysis. They are not probabilities of career outcomes.",
    }
    return report
