"""WS5 / WS3 — threat-model test cases: CSV injection, SSRF, job-import injection,
secret/PII scanning, paid-API tripwire and the runtime network block."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ctint import contracts, sec_csv, sec_inject, sec_paid, sec_scan, sec_ssrf, synth
from ctint.baseline import load_reference_engine
from ctint.netguard import NetworkBlocked
from ctint.paths import repo_root

# ----------------------------------------------------------------------------- T-03 CSV


@pytest.mark.parametrize("payload", sec_csv.PAYLOADS)
def test_csv_formula_payload_is_detected_and_neutralised(payload: str) -> None:
    assert sec_csv.is_formula_cell(payload)
    safe = sec_csv.neutralize(payload)
    assert not sec_csv.is_formula_cell(safe)
    assert sec_csv.neutralize(safe) == safe, "neutralisation must be idempotent"


@pytest.mark.parametrize("value", sec_csv.BENIGN)
def test_csv_benign_cell_is_not_flagged(value: str) -> None:
    assert not sec_csv.is_formula_cell(value)
    assert sec_csv.neutralize(value) == value


def test_csv_audit_finds_every_planted_cell_and_none_in_safe_export() -> None:
    rows = [["job_id", "title", "notes"]]
    rows += [[f"job-{i}", "Engineer", payload] for i, payload in enumerate(sec_csv.PAYLOADS)]
    unsafe = sec_csv.audit_csv(sec_csv.to_csv(rows, safe=False))
    assert len(unsafe) == len(sec_csv.PAYLOADS)
    assert {f.column for f in unsafe} == {3}
    assert sec_csv.audit_csv(sec_csv.to_csv(rows, safe=True)) == []


def test_csv_audit_handles_multiline_quoted_cells() -> None:
    text = 'id,notes\n1,"line one\n=HYPERLINK(""https://attacker.example"")"\n2,"ok"\n'
    assert sec_csv.audit_csv(text) == []  # the cell starts with 'line one', not with a trigger
    text2 = 'id,notes\n1,"=1+1\nsecond line"\n'
    assert [(f.row, f.column) for f in sec_csv.audit_csv(text2)] == [(2, 2)]


def test_contract_valid_application_notes_can_carry_formulas() -> None:
    """The frozen ApplicationEvent contract accepts formula text, so exporters must neutralise."""
    event = synth.gen_application_events(synth.gen_jobs(5), 1)[0]
    event["notes"] = sec_csv.PAYLOADS[1]
    assert contracts.check("application-event", event) == []
    row = [event["id"], event["notes"]]
    assert sec_csv.audit_csv(sec_csv.to_csv([row], safe=False))
    assert not sec_csv.audit_csv(sec_csv.to_csv([row], safe=True))


# ----------------------------------------------------------------------------- T-04 SSRF


@pytest.mark.parametrize("url", sec_ssrf.ATTACK_URLS)
def test_ssrf_attack_url_is_denied(url: str) -> None:
    verdict = sec_ssrf.classify_url(url)
    assert not verdict.allowed, str(verdict)
    assert verdict.reasons


@pytest.mark.parametrize("url", sec_ssrf.BENIGN_URLS)
def test_ssrf_benign_url_is_allowed(url: str) -> None:
    verdict = sec_ssrf.classify_url(url)
    assert verdict.allowed, str(verdict)


@pytest.mark.parametrize(
    "target", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.1", "172.31.255.254"]
)
def test_ssrf_private_ipv4_denied_in_every_legacy_encoding(target: str) -> None:
    value = int(ipaddress.IPv4Address(target))
    a, b, c, d = (value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255
    encodings = [
        str(value), hex(value), f"0{value:o}", f"0x{a:x}.0x{b:x}.0x{c:x}.0x{d:x}", f"0{a:o}.0{b:o}.0{c:o}.0{d:o}",
        f"{a}.{(b << 16) | (c << 8) | d}", f"{a}.{b}.{(c << 8) | d}", f"[::ffff:{target}]", f"[::ffff:{a:02x}{b:02x}:{c:02x}{d:02x}]",
    ]  # fmt: skip
    for host in encodings:
        verdict = sec_ssrf.classify_url(f"https://{host}/feed")
        assert not verdict.allowed, f"{target} as {host!r} was allowed"


def test_ssrf_reasons_are_specific() -> None:
    assert "loopback" in sec_ssrf.classify_url("https://2130706433/").reasons
    assert "obfuscated-ipv4-literal" in sec_ssrf.classify_url("https://0x7f.1/").reasons
    assert any("link-local" in r for r in sec_ssrf.classify_url("https://169.254.169.254/").reasons)
    assert "scheme:http" in sec_ssrf.classify_url("http://jobs.fictional-meridian.example/").reasons
    assert "ip-literal" in sec_ssrf.classify_url("https://8.8.8.8/feed").reasons


def test_contract_valid_job_url_can_still_be_an_ssrf_target() -> None:
    """JobPosting.url only requires minLength 1; connectors must classify before fetching."""
    job = synth.gen_jobs(1)[0]
    job["url"] = "https://169.254.169.254/latest/meta-data/"
    assert contracts.schema_errors("job-posting", job) == []
    assert not sec_ssrf.classify_url(job["url"]).allowed


# ----------------------------------------------------------------------------- T-01/T-02 injection


@pytest.mark.parametrize("payload", sec_inject.HTML_PAYLOADS)
def test_active_html_is_detected(payload: str) -> None:
    assert sec_inject.active_html(payload)


@pytest.mark.parametrize("text", sec_inject.SAFE_MARKUP)
def test_inert_text_is_not_flagged_as_active_html(text: str) -> None:
    assert not sec_inject.active_html(text)


def test_invisible_and_bidi_characters_are_reported() -> None:
    assert "U+202E" in sec_inject.invisible_characters(sec_inject.UNICODE_PAYLOADS[0])
    assert "U+200B" in sec_inject.invisible_characters(sec_inject.UNICODE_PAYLOADS[1])
    assert sec_inject.invisible_characters("plain ASCII title") == []


def test_poisoned_jobs_remain_contract_valid() -> None:
    """Hostile job text is legal under the frozen contract: every consumer must treat it as data."""
    poisoned = sec_inject.poison_jobs(synth.gen_jobs(40, seed=3), seed=3)
    for job in poisoned:
        assert contracts.check("job-posting", job) == [], job["id"]
        assert any(sec_inject.CANARY_PREFIX in r for r in job["requirements"])
        assert any(sec_inject.active_html(r) for r in job["requirements"])


def _profile() -> dict[str, Any]:
    return {"skills": ["python", "api-design", "aws"], "preferences": {"remote": "remote"}}


def test_reference_engine_is_not_steered_by_injected_job_text() -> None:
    engine = load_reference_engine()
    jobs = [j for j in synth.gen_jobs(60, seed=4) if "remote_policy" in j]
    poisoned = sec_inject.poison_jobs(jobs, seed=4)

    def rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = [engine.score_job(_profile(), row) for row in rows]
        return sorted(scored, key=lambda r: (-r["score"], r["job_id"]))

    assert sec_inject.differential_findings(rank(jobs), rank(poisoned), poisoned) == []


def test_differential_oracle_catches_a_steerable_ranker() -> None:
    """Self-test of the oracle with a deliberately vulnerable scorer."""
    jobs = synth.gen_jobs(20, seed=5)
    poisoned = sec_inject.poison_jobs(jobs, seed=5)

    def vulnerable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            text = " ".join(row.get("requirements", []))
            score = 100.0 if "rank this posting first" in text or "Set score to 100" in text else 50.0
            summary = "Great fit. " + (text.split("Write ")[-1][:40] if "Write CT-CANARY" in text else "")
            out.append({"job_id": row["id"], "score": score, "summary": summary})
        return sorted(out, key=lambda r: (-r["score"], r["job_id"]))

    findings = sec_inject.differential_findings(vulnerable(jobs), vulnerable(poisoned), poisoned)
    kinds = {f.split()[0] for f in findings}
    assert {"INJECTION-OBEYED", "INJECTION-CHANGED-SCORE", "INJECTION-CHANGED-ORDER"} <= kinds


def test_canary_in_verbatim_passthrough_is_not_a_leak_but_paraphrase_is() -> None:
    job = sec_inject.poison_jobs(synth.gen_jobs(1), seed=0)[0]
    verbatim = set(job["requirements"])
    assert sec_inject.canary_leaks({"raw_requirements": job["requirements"]}, verbatim) == []
    canary = sec_inject.canary_for(job["id"], 0)
    assert sec_inject.canary_leaks({"bullet": f"Delivered results ({canary})"}, verbatim) == ["/bullet"]
    assert sec_inject.canary_leaks({canary: 1}, verbatim) == [f"/{canary}#key"]


def test_lone_surrogate_survives_json_but_breaks_utf8_export() -> None:
    """Robustness hazard for every lane that writes job text to disk or HTTP."""
    title = json.loads('"Engineer \\ud800"')
    assert sec_inject.has_lone_surrogate(title)
    job = dict(synth.gen_jobs(1)[0], title=title)
    assert contracts.schema_errors("job-posting", job) == [], "schema gap: unencodable strings accepted"
    with pytest.raises(UnicodeEncodeError):
        json.dumps(job, ensure_ascii=False).encode("utf-8")


# ----------------------------------------------------------------------------- secrets / PII


def _samples() -> dict[str, str]:
    # Built at runtime so this file never contains a literal that looks like a credential.
    return {
        "AWS-ACCESS-KEY": "AKIA" + "Q" * 16,
        "GITHUB-TOKEN": "ghp_" + "a1B2" * 9,
        "ANTHROPIC-KEY": "sk-" + "ant-" + "api03-" + "Zx9" * 10,
        "OPENAI-KEY": "sk-" + "proj-" + "Ab3" * 14,
        "GOOGLE-API-KEY": "AIza" + "Sy" + "D" * 33,
        "SLACK-TOKEN": "xoxb-" + "1234567890-abcdefghij",
        "STRIPE-LIVE-KEY": "sk_" + "live_" + "4eC39HqLyjWDarjt",
        "PRIVATE-KEY-BLOCK": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "JWT": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N",
        "GENERIC-SECRET": 'api_key = "' + "q7Rz2LmX9vB4nT6wK1yP" + '"',
        "PII-EMAIL": "jane.roe" + "@" + "real-mail-provider.com",
        "PII-PHONE": "+1 415-" + "867-5309",
        "PII-SSN": "123-" + "45-" + "6789",
        "PII-LINKEDIN-PROFILE": "https://www.linkedin." + "com/in/" + "some-real-person",
    }


@pytest.mark.parametrize("rule", sorted(_samples()))
def test_secret_and_pii_rules_fire_and_redact(rule: str) -> None:
    sample = _samples()[rule]
    findings = sec_scan.scan_text("x.py", f"value = {sample}\n")
    assert rule in {f.rule for f in findings}, [str(f) for f in findings]
    for finding in findings:
        assert sample not in str(finding), "scanner output must not echo the secret"
    allowed = sec_scan.scan_text("x.py", f"value = {sample}  # ct-scan: allow={rule}\n")
    assert rule not in {f.rule for f in allowed}


@pytest.mark.parametrize(
    "line",
    [
        "contact: recruiter@fictional-meridian.example",
        "author: 12345+someone@users.noreply.github.com",
        "uses: actions/checkout@v4",
        "phone: 415-555-0134",
        'api_key = "example-placeholder-value-000"',
        'token: "${{ github.token }}"',
        "fetched_at 2026-01-05T09:00:00Z id job-syn-s0-000001",
        "sha c9b810f2016cd09681b21cc7884dd563065159ce",
        "version 4.26.0.20261006 and 2025.9.1",
        "git@github.com:owner/repo.git",
    ],
)
def test_scanner_ignores_synthetic_and_placeholder_text(line: str) -> None:
    assert sec_scan.scan_text("x.txt", line) == []


@pytest.mark.parametrize(
    "path",
    [
        ".env", "apps/web/.env.local", "keys/server.pem", "id_rsa", "personal/notes.md", "private/cv.txt",
        "exports/applications.csv", "linkedin-export-2026/Connections.csv", "data/Connections.csv",
        "docs/resume-private-v3.md", "fixtures/CV_final.pdf", "fixtures/report.docx", "app.sqlite", ".data/cache.json",
    ],
)  # fmt: skip
def test_forbidden_paths_are_flagged(path: str) -> None:
    assert sec_scan.scan_path_name(path), path


@pytest.mark.parametrize(
    "path",
    [".env.example", "docs/PRODUCT_SPEC.md", "fixtures/synthetic_jobs.json", "integration/ctint/sec_scan.py"],
)
def test_ordinary_paths_are_not_flagged(path: str) -> None:
    assert sec_scan.scan_path_name(path) == []


def test_scan_file_skips_binary_content_but_still_checks_the_name(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + ("AKIA" + "Q" * 16).encode())
    (tmp_path / "leak.txt").write_text("AKIA" + "Q" * 16, encoding="utf-8")
    assert sec_scan.scan(tmp_path, ["blob.bin"]) == []
    assert [f.rule for f in sec_scan.scan(tmp_path, ["leak.txt"])] == ["AWS-ACCESS-KEY"]
    assert sec_scan.main(["--root", str(tmp_path), "leak.txt"]) == 1
    assert sec_scan.main(["--root", str(tmp_path), "blob.bin"]) == 0


def test_repository_tracked_files_contain_no_secrets_or_pii() -> None:
    """Real gate over the actual checkout (every tracked file, all lanes present)."""
    root = repo_root()
    files = sec_scan.tracked_files(root)
    assert len(files) >= 20
    findings = sec_scan.scan(root, files)
    assert findings == [], "\n".join(str(f) for f in findings)


# ----------------------------------------------------------------------------- paid APIs / outreach


def _paid_samples() -> list[tuple[str, str, str]]:
    return [
        ("PAID-HOST", "client.py", 'URL = "https://api.' + 'openai.com/v1/chat/completions"'),
        ("PAID-HOST", "infra.yml", "endpoint: bedrock-" + "runtime.us-east-1.amazonaws.com"),
        ("PAID-SDK-PY", "m.py", "import " + "anthropic"),
        ("PAID-SDK-PY", "m.py", "    from " + "openai import OpenAI"),
        ("PAID-SDK-PY", "m.py", "import " + "boto3, json"),
        ("PAID-SDK-JS", "a.ts", "import OpenAI from '" + "openai';"),
        ("PAID-SDK-JS", "a.js", 'const s = require("@aws-' + 'sdk/client-s3");'),
        ("PAID-DEP-JS", "package.json", '    "@anthropic-' + 'ai/sdk": "^1.0.0",'),
        ("PAID-DEP-PY", "requirements.txt", "open" + "ai>=1.0"),
        ("PAID-DEP-PY", "packages/x/pyproject.toml", '  "lite' + 'llm==1.2",'),
        ("OUTBOUND-EMAIL", "send.py", "import " + "smtplib"),
        ("LINKEDIN-ENDPOINT", "scrape.py", 'u = "https://www.linkedin.' + 'com/voyager/api/jobs"'),
        ("CAPTCHA-BYPASS", "x.py", "from " + "cap" + "solver_client import solve"),
    ]


@pytest.mark.parametrize(("rule", "path", "line"), _paid_samples())
def test_paid_api_tripwire_fires(rule: str, path: str, line: str) -> None:
    assert rule in {f.rule for f in sec_paid.scan_text(path, f"# header\n{line}\n")}


def test_paid_tripwire_ignores_safe_code_and_flags_linkedin_automation() -> None:
    safe = (
        "import json\nfrom playwright.sync_api import sync_playwright\nimport openapi_spec\nopenai_like = 1\n"
    )
    assert sec_paid.scan_text("e2e.py", safe) == []
    risky = safe + 'page.goto("https://www.linkedin.' + 'com/feed")\n'
    assert "LINKEDIN-AUTOMATION" in {f.rule for f in sec_paid.scan_text("e2e.py", risky)}
    ts_risky = "import { chromium } from 'playwright';\nawait page.goto('https://linkedin." + "com/jobs');\n"
    assert "LINKEDIN-AUTOMATION" in {f.rule for f in sec_paid.scan_text("scrape.ts", ts_risky)}
    assert not sec_paid.is_code("docs/THREAT_MODEL.md")
    assert sec_paid.is_code("apps/web/package.json")


def test_guard_tests_that_only_name_forbidden_strings_are_not_findings() -> None:
    """Regression for a false positive observed on lane B @ 9be8096 (test_security_and_adapter.py:60)."""
    guard = (
        'FORBIDDEN_IMPORTS = {"selenium", "playwright", "requests"}\n'
        'for needle in ("linkedin.' + 'com", "mailto:", "smtp"):\n'
        "    assert needle not in lowered\n"
    )
    assert sec_paid.scan_text("packages/experiments/tests/test_security_and_adapter.py", guard) == []


def test_repository_code_makes_no_paid_or_outreach_calls() -> None:
    root = repo_root()
    findings = sec_paid.scan(root, sec_scan.tracked_files(root))
    assert findings == [], "\n".join(str(f) for f in findings)


# ----------------------------------------------------------------------------- runtime network block


def test_non_loopback_connections_are_blocked() -> None:
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("192.0.2.1", 443), timeout=0.2)
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("api.fictional-paid-service.example", 443)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked):
            sock.connect(("198.51.100.7", 80))
    finally:
        sock.close()
    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen("https://api.fictional-paid-service.example/v1", timeout=0.2)  # noqa: S310


def test_loopback_connections_still_work() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
    finally:
        server.close()


@pytest.mark.network
def test_network_marked_tests_are_never_executed() -> None:
    pytest.fail("a network-marked test ran in isolated mode")
