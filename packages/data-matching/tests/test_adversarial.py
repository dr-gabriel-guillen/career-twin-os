"""Workstream 9: every adversarial case planted in the seeded corpus must be handled as documented."""
import json

from conftest import AS_OF
from ct_matching import corpus


def shortlist_ids(service, **prefs):
    result = service.rank({"as_of": AS_OF, "top_n": 500, "preferences": prefs} if prefs else {"as_of": AS_OF, "top_n": 500})
    return result, {s["job_id"] for s in result["shortlist"]}


def refusal_map(result):
    return {r["job_id"]: r["reasons"] for r in result["refusals"]}


def test_corpus_has_at_least_150_fictional_postings(service, bundle):
    jobs = service.store.view(AS_OF)
    assert len(jobs) >= 150
    assert all(j["ext"]["synthetic"] is True for j in jobs)
    assert all(j["posting"]["organization"].casefold().startswith("fictional") for j in jobs)
    assert all(j["posting"]["url"].split("/")[2].endswith(".example") for j in jobs)
    assert all("not a real vacancy" in j["posting"]["license_note"] for j in jobs)
    assert bundle["meta"]["synthetic"] is True


def test_malformed_entries_are_rejected_with_classes_and_nothing_else_is_lost(import_report, bundle):
    assert import_report["counts"]["rejected"] == bundle["truth"]["malformed_entries"] == 10
    assert import_report["rejected_by_class"] == {"duplicate_source_id": 1, "malformed_record": 2,
                                                  "missing_required_field": 3, "unsafe_url": 4}
    assert import_report["counts"]["created"] == len(bundle["truth"]["postings"])


def test_no_stored_url_is_unsafe(service):
    from ct_connectors.textsafe import url_problem
    for job in service.store.view(AS_OF):
        assert url_problem(job["posting"]["url"]) is None
        assert job["ext"]["apply_url"] is None or url_problem(job["ext"]["apply_url"]) is None


def test_cross_source_duplicates_collapse(by_tag):
    for job in by_tag("dup-cross-source") + by_tag("dup-cross-source-copy"):
        dedup = job["ext"]["dedup"]
        assert len(dedup["members"]) == 2 and not dedup["repost_within_board"]


def test_reposts_collapse_and_are_flagged(by_tag):
    for job in by_tag("dup-repost-copy"):
        assert len(job["ext"]["dedup"]["members"]) == 2 and job["ext"]["dedup"]["repost_within_board"]


def test_only_one_member_of_each_duplicate_cluster_can_be_shortlisted(service):
    result, ids = shortlist_ids(service)
    clusters = {}
    for job in service.store.view(AS_OF):
        clusters.setdefault(job["ext"]["dedup"]["cluster_id"], []).append(job["posting"]["id"])
    for members in clusters.values():
        assert len(ids & set(members)) <= 1
    dup_refusals = [r for r in result["refusals"] if r["reasons"][0].startswith("duplicate-of:")]
    assert len(dup_refusals) == sum(len(m) - 1 for m in clusters.values()) == 8


def test_alias_same_employer_merges_and_colliding_distinct_employer_does_not(by_tag):
    original, alias, other = by_tag("alias-original")[0], by_tag("alias-same-employer")[0], by_tag("alias-collision-distinct-employer")[0]
    assert original["ext"]["employer"]["key"] == alias["ext"]["employer"]["key"] == other["ext"]["employer"]["key"]
    assert original["ext"]["dedup"]["cluster_id"] == alias["ext"]["dedup"]["cluster_id"]
    assert other["ext"]["dedup"]["cluster_id"] != original["ext"]["dedup"]["cluster_id"] and other["ext"]["dedup"]["is_canonical"]
    assert other["ext"]["employer"]["alias_ambiguous"] and original["ext"]["employer"]["alias_ambiguous"]


def test_revoked_postings_are_expired_and_refused(service, by_tag):
    result, ids = shortlist_ids(service)
    refused = refusal_map(result)
    jobs = by_tag("round2-revoked")
    assert len(jobs) == 6
    for job in jobs:
        assert job["posting"]["status"] == "expired" and job["ext"]["revoked"] == "absent-from-source-snapshot"
        assert job["posting"]["id"] not in ids
        assert any("availability: expired" in r for r in refused[job["posting"]["id"]])
        kinds = [e["kind"] for e in service.store.provenance(job["posting"]["id"])["events"]]
        assert kinds[-1] == "revoked"


