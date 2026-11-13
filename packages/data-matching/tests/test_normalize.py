import pytest

from a_testkit import make_env, posting
from ct_connectors import parse_envelope
from ct_matching.normalize import (comp_from_text, employer_key, employment_of, job_id, location_of, normalize,
                                   remote_of, resolve_compensation, seniority_of, split_sections, title_key)


def norm(**over):
    return normalize(parse_envelope(make_env(postings=[posting(**over)])).records[0])


# ------------------------------------------------------------------- identity
def test_job_id_is_stable_and_content_independent():
    a, b = norm(), norm(title="Completely Different Title", description="other")
    assert a["posting"]["id"] == b["posting"]["id"] == job_id("synthetic", "fictionaltest", "p1")
    assert a["ext"]["content_hash"] != b["ext"]["content_hash"]


def test_job_id_separates_sources_boards_and_ids():
    ids = {job_id("lever", "b", "1"), job_id("greenhouse", "b", "1"), job_id("lever", "c", "1"), job_id("lever", "b", "2")}
    assert len(ids) == 4
    assert job_id("lever", "Board", "1") == job_id("lever", "board", "1")
    assert job_id("lever", "b", "1|x") != job_id("lever", "b|1", "x")  # separator cannot be forged


@pytest.mark.parametrize("a,b", [("Fictional Apex Labs, Inc.", "FICTIONAL APEX LABS"), ("Fictional Apex Labs GmbH", "fictional apex labs"),
                                 ("Fictional  Café  Systems Ltd.", "fictional cafe systems"), ("Ｆｉｃｔｉｏｎａｌ Apex Labs LLC", "Fictional Apex Labs")])
def test_employer_key_folds_aliases(a, b):
    assert employer_key(a) == employer_key(b)


def test_employer_key_keeps_distinct_names_distinct():
    assert employer_key("Fictional Apex Labs") != employer_key("Fictional Apex Laboratories")
    assert employer_key("Fictional Co Op") != employer_key("Fictional")


def test_title_key_ignores_decorations_only():
    assert title_key("Sr. Backend Engineer (Remote)") == title_key("senior backend engineer")
    assert title_key("Backend Engineer") != title_key("Frontend Engineer")


# ------------------------------------------------------------ seniority / type
@pytest.mark.parametrize("title,raw,expected", [
    ("Senior Backend Engineer", None, "senior"), ("Sr. Data Engineer", None, "senior"), ("Staff Engineer", None, "staff"),
    ("Principal Architect", None, "principal"), ("Junior Developer", None, "junior"), ("Software Engineer II", None, "mid"),
    ("Software Engineer III", None, "senior"), ("Director of Engineering", None, "director"), ("VP Engineering", None, "vp"),
    ("Engineering Intern", None, "intern"), ("Backend Engineer", None, "unknown"), ("Field CTO", None, "unknown"),
    ("Field CTO", "director", "director"), ("Backend Engineer", "Staff", "staff"), ("Chief Technology Officer", None, "executive"),
    ("Seniority Analyst", None, "unknown"), ("Product Manager", None, "unknown"),
])
def test_seniority(title, raw, expected):
    assert seniority_of(title, raw) == expected


@pytest.mark.parametrize("raw,title,expected", [
    ("Full-time", "x", "full-time"), ("FullTime", "x", "full-time"), ("Contract", "x", "contract"), ("Part-time", "x", "part-time"),
    ("Internship", "x", "internship"), (None, "Data Engineer (Contract)", "contract"), (None, "Contracts Manager", "unknown"),
    (None, "Partner Engineer", "unknown"), ("Unpaid trial", "x", "unknown"), (None, "Engineer", "unknown"),
])
def test_employment_type_is_only_what_was_stated(raw, title, expected):
    assert employment_of(raw, title) == expected


# --------------------------------------------------------------- remote policy
@pytest.mark.parametrize("raw,loc,expected,flag", [
    ("Remote", "Austin, TX", "remote", None), ("on-site", None, "onsite", None), ("Hybrid", "Berlin", "hybrid", None),
    (None, "Remote - US", "remote", None), ("unspecified", "Austin, TX", "unknown", None), (None, None, "unknown", None),
    ("On-site", "Remote", "unknown", "remote-policy-conflict"), ("Remote or Hybrid", None, "unknown", "remote-policy-conflict"),
    ("Not remote", "Austin", "unknown", None),
])
def test_remote_policy_unknown_stays_unknown_and_conflicts_resolve_to_unknown(raw, loc, expected, flag):
    assert remote_of(raw, loc) == (expected, flag)


