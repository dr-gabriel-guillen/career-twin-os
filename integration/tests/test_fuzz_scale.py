"""WS10 — schema-boundary fuzzing and scale checks on synthetic volumes
(1k+ jobs, 100 applications, 20 experiments, 10 scenarios)."""

from __future__ import annotations

import json
import random
import time
from collections import Counter

import pytest

from ctint import contracts, fuzz, synth
from ctint.cases import load_cases
from ctint.timeutil import parse_rfc3339

N_JOBS = 1200


def _bases(name: str) -> list[dict[str, object]]:
    return [c.doc for c in load_cases(name) if c.layer == "valid" and isinstance(c.doc, dict)]


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_every_single_fault_mutation_is_rejected(name: str) -> None:
    schema = contracts.load_schema(name)
    accepted: list[str] = []
    total = 0
    for base in _bases(name):
        for label, mutated in fuzz.mutations(schema, base):
            total += 1
            if not contracts.schema_errors(name, mutated):
                accepted.append(label)
    assert total >= 60, f"{name}: only {total} mutations generated"
    assert accepted == [], f"{name}: schema accepted single-fault documents: {sorted(set(accepted))}"


def test_mutation_volume_is_reported() -> None:
    counts = {
        name: sum(1 for base in _bases(name) for _ in fuzz.mutations(contracts.load_schema(name), base))
        for name in contracts.CONTRACTS
    }
    print(f"\nfuzz mutations per contract: {json.dumps(counts)} total={sum(counts.values())}")
    assert sum(counts.values()) >= 1500


@pytest.mark.parametrize("name", list(contracts.CONTRACTS))
def test_validators_never_crash_on_random_json(name: str) -> None:
    rng = random.Random(f"fuzz:{name}")
    base = _bases(name)[0]
    for _ in range(1500):
        for doc in (fuzz.random_json(rng), fuzz.scramble(rng, base)):
            first = contracts.schema_errors(name, doc)
            contracts.semantic_violations(name, doc)
            assert contracts.schema_errors(name, doc) == first, "validation must be deterministic"


def test_random_documents_are_essentially_never_valid() -> None:
    rng = random.Random("fuzz:validity")
    valid = sum(
        1
        for _ in range(3000)
        for name in contracts.CONTRACTS
        if not contracts.schema_errors(name, fuzz.random_json(rng))
    )
    assert valid == 0


@pytest.mark.slow
def test_1200_synthetic_jobs_are_valid_unique_and_fast() -> None:
    jobs = synth.gen_jobs(N_JOBS, seed=11)
    started = time.perf_counter()
    problems = [(job["id"], errs) for job in jobs if (errs := contracts.check("job-posting", job))]
    elapsed = time.perf_counter() - started
    print(f"\nvalidated {N_JOBS} jobs in {elapsed:.2f}s ({N_JOBS / elapsed:.0f}/s)")
    assert problems == []
    assert len({job["id"] for job in jobs}) == N_JOBS
    assert len({job["source_id"] for job in jobs}) == N_JOBS
    assert elapsed < 30, "schema + semantic validation of 1.2k jobs should take seconds"
    # the corpus exercises the 'unknown stays unknown' paths
    assert sum(job["compensation"] is None for job in jobs) > 100
    assert sum(job["location"] is None for job in jobs) > 100
    assert sum("remote_policy" not in job for job in jobs) > 50
    assert Counter(job["role_family"] for job in jobs).keys() == synth.ROLE_FAMILIES.keys()
    assert all(job["source"] == "synthetic" and "Fictional" in job["organization"] for job in jobs)
    assert not any(
        v.rule == "JOB-URL-002" for job in jobs for v in contracts.semantic_violations("job-posting", job)
    )


def test_generators_are_deterministic_and_seed_sensitive() -> None:
    assert synth.gen_jobs(50, seed=1) == synth.gen_jobs(50, seed=1)
    assert synth.gen_jobs(50, seed=1) != synth.gen_jobs(50, seed=2)
    assert synth.gen_claims(seed=1) == synth.gen_claims(seed=1)
    assert synth.gen_claims(seed=1) != synth.gen_claims(seed=2)
    assert synth.gen_jobs(50, seed=1) == synth.gen_jobs(60, seed=1)[:50], (
        "prefix-stable for incremental imports"
    )


def test_100_applications_form_valid_ordered_event_streams() -> None:
    jobs = synth.gen_jobs(150, seed=7)
    events = synth.gen_application_events(jobs, 100, seed=7)
    job_ids = {job["id"] for job in jobs}
    assert len({e["application_id"] for e in events}) == 100
    assert len({e["id"] for e in events}) == len(events) >= 250
    order = ["saved", "drafted", "approved", "submitted"]
    by_app: dict[str, list[dict[str, object]]] = {}
    for event in events:
        assert contracts.check("application-event", event) == [], event["id"]
        assert event["job_id"] in job_ids
        by_app.setdefault(str(event["application_id"]), []).append(event)
    for stream in by_app.values():
        times = [parse_rfc3339(e["recorded_at"]) for e in stream]
        assert all(a is not None and b is not None and a < b for a, b in zip(times, times[1:], strict=False))
        types = [str(e["event_type"]) for e in stream]
        assert types[: min(len(types), 4)] == order[: min(len(types), 4)]
        assert len({e["job_id"] for e in stream}) == 1
        for event in stream:
            if event["event_type"] == "submitted":
                assert event["approval_token_ref"] and event["actor"] == "user"
    assert any(e["event_type"] == "offer" for e in events)
    assert any(e["event_type"] == "rejected" for e in events)


def test_20_experiments_10_scenarios_and_claims_are_valid() -> None:
    experiments = synth.gen_experiments(20, seed=7)
    scenarios = synth.gen_scenarios(10, seed=7)
    claims = synth.gen_claims(seed=7)
    assert len(experiments) == 20 and len(scenarios) == 10 and len(claims) >= 12
    for spec in experiments:
        assert contracts.check("experiment", spec) == [], spec["id"]
        assert (spec["design_locked_at"] is None) == (spec["status"] == "draft")
    for scenario in scenarios:
        assert contracts.check("transition-scenario", scenario) == [], scenario["id"]
        assert scenario["candidate_ref"] == synth.SUBJECT
    for claim in claims:
        assert contracts.check("profile-claim", claim) == [], claim["id"]
    assert {c["status"] for c in claims} >= {"supported", "self-reported"}
    assert {s["status"] for s in experiments} == {"draft", "preregistered", "running", "paused", "closed"}


def test_vectors_cover_every_job_with_a_fixed_vocabulary() -> None:
    jobs = synth.gen_jobs(300, seed=2)
    bundle = synth.gen_vectors(jobs)
    width = len(bundle["vocabulary"])
    assert width == len(synth.VOCABULARY) + 1
    assert [v["entity_id"] for v in bundle["vectors"]] == [j["id"] for j in jobs]
    assert all(
        len(v["values"]) == width and all(0.0 <= x <= 1.0 for x in v["values"]) for v in bundle["vectors"]
    )
    assert len({v["ref"] for v in bundle["vectors"]}) == len(jobs)
