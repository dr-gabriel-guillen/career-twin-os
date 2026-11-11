"""Adapters for lane A's published outputs (workstream 3 and 11).

Two routes into the atlas, both offline and both read-only with respect to lane A:

1. ``unwrap_job_records`` — accepts frozen ``JobPosting`` objects *or* lane A's
   ``{"posting": <JobPosting>, "ext": {...}}`` envelope and returns plain postings for
   ``AtlasEngine.fit``. This route depends only on the frozen contract. Skills are mapped
   through lane C's vocabulary; anything it cannot map is preserved as an unmapped term.

2. ``engine_from_skill_vectors`` — consumes lane A's ``skill_vectors`` payload
   (``api_version = "a-api/0.1"``) and fits the atlas directly in **lane A's feature space**.
   Lane A's ``source_vector_ref`` values are preserved on every emitted AtlasPoint, and
   ``observed = false`` rows become UNKNOWN (never zeros).

The second route depends on a payload that is not a frozen contract. The adapter therefore
pins the API version and fails loudly on anything else.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .engine import AtlasEngine
from .errors import InvalidInputError
from .features import (STATE_CONTRADICTED, STATE_NOT_STATED, STATE_STATED, STATE_UNKNOWN, FeatureVector,
                       normalize_family,
                       parse_utc, sanitize_label)
from .vocabulary import normalize_token
from .vocabulary import ACQ_STUDY, FeatureDef, Vocabulary, default_vocabulary

A_API_VERSION = "a-api/0.1"
EXTERNAL_EFFORT_HOURS = (60.0, 200.0)
A_CANDIDATE_STATES = frozenset({"supported", "self-reported", "unverified", "contradicted", "expired", "none"})
MAX_VECTOR_ROWS = 100_000


def unwrap_job_records(items: Sequence[Any]) -> list[Any]:
    """Return postings from a mixed list of JobPosting objects and ``{posting, ext}`` envelopes.

    Items that are neither are passed through unchanged so that ``AtlasEngine.fit`` reports
    them as skipped with a reason instead of this function hiding them.
    """
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise InvalidInputError("job records must be a sequence")
    out = []
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get("posting"), Mapping) and "id" not in item:
            out.append(dict(item["posting"]))
        else:
            out.append(item)
    return out


def vocabulary_from_names(names: Sequence[Any], version: str,
                          strict: bool = True) -> tuple[Vocabulary, list[str]]:
    """Build a feature space from an upstream vocabulary list.

    Names lane C already knows (directly or through an alias) inherit that feature's group,
    acquisition mode and effort placeholder. Unknown names get the group ``external`` and a
    default placeholder, and are listed in the returned notes so nobody mistakes the
    placeholder for knowledge.
    """
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence) or not names:
        raise InvalidInputError("vocabulary must be a non-empty list of feature names")
    if not isinstance(version, str) or not version.strip():
        raise InvalidInputError("vocabulary_version must be a non-empty string")
    base = default_vocabulary()
    feats: list[FeatureDef] = []
    seen: set[str] = set()
    external: list[str] = []
    for raw in names:
        token = normalize_token(raw)
        if token is None or (strict and token != raw):
            raise InvalidInputError(f"vocabulary entry {raw!r} is not a normalised feature id")
        if token in seen:
            raise InvalidInputError(f"vocabulary entry {token!r} is duplicated")
        seen.add(token)
        known, _ = base.resolve(token)
        if known is not None:
            ref = base.get(known)
            feats.append(FeatureDef(token, ref.group, ref.acquisition, ref.effort_hours))
        else:
            external.append(token)
            feats.append(FeatureDef(token, "external", ACQ_STUDY, EXTERNAL_EFFORT_HOURS))
    notes = []
    renamed = [f"{r}->{normalize_token(r)}" for r in names if isinstance(r, str) and normalize_token(r) != r]
    if renamed:
        notes.append("upstream feature ids normalised: " + ", ".join(renamed))
    if external:
        notes.append(f"{len(external)} upstream feature(s) are unknown to lane C and use the default effort "
                     f"placeholder and 'study-or-project' acquisition: {', '.join(external)}")
    return Vocabulary(f"upstream:{version.strip()[:80]}", tuple(feats), {}, frozenset()), notes


def _row_to_vector(row: Any, vocab: Vocabulary, kind: str, where: str,
                   postings: Mapping[str, Mapping[str, Any]] | None = None,
                   payload_synthetic: bool | None = None) -> FeatureVector:
    if not isinstance(row, Mapping):
        raise InvalidInputError(f"{where}: row must be an object")
    entity_id = row.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise InvalidInputError(f"{where}: entity_id must be a non-empty string")
    values = row.get("values")
    d = len(vocab.features)
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or len(values) != d:
        raise InvalidInputError(f"{where}: values must be a list of {d} numbers")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in values):
        raise InvalidInputError(f"{where}: values must be numbers")
    arr = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(arr)) or arr.min() < 0.0 or arr.max() > 1.0:
        raise InvalidInputError(f"{where}: values must be finite and within [0, 1]")
    observed = row.get("observed")
    if not isinstance(observed, bool):
        raise InvalidInputError(f"{where}: observed must be true or false")
    warnings: list[str] = []
    upstream_states = row.get("states") if kind == "candidate" else None
    if upstream_states is not None:
        # Lane A (a-api/0.1, added at A 3994155): one entry per vocabulary id. Authoritative for the
        # candidate: "none" = no evidence on file, "contradicted" stays contradicted, "expired" carries
        # weight 0 upstream and is treated here as no current evidence (noted).
        if (isinstance(upstream_states, (str, bytes)) or not isinstance(upstream_states, Sequence)
                or len(upstream_states) != d or any(x not in A_CANDIDATE_STATES for x in upstream_states)):
            raise InvalidInputError(f"{where}: states must list {d} entries from {sorted(A_CANDIDATE_STATES)}")
        if observed != bool((arr > 0).any()):
            # Repair note (review cycle 2, finding 8): states used to bypass the observed=false guard.
            raise InvalidInputError(f"{where}: observed must equal 'any value > 0' when states are supplied")
        zero_states = {"none", "contradicted", "expired"}
        if any((v == 0) != (st in zero_states) for v, st in zip(arr, upstream_states)):
            raise InvalidInputError(f"{where}: values and states disagree (values[i] == 0 must hold iff "
                                    "states[i] is none, contradicted or expired)")
        states = np.array([STATE_CONTRADICTED if st == "contradicted" else
                           STATE_NOT_STATED if st in ("none", "expired") else STATE_STATED
                           for st in upstream_states], dtype=np.int8)
        n_expired = sum(st == "expired" for st in upstream_states)
        if n_expired:
            warnings.append(f"upstream-expired: {n_expired} feature(s) have only expired evidence; treated as "
                            "no current evidence")
    elif observed:
        states = np.where(arr > 0, STATE_STATED, STATE_NOT_STATED).astype(np.int8)
    else:
        if arr.any():
            raise InvalidInputError(f"{where}: observed is false but non-zero values are present")
        arr = np.full(d, np.nan)
        states = np.full(d, STATE_UNKNOWN, dtype=np.int8)
        warnings.append("upstream-observed-false: every feature is UNKNOWN, not absent")
    ref = row.get("source_vector_ref")
    ref = ref.strip() if isinstance(ref, str) and ref.strip() else None
    prov = {name: (f"{ref or 'upstream'}#{name}",)
            for name, st in zip(vocab.feature_names, states) if st == STATE_STATED}
    family = normalize_family(row.get("role_family")) if kind == "job" else None
    record = (postings or {}).get(entity_id.strip())
    fetched = row.get("fetched_at") if parse_utc(row.get("fetched_at")) else None
    if fetched is None and record and parse_utc(record.get("fetched_at")):
        fetched = record.get("fetched_at")
    # Repair note (review cycle 2, finding 6): any single "synthetic" signal used to win (OR). Every signal
    # present must now agree (AND); a conflict is reported and the row counts as not verified.
    signals = [b for b in (payload_synthetic, row.get("synthetic") if isinstance(row.get("synthetic"), bool) else None,
                           record.get("_synthetic") if record else None) if isinstance(b, bool)]
    synthetic = bool(signals) and all(signals)
    if signals and not all(signals) and any(signals):
        warnings.append("provenance-conflict: synthetic markers disagree between payload, row and record; "
                        "treated as not verified")
    meta = {
        "role_family": family, "fetched_at": fetched,
        "status": row.get("status") if row.get("status") in ("active", "expired", "unknown") else "unknown",
        "source": "synthetic" if synthetic else "lane-a-skill-vectors",
        "min_qualifications": (), "n_requirements": 0,
        "n_unmapped_requirements": 0, "compensation_mid": None, "compensation_currency": None,
        "source_evidence": (),
    }
    if ref:
        meta["source_vector_ref"] = ref
    if kind == "job" and isinstance(row.get("seniority"), str):
        meta["seniority_raw"] = sanitize_label(row.get("seniority"), "")[:40]
    label = sanitize_label(row.get("label"), entity_id) if kind == "job" else "Candidate (evidence on file)"
    return FeatureVector(entity_id=entity_id.strip(), kind=kind, label=label, values=arr, states=states,
                         vocab_digest=vocab.digest(), feature_names=vocab.feature_names, provenance=prov,
                         warnings=tuple(warnings), meta=meta)


def engine_from_skill_vectors(payload: Any, standardize: bool = False, n_components: int = 2,
                              seed: int = 0, n_boot: int = 50,
                              records: Sequence[Any] | None = None) -> dict[str, Any]:
    """Fit an atlas from lane A's ``skill_vectors`` payload.

    Returns ``{"engine", "candidate", "notes"}``. ``candidate`` is ``None`` when the payload
    has no candidate row. ``notes`` lists everything this route cannot know (see below);
    callers should surface them next to the map.

    ``records`` (optional) are lane A JobRecords or JobPostings for the same postings. They are
    joined on ``entity_id == posting.id`` to recover capture time (enables freshness and drift
    checks) and the ``ext.synthetic`` marker. Nothing else is taken from them.
    """
    if not isinstance(payload, Mapping):
        raise InvalidInputError("skill-vector payload must be an object")
    postings: dict[str, dict[str, Any]] = {}
    if records is not None:
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise InvalidInputError("records must be a sequence")
        for item in records:
            if not isinstance(item, Mapping):
                continue
            posting = item.get("posting") if isinstance(item.get("posting"), Mapping) else item
            pid = posting.get("id")
            if isinstance(pid, str) and pid:
                ext = item.get("ext") if isinstance(item.get("ext"), Mapping) else {}
                postings[pid] = {"fetched_at": posting.get("fetched_at"),
                                 "_synthetic": posting.get("source") == "synthetic" or ext.get("synthetic") is True}
    payload_synthetic = payload.get("synthetic") if isinstance(payload.get("synthetic"), bool) else None
    version = payload.get("api_version")
    if version != A_API_VERSION:
        raise InvalidInputError(f"unsupported api_version {version!r}; this adapter is pinned to {A_API_VERSION!r}")
    vocab, notes = vocabulary_from_names(payload.get("vocabulary"), payload.get("vocabulary_version") or "")
    rows = payload.get("jobs")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise InvalidInputError("payload.jobs must be a list")
    if len(rows) > MAX_VECTOR_ROWS:
        raise InvalidInputError(f"payload.jobs has more than {MAX_VECTOR_ROWS} rows")
    vectors: list[FeatureVector] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for pos, row in enumerate(rows):
        try:
            v = _row_to_vector(row, vocab, "job", f"jobs[{pos}]", postings, payload_synthetic)
        except InvalidInputError as exc:
            skipped.append({"position": str(pos), "reason": str(exc)})
            continue
        if v.entity_id in seen:
            skipped.append({"position": str(pos), "reason": f"duplicate id {v.entity_id!r}; first kept"})
            continue
        row_version = row.get("vocabulary_version")
        if row_version is not None and row_version != payload.get("vocabulary_version"):
            skipped.append({"position": str(pos), "reason": "row vocabulary_version differs from payload"})
            continue
        seen.add(v.entity_id)
        vectors.append(v)
    engine = AtlasEngine.from_vectors(vectors, vocab, standardize=standardize, n_components=n_components,
                                      seed=seed, n_boot=n_boot, skipped=skipped)
    candidate = None
    if payload.get("candidate") is not None:
        candidate = _row_to_vector(payload["candidate"], vocab, "candidate", "candidate")
    joined = sum(1 for v in vectors if v.meta.get("fetched_at"))
    has_states = isinstance(payload.get("candidate"), Mapping) and payload["candidate"].get("states") is not None
    notes.extend([
        "candidate states supplied by lane A: contradicted claims are kept as contradicted" if has_states else
        "this payload carries no candidate 'states': a contradicted claim is indistinguishable from missing "
        "evidence (build the candidate from ProfileClaim objects with claims_to_vector to keep it)",
        "lane-A skill vectors carry no requirement lines or compensation, and the upstream feature space has no "
        "seniority feature: minimum qualifications, salary sensitivity and seniority steps are not assessable "
        "on this route (raw seniority is kept as metadata only)",
        f"capture time known for {joined} of {len(vectors)} posting(s); freshness and drift checks need it",
        "job values use lane A's encoding (1 = required, 0.5 = preferred, 0 = not listed); candidate values "
        "use lane A's evidence weights (supported 1, self-reported 0.5, unverified 0.25)",
    ])
    return {"engine": engine, "candidate": candidate, "notes": notes}