# -------------------------------------------------------------------- location
def test_location_explicit_country():
    loc, info = location_of("London, United Kingdom", None)
    assert loc == "London, United Kingdom" and info["jurisdictions"] == ["GB"] and info["jurisdiction_basis"] == "explicit-country"


def test_location_us_state_code():
    assert location_of("Austin, TX", None)[1]["jurisdictions"] == ["US"]


def test_location_ambiguous_ca_is_not_guessed():
    _, info = location_of("San Francisco, CA", None)
    assert info["jurisdictions"] == [] and "notes" in info
    assert location_of("San Francisco, CA", "US")[1]["jurisdictions"] == ["US"]
    assert location_of("San Francisco, CA, USA", None)[1]["jurisdictions"] == ["US"]


@pytest.mark.parametrize("raw", [None, "", "TBD", "N/A", "Multiple Locations", "Remote", "  ", "Anywhere"])
def test_missing_location_stays_null(raw):
    loc, info = location_of(raw, None)
    assert loc is None and info["jurisdictions"] == [] and info["parts"] == []


def test_remote_with_country_keeps_jurisdiction():
    loc, info = location_of("Remote - United States", None)
    assert loc == "Remote - United States" and info["jurisdictions"] == ["US"]


def test_multi_location_and_country_conflict():
    _, info = location_of("New York, NY or London, United Kingdom", None)
    assert info["multi"] and info["jurisdictions"] == ["GB", "US"]
    _, info = location_of("Berlin, Germany", "US")
    assert info["jurisdictions"] == [] and info["jurisdiction_basis"] == "conflict"


def test_unknown_city_without_country_has_no_jurisdiction():
    assert location_of("Springfield", None)[1]["jurisdictions"] == []


# ---------------------------------------------------------------- compensation
S = lambda lo, hi, cur="USD", per="year": {"min": lo, "max": hi, "currency": cur, "period": per, "basis": "base", "origin": "structured-field"}  # noqa: E731


def test_stated_annual_pay_passes_through_unverified():
    comp, detail = resolve_compensation([S(150000.0, 180000.0)], "")
    assert comp == {"currency": "USD", "min": 150000.0, "max": 180000.0, "basis": "base", "verified": False}
    assert detail["state"] == "stated"


def test_absent_pay_is_null_never_zero():
    comp, detail = resolve_compensation([], "Great benefits and competitive salary.")
    assert comp is None and detail["state"] == "absent" and detail["stated"] == []


@pytest.mark.parametrize("structured,text,state", [
    ([S(200000.0, 120000.0)], "", "conflict:inverted-range"),
    ([S(150000.0, 180000.0)], "USD 60,000 - 70,000 per year", "conflict:structured-vs-text"),
    ([S(150000.0, 180000.0), S(90000.0, 100000.0)], "", "conflict:multiple-ranges"),
    ([S(150000.0, 180000.0), S(120000.0, 140000.0, "GBP")], "", "multiple-ranges"),
    ([], "USD 100,000 - 120,000 per year or USD 180,000 - 220,000 per year", "conflict:multiple-ranges"),
    ([S(0.0, 0.0)], "", "unparseable:zero"),
    ([S(70.0, 95.0, per="hour")], "", "non-annual"),
    ([S(150000.0, 180000.0, per="unknown")], "", "period-unknown"),
    ([S(150000.0, 180000.0, cur=None)], "", "ambiguous-currency"),
    ([], "$150,000 - $180,000 per year", "ambiguous-currency"),
])
def test_unusable_pay_statements_yield_null_with_reason(structured, text, state):
    comp, detail = resolve_compensation(structured, text)
    assert comp is None and detail["state"] == state
    assert detail["stated"], "the source's own statement must be preserved for audit"


def test_agreeing_text_and_structured_is_not_a_conflict():
    comp, detail = resolve_compensation([S(150000.0, 180000.0)], "Pay: USD 150,000 - 180,000 per year")
    assert comp and detail["state"] == "stated"


def test_hourly_rate_is_never_annualized():
    comp, detail = resolve_compensation([S(70.0, 95.0, per="hour")], "")
    assert comp is None and detail["stated"][0]["max"] == 95.0 and detail["period"] == "hour"


