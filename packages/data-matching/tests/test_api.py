"""CLI + HTTP surface, and consumption of lane A's output from a separate process (acceptance criterion)."""
import copy
import http.client
import json
import subprocess
import sys
import threading

import pytest

from a_testkit import AS_OF, PKG, ROOT, make_env, posting
from ct_matching import corpus, schema
from ct_matching.httpapi import MAX_BODY_BYTES, make_server
from ct_matching.service import RequestError, Service, vault_from_profile

RUN = [sys.executable, str(PKG / "run.py")]


def cli(*args, expect=0):
    proc = subprocess.run(RUN + [str(a) for a in args], capture_output=True, timeout=120)
    assert proc.returncode == expect, proc.stderr.decode("utf-8", "replace")[-800:]
    return proc


# ------------------------------------------------------------------------- CLI
@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    work = tmp_path_factory.mktemp("lane_a_cli")
    cli("corpus", "--out", work / "corpus.json", "--profile-out", work / "profile.json")
    cli("import", "--store", work / "store.json", work / "corpus.json")
    return work


def test_cli_pipeline_emits_contract_valid_json_in_separate_processes(workdir):
    jobs = json.loads(cli("query", "--store", workdir / "store.json", "--as-of", AS_OF, "--limit", 500).stdout)
    ranked = json.loads(cli("rank", "--store", workdir / "store.json", "--profile", workdir / "profile.json",
                            "--as-of", AS_OF, "--top", 10).stdout)
    assert jobs["total"] >= 150 and len(jobs["items"]) == jobs["total"]
    assert len(ranked["shortlist"]) == 10
    for item in jobs["items"]:
        schema.check(item["posting"], "job-posting")
        assert "description_text" not in item["ext"]
    schema.check(ranked, "a-match-result")


def test_separate_consumer_process_accepts_output_using_only_frozen_contracts(workdir):
    """B/D stand-in: a script that imports nothing from lane A validates A's files against contracts/ on main."""
    (workdir / "jobs.json").write_bytes(cli("query", "--store", workdir / "store.json", "--as-of", AS_OF, "--limit", 500).stdout)
    (workdir / "rank.json").write_bytes(cli("rank", "--store", workdir / "store.json", "--profile", workdir / "profile.json",
                                            "--as-of", AS_OF).stdout)
    (workdir / "claims.json").write_bytes(cli("vault-export", "--profile", workdir / "profile.json", "--as-of", AS_OF,
                                              "--mode", "internal-matching").stdout)
    consumer = PKG / "examples" / "consumer_check.py"
    assert "ct_matching" not in consumer.read_text(encoding="utf-8").replace("ct_matching.corpus", "")
    proc = subprocess.run([sys.executable, "-I", str(consumer), "--contracts", str(ROOT / "contracts"),
                           "--jobs", str(workdir / "jobs.json"), "--rank", str(workdir / "rank.json"),
                           "--claims", str(workdir / "claims.json")], capture_output=True, timeout=60, cwd=str(workdir))
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout) == {"ok": True, "violations": 0}


def test_consumer_check_actually_detects_violations(workdir):
    ranked = json.loads(cli("rank", "--store", workdir / "store.json", "--profile", workdir / "profile.json", "--as-of", AS_OF).stdout)
    ranked["score_type"] = "hiring_probability"
    ranked["shortlist"][0]["availability"]["status"] = "expired"
    jobs = json.loads(cli("query", "--store", workdir / "store.json", "--as-of", AS_OF, "--limit", 3).stdout)
    jobs["items"][0]["posting"]["salary"] = 1
    del jobs["items"][1]["posting"]["url"]
    (workdir / "bad_rank.json").write_text(json.dumps(ranked), encoding="utf-8")
    (workdir / "bad_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", str(PKG / "examples" / "consumer_check.py"), "--contracts", str(ROOT / "contracts"),
                           "--jobs", str(workdir / "bad_jobs.json"), "--rank", str(workdir / "bad_rank.json")],
                          capture_output=True, timeout=60)
    assert proc.returncode == 1 and json.loads(proc.stdout)["violations"] >= 4


def test_cli_output_is_byte_identical_across_processes(workdir):
    args = ("rank", "--store", workdir / "store.json", "--profile", workdir / "profile.json", "--as-of", AS_OF, "--top", 20)
    assert cli(*args).stdout == cli(*args).stdout
    assert cli("corpus").stdout == cli("corpus").stdout


