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
MAX_PERM = 20_000
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
    compensation_units: tuple[str, ...] = ()   # distinct "CURRENCY/basis" units behind the median
    #: Mean over OBSERVED cells only (0 where the role never states anything readable). Used for the
    #: one-sided requirement shortfall so that mean-imputed guesses never count as requirements.
    requirement_profile: np.ndarray | None = None
    unknown_cell_share: float = 0.0            # share of skill cells in this role that were UNKNOWN

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
        units: set[str] = set()
        name_index = {n: j for j, n in enumerate(matrix.feature_names)}
        for i in idx:
            meta = matrix.vectors[int(i)].meta
            for f in meta.get("min_qualifications", ()):
                minq[name_index[f]] += 1
            n_req += int(meta.get("n_requirements", 0))
            n_unmapped += int(meta.get("n_unmapped_requirements", 0))
            if meta.get("compensation_mid") is not None:
                comps.append(float(meta["compensation_mid"]))
                units.add(str(meta.get("compensation_currency") or "unknown-unit"))
        centroid = Xg.mean(axis=0)
        # Repair note (review cycle 2, finding 1): the shortfall used `centroid`, which contains the
        # global mean for UNKNOWN cells, so a role whose postings listed no skills appeared to "require"
        # python at 0.5. The requirement profile averages observed cells only.
        raw = matrix.X[idx]
        req_profile = np.divide(np.where(observed, raw, 0.0).sum(axis=0), obs_n,
                                out=np.zeros(d), where=obs_n > 0)
        skill_cols = np.array([not n.startswith("ctx:") for n in matrix.feature_names])
        unknown_share = float(1.0 - observed[:, skill_cols].mean()) if skill_cols.any() else 0.0
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
            # A median across different currencies or bases is meaningless (review cycle 1, F8).
            compensation_mid_median=float(np.median(comps)) if comps and len(units) == 1 else None,
            compensation_units=tuple(sorted(units)),
            requirement_profile=req_profile, unknown_cell_share=round(unknown_share, 6),
            bootstrap_radius=radius, feature_names=matrix.feature_names)
    return out


def centroid_stability(profiles: Mapping[str, RoleProfile]) -> dict[str, dict[str, Any]]:
    """Flag centroids that rest on too few postings or wobble relative to their separation."""
    fams = sorted(profiles)
    out: dict[str, dict[str, Any]] = {}
    seps: dict[str, float | None] = {f: None for f in fams}
    if len(fams) > 1:
        # vectorised nearest-centroid distance (review cycle 2, finding 9: the pairwise Python loop was
        # quadratic in the number of posting-supplied families)
        C = np.vstack([profiles[f].centroid for f in fams])
        sq = (C * C).sum(axis=1)
        D2 = np.clip(sq[:, None] + sq[None, :] - 2.0 * (C @ C.T), 0.0, None)
        np.fill_diagonal(D2, np.inf)
        nearest = np.sqrt(D2.min(axis=1))
        seps = {f: float(v) for f, v in zip(fams, nearest)}
    for fam in fams:
        p = profiles[fam]
        separation = seps[fam]
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
    # Repair note (review cycle 1, F5): the smallest attainable exceedance share is 1/(n_perm+1).
    # With 1000 shuffles and 11+ families that floor sat above the threshold, so nothing could ever
    # be flagged. The shuffle count is raised until the floor is at most half the threshold.
    needed = int(np.ceil(2.0 / threshold))
    n_perm = min(max(int(n_perm), needed), MAX_PERM)
    underpowered = 1.0 / (n_perm + 1) > threshold
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
        nb = len(pooled) - na
        batch = max(1, min(n_perm, 4_000_000 // max(1, na * pooled.shape[1])))
        for start in range(0, n_perm, batch):
            stop = min(start + batch, n_perm)
            keys = rng.random((stop - start, len(pooled)))
            pick = np.argpartition(keys, na - 1, axis=1)[:, :na]     # a uniformly random subset of size na
            sa = pooled[pick].sum(axis=1)
            null[start:stop] = np.linalg.norm((total - sa) / nb - sa / na, axis=1)
        exceed = float((1 + int((null >= shift - 1e-12).sum())) / (n_perm + 1))
        noise = float(null.mean())
        order = np.argsort(-np.abs(delta), kind="stable")[:top]
        results[fam] = {
            "status": "compared", "n_early": int(na), "n_late": int(len(B)),
            "centroid_shift": round(shift, 6), "shuffled_shift_mean": round(noise, 6),
            "shift_to_noise": round(shift / noise, 4) if noise > 1e-12 else None,
            "permutation_exceedance_share": round(exceed, 6),
            "drifted": bool(exceed <= threshold),
            "underpowered": bool(underpowered),
            "top_feature_changes": [{"feature": names[i], "change": round(float(delta[i]), 4)}
                                    for i in order if abs(delta[i]) > 1e-9],
        }
    return {"families": results, "alpha": alpha, "families_compared": len(comparable),
            "underpowered": bool(underpowered),
            "underpowered_note": (f"permutation budget capped at {MAX_PERM}; with this many families the "
                                  "Bonferroni threshold is not reachable, so 'not drifted' is not evidence of "
                                  "stability") if underpowered else None,
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
