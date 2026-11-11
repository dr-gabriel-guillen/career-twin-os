"""Constraint-first, explainable ranking.

The score is a *relative heuristic for ordering a shortlist*. It is not an offer,
interview or hiring probability and is labelled so in every payload. Unknown inputs
are never scored as zero: they are left out of the point score and widen the
reported score interval instead.
"""
from __future__ import annotations

from typing import Any

from ct_connectors.base import canonical_json, sha256_hex

from . import schema, taxonomy
from .normalize import employer_key, utc
from .vault import PROOF_WEIGHT, EvidenceVault

MATCH_API_VERSION = "a-match/0.1"
SCORE_TYPE = "relative_heuristic_not_offer_probability"
NEAR_SIGNALS = ("skill_coverage", "proof_strength", "seniority_alignment", "workstyle")
STRATEGIC_SIGNALS = ("transition_utility", "customer_proximity", "enterprise_delivery")
DEFAULT_WEIGHTS = {"skill_coverage": 0.45, "proof_strength": 0.20, "seniority_alignment": 0.20, "workstyle": 0.15,
                   "transition_utility": 0.50, "customer_proximity": 0.25, "enterprise_delivery": 0.25}
DEFAULT_STRATEGY_WEIGHT = 0.5
WORKSTYLE_BY_PREFERENCE_RANK = (1.0, 0.7, 0.4)
DISCLAIMERS = (
    "Score is a relative ordering heuristic over synthetic/offline data; it is NOT an offer, interview or hiring probability.",
    "Unknown inputs are excluded from the point score and shown as a wider score interval; they are never treated as zero.",
    "Compensation is only reported when the source stated an annual figure with an explicit currency; it is never inferred or converted.",
    "customer_proximity, enterprise_delivery and transition_utility rest on lane A's authored taxonomy priors, not measured outcomes.",
    "Availability reflects the last capture; lane A cannot confirm a posting is still open.",
)


class MatchRequestError(ValueError):
    pass


def _level(name: str | None) -> int | None:
    return taxonomy.SENIORITY_LADDER.index(name) if name in taxonomy.SENIORITY_LADDER else None


def _prefs(raw: Any) -> dict[str, Any]:
    """Validate and default the preference block. Unknown keys are rejected (no silent typos)."""
    if raw is None:
        raw = {}
    if isinstance(raw, dict) and isinstance(raw.get("remote"), str):
        raw = dict(raw, remote=[raw["remote"]])  # legacy single-value form
    problems = schema.errors(raw, schema.load("a-preferences"))
    if problems:
        raise MatchRequestError("preferences invalid: " + "; ".join(problems[:5]))
    prefs = dict(raw)
    prefs["remote"] = list(prefs.get("remote") or [])
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(prefs.get("weights") or {})
    if all(weights[s] == 0 for s in NEAR_SIGNALS) or all(weights[s] == 0 for s in STRATEGIC_SIGNALS):
        raise MatchRequestError("weights: each composite needs at least one non-zero signal weight")
    prefs["weights"] = weights
    prefs.setdefault("strategy_weight", DEFAULT_STRATEGY_WEIGHT)
    families = list(prefs.get("target_families") or [])
    unmapped = []
    for title in prefs.get("target_roles") or []:
        fam = taxonomy.classify_family(title)
        (families.append(fam) if fam != "unknown" else unmapped.append(title))
    prefs["_target_families"] = sorted(set(families))
    prefs["_unmapped_targets"] = unmapped
    lo, hi = _level(prefs.get("seniority_min")), _level(prefs.get("seniority_max"))
    if lo is not None and hi is not None and lo > hi:
        raise MatchRequestError("seniority_min is above seniority_max")
    prefs["_excluded"] = {employer_key(x) for x in prefs.get("excluded_employers") or []}
    return prefs


def _constraint(name: str, outcome: str, detail: str) -> dict[str, str]:
    return {"constraint": name, "outcome": outcome, "detail": detail}


