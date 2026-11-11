"""SourceRecord → JobRecord envelope ``{"posting": <frozen JobPosting v0.1>, "ext": {...}}``.

Design rule: a value is only emitted when the source stated it. Anything missing,
ambiguous or contradictory is recorded as unknown plus a machine-readable reason.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from ct_connectors.base import SourceRecord, canonical_json, parse_ts, sha256_hex
from ct_connectors.textsafe import fold

from . import taxonomy

EXT_VERSION = "a-ext/0.1"
FRESHNESS_TTL_DAYS = 14
LONG_OPEN_DAYS = 180
MAX_SKILLS = 25

_LEGAL_SUFFIX = re.compile(r"[\s,]+(inc|incorporated|llc|ltd|limited|gmbh|corp|corporation|co|company|plc|s\.?a\.?|ag|bv|pty|holdings)\.?$")
_SENIORITY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(intern|internship|working student)\b", "intern"),
    (r"\b(chief \w+ officer|c[eit]o)\b", "executive"),
    (r"\b(vp|vice president|svp)\b", "vp"),
    (r"\b(director|head of)\b", "director"),
    (r"\b(principal|distinguished|fellow)\b", "principal"),
    (r"\b(staff|lead)\b", "staff"),
    (r"\b(senior|sr)\b|\biii\b", "senior"),
    (r"\b(junior|jr|entry[- ]level|associate|graduate)\b|\bi\b", "junior"),
    (r"\b(mid[- ]level|intermediate)\b|\bii\b", "mid"),
)
_COUNTRIES = {
    "united states": "US", "united states of america": "US", "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB", "germany": "DE", "deutschland": "DE",
    "canada": "CA", "france": "FR", "spain": "ES", "netherlands": "NL", "ireland": "IE", "india": "IN",
    "singapore": "SG", "australia": "AU", "brazil": "BR", "mexico": "MX", "japan": "JP", "argentina": "AR",
    "poland": "PL", "portugal": "PT", "sweden": "SE", "switzerland": "CH", "italy": "IT",
}
_ISO2 = set(_COUNTRIES.values())
_US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
                 "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
_AMBIGUOUS_STATE = {"CA", "DE", "IN", "AR"}  # also ISO country codes in our table
_NO_LOCATION = {"", "n/a", "na", "tbd", "unspecified", "various", "multiple locations", "anywhere", "-", "none", "unknown"}
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("preferred", r"(nice[- ]to[- ]haves?|preferred( qualifications)?|bonus( points)?|pluses|it'?s a plus|desirable)"),
    ("required", r"(requirements?|qualifications|minimum qualifications|what you('ll| will)? (bring|need)|must[- ]haves?|"
                 r"you have|about you|who you are|skills( (&|and) experience)?|required skills)"),
    ("duties", r"(responsibilities|what you('ll| will) do|the role|about the role|role overview|your impact|in this role)"),
    ("other", r"(about us|about the company|benefits|perks|compensation|pay( range)?|salary|equal opportunity|"
              r"our (values|team|mission)|why join( us)?|how to apply|what we offer)"),
)
_CUR_SYMBOL = {"€": "EUR", "£": "GBP", "US$": "USD", "C$": "CAD", "CA$": "CAD", "A$": "AUD"}
_AMOUNT = r"(\d{1,3}(?:[,.  ]\d{3})+|\d+(?:\.\d+)?)\s?([kK])?"
_COMP_TEXT = re.compile(
    r"(?P<cur1>USD|EUR|GBP|CAD|AUD|US\$|CA\$|C\$|A\$|[$€£])\s?" + _AMOUNT +
    r"(?:\s?(?:-|–|—|to)\s?(?P<cur2>USD|EUR|GBP|CAD|AUD|US\$|CA\$|C\$|A\$|[$€£])?\s?" + _AMOUNT + r")?"
    r"\s?(?P<code>USD|EUR|GBP|CAD|AUD)?"
    r"\s?(?P<per>per year|per annum|a year|/ ?yr|/ ?year|annually|annual|yearly|per hour|an hour|/ ?hr|/ ?hour|hourly|"
    r"per month|/ ?month|monthly)?", re.I)


def job_id(source: str, board: str, source_id: str) -> str:
    """Stable opaque id. Depends only on where the posting lives, never on its content."""
    return "jp_" + sha256_hex("jp/v1\x1f" + "\x1f".join((source, fold(board), source_id)))[:20]


def employer_key(name: str) -> str:
    key = fold(name)
    key = "".join(ch for ch in unicodedata.normalize("NFKD", key) if not unicodedata.combining(ch))
    for _ in range(3):
        key = _LEGAL_SUFFIX.sub("", key).strip(" ,.")
    return re.sub(r"[^a-z0-9]+", " ", key).strip()


def employer_id(name: str) -> str:
    """Stable opaque employer id derived only from the normalized name (never from store state).

    Two legal entities with the same normalized name share an id (``alias_ambiguous`` is then set):
    for cluster randomisation that errs on the safe side - it can merge clusters, never split one.
    """
    return "emp_" + sha256_hex("emp/v1:" + employer_key(name))[:16]


def title_key(title: str) -> str:
    key = fold(title)
    key = re.sub(r"\((remote|hybrid|onsite|on-site|contract|m/f/d|f/m/d|all genders)[^)]*\)", " ", key)
    key = re.sub(r"\b(sr)\b\.?", "senior", key)
    key = re.sub(r"\b(jr)\b\.?", "junior", key)
    return re.sub(r"[^a-z0-9+#]+", " ", key).strip()


def seniority_of(title: str, raw: str | None) -> str:
    for text in (raw, title):
        if not text:
            continue
        folded = fold(text)
        if folded in taxonomy.SENIORITY_LADDER:
            return folded
        if text is title and re.search(r"\bfield (cto|chief)", folded):
            continue  # "Field CTO" is a role family, not a C-level seniority statement
        for pattern, level in _SENIORITY_PATTERNS:
            if re.search(pattern, folded):
                return level
    return "unknown"


def employment_of(raw: str | None, title: str) -> str:
    if raw:
        f = fold(raw)
    else:  # only an explicit parenthetical in the title counts, e.g. "Data Engineer (Contract)"
        m = re.search(r"\((contract|part[- ]time|internship|intern|temporary)\)", fold(title))
        if not m:
            return "unknown"
        f = m.group(1)
    if "intern" in f:
        return "internship"
    if re.search(r"\bpart", f):
        return "part-time"
    if re.search(r"contract|freelance", f):
        return "contract"
    if re.search(r"\btemp", f):
        return "temporary"
    if re.search(r"\bfull", f) or f in ("permanent", "regular"):
        return "full-time"
    return "unknown"


def _remote_word(text: str | None) -> str | None:
    if not text:
        return None
    f = fold(text)
    hits = set()
    if re.search(r"\bhybrid\b", f):
        hits.add("hybrid")
    if re.search(r"\b(on[- ]?site|in[- ]office|office[- ]based)\b", f):
        hits.add("onsite")
    if re.search(r"\b(remote|work from home|wfh|distributed)\b", f) and not re.search(r"\b(no|not|non)[- ]remote\b", f):
        hits.add("remote")
    return hits.pop() if len(hits) == 1 else ("conflict" if hits else None)


def remote_of(raw: str | None, location: str | None) -> tuple[str, str | None]:
    """Return (policy, quality_flag). Contradictory statements resolve to 'unknown'."""
    stated, from_loc = _remote_word(raw), _remote_word(location)
    if stated == "conflict" or from_loc == "conflict":
        return "unknown", "remote-policy-conflict"
    if stated and from_loc and stated != from_loc:
        return "unknown", "remote-policy-conflict"
    return (stated or from_loc or "unknown"), None


def location_of(raw: str | None, country_raw: str | None) -> tuple[str | None, dict[str, Any]]:
    info: dict[str, Any] = {"raw": raw, "parts": [], "jurisdictions": [], "jurisdiction_basis": None, "multi": False}
    explicit = None
    if country_raw:
        c = fold(country_raw)
        explicit = _COUNTRIES.get(c) or (country_raw.strip().upper() if country_raw.strip().upper() in _ISO2 else None)
    cleaned = (raw or "").strip()
    place = re.sub(r"(?i)\b(remote|hybrid|on[- ]?site)\b", " ", cleaned)
    place = re.sub(r"^[\s\-–—:,/()|]+|[\s\-–—:,/()|]+$", "", place).strip()
    place = re.sub(r"\(\s*\)", "", place).strip()
    if fold(place) in _NO_LOCATION:
        if explicit:
            info["jurisdictions"], info["jurisdiction_basis"] = [explicit], "source-country-field"
        return None, info  # no place was stated (e.g. just "Remote"): location stays null
    pieces = [p.strip() for p in re.split(r"\s*(?:;|\||/|\bor\b|\band\b|&)\s*", place) if p.strip()]
    info["multi"] = len(pieces) > 1
    codes: list[str] = []
    basis: set[str] = set()
    for piece in pieces[:10]:
        parts = [p.strip() for p in piece.split(",") if p.strip()]
        entry: dict[str, Any] = {"city": None, "region": None, "country": None}
        last = parts[-1] if parts else ""
        country = _COUNTRIES.get(fold(last))
        if country:
            entry["country"] = country
            basis.add("explicit-country")
            parts = parts[:-1]
        elif len(parts) >= 2 and last.upper() in _US_STATES and last == last.upper():
            if last.upper() in _AMBIGUOUS_STATE and explicit != "US":
                info.setdefault("notes", []).append(f"'{last}' is both a US state code and a country code; left unknown")
            else:
                entry["country"] = "US"
                basis.add("us-state-code")
        if len(parts) >= 2:
            entry["city"], entry["region"] = parts[0], parts[1]
        elif len(parts) == 1:
            entry["city"] = parts[0]
        info["parts"].append(entry)
        if entry["country"]:
            codes.append(entry["country"])
    if explicit:
        if codes and any(c != explicit for c in codes):
            info.setdefault("notes", []).append("country field disagrees with location text; jurisdiction left unknown")
            codes = []
            basis = {"conflict"}
        else:
            codes, basis = [explicit], {"source-country-field"}
    info["jurisdictions"] = sorted(set(codes))
    info["jurisdiction_basis"] = "+".join(sorted(basis)) if basis else None
    return cleaned or None, info


def _amount(num: str, k: str | None) -> float | None:
    digits = num.replace(" ", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", digits):
        digits = digits.replace(",", "").replace(".", "")
    try:
        value = float(digits)
    except ValueError:
        return None
    return value * 1000 if k else value


def _per(word: str | None) -> str:
    if not word:
        return "unknown"
    w = word.lower()
    return "hour" if "h" in w and ("hour" in w or "hr" in w) else ("month" if "month" in w else "year")


_NOT_PAY = re.compile(r"\b(bonus(es)?|signing|sign-on|budget|stipend|allowance|relocation|reimburse\w*|equity|stock|rsus?|"
                      r"commission|revenue|funding|raised|series [a-f]|valuation|grant|award|prize|credit|voucher|"
                      r"customers?|users?|arr|mrr|spend)\b")


def comp_from_text(text: str) -> list[dict[str, Any]]:
    """Explicit pay ranges written in the description. Bare '$' has no currency (ambiguous)."""
    items: list[dict[str, Any]] = []
    for m in _COMP_TEXT.finditer(text[:20000]):
        context = fold(text[max(0, m.start() - 60): m.end() + 40])
        if _NOT_PAY.search(context):
            continue  # bonus, budget, funding, stipend ... are not salary statements
        groups = m.groups()
        cur1, n1, k1, cur2, n2, k2, code, per = groups
        lo = _amount(n1, k1)
        hi = _amount(n2, k2) if n2 else None
        if lo is not None and hi is not None and k2 and not k1 and lo < 1000:
            lo *= 1000  # "$120-150k": the k applies to both bounds
        if lo is None or (n2 is not None and hi is None) or (n2 is None and per is None):
            continue  # a lone number without a pay period is too weak to call compensation
        if lo < 10 and hi is None:
            continue
        currency = (code or "").upper() or _CUR_SYMBOL.get(cur1.upper(), cur1.upper() if len(cur1) == 3 else None)
        if cur2 and _CUR_SYMBOL.get(cur2.upper(), cur2.upper()) != _CUR_SYMBOL.get(cur1.upper(), cur1.upper()):
            currency = None
        items.append({"min": lo, "max": hi if hi is not None else lo, "currency": currency, "period": _per(per),
                      "basis": "unknown", "origin": "text-parsed"})
    return items


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    lo_a, hi_a = a["min"] if a["min"] is not None else a["max"], a["max"] if a["max"] is not None else a["min"]
    lo_b, hi_b = b["min"] if b["min"] is not None else b["max"], b["max"] if b["max"] is not None else b["min"]
    return lo_a <= hi_b and lo_b <= hi_a


def resolve_compensation(structured: list[dict[str, Any]], text: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (contract compensation or None, ext detail). Never guesses, converts or annualizes."""
    parsed = comp_from_text(text)
    stated = _dedupe(structured) + [p for p in _dedupe(parsed)]
    detail: dict[str, Any] = {"state": "absent", "period": None, "origin": None, "stated": stated}
    if not stated:
        return None, detail
    primary_pool = _dedupe(structured) or _dedupe(parsed)
    detail["origin"] = primary_pool[0]["origin"]
    if any(i["min"] is not None and i["max"] is not None and i["min"] > i["max"] for i in stated):
        detail["state"] = "conflict:inverted-range"
        return None, detail
    if len(primary_pool) > 1:
        same = {(i["currency"], i["period"]) for i in primary_pool}
        detail["state"] = "conflict:multiple-ranges" if len(same) == 1 else "multiple-ranges"
        return None, detail
    cand = primary_pool[0]
    detail["period"] = cand["period"]
    if structured:
        for other in _dedupe(parsed):
            comparable = other["currency"] in (None, cand["currency"]) and other["period"] in ("unknown", cand["period"])
            if comparable and not _overlap(cand, other):
                detail["state"] = "conflict:structured-vs-text"
                return None, detail
    if (cand["min"] or 0) == 0 and (cand["max"] or 0) == 0:
        detail["state"] = "unparseable:zero"
        return None, detail
    if cand["currency"] is None:
        detail["state"] = "ambiguous-currency"
        return None, detail
    if cand["period"] != "year":
        detail["state"] = "period-unknown" if cand["period"] == "unknown" else "non-annual"
        return None, detail
    detail["state"] = "stated"
    lo = cand["min"] if cand["min"] else None  # a stated 0 lower bound is not a salary floor
    return ({"currency": cand["currency"], "min": lo, "max": cand["max"], "basis": cand["basis"], "verified": False},
            detail)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        seen.setdefault(canonical_json({k: item[k] for k in ("min", "max", "currency", "period")}), item)
    return list(seen.values())


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"unsectioned": [], "required": [], "preferred": [], "duties": [], "other": []}
    current = "unsectioned"
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        head = fold(stripped).strip(" :-–—*#")
        if len(head) <= 60 and not stripped.startswith("- "):
            for name, pattern in _SECTIONS:
                if re.fullmatch(pattern, head):
                    current = name
                    break
            else:
                sections[current].append(stripped)
            continue
        sections[current].append(stripped)
    return sections


