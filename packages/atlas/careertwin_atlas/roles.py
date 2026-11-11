"""Role profiles: what a family of postings states, how sure we are, and whether it drifts.

A ``RoleProfile`` summarises the postings that share a ``role_family``. Its centroid lives in
the full feature space. Stability is measured by resampling the postings; a centroid built
from a handful of postings, or one that moves a lot under resampling, is flagged unstable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .errors import InvalidInputError
from .features import (STATE_STATED, STATE_UNKNOWN, FeatureMatrix, FeatureVector, parse_utc)

MIN_POSTINGS_FOR_STABLE = 5
INSTABILITY_RATIO = 0.25


@dataclass(frozen=True)
class RoleProfile:
    family: str
    label: str
    n_postings: int
    centroid: np.ndarray               # (d,) mean of imputed values
    prevalence: np.ndarray             # (d,) share of observed postings stating the feature
    observed_share: np.ndarray         # (d,) share of postings where the feature was readable
    min_qualification_share: np.ndarray  # (d,) share of postings naming it in requirements
    unmapped_requirement_share: float  # share of requirement lines we could not interpret
    compensation_coverage: float       # share of postings with any compensation figure
    compensation_mid_median: float | None
    bootstrap_radius: float            # mean centroid displacement under resampling
    feature_names: tuple[str, ...]

    def core_features(self, threshold: float = 0.5) -> list[str]:
        return [n for n, p in zip(self.feature_names, self.prevalence)
                if p >= threshold and not n.startswith("ctx:")]

    def as_vector(self, vocab_digest: str) -> FeatureVector:
        d = len(self.feature_names)
        return FeatureVector(
            entity_id=f"role:{self.family}", kind="role-centroid", label=self.label,
            values=self.centroid.copy(), states=np.full(d, STATE_STATED, dtype=np.int8),
            vocab_digest=vocab_digest, feature_names=self.feature_names,
            meta={"role_family": self.family, "n_postings": self.n_postings})


def build_role_profiles(matrix: FeatureMatrix, X_imputed: np.ndarray,
                        labels: Mapping[str, str] | None = None, seed: int = 0,
                        n_boot: int = 50) -> dict[str, RoleProfile]:
    """Group job vectors by ``meta.role_family`` and summarise each group."""
    labels = dict(labels or {})
    groups: dict[str, list[int]] = {}
    for i, v in enumerate(matrix.vectors):
        if v.kind != "job":
            continue
        fam = v.meta.get("role_family")
        if fam:
            groups.setdefault(fam, []).append(i)
    rng = np.random.default_rng(seed)
    out: dict[str, RoleProfile] = {}
    d = X_imputed.shape[1]
    for fam in sorted(groups):
        idx = np.array(groups[fam])
        Xg = X_imputed[idx]
        Sg = matrix.states[idx]
        observed = Sg != STATE_UNKNOWN
        obs_n = observed.sum(axis=0)
        stated = (Sg == STATE_STATED).sum(axis=0)
        prevalence = np.divide(stated, obs_n, out=np.zeros(d), where=obs_n > 0)
        minq = np.zeros(d)
        n_req = n_unmapped = 0
        comps = []
        name_index = {n: j for j, n in enumerate(matrix.feature_names)}
        for i in idx:
            meta = matrix.vectors[int(i)].meta
            for f in meta.get("min_qualifications", ()):
                minq[name_index[f]] += 1
            n_req += int(meta.get("n_requirements", 0))
            n_unmapped += int(meta.get("n_unmapped_requirements", 0))
            if meta.get("compensation_mid") is not None:
                comps.append(float(meta["compensation_mid"]))
        centroid = Xg.mean(axis=0)
        if len(idx) >= 2 and n_boot > 0:
            disp = []
            for _ in range(n_boot):
                sample = Xg[rng.integers(0, len(idx), size=len(idx))]
                disp.append(float(np.linalg.norm(sample.mean(axis=0) - centroid)))
            radius = float(np.mean(disp))
        else:
            radius = float("inf") if len(idx) < 2 else 0.0
        out[fam] = RoleProfile(
            family=fam, label=labels.get(fam, fam), n_postings=int(len(idx)), centroid=centroid,
            prevalence=prevalence, observed_share=obs_n / len(idx),
            min_qualification_share=minq / len(idx),
            unmapped_requirement_share=(n_unmapped / n_req) if n_req else 0.0,
            compensation_coverage=len(comps) / len(idx),
            compensation_mid_median=float(np.median(comps)) if comps else None,
            bootstrap_radius=radius, feature_names=matrix.feature_names)
    return out


def centroid_stability(profiles: Mapping[str, RoleProfile]) -> dict[str, dict[str, Any]]:
    """Flag centroids that rest on too few postings or wobble relative to their separation."""
    fams = sorted(profiles)
    out: dict[str, dict[str, Any]] = {}
    for fam in fams:
        p = profiles[fam]
        others = [float(np.linalg.norm(p.centroid - profiles[o].centroid)) for o in fams if o != fam]
        separation = min(others) if others else None
        reasons: list[str] = []
        if p.n_postings < MIN_POSTINGS_FOR_STABLE:
            reasons.append(f"only {p.n_postings} posting(s); fewer than {MIN_POSTINGS_FOR_STABLE}")
        ratio = None
        if separation is not None and separation > 1e-12 and np.isfinite(p.bootstrap_radius):
            ratio = p.bootstrap_radius / separation
            if ratio > INSTABILITY_RATIO:
                reasons.append(f"resampling moves the centroid by {ratio:.0%} of the distance to "
                               "the nearest other role")
        elif separation is not None and separation <= 1e-12:
            reasons.append("centroid coincides with another role's centroid")
        out[fam] = {
            "family": fam, "n_postings": p.n_postings,
            "bootstrap_radius": None if not np.isfinite(p.bootstrap_radius) else round(p.bootstrap_radius, 6),
            "nearest_centroid_distance": None if separation is None else round(separation, 6),
            "instability_ratio": None if ratio is None else round(float(ratio), 6),
            "unstable": bool(reasons), "reasons": reasons,
        }
    return out


def centroid_drift(early: Sequence[FeatureVector], late: Sequence[FeatureVector],
                   means: np.ndarray, seed: int = 0, n_perm: int = 1000, alpha: float = 0.01,
                   top: int = 5) -> dict[str, Any]:
    """Compare per-family centroids between two sets of postings (for example two time windows).

    For each family the observed centroid shift is compared with the shifts obtained when the
    window labels are shuffled (a permutation test of "the two windows state the same things").
    ``permutation_exceedance_share`` is the share of shuffles with a shift at least as large as
    the observed one; a family is flagged ``drifted`` when that share is at or below
    ``alpha / number_of_families_compared`` (Bonferroni). ``shift_to_noise`` is reported as an
    effect-size aid only. All of this describes posting text, not the labour market, and none
    of it is a hiring probability.

    Repair note (B01): the first version flagged drift when ``shift_to_noise > 3``. In a
    52-dimensional space the norm of pure resampling noise is already large, so a planted
    change of +0.64 in one feature scored 2.6 and was missed. The permutation test replaces it.
    """
    if not (0.0 < alpha < 1.0) or n_perm < 100:
        raise InvalidInputError("alpha must be within (0, 1) and n_perm >= 100")

    def group(vs: Sequence[FeatureVector]) -> dict[str, np.ndarray]:
        acc: dict[str, list[np.ndarray]] = {}
        for v in vs:
            fam = v.meta.get("role_family")
            if fam:
                acc.setdefault(fam, []).append(np.where(np.isnan(v.values), means, v.values))
        return {f: np.vstack(rows) for f, rows in acc.items()}

    ge, gl = group(early), group(late)
    rng = np.random.default_rng(seed)
    names = (early[0].feature_names if early else late[0].feature_names) if (early or late) else ()
    results: dict[str, Any] = {}
    comparable = [f for f in sorted(set(ge) & set(gl))
                  if len(ge[f]) >= MIN_POSTINGS_FOR_STABLE and len(gl[f]) >= MIN_POSTINGS_FOR_STABLE]
    threshold = alpha / max(1, len(comparable))
    for fam in sorted(set(ge) | set(gl)):
        if fam not in comparable:
            results[fam] = {"status": "not-comparable", "n_early": int(len(ge.get(fam, []))),
                            "n_late": int(len(gl.get(fam, []))),
                            "reason": f"family needs at least {MIN_POSTINGS_FOR_STABLE} postings in both windows"}
            continue
        A, B = ge[fam], gl[fam]
        na = len(A)
        pooled = np.vstack([A, B])
        delta = B.mean(axis=0) - A.mean(axis=0)
        shift = float(np.linalg.norm(delta))
        total = pooled.sum(axis=0)
        null = np.empty(n_perm)
        for t in range(n_perm):
            pick = rng.permutation(len(pooled))[:na]
            sa = pooled[pick].sum(axis=0)
            null[t] = np.linalg.norm((total - sa) / (len(pooled) - na) - sa / na)
        exceed = float((1 + int((null >= shift - 1e-12).sum())) / (n_perm + 1))
        noise = float(null.mean())
        order = np.argsort(-np.abs(delta), kind="stable")[:top]
        results[fam] = {
            "status": "compared", "n_early": int(na), "n_late": int(len(B)),
            "centroid_shift": round(shift, 6), "shuffled_shift_mean": round(noise, 6),
            "shift_to_noise": round(shift / noise, 4) if noise > 1e-12 else None,
            "permutation_exceedance_share": round(exceed, 6),
            "drifted": bool(exceed <= threshold),
            "top_feature_changes": [{"feature": names[i], "change": round(float(delta[i]), 4)}
                                    for i in order if abs(delta[i]) > 1e-9],
        }
    return {"families": results, "alpha": alpha, "families_compared": len(comparable),
            "bonferroni_threshold": round(threshold, 6), "n_perm": n_perm,
            "note": "Permutation test on posting text per family, Bonferroni-adjusted across families. "
                    "A statistical statement about synthetic postings; not a hiring probability."}


def split_by_time(vectors: Sequence[FeatureVector], cutoff: str) -> tuple[list[FeatureVector], list[FeatureVector]]:
    """Split job vectors into (before cutoff, on/after cutoff) by ``fetched_at``. Undated are dropped."""
    cut = parse_utc(cutoff)
    if cut is None:
        raise InvalidInputError("cutoff must be an ISO-8601 timestamp with a UTC offset")
    early, late = [], []
    for v in vectors:
        ts = parse_utc(v.meta.get("fetched_at"))
        if ts is None:
            continue
        (early if ts < cut else late).append(v)
    return early, late
