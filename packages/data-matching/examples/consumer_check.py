"""Stand-alone consumer check for lanes B/D/E. Imports NOTHING from lane A.

Reads lane A's JSON output (files or stdin) and validates it against the frozen
contracts on ``main`` using only the standard library, the way a separate consumer
process would.

Usage:
    python consumer_check.py --contracts <repo>/contracts --jobs jobs_query.json --rank rank_result.json [--claims export.json]
Exit code 0 = every object conforms; 1 = violations printed to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCORE_TYPE = "relative_heuristic_not_offer_probability"
_PROBABILITY_WORDS = re.compile(r'"(hiring|offer|interview)[ _]probability"[ ]*:|chance of (an )?(offer|interview)|likelihood of (hire|offer)', re.I)
_DT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")


def _is(value: Any, name: str) -> bool:
    return {"string": isinstance(value, str), "null": value is None, "boolean": isinstance(value, bool),
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool)}[name]


def violations(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Minimal independent JSON Schema check (type/enum/required/additionalProperties/items/minLength/uniqueItems/format)."""
    out: list[str] = []
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else types
        if not any(_is(value, n) for n in names):
            return [f"{path}: wrong type"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: too short")
        if schema.get("format") == "date-time" and not _DT.fullmatch(value):
            out.append(f"{path}: not date-time")
    if isinstance(value, list):
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            out.append(f"{path}: duplicates")
        for i, item in enumerate(value):
            out += violations(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        for key, sub in value.items():
            if key in props:
                out += violations(sub, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected {key}")
    return out


def check(contracts: Path, jobs: Any = None, rank: Any = None, claims: Any = None) -> list[str]:
    problems: list[str] = []
    job_schema = json.loads((contracts / "job-posting.schema.json").read_text(encoding="utf-8"))
    claim_schema = json.loads((contracts / "profile-claim.schema.json").read_text(encoding="utf-8"))
    known_ids: set[str] = set()
    if jobs is not None:
        for item in jobs["items"]:
            problems += [f"job {item['posting'].get('id')}: {v}" for v in violations(item["posting"], job_schema)]
            known_ids.add(item["posting"]["id"])
            if item["posting"]["compensation"] is None and item["ext"]["compensation"]["state"] == "stated":
                problems.append(f"job {item['posting']['id']}: compensation null but state says stated")
        if not jobs["items"]:
            problems.append("jobs: empty item list")
    if rank is not None:
        if rank.get("score_type") != SCORE_TYPE:
            problems.append("rank: score_type is not the non-probability constant")
        if _PROBABILITY_WORDS.search(json.dumps(rank)):
            problems.append("rank: probability wording present")
        for item in rank["shortlist"]:
            if not 0 <= item["score"] <= 100 or item["score_type"] != SCORE_TYPE:
                problems.append(f"rank {item['job_id']}: bad score/score_type")
            if item["availability"]["status"] == "expired":
                problems.append(f"rank {item['job_id']}: expired posting shortlisted")
            if item["compensation"]["stated"] is None and "compensation" not in item["unknown_inputs"]:
                problems.append(f"rank {item['job_id']}: unknown compensation not disclosed")
            if known_ids and jobs.get("total") == len(known_ids) and item["job_id"] not in known_ids:
                problems.append(f"rank {item['job_id']}: job id not in the job listing (referential check)")
    if claims is not None:
        for claim in claims["claims"]:
            problems += [f"claim {claim.get('id')}: {v}" for v in violations(claim, claim_schema)]
            if claim["status"] == "supported" and not claim["evidence_ids"]:
                problems.append(f"claim {claim['id']}: supported without evidence")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", required=True)
    for name in ("jobs", "rank", "claims"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    load = lambda p: None if p is None else json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    problems = check(Path(args.contracts), load(args.jobs), load(args.rank), load(args.claims))
    for line in problems:
        print(line, file=sys.stderr)
    print(json.dumps({"ok": not problems, "violations": len(problems)}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
