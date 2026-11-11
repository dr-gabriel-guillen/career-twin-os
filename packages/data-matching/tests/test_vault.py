import copy

import pytest

from a_testkit import AS_OF
from ct_matching import schema
from ct_matching.store import Ledger
from ct_matching.vault import EvidenceVault, VaultError

T = "2026-11-01T00:00:00Z"
H = "d" * 64


@pytest.fixture()
def vault():
    return EvidenceVault("candidate-synthetic-test")


def code(excinfo):
    return excinfo.value.code


# ------------------------------------------------------------ derived status
def test_claim_without_evidence_is_unverified(vault):
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"])
    assert vault.claim(cid)["status"] == "unverified" and vault.claim(cid)["visibility"] == "private"


def test_self_statement_is_self_reported_never_supported(vault):
    ev = vault.add_evidence("self-statement", "vault://self/form", T)
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"], evidence_ids=[ev])
    assert vault.claim(cid)["status"] == "self-reported"


def test_work_sample_with_hash_supports_a_skill(vault):
    ev = vault.add_evidence("work-sample", "vault://samples/repo", T, sha256=H)
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"], evidence_ids=[ev])
    assert vault.claim(cid)["status"] == "supported"


def test_verifying_kind_without_hash_does_not_support(vault):
    ev = vault.add_evidence("work-sample", "vault://samples/repo", T)
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"], evidence_ids=[ev])
    assert vault.claim(cid)["status"] == "unverified"


@pytest.mark.parametrize("claim_type", ["credential", "education", "employment"])
@pytest.mark.parametrize("kind", ["work-sample", "third-party-attestation", "self-statement", "resume-prose"])
def test_credentials_education_employment_need_issuer_or_document_evidence(vault, claim_type, kind):
    ev = vault.add_evidence(kind, "vault://x/y", T, sha256=H)
    cid = vault.add_claim(claim_type, "Fictional Certified Architect", T, evidence_ids=[ev])
    assert vault.claim(cid)["status"] != "supported"


@pytest.mark.parametrize("kind", ["issuer-verification", "document-hash"])
def test_credential_supported_with_issuer_or_document_hash(vault, kind):
    ev = vault.add_evidence(kind, "vault://issuer/cert", T, sha256=H)
    cid = vault.add_claim("credential", "Fictional Certified Architect", T, evidence_ids=[ev])
    assert vault.claim(cid)["status"] == "supported"


def test_status_cannot_be_set_by_caller(vault):
    with pytest.raises(TypeError):
        vault.add_claim("skill", "Knows Python", T, status="supported")  # type: ignore[call-arg]


def test_attaching_then_revoking_evidence_moves_status_both_ways(vault):
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"])
    ev = vault.add_evidence("work-sample", "vault://samples/repo", T, sha256=H)
    assert vault.attach_evidence(cid, ev, T) == "supported"
    vault.revoke_evidence(ev, T, "sample withdrawn")
    assert vault.claim(cid)["status"] == "unverified"
    kinds = [e["kind"] for e in vault.ledger.for_subject(cid)]
    assert kinds.count("status-derived") == 2


# -------------------------------------------------------------- résumé prose
PROSE = """Fictional summary. Built Python services on AWS and Kubernetes.
- AWS Certified Solutions Architect (fictional)
- PhD in Computer Science, Fictional University
Led customer deployments for enterprise accounts."""


def test_resume_prose_yields_only_self_reported_claims(vault):
    ids = vault.ingest_resume_prose(PROSE, T)
    claims = [vault.claim(c) for c in ids]
    assert {c["status"] for c in claims} == {"self-reported"}
    assert {"python", "aws", "kubernetes", "customer-delivery"} <= {s for c in claims for s in c["skills"]}
    creds = [c for c in claims if c["claim_type"] == "credential"]
    assert len(creds) == 2 and all(c["status"] == "self-reported" for c in creds)


def test_resume_prose_text_is_not_stored_only_its_hash(vault):
    vault.ingest_resume_prose(PROSE, T)
    dump = str(vault.to_dict())
    assert "Fictional summary" not in dump and "Led customer deployments" not in dump
    ev = [e for e in vault.evidence.values() if e["kind"] == "resume-prose"][0]
    assert len(ev["sha256"]) == 64 and ev["pointer"].startswith("vault://prose/")


def test_prose_credential_cannot_be_made_public_or_supported_without_evidence(vault):
    cid = [c for c in vault.ingest_resume_prose(PROSE, T) if vault.claim(c)["claim_type"] == "credential"][0]
    with pytest.raises(VaultError) as err:
        vault.set_visibility(cid, "public-approved", T, approval_ref="user-ok-1")
    assert code(err) == "not-supported"
    ev = vault.add_evidence("issuer-verification", "vault://issuer/check-1", T, sha256=H)
    assert vault.attach_evidence(cid, ev, T) == "supported"


