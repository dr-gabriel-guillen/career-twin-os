"""Hostile-input handling for job content: HTML → text, Unicode hygiene, URL checks.

Job descriptions are untrusted *data*. Nothing here interprets them as instructions;
suspected prompt injection is flagged so downstream LLM-based consumers can refuse it.
"""
from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_TEXT_CHARS = 40_000
MAX_URL_CHARS = 2048

FLAG_HIDDEN = "hidden-content-removed"
FLAG_ACTIVE = "active-content-removed"
FLAG_INJECTION = "prompt-injection-suspected"
FLAG_CONTROL = "control-chars-removed"
FLAG_BIDI = "bidi-control-removed"
FLAG_ZERO_WIDTH = "zero-width-removed"
FLAG_TRUNCATED = "text-truncated"
FLAG_FORMULA = "csv-formula-prefix"

_BIDI = set("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")
_ZERO_WIDTH = set("​‌‍⁠﻿­᠎")
_ASCII_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SKIP_TAGS = {"script", "style", "template", "noscript", "iframe", "object", "embed", "head",
              "svg", "math", "form", "textarea", "select", "title", "applet"}
_ACTIVE_TAGS = {"script", "iframe", "object", "embed", "form", "applet", "meta", "link", "base"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
              "source", "track", "wbr"}
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
               "section", "article", "header", "footer", "blockquote", "pre", "hr", "dd", "dt"}
_ZERO = r"(?:0+(?:\.0*)?|\.0+)"
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*(hidden|collapse)"
    r"|font-size\s*:\s*(" + _ZERO + r"(px|pt|em|rem|%)?|[01](\.\d+)?(px|pt))(?![.\d])"
    r"|opacity\s*:\s*" + _ZERO + r"(?![.\d])"
    r"|(left|top|right|text-indent|margin-left|margin-top)\s*:\s*-(\d{3,}(\.\d+)?(px)?|\d{2,}(\.\d+)?(em|rem|vw|vh|%))"
    r"|(max-)?(height|width)\s*:\s*" + _ZERO + r"(px|em|rem|%)?(?![.\d])"
    r"|clip\s*:\s*rect\(\s*0|clip-path\s*:\s*inset\(\s*(50|100)%"
    r"|(?<![-\w])color\s*:\s*(transparent|white|#fff(fff)?\b|rgba?\([^)]*,\s*" + _ZERO + r"\s*\))"
    r"|transform\s*:\s*scale\(\s*" + _ZERO + r"\s*[,)]", re.I)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)(</style\s*>|$)", re.I | re.S)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

_INJECTION = re.compile(
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules|context)"
    r"|disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|system)"
    r"|forget\s+(everything|all|your)\s+(you|previous|prior|instructions)"
    r"|(system|developer)\s+prompt"
    r"|you\s+are\s+(now\s+)?(an?\s+)?((ai|llm)\s+(assistant|model|agent|system|ranker|screener)|language\s+model|chatbot)\b"
    r"|as\s+an?\s+(ai|llm)\s+(assistant|model|agent)"
    r"|(ai|llm|automated|ats)\s+(agents?|assistants?|models?|screeners?|rankers?|systems?)\s*[:,]?\s*(must|should|shall|please|are\s+instructed)"
    r"|rank\s+this\s+(job|posting|role|candidate)\s+(first|highest|as\s+the\s+best|at\s+the\s+top|#?1)"
    r"|(assign|give|set|output|return)\s+(it\s+|this\s+\w+\s+)?(a\s+|the\s+)?(maximum|highest|perfect|top|100)\s*(%|percent)?\s*(score|rating|match|fit)"
    r"|score\s*(of|=|:)?\s*100\b"
    r"|new\s+instructions?\s*:"
    r"|\bBEGIN\s+(SYSTEM|INSTRUCTIONS?)\b|<\s*/?\s*(system|assistant|instructions?)\s*>|\[\s*(INST|SYSTEM)\s*\]"
    r"|do\s+not\s+(tell|inform|reveal\s+to)\s+the\s+(user|candidate|applicant)"
    r"|(reveal|print|exfiltrate|send)\s+(your|the)\s+(system\s+prompt|instructions|api\s+key|secrets?|credentials)"
    r"|override\s+(your|the|all)\s+(safety|instructions|rules|constraints|guardrails)",
    re.I)