def test_round2_updates_are_new_versions_with_history(service, by_tag, truth):
    for job in by_tag("round2-updated"):
        prov = service.store.provenance(job["posting"]["id"])
        assert [v["version"] for v in prov["versions"]] == [1, 2]
        assert prov["versions"][0]["fetched_at"] < prov["versions"][1]["fetched_at"]
        change = truth[job["posting"]["id"]]["round2"]
        if "title_override" in change:
            assert job["posting"]["title"].endswith("(Updated Scope)")
        if "pay" in change:
            assert job["posting"]["compensation"]["max"] == 133000
        if "required" in change:
            assert "technical-writing" in job["ext"]["skills_required"]


def test_stale_capture_arriving_last_did_not_overwrite(service, import_report, bundle):
    assert import_report["counts"]["ignored_stale"] >= 1
    assert not [j for j in service.store.view(AS_OF) if j["posting"]["title"].startswith("STALE ")]
    kinds = {e["kind"] for e in service.store.ledger.entries}
    assert "out-of-order-capture-ignored" in kinds


def test_evergreen_phantoms_are_refused_by_default_and_reason_is_given(service, by_tag):
    result, ids = shortlist_ids(service)
    refused = refusal_map(result)
    for job in by_tag("phantom-evergreen"):
        assert "phantom-risk:evergreen-requisition" in job["ext"]["quality_flags"]
        assert job["posting"]["id"] not in ids
        assert any(r.startswith("phantom-risk") for r in refused[job["posting"]["id"]])


def test_long_open_postings_are_cautioned_not_silently_trusted(by_tag):
    for job in by_tag("phantom-long-open"):
        assert "phantom-risk:long-open" in job["ext"]["quality_flags"]


def test_poisoned_html_never_reaches_text_and_injection_is_refused(service, by_tag):
    result, ids = shortlist_ids(service)
    refused = refusal_map(result)
    for job in by_tag("poisoned-html-injection") + by_tag("poisoned-html-active-content"):
        text = job["ext"]["description_text"].lower()
        for needle in ("<script", "fetch(", "onerror", "<iframe", "document.cookie", "ignore all previous", "system prompt", "evil.example"):
            assert needle not in text
    for job in by_tag("poisoned-html-injection"):
        assert "prompt-injection-suspected" in job["ext"]["security_flags"]
        assert "hidden-content-removed" in job["ext"]["security_flags"]
        assert any(r.startswith("content-integrity") for r in refused[job["posting"]["id"]])
    for job in by_tag("poisoned-html-active-content"):
        assert "active-content-removed" in job["ext"]["security_flags"]
        assert "prompt-injection-suspected" not in job["ext"]["security_flags"]


def test_visible_prompt_injection_is_kept_as_data_flagged_and_refused(service, by_tag):
    result, ids = shortlist_ids(service)
    for job in by_tag("prompt-injection-visible"):
        assert "prompt-injection-suspected" in job["ext"]["security_flags"]
        assert job["posting"]["id"] not in ids


def test_injection_text_cannot_change_any_score(service, by_tag):
    """Even when the user opts in to flagged content, instructions in the text are inert."""
    job = by_tag("prompt-injection-visible")[0]
    result, ids = shortlist_ids(service, allow_flagged_content=True, include_unknown_status=True)
    item = next((s for s in result["shortlist"] if s["job_id"] == job["posting"]["id"]), None)
    if item is not None:
        assert item["score"] < 100 and "prompt-injection-suspected" in item["cautions"]
    assert all(s["score_type"] == "relative_heuristic_not_offer_probability" for s in result["shortlist"])


def test_hidden_keyword_stuffing_adds_no_skills(by_tag, truth):
    job = by_tag("keyword-stuffing-hidden")[0]
    latent = truth[job["posting"]["id"]]
    assert set(job["posting"]["skills"]) <= set(latent["required"]) | set(latent["preferred"]) | {
        "executive-communication", "enterprise-sales", "solution-demos", "compliance", "systems-integration", "customer-delivery"}
    assert len(job["posting"]["skills"]) < 15 and "hidden-content-removed" in job["ext"]["security_flags"]