def extract_skills(record: SourceRecord) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """(required, preferred, unmapped declared tags, requirement lines, quality flags)."""
    sections = split_sections(record.description_text)
    declared, unmapped = set(), []
    for tag in record.declared_skills:
        sid = taxonomy.canonical_skill(tag)
        (declared.add(sid) if sid else unmapped.append(tag))
    required = set(declared)
    # With a Requirements section, only it (and declared tags) states requirements; free prose elsewhere, which
    # anyone can pad, is at most "preferred". Without any section, the unsectioned text is all we have.
    stated = sections["required"] if sections["required"] else sections["unsectioned"]
    required |= set(taxonomy.find_skills("\n".join(stated)))
    prose = sections["preferred"] + sections["duties"] + (sections["unsectioned"] if sections["required"] else [])
    preferred = set(taxonomy.find_skills("\n".join(prose))) - required
    flags = []
    if len(required) + len(preferred) > MAX_SKILLS:
        flags.append("skill-stuffing-suspected")
    req_sorted = sorted(required)[:MAX_SKILLS]
    pref_sorted = sorted(preferred)[: max(0, MAX_SKILLS - len(req_sorted))]
    lines = [ln[2:].strip()[:240] for ln in sections["required"] if ln.startswith("- ")][:20]
    return req_sorted, pref_sorted, sorted(set(unmapped))[:50], lines, flags


