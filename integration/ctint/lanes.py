"""Permanent lane registry and path-ownership checks.

Mirrors the table in ``AGENTS.md``. ``ownership_violations`` is what CI and the
integration runner use to prove a lane only changed files it owns.

    python -m ctint.lanes check --base origin/main --head HEAD --branch agent/ct-e-integration
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lane:
    key: str
    session: str
    branch: str
    owned: tuple[str, ...]
    issue: int
    pr: int
    role: str


LANES: dict[str, Lane] = {
    "A": Lane("A", "CAREER-TWIN-A", "agent/ct-a-data-matching", ("packages/data-matching/", "connectors/"), 2, 7,
              "job sources, profile evidence, ranking"),
    "B": Lane("B", "CAREER-TWIN-B", "agent/ct-b-experiments", ("packages/applications/", "packages/experiments/"), 3, 8,
              "application CRM, truthful résumés, A/B tests"),
    "C": Lane("C", "CAREER-TWIN-C", "agent/ct-c-atlas", ("packages/atlas/", "analytics/"), 4, 9,
              "PCA/UMAP, career transitions"),
    "D": Lane("D", "CAREER-TWIN-D", "agent/ct-d-product-ux", ("apps/web/", "packages/ui/"), 5, 10,
              "full UI and user workflows"),
    "E": Lane("E", "CAREER-TWIN-E", "agent/ct-e-integration", ("integration/", ".github/", "docs/coordination/"), 6, 11,
              "CI, temporary integration, security"),
}  # fmt: skip
FROZEN_SHARED = ("contracts/",)
MANIFEST_NAME = "integration-manifest.json"


def lane_for_branch(branch: str) -> Lane | None:
    name = branch.removeprefix("refs/heads/").removeprefix("origin/")
    return next((lane for lane in LANES.values() if lane.branch == name), None)


def owner_of(path: str) -> str | None:
    posix = path.replace("\\", "/")
    for lane in LANES.values():
        if posix.startswith(lane.owned):
            return lane.key
    return None


def ownership_violations(lane_key: str, changed: list[str]) -> list[str]:
    """Human-readable violations for every changed path the lane does not own."""
    lane = LANES[lane_key]
    problems: list[str] = []
    for path in sorted(set(changed)):
        posix = path.replace("\\", "/")
        if posix.startswith(lane.owned):
            continue
        if posix.startswith(FROZEN_SHARED):
            problems.append(f"{posix}: frozen contract changed without an RFC")
        elif (other := owner_of(posix)) is not None:
            problems.append(f"{posix}: owned by CAREER-TWIN-{other}")
        else:
            problems.append(f"{posix}: shared baseline path outside {lane.session}'s ownership")
    return problems


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result.stdout


def resolve_sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    """Paths changed on ``head`` since it diverged from ``base`` (three-dot diff, no rename folding)."""
    out = git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
    return [p for p in out.split("\x00") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lane ownership check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--repo", default=".")
    check.add_argument("--base", required=True)
    check.add_argument("--head", default="HEAD")
    group = check.add_mutually_exclusive_group(required=True)
    group.add_argument("--lane", choices=sorted(LANES))
    group.add_argument("--branch")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.lane:
        lane = LANES[args.lane]
    else:
        found = lane_for_branch(args.branch)
        if found is None:
            print(f"lanes: {args.branch!r} is not an agent branch; ownership check not applicable")
            return 0
        lane = found
    changed = changed_paths(repo, args.base, args.head)
    problems = ownership_violations(lane.key, changed)
    for problem in problems:
        print(f"OWNERSHIP-VIOLATION {lane.session}: {problem}")
    print(
        f"lanes: {lane.session} changed {len(changed)} path(s), {len(problems)} violation(s)", file=sys.stderr
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
