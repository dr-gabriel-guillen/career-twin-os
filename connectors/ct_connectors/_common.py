"""Shared record-building logic for all adapters."""
from __future__ import annotations

import math
from typing import Any, Iterator

from .base import (Capture, ConnectorError, ErrorClass, Issue, ParseResult, SourceRecord, canonical_json, reject,
                   sha256_hex, ts_or_none, warn)
from .textsafe import FLAG_FORMULA, FLAG_INJECTION, clean_text, detect_injection, one_line, sanitize_html, url_problem

MAX_JOBS_PER_PAYLOAD = 50_000
_PERIODS = {
    "year": "year", "yearly": "year", "annual": "year", "annually": "year", "per-year-salary": "year", "1 year": "year",
    "hour": "hour", "hourly": "hour", "per-hour-wage": "hour", "1 hour": "hour",
    "month": "month", "monthly": "month", "per-month-salary": "month", "1 month": "month",
    "week": "week", "weekly": "week", "1 week": "week", "day": "day", "daily": "day", "1 day": "day",
}
_TS_FIELDS = ("posted_at", "updated_at", "expires_at")


def norm_period(value: Any) -> str:
    """Map a source's pay-interval wording to year/hour/month/week/day, else 'unknown'."""
    if not isinstance(value, str):
        return "unknown"
    key = value.strip().lower().replace("_", " ")
    if key in _PERIODS:
        return _PERIODS[key]
    found = {period for word, period in (("annual", "year"), ("year", "year"), ("hour", "hour"), ("month", "month"),
                                         ("week", "week"), ("daily", "day")) if word in key}
    # "Hourly base rate (first year)" names two periods: ambiguous, so unknown (never annual by accident).
    return found.pop() if len(found) == 1 else "unknown"


