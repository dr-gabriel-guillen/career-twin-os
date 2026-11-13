"""Static guard against paid external APIs, outbound contact and LinkedIn automation.

``AGENTS.md`` forbids billable API calls, outreach, auto-apply and LinkedIn
harvesting in this batch. This guard scans code and dependency manifests (not
prose) for the hosts, SDK imports and package names that would make such a call
possible. It is a tripwire, not a sandbox: see ``netguard`` for the runtime block.

This file necessarily contains the patterns it searches for and is therefore the
single file excluded from its own scan (``SELF``).

    python -m ctint.sec_paid --tracked
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .sec_scan import MAX_BYTES, expand, tracked_files

SELF = "integration/ctint/sec_paid.py"
CODE_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".toml", ".cfg", ".ini",
    ".yml", ".yaml", ".sh", ".ps1", ".txt", ".lock", ".env.example",
)  # fmt: skip

PAID_HOSTS = (
    "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com", "aiplatform.googleapis.com",
    "api.cohere.ai", "api.cohere.com", "api.mistral.ai", "api.x.ai", "api.groq.com", "api.together.xyz",
    "openrouter.ai", "api.perplexity.ai", "bedrock-runtime", "api.replicate.com", "api.voyageai.com",
    "api.pinecone.io", "serpapi.com", "api.proxycurl.com", "nubela.co", "api.apify.com", "rapidapi.com",
    "api.twilio.com", "api.sendgrid.com", "api.mailgun.net", "api.stripe.com", "api.linkedin.com",
)  # fmt: skip
_PY_PACKAGES = (
    r"openai|anthropic|cohere|mistralai|groq|replicate|voyageai|pinecone|boto3|botocore|vertexai|litellm"
    r"|google\.generativeai|google\.genai|langchain_openai|langchain_anthropic|stripe|twilio|sendgrid"
    r"|linkedin_api|linkedin_scraper|apify_client|serpapi"
)
_JS_PACKAGES = (
    r"openai|@anthropic-ai/[\w.-]+|@aws-sdk/[\w.-]+|aws-sdk|cohere-ai|@google/generative-ai|@google-cloud/[\w.-]+"
    r"|@mistralai/[\w.-]+|groq-sdk|replicate|stripe|twilio|@sendgrid/[\w.-]+|nodemailer|linkedin-[\w.-]+"
    r"|puppeteer-extra-plugin-stealth|apify[\w.-]*|serpapi"
)
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PAID-HOST", re.compile("|".join(re.escape(h) for h in PAID_HOSTS), re.IGNORECASE)),
    ("PAID-SDK-PY", re.compile(rf"^\s*(?:from|import)\s+(?:{_PY_PACKAGES})(?:[\s.,;]|$)", re.MULTILINE)),
    ("PAID-SDK-JS", re.compile(rf"(?:from\s+|require\(\s*|import\(\s*|import\s+)[\"'](?:{_JS_PACKAGES})(?:/[\w./-]*)?[\"']")),
    ("PAID-DEP-JS", re.compile(rf"^\s*[\"'](?:{_JS_PACKAGES})[\"']\s*:\s*[\"']", re.MULTILINE)),
    ("PAID-DEP-PY", re.compile(
        r"^\s*[\"']?(?:openai|anthropic|cohere|mistralai|groq|replicate|voyageai|pinecone(?:-client)?|boto3|botocore"
        r"|litellm|google-generativeai|google-genai|langchain-openai|langchain-anthropic|stripe|twilio|sendgrid"
        r"|linkedin-api|linkedin-scraper|apify-client|google-search-results)\s*(?:[<>=~!\[;,\"']|$)",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("OUTBOUND-EMAIL", re.compile(r"^\s*(?:import\s+smtplib|from\s+smtplib\s+import)", re.MULTILINE)),
    ("LINKEDIN-ENDPOINT", re.compile(r"(?i)linkedin\.com/(?:voyager|jobs-guest|jobs/search|uas/login|checkpoint|oauth)")),
    ("CAPTCHA-BYPASS", re.compile(r"(?i)2captcha|anti-?captcha|capsolver|captcha[_-]?solver|undetected[_-]chromedriver")),
)  # fmt: skip
# Browser automation is legitimate for lane D's own E2E tests. It becomes a finding only when a
# file both IMPORTS an automation library and contains a LinkedIn URL. (A guard test that merely
# lists the strings "selenium" and "linkedin.com" as forbidden needles is not automation; the first
# real integration run on lane B at 9be8096 produced exactly that false positive.)
_AUTOMATION_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:selenium|playwright|pyppeteer|mechanize|scrapy|undetected_chromedriver)(?:[\s.,;]|$)"
    r"|(?:from\s+|require\(\s*|import\(\s*|import\s+)[\"'](?:playwright|@playwright/test|puppeteer(?:-core|-extra)?|selenium-webdriver)[\"']",
    re.MULTILINE,
)
_LINKEDIN_URL = re.compile(r"(?i)(?:https?:)?//[A-Za-z0-9.-]*linkedin\.com")


@dataclass(frozen=True)
class PaidFinding:
    rule: str
    path: str
    line: int
    excerpt: str

    def __str__(self) -> str:
        return f"{self.rule} {self.path}:{self.line} {self.excerpt}"


def is_code(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(CODE_SUFFIXES) and not lowered.endswith(".md")


def scan_text(path: str, text: str) -> list[PaidFinding]:
    findings: list[PaidFinding] = []
    for rule, pattern in RULES:
        if rule == "PAID-DEP-PY" and not re.search(
            r"(?i)(requirements[^/]*\.txt|pyproject\.toml|setup\.cfg|Pipfile)$", path
        ):
            continue
        if rule == "PAID-DEP-JS" and not path.lower().endswith(".json"):
            continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(PaidFinding(rule, path, line, match.group(0).strip()[:80]))
    if _AUTOMATION_IMPORT.search(text):
        for match in _LINKEDIN_URL.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                PaidFinding("LINKEDIN-AUTOMATION", path, line, "browser automation import + LinkedIn URL")
            )
    return findings


def scan(root: Path, rel_paths: Iterable[str]) -> list[PaidFinding]:
    findings: list[PaidFinding] = []
    for rel in rel_paths:
        posix = rel.replace("\\", "/")
        if posix == SELF or not is_code(posix):
            continue
        target = root / rel
        try:
            if not target.is_file() or target.stat().st_size > MAX_BYTES * 3:
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_text(posix, text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--root", default=".")
    parser.add_argument("--tracked", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    files = tracked_files(root) if args.tracked else expand(root, args.targets or ["."])
    findings = scan(root, files)
    for finding in findings:
        print(finding)
    print(f"sec_paid: {len(files)} files considered, {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
