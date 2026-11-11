"""Shared primitives: canonical hashing, UTC time, clock injection, text hygiene.

Stdlib only. Nothing in this package opens a network connection.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_TEXT_LEN = 4000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ud800-\udfff]")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")


class CareerTwinError(Exception):
    """Base class; every refusal raised by lane B derives from it."""


class ValidationError(CareerTwinError):
    pass


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every hash in lane B."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def parse_ts(value: str) -> datetime:
    """Parse a strict RFC 3339 timestamp and normalise to aware UTC."""
    if not isinstance(value, str) or not _TS.match(value):
        raise ValidationError(f"not an RFC 3339 timestamp with offset: {value!r}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValidationError(f"invalid timestamp: {value!r}") from exc
    if not (1990 <= dt.year <= 2200):
        raise ValidationError(f"timestamp outside the supported range: {value!r}")
    return dt


def fmt_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValidationError("naive datetime refused; lane B stores UTC only")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    """Wall clock. Replace with FixedClock in tests and simulations."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_ts(self) -> str:
        return fmt_ts(self.now())


class FixedClock(Clock):
    def __init__(self, start: str | datetime):
        self._now = parse_ts(start) if isinstance(start, str) else start

    def now(self) -> datetime:
        return self._now

    def set(self, value: str | datetime) -> None:
        new = parse_ts(value) if isinstance(value, str) else value
        self._now = new

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)


def clean_text(value: Any, *, field: str = "text", max_len: int = MAX_TEXT_LEN) -> str:
    """Normalise untrusted text for storage.

    Stored text stays plain text (it is escaped at render time, see escape_html).
    Control and bidi-override characters are removed, length is bounded.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = _CONTROL.sub("", value).strip()
    if len(cleaned) > max_len:
        raise ValidationError(f"{field} exceeds {max_len} characters")
    return cleaned


def single_line(value: str) -> str:
    """Collapse every line break / whitespace run to one space (used for résumé bullets)."""
    return " ".join(value.split())


def escape_html(value: str) -> str:
    return html.escape(value, quote=True)


_ACTIVE = re.compile(r"<\s*/?\s*[a-zA-Z!]|javascript\s*:|data\s*:\s*text/html|on[a-z]+\s*=", re.IGNORECASE)


def looks_like_markup(value: str) -> bool:
    """Heuristic flag only; safety comes from escaping, never from this check."""
    return bool(_ACTIVE.search(value))


def require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or _CONTROL.search(value) or value != value.strip():
        raise ValidationError(f"{field} must be a non-empty opaque id string")
    return value
