"""Shared fixtures. Everything is synthetic and generated in-process: no network, and no files
written outside the tmp_path that pytest provides."""
import json
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parents[1]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from careertwin_atlas.engine import AtlasEngine  # noqa: E402
from careertwin_atlas.features import claims_to_vector  # noqa: E402
from careertwin_atlas.taxonomy import generate_postings  # noqa: E402
from careertwin_atlas.vocabulary import default_vocabulary  # noqa: E402

CANDIDATE_REF = "candidate-synthetic-001"
AS_OF = "2026-10-31T00:00:00Z"


@pytest.fixture(scope="session")
def vocab():
    return default_vocabulary()


@pytest.fixture(scope="session")
def jobs(vocab):
    return generate_postings(480, vocab.skill_names, seed=3)


@pytest.fixture(scope="session")
def engine(jobs, vocab):
    return AtlasEngine.fit(jobs, vocab, seed=3, n_boot=20)


@pytest.fixture(scope="session")
def claims():
    path = PKG_ROOT / "fixtures" / "synthetic_candidate_claims.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def candidate(claims, vocab):
    return claims_to_vector(claims, vocab, CANDIDATE_REF, as_of=AS_OF)


@pytest.fixture(scope="session")
def contracts():
    out = {}
    for path in (REPO_ROOT / "contracts").glob("*.schema.json"):
        out[path.name] = json.loads(path.read_text(encoding="utf-8"))
    return out


def find_forbidden_keys(obj, forbidden, path="$"):
    """Recursively collect JSON paths whose KEY contains a forbidden fragment.

    Keys that start with ``not_`` are explicit disclaimers (``not_a_hiring_probability``) and are allowed.
    """
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            if not low.startswith("not_") and any(f in low for f in forbidden):
                hits.append(f"{path}.{k}")
            hits.extend(find_forbidden_keys(v, forbidden, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_forbidden_keys(v, forbidden, f"{path}[{i}]"))
    return hits
