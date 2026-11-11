import copy
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]
for path in (ROOT / "connectors", PKG / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ct_matching import corpus  # noqa: E402
from ct_matching.evalbench import _truth_by_id  # noqa: E402
from ct_matching.service import Service, vault_from_profile  # noqa: E402

AS_OF = corpus.AS_OF


@pytest.fixture(scope="session")
def bundle():
    return corpus.generate()


@pytest.fixture(scope="session")
def _loaded(bundle):
    service = Service()
    report = service.import_doc(copy.deepcopy(bundle))
    vault, prefs = vault_from_profile(corpus.SYNTHETIC_PROFILE, AS_OF)
    service.vault, service.preferences = vault, prefs
    return service, report


@pytest.fixture()
def service(_loaded):
    """Shared read-only service over the default corpus. Tests that mutate must build their own."""
    return _loaded[0]


@pytest.fixture()
def import_report(_loaded):
    return _loaded[1]


@pytest.fixture(scope="session")
def truth(bundle):
    return _truth_by_id(bundle)


@pytest.fixture()
def by_tag(service, truth):
    def find(tag):
        out = [service.store.get(jid, AS_OF) for jid, t in sorted(truth.items()) if tag in t["tags"]]
        assert out and all(out), f"no stored jobs tagged {tag}"
        return out
    return find


@pytest.fixture()
def profile():
    return copy.deepcopy(corpus.SYNTHETIC_PROFILE)


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
