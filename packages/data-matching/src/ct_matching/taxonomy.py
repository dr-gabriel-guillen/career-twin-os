"""Frozen skill and role-family taxonomy (version-hashed).

Everything here is an *authoring assumption* of lane A, not measured labour-market
data. Priors (customer proximity, enterprise delivery, family adjacency) are disclosed
in every explanation as ``taxonomy-prior``.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

# canonical skill id -> surface forms (matched case-insensitively on word boundaries)
SKILLS: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "typescript": ("typescript", "ts/js"),
    "javascript": ("javascript", "node.js", "nodejs"),
    "java": ("java",),
    "go": ("golang", "go lang"),
    "rust": ("rust",),
    "sql": ("sql", "postgresql", "postgres"),
    "react": ("react", "react.js", "next.js"),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud"),
    "azure": ("azure",),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform", "infrastructure as code"),
    "ci-cd": ("ci/cd", "ci-cd", "continuous delivery", "continuous integration"),
    "observability": ("observability", "monitoring and alerting"),
    "api-design": ("api design", "api-design", "rest apis", "restful apis", "graphql"),
    "distributed-systems": ("distributed systems", "distributed-systems"),
    "cloud-architecture": ("cloud architecture", "cloud-architecture", "solution architecture", "solutions architecture"),
    "security": ("security", "appsec", "application security"),
    "identity-access": ("iam", "sso", "oauth", "identity and access"),
    "compliance": ("soc 2", "soc2", "iso 27001", "compliance"),
    "data-engineering": ("data engineering", "data pipelines", "etl", "elt"),
    "spark": ("spark", "pyspark"),
    "data-modeling": ("data modeling", "data modelling", "dbt"),
    "machine-learning": ("machine learning", "ml models"),
    "deep-learning": ("deep learning", "pytorch", "tensorflow"),
    "llm-applications": ("llm", "llms", "large language models", "generative ai", "genai"),
    "rag": ("rag", "retrieval-augmented generation", "retrieval augmented generation"),
    "ai-evaluation": ("ai evaluation", "ai-evaluation", "model evaluation", "llm evaluation", "evals"),
    "mlops": ("mlops", "ml ops", "model deployment"),
    "prompt-engineering": ("prompt engineering", "prompt design"),
    "ai-strategy": ("ai strategy", "ai-strategy", "ai roadmap"),
    "statistics": ("statistics", "statistical analysis"),
    "experimentation": ("a/b testing", "experimentation"),
    "customer-delivery": ("customer delivery", "customer-delivery", "customer deployments", "customer implementations",
                          "implementation projects"),
    "technical-discovery": ("technical discovery", "discovery workshops", "requirements gathering"),
    "solution-demos": ("demos", "proof of concept", "proofs of concept", "poc", "pocs"),
    "enterprise-sales": ("enterprise sales", "enterprise-sales", "pre-sales", "presales", "sales cycles"),
    "executive-communication": ("executive communication", "executive-communication", "executive briefings",
                                "c-level", "executive stakeholders"),
    "stakeholder-management": ("stakeholder management", "cross-functional stakeholders"),
    "technical-writing": ("technical writing", "documentation"),
    "product-strategy": ("product strategy", "product roadmap", "roadmapping"),
    "user-research": ("user research", "customer interviews"),
    "project-management": ("project management", "program management"),
    "people-management": ("people management", "managing engineers", "performance reviews"),
    "mentoring": ("mentoring", "mentorship", "coaching"),
    "incident-response": ("incident response", "on-call", "incident management"),
    "networking": ("networking", "tcp/ip", "load balancing"),
    "linux": ("linux",),
    "performance-engineering": ("performance tuning", "performance engineering", "profiling"),
    "frontend-architecture": ("frontend architecture", "design systems"),
    "accessibility": ("accessibility", "wcag"),
    "threat-modeling": ("threat modeling", "threat modelling"),
    "penetration-testing": ("penetration testing", "pentesting", "red team"),
    "data-visualization": ("data visualization", "dashboards", "tableau"),
    "account-management": ("account management", "renewals", "customer success"),
    "systems-integration": ("systems integration", "enterprise integration", "integrations"),
    "contract-negotiation": ("contract negotiation", "negotiation"),
}

# family -> (title patterns, core skills, optional skills, customer-proximity prior, enterprise-delivery prior)
FAMILIES: dict[str, dict] = {
    "fde": {"titles": ("forward deployed", "deployed engineer", "implementation engineer"),
            "core": ("python", "api-design", "customer-delivery", "llm-applications", "systems-integration"),
            "optional": ("typescript", "ai-evaluation", "technical-discovery", "sql", "aws"),
            "customer": 0.95, "enterprise": 0.9},
    "field-cto": {"titles": ("field cto", "field chief technology"),
                  "core": ("executive-communication", "cloud-architecture", "ai-strategy", "enterprise-sales", "customer-delivery"),
                  "optional": ("stakeholder-management", "solution-demos", "technical-discovery", "security"),
                  "customer": 1.0, "enterprise": 1.0},
    "ai-architect": {"titles": ("ai architect", "applied ai", "ai solutions", "genai architect", "ml architect"),
                     "core": ("python", "llm-applications", "ai-evaluation", "cloud-architecture", "api-design"),
                     "optional": ("rag", "mlops", "aws", "ai-strategy", "prompt-engineering"),
                     "customer": 0.7, "enterprise": 0.8},
    "solutions-architect": {"titles": ("solutions architect", "solution architect", "solutions engineer", "sales engineer",
                                       "solutions consultant"),
                            "core": ("cloud-architecture", "solution-demos", "technical-discovery", "api-design", "enterprise-sales"),
                            "optional": ("aws", "security", "executive-communication", "systems-integration", "python"),
                            "customer": 0.9, "enterprise": 0.85},
    "tam": {"titles": ("technical account manager", "customer success engineer", "customer engineer"),
            "core": ("account-management", "stakeholder-management", "customer-delivery", "technical-discovery"),
            "optional": ("sql", "api-design", "project-management", "observability"),
            "customer": 0.9, "enterprise": 0.75},
    "ml-engineer": {"titles": ("machine learning engineer", "ml engineer", "ai engineer", "research engineer"),
                    "core": ("python", "machine-learning", "deep-learning", "mlops"),
                    "optional": ("llm-applications", "ai-evaluation", "spark", "kubernetes", "statistics"),
                    "customer": 0.25, "enterprise": 0.4},
    "data-scientist": {"titles": ("data scientist", "applied scientist", "decision scientist"),
                       "core": ("python", "statistics", "machine-learning", "sql"),
                       "optional": ("experimentation", "data-visualization", "deep-learning"),
                       "customer": 0.3, "enterprise": 0.3},
    "data-engineer": {"titles": ("data engineer", "analytics engineer", "data platform"),
                      "core": ("sql", "data-engineering", "python", "data-modeling"),
                      "optional": ("spark", "aws", "gcp", "terraform"),
                      "customer": 0.2, "enterprise": 0.45},
    "backend": {"titles": ("backend engineer", "back-end engineer", "software engineer", "platform engineer", "api engineer"),
                "core": ("api-design", "distributed-systems", "sql"),
                "optional": ("python", "go", "java", "aws", "kubernetes", "observability"),
                "customer": 0.15, "enterprise": 0.4},
    "frontend": {"titles": ("frontend engineer", "front-end engineer", "ui engineer", "web engineer"),
                 "core": ("typescript", "react", "frontend-architecture"),
                 "optional": ("javascript", "accessibility", "api-design", "performance-engineering"),
                 "customer": 0.2, "enterprise": 0.25},
    "devops-sre": {"titles": ("site reliability", "sre", "devops", "infrastructure engineer", "cloud engineer"),
                   "core": ("kubernetes", "terraform", "observability", "incident-response", "linux"),
                   "optional": ("aws", "gcp", "ci-cd", "networking", "python"),
                   "customer": 0.15, "enterprise": 0.5},
    "security-engineer": {"titles": ("security engineer", "security architect", "appsec engineer", "security analyst"),
                          "core": ("security", "threat-modeling", "identity-access"),
                          "optional": ("compliance", "penetration-testing", "aws", "incident-response", "python"),
                          "customer": 0.3, "enterprise": 0.6},
    "product-manager": {"titles": ("product manager", "product lead", "product owner"),
                        "core": ("product-strategy", "user-research", "stakeholder-management"),
                        "optional": ("experimentation", "sql", "technical-writing", "ai-strategy"),
                        "customer": 0.6, "enterprise": 0.45},
    "engineering-manager": {"titles": ("engineering manager", "head of engineering", "director of engineering",
                                       "vp of engineering", "vp engineering"),
                            "core": ("people-management", "mentoring", "project-management", "stakeholder-management"),
                            "optional": ("distributed-systems", "cloud-architecture", "executive-communication"),
                            "customer": 0.35, "enterprise": 0.5},
}
# Title patterns are tested in this order (more specific families first).
FAMILY_ORDER = ("field-cto", "fde", "ai-architect", "solutions-architect", "tam", "engineering-manager",
                "product-manager", "ml-engineer", "data-scientist", "data-engineer", "security-engineer",
                "devops-sre", "frontend", "backend")

# Undirected adjacency: plausibility of a direct move between families (authoring prior).
ADJACENCY: tuple[tuple[str, str], ...] = (
    ("fde", "solutions-architect"), ("fde", "ai-architect"), ("fde", "backend"), ("fde", "tam"),
    ("field-cto", "solutions-architect"), ("field-cto", "ai-architect"), ("field-cto", "engineering-manager"),
    ("ai-architect", "ml-engineer"), ("ai-architect", "solutions-architect"),
    ("solutions-architect", "tam"), ("solutions-architect", "backend"), ("solutions-architect", "security-engineer"),
    ("ml-engineer", "data-scientist"), ("ml-engineer", "backend"), ("ml-engineer", "data-engineer"),
    ("data-engineer", "backend"), ("data-engineer", "data-scientist"),
    ("backend", "frontend"), ("backend", "devops-sre"), ("backend", "security-engineer"), ("backend", "engineering-manager"),
    ("devops-sre", "security-engineer"), ("product-manager", "tam"), ("product-manager", "engineering-manager"),
    ("product-manager", "solutions-architect"),
)
CLOSENESS_BY_HOPS = {0: 1.0, 1: 0.6, 2: 0.3}
CLOSENESS_FAR = 0.1

SENIORITY_LADDER = ("intern", "junior", "mid", "senior", "staff", "principal", "director", "vp", "executive")

CUSTOMER_CUES = ("customer-facing", "client-facing", "customer facing", "client facing", "with customers",
                 "with clients", "on-site with", "onsite with", "pre-sales", "post-sales", "executive briefings",
                 "travel to customer", "customer workshops", "embedded with", "trusted advisor", "customer's environment")
ENTERPRISE_CUES = ("enterprise", "fortune 500", "regulated", "soc 2", "procurement", "rollout", "sla",
                   "production deployment", "production deployments", "large-scale", "mission-critical",
                   "system integrator", "change management")
EVERGREEN_CUES = ("evergreen", "talent pool", "talent community", "always hiring", "future opportunities",
                  "pipeline requisition", "general application", "no specific opening")


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _compile() -> tuple[re.Pattern[str], dict[str, str]]:
    surface_to_id: dict[str, str] = {}
    for sid, forms in SKILLS.items():
        for form in (sid,) + forms:
            surface_to_id.setdefault(_fold(form), sid)
    ordered = sorted(surface_to_id, key=lambda s: (-len(s), s))
    pattern = re.compile(r"(?<![a-z0-9+#])(" + "|".join(re.escape(s) for s in ordered) + r")(?![a-z0-9+#])")
    return pattern, surface_to_id


_SKILL_RE, _SURFACE = _compile()
SKILL_IDS: tuple[str, ...] = tuple(sorted(SKILLS))
SKILL_INDEX = {s: i for i, s in enumerate(SKILL_IDS)}


def canonical_skill(tag: str) -> str | None:
    """Map one declared tag to a canonical skill id (exact surface form match only)."""
    return _SURFACE.get(_fold(tag).replace("_", "-")) or _SURFACE.get(_fold(tag).replace("_", " "))


def find_skills(text: str) -> list[str]:
    """Canonical skill ids mentioned in free text, in sorted order."""
    return sorted({_SURFACE[m.group(1)] for m in _SKILL_RE.finditer(_fold(text))})


def classify_family(title: str) -> str:
    folded = _fold(title)
    for family in FAMILY_ORDER:
        for pat in FAMILIES[family]["titles"]:
            if re.search(r"(?<![a-z0-9])" + re.escape(pat) + r"(?![a-z0-9])", folded):
                return family
    return "unknown"


def _hops() -> dict[tuple[str, str], int]:
    names = list(FAMILIES)
    dist = {(a, b): (0 if a == b else 99) for a in names for b in names}
    for a, b in ADJACENCY:
        dist[(a, b)] = dist[(b, a)] = 1
    for k in names:
        for i in names:
            for j in names:
                if dist[(i, k)] + dist[(k, j)] < dist[(i, j)]:
                    dist[(i, j)] = dist[(i, k)] + dist[(k, j)]
    return dist


_HOPS = _hops()


def family_closeness(a: str, b: str) -> float | None:
    """Prior closeness of two role families in [0,1]; None if either is unknown."""
    if a not in FAMILIES or b not in FAMILIES:
        return None
    return CLOSENESS_BY_HOPS.get(_HOPS[(a, b)], CLOSENESS_FAR)


def count_cues(text: str, cues: tuple[str, ...]) -> int:
    folded = _fold(text)
    return sum(1 for cue in cues if cue in folded)


def taxonomy_version() -> str:
    blob = json.dumps({"skills": SKILLS, "families": FAMILIES, "order": FAMILY_ORDER, "adj": ADJACENCY,
                       "ladder": SENIORITY_LADDER}, sort_keys=True, default=list)
    return "tax-" + hashlib.sha256(blob.encode()).hexdigest()[:12]
