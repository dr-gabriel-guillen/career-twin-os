"""Gap analysis and local-movement explanations.

Vocabulary of this module, chosen to avoid over-claiming:

* ``no-evidence-on-file``: the role's postings commonly state the feature and the candidate has
  no claim about it. This is a *missing evidence* finding, not proof the skill is absent.
* ``weak-evidence``: a claim exists but is self-reported, unverified, expired or partial.
* ``contradicted``: evidence on file says the claim does not hold.
* ``evidenced``: supported at or above ``EVIDENCED_LEVEL``.

Nothing here estimates the chance of an interview, an offer or a salary.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .engine import AtlasEngine
from .errors import InvalidInputError
from .features import STATE_CONTRADICTED, STATE_NOT_STATED, STATE_STATED, STATE_UNKNOWN, FeatureVector

EVIDENCED_LEVEL = 0.75
STATUS_NOTE = "hypothetical_gap_not_probability"


def candidate_state(vec: FeatureVector, feature: str) -> tuple[str, float | None]:
    i = vec.feature_names.index(feature)
    state, value = int(vec.states[i]), float(vec.values[i])
    if state == STATE_UNKNOWN:
        return "unknown", None
    if state == STATE_CONTRADICTED:
        return "contradicted", 0.0
    if state == STATE_NOT_STATED or value <= 0.0:
        return "no-evidence-on-file", 0.0
    if state == STATE_STATED and value >= EVIDENCED_LEVEL:
        return "evidenced", value
    return "weak-evidence", value


def _confidence(engine: AtlasEngine, role: str) -> dict[str, Any]:
    p = engine.profiles[role]
    stab = engine.stability[role]
    reasons: list[str] = []
    score = 2
    if stab["unstable"]:
        score -= 1
        reasons.extend(stab["reasons"])
    if p.n_postings < 30:
        score -= 1
        reasons.append(f"role profile rests on {p.n_postings} posting(s)")
    if p.unmapped_requirement_share > 0.25:
        score -= 1
        reasons.append(f"{p.unmapped_requirement_share:.0%} of requirement lines could not be "
                       "mapped to the vocabulary")
    level = "high" if score >= 2 else "medium" if score == 1 else "low"
    if not reasons:
        reasons.append("profile built from enough postings, stable under resampling, "
                       "requirements mostly interpretable")
    return {"level": level, "reasons": reasons,
            "meaning": "confidence in the description of the role's stated requirements; "
                       "not confidence in any hiring outcome"}


def analyze_gap(engine: AtlasEngine, candidate: FeatureVector, role: str,
                core_threshold: float = 0.5, common_threshold: float = 0.25) -> dict[str, Any]:
    """Compare a candidate evidence vector with one role profile."""
    engine.check_vector(candidate)
    if role not in engine.profiles:
        raise InvalidInputError(f"unknown target role {role!r}; known: {sorted(engine.profiles)}")
    if not (0.0 < common_threshold <= core_threshold <= 1.0):
        raise InvalidInputError("thresholds must satisfy 0 < common <= core <= 1")
    p = engine.profiles[role]
    requirements = []
    counts = {"evidenced": 0, "weak-evidence": 0, "no-evidence-on-file": 0, "contradicted": 0, "unknown": 0}
    for i, name in enumerate(p.feature_names):
        if name.startswith("ctx:") or p.prevalence[i] < common_threshold:
            continue
        state, level = candidate_state(candidate, name)
        tier = "core" if p.prevalence[i] >= core_threshold else "common"
        fdef = engine.vocab.get(name)
        requirements.append({
            "feature": name, "tier": tier,
            "stated_in_share_of_postings": round(float(p.prevalence[i]), 4),
            "named_as_minimum_qualification_share": round(float(p.min_qualification_share[i]), 4),
            "candidate_state": state, "candidate_evidence_level": level,
            "evidence_refs": list(candidate.provenance.get(name, ())),
            "acquisition": fdef.acquisition,
        })
        if tier == "core":
            counts[state] += 1
    requirements.sort(key=lambda r: (-r["stated_in_share_of_postings"], r["feature"]))
    core = [r for r in requirements if r["tier"] == "core"]
    table = engine.role_table(candidate)
    row = next(r for r in table if r["role"] == role)
    unknown_inputs = [n for n, s in zip(candidate.feature_names, candidate.states) if s == STATE_UNKNOWN]
    return {
        "status": STATUS_NOTE,
        "target_role": role, "target_label": p.label,
        "basis": {"n_postings": p.n_postings, "core_threshold": core_threshold,
                  "common_threshold": common_threshold,
                  "note": "requirements are what synthetic postings state, grouped by role_family"},
        "requirements": requirements,
        "core_summary": counts,
        "skill_gaps": [r["feature"] for r in core if r["candidate_state"] == "no-evidence-on-file"],
        "evidence_gaps": [r["feature"] for r in core if r["candidate_state"] == "weak-evidence"],
        "contradicted": [r["feature"] for r in core if r["candidate_state"] == "contradicted"],
        "minimum_qualifications": [r["feature"] for r in requirements
                                   if r["named_as_minimum_qualification_share"] >= 0.25],
        "unobserved_requirements": {
            "unmapped_requirement_share": round(p.unmapped_requirement_share, 4),
            "note": "Postings can hold requirements this vocabulary cannot read, and real roles "
                    "have requirements that postings never state. Treat this list as incomplete.",
        },
        "proximity": {
            "requirement_shortfall": round(row["requirement_shortfall"], 6),
            "full_space_distance": round(row["full_space_distance"], 6),
            "cosine_similarity": None if row["cosine_similarity"] is None else round(row["cosine_similarity"], 6),
            "shortfall_rank_among_roles": row["shortfall_rank"],
            "full_space_rank_among_roles": row["full_space_rank"],
            "map_distance": round(row["map_distance"], 6),
            "map_rank_among_roles": row["map_rank"],
            "map_rank_disagrees_with_full_space": row["map_rank_disagrees"],
            "note": "Use the full-space numbers. Map distance is shown only to expose distortion.",
        },
        "unknown_candidate_inputs": unknown_inputs,
        "confidence": _confidence(engine, role),
    }


def explain_movement(engine: AtlasEngine, before: FeatureVector, after: FeatureVector,
                     role: str | None = None, top: int = 8) -> dict[str, Any]:
    """Explain how a change in evidence moves a point: on the map and in the full space.

    The map shift is decomposed exactly, feature by feature, because PCA is linear:
    ``shift = sum_f delta_f * component_weights_f``. A feature with a large full-space effect
    can have a tiny map effect; that mismatch is reported instead of hidden.
    """
    xb, xa = engine.imputed(before), engine.imputed(after)
    delta = (xa - xb) / engine.model.scale
    comps = engine.model.components
    contrib = delta[:, None] * comps.T                       # (d, k)
    shift = contrib.sum(axis=0)
    changed = np.flatnonzero(np.abs(delta) > 1e-12)
    per_feature = []
    target = None
    if role is not None:
        if role not in engine.profiles:
            raise InvalidInputError(f"unknown role {role!r}")
        target = engine.profiles[role].centroid
    for i in changed:
        entry: dict[str, Any] = {
            "feature": engine.matrix.feature_names[i],
            "change": round(float(xa[i] - xb[i]), 6),
            "map_shift": [round(float(c), 6) for c in contrib[i]],
        }
        if target is not None:
            s = engine.model.scale[i]
            before_sq = ((target[i] - xb[i]) / s) ** 2
            after_sq = ((target[i] - xa[i]) / s) ** 2
            entry["full_space_sq_distance_change"] = round(float(after_sq - before_sq), 6)
        per_feature.append(entry)
    per_feature.sort(key=lambda e: -float(np.linalg.norm(e["map_shift"])))
    full_move = float(np.linalg.norm(delta))
    map_move = float(np.linalg.norm(shift))
    out: dict[str, Any] = {
        "map_shift": [round(float(s), 6) for s in shift],
        "map_shift_length": round(map_move, 6),
        "full_space_shift_length": round(full_move, 6),
        "share_of_movement_visible_on_map": round(map_move / full_move, 6) if full_move > 1e-12 else None,
        "per_feature": per_feature[:top],
        "features_changed": int(len(changed)),
        "note": "The map shows only part of a movement. Judge progress by full-space numbers.",
    }
    if target is not None:
        s = engine.model.scale
        out["full_space_distance_to_role"] = {
            "role": role,
            "before": round(float(np.linalg.norm((target - xb) / s)), 6),
            "after": round(float(np.linalg.norm((target - xa) / s)), 6),
        }
    return out
