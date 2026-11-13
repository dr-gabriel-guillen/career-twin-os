"""Typed errors for the atlas package. Callers can catch ``AtlasError`` for all of them."""
from __future__ import annotations


class AtlasError(Exception):
    """Base class for every error raised deliberately by this package."""


class InvalidInputError(AtlasError):
    """Input is malformed (wrong type, non-finite numbers, empty where data is required)."""


class FeatureSpaceMismatchError(AtlasError):
    """Vectors or matrices were built against different feature spaces / vocabularies."""


class DegenerateInputError(AtlasError):
    """Input is valid but carries too little information for the requested computation."""


class OptionalDependencyMissing(AtlasError):
    """An optional plugin (for example UMAP) was requested but its package is not installed."""
