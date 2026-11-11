"""Controlled vocabulary for atlas feature vectors.

Everything here is a SYNTHETIC DESIGN ASSUMPTION made by lane C so that the atlas has a
transparent, versioned feature space. Nothing in this file is a measurement of the labour
market: effort-hour ranges, acquisition modes and seniority levels are planning placeholders
that a user can override, never empirical facts.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable, Mapping

from .errors import InvalidInputError

VOCAB_VERSION = "atlas-vocab-0.1.0"

#: Feature produced from ``JobPosting.seniority`` (ordinal, 0..1). Not a skill.
SENIORITY_FEATURE = "ctx:seniority-level"

#: Evidence for these features is normally gained by doing the job, not by study alone.
ACQ_ROLE = "role-exposure"
ACQ_STUDY = "study-or-project"

_GROUP_EFFORT_HOURS: dict[str, tuple[float, float]] = {
    # (low, high) hours to build *credible evidence* for one feature. Placeholder ranges.
    "engineering": (60.0, 200.0),
    "ai": (40.0, 160.0),
    "cloud-platform": (60.0, 200.0),
    "customer": (120.0, 400.0),
    "leadership": (200.0, 800.0),
    "governance": (60.0, 240.0),
    "context": (0.0, 0.0),
}

_SKILLS_BY_GROUP: dict[str, tuple[str, ...]] = {
    "engineering": (
        "python", "typescript", "go", "java", "sql", "api-design", "distributed-systems",
        "system-design", "testing", "data-modeling", "backend-performance", "frontend",
    ),
    "ai": (
        "ai-evaluation", "llm-integration", "rag", "prompt-engineering", "ml-modeling", "mlops",
        "agent-orchestration", "ai-strategy", "ai-safety",
    ),
    "cloud-platform": (
        "aws", "cloud-architecture", "kubernetes", "infrastructure-as-code", "ci-cd",
        "observability", "sre", "security", "cost-optimization",
    ),
    "customer": (
        "customer-delivery", "solution-design", "technical-discovery", "enterprise-sales",
        "executive-communication", "pre-sales-demos", "stakeholder-management",
    ),
    "leadership": (
        "people-management", "technical-leadership", "hiring", "roadmap-planning",
        "budget-ownership", "org-design", "mentoring",
    ),
    "governance": (
        "privacy-engineering", "data-governance", "regulatory-compliance", "legal-reasoning",
        "ai-governance", "contract-analysis", "risk-assessment",
    ),
}

_ROLE_EXPOSURE_FEATURES = frozenset({
    "customer-delivery", "enterprise-sales", "executive-communication", "stakeholder-management",
    "pre-sales-demos", "people-management", "hiring", "budget-ownership", "org-design",
})

#: alias -> canonical. ``lossy`` aliases collapse a distinct concept onto the nearest feature.
_ALIASES: dict[str, str] = {
    "k8s": "kubernetes", "terraform": "infrastructure-as-code", "iac": "infrastructure-as-code",
    "llm-evals": "ai-evaluation", "evals": "ai-evaluation", "model-evaluation": "ai-evaluation",
    "retrieval-augmented-generation": "rag", "amazon-web-services": "aws",
    "machine-learning": "ml-modeling", "ml": "ml-modeling", "golang": "go",
    "postgres": "sql", "postgresql": "sql", "rest": "api-design", "grpc": "api-design",
    "exec-communication": "executive-communication", "customer-facing": "customer-delivery",
    "devops": "ci-cd", "monitoring": "observability", "gdpr": "regulatory-compliance",
    "people-leadership": "people-management", "team-management": "people-management",
    "llm": "llm-integration", "llms": "llm-integration", "agents": "agent-orchestration",
    "javascript": "typescript", "cloud": "cloud-architecture", "privacy": "privacy-engineering",
    "site-reliability": "sre", "finops": "cost-optimization", "presales": "pre-sales-demos",
}
_LOSSY_ALIASES = frozenset({"javascript", "cloud", "privacy", "postgres", "postgresql", "gdpr",
                            "devops", "ml", "llm", "llms", "rest", "grpc"})

#: Convenience bundles a user can name in a scenario instead of listing features one by one.
BUNDLES: dict[str, tuple[str, ...]] = {
    "client-exposure": ("customer-delivery", "stakeholder-management", "technical-discovery"),
    "management-scope": ("people-management", "hiring", "budget-ownership"),
}

#: Ordinal convention for ``JobPosting.seniority`` strings. Unknown strings stay UNKNOWN.
SENIORITY_LEVELS: dict[str, float] = {
    "intern": 0.0, "junior": 0.15, "entry": 0.15, "mid": 0.35, "mid-level": 0.35,
    "senior": 0.55, "staff": 0.7, "lead": 0.7, "manager": 0.7, "principal": 0.8,
    "senior-manager": 0.8, "director": 0.9, "vp": 0.95, "executive": 1.0, "c-level": 1.0,
}

_TERM_MAX_LEN = 64
_SAFE_CHARS = re.compile(r"[^a-z0-9+#.\-]")
_SEPARATORS = re.compile(r"[\s_/]+")


def normalize_token(raw: object) -> str | None:
    """Lower-case, hyphenate and strip a raw term. Returns ``None`` for unusable input.

    Raw text is untrusted (it may come from a job description): the result only ever
    contains ``[a-z0-9+#.-]`` and is capped at 64 characters.
    """
    if not isinstance(raw, str):
        return None
    text = _SEPARATORS.sub("-", raw.strip().lower())
    text = _SAFE_CHARS.sub("", text).strip("-.")
    text = re.sub(r"-{2,}", "-", text)
    if not text or len(text) > _TERM_MAX_LEN:
        return None
    return text


@dataclass(frozen=True)
class FeatureDef:
    name: str
    group: str
    acquisition: str
    effort_hours: tuple[float, float]

    @property
    def effort_mid(self) -> float:
        return (self.effort_hours[0] + self.effort_hours[1]) / 2.0


@dataclass(frozen=True)
class Vocabulary:
    """Ordered, versioned feature space. Instances are immutable; perturbations return copies."""

    version: str
    features: tuple[FeatureDef, ...]
    aliases: Mapping[str, str] = field(default_factory=dict)
    lossy_aliases: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        names = [f.name for f in self.features]
        if len(names) != len(set(names)):
            raise InvalidInputError("vocabulary has duplicate feature names")
        if not names:
            raise InvalidInputError("vocabulary needs at least one feature")
        unknown = sorted(set(self.aliases.values()) - set(names))
        if unknown:
            raise InvalidInputError(f"aliases point to unknown features: {unknown}")

    # -- lookups ------------------------------------------------------------------
    @cached_property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.features)

    @cached_property
    def index(self) -> dict[str, int]:
        return {f.name: i for i, f in enumerate(self.features)}

    @property
    def skill_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.features if f.group != "context")

    def get(self, name: str) -> FeatureDef:
        return self.features[self.index[name]]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.index

    def resolve(self, raw: object) -> tuple[str | None, str]:
        """Map a raw term to a canonical feature.

        Returns ``(feature | None, how)`` with ``how`` in
        ``{"exact", "alias", "lossy-alias", "unmapped", "invalid"}``.
        """
        token = normalize_token(raw)
        if token is None:
            return None, "invalid"
        if token in self.index and not token.startswith("ctx:"):
            return token, "exact"
        target = self.aliases.get(token)
        if target is not None:
            return target, ("lossy-alias" if token in self.lossy_aliases else "alias")
        return None, "unmapped"

    @cached_property
    def _digest(self) -> str:
        payload = {
            "version": self.version,
            "features": [[f.name, f.group] for f in self.features],
            "aliases": sorted(self.aliases.items()),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def digest(self) -> str:
        """Stable digest of the feature space. Two vectors are comparable iff digests match."""
        return self._digest

    # -- perturbations used by the sensitivity analysis -----------------------------
    def without_features(self, names: Iterable[str]) -> "Vocabulary":
        drop = set(names)
        kept = tuple(f for f in self.features if f.name not in drop)
        aliases = {a: t for a, t in self.aliases.items() if t not in drop}
        return Vocabulary(self.version + "+dropped", kept, aliases, self.lossy_aliases)

    def without_aliases(self) -> "Vocabulary":
        return Vocabulary(self.version + "+noalias", self.features, {}, frozenset())


def default_vocabulary() -> Vocabulary:
    feats: list[FeatureDef] = []
    for group, names in _SKILLS_BY_GROUP.items():
        for name in names:
            acq = ACQ_ROLE if name in _ROLE_EXPOSURE_FEATURES else ACQ_STUDY
            feats.append(FeatureDef(name, group, acq, _GROUP_EFFORT_HOURS[group]))
    feats.append(FeatureDef(SENIORITY_FEATURE, "context", ACQ_ROLE, _GROUP_EFFORT_HOURS["context"]))
    return Vocabulary(VOCAB_VERSION, tuple(feats), dict(_ALIASES), _LOSSY_ALIASES)