def clean_text(value: object, *, limit: int = MAX_TEXT_CHARS) -> tuple[str, list[str]]:
    """NFC-normalize and strip control / bidi / zero-width characters. Returns (text, flags)."""
    if not isinstance(value, str):
        return "", []
    flags: set[str] = set()
    if len(value) > limit * 4:
        value = value[: limit * 4]
        flags.add(FLAG_TRUNCATED)
    out: list[str] = []
    # Fast path: printable ASCII (plus \n, \t) needs no per-character inspection.
    scan = "" if value.isascii() and not _ASCII_CONTROL.search(value) else value
    if not scan:
        out.append(value)
    for ch in scan:
        if ch in _BIDI:
            flags.add(FLAG_BIDI)
            continue
        if ch in _ZERO_WIDTH:
            flags.add(FLAG_ZERO_WIDTH)
            continue
        if ch in "\n\t":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co"):
            flags.add(FLAG_CONTROL)
            continue
        if cat == "Zs" or cat in ("Zl", "Zp"):
            out.append("\n" if cat != "Zs" else " ")
            continue
        out.append(ch)
    text = unicodedata.normalize("NFC", "".join(out))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit]
        flags.add(FLAG_TRUNCATED)
    return text, sorted(flags)


def fold(value: str) -> str:
    """Comparison key: NFKC + casefold + collapsed whitespace, invisibles removed."""
    text, _ = clean_text(value, limit=2000)
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def one_line(value: object, *, limit: int = 300) -> tuple[str, list[str]]:
    text, flags = clean_text(value, limit=limit * 4)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
        flags = sorted(set(flags) | {FLAG_TRUNCATED})
    if text[:1] in ("=", "+", "@") or (text[:1] == "-" and len(text) > 1 and not text[1].isspace()):
        flags = sorted(set(flags) | {FLAG_FORMULA})
    return text, flags