def quality_flags(record: SourceRecord) -> list[str]:
    flags: list[str] = []
    cap, posted, updated, expires = (parse_ts(x) for x in (record.captured_at, record.posted_at, record.updated_at,
                                                           record.expires_at))
    assert cap is not None
    if posted and posted > cap + timedelta(days=1):
        flags.append("time-anomaly:posted-in-future")
    if updated and updated > cap + timedelta(days=1):
        flags.append("time-anomaly:updated-in-future")
    if posted and updated and updated < posted - timedelta(days=1):
        flags.append("time-anomaly:updated-before-posted")
    if posted and expires and expires < posted:
        flags.append("time-anomaly:expires-before-posted")
    if posted and cap - posted > timedelta(days=LONG_OPEN_DAYS):
        flags.append("phantom-risk:long-open")
    if taxonomy.count_cues(record.title + "\n" + record.description_text, taxonomy.EVERGREEN_CUES):
        flags.append("phantom-risk:evergreen-requisition")
    if len(record.description_text) < 80:
        flags.append("thin-description")
    return flags


def status_at(ext: dict[str, Any], fetched_at: str, as_of: datetime, ttl_days: int = FRESHNESS_TTL_DAYS) -> tuple[str, str]:
    """Availability *as far as lane A can honestly say* at ``as_of``. Returns (status, reason)."""
    if ext.get("revoked"):
        return "expired", ext["revoked"]
    if ext.get("listed") is False:
        return "expired", "delisted-by-source"
    if any(f.startswith("time-anomaly") for f in ext.get("quality_flags", [])):
        return "unknown", "time-anomaly"
    if "producer-status-unknown" in ext.get("quality_flags", []):
        return "unknown", "producer-status-unknown"
    fetched = parse_ts(fetched_at)
    assert fetched is not None
    if as_of < fetched - timedelta(days=1):
        return "unknown", "as-of-precedes-capture"
    expires = parse_ts(ext["timestamps"].get("expires_at"))
    if expires and expires <= as_of:
        return "expired", "past-source-expiry"
    if as_of - fetched > timedelta(days=ttl_days):
        return "unknown", "stale-capture"
    return "active", "listed-at-capture"