def hard_constraints(env: dict[str, Any], prefs: dict[str, Any]) -> list[dict[str, str]]:
    p, e = env["posting"], env["ext"]
    out: list[dict[str, str]] = []
    bad_flags = [f for f in e["security_flags"] if f == "prompt-injection-suspected"]
    if bad_flags and not prefs.get("allow_flagged_content", False):
        out.append(_constraint("content-integrity", "fail", "prompt-injection-suspected: posting text tries to instruct automated readers"))
    else:
        out.append(_constraint("content-integrity", "pass", "no manipulation flags" if not bad_flags else "flagged content allowed by request"))
    if p["status"] == "expired":
        out.append(_constraint("availability", "fail", f"expired: {e['status_reason']}"))
    elif p["status"] == "unknown":
        allowed = prefs.get("include_unknown_status", False)
        out.append(_constraint("availability", "unknown" if allowed else "fail", f"availability-unknown: {e['status_reason']}"))
    else:
        out.append(_constraint("availability", "pass", e["status_reason"]))
    if "phantom-risk:evergreen-requisition" in e["quality_flags"] and prefs.get("exclude_phantom_risk", True):
        out.append(_constraint("phantom-risk", "fail", "evergreen/talent-pool requisition: no specific opening stated"))
    if e["employer"]["key"] in prefs["_excluded"]:
        out.append(_constraint("excluded-employer", "fail", "employer excluded by user"))
    if prefs["remote"]:
        if p["remote_policy"] == "unknown":
            out.append(_constraint("remote-policy", "unknown", "source did not state a workplace policy"))
        else:
            ok = p["remote_policy"] in prefs["remote"]
            out.append(_constraint("remote-policy", "pass" if ok else "fail", p["remote_policy"]))
    if prefs.get("jurisdictions"):
        have = e["location"]["jurisdictions"]
        if not have:
            out.append(_constraint("jurisdiction", "unknown", "job jurisdiction not stated or ambiguous"))
        else:
            ok = bool(set(have) & set(prefs["jurisdictions"]))
            out.append(_constraint("jurisdiction", "pass" if ok else "fail", ",".join(have)))
    if prefs.get("employment_types"):
        if p["employment_type"] == "unknown":
            out.append(_constraint("employment-type", "unknown", "source did not state employment type"))
        else:
            ok = p["employment_type"] in prefs["employment_types"]
            out.append(_constraint("employment-type", "pass" if ok else "fail", p["employment_type"]))
    lo, hi, level = _level(prefs.get("seniority_min")), _level(prefs.get("seniority_max")), _level(p["seniority"])
    if lo is not None or hi is not None:
        if level is None:
            out.append(_constraint("seniority-band", "unknown", "seniority not stated"))
        else:
            ok = (lo is None or level >= lo) and (hi is None or level <= hi)
            out.append(_constraint("seniority-band", "pass" if ok else "fail", p["seniority"]))
    floor = prefs.get("compensation_floor")
    comp = p["compensation"]
    if floor or prefs.get("require_known_compensation"):
        if comp is None:
            strict = prefs.get("require_known_compensation", False)
            out.append(_constraint("compensation-floor", "fail" if strict else "unknown",
                                   f"compensation unknown ({e['compensation']['state']}); not inferred"))
        elif floor and comp["currency"] != floor["currency"]:
            out.append(_constraint("compensation-floor", "unknown",
                                   f"stated in {comp['currency']}, floor in {floor['currency']}; no conversion performed"))
        elif floor:
            top = comp["max"] if comp["max"] is not None else comp["min"]
            if top < floor["amount"]:
                out.append(_constraint("compensation-floor", "fail", f"stated maximum {top:g} {comp['currency']} is below floor"))
            else:
                straddles = comp["min"] is not None and comp["min"] < floor["amount"]
                out.append(_constraint("compensation-floor", "pass", "range straddles floor" if straddles else "at or above floor"))
        else:
            out.append(_constraint("compensation-floor", "pass", "compensation stated"))
    return out


