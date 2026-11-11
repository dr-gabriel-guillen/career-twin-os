"""Construction of contract-conforming ``AtlasPoint`` objects (contracts/atlas-point.schema.json)."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from .errors import InvalidInputError
from .features import sanitize_label

REPRESENTATION_TYPES = ("candidate", "job", "role-centroid", "scenario")
PROJECTION_METHODS = ("pca", "umap", "interpretable-axes")
MAP_IS_NOT_FIT = "map-position-is-not-a-fit-or-hiring-score"


def atlas_point(entity_id: str, label: str, coords: Sequence[float], source_vector_ref: str,
                representation_type: str, projection_method: str, projection_id: str,
                neighborhood_quality: float | None = None, fit_metric_ref: str | None = None,
                warnings: Iterable[str] = ()) -> dict[str, Any]:
    if representation_type not in REPRESENTATION_TYPES:
        raise InvalidInputError(f"representation_type must be one of {REPRESENTATION_TYPES}")
    if projection_method not in PROJECTION_METHODS:
        raise InvalidInputError(f"projection_method must be one of {PROJECTION_METHODS}")
    c = np.asarray(coords, dtype=np.float64).ravel()
    if c.size not in (2, 3) or not np.all(np.isfinite(c)):
        raise InvalidInputError("coords must be 2 or 3 finite numbers")
    if not all(isinstance(x, str) and x for x in (entity_id, projection_id, source_vector_ref)):
        raise InvalidInputError("entity_id, projection_id and source_vector_ref must be non-empty strings")
    nq = None
    if neighborhood_quality is not None:
        nq = float(neighborhood_quality)
        if not (np.isfinite(nq) and 0.0 <= nq <= 1.0):
            raise InvalidInputError("neighborhood_quality must be within [0, 1] or null")
        nq = round(nq, 6)
    seen: list[str] = []
    for w in warnings:
        if isinstance(w, str) and w and w not in seen:
            seen.append(w)
    return {
        "entity_id": entity_id,
        "projection_id": projection_id,
        "label": sanitize_label(label, entity_id),
        "coords": [round(float(x), 6) + 0.0 for x in c],
        "source_vector_ref": source_vector_ref,
        "representation_type": representation_type,
        "projection_method": projection_method,
        "neighborhood_quality": nq,
        "fit_metric_ref": fit_metric_ref,
        "warnings": seen,
    }
