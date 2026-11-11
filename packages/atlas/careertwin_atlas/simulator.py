"""Counterfactual transition simulator -> ``TransitionScenario`` (contracts/transition-scenario.schema.json).

The simulator answers one question: *if this evidence existed, how would the candidate's
position relative to a target role's stated requirements change, and what would it cost?*

It never outputs a probability of interview, offer or compensation. Every number is a
distance, a count, an hour budget or a rank inside a synthetic feature space, and every
scenario is labelled ``hypothetical``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import numpy as np

from .engine import AtlasEngine
from .errors import InvalidInputError
from .features import STATE_CONTRADICTED, STATE_STATED, STATE_UNKNOWN, FeatureVector, sanitize_label
from .gaps import EVIDENCED_LEVEL, analyze_gap, candidate_state, explain_movement
from .graph import ELIGIBILITY_NOTE, WEEKS_PER_MONTH, TransitionGraph, build_transition_graph, plan_paths
from .points import MAP_IS_NOT_FIT, atlas_point
from .vocabulary import ACQ_ROLE, BUNDLES, normalize_token

#: Highest evidence level an action of this kind can reach. Declared assumption.
EVIDENCE_GOALS: dict[str, float] = {
    "coursework": 0.6, "study": 0.6, "certification": 0.7, "portfolio-project": 0.8,
    "open-source-contribution": 0.8, "verified-work-sample": 1.0, "client-engagement": 1.0,
    "in-role": 1.0,
}
ROLE_CAPABLE_GOALS = frozenset({"client-engagement", "in-role", "verified-work-sample"})
NON_ROLE_CAP = 0.3
DEFAULT_GOAL = "coursework"
MAX_ACTIONS = 50
GOAL_TEXT_MAX = 300
SCENARIO_OUTPUTS_VERSION = "scenario-outputs/0.1"
LEADERSHIP_BINS = (0.2, 0.5)          # < 0.2 low, < 0.5 medium, otherwise high. Reading convention.


def parse_evidence_goal(text: object) -> tuple[str | None, str]:
    """Split an ``evidence_goal`` into (recognised kind | None, free text).

    The contract types ``evidence_goal`` as a free string. Lane C recognises either a bare
    kind (``"portfolio-project"``) or a ``"kind: free text"`` prefix
    (``"portfolio-project: publish a small evaluation harness"``). Anything else is free text
    with no recognised kind; the caller then applies the default kind and says so.
    """
    if not isinstance(text, str) or not text.strip():
        return None, ""
    head, sep, tail = text.partition(":")
    token = normalize_token(head)
    if token in EVIDENCE_GOALS:
        return token, tail.strip() if sep else ""
    return None, text.strip()
NO_PROBABILITY_NOTE = ("Outputs are distances, counts, hours and ranks in a synthetic feature space. "
                       "They are not probabilities of interview, offer or compensation.")


def _validate_actions(planned_actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(planned_actions, (str, bytes)) or not isinstance(planned_actions, Sequence):
        raise InvalidInputError("planned_actions must be a list")
    if len(planned_actions) > MAX_ACTIONS:
        raise InvalidInputError(f"at most {MAX_ACTIONS} planned actions are supported")
    out = []
    for n, a in enumerate(planned_actions):
        if not isinstance(a, Mapping):
            raise InvalidInputError(f"planned_actions[{n}] must be an object")
        extra = set(a) - {"skill", "effort_hours", "evidence_goal"}
        if extra:
            raise InvalidInputError(f"planned_actions[{n}] has unsupported keys {sorted(extra)}")
        skill, hours = a.get("skill"), a.get("effort_hours")
        if not isinstance(skill, str) or not skill.strip():
            raise InvalidInputError(f"planned_actions[{n}].skill must be a non-empty string")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not np.isfinite(hours) or hours < 0:
            raise InvalidInputError(f"planned_actions[{n}].effort_hours must be a finite number >= 0")
        goal = a.get("evidence_goal")
        if goal is not None and not isinstance(goal, str):
            raise InvalidInputError(f"planned_actions[{n}].evidence_goal must be a string")
        clean: dict[str, Any] = {"skill": skill.strip()[:64], "effort_hours": float(hours)}
        if goal is not None:
            clean["evidence_goal"] = sanitize_label(goal, "")[:GOAL_TEXT_MAX]
        out.append(clean)
    return out


def apply_actions(engine: AtlasEngine, candidate: FeatureVector,
                  actions: Sequence[Mapping[str, Any]], scenario_ref: str) -> dict[str, Any]:
    """Return the hypothetical evidence vector after ``actions`` plus a full audit trail."""
    vocab = engine.vocab
    index = vocab.index
    values = candidate.values.copy()
    states = candidate.states.copy()
    applied, unmapped, risks, assumptions = [], [], [], []
    for a in actions:
        raw = a["skill"]
        token = normalize_token(raw)
        if token in BUNDLES:
            features = [f for f in BUNDLES[token] if f in index]
            how = "bundle"
        else:
            feature, how = vocab.resolve(raw)
            features = [feature] if feature else []
        if not features:
            unmapped.append(raw)
            continue
        goal_token, _ = parse_evidence_goal(a.get("evidence_goal"))
        if goal_token is not None:
            goal, cap = goal_token, EVIDENCE_GOALS[goal_token]
        else:
            goal, cap = DEFAULT_GOAL, EVIDENCE_GOALS[DEFAULT_GOAL]
            assumptions.append(f"action on {raw!r}: evidence goal missing or not recognised; "
                               f"treated as {DEFAULT_GOAL} (evidence level capped at {cap}). Prefix the "
                               f"goal with a kind, for example 'portfolio-project: ...', to change this")
        hours_each = a["effort_hours"] / len(features)
        for f in features:
            i = index[f]
            fdef = vocab.get(f)
            f_cap = cap
            if fdef.acquisition == ACQ_ROLE and goal not in ROLE_CAPABLE_GOALS:
                f_cap = min(cap, NON_ROLE_CAP)
                risks.append(f"{f}: evidence is normally gained in-role; a {goal} action is capped "
                             f"at evidence level {NON_ROLE_CAP}")
            before_state = int(states[i])
            old = 0.0 if before_state in (STATE_UNKNOWN, STATE_CONTRADICTED) else float(values[i])
            ref = fdef.effort_mid
            blocked = before_state == STATE_CONTRADICTED
            if blocked:
                # A contradiction is a fact about existing evidence. Hours do not erase it.
                new = 0.0
                risks.append(f"{f}: evidence on file contradicts this claim; the action is not "
                             "simulated until the contradiction is resolved")
            elif old >= f_cap:
                new = old
            elif ref <= 0:
                new = f_cap
            else:
                # Linear evidence rate: ``ref`` hours take a feature from 0 to the goal's cap.
                new = min(f_cap, old + hours_each * f_cap / ref)
            completion = 1.0 if f_cap <= old else (new - old) / (f_cap - old)
            if hours_each == 0:
                risks.append(f"{f}: zero effort hours planned; no change")
            if not blocked:
                if new > 0:
                    values[i], states[i] = new, STATE_STATED
                elif before_state == STATE_UNKNOWN:
                    values[i] = np.nan
            applied.append({
                "skill": raw, "feature": f, "resolved_via": how, "evidence_goal": goal,
                "effort_hours": round(hours_each, 2),
                "reference_hours_assumed": round(ref, 1),
                "completion": round(completion, 4),
                "evidence_level_before": round(old, 4),
                "evidence_level_after": round(float(new), 4),
                "blocked_by_contradiction": bool(blocked),
                "reaches_evidenced_level": bool(new >= EVIDENCED_LEVEL),
            })
    if unmapped:
        risks.append(f"{len(unmapped)} planned action(s) name skills outside the vocabulary and "
                     "were not simulated: " + ", ".join(repr(u) for u in unmapped[:5]))
    scenario = FeatureVector(
        entity_id=scenario_ref, kind="scenario", label="Hypothetical scenario",
        values=values, states=states, vocab_digest=candidate.vocab_digest,
        feature_names=candidate.feature_names, provenance=candidate.provenance,
        meta={"derived_from": candidate.entity_id})
    return {"vector": scenario, "applied": applied, "unmapped": unmapped, "risks": risks,
            "assumptions": assumptions}


def _snapshot(engine: AtlasEngine, vec: FeatureVector, role: str) -> dict[str, Any]:
    gap = analyze_gap(engine, vec, role)
    return {
        "requirement_shortfall": gap["proximity"]["requirement_shortfall"],
        "full_space_distance": gap["proximity"]["full_space_distance"],
        "shortfall_rank_among_roles": gap["proximity"]["shortfall_rank_among_roles"],
        "core_summary": gap["core_summary"],
        "nearest_roles_by_shortfall": engine.role_order(vec)[:3],
    }


def _same_budget_alternative(engine: AtlasEngine, candidate: FeatureVector, role: str,
                             budget_hours: float) -> dict[str, Any]:
    """Greedy use of the same hours on the target's study-acquirable requirements."""
    vocab = engine.vocab
    p = engine.profiles[role]
    cap = EVIDENCE_GOALS["portfolio-project"]
    options = []
    for i, name in enumerate(p.feature_names):
        if name.startswith("ctx:") or p.prevalence[i] < 0.25:
            continue
        fdef = vocab.get(name)
        state, level = candidate_state(candidate, name)
        level = level or 0.0
        if fdef.acquisition == ACQ_ROLE or state == "contradicted" or level >= cap:
            continue
        hours = fdef.effort_mid * (cap - level) / cap
        s = engine.model.scale[i]
        gain = (max(0.0, p.centroid[i] - level) / s) ** 2 - (max(0.0, p.centroid[i] - cap) / s) ** 2
        if hours > 0 and gain > 0:
            options.append((gain / hours, name, hours))
    options.sort(key=lambda t: (-t[0], t[1]))
    left = float(budget_hours)
    actions = []
    for _, name, hours in options:
        if left <= 0:
            break
        spend = min(hours, left)
        actions.append({"skill": name, "effort_hours": round(spend, 1), "evidence_goal": "portfolio-project"})
        left -= spend
    result = apply_actions(engine, candidate, actions, candidate.entity_id + "#alt-same-budget")
    snap = _snapshot(engine, result["vector"], role)
    return {"label": "same hours, spent where the stated shortfall shrinks most per hour",
            "planned_actions": actions, "hours_used": round(budget_hours - max(left, 0.0), 1),
            "requirement_shortfall": snap["requirement_shortfall"], "core_summary": snap["core_summary"],
            "caveat": "Greedy on synthetic effort placeholders; role-exposure requirements are excluded "
                      "because hours of study do not produce that evidence."}


