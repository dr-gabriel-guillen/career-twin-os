import copy
import json

import pytest

from a_testkit import make_env, posting
from ct_connectors import ConnectorError
from ct_matching.normalize import job_id
from ct_matching.store import JobStore, Ledger

T1, T2, T3 = "2026-11-01T00:00:00Z", "2026-11-05T00:00:00Z", "2026-11-08T00:00:00Z"
JID = job_id("synthetic", "fictionaltest", "p1")


def store_with(*envs):
    store = JobStore()
    reports = [store.import_envelope(e) for e in envs]
    return store, reports


# ------------------------------------------------------------------ versioning
def test_reimport_of_identical_capture_is_idempotent():
    env = make_env(postings=[posting()])
    store, reports = store_with(env, copy.deepcopy(env))
    assert reports[0]["counts"]["created"] == 1 and reports[1]["counts"]["unchanged"] == 1
    assert len(store.versions[JID]) == 1


def test_changed_content_creates_new_version_and_keeps_history():
    store, reports = store_with(make_env(captured_at=T1, postings=[posting()]),
                                make_env(captured_at=T2, postings=[posting(title="Staff Backend Engineer")]))
    assert reports[1]["counts"]["updated"] == 1
    chain = store.versions[JID]
    assert [e["ext"]["version"] for e in chain] == [1, 2]
    assert chain[1]["ext"]["supersedes"] == chain[0]["ext"]["content_hash"]
    assert chain[0]["posting"]["title"] == "Senior Backend Engineer"  # history is immutable
    event = [e for e in store.ledger.for_subject(JID) if e["kind"] == "version-added"][0]
    assert "title" in event["detail"]["changed"] and "seniority" in event["detail"]["changed"]


def test_refresh_only_moves_fetched_at():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]), make_env(captured_at=T2, postings=[posting()]))
    assert store.versions[JID][-1]["posting"]["fetched_at"] == T2 and len(store.versions[JID]) == 1


def test_time_reversal_older_capture_cannot_overwrite_newer_state():
    store, reports = store_with(make_env(captured_at=T2, postings=[posting(title="Staff Backend Engineer")]),
                                make_env(captured_at=T1, postings=[posting(title="OLD TITLE")]))
    assert reports[1]["counts"]["ignored_stale"] == 1
    assert store.get(JID, T3)["posting"]["title"] == "Staff Backend Engineer"
    assert store.ledger.for_subject(JID)[-1]["kind"] == "out-of-order-capture-ignored"


# ------------------------------------------------------------------ revocation
def test_posting_missing_from_later_full_snapshot_is_revoked_not_deleted():
    store, reports = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]),
                                make_env(captured_at=T2, postings=[posting("p2")]))
    assert reports[1]["counts"]["revoked"] == 1
    gone = store.get(JID, T2)
    assert gone["posting"]["status"] == "expired" and gone["ext"]["status_reason"] == "absent-from-source-snapshot"
    assert store.get(job_id("synthetic", "fictionaltest", "p2"), T2)["posting"]["status"] == "active"


def test_partial_snapshot_never_revokes():
    store, reports = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]),
                                make_env(captured_at=T2, postings=[posting("p2")], full_snapshot=False))
    assert reports[1]["counts"]["revoked"] == 0 and store.get(JID, T2)["posting"]["status"] == "active"


def test_empty_snapshot_does_not_mass_revoke():
    store, reports = store_with(make_env(captured_at=T1, postings=[posting("p1")]), make_env(captured_at=T2, postings=[]))
    assert reports[1]["counts"]["revoked"] == 0
    assert store.ledger.entries[-1]["kind"] == "empty-snapshot-ignored"


def test_snapshot_of_another_board_or_source_does_not_revoke():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting("p1")]),
                          make_env(board="otherboard", captured_at=T2, postings=[posting("zzz")]))
    assert store.get(JID, T2)["posting"]["status"] == "active"


def test_stale_snapshot_cannot_revoke_newer_observation():
    store, _ = store_with(make_env(captured_at=T2, postings=[posting("p1")]), make_env(captured_at=T1, postings=[posting("p2")]))
    assert store.get(JID, T2)["posting"]["status"] == "active"


