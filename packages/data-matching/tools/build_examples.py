"""Regenerate the frozen API example payloads under ``packages/data-matching/examples/``.

Run: ``python packages/data-matching/tools/build_examples.py``. Output is a pure function of
the seeded corpus, so a test can assert the committed examples are current.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PKG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PKG / "src"), str(PKG.parents[1] / "connectors")]

from ct_matching import corpus  # noqa: E402
from ct_matching.service import Service, vault_from_profile  # noqa: E402

OUT = PKG / "examples"


def build() -> dict[str, Any]:
    service = Service()
    report = service.import_doc(corpus.generate())
    service.vault, service.preferences = vault_from_profile(corpus.SYNTHETIC_PROFILE, corpus.AS_OF)
    ranked = service.rank({"as_of": corpus.AS_OF, "top_n": 10})
    top = ranked["shortlist"][0]["job_id"]
    listing = service.query({"as_of": corpus.AS_OF, "status": "active", "family": "fde", "limit": "2"})
    vectors = service.skill_vectors(corpus.AS_OF)
    vectors["jobs"] = vectors["jobs"][:2]
    vectors["note"] = "example truncated to 2 jobs"
    report["rejected"], report["warnings"] = report["rejected"][:4], report["warnings"][:3]
    refused = {}
    for item in ranked["refusals"]:
        refused.setdefault(item["reasons"][0].split(":")[0], item)
    ranked["refusals"] = [refused[k] for k in sorted(refused)]
    ranked["note"] = "example: refusals reduced to one per reason class; result_hash is of the full result"
    return {
        "import_report": report,
        "jobs_query": listing,
        "job_record": service.job(top, corpus.AS_OF),
        "provenance": service.provenance(top),
        "rank_result": ranked,
        "skill_vectors": vectors,
        "vault_export_resume_draft": service.vault.export("resume-draft", corpus.AS_OF),
        "vault_export_internal_matching": service.vault.export("internal-matching", corpus.AS_OF),
        "rank_request": {"as_of": corpus.AS_OF, "top_n": 3, "preferences": corpus.SYNTHETIC_PROFILE["preferences"]},
    }


def render(doc: Any) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, doc in build().items():
        (OUT / f"{name}.json").write_text(render(doc), encoding="utf-8", newline="\n")
    print("wrote", sorted(p.name for p in OUT.glob("*.json")))


if __name__ == "__main__":
    main()
