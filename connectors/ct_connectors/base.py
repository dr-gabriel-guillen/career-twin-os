"""Connector primitives: source records, issue classification, offline fetchers.

Nothing in this package opens a network socket. Adapters are pure functions from
an already-captured payload to :class:`SourceRecord` objects plus classified issues.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

CONNECTOR_API_VERSION = "ct-connectors/0.1.0"

#: Values allowed by contracts/job-posting.schema.json#/properties/source
CONTRACT_SOURCES = ("synthetic", "greenhouse", "lever", "ashby", "user-import", "other-permitted")


class ErrorClass:
    """Stable machine-readable error classes (strings, so they survive JSON)."""

    MALFORMED_PAYLOAD = "malformed_payload"          # payload is not the documented shape at all
    MALFORMED_RECORD = "malformed_record"            # one job entry is not an object / wrong types
    MISSING_REQUIRED_FIELD = "missing_required_field"
    UNSAFE_URL = "unsafe_url"
    UNSUPPORTED_SOURCE = "unsupported_source"
    BAD_CAPTURE_METADATA = "bad_capture_metadata"
    NETWORK_DISABLED = "network_disabled"
    OVERSIZED_FIELD = "oversized_field"
    DUPLICATE_SOURCE_ID = "duplicate_source_id"
    # warnings (record still accepted)
    TIMESTAMP_UNPARSEABLE = "timestamp_unparseable"
    TIME_ANOMALY = "time_anomaly"
    COMPENSATION_UNPARSEABLE = "compensation_unparseable"
    CONTENT_SANITIZED = "content_sanitized"

    REJECTING = frozenset({MALFORMED_PAYLOAD, MALFORMED_RECORD, MISSING_REQUIRED_FIELD, UNSAFE_URL,
                           UNSUPPORTED_SOURCE, BAD_CAPTURE_METADATA, NETWORK_DISABLED,
                           OVERSIZED_FIELD, DUPLICATE_SOURCE_ID})


class ConnectorError(Exception):
    """Raised only for whole-payload failures; carries an ErrorClass."""

    def __init__(self, error_class: str, detail: str):
        super().__init__(f"{error_class}: {detail}")
        self.error_class = error_class
        self.detail = detail


@dataclass(frozen=True)
class Issue:
    error_class: str
    severity: str  # "reject" | "warn"
    source: str
    source_ref: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reject(error_class: str, source: str, ref: Any, detail: str) -> Issue:
    return Issue(error_class, "reject", source, _short(ref), _short(detail, 300))


def warn(error_class: str, source: str, ref: Any, detail: str) -> Issue:
    return Issue(error_class, "warn", source, _short(ref), _short(detail, 300))


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Capture:
    """Metadata about *how and when* a payload was obtained. Never guessed."""

    source: str
    board: str
    captured_at: str            # UTC ISO-8601, validated
    organization: str | None = None
    org_uid: str | None = None  # optional registry id that disambiguates employer aliases
    license_note: str = ""
    synthetic: bool = False
    full_snapshot: bool = True  # True when payload lists *all* open postings of the board

    @classmethod
    def from_dict(cls, raw: Any) -> "Capture":
        if not isinstance(raw, dict):
            raise ConnectorError(ErrorClass.BAD_CAPTURE_METADATA, "capture must be an object")
        source = raw.get("source")
        if source not in CONTRACT_SOURCES:
            raise ConnectorError(ErrorClass.UNSUPPORTED_SOURCE, f"source {source!r} not in contract enum")
        board = raw.get("board")
        if not isinstance(board, str) or not board.strip():
            raise ConnectorError(ErrorClass.BAD_CAPTURE_METADATA, "capture.board must be a non-empty string")
        ts = parse_ts(raw.get("captured_at"))
        if ts is None:
            raise ConnectorError(ErrorClass.BAD_CAPTURE_METADATA, "capture.captured_at must be a tz-aware timestamp")
        org = raw.get("organization")
        uid = raw.get("org_uid")
        return cls(source=source, board=board.strip(), captured_at=iso(ts),
                   organization=org if isinstance(org, str) and org.strip() else None,
                   org_uid=uid if isinstance(uid, str) and uid.strip() else None,
                   license_note=str(raw.get("license_note") or ""),
                   synthetic=raw.get("synthetic") is True,
                   full_snapshot=raw.get("full_snapshot", True) is True)


@dataclass
class SourceRecord:
    """One posting exactly as the source stated it, lightly typed. No inference."""

    source: str
    board: str
    source_id: str
    title: str
    organization: str
    url: str
    captured_at: str
    org_uid: str | None = None
    apply_url: str | None = None
    posted_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    listed: bool | None = None            # None = source did not say
    location_raw: str | None = None
    country_raw: str | None = None
    remote_raw: str | None = None         # source's own wording, e.g. "on-site", "Remote"
    employment_raw: str | None = None
    seniority_raw: str | None = None
    department: str | None = None
    description_text: str = ""            # sanitized plain text (see textsafe)
    declared_skills: list[str] = field(default_factory=list)
    compensation_raw: list[dict[str, Any]] = field(default_factory=list)
    security_flags: list[str] = field(default_factory=list)
    license_note: str = ""
    synthetic: bool = False
    payload_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParseResult:
    capture: Capture
    records: list[SourceRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    seen_source_ids: list[str] = field(default_factory=list)  # every id present, even rejected ones

    @property
    def rejected(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "reject"]


class Adapter(Protocol):
    source: str

    def parse(self, payload: Any, capture: Capture) -> ParseResult: ...


# ---------------------------------------------------------------- time helpers
def parse_ts(value: Any) -> datetime | None:
    """Parse a timestamp into aware UTC. Naive or garbage values return None (unknown)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Epoch milliseconds (Lever). Accept only a sane window 2000-01-01 .. 2100-01-01.
        if 946684800000 <= value <= 4102444800000:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).replace(microsecond=0)
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 40:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # naive timestamps are ambiguous -> unknown
    try:
        return dt.astimezone(timezone.utc).replace(microsecond=0)
    except (OverflowError, ValueError):
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_or_none(value: Any) -> str | None:
    dt = parse_ts(value)
    return iso(dt) if dt else None


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------- fetchers
class FixtureFetcher:
    """Loads a recorded capture envelope ``{"capture": {...}, "payload": ...}`` from disk."""

    MAX_BYTES = 64 * 1024 * 1024

    def load(self, path: str | Path) -> tuple[Capture, Any]:
        p = Path(path)
        if p.stat().st_size > self.MAX_BYTES:
            raise ConnectorError(ErrorClass.OVERSIZED_FIELD, f"{p.name} exceeds {self.MAX_BYTES} bytes")
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, f"{p.name}: {exc}") from exc
        return split_envelope(doc)


def split_envelope(doc: Any) -> tuple[Capture, Any]:
    if not isinstance(doc, dict) or "capture" not in doc or "payload" not in doc:
        raise ConnectorError(ErrorClass.MALFORMED_PAYLOAD, "expected {'capture':…, 'payload':…} envelope")
    return Capture.from_dict(doc["capture"]), doc["payload"]


class LiveFetchDisabled:
    """Placeholder for a future, owner-authorized live fetcher. Always refuses."""

    def load(self, *_args: Any, **_kwargs: Any) -> tuple[Capture, Any]:
        raise ConnectorError(ErrorClass.NETWORK_DISABLED,
                             "live ATS fetching is disabled: owner authorization required (AGENTS.md §Safety)")