def test_malformed_entry_in_snapshot_does_not_revoke_its_stored_version():
    broken = posting("p1")
    del broken["title"]
    store, reports = store_with(make_env(captured_at=T1, postings=[posting("p1")]),
                                make_env(captured_at=T2, postings=[broken, posting("p2")]))
    assert reports[1]["counts"]["revoked"] == 0 and reports[1]["counts"]["rejected"] == 1


def test_relisted_posting_becomes_active_again():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]),
                          make_env(captured_at=T2, postings=[posting("p2")]),
                          make_env(captured_at=T3, postings=[posting("p1"), posting("p2")]))
    assert store.get(JID, T3)["posting"]["status"] == "active"
    assert "relisted" in [e["kind"] for e in store.ledger.for_subject(JID)]


# ---------------------------------------------------------------------- status
def test_status_goes_unknown_when_capture_is_stale_never_stays_active():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]))
    assert store.get(JID, "2026-11-10T00:00:00Z")["posting"]["status"] == "active"
    late = store.get(JID, "2026-12-25T00:00:00Z")
    assert late["posting"]["status"] == "unknown" and late["ext"]["status_reason"] == "stale-capture"


def test_status_before_capture_time_is_unknown():
    store, _ = store_with(make_env(captured_at=T3, postings=[posting()]))
    assert store.get(JID, "2026-10-01T00:00:00Z")["posting"]["status"] == "unknown"


def test_expiry_is_evaluated_at_as_of():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting(expires_at="2026-11-04T00:00:00Z")]))
    assert store.get(JID, "2026-11-03T00:00:00Z")["posting"]["status"] == "active"
    assert store.get(JID, "2026-11-04T00:00:00Z")["posting"]["status"] == "expired"


def test_view_does_not_mutate_stored_status():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]))
    store.view("2027-01-01T00:00:00Z")
    assert store.versions[JID][-1]["posting"]["status"] == "active"


# ----------------------------------------------------------------------- dedup
def lever_env(board="fictionaltest", org="Fictional Test Co", uid=None, **over):
    job = {"id": "lv1", "text": "Senior Backend Engineer", "hostedUrl": "https://jobs.fictional-test.example/lv1",
           "workplaceType": "remote", "categories": {"location": "Austin, TX", "commitment": "Full-time"},
           "description": "Fictional posting for tests.<h3>Requirements</h3><ul><li>Python</li><li>API design</li>"
                          "<li>Distributed systems</li></ul><h3>Nice to have</h3><ul><li>AWS</li></ul>"}
    job.update(over)
    cap = {"source": "lever", "board": board, "captured_at": T1, "organization": org, "synthetic": True}
    if uid:
        cap["org_uid"] = uid
    return {"capture": cap, "payload": [job]}


def test_same_job_on_two_sources_is_one_cluster_with_stable_canonical():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]), lever_env())
    a, b = store.get(JID, T1), store.get(job_id("lever", "fictionaltest", "lv1"), T1)
    assert a["ext"]["dedup"]["cluster_id"] == b["ext"]["dedup"]["cluster_id"]
    assert b["ext"]["dedup"]["is_canonical"] and a["ext"]["dedup"]["duplicate_of"] == b["posting"]["id"]
    assert len(store.view(T1, canonical_only=True)) == 1 and len(store.view(T1)) == 2
    reverse, _ = store_with(lever_env(), make_env(captured_at=T1, postings=[posting()]))
    assert reverse.get(JID, T1)["ext"]["dedup"] == a["ext"]["dedup"]  # import order does not change the answer


def test_alias_spelling_of_same_employer_still_dedups():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting(organization="Fictional Test Co, Inc.")]),
                          lever_env(org="FICTIONAL TEST CO"))
    assert len(store.view(T1, canonical_only=True)) == 1