def test_cli_store_file_is_reproducible(workdir):
    cli("import", "--store", workdir / "store2.json", workdir / "corpus.json")
    assert (workdir / "store.json").read_bytes() == (workdir / "store2.json").read_bytes()


def test_cli_provenance_and_vectors(workdir):
    jobs = json.loads(cli("query", "--store", workdir / "store.json", "--as-of", AS_OF, "--limit", 1).stdout)
    jid = jobs["items"][0]["posting"]["id"]
    prov = json.loads(cli("provenance", "--store", workdir / "store.json", jid).stdout)
    assert prov["job_id"] == jid and prov["ledger"]["verified"] is True
    vectors = json.loads(cli("skill-vectors", "--store", workdir / "store.json", "--profile", "synthetic", "--as-of", AS_OF).stdout)
    assert vectors["jobs"] and all(len(j["values"]) == len(vectors["vocabulary"]) for j in vectors["jobs"])
    assert vectors["candidate"]["entity_id"] == "candidate-synthetic-001"


@pytest.mark.parametrize("args,code", [
    (("rank", "--store", "{store}", "--profile", "synthetic", "--as-of", "now"), "bad-as-of"),
    (("rank", "--store", "{store}", "--profile", "{missing}", "--as-of", AS_OF), "file-not-found"),
    (("query", "--store", "{store}", "--as-of", AS_OF, "--limit", "0"), "bad-parameter"),
    (("query", "--store", "{store}", "--as-of", AS_OF, "--retriever", "gpt", "--q", "x"), "bad-parameter"),
    (("provenance", "--store", "{store}", "jp_does_not_exist"), "not-found"),
    (("vault-export", "--profile", "synthetic", "--as-of", AS_OF, "--mode", "everything"), "bad-export-mode"),
    (("import", "--store", "{out}", "{garbage}"), "malformed-json"),
])
def test_cli_errors_are_json_on_stderr_with_exit_code_3(workdir, args, code):
    (workdir / "garbage.json").write_text("{nope", encoding="utf-8")
    subst = {"store": workdir / "store.json", "missing": workdir / "nope.json", "out": workdir / "x.json",
             "garbage": workdir / "garbage.json"}
    proc = cli(*[a.format(**subst) if isinstance(a, str) else a for a in args], expect=3)
    assert json.loads(proc.stderr.decode().strip().splitlines()[-1])["error"]["code"] == code
    assert proc.stdout == b""


def test_cli_refuses_tampered_store(workdir):
    doc = json.loads((workdir / "store.json").read_text(encoding="utf-8"))
    doc["ledger"][3]["detail"]["content_hash"] = "0" * 64
    (workdir / "tampered.json").write_text(json.dumps(doc), encoding="utf-8")
    proc = cli("query", "--store", workdir / "tampered.json", "--as-of", AS_OF, expect=3)
    assert b"ledger fails verification" in proc.stderr


