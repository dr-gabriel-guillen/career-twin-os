"""Secret / PII / private-fixture scanner for a PUBLIC, synthetic-only repository.

Usage::

    python -m ctint.sec_scan --tracked            # scan every git-tracked file
    python -m ctint.sec_scan path/to/dir ...      # scan given files or directories

Exit status 1 when anything is found. Excerpts are redacted so that the scanner's
own output can be pasted into a public Issue without leaking what it found.
A line can be exempted for one rule with ``ct-scan: allow=<RULE>`` (reviewable in diffs).
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2_000_000
#: never descended into when scanning a directory (``--tracked`` ignores them anyway)
SKIP_DIRS = frozenset(
    {".git", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__", "node_modules", ".venv"}
)

SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS-ACCESS-KEY", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    ("GITHUB-TOKEN", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}")),
    ("ANTHROPIC-KEY", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OPENAI-KEY", re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("GOOGLE-API-KEY", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("SLACK-TOKEN", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("STRIPE-LIVE-KEY", re.compile(r"[sr]k_live_[A-Za-z0-9]{16,}")),
    ("PRIVATE-KEY-BLOCK", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
)
_GENERIC = re.compile(
    r"(?i)(?:api[_-]?key|secret|token|passwd|password|client[_-]?secret|auth)[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"
)
_PLACEHOLDER = re.compile(
    r"(?i)example|placeholder|changeme|synthetic|dummy|redacted|x{4,}|\*{4,}|<|\$\{|%\("
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "users.noreply.github.com",
    "anthropic.com",
)
ALLOWED_EMAIL_SUFFIXES = (".example", ".invalid", ".test", ".localhost")
ALLOWED_EMAILS = ("git@github.com", "noreply@github.com")
_PHONE = re.compile(r"(?<![\d.-])(?:\+?1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\d.-])")
_FICTIONAL_PHONE = re.compile(r"555[\s.-]01\d{2}$")
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_LINKEDIN_PROFILE = re.compile(r"(?i)linkedin\.com/(?:in|pub)/[A-Za-z0-9%_-]{3,}")
# A machine's home directory names a real account. Lane E nearly committed one (a Windows temp path copied
# from a stage message into HANDOFF.md); generic placeholders such as <user>, %USERNAME%, $USER pass.
_LOCAL_HOME_PATH = re.compile(
    r"(?i)\b[a-z]:[\\/]+users[\\/]+(?!public\b|default\b|all users\b|runneradmin\b)[a-z0-9._-]{2,}"
    r"|(?<![\w.-])/home/(?!runner\b|user\b|ubuntu\b)[a-z_][a-z0-9_.-]{1,}"
    r"|(?<![\w.-])/users/(?!shared\b)[a-z0-9._-]{2,}/"
    r"|(?<![\w.-])/c/users/(?!public\b)[a-z0-9._-]{2,}"
)

FORBIDDEN_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PATH-DOTENV", re.compile(r"(^|/)\.env(\.(?!example$)[^/]+)?$")),
    ("PATH-KEY-MATERIAL", re.compile(r"(?i)\.(pem|key|p12|pfx|keystore|jks)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$")),
    ("PATH-PRIVATE-DIR", re.compile(r"(?i)(^|/)(personal|private|exports|\.data)/")),
    ("PATH-LINKEDIN-EXPORT", re.compile(
        r"(?i)linkedin[-_ ]?(data)?[-_ ]?export|(^|/)(Connections|Positions|Profile|Invitations|messages)\.csv$"
    )),
    ("PATH-PRIVATE-RESUME", re.compile(r"(?i)resume[-_]private|(^|/)(cv|resume|résumé)[^/]*\.(pdf|docx?|pages|rtf|odt)$")),
    ("PATH-DOCUMENT-BINARY", re.compile(r"(?i)\.(pdf|docx?|pages|rtf|odt|xlsx?|pptx?)$")),
    ("PATH-DATABASE", re.compile(r"(?i)\.(sqlite3?|db|mdb|dump)$")),
)  # fmt: skip

_PRAGMA = re.compile(r"ct-scan:\s*allow=([A-Z0-9-]+)")


@dataclass(frozen=True)
class ScanFinding:
    rule: str
    path: str
    line: int
    excerpt: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.rule} {where} {self.excerpt}"


def _redact(text: str) -> str:
    return f"{text[:4]}…[{len(text)} chars]"


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _email_allowed(address: str, domain: str) -> bool:
    domain = domain.lower()
    return (
        address.lower() in ALLOWED_EMAILS
        or domain in ALLOWED_EMAIL_DOMAINS
        or domain.endswith(ALLOWED_EMAIL_SUFFIXES)
    )


def scan_path_name(path: str) -> list[ScanFinding]:
    posix = path.replace("\\", "/")
    return [
        ScanFinding(rule, posix, 0, "forbidden path")
        for rule, pattern in FORBIDDEN_PATHS
        if pattern.search(posix)
    ]


def scan_text(path: str, text: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        allowed = set(_PRAGMA.findall(line))
        hits: list[tuple[str, str]] = []
        for rule, pattern in SECRET_RULES:
            hits.extend((rule, m.group(0)) for m in pattern.finditer(line))
        for match in _GENERIC.finditer(line):
            value = match.group(1)
            if not _PLACEHOLDER.search(value) and _entropy(value) >= 3.5:
                hits.append(("GENERIC-SECRET", value))
        for match in _EMAIL.finditer(line):
            if not _email_allowed(match.group(0), match.group(1)):
                hits.append(("PII-EMAIL", match.group(0)))
        for match in _PHONE.finditer(line):
            if not _FICTIONAL_PHONE.search(match.group(0)):
                hits.append(("PII-PHONE", match.group(0)))
        hits.extend(("PII-SSN", m.group(0)) for m in _SSN.finditer(line))
        hits.extend(("PII-LINKEDIN-PROFILE", m.group(0)) for m in _LINKEDIN_PROFILE.finditer(line))
        hits.extend(("PII-LOCAL-PATH", m.group(0)) for m in _LOCAL_HOME_PATH.finditer(line))
        for rule, value in hits:
            if rule not in allowed:
                findings.append(ScanFinding(rule, path.replace("\\", "/"), number, _redact(value)))
    return findings


def scan_file(root: Path, rel: str) -> list[ScanFinding]:
    findings = scan_path_name(rel)
    target = root / rel
    try:
        if not target.is_file() or target.stat().st_size > MAX_BYTES:
            return findings
        raw = target.read_bytes()
    except OSError as exc:
        return [*findings, ScanFinding("SCAN-UNREADABLE", rel, 0, type(exc).__name__)]
    if b"\x00" in raw[:8192]:
        return findings
    return [*findings, *scan_text(rel, raw.decode("utf-8", errors="replace"))]


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8", errors="replace")
    return [p for p in out.split("\x00") if p]


def expand(root: Path, targets: Iterable[str]) -> list[str]:
    files: list[str] = []
    for target in targets:
        path = (root / target).resolve()
        if path.is_dir():
            files.extend(
                str(p.relative_to(root)).replace("\\", "/")
                for p in sorted(path.rglob("*"))
                if p.is_file() and not (SKIP_DIRS & set(p.relative_to(root).parts))
            )
        else:
            files.append(str(path.relative_to(root)).replace("\\", "/"))
    return files


def scan(root: Path, rel_paths: Iterable[str]) -> list[ScanFinding]:
    return [finding for rel in rel_paths for finding in scan_file(root, rel)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="*", help="files or directories relative to --root")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--tracked", action="store_true", help="scan all git-tracked files")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    files = tracked_files(root) if args.tracked else expand(root, args.targets or ["."])
    findings = scan(root, files)
    for finding in findings:
        print(finding)
    print(f"sec_scan: {len(files)} files scanned, {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
