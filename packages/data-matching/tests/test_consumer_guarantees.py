"""Guarantees lane A acknowledged to consumers (B: Issue #2 NEEDS-INTERFACE; C/D: hub RFC)."""
import copy
import re

from a_testkit import AS_OF, make_env, posting
from ct_matching.normalize import employer_id, job_id
from ct_matching.store import JobStore
from ct_matching.vault import EvidenceVault

T1, T2 = "2026-11-01T00:00:00Z", "2026-11-05T00:00:00Z"
H = "9" * 64


# B-1: JobPosting.id is stable for the life of a posting
def test_job_id_survives_refetch_content_edits_revocation_and_relisting():
    store = JobStore()
    jid = job_id("synthetic", "fictionaltest", "p1")
    store.import_envelope(make_env(captured_at=T1, postings=[posting("p1"), posting("p2")]))
    store.import_envelope(make_env(captured_at=T2, postings=[posting("p2")]))                       # revoked
    store.import_envelope(make_env(captured_at="2026-11-06T00:00:00Z", postings=[posting("p1", title="Staff Engineer", description="new"), posting("p2")]))
    assert list(store.versions) == [jid, job_id("synthetic", "fictionaltest", "p2")] or set(store.versions) == {jid, job_id("synthetic", "fictionaltest", "p2")}
    assert [e["ext"]["version"] for e in store.versions[jid]] == [1, 2]
    assert all(e["posting"]["id"] == jid for e in store.versions[jid])


def test_repost_gets_new_id_but_is_linked_through_dedup_members():
    store = JobStore()
    store.import_envelope(make_env(captured_at=T1, postings=[posting("p1"), posting("p1-again")]))
    a, b = store.view(T1)
    assert a["posting"]["id"] != b["posting"]["id"]
    assert a["ext"]["dedup"]["members"] == b["ext"]["dedup"]["members"] and len(a["ext"]["dedup"]["members"]) == 2


# B-2: stable opaque employer id
def test_employer_id_is_opaque_stable_and_alias_insensitive(service):
    assert employer_id("Fictional Meridian Systems") == employer_id("FICTIONAL  MERIDIAN SYSTEMS, Inc.")
    assert employer_id("Fictional Meridian Systems") != employer_id("Fictional Meridian Labs")
    for job in service.store.view(AS_OF):
        emp = job["ext"]["employer"]
        assert re.fullmatch(r"emp_[0-9a-f]{16}", emp["id"]) and emp["id"] == employer_id(job["posting"]["organization"])


def test_employer_id_does_not_depend_on_store_state_or_import_order():
    a, b = JobStore(), JobStore()
    one = make_env(captured_at=T1, postings=[posting("p1")], org_uid="org-A")
    two = make_env(board="otherboard", captured_at=T1, postings=[posting("p9")], org_uid="org-B")
    for store, order in ((a, (one, two)), (b, (two, one))):
        for env in order:
            store.import_envelope(copy.deepcopy(env))
    ids_a = {j["posting"]["id"]: j["ext"]["employer"]["id"] for j in a.view(T1)}
    ids_b = {j["posting"]["id"]: j["ext"]["employer"]["id"] for j in b.view(T1)}
    assert ids_a == ids_b and len(set(ids_a.values())) == 1
    assert all(j["ext"]["employer"]["alias_ambiguous"] for j in a.view(T1))  # two registered entities share the name


# B-3 / B-4: claim id, updated_at and evidence semantics
def test_claim_id_is_stable_under_state_changes_and_updated_at_moves():
    vault = EvidenceVault("candidate-synthetic-b")
    cid = vault.add_claim("skill", "Knows Python", T1, skills=["python"])
    ev = vault.add_evidence("work-sample", "vault://samples/a", T1, sha256=H)
    ev2 = vault.add_evidence("work-sample", "vault://samples/b", T1, sha256=H)
    vault.attach_evidence(cid, ev, T2)
    assert vault.claim(cid)["id"] == cid and vault.claim(cid)["updated_at"] == T2
    vault.attach_evidence(cid, ev2, "2026-11-06T00:00:00Z")          # status unchanged, evidence changed
    assert vault.claim(cid)["updated_at"] == "2026-11-06T00:00:00Z"
    vault.revoke_evidence(ev2, "2026-11-07T00:00:00Z", "withdrawn")   # still supported by ev
    assert vault.claim(cid)["status"] == "supported" and vault.claim(cid)["updated_at"] == "2026-11-07T00:00:00Z"
    vault.set_visibility(cid, "shareable", "2026-11-08T00:00:00Z")
    assert vault.claim(cid)["updated_at"] == "2026-11-08T00:00:00Z"


def test_editing_claim_text_is_a_new_claim_id_old_one_is_untouched():
    vault = EvidenceVault("candidate-synthetic-b")
    a = vault.add_claim("skill", "Knows Python", T1, skills=["python"])
    b = vault.add_claim("skill", "Knows Python very well", T2, skills=["python"])
    assert a != b and vault.claim(a)["claim_text"] == "Knows Python" and vault.claim(a)["updated_at"] == T1


def test_exported_supported_claims_always_carry_live_evidence(service):
    vault = EvidenceVault("candidate-synthetic-b")
    keep = vault.add_evidence("work-sample", "vault://samples/a", T1, sha256=H)
    gone = vault.add_evidence("work-sample", "vault://samples/b", T1, sha256=H)
    cid = vault.add_claim("skill", "Knows Python", T1, skills=["python"], evidence_ids=[keep, gone])
    vault.revoke_evidence(gone, T2, "withdrawn")
    claim = vault.export("internal-matching", AS_OF)["claims"][0]
    assert claim["id"] == cid and claim["status"] == "supported" and claim["evidence_ids"] == [keep]
    for mode in ("internal-matching", "resume-draft", "public"):
        for item in service.vault.export(mode, AS_OF)["claims"]:
            assert item["status"] != "supported" or item["evidence_ids"]
            assert item["status"] != "contradicted"


# B-5: one vocabulary for job skills and claim skills
def test_job_skills_and_claim_skills_share_the_taxonomy_vocabulary(service):
    from ct_matching import taxonomy
    vocab = set(taxonomy.SKILL_IDS)
    for job in service.store.view(AS_OF):
        assert set(job["posting"]["skills"]) <= vocab
    for claim in service.vault.export("internal-matching", AS_OF)["claims"]:
        assert set(claim["skills"]) <= vocab
    assert all(s == s.lower().strip() and " " not in s for s in vocab)