def test_unicode_titles_are_cleaned_and_stay_valid_json(by_tag):
    titles = [j["posting"]["title"] for j in by_tag("unicode")]
    assert len(titles) == 6
    for title in titles:
        assert title and not any(ord(c) < 32 for c in title)
        assert not any(c in title for c in "​‍‮‬\x00")
        json.dumps(title).encode("utf-8")
    assert "Ingénieur Senior Backend" in titles and any("\U0001F680" in t for t in titles)


def test_contradictory_or_unusable_salary_is_null_with_reason(by_tag):
    expected = {"comp-structured-vs-text": "conflict:structured-vs-text", "comp-inverted": "conflict:inverted-range",
                "comp-two-ranges": "conflict:multiple-ranges", "comp-zero": "unparseable:zero",
                "comp-hourly": "non-annual", "comp-bare-dollar": "ambiguous-currency"}
    for tag, state in expected.items():
        job = by_tag(tag)[0]
        assert job["posting"]["compensation"] is None, tag
        assert job["ext"]["compensation"]["state"] == state, tag
        assert job["ext"]["compensation"]["stated"], tag


def test_no_compensation_is_hallucinated_anywhere(service, truth):
    """A posting has contract compensation iff the generator stated a clean annual range for it."""
    for job in service.store.view(AS_OF):
        latent = truth[job["posting"]["id"]]
        comp = job["posting"]["compensation"]
        if comp is not None:
            assert latent["pay_stated_cleanly"] or (latent["round2"] or {}).get("pay"), job["posting"]["id"]
            assert comp["verified"] is False and comp["currency"] in ("USD", "EUR", "GBP", "CAD")
            assert comp["max"] is None or comp["max"] >= 1000
        else:
            assert job["ext"]["compensation"]["state"] != "stated"


def test_missing_location_stays_null(by_tag):
    for job in by_tag("missing-location"):
        assert job["posting"]["location"] is None and job["ext"]["location"]["jurisdictions"] == []


def test_time_anomalies_make_status_unknown_and_refused(service, by_tag):
    result, ids = shortlist_ids(service)
    for tag in ("time-posted-in-future", "time-updated-before-posted", "time-expires-before-posted"):
        job = by_tag(tag)[0]
        assert job["posting"]["status"] == "unknown" and job["ext"]["status_reason"] == "time-anomaly", tag
        assert job["posting"]["id"] not in ids


def test_expired_and_delisted_are_never_active(service, by_tag):
    result, ids = shortlist_ids(service)
    for job in by_tag("expired-by-date") + by_tag("delisted"):
        assert job["posting"]["status"] == "expired" and job["posting"]["id"] not in ids


def test_shortlist_contains_no_latently_inactive_posting(service, truth):
    result, ids = shortlist_ids(service)
    assert len(ids) == result["corpus"]["eligible"] > 20
    assert [j for j in ids if not truth[j]["active"]] == []


def test_every_shortlisted_job_is_active_at_as_of_and_fresh(service):
    result, _ = shortlist_ids(service)
    for item in result["shortlist"]:
        assert item["availability"]["status"] == "active"
        assert item["availability"]["fetched_at"] <= AS_OF


def test_a_month_later_nothing_is_presented_as_available(service):
    result = service.rank({"as_of": "2026-12-20T00:00:00Z", "top_n": 50})
    assert result["shortlist"] == [] and result["corpus"]["refusal_reasons"]["availability"] > 100
    opted = service.rank({"as_of": "2026-12-20T00:00:00Z", "top_n": 50, "preferences": {"include_unknown_status": True}})
    assert opted["shortlist"] and all(s["availability"]["status"] == "unknown" for s in opted["shortlist"])
    assert all("constraint:availability" in s["unknown_inputs"] for s in opted["shortlist"])


def test_corpus_is_reproducible_by_seed():
    a, b = corpus.generate(), corpus.generate()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(corpus.generate(seed=7), sort_keys=True) != json.dumps(a, sort_keys=True)
