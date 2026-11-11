"""Principal component analysis on numpy SVD, with a reproducible, serialisable model.

No scikit-learn dependency. ``fit_pca`` computes the thin SVD of the centred (optionally
standardised) matrix; the test-suite cross-checks it against an independent
eigendecomposition of the covariance matrix.

Degenerate inputs never produce silently misleading coordinates: zero-variance data, rank
below the requested number of components and zero-variance columns are all reported through
``PCAModel.warnings`` and the affected axes are identically zero.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .errors import DegenerateInputError, FeatureSpaceMismatchError, InvalidInputError
from .features import impute

MODEL_FORMAT = "atlas-pca-model-0.1.0"
_RANK_RTOL = 1e-10


@dataclass(frozen=True)
class PCAModel:
    feature_names: tuple[str, ...]
    vocab_digest: str
    mean: np.ndarray                    # (d,) centring vector, also used to impute UNKNOWN
    scale: np.ndarray                   # (d,) 1.0 everywhere unless standardize=True
    components: np.ndarray              # (k, d) orthonormal rows; zero rows beyond data rank
    explained_variance: np.ndarray      # (k,)
    explained_variance_ratio: np.ndarray  # (k,)
    total_variance: float
    all_variance_ratio: np.ndarray      # ratios for every non-trivial component (scree)
    n_samples: int
    rank: int
    standardize: bool
    zero_variance_features: tuple[str, ...]
    warnings: tuple[str, ...]

    # -- identity -----------------------------------------------------------------
    @property
    def n_components(self) -> int:
        return int(self.components.shape[0])

    @property
    def model_id(self) -> str:
        """Digest of the fitted parameters rounded to 1e-9 (stable across BLAS builds)."""
        payload = {
            "format": MODEL_FORMAT, "vocab": self.vocab_digest, "standardize": self.standardize,
            "features": list(self.feature_names),
            "mean": np.round(self.mean, 9).tolist(),
            "scale": np.round(self.scale, 9).tolist(),
            "components": (np.round(self.components, 9) + 0.0).tolist(),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    # -- projection ---------------------------------------------------------------
    def _prepare(self, X: np.ndarray, feature_names: Sequence[str] | None) -> tuple[np.ndarray, np.ndarray]:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X[None, :]
        if X.ndim != 2:
            raise InvalidInputError("expected a 2-D array of shape (n_samples, n_features)")
        if feature_names is not None and tuple(feature_names) != self.feature_names:
            raise FeatureSpaceMismatchError(
                "feature names/order differ from the fitted model; refit or re-map the vectors")
        if X.shape[1] != len(self.feature_names):
            raise FeatureSpaceMismatchError(
                f"got {X.shape[1]} features, model was fitted on {len(self.feature_names)}")
        filled, n_imputed = impute(X, self.mean)
        return (filled - self.mean) / self.scale, n_imputed

    def transform(self, X: np.ndarray, feature_names: Sequence[str] | None = None) -> np.ndarray:
        """Project rows (training or held-out) with the frozen fitted parameters."""
        Z, _ = self._prepare(X, feature_names)
        return Z @ self.components.T

    def transform_with_report(self, X: np.ndarray,
                              feature_names: Sequence[str] | None = None) -> dict[str, np.ndarray]:
        """Project and report, per row, how much of the row the map cannot show.

        ``residual_share`` is the fraction of a row's squared distance from the training mean
        that lies outside the retained components (0 = fully visible, 1 = invisible).
        """
        Z, n_imputed = self._prepare(X, feature_names)
        scores = Z @ self.components.T
        total = np.einsum("ij,ij->i", Z, Z)
        kept = np.einsum("ij,ij->i", scores, scores)
        residual = np.clip(total - kept, 0.0, None)
        share = np.divide(residual, total, out=np.zeros_like(total), where=total > 1e-18)
        return {"coords": scores, "residual_share": share, "n_imputed": n_imputed,
                "distance_from_mean": np.sqrt(total)}

    def inverse_transform(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=np.float64)
        if scores.ndim != 2 or scores.shape[1] != self.n_components:
            raise InvalidInputError("scores must have shape (n, n_components)")
        return (scores @ self.components) * self.scale + self.mean

    def standardized(self, X: np.ndarray) -> np.ndarray:
        """Rows in the model's own metric space (imputed, centred, scaled)."""
        return self._prepare(X, None)[0]

    def loadings(self, top: int = 6) -> list[dict[str, Any]]:
        """Per component: variance share and the features with the largest |weight|."""
        out = []
        for c in range(self.n_components):
            w = self.components[c]
            order = np.argsort(-np.abs(w), kind="stable")[:top]
            out.append({
                "component": c + 1,
                "explained_variance_ratio": float(self.explained_variance_ratio[c]),
                "positive": [{"feature": self.feature_names[i], "weight": round(float(w[i]), 6)}
                             for i in order if w[i] > 0],
                "negative": [{"feature": self.feature_names[i], "weight": round(float(w[i]), 6)}
                             for i in order if w[i] < 0],
            })
        return out

    # -- serialisation ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MODEL_FORMAT, "model_id": self.model_id,
            "feature_names": list(self.feature_names), "vocab_digest": self.vocab_digest,
            "mean": self.mean.tolist(), "scale": self.scale.tolist(),
            "components": self.components.tolist(),
            "explained_variance": self.explained_variance.tolist(),
            "explained_variance_ratio": self.explained_variance_ratio.tolist(),
            "total_variance": self.total_variance,
            "all_variance_ratio": self.all_variance_ratio.tolist(),
            "n_samples": self.n_samples, "rank": self.rank, "standardize": self.standardize,
            "zero_variance_features": list(self.zero_variance_features),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PCAModel":
        if not isinstance(d, dict) or d.get("format") != MODEL_FORMAT:
            raise InvalidInputError(f"not a {MODEL_FORMAT} document")
        try:
            names = tuple(d["feature_names"])
            mean = np.asarray(d["mean"], dtype=np.float64)
            scale = np.asarray(d["scale"], dtype=np.float64)
            comps = np.asarray(d["components"], dtype=np.float64)
            model = cls(
                feature_names=names, vocab_digest=str(d["vocab_digest"]), mean=mean, scale=scale,
                components=comps,
                explained_variance=np.asarray(d["explained_variance"], dtype=np.float64),
                explained_variance_ratio=np.asarray(d["explained_variance_ratio"], dtype=np.float64),
                total_variance=float(d["total_variance"]),
                all_variance_ratio=np.asarray(d["all_variance_ratio"], dtype=np.float64),
                n_samples=int(d["n_samples"]), rank=int(d["rank"]),
                standardize=bool(d["standardize"]),
                zero_variance_features=tuple(d["zero_variance_features"]),
                warnings=tuple(d["warnings"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInputError(f"malformed PCA model document: {exc}") from exc
        dn = len(names)
        if (mean.shape != (dn,) or scale.shape != (dn,) or comps.ndim != 2 or comps.shape[1] != dn
                or not np.all(np.isfinite(mean)) or not np.all(np.isfinite(comps))
                or not np.all(np.isfinite(scale)) or np.any(scale <= 0)):
            raise InvalidInputError("PCA model document has inconsistent or non-finite parameters")
        return model


def fit_pca(
    X: np.ndarray,
    feature_names: Sequence[str],
    vocab_digest: str = "unversioned",
    n_components: int = 2,
    standardize: bool = False,
) -> PCAModel:
    """Fit PCA. ``X`` may contain NaN (UNKNOWN); those are mean-imputed before fitting.

    Raises ``InvalidInputError`` for malformed input and ``DegenerateInputError`` when there
    are fewer than two rows. Zero variance and low rank are *reported*, not raised, because a
    map of identical points is a legitimate (if uninformative) result that the UI must label.
    """
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2:
        raise InvalidInputError("X must be 2-D (n_samples, n_features)")
    n, d = X.shape
    names = tuple(feature_names)
    if len(names) != d:
        raise FeatureSpaceMismatchError(f"{len(names)} feature names for {d} columns")
    if len(set(names)) != d:
        raise InvalidInputError("feature names must be unique")
    if d < 1:
        raise InvalidInputError("X needs at least one feature column")
    if isinstance(n_components, bool) or not isinstance(n_components, int) or n_components < 1:
        raise InvalidInputError("n_components must be a positive integer")
    if n < 2:
        raise DegenerateInputError("PCA needs at least two rows")
    if np.isinf(X).any():
        raise InvalidInputError("X contains infinite values")

    warnings: list[str] = []
    observed = ~np.isnan(X)
    counts = observed.sum(axis=0)
    mean = np.divide(np.where(observed, X, 0.0).sum(axis=0), counts,
                     out=np.zeros(d), where=counts > 0)
    empty_cols = [names[i] for i in np.flatnonzero(counts == 0)]
    if empty_cols:
        warnings.append("all-unknown-features (imputed 0.0, carry no information): " + ",".join(empty_cols))
    n_imputed = int((~observed).sum())
    if n_imputed:
        warnings.append(f"mean-imputed {n_imputed} unknown cells ({n_imputed / X.size:.2%}); "
                        "variance along those features is understated")
    Xc = np.where(observed, X, mean[None, :]) - mean

    col_std = Xc.std(axis=0, ddof=1)
    zero_var = [names[i] for i in np.flatnonzero(col_std <= 1e-12)]
    scale = np.ones(d)
    if standardize:
        scale = np.where(col_std > 1e-12, col_std, 1.0)
        Xc = Xc / scale
    if zero_var:
        warnings.append("zero-variance features (no influence on the map): " + ",".join(zero_var))

    total_var = float((Xc ** 2).sum() / (n - 1))
    k_max = min(n - 1, d)
    if total_var <= 1e-18:
        warnings.append("zero-variance-input: every row is identical after preprocessing; "
                        "all coordinates are 0 and the map carries no information")
        rank = 0
        s = np.zeros(0)
        vt = np.zeros((0, d))
    else:
        _, s, vt = np.linalg.svd(Xc, full_matrices=False)
        rank = int((s > s[0] * _RANK_RTOL).sum())
        s, vt = s[:rank], vt[:rank]
        # Deterministic sign: the largest-|weight| entry of every component is positive.
        for c in range(rank):
            j = int(np.argmax(np.abs(vt[c])))
            if vt[c, j] < 0:
                vt[c] = -vt[c]
    var_all = (s ** 2) / (n - 1)
    ratio_all = var_all / total_var if total_var > 1e-18 else np.zeros(0)

    components = np.zeros((n_components, d))
    ev = np.zeros(n_components)
    evr = np.zeros(n_components)
    keep = min(n_components, rank)
    components[:keep] = vt[:keep]
    ev[:keep] = var_all[:keep]
    evr[:keep] = ratio_all[:keep]
    if n_components > rank and rank > 0:
        warnings.append(f"rank-deficient: data rank is {rank} (max {k_max}) but {n_components} "
                        f"components were requested; axes {rank + 1}..{n_components} are identically 0")
    if rank >= 2 and n_components >= 2 and keep >= 2:
        nxt = var_all[keep] if rank > keep else 0.0
        if ev[keep - 1] > 0 and nxt / ev[keep - 1] > 0.9:
            warnings.append("near-degenerate eigenvalues at the cut: the last kept axis and the first "
                            "dropped axis explain almost the same variance, so the plane is not unique")
    return PCAModel(
        feature_names=names, vocab_digest=vocab_digest, mean=mean, scale=scale,
        components=components, explained_variance=ev, explained_variance_ratio=evr,
        total_variance=total_var, all_variance_ratio=ratio_all, n_samples=n, rank=rank,
        standardize=bool(standardize), zero_variance_features=tuple(zero_var),
        warnings=tuple(warnings),
    )


def subspace_agreement(a: PCAModel, b: PCAModel) -> float:
    """Smallest cosine of the principal angles between two fitted planes (1 = same plane)."""
    if a.feature_names != b.feature_names:
        raise FeatureSpaceMismatchError("models were fitted on different feature spaces")
    ka, kb = min(a.rank, a.n_components), min(b.rank, b.n_components)
    k = min(ka, kb)
    if k == 0:
        return 0.0
    s = np.linalg.svd(a.components[:k] @ b.components[:k].T, compute_uv=False)
    return float(np.clip(s.min(), 0.0, 1.0))