def number(value: Any) -> float | None:
    """A finite, non-negative real number or None. Strings are *not* coerced."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0 or value > 1e12:
        return None
    return float(value)


def comp_item(lo: Any, hi: Any, currency: Any, period: Any, basis: Any = "unknown") -> dict[str, Any] | None:
    lo_n, hi_n = number(lo), number(hi)
    if lo_n is None and hi_n is None:
        return None
    cur = None
    if isinstance(currency, str) and len(currency.strip()) == 3 and currency.strip().isascii() and currency.strip().isalpha():
        cur = currency.strip().upper()
    return {"min": lo_n, "max": hi_n, "currency": cur, "period": norm_period(period),
            "basis": basis if basis in ("base", "total") else "unknown", "origin": "structured-field"}


def field_text(value: Any, limit: int = 200) -> tuple[str, list[str]]:
    """One-line text for a structured field: markup reduced to visible text, injection flagged."""
    if not isinstance(value, str):
        return "", []
    flags: set[str] = set()
    if "<" in value and ">" in value:
        value, html_flags = sanitize_html(value)
        flags |= set(html_flags)
    text, line_flags = one_line(value, limit=limit)
    flags |= set(line_flags)
    if detect_injection(text):
        flags.add(FLAG_INJECTION)
    return text, sorted(flags)


def text_field(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text, _ = one_line(value, limit=200)
    return text or None


def build_record(capture: Capture, raw: Any, issues: list[Issue], *, source_id: Any, title: Any, url: Any,
                 description_html: Any = None, description_plain: Any = None, entity_escaped: bool = False,
                 organization: Any = None, **fields: Any) -> SourceRecord | None:
    """Validate the mandatory fields, sanitize text, and build a SourceRecord (or classify a rejection)."""
    src = capture.source
    if isinstance(source_id, bool) or not isinstance(source_id, (str, int)) or str(source_id).strip() == "":
        issues.append(reject(ErrorClass.MISSING_REQUIRED_FIELD, src, "?", "posting id missing or not a string/integer"))
        return None
    sid = str(source_id).strip()
    if len(sid) > 200 or any(not ch.isprintable() for ch in sid):
        issues.append(reject(ErrorClass.MALFORMED_RECORD, src, sid, "posting id too long or non-printable"))
        return None
    flags: set[str] = set()
    title_text, tflags = field_text(title, limit=300)
    flags |= set(tflags)
    if not title_text:
        issues.append(reject(ErrorClass.MISSING_REQUIRED_FIELD, src, sid, "title missing or empty"))
        return None
    problem = url_problem(url)
    if problem:
        cls = ErrorClass.MISSING_REQUIRED_FIELD if problem == "empty" else ErrorClass.UNSAFE_URL
        issues.append(reject(cls, src, sid, f"posting url rejected: {problem}"))
        return None
    apply_url = fields.pop("apply_url", None)
    if apply_url is not None and url_problem(apply_url):
        issues.append(warn(ErrorClass.UNSAFE_URL, src, sid, f"apply_url dropped: {url_problem(apply_url)}"))
        apply_url = None
    org_source = organization if isinstance(organization, str) and organization.strip() else capture.organization
    org_text, oflags = field_text(org_source, limit=200)
    flags |= set(oflags) - {FLAG_FORMULA}
    if not org_text:
        issues.append(reject(ErrorClass.MISSING_REQUIRED_FIELD, src, sid, "organization unknown (not in payload or capture)"))
        return None
    if isinstance(description_html, str) and description_html.strip():
        desc, dflags = sanitize_html(description_html, entity_escaped=entity_escaped)
    else:
        desc, dflags = clean_text(description_plain)
        if detect_injection(desc):
            dflags = sorted(set(dflags) | {FLAG_INJECTION})
    flags |= set(dflags)
    if detect_injection(title_text) or detect_injection(org_text):
        flags.add(FLAG_INJECTION)
    for key in _TS_FIELDS:
        raw_ts = fields.get(key)
        fields[key] = ts_or_none(raw_ts)
        if raw_ts not in (None, "") and fields[key] is None:
            issues.append(warn(ErrorClass.TIMESTAMP_UNPARSEABLE, src, sid, f"{key} not a tz-aware timestamp; kept unknown"))
    comp_in = fields.pop("compensation_raw", None) or []
    comp = [c for c in comp_in if c]
    if len(comp) < len(comp_in):
        issues.append(warn(ErrorClass.COMPENSATION_UNPARSEABLE, src, sid, "a structured pay entry had no usable numeric bounds; dropped"))
    skills_in = fields.pop("declared_skills", None) or []
    skills = []
    for raw_skill in skills_in[:200]:
        text, sflags = field_text(raw_skill)
        flags |= set(sflags) - {FLAG_FORMULA}
        if text and FLAG_INJECTION not in sflags:
            skills.append(text)
    skills = skills[:100]
    listed = fields.pop("listed", None)
    plain = {}
    for key, value in fields.items():
        if key in _TS_FIELDS:
            plain[key] = value
            continue
        text, fflags = field_text(value)
        flags |= set(fflags) - {FLAG_FORMULA}
        plain[key] = text or None
    if flags:
        issues.append(warn(ErrorClass.CONTENT_SANITIZED, src, sid, ",".join(sorted(flags))))
    return SourceRecord(
        source=src, board=capture.board, source_id=sid, title=title_text, organization=org_text, url=url,
        captured_at=capture.captured_at, org_uid=capture.org_uid, apply_url=apply_url,
        listed=listed if isinstance(listed, bool) else None,
        description_text=desc, declared_skills=skills, compensation_raw=comp, security_flags=sorted(flags),
        license_note=capture.license_note, synthetic=capture.synthetic,
        payload_sha256=sha256_hex(_safe_json(raw)), **plain)


def _safe_json(raw: Any) -> str:
    try:
        return canonical_json(raw)
    except (TypeError, ValueError, RecursionError):
        return repr(type(raw))


def iter_jobs(payload: Any, source: str, issues: list[Issue], container_key: str | None) -> Iterator[tuple[int, dict]]:
    """Yield (index, job_dict) for well-formed entries; classify everything else."""
    jobs = payload
    if container_key is not None:
        if not isinstance(payload, dict) or not isinstance(payload.get(container_key), list):
            raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, f"{source}: expected object with list '{container_key}'")
        jobs = payload[container_key]
    elif not isinstance(payload, list):
        raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, f"{source}: expected a JSON array of postings")
    if len(jobs) > MAX_JOBS_PER_PAYLOAD:
        raise ConnectorError(ErrorClass.OVERSIZED_FIELD, f"{source}: {len(jobs)} postings exceeds limit")
    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            issues.append(reject(ErrorClass.MALFORMED_RECORD, source, f"index {idx}",
                                 f"entry is {type(job).__name__}, not object"))
            continue
        yield idx, job


def finish(capture: Capture, records: list[SourceRecord], issues: list[Issue], seen: list[str]) -> ParseResult:
    """Drop repeated source ids inside one payload (keep the first, classify the rest)."""
    out: list[SourceRecord] = []
    ids: set[str] = set()
    for rec in records:
        if rec.source_id in ids:
            issues.append(reject(ErrorClass.DUPLICATE_SOURCE_ID, capture.source, rec.source_id,
                                 "source id repeated within one payload; later entry ignored"))
            continue
        ids.add(rec.source_id)
        out.append(rec)
    return ParseResult(capture=capture, records=out, issues=issues, seen_source_ids=sorted(set(seen)))


def seen_id(job: dict) -> str | None:
    value = job.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip() or None


def get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []
