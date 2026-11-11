"""WS1/WS6 — the findings ledger is exact.

``known_findings.json`` lists every open defect lane E found on the shared
baseline (frozen contracts, fixtures, reference engine). These tests recompute
the evidence and require an exact match in both directions: a new defect or a
silently fixed one fails CI until the ledger is updated. The baseline's own six
unit tests still pass; they simply do not look at any of this.
"""

from __future__ import annotations

import subprocess
import sys

from ctint import baseline, contracts
from ctint.paths import repo_root


def test_schema_audit_matches_ledger_exactly() -> None:
    observed = {f"{v.rule}:{v.path}" for v in contracts.audit_schemas()}
    recorded = baseline.ledger_evidence_keys("SCHEMA-")
    assert observed - recorded == set(), "new schema finding not in known_findings.json"
    assert recorded - observed == set(), "ledger lists a schema finding that no longer reproduces"


def test_baseline_characterisation_matches_ledger_exactly() -> None:
    observed = baseline.characterize_baseline()
    recorded = baseline.ledger_evidence_keys("REF-")
    assert set(observed) - recorded == set(), {k: observed[k] for k in set(observed) - recorded}
    assert recorded - set(observed) == set(), "ledger lists a baseline finding that no longer reproduces"
    assert "4/4" in observed["REF-FIXTURES-NOT-CONTRACT-VALID"]
    assert "U+241F" in observed["REF-ASSIGN-KEY-COLLISION"]


def test_ledger_is_well_formed() -> None:
    ledger = baseline.load_ledger()
    ids = [item["id"] for item in ledger["findings"]]
    assert ids == sorted(set(ids)) and len(ids) >= 10
    seen: set[str] = set()
    for item in ledger["findings"]:
        assert item["status"] in ("OPEN", "ACKNOWLEDGED", "FIXED", "WONTFIX"), item["id"]
        assert item["severity"] in ("high", "medium", "low"), item["id"]
        assert item["routed"].startswith("#") and item["evidence"], item["id"]
        assert not (seen & set(item["evidence"])), f"{item['id']}: evidence key listed twice"
        seen |= set(item["evidence"])
        assert all(len(item[k]) > 20 for k in ("title", "impact", "proposed")), item["id"]


def test_baseline_unit_tests_still_pass_untouched() -> None:
    """The six bootstrap tests on main are green; the findings above are things they never check."""
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo_root(), capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr[-800:]
    assert "Ran 6 tests" in result.stderr
