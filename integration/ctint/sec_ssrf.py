"""Job-source URL / SSRF classifier (threat T-04).

A connector that fetches a job-source URL supplied by a feed or a user must
refuse URLs that resolve to loopback, private, link-local or cloud-metadata
addresses. This module is an *offline lexical oracle*: it never resolves DNS,
so a public name that later resolves to a private address (DNS rebinding) is a
documented residual that the fetching lane must handle at connect time.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("https",)
_INTERNAL_SUFFIXES = (
    ".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp", ".localdomain", ".home.arpa",
)  # fmt: skip
_INTERNAL_NAMES = (
    "localhost",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "kubernetes.default.svc",
)
_NUMERIC_PART = re.compile(r"^(0[xX][0-9a-fA-F]*|[0-9]+)$")


@dataclass(frozen=True)
class UrlVerdict:
    url: str
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return f"{'ALLOW' if self.allowed else 'DENY'} {self.url!r} {', '.join(self.reasons)}"


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse the forms ``inet_aton`` accepts: ``2130706433``, ``0x7f.1``, ``0177.0.0.1``..."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(_NUMERIC_PART.match(p) for p in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                numbers.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part.startswith("0"):
                numbers.append(int(part, 8))
            else:
                numbers.append(int(part, 10))
        except ValueError:
            return None
    *heads, tail = numbers
    if any(h > 255 for h in heads) or tail >= 256 ** (4 - len(heads)):
        return None
    value = tail
    for index, head in enumerate(heads):
        value += head << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _ip_reasons(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[str]:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if mapped is not None:
            return [f"ipv6-embeds-ipv4:{r}" for r in _ip_reasons(mapped)] or ["ipv6-embedded-ipv4"]
    reasons = []
    if ip.is_loopback:
        reasons.append("loopback")
    if ip.is_link_local:
        reasons.append("link-local/metadata")
    if ip.is_private:
        reasons.append("private-range")
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        reasons.append("non-routable")
    if not ip.is_global and not reasons:
        reasons.append("not-global")
    return reasons


def classify_url(url: str) -> UrlVerdict:
    """Decide whether a connector may fetch ``url``. Deny by default."""
    reasons: list[str] = []
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(ord(c) < 0x20 or c == "\x7f" for c in url)
    ):
        return UrlVerdict(str(url), False, ("malformed-or-control-characters",))
    if "\\" in url:
        reasons.append("backslash-in-url")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return UrlVerdict(url, False, ("unparseable",))
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        reasons.append(f"scheme:{parts.scheme.lower() or 'none'}")
    if not host:
        return UrlVerdict(url, False, (*reasons, "no-host"))
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        reasons.append("userinfo-in-authority")
    if port not in (None, 443):
        reasons.append(f"non-default-port:{port}")
    name = host.lower().rstrip(".")
    try:
        ascii_name = name.encode("idna").decode("ascii") if not name.isascii() else name
    except UnicodeError:
        ascii_name = name
        reasons.append("non-idna-hostname")
    if not name.isascii() or "xn--" in ascii_name:
        reasons.append("idn-homograph-risk")
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        address = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        address = _legacy_ipv4(name)
        if address is not None:
            reasons.append("obfuscated-ipv4-literal")
    if address is not None:
        ip_reasons = _ip_reasons(address)
        reasons.extend(ip_reasons or ["ip-literal"])
    else:
        if name in _INTERNAL_NAMES or name.endswith(_INTERNAL_SUFFIXES):
            reasons.append("internal-hostname")
        if "." not in name:
            reasons.append("single-label-hostname")
    return UrlVerdict(url, not reasons, tuple(reasons))


#: URLs a job-source fetcher must refuse.
ATTACK_URLS: tuple[str, ...] = (
    "http://jobs.fictional-meridian.example/feed.json",
    "https://127.0.0.1/feed",
    "https://localhost/feed",
    "https://LOCALHOST./feed",
    "https://[::1]/feed",
    "https://[::ffff:127.0.0.1]/feed",
    "https://[::ffff:169.254.169.254]/latest/meta-data/",
    "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "https://metadata.google.internal/computeMetadata/v1/",
    "https://10.0.0.5/feed",
    "https://172.16.4.1/feed",
    "https://192.168.1.10/feed",
    "https://100.64.0.1/feed",
    "https://0.0.0.0/feed",
    "https://2130706433/feed",
    "https://0x7f000001/feed",
    "https://0x7f.1/feed",
    "https://0177.0.0.1/feed",
    "https://127.1/feed",
    "https://[fd00::1]/feed",
    "https://[fe80::1%25eth0]/feed",
    "https://jobs.fictional-meridian.example@127.0.0.1/feed",
    "https://user:pw@jobs.fictional-meridian.example/feed",
    "https://jobs.fictional-meridian.example:8443/feed",
    "https://jobs.fictional-meridian.example:22/",
    "https://intranet/feed",
    "https://build.corp/feed",
    "https://printer.local/feed",
    "https://kubernetes.default.svc/api",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_FLUSHALL",
    "ftp://jobs.fictional-meridian.example/feed",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//169.254.169.254/latest",
    "https:\\\\127.0.0.1\\feed",
    "https://jobs.fictional-meridian.example\\@127.0.0.1/feed",
    "https://jobs.fictional-meridian.example/feed\r\nHost: 127.0.0.1",
    " https://jobs.fictional-meridian.example/feed",
    "https://\u0440\u0430ypal.example/feed",
    "https://xn--80ak6aa92e.example/feed",
    "",
)
#: URLs that are lexically acceptable (still subject to connect-time checks).
BENIGN_URLS: tuple[str, ...] = (
    "https://jobs.fictional-meridian.example/feed.json",
    "https://boards.fictional-quartz.example/v1/boards/quartz/jobs?content=true",
    "https://jobs.fictional-meridian.example:443/feed.json",
    "https://JOBS.Fictional-Meridian.EXAMPLE/feed.json",
    "https://203.0.113.10.fictional-meridian.example/feed",
    "https://8.8.8.8.example/feed",
)