def test_same_name_different_registered_employer_is_never_merged():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()], org_uid="org-A"), lever_env(uid="org-B"))
    jobs = store.view(T1)
    assert all(j["ext"]["dedup"]["is_canonical"] for j in jobs)
    assert all(j["ext"]["employer"]["alias_ambiguous"] for j in jobs)


def test_same_title_different_role_content_is_not_a_duplicate():
    other = "Fictional posting.<h3>Requirements</h3><ul><li>Kubernetes</li><li>Terraform</li><li>Observability</li></ul>"
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]), lever_env(description=other))
    assert len(store.view(T1, canonical_only=True)) == 2


@pytest.mark.parametrize("change", [{"text": "Senior Frontend Engineer"}, {"categories": {"location": "Denver, CO"}},
                                    {"workplaceType": "on-site"}])
def test_different_title_location_or_policy_is_not_a_duplicate(change):
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]), lever_env(**change))
    assert len(store.view(T1, canonical_only=True)) == 2


def test_repost_within_board_is_flagged():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p1-repost")]))
    assert all(j["ext"]["dedup"]["repost_within_board"] for j in store.view(T1))
    assert len(store.view(T1, canonical_only=True)) == 1


# ---------------------------------------------------------- ledger/persistence
def test_ledger_detects_edit_reorder_and_truncation():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]),
                          make_env(captured_at=T2, postings=[posting("p2", title="Staff Backend Engineer")]))
    assert store.ledger.verify() == (True, None) and len(store.ledger.entries) >= 4
    edited = Ledger(copy.deepcopy(store.ledger.entries))
    edited.entries[1]["detail"]["content_hash"] = "f" * 64
    assert edited.verify() == (False, 1)
    swapped = Ledger(copy.deepcopy(store.ledger.entries))
    swapped.entries[0], swapped.entries[1] = swapped.entries[1], swapped.entries[0]
    assert swapped.verify()[0] is False
    removed = Ledger(copy.deepcopy(store.ledger.entries)[1:])
    assert removed.verify() == (False, 0)
    rehashed = Ledger(copy.deepcopy(store.ledger.entries))
    rehashed.entries[1]["detail"]["x"] = 1
    rehashed.entries[1]["hash"] = Ledger._digest(rehashed.entries[1])
    assert rehashed.verify() == (False, 2)  # the next link exposes a recomputed hash


def test_save_load_roundtrip_is_lossless_and_deterministic(tmp_path):
    store, _ = store_with(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]), lever_env())
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    store.save(a)
    loaded = JobStore.load(a)
    loaded.save(b)
    assert a.read_bytes() == b.read_bytes()
    assert loaded.view(T2) == store.view(T2)
    assert loaded.provenance(JID) == store.provenance(JID)


def test_load_refuses_tampered_ledger_and_foreign_files(tmp_path):
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]))
    path = tmp_path / "s.json"
    store.save(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["ledger"][0]["detail"]["source"] = "greenhouse"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ConnectorError):
        JobStore.load(path)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectorError):
        JobStore.load(path)


def test_provenance_reports_every_version_and_source_pin():
    store, _ = store_with(make_env(captured_at=T1, postings=[posting()]),
                          make_env(captured_at=T2, postings=[posting(title="Staff Backend Engineer")]))
    prov = store.provenance(JID)
    assert [v["version"] for v in prov["versions"]] == [1, 2]
    assert prov["versions"][1]["fetched_at"] == T2 and prov["source_id"] == "p1" and prov["synthetic"] is True
    assert all(any(s.startswith("payload-sha256:") for s in v["source_evidence"]) for v in prov["versions"])
    assert prov["ledger"]["verified"] and [e["kind"] for e in prov["events"]] == ["created", "version-added"]
    assert store.provenance("jp_missing") is None


def test_bad_envelope_is_classified_not_raised():
    store = JobStore()
    for doc in (None, [], {"capture": {}}, {"capture": {"source": "linkedin", "board": "b", "captured_at": T1}, "payload": []}):
        report = store.import_envelope(doc)
        assert report["ok"] is False and report["rejected"][0]["severity"] == "reject"
    assert store.versions == {} and len(store.quarantine) == 4
