import copy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from a_testkit import AS_OF  # noqa: E402  (also puts the package and connectors on sys.path)
from ct_matching import corpus  # noqa: E402
from ct_matching.evalbench import _truth_by_id  # noqa: E402
from ct_matching.service import Service, vault_from_profile  # noqa: E402



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