def neutralize_cell(value: str) -> str:
    """Make a string safe to place in a CSV/spreadsheet cell (prefix formula triggers)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class _Extractor(HTMLParser):
    def __init__(self, hidden_classes: frozenset[str] = frozenset(), hidden_ids: frozenset[str] = frozenset()) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_classes, self.hidden_ids = hidden_classes, hidden_ids
        self.parts: list[str] = []
        self.hidden_parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # (tag, suppresses_text)
        self.flags: set[str] = set()

    def _suppressed(self) -> bool:
        return any(flag for _, flag in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = {k.lower(): (v or "") for k, v in attrs}
        if tag in _ACTIVE_TAGS or any(k.startswith("on") for k in amap) or any(
                re.match(r"\s*(javascript|data|vbscript)\s*:", amap.get(k, ""), re.I) for k in ("href", "src", "action")):
            self.flags.add(FLAG_ACTIVE)
        hidden = (tag in _SKIP_TAGS or "hidden" in amap or amap.get("aria-hidden", "").strip().lower() == "true"
                  or bool(_HIDDEN_STYLE.search(amap.get("style", "")))
                  or (tag == "input" and amap.get("type", "").lower() == "hidden")
                  or bool(set(amap.get("class", "").split()) & self.hidden_classes)
                  or amap.get("id", "") in self.hidden_ids)
        if hidden and tag not in _SKIP_TAGS:
            self.flags.add(FLAG_HIDDEN)
        if tag in _BLOCK_TAGS and not self._suppressed():
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")
        if tag not in _VOID_TAGS:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        depth = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[depth:]

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                del self.stack[idx:]
                break
        if tag in _BLOCK_TAGS and not self._suppressed():
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed():
            if data.strip():
                self.hidden_parts.append(data)
                if not any(t in _SKIP_TAGS for t, _ in self.stack):
                    self.flags.add(FLAG_HIDDEN)
        else:
            self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        if data.strip():
            self.hidden_parts.append(data)
            self.flags.add(FLAG_HIDDEN)


def sanitize_html(markup: object, *, entity_escaped: bool = False) -> tuple[str, list[str]]:
    """Convert untrusted HTML to visible plain text.

    Removes scripts, styles, comments and visually hidden elements (a common vector for
    keyword stuffing and prompt injection). Injection phrasing is *flagged* whether it
    appears in visible or hidden text; visible text is kept verbatim as data.
    """
    if not isinstance(markup, str) or not markup:
        return "", []
    flags: set[str] = set()
    if len(markup) > MAX_TEXT_CHARS * 8:
        markup = markup[: MAX_TEXT_CHARS * 8]
        flags.add(FLAG_TRUNCATED)
    if entity_escaped:
        markup = html.unescape(markup)
    hidden_classes: set[str] = set()
    hidden_ids: set[str] = set()
    for block in _STYLE_BLOCK.finditer(markup):
        for selectors, body in _CSS_RULE.findall(block.group(1)):
            if _HIDDEN_STYLE.search(body):
                hidden_classes |= set(re.findall(r"\.([A-Za-z0-9_-]+)", selectors))
                hidden_ids |= set(re.findall(r"#([A-Za-z0-9_-]+)", selectors))
    parser = _Extractor(frozenset(hidden_classes), frozenset(hidden_ids))
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # html.parser is lenient, but never let hostile markup crash ingestion
        flags.add(FLAG_ACTIVE)
    if any(suppress and tag in _SKIP_TAGS for tag, suppress in parser.stack):
        flags.add(FLAG_ACTIVE)  # unterminated <script>/<style> swallowing the rest of the document
    flags |= parser.flags
    text, tflags = clean_text("".join(parser.parts))
    flags |= set(tflags)
    hidden_text, _ = clean_text(" ".join(parser.hidden_parts))
    if detect_injection(text) or detect_injection(hidden_text):
        flags.add(FLAG_INJECTION)
    return text, sorted(flags)


def detect_injection(text: str) -> bool:
    if not text:
        return False
    probe = unicodedata.normalize("NFKC", text)
    probe = "".join(ch for ch in probe if ch not in _ZERO_WIDTH and ch not in _BIDI)
    probe = re.sub(r"\s+", " ", probe)
    return bool(_INJECTION.search(probe))


def url_problem(url: object) -> str | None:
    """Return a reason string if the URL is unsafe to store/display, else None.

    We never fetch URLs, but consumers might; reject SSRF-shaped and non-web targets.
    """
    if not isinstance(url, str) or not url.strip():
        return "empty"
    if len(url) > MAX_URL_CHARS:
        return "too-long"
    if url != url.strip() or any(ord(c) < 0x21 or ord(c) == 0x7F or c in _BIDI or c in _ZERO_WIDTH for c in url):
        return "whitespace-or-control"
    if "\\" in url:
        return "backslash"
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "unparseable"
    if parts.scheme.lower() not in ("http", "https"):
        return "scheme-not-http"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "embedded-credentials"
    if not host:
        return "no-host"
    host = host.rstrip(".").lower()
    if port is not None and port not in (80, 443):
        return "unusual-port"
    try:
        host_ascii = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        return "bad-idn"
    if host_ascii != host:
        return "non-ascii-host"  # homograph risk
    if any(label.startswith("xn--") for label in host.split(".")):
        return "punycode-host"  # IDN homograph risk; no legitimate B01 fixture needs one
    candidate = host.strip("[]")
    try:
        ip = ipaddress.ip_address(candidate)
        if not ip.is_global:
            return "non-public-ip"
        return "ip-literal-host"
    except ValueError:
        pass
    if re.fullmatch(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}", host):
        return "numeric-host"
    if "." not in host:
        return "single-label-host"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".intranet", ".lan", ".home",
                                              ".corp", ".arpa", ".onion", ".svc", ".cluster.local")):
        return "internal-host"
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+", host):
        return "bad-hostname"
    return None
