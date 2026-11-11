"""Input pipeline: contract objects -> transparent feature vectors.

Design rules (workstreams 1 and 3 of Issue #4):

* Every feature value carries a *state*. ``NOT_STATED`` means "the source says nothing about
  this", which is different from ``CONTRADICTED`` ("the source says no") and from ``UNKNOWN``
  ("we could not read this part of the source at all"). Absence is never treated as disproof.
* Every stated feature keeps provenance (which field of which object produced it).
* Out-of-vocabulary terms are preserved in ``unmapped_terms``; they are never silently dropped.
* Free text is untrusted. It is only ever matched against the controlled vocabulary.
* Candidate provenance contains claim IDs only, never claim text.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from .errors import FeatureSpaceMismatchError, InvalidInputError
from .vocabulary import SENIORITY_FEATURE, SENIORITY_LEVELS, Vocabulary, normalize_token

STATE_UNKNOWN = 0
STATE_NOT_STATED = 1
STATE_STATED = 2
STATE_CONTRADICTED = 3
STATE_NAMES = {STATE_UNKNOWN: "unknown", STATE_NOT_STATED: "not-stated",
               STATE_STATED: "stated", STATE_CONTRADICTED: "contradicted"}

#: Evidence weight per ``ProfileClaim.status``. Declared assumption, overridable per call.
DEFAULT_EVIDENCE_WEIGHTS: dict[str, float] = {
    "supported": 1.0, "self-reported": 0.6, "unverified": 0.3, "contradicted": 0.0,
}

MAX_LIST_ITEMS = 200
MAX_TEXT_CHARS = 2000
_LABEL_MAX = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_TEXT_TOKEN = re.compile(r"[a-z0-9+#.\-]+")
#: Single words that are also ordinary English; never matched inside free-text requirements.
_FREE_TEXT_AMBIGUOUS = frozenset({
    "go", "rest", "cloud", "agents", "hiring", "testing", "security", "privacy", "monitoring",
    "frontend", "mentoring", "ml", "llm", "llms", "evals", "java",
})


def sanitize_label(raw: object, fallback: str = "unlabelled") -> str:
    """Strip control/bidi characters and cap length. Consumers must still escape for HTML/CSV."""
    if not isinstance(raw, str):
        return fallback
    text = _CONTROL.sub(" ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:_LABEL_MAX]


def parse_utc(ts: object) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware UTC datetime; ``None`` if unusable."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class FeatureVector:
    entity_id: str
    kind: str                      # candidate | job | role-centroid | scenario
    label: str
    values: np.ndarray             # float64; NaN where state is UNKNOWN
    states: np.ndarray             # int8, one of STATE_*
    vocab_digest: str
    feature_names: tuple[str, ...]
    provenance: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unmapped_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.values.shape != (len(self.feature_names),) or self.states.shape != self.values.shape:
            raise InvalidInputError("values/states must be 1-D and match the feature space")
        unknown = self.states == STATE_UNKNOWN
        if not np.array_equal(np.isnan(self.values), unknown):
            raise InvalidInputError("NaN values must coincide exactly with UNKNOWN states")
        finite = self.values[~unknown]
        if finite.size and not np.all(np.isfinite(finite)):
            raise InvalidInputError("feature values must be finite")

    @property
    def vector_ref(self) -> str:
        """Content-addressed reference used as ``AtlasPoint.source_vector_ref``."""
        payload = {
            "entity_id": self.entity_id, "kind": self.kind, "vocab": self.vocab_digest,
            "values": [None if np.isnan(v) else round(float(v), 6) for v in self.values],
            "states": [int(s) for s in self.states],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "vec:sha256:" + hashlib.sha256(blob).hexdigest()[:24]

    def stated_features(self) -> list[str]:
        return [n for n, s in zip(self.feature_names, self.states) if s == STATE_STATED]

    def value_of(self, name: str) -> float:
        return float(self.values[self.feature_names.index(name)])

    def state_of(self, name: str) -> int:
        return int(self.states[self.feature_names.index(name)])


def _blank(vocab: Vocabulary) -> tuple[np.ndarray, np.ndarray]:
    d = len(vocab.features)
    return np.zeros(d, dtype=np.float64), np.full(d, STATE_NOT_STATED, dtype=np.int8)


def match_free_text(text: str, vocab: Vocabulary) -> list[str]:
    """Return vocabulary features mentioned in a free-text requirement (longest match first).

    Conservative on purpose: ambiguous everyday words are ignored, so the function prefers
    missing a mention (reported as an unmapped requirement) over inventing one.
    """
    tokens = _TEXT_TOKEN.findall(text[:MAX_TEXT_CHARS].lower())
    found: list[str] = []
    i = 0
    while i < len(tokens):
        hit = None
        for n in (4, 3, 2, 1):
            if i + n > len(tokens):
                continue
            phrase = "-".join(tokens[i:i + n]).strip("-.")
            if n == 1 and phrase in _FREE_TEXT_AMBIGUOUS:
                continue
            feature, how = vocab.resolve(phrase)
            if feature is not None and not (n == 1 and how == "lossy-alias"):
                hit = (feature, n)
                break
        if hit:
            if hit[0] not in found:
                found.append(hit[0])
            i += hit[1]
        else:
            i += 1
    return found


def job_to_vector(job: Mapping[str, Any], vocab: Vocabulary) -> FeatureVector:
    """Map one ``JobPosting`` (contract v0.1) to a feature vector with states and provenance."""
    if not isinstance(job, Mapping):
        raise InvalidInputError("job must be a mapping")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise InvalidInputError("job.id must be a non-empty string")
    values, states = _blank(vocab)
    index = vocab.index
    prov: dict[str, list[str]] = {}
    unmapped: list[str] = []
    warnings: list[str] = []
    lossy: list[str] = []

    skills = job.get("skills")
    skill_idx = [index[n] for n in vocab.skill_names]
    if not isinstance(skills, Sequence) or isinstance(skills, (str, bytes)):
        warnings.append("skills-field-missing-or-invalid: skill features are UNKNOWN")
        skills = None
    elif len(skills) == 0:
        warnings.append("skills-field-empty: skill features are UNKNOWN, not 'requires nothing'")
        skills = None
    if skills is None:
        values[skill_idx] = np.nan
        states[skill_idx] = STATE_UNKNOWN
    else:
        if len(skills) > MAX_LIST_ITEMS:
            warnings.append(f"skills-truncated: only first {MAX_LIST_ITEMS} of {len(skills)} read")
        for pos, raw in enumerate(list(skills)[:MAX_LIST_ITEMS]):
            feature, how = vocab.resolve(raw)
            if feature is None:
                token = normalize_token(raw)
                unmapped.append(token if token is not None else "<invalid-term>")
                continue
            i = index[feature]
            values[i], states[i] = 1.0, STATE_STATED
            prov.setdefault(feature, []).append(f"job:{job_id}#skills[{pos}]:{how}")
            if how == "lossy-alias":
                lossy.append(feature)

    min_quals: list[str] = []
    unmapped_reqs = 0
    reqs = job.get("requirements")
    n_reqs = 0
    if isinstance(reqs, Sequence) and not isinstance(reqs, (str, bytes)):
        for pos, text in enumerate(list(reqs)[:MAX_LIST_ITEMS]):
            if not isinstance(text, str) or not text.strip():
                continue
            n_reqs += 1
            hits = match_free_text(text, vocab)
            if not hits:
                unmapped_reqs += 1
                continue
            for feature in hits:
                i = index[feature]
                values[i], states[i] = 1.0, STATE_STATED
                prov.setdefault(feature, []).append(f"job:{job_id}#requirements[{pos}]")
                if feature not in min_quals:
                    min_quals.append(feature)

    si = index.get(SENIORITY_FEATURE)
    if si is not None:
        token = normalize_token(job.get("seniority"))
        level = SENIORITY_LEVELS.get(token) if token else None
        if level is None:
            values[si], states[si] = np.nan, STATE_UNKNOWN
            warnings.append("seniority-unknown: not mapped to an ordinal level")
        else:
            values[si], states[si] = level, STATE_STATED
            prov.setdefault(SENIORITY_FEATURE, []).append(f"job:{job_id}#seniority")

    if unmapped:
        warnings.append(f"unmapped-skill-terms: {len(unmapped)} preserved, not used in vector")
    if lossy:
        warnings.append("lossy-alias-used: " + ",".join(sorted(set(lossy))))
    family = normalize_token(job.get("role_family"))
    comp = job.get("compensation")
    comp_mid = None
    if isinstance(comp, Mapping):
        lo, hi = comp.get("min"), comp.get("max")
        nums = [float(x) for x in (lo, hi) if isinstance(x, (int, float)) and not isinstance(x, bool)
                and np.isfinite(x)]
        if nums:
            comp_mid = sum(nums) / len(nums)
    evidence = job.get("source_evidence")
    meta = {
        "role_family": family,
        "fetched_at": job.get("fetched_at") if parse_utc(job.get("fetched_at")) else None,
        "status": job.get("status") if job.get("status") in ("active", "expired", "unknown") else "unknown",
        "source": job.get("source") if isinstance(job.get("source"), str) else "unknown",
        "min_qualifications": tuple(min_quals),
        "n_requirements": n_reqs,
        "n_unmapped_requirements": unmapped_reqs,
        "compensation_mid": comp_mid,
        "compensation_currency": comp.get("currency") if isinstance(comp, Mapping) else None,
        "source_evidence": tuple(e for e in evidence if isinstance(e, str))[:20]
        if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)) else (),
    }
    return FeatureVector(
        entity_id=job_id, kind="job", label=sanitize_label(job.get("title"), job_id),
        values=values, states=states, vocab_digest=vocab.digest(),
        feature_names=vocab.feature_names,
        provenance={k: tuple(v) for k, v in prov.items()},
        unmapped_terms=tuple(unmapped), warnings=tuple(warnings), meta=meta,
    )


def claims_to_vector(
    claims: Sequence[Mapping[str, Any]],
    vocab: Vocabulary,
    candidate_ref: str,
    as_of: str | None = None,
    evidence_weights: Mapping[str, float] | None = None,
    seniority: str | None = None,
) -> FeatureVector:
    """Map ``ProfileClaim`` objects (contract v0.1) to a candidate evidence vector.

    A feature with no claim is ``NOT_STATED`` ("no evidence on file"), which downstream gap
    analysis reports as *missing evidence*, never as *lacking the skill*.
    """
    if not isinstance(candidate_ref, str) or not candidate_ref:
        raise InvalidInputError("candidate_ref must be a non-empty string")
    if isinstance(claims, (str, bytes)) or not isinstance(claims, Sequence):
        raise InvalidInputError("claims must be a sequence of ProfileClaim mappings")
    weights = dict(DEFAULT_EVIDENCE_WEIGHTS)
    if evidence_weights:
        weights.update(evidence_weights)
    for k, w in weights.items():
        if not (isinstance(w, (int, float)) and 0.0 <= float(w) <= 1.0):
            raise InvalidInputError(f"evidence weight for {k!r} must be within [0, 1]")
    now = parse_utc(as_of) if as_of else None
    if as_of and now is None:
        raise InvalidInputError("as_of must be an ISO-8601 timestamp with a UTC offset")

    values, states = _blank(vocab)
    index = vocab.index
    prov: dict[str, list[str]] = {}
    unmapped: list[str] = []
    warnings: list[str] = []
    positive: dict[str, float] = {}
    contradicted: set[str] = set()

    for claim in list(claims)[:5000]:
        if not isinstance(claim, Mapping):
            warnings.append("claim-skipped: not a mapping")
            continue
        cid = claim.get("id") if isinstance(claim.get("id"), str) else "<no-id>"
        if claim.get("subject_id") not in (None, candidate_ref):
            warnings.append(f"claim-skipped:{cid}: subject_id does not match candidate_ref")
            continue
        status = claim.get("status")
        if status not in weights:
            warnings.append(f"claim-skipped:{cid}: unknown status")
            continue
        terms = claim.get("skills")
        if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes)) or not terms:
            terms = [claim.get("claim_text")] if claim.get("claim_type") == "skill" else []
        expired = False
        valid_to = parse_utc(claim.get("valid_to"))
        if now is not None and valid_to is not None and valid_to < now:
            expired = True
            warnings.append(f"claim-expired:{cid}: downgraded to unverified weight")
        for raw in list(terms)[:MAX_LIST_ITEMS]:
            feature, how = vocab.resolve(raw)
            if feature is None:
                token = normalize_token(raw)
                unmapped.append(token if token is not None else "<invalid-term>")
                continue
            prov.setdefault(feature, []).append(f"claim:{cid}:{status}{':expired' if expired else ''}")
            if status == "contradicted":
                contradicted.add(feature)
            else:
                w = min(weights[status], weights["unverified"]) if expired else weights[status]
                positive[feature] = max(positive.get(feature, 0.0), float(w))

    for feature in sorted(set(positive) | contradicted):
        i = index[feature]
        if feature in contradicted and feature in positive:
            values[i] = min(positive[feature], weights["unverified"])
            states[i] = STATE_STATED
            warnings.append(f"claim-conflict:{feature}: supported and contradicted; held at unverified weight")
        elif feature in contradicted:
            values[i], states[i] = 0.0, STATE_CONTRADICTED
        else:
            values[i], states[i] = positive[feature], STATE_STATED

    si = index.get(SENIORITY_FEATURE)
    if si is not None:
        token = normalize_token(seniority) if seniority else None
        level = SENIORITY_LEVELS.get(token) if token else None
        if level is None:
            values[si], states[si] = np.nan, STATE_UNKNOWN
            warnings.append("candidate-seniority-unknown: imputed at projection time, flagged")
        else:
            values[si], states[si] = level, STATE_STATED
    if unmapped:
        warnings.append(f"unmapped-claim-terms: {len(unmapped)} preserved, not used in vector")
    return FeatureVector(
        entity_id=candidate_ref, kind="candidate", label="Candidate (evidence on file)",
        values=values, states=states, vocab_digest=vocab.digest(),
        feature_names=vocab.feature_names,
        provenance={k: tuple(v) for k, v in prov.items()},
        unmapped_terms=tuple(unmapped), warnings=tuple(warnings), meta={"as_of": as_of},
    )


def profile_fixture_to_claims(profile: Mapping[str, Any],
                              updated_at: str = "2026-10-31T00:00:00Z") -> list[dict[str, Any]]:
    """Adapt the legacy ``fixtures/synthetic_profile.json`` shape to ProfileClaim objects.

    Every skill in that fixture is an unevidenced list entry, so each becomes a
    ``self-reported`` claim. Nothing is upgraded to ``supported``.
    """
    subject = profile.get("id")
    if not isinstance(subject, str) or not subject:
        raise InvalidInputError("profile.id must be a non-empty string")
    out = []
    for n, skill in enumerate(profile.get("skills") or []):
        out.append({
            "id": f"{subject}-claim-{n:03d}", "subject_id": subject, "claim_type": "skill",
            "claim_text": str(skill), "status": "self-reported", "evidence_ids": [],
            "visibility": "private", "updated_at": updated_at, "skills": [str(skill)],
        })
    return out


@dataclass(frozen=True)
class FeatureMatrix:
    X: np.ndarray                  # (n, d) float64 with NaN for UNKNOWN
    states: np.ndarray             # (n, d) int8
    vectors: tuple[FeatureVector, ...]
    feature_names: tuple[str, ...]
    vocab_digest: str

    @property
    def ids(self) -> list[str]:
        return [v.entity_id for v in self.vectors]

    def __len__(self) -> int:
        return len(self.vectors)


def build_matrix(vectors: Sequence[FeatureVector]) -> FeatureMatrix:
    if not vectors:
        raise InvalidInputError("need at least one feature vector")
    first = vectors[0]
    for v in vectors:
        if v.vocab_digest != first.vocab_digest or v.feature_names != first.feature_names:
            raise FeatureSpaceMismatchError(
                f"vector {v.entity_id!r} was built with vocabulary {v.vocab_digest}, "
                f"expected {first.vocab_digest}")
    X = np.vstack([v.values for v in vectors])
    S = np.vstack([v.states for v in vectors])
    return FeatureMatrix(X, S, tuple(vectors), first.feature_names, first.vocab_digest)


def column_means(X: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Per-column mean over observed (non-NaN) entries. Columns with no data get 0.0."""
    observed = ~np.isnan(X)
    counts = observed.sum(axis=0)
    sums = np.where(observed, X, 0.0).sum(axis=0)
    means = np.divide(sums, counts, out=np.zeros(X.shape[1]), where=counts > 0)
    return means, [int(i) for i in np.flatnonzero(counts == 0)]


def impute(X: np.ndarray, means: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Replace NaN with the supplied column means. Returns (filled, per-row imputed count).

    Mean imputation makes an unknown feature contribute nothing after centering. It also
    understates variance, which is why callers must surface the imputed counts.
    """
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2 or X.shape[1] != means.shape[0]:
        raise FeatureSpaceMismatchError(
            f"matrix has {X.shape[-1] if X.ndim else 0} columns, model expects {means.shape[0]}")
    mask = np.isnan(X)
    if np.isinf(X).any():
        raise InvalidInputError("feature matrix contains infinite values")
    return np.where(mask, means[None, :], X), mask.sum(axis=1)
