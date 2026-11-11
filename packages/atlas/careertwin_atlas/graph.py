"""Transition graph between role families and candidate-specific path planning.

Nodes are role families. A directed edge u -> v lists the evidence that v's postings commonly
state and u's do not, with an effort range, exposure deltas and explicit constraints.

Modelling assumptions (all declared in the output, all overridable):

* Effort hours per feature are vocabulary placeholders, not empirical learning times.
* Evidence for "role-exposure" features (client work, management scope, executive
  communication) cannot be produced by study alone. An unmet role-exposure requirement is
  reported as an *entry barrier*, never silently converted into hours.
* Holding an intermediate role for ``tenure_months`` is *assumed* to produce evidence for
  the features that role's postings state at least ``CONFERRED_THRESHOLD`` of the time (its
  core and common features). That is an assumption about evidence, not a promise.
* Paths are ranked by the hardest single step (most unmet role-exposure requirements at one
  entry), elapsed time and effort. A stepping stone can lower the hardest step at the cost
  of time; whether that trade is worth it is the user's call.
* No path, title or credential guarantees eligibility for anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Any, Mapping

import numpy as np

from .engine import AtlasEngine
from .errors import InvalidInputError
from .features import STATE_CONTRADICTED, STATE_UNKNOWN, FeatureVector
from .gaps import EVIDENCED_LEVEL
from .vocabulary import ACQ_ROLE, SENIORITY_FEATURE

WEEKS_PER_MONTH = 4.345
STUDY_EVIDENCE_CAP = 0.8
CONFERRED_THRESHOLD = 0.25
ELIGIBILITY_NOTE = ("No path guarantees eligibility, an interview or an offer. A title, degree or "
                    "certificate does not by itself establish that requirements are met.")
DEFAULT_PATH_WEIGHTS = {"time": 1.0, "barriers": 1.0, "effort": 0.5}


@dataclass(frozen=True)
class TransitionEdge:
    source: str
    target: str
    required_evidence: tuple[str, ...]
    role_exposure_features: tuple[str, ...]
    effort_hours_low: float
    effort_hours_high: float
    seniority_delta: float | None
    leadership_exposure_delta: float
    client_exposure_delta: float
    constraints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "target": self.target,
            "required_evidence": list(self.required_evidence),
            "role_exposure_features": list(self.role_exposure_features),
            "effort_hours_range": [self.effort_hours_low, self.effort_hours_high],
            "seniority_delta": self.seniority_delta,
            "leadership_exposure_delta": self.leadership_exposure_delta,
            "client_exposure_delta": self.client_exposure_delta,
            "constraints": list(self.constraints),
        }


@dataclass(frozen=True)
class TransitionGraph:
    nodes: Mapping[str, Mapping[str, Any]]
    edges: Mapping[tuple[str, str], TransitionEdge]
    core_threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {k: dict(v) for k, v in self.nodes.items()},
            "edges": [e.to_dict() for _, e in sorted(self.edges.items())],
            "core_threshold": self.core_threshold,
            "eligibility_note": ELIGIBILITY_NOTE,
            "assumptions": [
                "effort hours are vocabulary placeholders, not measured learning times",
                "role-exposure evidence cannot be produced by study alone",
                f"holding a role is assumed to evidence features stated in at least "
                f"{CONFERRED_THRESHOLD:.0%} of its postings",
                "edges describe differences between what synthetic postings state",
            ],
        }


def _group_mean(engine: AtlasEngine, prevalence: np.ndarray, group: str) -> float:
    idx = [i for i, f in enumerate(engine.vocab.features) if f.group == group]
    return float(prevalence[idx].mean()) if idx else 0.0


def build_transition_graph(engine: AtlasEngine, core_threshold: float = 0.5) -> TransitionGraph:
    if not (0.0 < core_threshold <= 1.0):
        raise InvalidInputError("core_threshold must be within (0, 1]")
    vocab = engine.vocab
    si = vocab.index.get(SENIORITY_FEATURE)
    nodes: dict[str, dict[str, Any]] = {}
    for fam, p in engine.profiles.items():
        nodes[fam] = {
            "label": p.label, "n_postings": p.n_postings,
            "core_features": p.core_features(core_threshold),
            "conferred_features": p.core_features(min(core_threshold, CONFERRED_THRESHOLD)),
            "seniority_level_mean": None if si is None or p.observed_share[si] == 0
            else round(float(p.centroid[si]), 4),
            "leadership_exposure": round(_group_mean(engine, p.prevalence, "leadership"), 4),
            "client_exposure": round(_group_mean(engine, p.prevalence, "customer"), 4),
            "centroid_unstable": bool(engine.stability[fam]["unstable"]),
        }
    edges: dict[tuple[str, str], TransitionEdge] = {}
    for u in nodes:
        for v in nodes:
            if u == v:
                continue
            need = [f for f in nodes[v]["core_features"] if f not in nodes[u]["core_features"]]
            role_feats = [f for f in need if vocab.get(f).acquisition == ACQ_ROLE]
            study = [f for f in need if f not in role_feats]
            lo = sum(vocab.get(f).effort_hours[0] for f in study)
            hi = sum(vocab.get(f).effort_hours[1] for f in study)
            su, sv = nodes[u]["seniority_level_mean"], nodes[v]["seniority_level_mean"]
            sdelta = None if su is None or sv is None else round(sv - su, 4)
            constraints: list[str] = []
            if role_feats:
                constraints.append("requires evidence normally gained in-role, not from study alone: "
                                   + ", ".join(role_feats))
            if sdelta is not None and sdelta > 0.25:
                constraints.append("target postings state a much higher seniority level; an "
                                   "intermediate scope step is usually needed (assumption)")
            if nodes[v]["n_postings"] < 5 or nodes[v]["centroid_unstable"]:
                constraints.append("target role profile is unstable or built from few postings; "
                                   "requirements may be mis-stated")
            edges[(u, v)] = TransitionEdge(
                u, v, tuple(need), tuple(role_feats), float(lo), float(hi), sdelta,
                round(nodes[v]["leadership_exposure"] - nodes[u]["leadership_exposure"], 4),
                round(nodes[v]["client_exposure"] - nodes[u]["client_exposure"], 4),
                tuple(constraints))
    return TransitionGraph(nodes, edges, core_threshold)


def _levels(candidate: FeatureVector) -> tuple[dict[str, float], set[str]]:
    levels: dict[str, float] = {}
    contradicted: set[str] = set()
    for name, value, state in zip(candidate.feature_names, candidate.values, candidate.states):
        if state == STATE_UNKNOWN:
            continue
        levels[name] = float(value)
        if state == STATE_CONTRADICTED:
            contradicted.add(name)
    return levels, contradicted


def evaluate_path(engine: AtlasEngine, graph: TransitionGraph, candidate: FeatureVector,
                  path: tuple[str, ...], hours_per_week: float, horizon_months: int,
                  tenure_months: float = 18.0) -> dict[str, Any]:
    """Cost out one ordered list of roles ending at the target, for one candidate."""
    vocab = engine.vocab
    levels, contradicted = _levels(candidate)
    steps = []
    total_lo = total_hi = 0.0
    barriers_total = 0
    barriers_max_step = 0
    tenure_total = 0.0
    caveats: list[str] = []
    for pos, role in enumerate(path):
        node = graph.nodes[role]
        missing_study, barriers = [], []
        lo = hi = 0.0
        for f in node["core_features"]:
            level = levels.get(f, 0.0)
            if level >= EVIDENCED_LEVEL:
                continue
            fdef = vocab.get(f)
            if fdef.acquisition == ACQ_ROLE:
                barriers.append(f)
                continue
            remaining = max(0.0, 1.0 - level / STUDY_EVIDENCE_CAP)
            missing_study.append({"feature": f, "current_evidence_level": round(level, 4),
                                  "effort_hours_range": [round(fdef.effort_hours[0] * remaining, 1),
                                                         round(fdef.effort_hours[1] * remaining, 1)]})
            lo += fdef.effort_hours[0] * remaining
            hi += fdef.effort_hours[1] * remaining
            levels[f] = max(level, STUDY_EVIDENCE_CAP)
        hit = sorted(contradicted & set(node["core_features"]))
        if hit:
            caveats.append(f"{role}: evidence on file contradicts {', '.join(hit)}; "
                           "effort alone does not resolve a contradiction")
        is_final = pos == len(path) - 1
        tenure = 0.0 if is_final else float(tenure_months)
        if not is_final:
            for f in node["conferred_features"]:
                levels[f] = max(levels.get(f, 0.0), 1.0)
        steps.append({
            "role": role, "label": node["label"],
            "missing_study_or_project_evidence": missing_study,
            "entry_barriers_role_exposure": barriers,
            "effort_hours_range": [round(lo, 1), round(hi, 1)],
            "assumed_tenure_months_before_next_step": tenure,
            "role_profile_unstable": node["centroid_unstable"],
        })
        total_lo += lo
        total_hi += hi
        barriers_total += len(barriers)
        barriers_max_step = max(barriers_max_step, len(barriers))
        tenure_total += tenure
    per_month = hours_per_week * WEEKS_PER_MONTH
    months_lo = total_lo / per_month + tenure_total
    months_hi = total_hi / per_month + tenure_total
    fits = "yes" if months_hi <= horizon_months else "no" if months_lo > horizon_months else "uncertain"
    seniority_known = (SENIORITY_FEATURE in candidate.feature_names
                       and candidate.state_of(SENIORITY_FEATURE) != STATE_UNKNOWN)
    if not seniority_known:
        caveats.append("candidate seniority is unknown, so seniority steps are not assessed")
    return {
        "path": list(path), "kind": "direct" if len(path) == 1 else "stepping-stone",
        "steps": steps,
        "effort_hours_range": [round(total_lo, 1), round(total_hi, 1)],
        "months_range": [round(months_lo, 1), round(months_hi, 1)],
        "assumed_tenure_months_total": tenure_total,
        "entry_barriers_total": barriers_total,
        "entry_barriers_hardest_step": barriers_max_step,
        "fits_time_horizon": fits,
        "caveats": caveats,
    }


def plan_paths(engine: AtlasEngine, graph: TransitionGraph, candidate: FeatureVector, target: str,
               hours_per_week: float = 6.0, horizon_months: int = 12, max_hops: int = 3,
               tenure_months: float = 18.0, weights: Mapping[str, float] | None = None,
               top: int = 5) -> dict[str, Any]:
    """Enumerate direct and stepping-stone paths to ``target`` and rank them by declared weights."""
    engine.check_vector(candidate)
    if target not in graph.nodes:
        raise InvalidInputError(f"unknown target role {target!r}; known: {sorted(graph.nodes)}")
    if not (isinstance(hours_per_week, (int, float)) and np.isfinite(hours_per_week) and hours_per_week > 0):
        raise InvalidInputError("hours_per_week must be a positive number")
    if isinstance(horizon_months, bool) or not isinstance(horizon_months, int) or horizon_months < 1:
        raise InvalidInputError("horizon_months must be a positive integer")
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not (1 <= max_hops <= 4):
        raise InvalidInputError("max_hops must be an integer between 1 and 4")
    w = dict(DEFAULT_PATH_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k not in w or not isinstance(v, (int, float)) or not np.isfinite(v) or v < 0:
                raise InvalidInputError(f"invalid path weight {k!r}")
            w[k] = float(v)
    others = sorted(r for r in graph.nodes if r != target)
    results = []
    for n_mid in range(0, max_hops):
        for mids in permutations(others, n_mid):
            res = evaluate_path(engine, graph, candidate, tuple(mids) + (target,),
                                hours_per_week, horizon_months, tenure_months)
            mid_months = sum(res["months_range"]) / 2.0
            mid_effort = sum(res["effort_hours_range"]) / 2.0
            res["ranking_score"] = round(w["time"] * mid_months / horizon_months
                                         + w["barriers"] * res["entry_barriers_hardest_step"]
                                         + w["effort"] * mid_effort / 1000.0, 6)
            results.append(res)
    results.sort(key=lambda r: (r["ranking_score"], len(r["path"]), r["path"]))
    for r in results:
        m, b = sum(r["months_range"]) / 2.0, r["entry_barriers_hardest_step"]
        r["pareto_optimal_on_time_and_barriers"] = not any(
            (sum(o["months_range"]) / 2.0 <= m and o["entry_barriers_hardest_step"] <= b)
            and (sum(o["months_range"]) / 2.0 < m or o["entry_barriers_hardest_step"] < b)
            for o in results)
    direct = next(r for r in results if r["kind"] == "direct")
    chosen = results[:top]
    if direct not in chosen:
        chosen.append(direct)
    return {
        "target": target, "paths_evaluated": len(results), "alternatives": chosen,
        "ranking_weights": w,
        "ranking_note": "Lower score ranks first. The score orders options under the declared "
                        "weights; it is not a likelihood of success.",
        "inputs": {"hours_per_week": hours_per_week, "horizon_months": horizon_months,
                   "max_hops": max_hops, "assumed_tenure_months": tenure_months},
        "eligibility_note": ELIGIBILITY_NOTE,
    }
