"""Shared helpers for lane A tests (uniquely named so several conftest.py files can coexist in one pytest run)."""
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]
for path in (ROOT / "connectors", PKG / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ct_matching import corpus  # noqa: E402

AS_OF = corpus.AS_OF


def make_env(source="synthetic", board="fictionaltest", captured_at="2026-11-08T00:00:00Z", postings=None, **capture):
    """A small native-format capture envelope for focused tests."""
    return {"capture": {"source": source, "board": board, "captured_at": captured_at, "organization": "Fictional Test Co",
                        "license_note": "synthetic test", "synthetic": True, **capture},
            "payload": {"postings": postings or []}}


def posting(pid="p1", **over):
    base = {"id": pid, "title": "Senior Backend Engineer", "url": f"https://jobs.fictional-test.example/{pid}",
            "posted_at": "2026-10-20T00:00:00Z", "location": "Austin, TX", "country": "US", "remote": "Remote",
            "employment_type": "Full-time",
            "description": "Fictional posting for tests.\nRequirements\n- Python\n- API design\n- Distributed systems\n"
                           "Nice to have\n- AWS"}
    base.update(over)
    return base