# ------------------------------------------------------------------------ HTTP
@pytest.fixture(scope="module")
def server():
    service = Service()
    service.import_doc(corpus.generate())
    service.vault, service.preferences = vault_from_profile(corpus.SYNTHETIC_PROFILE, AS_OF)
    srv = make_server(service, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(server, method, path, body=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
    hdrs = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    return resp.status, (json.loads(payload) if payload else None), resp


def test_http_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_http_health_jobs_job_provenance(server):
    status, health, resp = call(server, "GET", "/v1/health")
    assert status == 200 and health["ok"] and health["network_access"] == "disabled" and health["store"]["ledger_verified"]
    assert resp.getheader("Access-Control-Allow-Origin") is None and resp.getheader("X-Content-Type-Options") == "nosniff"
    status, jobs, _ = call(server, "GET", f"/v1/jobs?as_of={AS_OF}&status=active&family=fde&limit=5")
    assert status == 200 and 0 < len(jobs["items"]) <= 5
    assert all(i["posting"]["status"] == "active" and i["posting"]["role_family"] == "fde" for i in jobs["items"])
    jid = jobs["items"][0]["posting"]["id"]
    status, job, _ = call(server, "GET", f"/v1/jobs/{jid}?as_of={AS_OF}")
    assert status == 200 and job["posting"]["id"] == jid and "description_text" in job["ext"]
    schema.check(job["posting"], "job-posting")
    status, prov, _ = call(server, "GET", f"/v1/provenance/{jid}")
    assert status == 200 and prov["versions"][0]["version"] == 1


def test_http_rank_matches_library_result_exactly(server, service):
    status, ranked, _ = call(server, "POST", "/v1/rank", {"as_of": AS_OF, "top_n": 7})
    assert status == 200 and len(ranked["shortlist"]) == 7
    assert ranked["result_hash"] == service.rank({"as_of": AS_OF, "top_n": 7})["result_hash"]
    schema.check(ranked, "a-match-result")


def test_http_rank_with_inline_profile_and_preferences(server, profile):
    profile["preferences"]["remote"] = ["onsite"]
    status, ranked, _ = call(server, "POST", "/v1/rank", {"as_of": AS_OF, "profile": profile, "preferences": {"jurisdictions": ["GB", "DE"]}})
    assert status == 200 and ranked["shortlist"]
    for item in ranked["shortlist"]:
        outcomes = {c["constraint"]: c["outcome"] for c in item["constraints"]}
        assert outcomes["remote-policy"] in ("pass", "unknown") and outcomes["jurisdiction"] in ("pass", "unknown")


def test_http_search_and_pagination(server):
    _, page1, _ = call(server, "GET", f"/v1/jobs?as_of={AS_OF}&limit=10&offset=0")
    _, page2, _ = call(server, "GET", f"/v1/jobs?as_of={AS_OF}&limit=10&offset=10")
    ids1, ids2 = [i["posting"]["id"] for i in page1["items"]], [i["posting"]["id"] for i in page2["items"]]
    assert len(ids1) == len(ids2) == 10 and not set(ids1) & set(ids2) and ids1 == sorted(ids1)
    _, hits, _ = call(server, "GET", f"/v1/jobs?as_of={AS_OF}&q=kubernetes%20terraform%20on-call&retriever=hybrid&limit=5")
    assert hits["items"][0]["posting"]["role_family"] == "devops-sre" and "retrieval_score" in hits["items"][0]


def test_http_import_then_visible(server):
    env = make_env(board="fictionalhttp", postings=[posting("http-1", title="Senior Security Engineer")])
    status, report, _ = call(server, "POST", "/v1/import", env)
    assert status == 200 and report["counts"]["created"] == 1
    status, again, _ = call(server, "POST", "/v1/import", env)
    assert again["counts"]["created"] == 0 and again["counts"]["unchanged"] == 1
    status, bad, _ = call(server, "POST", "/v1/import", {"capture": {"source": "linkedin", "board": "x", "captured_at": AS_OF}, "payload": []})
    assert status == 200 and bad["counts"]["rejected"] == 1 and bad["rejected_by_class"] == {"unsupported_source": 1}


@pytest.mark.parametrize("method,path,body,headers,raw,status,code", [
    ("GET", "/v1/jobs", None, None, None, 400, "bad-as-of"),
    ("GET", "/v1/jobs?as_of=2026-11-10", None, None, None, 400, "bad-as-of"),
    ("GET", f"/v1/jobs?as_of={AS_OF}&limit=abc", None, None, None, 400, "bad-parameter"),
    ("GET", f"/v1/jobs?as_of={AS_OF}&limit=100000", None, None, None, 400, "bad-parameter"),
    ("GET", f"/v1/jobs?as_of={AS_OF}&sort=score", None, None, None, 400, "bad-parameter"),
    ("GET", f"/v1/jobs?as_of={AS_OF}&as_of={AS_OF}", None, None, None, 400, "bad-parameter"),
    ("GET", f"/v1/jobs/jp_nope?as_of={AS_OF}", None, None, None, 404, "not-found"),
    ("GET", "/v1/provenance/..%2F..%2Fetc%2Fpasswd", None, None, None, 404, "not-found"),
    ("GET", "/v2/health", None, None, None, 404, "not-found"),
    ("GET", "/", None, None, None, 404, "not-found"),
    ("GET", "/v1/rank", None, None, None, 405, "method-not-allowed"),
    ("POST", "/v1/health", {}, None, None, 405, "method-not-allowed"),
    ("PUT", "/v1/jobs", {}, None, None, 405, "method-not-allowed"),
    ("DELETE", "/v1/jobs/x", None, None, None, 405, "method-not-allowed"),
    ("POST", "/v1/rank", None, {"Content-Type": "application/json"}, b"{not json", 400, "malformed-json"),
    ("POST", "/v1/rank", None, {"Content-Type": "application/json"}, b"\xff\xfe", 400, "malformed-json"),
    ("POST", "/v1/rank", None, {"Content-Type": "text/plain"}, b"{}", 415, "unsupported-media-type"),
    ("POST", "/v1/rank", None, {"Content-Type": "application/x-www-form-urlencoded"}, b"as_of=x", 415, "unsupported-media-type"),
    ("POST", "/v1/rank", [1, 2], None, None, 400, "bad-request"),
    ("POST", "/v1/rank", {"as_of": AS_OF, "top_n": 0}, None, None, 400, "bad-preferences"),
    ("POST", "/v1/rank", {"as_of": AS_OF, "preferences": {"weights": {"luck": 9}}}, None, None, 400, "bad-preferences"),
    ("POST", "/v1/rank", {"as_of": AS_OF, "sql": "drop"}, None, None, 400, "bad-parameter"),
    ("POST", "/v1/rank", {"as_of": AS_OF, "profile": "me"}, None, None, 400, "bad-profile"),
    ("POST", "/v1/rank", {"as_of": AS_OF, "profile": {"subject_id": "s", "evidence": [{"ref": "e", "kind": "document-hash",
                                                                                         "pointer": "https://drive.example/cv"}]}}, None, None, 400, "bad-profile"),
    ("GET", "/v1/health", None, {"Host": "evil.example"}, None, 403, "forbidden-host"),
    ("GET", "/v1/health", None, {"Host": "127.0.0.1.evil.example:80"}, None, 403, "forbidden-host"),
])
def test_http_negative_cases_return_structured_errors(server, method, path, body, headers, raw, status, code):
    got, payload, _ = call(server, method, path, body, headers, raw)
    assert got == status and payload["error"]["code"] == code
    assert "Traceback" not in json.dumps(payload)


def test_http_oversized_body_rejected_without_reading_it(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
    conn.putrequest("POST", "/v1/import")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413 and json.loads(resp.read())["error"]["code"] == "payload-too-large"
    conn.close()


def test_http_server_survives_errors_and_keeps_serving(server):
    for _ in range(3):
        call(server, "POST", "/v1/rank", None, {"Content-Type": "application/json"}, b"[")
    assert call(server, "GET", "/v1/health")[0] == 200


def test_http_never_echoes_request_text_unescaped_in_errors(server):
    status, payload, resp = call(server, "GET", "/v1/jobs?as_of=%3Cscript%3Ealert(1)%3C/script%3E")
    assert status == 400 and "<script>" not in json.dumps(payload)
    assert resp.getheader("Content-Type").startswith("application/json")


# --------------------------------------------------------------------- service
def test_service_rank_without_profile_is_refused():
    service = Service()
    service.import_envelope = None
    with pytest.raises(RequestError) as err:
        service.rank({"as_of": AS_OF})
    assert err.value.code == "no-profile"


def test_import_bundle_and_single_envelope_agree(bundle):
    a, b = Service(), Service()
    a.import_doc(copy.deepcopy(bundle))
    for env in copy.deepcopy(bundle)["captures"]:
        b.import_doc(env)
    assert a.store.to_dict() == b.store.to_dict()


def test_frozen_examples_are_current():
    sys.path.insert(0, str(PKG / "tools"))
    import build_examples
    for name, doc in build_examples.build().items():
        committed = (PKG / "examples" / f"{name}.json").read_text(encoding="utf-8").replace("\r\n", "\n")
        assert committed == build_examples.render(doc), f"examples/{name}.json is stale; run tools/build_examples.py"


def test_committed_corpus_fixture_matches_generator():
    committed = json.loads((PKG / "fixtures" / "corpus_seed20261110_n180.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(corpus.generate(), ensure_ascii=False))
    profile = json.loads((PKG / "fixtures" / "synthetic_profile.json").read_text(encoding="utf-8"))
    assert profile == json.loads(json.dumps(corpus.SYNTHETIC_PROFILE))