def test_reingesting_same_prose_is_idempotent(vault):
    first = vault.ingest_resume_prose(PROSE, T)
    assert vault.ingest_resume_prose(PROSE, T) == first and len(vault.claims) == len(first)


# ------------------------------------------------------------------ conflicts
def test_conflict_marks_both_claims_contradicted_and_withholds_them(vault):
    ev = vault.add_evidence("document-hash", "vault://docs/a", T, sha256=H)
    a = vault.add_claim("employment", "Worked at Fictional Co 2019-2023", T, evidence_ids=[ev])
    b = vault.add_claim("employment", "Worked at Fictional Co 2021-2025", T)
    vault.register_conflict(a, T, "overlapping, inconsistent dates", other_claim_id=b)
    assert vault.claim(a)["status"] == vault.claim(b)["status"] == "contradicted"
    for mode in ("internal-matching", "resume-draft", "public"):
        assert vault.export(mode, AS_OF)["claims"] == []
    assert vault.resolve_conflicts(a, T, "dates corrected by user") == "supported"
    assert vault.claim(b)["status"] == "contradicted"


def test_contradicted_public_claim_fails_closed_to_private(vault):
    ev = vault.add_evidence("work-sample", "vault://samples/repo", T, sha256=H)
    cid = vault.add_claim("skill", "Knows Python", T, skills=["python"], evidence_ids=[ev])
    vault.set_visibility(cid, "public-approved", T, approval_ref="user-ok-1")
    vault.register_conflict(cid, T, "counter-evidence received")
    assert vault.claim(cid)["visibility"] == "private" and vault.export("public", AS_OF)["claims"] == []
    vault.resolve_conflicts(cid, T, "counter-evidence withdrawn")
    assert vault.claim(cid)["visibility"] == "private", "approval must be given again, not silently restored"


# -------------------------------------------------------------- export gates
def build(vault):
    work = vault.add_evidence("work-sample", "vault://samples/repo", T, sha256=H)
    selfy = vault.add_evidence("self-statement", "vault://self/form", T)
    ids = {"supported_private": vault.add_claim("skill", "Python services", T, skills=["python"], evidence_ids=[work]),
           "supported_shareable": vault.add_claim("skill", "API design", T, skills=["api-design"], evidence_ids=[work]),
           "supported_public": vault.add_claim("skill", "Distributed systems", T, skills=["distributed-systems"], evidence_ids=[work]),
           "self_shareable": vault.add_claim("skill", "TypeScript", T, skills=["typescript"], evidence_ids=[selfy]),
           "unverified_shareable": vault.add_claim("skill", "Rust", T, skills=["rust"]),
           "expired": vault.add_claim("credential", "Fictional lapsed cert", T, evidence_ids=[vault.add_evidence(
               "issuer-verification", "vault://issuer/x", T, sha256=H)], valid_to="2026-01-01T00:00:00Z")}
    for key in ("supported_shareable", "self_shareable", "unverified_shareable", "expired"):
        vault.set_visibility(ids[key], "shareable", T)
    vault.set_visibility(ids["supported_public"], "public-approved", T, approval_ref="user-approval-0001")
    return ids


def exported(vault, mode):
    out = vault.export(mode, AS_OF)
    for claim in out["claims"]:
        schema.check(claim, "profile-claim")
    return {c["id"] for c in out["claims"]}, out["withheld"]


def test_internal_matching_export_includes_private_but_not_expired(vault):
    ids = build(vault)
    got, withheld = exported(vault, "internal-matching")
    assert got == {ids[k] for k in ids if k != "expired"} and withheld["expired"] == 1


def test_resume_draft_export_excludes_private_and_unverified(vault):
    ids = build(vault)
    got, withheld = exported(vault, "resume-draft")
    assert got == {ids["supported_shareable"], ids["supported_public"], ids["self_shareable"]}
    assert withheld == {"contradicted": 0, "expired": 1, "visibility": 1, "status": 1}


def test_public_export_needs_approval_and_support(vault):
    ids = build(vault)
    got, _ = exported(vault, "public")
    assert got == {ids["supported_public"]}


def test_public_approval_requires_reference_and_supported_status(vault):
    ids = build(vault)
    for ref in (None, "", "   "):
        with pytest.raises(VaultError) as err:
            vault.set_visibility(ids["supported_private"], "public-approved", T, approval_ref=ref)
        assert code(err) == "approval-required"
    with pytest.raises(VaultError) as err:
        vault.set_visibility(ids["self_shareable"], "public-approved", T, approval_ref="user-approval-2")
    assert code(err) == "not-supported"


def test_unknown_export_mode_refused(vault):
    with pytest.raises(VaultError) as err:
        vault.export("everything", AS_OF)
    assert code(err) == "bad-export-mode"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("pointer", ["https://drive.example/cv.pdf", "C:\\Users\\x\\cv.pdf", "vault://", "vault://a b", "",
                                     "vault://x?token=1", None, "vault://" + "a" * 300])
