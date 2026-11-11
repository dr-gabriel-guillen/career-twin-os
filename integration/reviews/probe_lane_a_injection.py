"""Reproduce lane E's review findings A-F1..A-F3 against lane A's code (read-only).

    git worktree add --detach /tmp/lane-a <A-sha>
    cd integration && python reviews/probe_lane_a_injection.py --lane-root /tmp/lane-a

Offline and synthetic: uses lane A's own seeded corpus and synthetic profile. Prints JSON:

* ``minimal``  — one hostile string at a time inserted after the first ``</p>`` of ONE posting per
  source; reports every string that changes extracted required/preferred skills (A-F1, A-F2);
* ``differential`` — every posting poisoned outside its Requirements list; reports postings whose
  refusal reason changed to something other than ``content-integrity`` (A-F3 and phantom evasion)
  and score changes among postings eligible in both runs.

Exit status 1 if any finding reproduces, else 0. Not part of the isolated test suite because it needs
lane A's code; lane E runs it per pinned SHA during review.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ctint import sec_inject  # noqa: E402

SEED, N = 20261110, 120


def _jobs(capture: dict[str, Any]) -> list[Any]:
    payload = capture["payload"]
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    return jobs if isinstance(jobs, list) else []


def _html_fields(job: dict[str, Any]) -> list[str]:
    return [k for k, v in job.items() if isinstance(v, str) and "</p>" in v]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane-root", required=True)
    args = parser.parse_args(argv)
    root = Path(args.lane_root).resolve()
    for rel in ("packages/data-matching/src", "connectors"):
        sys.path.insert(0, str(root / rel))
    from ct_matching import corpus  # type: ignore[import-not-found]
    from ct_matching.service import Service  # type: ignore[import-not-found]

    bundle = corpus.generate(SEED, N)
    profile = json.loads(
        (root / "packages/data-matching/fixtures/synthetic_profile.json").read_text(encoding="utf-8")
    )
    as_of = bundle["meta"]["as_of"]

    def skills(doc: dict[str, Any]) -> list[tuple[Any, Any]]:
        service = Service()
        service.import_doc(copy.deepcopy(doc))
        return [
            (e["ext"].get("skills_required"), e["ext"].get("skills_preferred"))
            for e in service.store.view(as_of)
        ]

    def rank(doc: dict[str, Any]) -> dict[str, Any]:
        service = Service()
        service.import_doc(copy.deepcopy(doc))
        result: dict[str, Any] = service.rank({"as_of": as_of, "profile": profile, "top_n": 50})
        return result

    hostile = [("prompt", p.format(canary="CT-CANARY-TEST")) for p in sec_inject.PROMPT_INJECTIONS]
    hostile += [("html", h) for h in sec_inject.HTML_PAYLOADS] + [
        ("unicode", u) for u in sec_inject.UNICODE_PAYLOADS
    ]
    minimal: list[dict[str, Any]] = []
    for source in ("ashby", "lever", "greenhouse"):
        capture = next(
            (c for c in bundle["captures"] if c["capture"]["source"] == source and any(isinstance(j, dict) and _html_fields(j) for j in _jobs(c))),
            None,
        )  # fmt: skip
        if capture is None:
            continue
        single = copy.deepcopy(capture)
        jobs = _jobs(single)
        first = next(j for j in jobs if isinstance(j, dict) and _html_fields(j))
        jobs[:] = [first]
        base = skills(single)
        for kind, text in hostile:
            trial = copy.deepcopy(single)
            job = _jobs(trial)[0]
            for key in _html_fields(job):
                job[key] = job[key].replace(
                    "</p>", "</p>" + (text if kind == "html" else f"<p>{text}</p>"), 1
                )
            got = skills(trial)
            if got != base:
                minimal.append(
                    {"source": source, "kind": kind, "inserted": text, "before": base, "after": got}
                )

    poisoned = copy.deepcopy(bundle)
    for i, capture in enumerate(poisoned["captures"]):
        for j, job in enumerate(_jobs(capture)):
            if not isinstance(job, dict):
                continue
            canary = sec_inject.canary_for(f"{i}-{j}")
            text = (
                f"<p>{sec_inject.PROMPT_INJECTIONS[(i + j) % len(sec_inject.PROMPT_INJECTIONS)].format(canary=canary)}</p>"
                f"{sec_inject.HTML_PAYLOADS[(i + j) % len(sec_inject.HTML_PAYLOADS)]}"
                f"<p>{sec_inject.UNICODE_PAYLOADS[(i + j) % len(sec_inject.UNICODE_PAYLOADS)]}</p>"
            )
            for key in _html_fields(job):
                job[key] = re.sub(r"(</p>)", lambda m, t=text: m.group(1) + t, job[key], count=1)
    clean, dirty = rank(bundle), rank(poisoned)
    reasons_clean = {r["job_id"]: r["reasons"] for r in clean["refusals"]}
    reasons_dirty = {r["job_id"]: r["reasons"] for r in dirty["refusals"]}
    eligible_clean = {r["job_id"]: r for r in clean["shortlist"]}
    eligible_dirty = {r["job_id"]: r for r in dirty["shortlist"]}
    evasions = [
        {"job_id": jid, "clean": reasons_clean.get(jid, ["ELIGIBLE"]), "poisoned": reasons_dirty.get(jid, ["ELIGIBLE"])}
        for jid in sorted(set(reasons_clean) | set(reasons_dirty) | set(eligible_clean) | set(eligible_dirty))
        if reasons_clean.get(jid, ["ELIGIBLE"]) != reasons_dirty.get(jid, ["ELIGIBLE"])
        and not reasons_dirty.get(jid, ["ELIGIBLE"])[0].startswith("content-integrity")
    ]  # fmt: skip
    score_changes = [
        {"job_id": jid, "clean": eligible_clean[jid]["score"], "poisoned": eligible_dirty[jid]["score"]}
        for jid in sorted(set(eligible_clean) & set(eligible_dirty))
        if eligible_clean[jid]["score"] != eligible_dirty[jid]["score"]
    ]
    report = {
        "lane_root": str(root), "seed": SEED, "n": N,
        "minimal": minimal,
        "differential": {
            "content_integrity_refusals": [clean["corpus"]["refusal_reasons"].get("content-integrity", 0),
                                           dirty["corpus"]["refusal_reasons"].get("content-integrity", 0)],
            "evasions": evasions, "score_changes_among_eligible": score_changes,
            "canary_leaks": sec_inject.canary_leaks(dirty)[:5],
        },
    }  # fmt: skip
    print(json.dumps(report, indent=1, ensure_ascii=True))
    return 1 if minimal or evasions or score_changes or report["differential"]["canary_leaks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
