"""WS7 — the synthetic end-to-end pipeline as an artifact exchange.

    jobs.import -> profile.claims -> match.rank -> [E pins the top role] -> features.vectors
      -> resume.variant -> application.record -> experiment.run -> atlas.project
      -> simulator.scenario -> ui.e2e

Each stage is provided by the lane that owns it (a command declared in that
lane's ``integration-manifest.json``) and communicates only through JSON files in
one artifacts directory. Lane E validates every artifact against the frozen
contracts plus cross-stage referential integrity, and probes whether a stage
really computes from its inputs.

Status vocabulary per stage:

* ``PASS``    real lane code ran, labelled ``mock: false``, all validators and probes passed
* ``FAIL``    command failed, artifact invalid, upstream tampered, or output is canned
* ``BLOCKED`` no provider (lane has not delivered) or an upstream artifact is missing
* ``MOCK``    the lane itself labels the stage as mock/fixture output
* ``STUB``    lane E's own reference stub ran (``--allow-stub``); never integration evidence
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import atlas_quality, contracts, sec_inject
from . import experiment_integrity as ei
from .outdir import OutputPathError
from .timeutil import parse_rfc3339

USABLE_CLAIM_STATUSES = ("supported", "self-reported")
#: what actor "system" may record (same set as lane B's workflow); everything else needs a person or an import
SYSTEM_MAY_RECORD = ("saved", "drafted", "note")
OUTCOME_EVENT_TYPES = ("recruiter_screen", "interview", "rejected", "offer")


@dataclass(frozen=True)
class Stage:
    id: str
    lane: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    summary: str


STAGES: tuple[Stage, ...] = (
    Stage("jobs.import", "A", (), ("jobs.json",), "import >= count synthetic postings as JobPosting[]"),
    Stage("profile.claims", "A", (), ("claims.json",), "evidence-scoped ProfileClaim[] for one synthetic candidate"),
    Stage("match.rank", "A", ("jobs.json", "claims.json"), ("matches.json",), "explainable ranking"),
    Stage("features.vectors", "A", ("jobs.json",), ("vectors.json",), "feature vectors with a fixed vocabulary"),
    Stage("resume.variant", "B", ("jobs.json", "claims.json", "pin.json"), ("resume_variant.json",),
          "evidence-backed résumé variant for the pinned role"),
    Stage("application.record", "B", ("pin.json", "resume_variant.json"), ("events.json",),
          "synthetic application with a user-approval gate"),
    Stage("experiment.run", "B", ("jobs.json",),
          ("experiment.json", "assignments.json", "experiment_events.json", "analysis.json"),
          "preregistered randomized experiment + outcome analysis"),
    Stage("atlas.project", "C", ("vectors.json",), ("atlas_points.json", "atlas_diagnostics.json"),
          "2D projection with distortion diagnostics"),
    Stage("simulator.scenario", "C", ("claims.json", "jobs.json", "pin.json"), ("scenarios.json",),
          "hypothetical transition scenarios for the pinned role"),
    Stage("ui.e2e", "D", ("jobs.json", "matches.json", "events.json", "analysis.json", "atlas_points.json", "scenarios.json"),
          ("ui_report.json",), "browser keyboard E2E over the pipeline artifacts"),
)  # fmt: skip
STAGE_BY_ID = {stage.id: stage for stage in STAGES}
PIN_AFTER = "match.rank"


#: every file named in some stage's ``writes``: contract-level artifacts no later stage may modify
DECLARED_ARTIFACTS = frozenset(name for stage in STAGES for name in stage.writes) | {"pin.json"}


def classify_modified(
    modified: list[str], producer: dict[str, str], stage: Stage
) -> tuple[list[str], list[str]]:
    """Split files a stage changed into (tampered, own sidecars).

    A stage may update a non-declared sidecar that its OWN lane created earlier in the run (lane C's
    scenario stage refreshes ``atlas.pca.json`` written by its atlas stage, C @ f76e24c). Changing a
    declared artifact, a file another lane created, or a file of unknown origin is tampering.
    """
    tampered, own = [], []
    for name in modified:
        if name not in DECLARED_ARTIFACTS and producer.get(name) == stage.lane:
            own.append(name)
        else:
            tampered.append(name)
    return tampered, own


@dataclass
class RunOutcome:
    ok: bool
    detail: str = ""
    log: str = ""


class Provider(Protocol):
    name: str
    mock: bool
    is_stub: bool

    def run(self, stage: Stage, artifacts: Path, seed: int, count: int) -> RunOutcome: ...


@dataclass
class StageResult:
    stage: str
    lane: str
    status: str
    provider: str = ""
    mock: bool | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    log_tail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- artifact io


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(path: Path) -> str:
    return json.dumps(read_json(path), sort_keys=True)


def _is_list_of_dicts(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, dict) for v in value)


# --------------------------------------------------------------------------- validators


def _each(contract: str, docs: list[dict[str, Any]], id_key: str, limit: int = 8) -> list[str]:
    problems: list[str] = []
    for index, doc in enumerate(docs):
        for problem in contracts.check(contract, doc):
            problems.append(f"{contract}[{index}] {doc.get(id_key)!r}: {problem}")
            if len(problems) >= limit:
                return [*problems, f"... further {contract} problems suppressed"]
    return problems


def _unique(docs: list[dict[str, Any]], key: str, what: str) -> list[str]:
    values = [d.get(key) for d in docs]
    return [] if len(set(map(str, values))) == len(values) else [f"{what}: duplicate {key} values"]


def v_jobs(a: Path, count: int) -> list[str]:
    jobs = read_json(a / "jobs.json")
    if not _is_list_of_dicts(jobs):
        return ["jobs.json: expected an array of JobPosting objects"]
    problems = _each("job-posting", jobs, "id")
    if len(jobs) < count:
        problems.append(f"jobs.json: {len(jobs)} postings, expected at least {count}")
    problems += _unique(jobs, "id", "jobs.json")
    if len({(j.get("source"), j.get("source_id")) for j in jobs}) != len(jobs):
        problems.append("jobs.json: duplicate (source, source_id) pairs")
    # Lane A deliberately emits ATS-shaped synthetic records (source "greenhouse" etc. with a fictional
    # employer and a "Synthetic fixture" license_note, RFC #12). What must never appear offline is a
    # posting that is NOT marked fictional, whatever its source enum says.
    unmarked = [
        str(j.get("id")) for j in jobs
        if not any(m in f"{j.get('organization', '')} {j.get('license_note', '')}".casefold() for m in ("fictional", "synthetic"))
    ]  # fmt: skip
    if unmarked:
        problems.append(
            f"jobs.json: {len(unmarked)} postings are not marked fictional/synthetic, e.g. {unmarked[0]!r}"
        )
    return problems


def v_claims(a: Path, count: int) -> list[str]:
    claims = read_json(a / "claims.json")
    if not _is_list_of_dicts(claims) or not claims:
        return ["claims.json: expected a non-empty array of ProfileClaim objects"]
    problems = _each("profile-claim", claims, "id") + _unique(claims, "id", "claims.json")
    if len({c.get("subject_id") for c in claims}) != 1:
        problems.append("claims.json: more than one subject_id in a single-candidate export")
    if not any(c.get("status") in USABLE_CLAIM_STATUSES for c in claims):
        problems.append("claims.json: no supported or self-reported claim to build a résumé from")
    return problems


def v_matches(a: Path, count: int) -> list[str]:
    matches = read_json(a / "matches.json")
    job_ids = {j["id"] for j in read_json(a / "jobs.json")}
    if not _is_list_of_dicts(matches) or not matches:
        return ["matches.json: expected a non-empty array of match explanations"]
    problems = _unique(matches, "job_id", "matches.json")
    unknown = [m.get("job_id") for m in matches if m.get("job_id") not in job_ids]
    if unknown:
        problems.append(f"matches.json: {len(unknown)} job_id not present in jobs.json, e.g. {unknown[0]!r}")
    scores = [m.get("score") for m in matches]
    if not all(isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) for s in scores):
        problems.append("matches.json: every entry needs a finite numeric 'score'")
    elif any(x < y for x, y in zip(scores, scores[1:], strict=False)):
        problems.append("matches.json: not sorted by descending score")
    for index, match in enumerate(matches[:2000]):
        label = str(match.get("score_type", "")).lower()
        if "not" not in label or "probab" not in label:
            problems.append(f"matches.json[{index}]: score_type must state it is not a hiring probability")
            break
    claims = contracts.probability_claims(matches)
    if claims:
        problems.append(f"matches.json: hiring-probability language at {claims[0][0]}")
    return problems


def v_vectors(a: Path, count: int) -> list[str]:
    bundle = read_json(a / "vectors.json")
    if not isinstance(bundle, dict):
        return ["vectors.json: expected an object with 'vocabulary' and 'vectors'"]
    try:
        _, entities, _ = atlas_quality.as_matrix(bundle)
    except (ValueError, AttributeError, TypeError) as exc:
        return [f"vectors.json: {exc}"]
    missing = {j["id"] for j in read_json(a / "jobs.json")} - set(entities)
    return [f"vectors.json: {len(missing)} jobs have no vector"] if missing else []


def v_resume(a: Path, count: int) -> list[str]:
    variant = read_json(a / "resume_variant.json")
    if not isinstance(variant, dict):
        return ["resume_variant.json: expected an object"]
    claims = {c["id"]: c for c in read_json(a / "claims.json")}
    pin = read_json(a / "pin.json")
    problems: list[str] = []
    if not isinstance(variant.get("id"), str) or not variant["id"].strip():
        problems.append("resume_variant.json: missing variant id")
    if variant.get("job_id") != pin["job_id"]:
        problems.append(
            f"resume_variant.json: built for {variant.get('job_id')!r}, pinned role is {pin['job_id']!r}"
        )
    if variant.get("schema") == "careertwin.b.resume-variant.v1":
        # Lane B's native shape (published on #5; E promised on #3 to follow B's names): one claim per bullet.
        # In this shape a bullet IS a claim, verbatim. Text that differs from the cited claim is content
        # nobody has evidence for (consolidation probe: an appended "grew revenue 300% as CTO" passed).
        for index, bullet in enumerate(variant.get("bullets", [])):
            if not isinstance(bullet, dict):
                continue
            claim = claims.get(bullet.get("claim_id"))
            if bullet.get("manual_override"):
                problems.append(
                    f"resume_variant.json: bullet {index} is a manual override; an automated run has no "
                    "person who could have written it"
                )
            elif claim is not None and bullet.get("text") != claim.get("claim_text"):
                problems.append(
                    f"resume_variant.json: bullet {index} text is not the text of claim "
                    f"{bullet.get('claim_id')!r} it cites (invented or embellished content)"
                )
        variant = dict(
            variant,
            claim_ids=[b.get("claim_id") for b in variant.get("bullets", []) if isinstance(b, dict)],
            bullets=[
                {"text": b.get("text"), "claim_ids": [b.get("claim_id")] if b.get("claim_id") else []}
                for b in variant.get("bullets", []) if isinstance(b, dict)
            ],
        )  # fmt: skip
    cited = variant.get("claim_ids")
    if not isinstance(cited, list) or not cited:
        return [*problems, "resume_variant.json: claim_ids must be a non-empty array"]
    for claim_id in cited:
        claim = claims.get(claim_id)
        if claim is None:
            problems.append(f"resume_variant.json: cites unknown claim {claim_id!r} (fabricated evidence)")
        elif claim.get("status") not in USABLE_CLAIM_STATUSES:
            problems.append(
                f"resume_variant.json: cites claim {claim_id!r} with status {claim.get('status')!r}"
            )
        elif claim.get("visibility") == "private":
            problems.append(f"resume_variant.json: cites private claim {claim_id!r} (not exportable)")
    bullets = variant.get("bullets")
    if not _is_list_of_dicts(bullets) or not bullets:
        return [*problems, "resume_variant.json: bullets must be a non-empty array"]
    for index, bullet in enumerate(bullets):
        refs = bullet.get("claim_ids")
        if not isinstance(refs, list) or not refs or not set(refs) <= set(cited):
            problems.append(f"resume_variant.json: bullet {index} is not traceable to the variant's claims")
        text = str(bullet.get("text", ""))
        if not text.strip():
            problems.append(f"resume_variant.json: bullet {index} is empty")
        if sec_inject.active_html(text):
            problems.append(f"resume_variant.json: bullet {index} contains active HTML")
        if sec_inject.CANARY_PREFIX in text:
            problems.append(f"resume_variant.json: bullet {index} echoes injected job text")
    if contracts.probability_claims(variant):
        problems.append("resume_variant.json: hiring-probability language")
    return problems


def v_events(a: Path, count: int) -> list[str]:
    events = read_json(a / "events.json")
    if not _is_list_of_dicts(events) or not events:
        return ["events.json: expected a non-empty array of ApplicationEvent objects"]
    pin = read_json(a / "pin.json")
    variant = read_json(a / "resume_variant.json")
    problems = _each("application-event", events, "id") + _unique(events, "id", "events.json")
    mine = [e for e in events if e.get("job_id") == pin["job_id"]]
    if not mine:
        return [*problems, f"events.json: no event for the pinned role {pin['job_id']!r}"]
    for event in events:
        if event.get("actor") == "system" and event.get("event_type") not in SYSTEM_MAY_RECORD:
            problems.append(
                f"events.json: {event.get('id')!r} is a {event.get('event_type')!r} event recorded by 'system'; "
                "approvals, submissions, withdrawals and outcomes come from the user or an import, never from "
                "the system (invented application outcome)"
            )
    by_app: dict[str, list[dict[str, Any]]] = {}
    for event in mine:
        by_app.setdefault(str(event.get("application_id")), []).append(event)
    complete = False
    for app_id, stream in by_app.items():
        seen_submission = False
        for event in stream:
            seen_submission = seen_submission or event.get("event_type") == "submitted"
            if event.get("event_type") in OUTCOME_EVENT_TYPES and not seen_submission:
                problems.append(
                    f"events.json: {event.get('id')!r} reports {event.get('event_type')!r} for application "
                    f"{app_id!r} before any submission was recorded"
                )
        times = [parse_rfc3339(e.get("recorded_at")) for e in stream]
        if any(x is None or y is None or x > y for x, y in zip(times, times[1:], strict=False)):
            problems.append(f"events.json: application {app_id!r} events are not in recorded order")
        types = [e.get("event_type") for e in stream]
        wanted = ["saved", "drafted", "approved", "submitted"]
        positions = [types.index(t) if t in types else -1 for t in wanted]
        if -1 not in positions and positions == sorted(positions):
            complete = True
            submitted = stream[positions[3]]
            approved = stream[positions[2]]
            if submitted.get("resume_variant_id") != variant.get("id"):
                problems.append(
                    "events.json: 'submitted' does not reference the résumé variant that was built"
                )
            if submitted.get("approval_token_ref") != approved.get("approval_token_ref"):
                problems.append("events.json: 'submitted' approval token differs from the 'approved' event")
    if not complete:
        problems.append(
            "events.json: no application runs saved -> drafted -> approved -> submitted for the pinned role"
        )
    return problems


def v_experiment(a: Path, count: int) -> list[str]:
    spec = read_json(a / "experiment.json")
    assignments = read_json(a / "assignments.json")
    events = read_json(a / "experiment_events.json")
    analysis = read_json(a / "analysis.json")
    if not isinstance(spec, dict) or not _is_list_of_dicts(assignments) or not _is_list_of_dicts(events):
        return [
            "experiment.run: experiment.json must be an object; assignments/events must be arrays of objects"
        ]
    if not isinstance(analysis, dict):
        return ["analysis.json: expected an object"]
    problems = _each("application-event", events, "id")
    if spec.get("status") not in ("running", "paused", "closed"):
        problems.append(
            f"experiment.json: status {spec.get('status')!r}; a run needs a locked, started design"
        )
    if len(assignments) < 2:
        problems.append(f"assignments.json: {len(assignments)} assigned units; nothing was randomised")
    # Sample size is the producer's preregistered design decision; E's sample-ratio check simply
    # does not apply below 40 units (see experiment_integrity.check_experiment_bundle). Lane-native
    # shapes (B's assignment journal and analysis report) are mapped inside check_experiment_bundle.
    job_ids = {j["id"] for j in read_json(a / "jobs.json")}
    stray = [e.get("job_id") for e in events if e.get("job_id") not in job_ids]
    if stray:
        problems.append(
            f"experiment_events.json: {len(stray)} events reference jobs not in jobs.json, e.g. {stray[0]!r}"
        )
    for finding in ei.check_experiment_bundle(spec, assignments, events, analysis):
        if finding.severity != "low":
            problems.append(f"experiment.run: {finding}")
    problems.extend(lab_boundary(a, events))
    return problems


SIMULATED_TYPES = ("approved", "submitted")


def lab_boundary(a: Path, events: list[dict[str, Any]]) -> list[str]:
    """The experiment stage's events are a simulated stream, kept apart from the application log.

    Two producer rules (issue #6, [CHATGPT-REVIEW] HIGH): its application ids never reuse an id from
    events.json, and every approval or submission it simulates says so in its source. Consumers rely on
    the file, not on these labels, to tell the streams apart; the labels keep the record honest when
    read on its own.
    """
    problems: list[str] = []
    log = read_json(a / "events.json") if (a / "events.json").exists() else []
    log_apps = {e.get("application_id") for e in log} if _is_list_of_dicts(log) else set()
    shared = sorted({str(e.get("application_id")) for e in events} & {str(x) for x in log_apps})
    if shared:
        problems.append(
            f"experiment_events.json: {len(shared)} simulated application ids also occur in events.json, "
            f"e.g. {shared[0]!r}; the simulated stream must not write into the application log"
        )
    unlabelled = [e.get("id") for e in events
                  if e.get("event_type") in SIMULATED_TYPES and not str(e.get("source", "")).startswith("simulation:")]
    if unlabelled:
        problems.append(
            f"experiment_events.json: {len(unlabelled)} simulated approvals/submissions do not carry a "
            f"'simulation:' source, e.g. {unlabelled[0]!r}; they would read as the user's own actions"
        )
    return problems


def v_atlas(a: Path, count: int) -> list[str]:
    points = read_json(a / "atlas_points.json")
    diagnostics = read_json(a / "atlas_diagnostics.json")
    if not _is_list_of_dicts(points) or not points:
        return ["atlas_points.json: expected a non-empty array of AtlasPoint objects"]
    if not isinstance(diagnostics, dict):
        return ["atlas_diagnostics.json: expected an object"]
    vectors = read_json(a / "vectors.json")
    findings, _ = atlas_quality.verify_atlas(points, vectors, diagnostics)
    problems = [f"atlas.project: {f}" for f in findings if f.severity != "low"]
    covered = {p.get("source_vector_ref") for p in points}
    # An unobserved vector (no recognisable skills) is unknown, not the origin: lane A's RFC #12 point 5
    # asks consumers to mask it, and lane C lists it in `unplaced`. Only observed vectors must be placed,
    # and every unplaced one must be reported rather than silently dropped.
    unplaced = {
        str(u.get("source_vector_ref") or u.get("entity_id")) for u in diagnostics.get("unplaced", []) or []
    }
    missing = [
        v["ref"] for v in vectors.get("vectors", [])
        if v["ref"] not in covered and v.get("observed", True) and v["ref"] not in unplaced
    ]  # fmt: skip
    if missing:
        problems.append(
            f"atlas_points.json: {len(missing)} observed input vectors were neither projected nor reported unplaced"
        )
    silent = [
        v["ref"] for v in vectors.get("vectors", [])
        if v["ref"] not in covered and not v.get("observed", True)
        and v["ref"] not in unplaced and str(v.get("entity_id")) not in unplaced
    ]  # fmt: skip
    if silent:
        problems.append(
            f"atlas_diagnostics.json: {len(silent)} unobserved vectors dropped without an `unplaced` entry"
        )
    if contracts.probability_claims(diagnostics):
        problems.append("atlas_diagnostics.json: hiring-probability language")
    return problems


def v_scenarios(a: Path, count: int) -> list[str]:
    scenarios = read_json(a / "scenarios.json")
    if not _is_list_of_dicts(scenarios) or not scenarios:
        return ["scenarios.json: expected a non-empty array of TransitionScenario objects"]
    subject = read_json(a / "claims.json")[0]["subject_id"]
    problems = _each("transition-scenario", scenarios, "id") + _unique(scenarios, "id", "scenarios.json")
    if any(s.get("candidate_ref") != subject for s in scenarios):
        problems.append(f"scenarios.json: candidate_ref must be the claims subject {subject!r}")
    if not any(s.get("planned_actions") for s in scenarios):
        problems.append("scenarios.json: no scenario proposes any action")
    # Proposed by lane D on #5: a scenario drawn on the run's map must come from the same fitted model.
    diagnostics_path = a / "atlas_diagnostics.json"
    if diagnostics_path.is_file():
        atlas_projection = read_json(diagnostics_path).get("projection_id")
        for scenario in scenarios:
            outputs = scenario.get("outputs")
            model_ref = outputs.get("model_ref") if isinstance(outputs, dict) else None
            ref = model_ref.get("projection_id") if isinstance(model_ref, dict) else None
            if ref is not None and atlas_projection is not None and ref != atlas_projection:
                problems.append(
                    f"scenarios.json: {scenario.get('id')!r} uses projection {ref!r}, the atlas stage fitted "
                    f"{atlas_projection!r}; its map points cannot be drawn on this run's atlas"
                )
    return problems


def v_ui(a: Path, count: int) -> list[str]:
    report = read_json(a / "ui_report.json")
    if not isinstance(report, dict):
        return ["ui_report.json: expected an object"]
    tests = report.get("tests")
    if not _is_list_of_dicts(tests) or not tests:
        return ["ui_report.json: no browser tests reported"]
    problems = [
        f"ui_report.json: test {t.get('name')!r} status {t.get('status')!r}"
        for t in tests
        if t.get("status") != "pass"
    ]
    if report.get("keyboard_only") is not True:
        problems.append("ui_report.json: the E2E flow was not driven by keyboard only")
    if report.get("data_source") != "artifacts":
        problems.append(
            f"ui_report.json: data_source {report.get('data_source')!r}; the UI must render the pipeline artifacts"
        )
    if report.get("mock_sources"):
        problems.append(f"ui_report.json: screens still backed by mocks: {report.get('mock_sources')}")
    problems.extend(crm_boundary(a, report))
    return problems


def crm_boundary(a: Path, report: dict[str, Any]) -> list[str]:
    """The browser-measured application count must equal the log's, with no simulated unit shown."""
    measured = (report.get("detail") or {}).get("crm_boundary")
    if not isinstance(measured, dict):
        return ["ui_report.json: detail.crm_boundary missing; the UI stage must measure the applications it shows"]
    log = read_json(a / "events.json") if (a / "events.json").exists() else []
    expected = len({e.get("application_id") for e in log}) if _is_list_of_dicts(log) else 0
    problems = []
    if measured.get("applications_shown") != expected:
        problems.append(
            f"ui_report.json: the Applications screen showed {measured.get('applications_shown')!r} applications; "
            f"events.json has {expected}"
        )
    if measured.get("simulated_shown_as_applications") != 0:
        problems.append(
            f"ui_report.json: {measured.get('simulated_shown_as_applications')!r} simulated experiment units "
            "were shown as applications or tracked postings"
        )
    return problems


VALIDATORS: dict[str, Callable[[Path, int], list[str]]] = {
    "jobs.import": v_jobs,
    "profile.claims": v_claims,
    "match.rank": v_matches,
    "features.vectors": v_vectors,
    "resume.variant": v_resume,
    "application.record": v_events,
    "experiment.run": v_experiment,
    "atlas.project": v_atlas,
    "simulator.scenario": v_scenarios,
    "ui.e2e": v_ui,
}


# --------------------------------------------------------------------------- pin + probes


def pin_top_role(artifacts: Path) -> dict[str, Any]:
    """Lane E plays the synthetic user: pin the top-ranked ACTIVE role (falls back to rank 1)."""
    matches = read_json(artifacts / "matches.json")
    status = {j["id"]: j.get("status") for j in read_json(artifacts / "jobs.json")}
    chosen = next((m for m in matches if status.get(m["job_id"]) == "active"), matches[0])
    pin = {
        "job_id": chosen["job_id"],
        "pinned_by": "synthetic-user (lane E orchestrator)",
        "rank": matches.index(chosen) + 1,
    }
    write_json(artifacts / "pin.json", pin)
    return pin


def _halve(path: Path, key: str | None = None) -> None:
    data = read_json(path)
    if key is None:
        write_json(path, data[: max(1, len(data) // 2)])
    else:
        data[key] = data[key][: max(1, len(data[key]) // 2)]
        write_json(path, data)


def _repin(path: Path, artifacts: Path) -> None:
    pin = read_json(path)
    other = next(m["job_id"] for m in read_json(artifacts / "matches.json") if m["job_id"] != pin["job_id"])
    write_json(path, dict(pin, job_id=other))


#: stage -> (description, input mutation applied in a scratch copy). Output must change.
PROBES: dict[str, tuple[str, Callable[[Path], None]]] = {
    "match.rank": ("half of jobs.json removed", lambda d: _halve(d / "jobs.json")),
    "features.vectors": ("half of jobs.json removed", lambda d: _halve(d / "jobs.json")),
    "experiment.run": ("half of jobs.json removed", lambda d: _halve(d / "jobs.json")),
    "atlas.project": ("half of the vectors removed", lambda d: _halve(d / "vectors.json", "vectors")),
    "resume.variant": ("a different role pinned", lambda d: _repin(d / "pin.json", d)),
}


def _rerun(
    stage: Stage,
    provider: Provider,
    artifacts: Path,
    scratch: Path,
    seed: int,
    count: int,
    *,
    keep_state: bool,
) -> tuple[str, RunOutcome | None]:
    """Copy the artifacts into ``scratch``, apply the stage's input mutation and run it again."""
    if scratch.exists():
        shutil.rmtree(scratch)
    shutil.copytree(artifacts, scratch, ignore=None if keep_state else _ignore_state_dirs(artifacts))
    for name in stage.writes:
        (scratch / name).unlink(missing_ok=True)
    if stage.id == "jobs.import":
        return f"seed {seed + 1} and count {count + 7}", provider.run(stage, scratch, seed + 1, count + 7)
    if stage.id in PROBES:
        what, mutate = PROBES[stage.id]
        mutate(scratch)
        return what, provider.run(stage, scratch, seed, count)
    return "", None


def _ignore_state_dirs(root: Path) -> Callable[[str, list[str]], list[str]]:
    def ignore(directory: str, names: list[str]) -> list[str]:
        if Path(directory).resolve() != root.resolve():
            return []
        return [n for n in names if (root / n).is_dir()]

    return ignore


def _same_outputs(stage: Stage, a: Path, b: Path) -> bool:
    return all(_canonical(a / name) == _canonical(b / name) for name in stage.writes)


def _probe(
    stage: Stage, provider: Provider, artifacts: Path, scratch: Path, seed: int, count: int
) -> list[str]:
    """Does the stage compute from its inputs?

    Two re-runs on mutated inputs:

    1. **fresh** (lane state directories removed): the stage must succeed and its output must change,
       otherwise ``CANNED-OUTPUT``;
    2. **with persisted lane state** (only if the artifacts hold state directories): the stage must either
       refuse the drifted input (non-zero exit, e.g. lane B's ``InputDrift``) or recompute. Succeeding with
       byte-identical output is ``STALE-STATE`` (lane B @ 84757ed, fixed in 755ccae).
    """
    problems: list[str] = []
    what, fresh = _rerun(stage, provider, artifacts, scratch, seed, count, keep_state=False)
    if fresh is None:
        return problems
    if not fresh.ok or not all((scratch / name).is_file() for name in stage.writes):
        return [
            f"PROBE-FAILED: stage did not complete when re-run in a fresh directory with {what}: {fresh.detail}"
        ]
    try:
        if _same_outputs(stage, scratch, artifacts):
            problems.append(
                f"CANNED-OUTPUT: output is byte-identical after {what}; the stage does not compute from its inputs"
            )
        if stage.id == "jobs.import" and len(read_json(scratch / "jobs.json")) < count + 7:
            problems.append(
                f"CANNED-OUTPUT: asked for {count + 7} postings, got {len(read_json(scratch / 'jobs.json'))}"
            )
    except (ValueError, OSError) as exc:
        return [f"PROBE-FAILED: unreadable output when re-run with {what}: {exc}"]
    state_dirs = sorted(p.name for p in artifacts.iterdir() if p.is_dir())
    if not state_dirs or problems:
        return problems
    _, stateful = _rerun(stage, provider, artifacts, scratch, seed, count, keep_state=True)
    if stateful is not None and stateful.ok and all((scratch / n).is_file() for n in stage.writes):
        try:
            if _same_outputs(stage, scratch, artifacts):
                problems.append(
                    f"STALE-STATE: with lane state {state_dirs} present the stage succeeded on drifted input "
                    f"({what}) and returned byte-identical output instead of refusing or recomputing"
                )
        except (ValueError, OSError) as exc:
            problems.append(f"PROBE-FAILED: unreadable output on the stateful re-run: {exc}")
    return problems


# --------------------------------------------------------------------------- pipeline


def run_pipeline(
    providers: dict[str, Provider | None],
    artifacts: Path,
    *,
    seed: int = 7,
    count: int = 150,
    probes: bool = True,
    logs: Path | None = None,
) -> list[StageResult]:
    """Run every stage in order and return one result per stage. Never raises for stage failures.

    With ``logs``, each stage's full output is saved as ``stage-<id>.log`` (the report keeps only a tail;
    run final4 lost lane D's assertion message to that truncation).
    """
    artifacts.mkdir(parents=True, exist_ok=True)
    # The probe directory is deleted and recreated during the run. It must be one this run creates:
    # a directory or link that already has that name is not ours, so the pipeline refuses to start
    # rather than delete it.
    probe_dir = artifacts.parent / f"{artifacts.name}.probe"
    if probe_dir.exists() or probe_dir.is_symlink():
        raise OutputPathError(
            f"refusing to run: {probe_dir} already exists and the pipeline will not delete a directory "
            "it did not create. Remove it yourself or use a new artifacts directory."
        )
    results: list[StageResult] = []
    producer: dict[str, str] = {}  # artifact file name -> lane that created it ("E" for pin.json)
    for stage in STAGES:
        provider = providers.get(stage.id)
        result = StageResult(stage.id, stage.lane, "BLOCKED")
        results.append(result)
        if provider is None:
            result.problems.append(
                f"no provider: CAREER-TWIN-{stage.lane} has not delivered stage {stage.id!r}"
            )
            continue
        result.provider, result.mock = provider.name, provider.mock
        missing = [name for name in stage.reads if not (artifacts / name).is_file()]
        if missing:
            result.problems.append(f"upstream artifact(s) missing: {', '.join(missing)}")
            continue
        before = {
            p.name: _digest(p) for p in artifacts.iterdir() if p.is_file() and p.name not in stage.writes
        }
        started = time.perf_counter()
        try:
            outcome = provider.run(stage, artifacts, seed, count)
        except Exception as exc:  # noqa: BLE001 - a provider crash is a stage failure, not a harness crash
            outcome = RunOutcome(False, f"provider raised {type(exc).__name__}: {exc}")
        result.duration_s = round(time.perf_counter() - started, 3)
        result.log_tail = outcome.log[-1500:]
        if logs is not None:
            logs.mkdir(parents=True, exist_ok=True)
            (logs / f"stage-{stage.id}.log").write_text(outcome.log, encoding="utf-8", errors="replace")
        result.status = "FAIL"
        if not outcome.ok:
            result.problems.append(f"stage command failed: {outcome.detail}")
            continue
        modified = sorted(
            n
            for n, digest in before.items()
            if not (artifacts / n).is_file() or _digest(artifacts / n) != digest
        )
        tampered, own_sidecars = classify_modified(modified, producer, stage)
        if own_sidecars:
            result.notes.append(
                f"updated its own lane's sidecar file(s) {own_sidecars} (allowed; not a contract artifact)"
            )
        if tampered:
            result.problems.append(f"TAMPERED-UPSTREAM: stage modified artifacts it does not own: {tampered}")
            continue
        for path in artifacts.iterdir():
            if path.is_file() and path.name not in before:
                producer.setdefault(path.name, stage.lane)
        absent = [name for name in stage.writes if not (artifacts / name).is_file()]
        if absent:
            result.problems.append(f"stage did not write: {', '.join(absent)}")
            continue
        try:
            result.problems.extend(VALIDATORS[stage.id](artifacts, count))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, OSError) as exc:
            result.problems.append(f"artifact could not be validated ({type(exc).__name__}: {exc})")
        if not result.problems and probes:
            result.problems.extend(_probe(stage, provider, artifacts, probe_dir, seed, count))
        if result.problems:
            for name in stage.writes:  # an invalid artifact must not feed downstream stages
                (artifacts / name).rename(artifacts / f"{name}.rejected")
            continue
        result.status = "STUB" if provider.is_stub else "MOCK" if provider.mock else "PASS"
        if stage.id == PIN_AFTER:
            pin = pin_top_role(artifacts)
            producer["pin.json"] = "E"
            result.notes.append(f"lane E pinned {pin['job_id']} (rank {pin['rank']})")
    if probe_dir.exists() and not probe_dir.is_symlink():
        shutil.rmtree(probe_dir, ignore_errors=True)
    return results


def verdict(results: list[StageResult]) -> str:
    """PASS only when every stage ran on real, non-mock lane code."""
    statuses = {r.status for r in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if statuses & {"STUB", "MOCK"}:
        return "NOT-INTEGRATED"
    return "PASS" if statuses == {"PASS"} and len(results) == len(STAGES) else "UNKNOWN"


def summarize(results: list[StageResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for result in results:
        out[result.status] = out.get(result.status, 0) + 1
    return out
