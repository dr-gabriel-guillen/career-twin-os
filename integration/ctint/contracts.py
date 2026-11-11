"""Contract conformance for the six frozen schemas in ``contracts/``.

Three layers, kept separate on purpose:

1. ``schema_errors``     — JSON Schema Draft 2020-12 validation with a strict
                           ``date-time`` format checker.
2. ``semantic_violations`` — invariants stated in ``docs/coordination/CONTRACTS.md``
                           and ``AGENTS.md`` that the frozen schemas do not encode
                           (UTC, approval gate, design lock, no hiring-probability claims).
3. ``audit_schemas``     — static audit of the schema files themselves.

Lane E never edits ``contracts/``; gaps found here are routed as RFCs.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .paths import contracts_dir
from .timeutil import is_utc, parse_rfc3339

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

#: contract name -> (schema file, title declared in the schema)
CONTRACTS: dict[str, tuple[str, str]] = {
    "job-posting": ("job-posting.schema.json", "JobPosting"),
    "profile-claim": ("profile-claim.schema.json", "ProfileClaim"),
    "application-event": ("application-event.schema.json", "ApplicationEvent"),
    "experiment": ("experiment.schema.json", "ExperimentSpec"),
    "atlas-point": ("atlas-point.schema.json", "AtlasPoint"),
    "transition-scenario": ("transition-scenario.schema.json", "TransitionScenario"),
}

#: required free-text / identifier fields that must not be blank (rule ID-001)
NON_BLANK: dict[str, tuple[str, ...]] = {
    "job-posting": ("id", "source_id", "title", "organization", "url"),
    "profile-claim": ("id", "subject_id", "claim_text"),
    "application-event": ("id", "application_id", "job_id", "source"),
    "experiment": ("id", "title", "hypothesis", "primary_metric", "stopping_rule"),
    "atlas-point": ("entity_id", "projection_id", "label", "source_vector_ref"),
    "transition-scenario": ("id", "candidate_ref", "target_role"),
}

#: rule id -> (severity, where the rule comes from)
RULES: dict[str, tuple[str, str]] = {
    "UTC-001": ("error", "CONTRACTS.md: 'UTC ISO-8601 timestamps'"),
    "ID-001": ("error", "CONTRACTS.md: 'Stable opaque IDs' — a blank string is not an ID"),
    "PROB-001": ("error", "Hub #1: never call a score or geometry a probability of being hired"),
    "JOB-URL-001": ("error", "CONTRACTS.md: 'job source URLs' — must be an absolute http(s) URL"),
    "JOB-URL-002": ("advisory", "E proposal: synthetic jobs link only to RFC 2606 reserved hosts"),
    "JOB-COMP-001": ("error", "CONTRACTS.md: unknown stays unknown; min > max is contradictory"),
    "JOB-COMP-002": ("error", "compensation bounds cannot be negative"),
    "JOB-SYN-001": ("error", "AGENTS.md: 'All job fixtures must be explicitly fictional'"),
    "CLAIM-EVID-001": ("error", "CONTRACTS.md: provenance for each claim — 'supported' needs evidence"),
    "APP-APPROVAL-001": ("error", "CONTRACTS.md: submission is an explicit manual user action"),
    "APP-APPROVAL-002": ("error", "AGENTS.md: no auto-apply — 'system' may not record 'submitted'"),
    "APP-TIME-001": ("error", "an event cannot occur after it was recorded"),
    "EXP-LOCK-001": ("error", "Hub #1: preregistration — non-draft experiments carry a design lock"),
    "EXP-VAR-001": ("error", "variant names must be non-blank"),
    "ATLAS-NUM-001": ("error", "coordinates must be finite numbers"),
    "ATLAS-DIST-001": ("error", "CONTRACTS.md: 'All maps should display distortion metrics'"),
    "SCN-ASSUME-001": ("advisory", "a hypothetical scenario should state its assumptions"),
}

SYNTHETIC_HOST_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
SYNTHETIC_HOSTS = ("example.com", "example.org", "example.net")


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    message: str

    @property
    def severity(self) -> str:
        return RULES.get(self.rule, ("error", ""))[0]

    def __str__(self) -> str:
        return f"[{self.rule}/{self.severity}] {self.path or '/'}: {self.message}"


# --------------------------------------------------------------------------- schema layer


def schema_path(name: str, root: Path | None = None) -> Path:
    return contracts_dir(root) / CONTRACTS[name][0]


def load_schema(name: str, root: Path | None = None) -> dict[str, Any]:
    loaded = json.loads(schema_path(name, root).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{name}: schema root must be an object")
    return loaded


def _format_checker() -> FormatChecker:
    checker = FormatChecker(formats=())

    def _date_time(value: object) -> bool:
        # Non-strings are the business of "type", not "format".
        return not isinstance(value, str) or parse_rfc3339(value) is not None

    checker.checks("date-time")(_date_time)
    return checker


@lru_cache(maxsize=64)
def _validator(name: str, root_key: str) -> Draft202012Validator:
    schema = load_schema(name, Path(root_key) if root_key else None)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=_format_checker())


def _pointer(parts: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def schema_errors(name: str, doc: Any, root: Path | None = None) -> list[str]:
    """Draft 2020-12 errors as stable ``<json-pointer>: <message>`` strings."""
    validator = _validator(name, str(root) if root else "")
    found = [f"{_pointer(err.absolute_path) or '/'}: {err.message}" for err in validator.iter_errors(doc)]
    return sorted(found)


# --------------------------------------------------------------------------- probability lint

_WORD = r"(?:probabilit(?:y|ies)|likelihood|chances?|odds)"
_OUTCOME = r"(?:hiring|hired|hire|offers?|interviews?|callbacks?|success|acceptance)"
_NEGATED = re.compile(
    rf"(?i)(?<![a-z])(?:not|no|never|non|isn't|aren't|n't)(?:[\s_-]+[a-z']+){{0,4}}?[\s_-]+{_WORD}"
)
_CLAIM = re.compile(
    rf"(?i){_OUTCOME}[\s_-]*(?:{_WORD}|prob(?![a-z]))"
    rf"|(?<![a-z])p[_-]?(?:hire|offer|interview)(?![a-z])"
    rf"|{_WORD}[\s_-]+(?:of|to)[\s_-]+(?:(?:getting|being|get|be|receiving|an?|the)[\s_-]+){{0,3}}"
    rf"(?:hir|offer|interview|success|callback)"
)


def is_probability_claim(text: str) -> bool:
    """Heuristic: does ``text`` present a hiring/offer probability?

    Explicit negations ("not a hiring probability") are allowed. This is a lint,
    not a proof: creative phrasing can evade it, see THREAT_MODEL.md residuals.
    """
    return _CLAIM.search(_NEGATED.sub(" ", text)) is not None


def probability_claims(doc: Any, path: str = "") -> list[tuple[str, str]]:
    """Every key or string value in ``doc`` that reads as a hiring-probability claim."""
    found: list[tuple[str, str]] = []
    if isinstance(doc, dict):
        for key, value in doc.items():
            child = f"{path}/{key}"
            if isinstance(key, str) and is_probability_claim(key):
                found.append((child, key))
            found.extend(probability_claims(value, child))
    elif isinstance(doc, list):
        for index, value in enumerate(doc):
            found.extend(probability_claims(value, f"{path}/{index}"))
    elif isinstance(doc, str) and is_probability_claim(doc):
        found.append((path, doc[:120]))
    return found


# --------------------------------------------------------------------------- semantic layer


def date_time_fields(name: str, root: Path | None = None) -> list[str]:
    props = load_schema(name, root).get("properties", {})
    return sorted(k for k, v in props.items() if isinstance(v, dict) and v.get("format") == "date-time")


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def _job_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    url = doc.get("url")
    host = ""
    if isinstance(url, str):
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().rstrip(".")
            ok = parts.scheme in ("http", "https") and bool(host)
        except ValueError:
            ok = False
        if not ok:
            yield Violation("JOB-URL-001", "/url", f"not an absolute http(s) URL: {url[:80]!r}")
    comp = doc.get("compensation")
    if isinstance(comp, dict):
        low, high = comp.get("min"), comp.get("max")
        numeric = [v for v in (low, high) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(numeric) == 2 and numeric[0] > numeric[1]:
            yield Violation("JOB-COMP-001", "/compensation", f"min {low} > max {high}")
        if any(v < 0 for v in numeric):
            yield Violation("JOB-COMP-002", "/compensation", "negative bound")
    if doc.get("source") == "synthetic":
        marker = f"{doc.get('organization', '')} {doc.get('license_note', '')}".casefold()
        if "fictional" not in marker and "synthetic" not in marker:
            yield Violation(
                "JOB-SYN-001",
                "/organization",
                "synthetic posting is not marked fictional in organization or license_note",
            )
        if host and not (host.endswith(SYNTHETIC_HOST_SUFFIXES) or host in SYNTHETIC_HOSTS):
            yield Violation("JOB-URL-002", "/url", f"synthetic posting links to non-reserved host {host!r}")


def _claim_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    if doc.get("status") == "supported" and not doc.get("evidence_ids"):
        yield Violation("CLAIM-EVID-001", "/evidence_ids", "status 'supported' with no evidence_ids")


def _event_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    if doc.get("event_type") == "submitted":
        actor = doc.get("actor")
        if actor == "system":
            yield Violation("APP-APPROVAL-002", "/actor", "'submitted' recorded by actor 'system'")
        if actor != "import" and _blank(doc.get("approval_token_ref")):
            yield Violation(
                "APP-APPROVAL-001", "/approval_token_ref", "'submitted' without an approval_token_ref"
            )
    occurred, recorded = parse_rfc3339(doc.get("occurred_at")), parse_rfc3339(doc.get("recorded_at"))
    if occurred is not None and recorded is not None and occurred > recorded:
        yield Violation("APP-TIME-001", "/occurred_at", "occurred_at is later than recorded_at")


def _experiment_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    if doc.get("status") in ("preregistered", "running", "paused", "closed") and not doc.get(
        "design_locked_at"
    ):
        yield Violation("EXP-LOCK-001", "/design_locked_at", f"status {doc.get('status')!r} without a lock")
    variants = doc.get("variants")
    if isinstance(variants, list):
        for index, variant in enumerate(variants):
            if _blank(variant):
                yield Violation("EXP-VAR-001", f"/variants/{index}", "blank variant name")


def _atlas_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    coords = doc.get("coords")
    if isinstance(coords, list):
        for index, value in enumerate(coords):
            if isinstance(value, float) and not math.isfinite(value):
                yield Violation("ATLAS-NUM-001", f"/coords/{index}", f"non-finite coordinate {value!r}")
    quality = doc.get("neighborhood_quality")
    if isinstance(quality, float) and not math.isfinite(quality):
        yield Violation("ATLAS-NUM-001", "/neighborhood_quality", "non-finite quality")
    if doc.get("projection_method") in ("pca", "umap"):
        has_metric = quality is not None or bool(doc.get("fit_metric_ref")) or bool(doc.get("warnings"))
        if not has_metric:
            yield Violation(
                "ATLAS-DIST-001",
                "",
                "projected point carries no neighborhood_quality, fit_metric_ref or warning",
            )


def _scenario_rules(doc: dict[str, Any]) -> Iterator[Violation]:
    if not doc.get("assumptions"):
        yield Violation("SCN-ASSUME-001", "/assumptions", "hypothetical scenario lists no assumptions")


_RULESETS = {
    "job-posting": _job_rules,
    "profile-claim": _claim_rules,
    "application-event": _event_rules,
    "experiment": _experiment_rules,
    "atlas-point": _atlas_rules,
    "transition-scenario": _scenario_rules,
}


def semantic_violations(name: str, doc: Any, root: Path | None = None) -> list[Violation]:
    """Invariants beyond the schema. Safe on arbitrary input (never raises)."""
    if not isinstance(doc, dict):
        return []
    found: list[Violation] = []
    for field in date_time_fields(name, root):
        value = doc.get(field)
        if isinstance(value, str) and parse_rfc3339(value) is not None and not is_utc(value):
            found.append(Violation("UTC-001", f"/{field}", f"timestamp is not UTC: {value!r}"))
    for field in NON_BLANK[name]:
        if field in doc and isinstance(doc[field], str) and not doc[field].strip():
            found.append(Violation("ID-001", f"/{field}", "blank required string"))
    found.extend(_RULESETS[name](doc))
    for path, snippet in probability_claims(doc):
        found.append(Violation("PROB-001", path, f"reads as a hiring-probability claim: {snippet!r}"))
    return found


def check(name: str, doc: Any, root: Path | None = None) -> list[str]:
    """Blocking problems only: schema errors plus error-severity semantic violations."""
    errors = [f"[SCHEMA] {e}" for e in schema_errors(name, doc, root)]
    errors.extend(str(v) for v in semantic_violations(name, doc, root) if v.severity == "error")
    return errors


# --------------------------------------------------------------------------- schema audit

_OWNER_FIELDS = {"subject_id", "user_id", "owner_id", "candidate_ref", "tenant_id", "account_id"}
_ID_VERSION = re.compile(r"\.v\d+\.\d+\.schema\.json$")


def audit_schemas(root: Path | None = None) -> list[Violation]:
    """Static findings about the schema files. Paths are ``<contract>#<pointer>``."""
    found: list[Violation] = []
    seen_ids: dict[str, str] = {}
    for name, (filename, title) in CONTRACTS.items():
        schema = load_schema(name, root)
        if schema.get("$schema") != DRAFT_2020_12:
            found.append(Violation("SCHEMA-META-001", f"{name}#/$schema", "not Draft 2020-12"))
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            found.append(Violation("SCHEMA-META-002", f"{name}#", f"invalid schema: {exc.message}"))
        if schema.get("title") != title:
            found.append(Violation("SCHEMA-TITLE-001", f"{name}#/title", f"expected title {title!r}"))
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            found.append(
                Violation("SCHEMA-ID-001", f"{name}#/$id", "no $id, so no machine-readable identity/version")
            )
        else:
            stem = filename.removesuffix(".schema.json")
            if not _ID_VERSION.search(schema_id) or f"/{stem}.v" not in schema_id:
                found.append(
                    Violation("SCHEMA-ID-002", f"{name}#/$id", f"unversioned or misnamed: {schema_id}")
                )
            if schema_id in seen_ids:
                found.append(Violation("SCHEMA-ID-003", f"{name}#/$id", f"duplicates {seen_ids[schema_id]}"))
            seen_ids[schema_id] = name
        if schema.get("additionalProperties") is not False:
            found.append(Violation("SCHEMA-OPEN-001", f"{name}#", "top level allows additional properties"))
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", []))
        for prop, spec in sorted(props.items()):
            if not isinstance(spec, dict):
                continue
            types = spec.get("type")
            type_list = types if isinstance(types, list) else [types]
            free_string = type_list == ["string"] and not ({"enum", "const", "minLength"} & set(spec))
            if prop in required and free_string and spec.get("format") != "date-time":
                found.append(
                    Violation("SCHEMA-MINLEN-001", f"{name}#/properties/{prop}", "required string accepts ''")
                )
            if spec.get("format") == "date-time" and "pattern" not in spec:
                found.append(
                    Violation(
                        "SCHEMA-UTC-001", f"{name}#/properties/{prop}", "date-time does not require UTC"
                    )
                )
            if "object" in type_list and "properties" not in spec:
                found.append(
                    Violation("SCHEMA-OPEN-002", f"{name}#/properties/{prop}", "untyped open object")
                )
        # A job posting is market data, not user data; every other contract describes one person's records.
        if name != "job-posting" and not (_OWNER_FIELDS & set(props)):
            found.append(
                Violation(
                    "SCHEMA-OWNER-001", f"{name}#/properties", "no subject/owner field for export isolation"
                )
            )
    return sorted(found, key=lambda v: (v.rule, v.path))