def simulate_transition(
    engine: AtlasEngine,
    candidate: FeatureVector,
    target_role: str,
    planned_actions: Sequence[Mapping[str, Any]],
    time_horizon_months: int = 12,
    hours_per_week: float = 6.0,
    graph: TransitionGraph | None = None,
    projection_id: str | None = None,
) -> dict[str, Any]:
    """Build one hypothetical ``TransitionScenario`` for a candidate, target role and plan."""
    engine.check_vector(candidate)
    target_input = target_role
    try:
        target_role = engine.resolve_role(target_role)
    except InvalidInputError as exc:
        raise InvalidInputError(f"unknown target_role: {exc}") from exc
    if isinstance(time_horizon_months, bool) or not isinstance(time_horizon_months, int) or time_horizon_months < 1:
        raise InvalidInputError("time_horizon_months must be an integer >= 1")
    if isinstance(hours_per_week, bool) or not isinstance(hours_per_week, (int, float)) \
            or not np.isfinite(hours_per_week) or not (0 < hours_per_week <= 100):
        raise InvalidInputError("hours_per_week must be within (0, 100]")
    actions = _validate_actions(planned_actions)
    graph = graph or build_transition_graph(engine)
    projection_id = projection_id or f"pca-{engine.model.model_id}-{engine.model.n_components}d"

    digest_src = json.dumps({"c": candidate.vector_ref, "t": target_role, "a": actions,
                             "h": time_horizon_months, "w": hours_per_week, "m": engine.model.model_id},
                            sort_keys=True, separators=(",", ":")).encode("utf-8")
    scenario_id = "scn-" + hashlib.sha256(digest_src).hexdigest()[:16]
    result = apply_actions(engine, candidate, actions, f"{candidate.entity_id}#{scenario_id}")
    scenario_vec: FeatureVector = result["vector"]
    risks: list[str] = list(result["risks"])
    assumptions: list[str] = [
        "All role requirements come from synthetic, fictional postings grouped by role_family.",
        "Evidence levels per claim status and per evidence goal are declared conventions, not measurements.",
        "Effort reference hours are vocabulary placeholders; real effort varies by person.",
        "A feature with no claim is 'no evidence on file', not proof the skill is absent.",
        f"Capacity assumes {hours_per_week} hours per week for {time_horizon_months} month(s).",
        ELIGIBILITY_NOTE,
    ] + result["assumptions"]

    before = _snapshot(engine, candidate, target_role)
    after = _snapshot(engine, scenario_vec, target_role)
    gap_after = analyze_gap(engine, scenario_vec, target_role)
    remaining = [{"feature": r["feature"], "tier": r["tier"], "state": r["candidate_state"],
                  "acquisition": r["acquisition"],
                  "stated_in_share_of_postings": r["stated_in_share_of_postings"]}
                 for r in gap_after["requirements"]
                 if r["tier"] == "core" and r["candidate_state"] != "evidenced"]
    planned_hours = float(sum(a["effort_hours"] for a in actions))
    capacity = hours_per_week * WEEKS_PER_MONTH * time_horizon_months
    movement = explain_movement(engine, candidate, scenario_vec, target_role)

    p = engine.profiles[target_role]
    for a in result["applied"]:
        i = p.feature_names.index(a["feature"])
        if p.prevalence[i] < 0.25:
            risks.append(f"{a['feature']}: stated in only {p.prevalence[i]:.0%} of target-role postings; "
                         "this action barely changes the stated shortfall")
    if planned_hours > capacity:
        risks.append(f"plan needs {planned_hours:.0f} h but the horizon holds about {capacity:.0f} h "
                     f"at {hours_per_week} h/week")
    role_left = [r["feature"] for r in remaining if r["acquisition"] == ACQ_ROLE]
    if role_left:
        risks.append(f"{len(role_left)} core requirement(s) need in-role evidence this plan cannot "
                     "produce: " + ", ".join(role_left))
    if gap_after["contradicted"]:
        risks.append("contradicted core claims remain: " + ", ".join(gap_after["contradicted"]))
    conf = gap_after["confidence"]
    if conf["level"] != "high":
        risks.append("target role description has " + conf["level"] + " confidence: " + "; ".join(conf["reasons"]))
    if gap_after["unknown_candidate_inputs"]:
        risks.append("unknown candidate inputs were mean-imputed for distances: "
                     + ", ".join(gap_after["unknown_candidate_inputs"]))
    if gap_after["unobserved_requirements"]["unmapped_requirement_share"] > 0:
        risks.append("some posting requirements could not be interpreted; the requirement list is incomplete")
    vis = movement["share_of_movement_visible_on_map"]
    if vis is not None and vis < 0.5:
        risks.append(f"only {vis:.0%} of this scenario's movement is visible on the 2-D map; "
                     "do not judge progress from the picture")
    if not result["applied"]:
        risks.append("no planned action could be simulated; hypothetical equals baseline")

    target_skills = p.core_features()
    cov_before = before["core_summary"]["evidenced"] / len(target_skills) if target_skills else None
    cov_after = after["core_summary"]["evidenced"] / len(target_skills) if target_skills else None
    lead_idx = [i for i, f in enumerate(engine.vocab.features) if f.group == "leadership"]
    lead_value = float(p.prevalence[lead_idx].mean()) if lead_idx else None
    lead_level = ("unknown" if lead_value is None else "low" if lead_value < LEADERSHIP_BINS[0]
                  else "medium" if lead_value < LEADERSHIP_BINS[1] else "high")
    uncertainty = {"high": "low", "medium": "medium", "low": "high"}[conf["level"]]

    proj_b, proj_a = engine.project(candidate), engine.project(scenario_vec)
    point_warnings = [MAP_IS_NOT_FIT, "hypothetical-scenario"]
    points = [
        atlas_point(candidate.entity_id, "Candidate (evidence on file)", proj_b["coords"],
                    candidate.vector_ref, "candidate", "pca", projection_id, None,
                    f"gap:{target_role}:requirement_shortfall", [MAP_IS_NOT_FIT]),
        atlas_point(scenario_vec.entity_id, "Hypothetical scenario", proj_a["coords"],
                    scenario_vec.vector_ref, "scenario", "pca", projection_id, None,
                    f"gap:{target_role}:requirement_shortfall", point_warnings),
    ]
    paths = plan_paths(engine, graph, candidate, target_role, hours_per_week=hours_per_week,
                       horizon_months=time_horizon_months, top=3)
    outputs = {
        "schema_version": SCENARIO_OUTPUTS_VERSION,
        "not_a_hiring_probability": True,
        "metric_semantics": NO_PROBABILITY_NOTE,
        "target_role_family": target_role,
        "target_label": p.label,
        "target_skills": target_skills,
        "coverage_before": None if cov_before is None else round(cov_before, 4),
        "coverage_after": None if cov_after is None else round(cov_after, 4),
        "coverage_definition": "share of target_skills (stated in at least half of the target role's "
                               f"postings) with evidence at or above level {EVIDENCED_LEVEL}; weak, missing "
                               "and contradicted evidence do not count",
        "leadership_exposure": lead_level,
        "leadership_exposure_value": None if lead_value is None else round(lead_value, 4),
        "leadership_exposure_definition": "mean share of target-role postings stating each leadership feature; "
                                          f"bins: < {LEADERSHIP_BINS[0]} low, < {LEADERSHIP_BINS[1]} medium, else high",
        "uncertainty": uncertainty,
        "uncertainty_note": "Uncertainty of the role description, not of any outcome: " + "; ".join(conf["reasons"]),
        "atlas_entity_id": scenario_vec.entity_id,
        "baseline": before,
        "hypothetical": after,
        "delta": {
            "requirement_shortfall": round(after["requirement_shortfall"] - before["requirement_shortfall"], 6),
            "full_space_distance": round(after["full_space_distance"] - before["full_space_distance"], 6),
            "shortfall_rank_among_roles": after["shortfall_rank_among_roles"] - before["shortfall_rank_among_roles"],
            "core_requirements_evidenced": after["core_summary"]["evidenced"] - before["core_summary"]["evidenced"],
        },
        "applied_actions": result["applied"],
        "unmapped_actions": result["unmapped"],
        "remaining_missing_evidence": remaining,
        "effort": {"planned_hours": round(planned_hours, 1), "capacity_hours_within_horizon": round(capacity, 1),
                   "fits_time_budget": bool(planned_hours <= capacity), "hours_per_week": hours_per_week},
        "movement": movement,
        "map_points": points,
        "alternatives": {
            "same_budget": _same_budget_alternative(engine, candidate, target_role, planned_hours),
            "paths": paths["alternatives"],
            "path_ranking_note": paths["ranking_note"],
        },
        "confidence_in_role_description": conf,
        "model_ref": {"model_id": engine.model.model_id, "vocab_digest": engine.matrix.vocab_digest,
                      "projection_id": projection_id},
    }
    unique_risks: list[str] = []
    for r in risks:
        if r not in unique_risks:
            unique_risks.append(r)
    return {
        "id": scenario_id,
        "candidate_ref": candidate.entity_id,
        "target_role": target_input.strip() if isinstance(target_input, str) else target_role,
        "planned_actions": actions,
        "time_horizon_months": time_horizon_months,
        "status": "hypothetical",
        "assumptions": assumptions,
        "outputs": outputs,
        "risks": unique_risks,
    }