@pytest.mark.parametrize("text,expected", [
    ("USD 120,000 - 150,000 per year", (120000.0, 150000.0, "USD", "year")),
    ("€70.000 – €90.000 per year", (70000.0, 90000.0, "EUR", "year")),
    ("£60k to £75k annually", (60000.0, 75000.0, "GBP", "year")),
    ("US$120-150k a year", (120000.0, 150000.0, "USD", "year")),
    ("$55 - $70 per hour", (55.0, 70.0, None, "hour")),
    ("$150,000 - $180,000", (150000.0, 180000.0, None, "unknown")),
])
def test_comp_text_parser(text, expected):
    item = comp_from_text(text)[0]
    assert (item["min"], item["max"], item["currency"], item["period"]) == expected


@pytest.mark.parametrize("text", ["We raised $40 million in Series B.", "Over 500 customers and 3 offices.",
                                  "Join 10,000 - 20,000 users", "Call 555-0100 to apply", "$5 lunch vouchers"])
def test_comp_text_parser_ignores_non_salary_numbers(text):
    assert comp_from_text(text) == []


def test_zero_lower_bound_is_not_a_floor():
    comp, _ = resolve_compensation([S(0.0, 200000.0)], "")
    assert comp["min"] is None and comp["max"] == 200000.0


# ---------------------------------------------------------------------- skills
def test_sections_split_required_from_preferred():
    env = norm()
    assert env["ext"]["skills_required"] == ["api-design", "distributed-systems", "python"]
    assert env["ext"]["skills_preferred"] == ["aws"]
    assert env["posting"]["skills"] == ["api-design", "aws", "distributed-systems", "python"]
    assert env["posting"]["requirements"] == ["Python", "API design", "Distributed systems"]


def test_benefits_section_does_not_create_skill_requirements():
    env = norm(description="Requirements\n- Python\nBenefits\n- Security badge, compliance training, statistics club")
    assert env["ext"]["skills_required"] == ["python"]


def test_skill_substrings_do_not_match():
    env = norm(description="Requirements\n- We trust JavaScripters who gopher around; Javanese speakers welcome; raggedy sqlite")
    assert env["posting"]["skills"] == []


def test_duty_mentions_are_preferred_not_required():
    env = norm(description="About the role\nYou will run executive briefings.\nRequirements\n- Python")
    assert env["ext"]["skills_required"] == ["python"] and env["ext"]["skills_preferred"] == ["executive-communication"]


def test_no_recognizable_skills_gives_empty_list_not_invented():
    env = norm(description="We are a fictional company. Join us.")
    assert env["posting"]["skills"] == [] and env["ext"]["skills_required"] == []


def test_declared_tags_map_to_taxonomy_and_unknown_tags_are_kept_aside():
    env = norm(skills=["Python", "K8s", "underwater basket weaving"], description="x")
    assert env["ext"]["skills_required"] == ["kubernetes", "python"]
    assert env["ext"]["skills_unmapped"] == ["underwater basket weaving"]


def test_keyword_stuffing_is_capped_and_flagged():
    from ct_matching import taxonomy
    env = norm(description="Requirements\n- " + ", ".join(s.replace("-", " ") for s in taxonomy.SKILL_IDS))
    assert len(env["posting"]["skills"]) <= 25 and "skill-stuffing-suspected" in env["ext"]["quality_flags"]


def test_split_sections_heading_must_be_whole_line():
    sections = split_sections("Requirements are flexible here\n- Python")
    assert sections["required"] == [] and len(sections["unsectioned"]) == 2


# ----------------------------------------------------------------- time/status
def test_quality_flags_for_time_anomalies():
    assert "time-anomaly:posted-in-future" in norm(posted_at="2027-03-01T00:00:00Z")["ext"]["quality_flags"]
    assert "time-anomaly:updated-before-posted" in norm(updated_at="2026-01-01T00:00:00Z")["ext"]["quality_flags"]
    assert "time-anomaly:expires-before-posted" in norm(expires_at="2026-10-01T00:00:00Z")["ext"]["quality_flags"]
    assert norm(posted_at="2027-03-01T00:00:00Z")["posting"]["status"] == "unknown"


def test_status_expired_when_source_expiry_passed_and_when_delisted():
    assert norm(expires_at="2026-11-01T00:00:00Z")["posting"]["status"] == "expired"
    env = norm(revoked=True)
    assert env["posting"]["status"] == "expired" and env["ext"]["status_reason"] == "delisted-by-source"


def test_naive_timestamps_are_unknown_not_assumed_utc():
    env = norm(posted_at="2026-10-20T00:00:00", expires_at="2026-10-21")
    assert env["ext"]["timestamps"]["posted_at"] is None and env["ext"]["timestamps"]["expires_at"] is None
    assert env["posting"]["status"] == "active"


def test_normalize_is_deterministic():
    assert norm() == norm()
