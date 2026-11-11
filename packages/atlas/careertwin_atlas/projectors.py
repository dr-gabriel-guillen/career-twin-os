"""Alternative projections: interpretable axes, an optional UMAP plugin and a seeded baseline.

* ``InterpretableAxes``: hand-defined, human-readable axes (a signed average of named feature
  groups). Less variance-efficient than PCA, but each axis means something a user can check.
* ``umap_project``: OPTIONAL. Uses ``umap-learn`` only if it is installed. It is never
  imported at package import time and nothing else in the package depends on it. When the
  package is missing the caller gets ``OptionalDependencyMissing`` and reports "not executed".
* ``random_projection``: a seeded Gaussian projection used only as a *diagnostic baseline*
  (how good is a map that ignores the data?) and to exercise seed-variation checks. It is
  never emitted as an AtlasPoint method.
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .errors import FeatureSpaceMismatchError, InvalidInputError, OptionalDependencyMissing
from .vocabulary import SENIORITY_FEATURE, Vocabulary

AXES_VERSION = "atlas-axes-0.1.0"

#: (axis name, positive pole description, groups/features +, negative pole description, groups/features -)
_AXIS_DEFS: tuple[tuple[str, str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("customer-facing vs builder", "customer & commercial exposure", ("group:customer",),
     "hands-on engineering & platform", ("group:engineering", "group:cloud-platform")),
    ("leadership scope vs individual contribution", "leadership scope & seniority",
     ("group:leadership", SENIORITY_FEATURE),
     "individual technical contribution", ("group:engineering", "group:ai")),
    ("governance vs AI build", "privacy, legal & governance", ("group:governance",),
     "AI system building", ("group:ai",)),
)


def _expand(spec: Sequence[str], vocab: Vocabulary) -> list[str]:
    names: list[str] = []
    for item in spec:
        if item.startswith("group:"):
            names.extend(f.name for f in vocab.features if f.group == item[6:])
        elif item in vocab.index:
            names.append(item)
    return names


@dataclass(frozen=True)
class InterpretableAxes:
    feature_names: tuple[str, ...]
    vocab_digest: str
    weights: np.ndarray                 # (k, d)
    definitions: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    @property
    def projection_id(self) -> str:
        return f"interpretable-axes-{AXES_VERSION}-{self.vocab_digest}"

    def transform(self, X_imputed: np.ndarray) -> np.ndarray:
        X = np.asarray(X_imputed, dtype=np.float64)
        if X.ndim == 1:
            X = X[None, :]
        if X.ndim != 2 or X.shape[1] != len(self.feature_names):
            raise FeatureSpaceMismatchError(
                f"got {X.shape[-1]} features, axes were defined on {len(self.feature_names)}")
        if not np.all(np.isfinite(X)):
            raise InvalidInputError("interpretable axes need imputed, finite input")
        return X @ self.weights.T

    def axis_cosines(self) -> list[list[float]]:
        """Cosine between axis weight vectors. Non-zero off-diagonals mean correlated axes."""
        norms = np.linalg.norm(self.weights, axis=1)
        safe = np.where(norms > 1e-12, norms, 1.0)
        W = self.weights / safe[:, None]
        return np.round(W @ W.T, 6).tolist()


def build_interpretable_axes(vocab: Vocabulary, n_axes: int = 2) -> InterpretableAxes:
    if n_axes not in (2, 3):
        raise InvalidInputError("n_axes must be 2 or 3")
    d = len(vocab.features)
    W = np.zeros((n_axes, d))
    defs, warnings = [], []
    for a in range(n_axes):
        name, pos_desc, pos_spec, neg_desc, neg_spec = _AXIS_DEFS[a]
        pos, neg = _expand(pos_spec, vocab), _expand(neg_spec, vocab)
        if not pos or not neg:
            warnings.append(f"axis {name!r}: a pole has no features in this vocabulary; axis is partial")
        for f in pos:
            W[a, vocab.index[f]] += 1.0 / len(pos)
        for f in neg:
            W[a, vocab.index[f]] -= 1.0 / len(neg)
        defs.append({"axis": a + 1, "name": name, "positive_pole": pos_desc, "positive_features": pos,
                     "negative_pole": neg_desc, "negative_features": neg,
                     "formula": "mean(positive features) - mean(negative features)"})
    return InterpretableAxes(vocab.feature_names, vocab.digest(), W, tuple(defs), tuple(warnings))


def random_projection(X: np.ndarray, seed: int, n_components: int = 2) -> np.ndarray:
    """Seeded Gaussian random projection of centred data. Diagnostic baseline only."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2 or not np.all(np.isfinite(X)):
        raise InvalidInputError("random projection needs a finite 2-D array")
    rng = np.random.default_rng(seed)
    R = rng.standard_normal((X.shape[1], n_components)) / np.sqrt(n_components)
    return (X - X.mean(axis=0)) @ R


def umap_available() -> bool:
    try:
        return importlib.util.find_spec("umap") is not None
    except (ImportError, ValueError):
        return False


def umap_project(X: np.ndarray, seed: int = 0, n_components: int = 2, n_neighbors: int = 15,
                 min_dist: float = 0.1) -> np.ndarray:
    """Project with umap-learn if installed. Offline: UMAP runs locally and needs no network."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2 or not np.all(np.isfinite(X)):
        raise InvalidInputError("UMAP needs a finite 2-D array")
    if not umap_available():
        raise OptionalDependencyMissing(
            "umap-learn is not installed; UMAP is an optional plugin. PCA and interpretable axes "
            "remain fully functional.")
    if X.shape[0] < 4:
        raise InvalidInputError("UMAP needs at least 4 rows")
    try:
        umap = importlib.import_module("umap")
        reducer = umap.UMAP(n_components=n_components, n_neighbors=min(n_neighbors, X.shape[0] - 1),
                            min_dist=min_dist, random_state=seed)
        emb = np.asarray(reducer.fit_transform(X), dtype=np.float64)
    except OptionalDependencyMissing:
        raise
    except ImportError as exc:
        raise OptionalDependencyMissing(f"umap-learn could not be imported: {exc}") from exc
    if emb.shape != (X.shape[0], n_components) or not np.all(np.isfinite(emb)):
        raise InvalidInputError("UMAP plugin returned an embedding of the wrong shape or with non-finite values")
    return emb