def normalize(record: SourceRecord) -> dict[str, Any]:
    """Build the JobRecord envelope for one source record (status evaluated at capture time)."""
    required, preferred, unmapped, req_lines, skill_flags = extract_skills(record)
    location, loc_info = location_of(record.location_raw, record.country_raw)
    remote, remote_flag = remote_of(record.remote_raw, record.location_raw)
    compensation, comp_detail = resolve_compensation(record.compensation_raw, record.description_text)
    family = taxonomy.classify_family(record.title)
    qflags = quality_flags(record) + skill_flags + ([remote_flag] if remote_flag else [])
    if comp_detail["state"].startswith("conflict"):
        qflags.append("compensation-conflict")
    text = record.description_text
    ext: dict[str, Any] = {
        "ext_version": EXT_VERSION,
        "wire_format": record.source,
        "synthetic": record.synthetic,
        "board": record.board,
        "employer": {"id": employer_id(record.organization), "name": record.organization,
                     "key": employer_key(record.organization), "org_uid": record.org_uid, "alias_ambiguous": False},
        "timestamps": {"captured_at": record.captured_at, "posted_at": record.posted_at,
                       "updated_at": record.updated_at, "expires_at": record.expires_at},
        "listed": record.listed,
        "revoked": None,
        "apply_url": record.apply_url,
        "department": record.department,
        "location": loc_info,
        "compensation": comp_detail,
        "skills_required": required,
        "skills_preferred": preferred,
        "skills_unmapped": unmapped,
        "cues": {"customer": taxonomy.count_cues(text, taxonomy.CUSTOMER_CUES),
                 "enterprise": taxonomy.count_cues(text, taxonomy.ENTERPRISE_CUES)},
        "security_flags": list(record.security_flags),
        "quality_flags": sorted(set(qflags)),
        "description_text": text,
        "taxonomy_version": taxonomy.taxonomy_version(),
        "payload_sha256": record.payload_sha256,
    }
    posting: dict[str, Any] = {
        "id": job_id(record.source, record.board, record.source_id),
        # B01: every synthetic capture is labelled "synthetic" in the frozen object; the ATS wire format moves to ext.
        "source": "synthetic" if record.synthetic else record.source,
        "source_id": record.source_id,
        "title": record.title,
        "organization": record.organization,
        "url": record.url,
        "fetched_at": record.captured_at,
        "status": "unknown",
        "skills": sorted(set(required) | set(preferred)),
        "seniority": seniority_of(record.title, record.seniority_raw),
        "employment_type": employment_of(record.employment_raw, record.title),
        "location": location,
        "remote_policy": remote,
        "compensation": compensation,
        "license_note": record.license_note,
        "role_family": family,
        "requirements": req_lines,
        "source_evidence": [f"capture:{record.source}/{record.board}@{record.captured_at}",
                            f"payload-sha256:{record.payload_sha256}", f"url:{record.url}"],
    }
    posting["status"], ext["status_reason"] = status_at(ext, record.captured_at, parse_ts(record.captured_at))  # type: ignore[arg-type]
    ext["content_hash"] = content_hash(posting, ext)
    return {"posting": posting, "ext": ext}


def content_hash(posting: dict[str, Any], ext: dict[str, Any]) -> str:
    """Hash of everything the source said about the job, excluding capture time and derived status."""
    stable_posting = {k: v for k, v in posting.items() if k not in ("fetched_at", "status", "source_evidence")}
    stable_ext = {k: ext[k] for k in ("listed", "apply_url", "department", "compensation", "skills_required",
                                      "skills_preferred", "description_text")}
    stable_ext["timestamps"] = {k: v for k, v in ext["timestamps"].items() if k != "captured_at"}
    return sha256_hex(canonical_json([stable_posting, stable_ext]))


def utc(text: str) -> datetime:
    dt = parse_ts(text)
    if dt is None:
        raise ValueError(f"not a tz-aware timestamp: {text!r}")
    return dt.astimezone(timezone.utc)
