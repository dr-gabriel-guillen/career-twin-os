"""Gates for the simulated experiment stream (issue #6, [CHATGPT-REVIEW] HIGH).

The experiment stage simulates approvals and submissions to exercise the approval gate. These gates
keep that stream out of the application log: producer side (ids and labels) and UI side (the
browser-measured application count).
"""

from __future__ import annotations

import json
from pathlib import Path

from ctint import e2e


def _write(d: Path, name: str, value: object) -> None:
    (d / name).write_text(json.dumps(value), encoding="utf-8")


def _evt(app: str, kind: str, source: str) -> dict[str, object]:
    return {"id": f"evt-{app}-{kind}", "application_id": app, "event_type": kind, "source": source}


def test_labelled_disjoint_stream_passes(tmp_path: Path) -> None:
    _write(tmp_path, "events.json", [_evt("app-1", "submitted", "user:manual-submission-report")])
    lab = [_evt("u-2", "approved", "simulation:experiment-approval"), _evt("u-2", "submitted", "simulation:experiment-submission"),
           _evt("u-2", "drafted", "system:resume-engine")]
    assert e2e.lab_boundary(tmp_path, lab) == []


def test_unlabelled_simulated_submission_fails(tmp_path: Path) -> None:
    _write(tmp_path, "events.json", [])
    problems = e2e.lab_boundary(tmp_path, [_evt("u-2", "submitted", "user:manual-submission-report")])
    assert len(problems) == 1 and "simulation:" in problems[0]


def test_shared_application_id_fails_whatever_its_name(tmp_path: Path) -> None:
    # No id prefix is involved: a plain id shared by both files is the defect.
    _write(tmp_path, "events.json", [_evt("app-plain", "saved", "user:manual")])
    problems = e2e.lab_boundary(tmp_path, [_evt("app-plain", "note", "simulation:x")])
    assert any("also occur in events.json" in p for p in problems)


def _report(boundary: object) -> dict[str, object]:
    return {"tests": [{"name": "t", "status": "pass"}], "keyboard_only": True, "data_source": "artifacts",
            "mock_sources": [], "detail": {"crm_boundary": boundary}}


def test_ui_must_measure_the_log_count(tmp_path: Path) -> None:
    _write(tmp_path, "events.json", [_evt("a", "saved", "x"), _evt("a", "drafted", "x"), _evt("b", "saved", "x")])
    _write(tmp_path, "ui_report.json", _report({"applications_shown": 2, "simulated_shown_as_applications": 0}))
    assert e2e.v_ui(tmp_path, 0) == []
    _write(tmp_path, "ui_report.json", _report({"applications_shown": 140, "simulated_shown_as_applications": 138}))
    problems = e2e.v_ui(tmp_path, 0)
    assert any("showed 140" in p for p in problems) and any("138 simulated" in p for p in problems)
    _write(tmp_path, "ui_report.json", _report(None))
    assert any("crm_boundary missing" in p for p in e2e.v_ui(tmp_path, 0))
