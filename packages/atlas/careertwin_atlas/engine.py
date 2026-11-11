"""AtlasEngine: one fitted, immutable snapshot of the atlas (vectors, PCA model, role profiles).

Everything downstream (gap analysis, transition graph, simulator, sensitivity, API adapter)
reads from an engine, so that a single ``fit`` defines one consistent feature space.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .errors import DegenerateInputError, FeatureSpaceMismatchError, InvalidInputError
from .features import (STATE_UNKNOWN, FeatureMatrix, FeatureVector, build_matrix, job_to_vector)
from .pca import PCAModel, fit_pca
from .roles import RoleProfile, build_role_profiles, centroid_stability
from .taxonomy import FAMILY_LABELS
from .vocabulary import Vocabulary, default_vocabulary


@dataclass(frozen=True)
class AtlasEngine:
    vocab: Vocabulary
    matrix: FeatureMatrix
    model: PCAModel
    X: np.ndarray                      # imputed raw feature matrix (n, d)
    Z: np.ndarray                      # model metric space (centred, scaled) (n, d)
    coords: np.ndarray                 # (n, n_components)
    residual_share: np.ndarray         # (n,) share of each row the map cannot show
    n_imputed: np.ndarray              # (n,) unknown cells per row
    profiles: Mapping[str, RoleProfile]
    stability: Mapping[str, Mapping[str, Any]]
    skipped: tuple[Mapping[str, str], ...]
    seed: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    unplaced: tuple[Mapping[str, str], ...] = field(default_factory=tuple)

    # -- construction ---------------------------------------------------------------
    @classmethod
    def fit(cls, jobs: Sequence[Mapping[str, Any]], vocab: Vocabulary | None = None,
            standardize: bool = False, n_components: int = 2, seed: int = 0,
            n_boot: int = 50) -> "AtlasEngine":
        if isinstance(jobs, (str, bytes)) or not isinstance(jobs, Sequence):
            raise InvalidInputError("jobs must be a sequence of JobPosting mappings")
        vocab = vocab or default_vocabulary()
        vectors: list[FeatureVector] = []
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        for pos, job in enumerate(jobs):
            try:
                v = job_to_vector(job, vocab)
            except InvalidInputError as exc:
                skipped.append({"position": str(pos), "reason": str(exc)})
                continue
            if v.entity_id in seen:
                skipped.append({"position": str(pos), "reason": f"duplicate id {v.entity_id!r}; first kept"})
                continue
            seen.add(v.entity_id)
            vectors.append(v)
        return cls.from_vectors(vectors, vocab, standardize=standardize, n_components=n_components,
                                seed=seed, n_boot=n_boot, skipped=skipped)

    @classmethod
    def from_vectors(cls, vectors: Sequence[FeatureVector], vocab: Vocabulary, standardize: bool = False,
                     n_components: int = 2, seed: int = 0, n_boot: int = 50,
                     skipped: Sequence[Mapping[str, str]] = (),
                     role_labels: Mapping[str, str] | None = None) -> "AtlasEngine":
        """Fit from ready-made feature vectors (for example lane A's published skill vectors)."""
        skipped = [dict(s) for s in skipped]
        if any(v.vocab_digest != vocab.digest() or v.feature_names != vocab.feature_names for v in vectors):
            raise FeatureSpaceMismatchError("vectors were not built with the supplied vocabulary")
        # A vector with no readable skill at all is UNKNOWN, not "average". Mean imputation would
        # draw it at the centre of the map, so it is kept out of the fit and reported instead.
        skill_cols = [i for i, f in enumerate(vocab.features) if f.group != "context"]
        usable: list[FeatureVector] = []
        unplaced: list[dict[str, str]] = []
        for v in vectors:
            if skill_cols and bool(np.all(v.states[skill_cols] == STATE_UNKNOWN)):
                unplaced.append({"entity_id": v.entity_id, "label": v.label, "source_vector_ref": v.vector_ref,
                                 "reason": "no observed skill features: unknown, not placed on the map"})
            else:
                usable.append(v)
        vectors = usable
        if len(vectors) < 2:
            raise DegenerateInputError(
                f"need at least two usable postings to fit an atlas; got {len(vectors)} "
                f"({len(skipped)} skipped, {len(unplaced)} with no observed skills)")
        matrix = build_matrix(vectors)
        model = fit_pca(matrix.X, matrix.feature_names, matrix.vocab_digest,
                        n_components=n_components, standardize=standardize)
        rep = model.transform_with_report(matrix.X)
        X = np.where(np.isnan(matrix.X), model.mean[None, :], matrix.X)
        Z = model.standardized(matrix.X)
        labels = dict(FAMILY_LABELS)
        labels.update(role_labels or {})
        profiles = build_role_profiles(matrix, X, labels, seed=seed, n_boot=n_boot)
        warnings = list(model.warnings)
        if skipped:
            warnings.append(f"{len(skipped)} posting(s) skipped as unusable or duplicate")
        if unplaced:
            warnings.append(f"{len(unplaced)} posting(s) have no observed skill features and are not placed "
                            "on the map (unknown is not average)")
        unlabelled = sum(1 for v in vectors if not v.meta.get("role_family"))
        if unlabelled:
            warnings.append(f"{unlabelled} posting(s) have no role_family and are excluded from role profiles")
        return cls(vocab=vocab, matrix=matrix, model=model, X=X, Z=Z, coords=rep["coords"],
                   residual_share=rep["residual_share"], n_imputed=rep["n_imputed"],
                   profiles=profiles, stability=centroid_stability(profiles),
                   skipped=tuple(skipped), seed=seed, warnings=tuple(warnings), unplaced=tuple(unplaced))

    # -- helpers --------------------------------------------------------------------
    def check_vector(self, vec: FeatureVector) -> None:
        if vec.vocab_digest != self.matrix.vocab_digest or vec.feature_names != self.matrix.feature_names:
            raise FeatureSpaceMismatchError(
                f"vector {vec.entity_id!r} uses vocabulary {vec.vocab_digest}; "
                f"engine was fitted on {self.matrix.vocab_digest}")

    def has_observed_skills(self, vec: FeatureVector) -> bool:
        """False when every skill feature of ``vec`` is UNKNOWN (such a vector must not be placed)."""
        self.check_vector(vec)
        cols = [i for i, f in enumerate(self.vocab.features) if f.group != "context"]
        return not cols or not bool(np.all(vec.states[cols] == STATE_UNKNOWN))

    def resolve_role(self, text: object) -> str:
        """Accept a role family id or its display label (case-insensitive); return the family id."""
        if isinstance(text, str) and text in self.profiles:
            return text
        if isinstance(text, str) and text.strip():
            wanted = text.strip().casefold()
            hits = sorted(f for f, p in self.profiles.items()
                          if f.casefold() == wanted or p.label.casefold() == wanted)
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise InvalidInputError(f"role {text!r} is ambiguous: matches {hits}")
        raise InvalidInputError(f"unknown role {text!r}; known: {sorted(self.profiles)}")

    def imputed(self, vec: FeatureVector) -> np.ndarray:
        self.check_vector(vec)
        return np.where(np.isnan(vec.values), self.model.mean, vec.values)

    def project(self, vec: FeatureVector) -> dict[str, Any]:
        """Project one held-out vector with the frozen model."""
        self.check_vector(vec)
        rep = self.model.transform_with_report(vec.values[None, :])
        return {"coords": rep["coords"][0], "residual_share": float(rep["residual_share"][0]),
                "n_imputed": int(rep["n_imputed"][0])}

    def role_table(self, vec: FeatureVector) -> list[dict[str, Any]]:
        """Distance of one vector to every role centroid, in the full space and on the map.

        ``requirement_shortfall`` only counts what the role states and the vector does not
        cover (one-sided). A skill feature whose state is UNKNOWN counts as **no evidence**:
        evidence that is not on file cannot cover a requirement. (Repair note, review cycle 1,
        F1: unknown features used to be dropped, so a candidate with no readable evidence scored
        0.0 for every role and adding evidence made the number worse.) A vector with no observed
        skill feature at all is refused. ``full_space_distance`` is the symmetric Euclidean
        distance in the model's metric space (unknowns mean-imputed, as for the map).
        ``map_distance`` is reported only so that disagreement with the full space can be shown;
        it is never a fit score. Equal values share a rank.
        """
        if not self.has_observed_skills(vec):
            raise InvalidInputError(f"{vec.entity_id!r} has no observed skill evidence; a role comparison "
                                    "is not assessable")
        x = self.imputed(vec)
        skill = np.array([not n.startswith("ctx:") for n in self.matrix.feature_names])
        evidence = np.where(vec.states == STATE_UNKNOWN, 0.0, np.nan_to_num(vec.values, nan=0.0))
        n_unknown = int(((vec.states == STATE_UNKNOWN) & skill).sum())
        scale = self.model.scale
        here = self.model.transform(x[None, :])[0]
        rows = []
        for fam in sorted(self.profiles):
            p = self.profiles[fam]
            diff = (p.centroid - x) / scale
            short = np.clip((p.centroid - evidence) / scale, 0.0, None) * skill
            denom = float(np.linalg.norm(x) * np.linalg.norm(p.centroid))
            there = self.model.transform(p.centroid[None, :])[0]
            rows.append({
                "role": fam, "label": p.label, "n_postings": p.n_postings,
                "requirement_shortfall": float(np.linalg.norm(short)),
                "full_space_distance": float(np.linalg.norm(diff)),
                "cosine_similarity": float(x @ p.centroid / denom) if denom > 1e-12 else None,
                "map_distance": float(np.linalg.norm(here - there)),
                "unknown_features_counted_as_no_evidence": n_unknown,
            })
        for key, rank_key in (("requirement_shortfall", "shortfall_rank"),
                              ("full_space_distance", "full_space_rank"),
                              ("map_distance", "map_rank")):
            vals = [round(r[key], 9) for r in rows]
            for r, v in zip(rows, vals):
                r[rank_key] = 1 + sum(1 for w in vals if w < v)        # competition ranking: ties share
        for r in rows:
            r["map_rank_disagrees"] = bool(r["map_rank"] != r["full_space_rank"])
        return sorted(rows, key=lambda r: (r["shortfall_rank"], r["requirement_shortfall"], r["role"]))

    def role_order(self, vec: FeatureVector) -> list[str]:
        return [r["role"] for r in self.role_table(vec)]
