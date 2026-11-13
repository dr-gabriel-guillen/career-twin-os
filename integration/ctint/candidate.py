"""Provenance check for a committed acceptance-candidate pins file.

Proves that what CI checks out is what the candidate pins name, so a passing candidate job is
evidence about the pinned code and not about whatever else the branch carries:

* every pin is a full SHA present in the repository and an ancestor of the checked-out commit;
* no code a lane owns has changed between its pinned commit and the checkout (lane E's harness is
  taken from the checkout itself and is not pinned);
* the pins file never claims to be the baseline: it must not be ``acceptance-pins.json``.

    python -m ctint.candidate verify --pins acceptance-pins.candidate.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from .lanes import LANES, git, resolve_sha

# Paths inside a lane's ownership that are not part of the built product (review screenshots).
NON_CODE = ("apps/web/design/",)
BASELINE_PINS = "acceptance-pins.json"


def is_ancestor(repo: Path, ancestor: str, head: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, head],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict) or "base" not in data:
        raise ValueError(f"{path}: expected an object with 'base' and 'lanes'")
    return data


def verify(repo: Path, pins_path: Path, head: str = "HEAD") -> tuple[list[str], list[str]]:
    """Return (problems, facts). An empty problems list means the checkout carries the pinned code."""
    problems: list[str] = []
    facts: list[str] = []
    if pins_path.name == BASELINE_PINS:
        problems.append(f"{pins_path.name} is the baseline pins file, not a candidate")
    pins = load(pins_path)
    head_sha = resolve_sha(repo, head)
    facts.append(f"checkout {head_sha}")
    expected = {key for key in LANES if key != "E"}
    if set(pins["lanes"]) != expected:
        problems.append(f"pins name lanes {sorted(pins['lanes'])}, expected {sorted(expected)}")
    for label, sha in (("base", pins["base"]), *sorted(pins["lanes"].items())):
        try:
            resolved = resolve_sha(repo, str(sha))
        except RuntimeError:
            problems.append(f"{label}: {sha} is not in this repository")
            continue
        if resolved != sha:
            problems.append(f"{label}: pin {sha!r} is not a full commit SHA (resolves to {resolved})")
            continue
        if not is_ancestor(repo, resolved, head_sha):
            problems.append(f"{label}: {resolved[:12]} is not an ancestor of the checkout {head_sha[:12]}")
            continue
        if label == "base" or label not in LANES:
            facts.append(f"{label} {resolved}")
            continue
        owned = list(LANES[label].owned)
        out = git(repo, "diff", "--name-only", "--no-renames", "-z", resolved, head_sha, "--", *owned)
        drift = [p for p in out.split("\x00") if p and not p.startswith(NON_CODE)]
        if drift:
            problems.append(
                f"lane {label}: {len(drift)} owned path(s) changed between the pin {resolved[:12]} and the "
                f"checkout, e.g. {drift[0]}; re-pin the candidate"
            )
        else:
            facts.append(f"lane {label} {resolved}: owned code identical in the checkout")
    return problems, facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acceptance-candidate provenance check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--repo", default="..")
    check.add_argument("--pins", required=True)
    check.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        problems, facts = verify(Path(args.repo).resolve(), Path(args.pins), args.head)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"CANDIDATE-ERROR: {exc}")
        return 2
    for fact in facts:
        print(f"candidate: {fact}")
    for problem in problems:
        print(f"CANDIDATE-PROVENANCE: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