def signals(env: dict[str, Any], prefs: dict[str, Any], proof: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each signal: value in [0,1] or None (unknown) plus the basis for that value."""
    p, e = env["posting"], env["ext"]
    required, preferred = e["skills_required"], e["skills_preferred"]
    have = {s for s, slot in proof.items() if slot["status"] is not None and slot["weight"] > 0}
    out: dict[str, dict[str, Any]] = {}
    denom = len(required) + 0.5 * len(preferred)
    matched = [s for s in required + preferred if s in have]
    if denom == 0:
        out["skill_coverage"] = {"value": None, "basis": "posting lists no recognizable skills"}
    else:
        num = sum(1 for s in required if s in have) + 0.5 * sum(1 for s in preferred if s in have)
        out["skill_coverage"] = {"value": num / denom, "basis": f"{len(matched)} of {len(required) + len(preferred)} listed skills claimed (preferred count half)"}
    if matched:
        out["proof_strength"] = {"value": sum(proof[s]["weight"] for s in matched) / len(matched),
                                 "basis": "mean evidence weight of matched skills (supported=1, self-reported=0.5, unverified=0.25)"}
    else:
        out["proof_strength"] = {"value": None, "basis": "no matched skills to evidence"}
    level, target = _level(p["seniority"]), _level(prefs.get("seniority_target"))
    if target is None:
        lo, hi = _level(prefs.get("seniority_min")), _level(prefs.get("seniority_max"))
        target = None if lo is None and hi is None else ((lo if lo is not None else hi) + (hi if hi is not None else lo)) / 2  # type: ignore[operator]
    if level is None or target is None:
        out["seniority_alignment"] = ({"value": None, "basis": "no seniority preference given", "na": True} if target is None
                                      else {"value": None, "basis": "job seniority not stated"})
    else:
        out["seniority_alignment"] = {"value": max(0.0, 1 - abs(level - target) / 3), "basis": f"{p['seniority']} vs preferred level"}
    if not prefs["remote"]:
        out["workstyle"] = {"value": None, "basis": "no workstyle preference given", "na": True}
    elif p["remote_policy"] == "unknown":
        out["workstyle"] = {"value": None, "basis": "workplace policy not stated"}
    elif p["remote_policy"] in prefs["remote"]:
        rank = prefs["remote"].index(p["remote_policy"])
        out["workstyle"] = {"value": WORKSTYLE_BY_PREFERENCE_RANK[min(rank, 2)], "basis": f"{p['remote_policy']} is preference #{rank + 1}"}
    else:
        out["workstyle"] = {"value": 0.0, "basis": f"{p['remote_policy']} is not a listed preference"}
    family = p.get("role_family", "unknown")
    targets = prefs["_target_families"]
    best = None
    for t in targets:
        c = taxonomy.family_closeness(family, t)
        if c is not None and (best is None or c > best[0]):
            best = (c, t)
    if not targets:
        out["transition_utility"] = {"value": None, "basis": "no target roles given", "na": True}
    elif best is None:
        out["transition_utility"] = {"value": None, "basis": "role family unrecognized"}
    else:
        core = taxonomy.FAMILIES[best[1]]["core"]
        stretch = len(set(core) & set(required + preferred)) / len(core)
        out["transition_utility"] = {"value": 0.7 * best[0] + 0.3 * stretch,
                                     "basis": f"taxonomy-prior: {family}->{best[1]} closeness {best[0]:g}; exercises {stretch:.0%} of target core skills"}
    for name, key in (("customer_proximity", "customer"), ("enterprise_delivery", "enterprise")):
        cue = min(1.0, e["cues"][key] / 3)
        if family in taxonomy.FAMILIES:
            prior = taxonomy.FAMILIES[family][key]
            out[name] = {"value": 0.7 * prior + 0.3 * cue, "basis": f"taxonomy-prior {prior:g} for {family}; {e['cues'][key]} text cue(s)"}
        elif e["cues"][key]:
            out[name] = {"value": cue, "basis": f"{e['cues'][key]} text cue(s); role family unrecognized"}
        else:
            out[name] = {"value": None, "basis": "role family unrecognized and no text cues"}
    return out


def _wmean(sig: dict[str, dict[str, Any]], names: tuple[str, ...], weights: dict[str, float], fill: float | None) -> float | None:
    num = den = 0.0
    for name in names:
        value = sig[name]["value"]
        if value is None:
            if fill is None or sig[name].get("na"):
                continue  # "na" = the user gave no preference: not an unknown, so it never widens the interval
            value = fill
        num += weights[name] * value
        den += weights[name]
    return num / den if den > 0 else None


def compose(sig: dict[str, dict[str, Any]], weights: dict[str, float], lam: float) -> dict[str, Any]:
    """Point score from known signals only; [low, high] by setting unknown signals to 0 / 1."""
    near, strat = _wmean(sig, NEAR_SIGNALS, weights, None), _wmean(sig, STRATEGIC_SIGNALS, weights, None)
    if near is None and strat is None:
        return {"near": None, "strategic": None, "overall": None, "low": None, "high": None}
    overall = ((1 - lam) * near + lam * strat) if near is not None and strat is not None else (near if strat is None else strat)

    def bound(fill: float) -> float:
        n, s = _wmean(sig, NEAR_SIGNALS, weights, fill), _wmean(sig, STRATEGIC_SIGNALS, weights, fill)
        if n is None or s is None:  # one composite has no applicable signal at all (user gave no such preference)
            return s if n is None else n  # type: ignore[return-value]
        return (1 - lam) * n + lam * s

    return {"near": near, "strategic": strat, "overall": overall,
            "low": min(bound(0.0), overall), "high": max(bound(1.0), overall)}


def _order(scored: list[tuple[str, dict[str, Any]]], key: str = "overall") -> list[str]:
    return [jid for jid, _ in sorted(scored, key=lambda t: (-(round(t[1][key], 9) if t[1][key] is not None else -1.0),
                                                           round((t[1]["high"] or 0) - (t[1]["low"] or 0), 9), t[0]))]


def _pct(value: float | None) -> float | None:
    return None if value is None else round(100 * value, 1)


def rank(jobs: list[dict[str, Any]], vault: EvidenceVault, preferences: dict[str, Any] | None, as_of: str, *,
         top_n: int = 10, counterfactual_top: int = 5) -> dict[str, Any]:
    """Rank a store view for one candidate. Pure function of its inputs."""
    prefs = _prefs(preferences)
    moment = utc(as_of).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= 500:
        raise MatchRequestError("top_n must be an integer in 1..500")
    proof = vault.skill_proof(moment)
    weights, lam = prefs["weights"], prefs["strategy_weight"]
    refusals: list[dict[str, Any]] = []
    eligible: dict[str, dict[str, Any]] = {}
    for env in jobs:
        jid = env["posting"]["id"]
        dedup = env["ext"].get("dedup") or {"is_canonical": True}
        if not dedup["is_canonical"]:
            refusals.append({"job_id": jid, "reasons": [f"duplicate-of:{dedup['canonical_id']}"]})
            continue
        checks = hard_constraints(env, prefs)
        failed = [c for c in checks if c["outcome"] == "fail"]
        if failed:
            refusals.append({"job_id": jid, "reasons": [f"{c['constraint']}: {c['detail']}" for c in failed]})
            continue
        sig = signals(env, prefs, proof)
        comp = compose(sig, weights, lam)
        if comp["overall"] is None:
            refusals.append({"job_id": jid, "reasons": ["insufficient-information: no signal could be computed"]})
            continue
        eligible[jid] = {"env": env, "checks": checks, "signals": sig, "comp": comp}
    scored = [(jid, item["comp"]) for jid, item in eligible.items()]
    order = _order(scored)
    position = {jid: i + 1 for i, jid in enumerate(order)}
    near_pos = {jid: i + 1 for i, jid in enumerate(_order([(j, c) for j, c in scored if c["near"] is not None], "near"))}
    strat_pos = {jid: i + 1 for i, jid in enumerate(_order([(j, c) for j, c in scored if c["strategic"] is not None], "strategic"))}
    spread = _sensitivity(eligible, weights, lam)
    shortlist = []
    for jid in order[:top_n]:
        item = eligible[jid]
        shortlist.append(_explain(jid, item, position[jid], near_pos.get(jid), strat_pos.get(jid), spread[jid], proof,
                                  prefs, moment,
                                  _counterfactuals(jid, eligible, prefs, proof, order) if position[jid] <= counterfactual_top else []))
    reason_counts: dict[str, int] = {}
    for r in refusals:
        for reason in r["reasons"]:
            key = reason.split(":")[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    k = min(top_n, len(order))
    near_top = [j for j in sorted(near_pos, key=near_pos.get)[:k]]  # type: ignore[arg-type]
    strat_top = [j for j in sorted(strat_pos, key=strat_pos.get)[:k]]  # type: ignore[arg-type]
    result: dict[str, Any] = {
        "api_version": MATCH_API_VERSION, "score_type": SCORE_TYPE, "as_of": moment,
        "subject_id": vault.subject_id, "taxonomy_version": taxonomy.taxonomy_version(),
        "policy": {"weights": weights, "strategy_weight": lam, "proof_weight": PROOF_WEIGHT,
                   "target_families": prefs["_target_families"], "unmapped_target_roles": prefs["_unmapped_targets"]},
        "corpus": {"considered": len(jobs), "eligible": len(order), "refused": len(refusals),
                   "refusal_reasons": dict(sorted(reason_counts.items()))},
        "shortlist": shortlist,
        "refusals": sorted(refusals, key=lambda r: r["job_id"]),
        "strategy_comparison": {"k": k, "near_term_top": near_top, "strategic_top": strat_top,
                                "overlap": len(set(near_top) & set(strat_top)),
                                "note": "near_term_fit rewards what the candidate can evidence today; strategic_utility rewards movement toward target roles"},
        "disclaimers": list(DISCLAIMERS),
    }
    result["result_hash"] = sha256_hex(canonical_json(result))
    return result


def _sensitivity(eligible: dict[str, dict[str, Any]], weights: dict[str, float], lam: float) -> dict[str, dict[str, int]]:
    """Rank range of every job when each weight moves ±20% (strategy weight ±0.1)."""
    variants: list[tuple[dict[str, float], float]] = []
    for name in weights:
        for factor in (0.8, 1.2):
            w = dict(weights)
            w[name] = weights[name] * factor
            variants.append((w, lam))
    variants += [(weights, max(0.0, lam - 0.1)), (weights, min(1.0, lam + 0.1))]
    spread = {jid: {"best": 10 ** 9, "worst": 0} for jid in eligible}
    for w, l in variants:
        order = _order([(jid, compose(item["signals"], w, l)) for jid, item in eligible.items()])
        for idx, jid in enumerate(order):
            spread[jid]["best"] = min(spread[jid]["best"], idx + 1)
            spread[jid]["worst"] = max(spread[jid]["worst"], idx + 1)
    return spread


def _counterfactuals(jid: str, eligible: dict[str, dict[str, Any]], prefs: dict[str, Any],
                     proof: dict[str, dict[str, Any]], order: list[str]) -> list[dict[str, Any]]:
    item = eligible[jid]
    env = item["env"]
    base = item["comp"]["overall"]
    others = sorted((eligible[o]["comp"]["overall"] for o in order if o != jid), reverse=True)

    def what_if(new_proof: dict[str, dict[str, Any]], label: str, kind: str) -> dict[str, Any]:
        comp = compose(signals(env, prefs, new_proof), prefs["weights"], prefs["strategy_weight"])
        new_rank = 1 + sum(1 for o in others if round(o, 9) > round(comp["overall"], 9))
        return {"kind": kind, "change": label, "score": _pct(comp["overall"]),
                "score_delta": round(100 * (comp["overall"] - base), 1), "rank": new_rank,
                "status": "hypothetical; assumes other candidates' and postings' data unchanged"}

    out = []
    have = {s for s, slot in proof.items() if slot["status"] is not None and slot["weight"] > 0}
    missing = [s for s in env["ext"]["skills_required"] if s not in have][:5]
    for skill in missing:
        alt = dict(proof)
        alt[skill] = {"status": "supported", "weight": PROOF_WEIGHT["supported"], "claim_ids": [],
                      "contradicted_claim_ids": [], "expired_claim_ids": []}
        out.append(what_if(alt, f"acquire and evidence skill '{skill}'", "skill-acquired"))
    weak = [s for s in env["ext"]["skills_required"] + env["ext"]["skills_preferred"]
            if s in have and proof[s]["status"] != "supported"]
    if weak:
        alt = {s: (dict(slot, status="supported", weight=PROOF_WEIGHT["supported"]) if s in weak else slot)
               for s, slot in proof.items()}
        out.append(what_if(alt, "attach verifying evidence to: " + ", ".join(weak), "evidence-added"))
    return out


def _explain(jid: str, item: dict[str, Any], position: int, near_rank: int | None, strat_rank: int | None,
             spread: dict[str, int], proof: dict[str, dict[str, Any]], prefs: dict[str, Any], as_of: str,
             counterfactuals: list[dict[str, Any]]) -> dict[str, Any]:
    env, sig, comp = item["env"], item["signals"], item["comp"]
    p, e = env["posting"], env["ext"]
    weights, lam = prefs["weights"], prefs["strategy_weight"]
    components = {}
    for group, names, share in (("near_term_fit", NEAR_SIGNALS, 1 - lam), ("strategic_utility", STRATEGIC_SIGNALS, lam)):
        known = sum(weights[n] for n in names if sig[n]["value"] is not None)
        for name in names:
            value = sig[name]["value"]
            effective = (weights[name] / known * share) if value is not None and known > 0 else 0.0
            if comp["near" if group == "near_term_fit" else "strategic"] is not None and (comp["near"] is None or comp["strategic"] is None):
                effective = (weights[name] / known) if value is not None and known > 0 else 0.0
            components[name] = {"group": group, "value": None if value is None else round(value, 4),
                                "configured_weight": weights[name], "effective_weight": round(effective, 4),
                                "contribution": None if value is None else round(100 * effective * value, 2),
                                "basis": sig[name]["basis"]}
    have = {s for s, slot in proof.items() if slot["status"] is not None and slot["weight"] > 0}
    matched, unsupported = [], []
    for kind, skills in (("required", e["skills_required"]), ("preferred", e["skills_preferred"])):
        for skill in skills:
            slot = proof.get(skill)
            if skill in have:
                matched.append({"skill": skill, "requirement": kind, "proof_status": slot["status"], "claim_ids": slot["claim_ids"]})  # type: ignore[index]
                if slot["status"] != "supported":  # type: ignore[index]
                    unsupported.append({"skill": skill, "reason": f"claim is {slot['status']}; no verifying evidence attached"})  # type: ignore[index]
            elif slot and slot["contradicted_claim_ids"]:
                unsupported.append({"skill": skill, "reason": "claim is contradicted; not counted"})
            elif slot and slot["expired_claim_ids"]:
                unsupported.append({"skill": skill, "reason": "claim validity has expired; not counted"})
    unknowns = [name for name in NEAR_SIGNALS + STRATEGIC_SIGNALS if sig[name]["value"] is None and not sig[name].get("na")]
    unknowns += [f"constraint:{c['constraint']}" for c in item["checks"] if c["outcome"] == "unknown"]
    if p["compensation"] is None:
        unknowns.append("compensation")
    unknowns += ["recruiter_preferences", "applicant_pool", "whether_posting_is_still_open"]
    return {
        "job_id": jid, "rank": position, "score": _pct(comp["overall"]), "score_type": SCORE_TYPE,
        "score_interval": {"low": _pct(comp["low"]), "high": _pct(comp["high"]),
                           "meaning": "range of the score if unknown signals were worst-case / best-case"},
        "title": p["title"], "organization": p["organization"], "role_family": p.get("role_family", "unknown"),
        "composites": {"near_term_fit": _pct(comp["near"]), "strategic_utility": _pct(comp["strategic"]),
                       "strategy_weight": lam, "rank_by_near_term_fit": near_rank, "rank_by_strategic_utility": strat_rank},
        "components": components,
        "matched_skills": matched,
        "missing_skills": [s for s in e["skills_required"] if s not in have],
        "missing_preferred_skills": [s for s in e["skills_preferred"] if s not in have],
        "unsupported_evidence": unsupported,
        "constraints": item["checks"],
        "unknown_inputs": unknowns,
        "availability": {"status": p["status"], "reason": e["status_reason"], "as_of": as_of, "fetched_at": p["fetched_at"]},
        "compensation": {"stated": p["compensation"], "state": e["compensation"]["state"],
                         "note": "source-stated, unverified" if p["compensation"] else "unknown; not inferred"},
        "provenance": {"source": p["source"], "source_id": p["source_id"], "url": p["url"], "synthetic": e["synthetic"],
                       "content_hash": e["content_hash"], "version": e.get("version", 1),
                       "duplicates_collapsed": [m for m in (e.get("dedup") or {}).get("members", []) if m != jid]},
        "cautions": sorted(set(e["quality_flags"]) | set(e["security_flags"])),
        "sensitivity": {"rank_best": spread["best"], "rank_worst": spread["worst"],
                        "method": "each weight ±20%, strategy weight ±0.1, one at a time"},
        "counterfactuals": counterfactuals,
    }


def legacy_profile_to_vault(doc: dict[str, Any], at: str) -> tuple[EvidenceVault, dict[str, Any]]:
    """Adapt ``fixtures/synthetic_profile.json`` (main) into a vault + preferences. Skills become self-reported."""
    vault = EvidenceVault(str(doc.get("id") or "candidate-unknown"))
    for skill in doc.get("skills", []):
        if isinstance(skill, str) and taxonomy.canonical_skill(skill):
            ev = vault.add_evidence("self-statement", "vault://legacy-profile/" + taxonomy.canonical_skill(skill), at)  # type: ignore[operator]
            vault.add_claim("skill", f"Skill: {taxonomy.canonical_skill(skill)}", at, skills=[skill], evidence_ids=[ev],
                            origin="legacy-profile")
    pref_in = doc.get("preferences") or {}
    prefs: dict[str, Any] = {}
    if isinstance(pref_in.get("remote"), (str, list)):
        prefs["remote"] = pref_in["remote"]
    if isinstance(pref_in.get("target_roles"), list):
        prefs["target_roles"] = [t for t in pref_in["target_roles"] if isinstance(t, str)]
    return vault, prefs
