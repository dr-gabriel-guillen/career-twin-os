"""Strict RFC 3339 ``date-time`` parsing without third-party validators.

Draft 2020-12 treats ``format`` as an annotation unless a format checker is
supplied, and RFC 3339 allows any numeric offset. ``CONTRACTS.md`` requires UTC,
so lane E checks both: syntactic validity (schema layer) and UTC (semantic layer).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?([Zz]|[+-]\d{2}:\d{2})\Z"
)


def parse_rfc3339(value: object) -> datetime | None:
    """Return an aware datetime, or ``None`` when ``value`` is not RFC 3339.

    Leap seconds (``:60``) are rejected: no lane needs them and ``datetime``
    cannot represent them.
    """
    if not isinstance(value, str):
        return None
    match = _RFC3339.match(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = match.group(7)
    micro = int((fraction[1:] + "000000")[:6]) if fraction else 0
    offset = match.group(8)
    if offset in ("Z", "z"):
        tz: timezone = UTC
    else:
        off_h, off_m = int(offset[1:3]), int(offset[4:6])
        if off_h > 23 or off_m > 59:
            return None
        delta = timedelta(hours=off_h, minutes=off_m)
        tz = timezone(-delta if offset[0] == "-" else delta)
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=tz)
    except ValueError:
        return None


def is_rfc3339(value: object) -> bool:
    return parse_rfc3339(value) is not None


def is_utc(value: object) -> bool:
    """True only for an RFC 3339 instant written with ``Z`` or ``+00:00``.

    ``-00:00`` means "offset unknown" in RFC 3339 and is therefore not UTC.
    """
    if not isinstance(value, str) or parse_rfc3339(value) is None:
        return False
    return value[-1] in "Zz" or value.endswith("+00:00")