def test_evidence_pointer_must_be_opaque_vault_locator(vault, pointer):
    with pytest.raises(VaultError) as err:
        vault.add_evidence("document-hash", pointer, T, sha256=H)
    assert code(err) == "bad-pointer"


@pytest.mark.parametrize("args,expected", [
    (("telepathy", "vault://x/y", T), "bad-evidence-kind"),
    (("document-hash", "vault://x/y", "2026-11-01"), "bad-timestamp"),
    (("document-hash", "vault://x/y", "2026-11-01T00:00:00"), "bad-timestamp"),
])
def test_evidence_validation(vault, args, expected):
    with pytest.raises(VaultError) as err:
        vault.add_evidence(*args)
    assert code(err) == expected


@pytest.mark.parametrize("sha", ["abc", "D" * 64, "g" * 64, 5])
def test_evidence_hash_must_be_lowercase_sha256(vault, sha):
    with pytest.raises(VaultError) as err:
        vault.add_evidence("document-hash", "vault://x/y", T, sha256=sha)
    assert code(err) == "bad-hash"


def test_claim_validation(vault):
    for kwargs, expected in ((dict(claim_type="superpower", claim_text="x"), "bad-claim-type"),
                             (dict(claim_type="skill", claim_text=" \u200b "), "empty-claim"),
                             (dict(claim_type="skill", claim_text="x", evidence_ids=["ev_missing"]), "unknown-evidence")):
        with pytest.raises(VaultError) as err:
            vault.add_claim(at=T, **kwargs)
        assert code(err) == expected
    with pytest.raises(VaultError):
        EvidenceVault("")


def test_unknown_skill_tags_on_claims_are_dropped_not_invented(vault):
    cid = vault.add_claim("skill", "Does things", T, skills=["python", "telekinesis", 7])
    assert vault.claim(cid)["skills"] == ["python"]


def test_duplicate_claim_merges_evidence(vault):
    a = vault.add_claim("skill", "Knows  Python", T, skills=["python"])
    ev = vault.add_evidence("work-sample", "vault://samples/repo", T, sha256=H)
    b = vault.add_claim("skill", "knows python", T, evidence_ids=[ev])
    assert a == b and vault.claim(a)["status"] == "supported" and len(vault.claims) == 1


# ------------------------------------------------------------ proof / ledger
def test_skill_proof_takes_strongest_usable_claim_and_reports_excluded(vault):
    build(vault)
    weak = vault.add_claim("skill", "Python hobbyist", T, skills=["python"])
    bad = vault.add_claim("skill", "Python guru", T, skills=["python"])
    vault.register_conflict(bad, T, "reference disagrees")
    proof = vault.skill_proof(AS_OF)
    assert proof["python"]["status"] == "supported" and proof["python"]["weight"] == 1.0
    assert weak in proof["python"]["claim_ids"] and proof["python"]["contradicted_claim_ids"] == [bad]
    assert proof["typescript"]["weight"] == 0.5 and proof["rust"]["weight"] == 0.25


def test_skill_only_backed_by_contradicted_or_expired_claims_counts_for_nothing(vault):
    a = vault.add_claim("skill", "Deep learning researcher", T, skills=["deep-learning"])
    vault.register_conflict(a, T, "reference disagrees")
    ev = vault.add_evidence("issuer-verification", "vault://issuer/x", T, sha256=H)
    vault.add_claim("credential", "Lapsed compliance cert", T, skills=["compliance"], evidence_ids=[ev], valid_to="2026-01-01T00:00:00Z")
    proof = vault.skill_proof(AS_OF)
    assert proof["deep-learning"]["status"] is None and proof["deep-learning"]["weight"] == 0.0
    assert proof["compliance"]["status"] is None and proof["compliance"]["expired_claim_ids"]


def test_vault_roundtrip_and_tamper_detection(vault):
    build(vault)
    doc = copy.deepcopy(vault.to_dict())
    again = EvidenceVault.from_dict(copy.deepcopy(doc))
    assert again.export("resume-draft", AS_OF) == vault.export("resume-draft", AS_OF)
    forged = copy.deepcopy(doc)
    target = next(c for c in forged["claims"].values() if c["status"] == "unverified")
    target["status"] = "supported"
    with pytest.raises(VaultError) as err:
        EvidenceVault.from_dict(forged)
    assert code(err) == "status-not-derivable"
    cut = copy.deepcopy(doc)
    cut["ledger"][2]["detail"]["kind"] = "issuer-verification"
    with pytest.raises(VaultError) as err:
        EvidenceVault.from_dict(cut)
    assert code(err) == "ledger-tampered"


def test_claim_provenance_lists_evidence_and_events(vault):
    ids = build(vault)
    prov = vault.provenance(ids["supported_public"])
    assert prov["evidence"][0]["kind"] == "work-sample" and prov["ledger"]["verified"]
    assert [e["kind"] for e in prov["events"]] == ["claim-added", "status-derived", "visibility-set"]
    assert isinstance(vault.ledger, Ledger) and vault.ledger.verify() == (True, None)
