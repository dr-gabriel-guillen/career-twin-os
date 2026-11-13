"""Scenario geometry gate: the drawn route must be the model's own linear shift on one projection."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ctint import e2e

SAMPLE = Path(__file__).resolve().parents[2] / "apps" / "web" / "tests" / "upstream-samples" / "pipeline"


def _copy(tmp: Path) -> list[dict]:
    for name in ("scenarios.json", "atlas.pca.json"):
        shutil.copy(SAMPLE / name, tmp / name)
    return json.loads((tmp / "scenarios.json").read_text(encoding="utf-8"))


def test_committed_pipeline_sample_is_consistent(tmp_path: Path) -> None:
    scenarios = _copy(tmp_path)
    assert e2e.scenario_geometry(tmp_path, scenarios) == []


def test_a_moved_scenario_point_is_caught(tmp_path: Path) -> None:
    scenarios = _copy(tmp_path)
    view = json.loads((tmp_path / "atlas.pca.json").read_text(encoding="utf-8"))
    target = scenarios[0]["outputs"]["atlas_entity_id"]
    for p in view["points"]:
        if p["entity_id"] == target:
            p["coords"] = [p["coords"][0] - 0.3, p["coords"][1] - 0.6]  # e.g. "snapped" nearer a role centre
    (tmp_path / "atlas.pca.json").write_text(json.dumps(view), encoding="utf-8")
    assert any("drawn route" in p for p in e2e.scenario_geometry(tmp_path, scenarios))


def test_inconsistent_per_feature_shifts_are_caught(tmp_path: Path) -> None:
    scenarios = _copy(tmp_path)
    scenarios[0]["outputs"]["movement"]["per_feature"][0]["map_shift"][0] += 0.5
    assert any("sum to" in p for p in e2e.scenario_geometry(tmp_path, scenarios))
