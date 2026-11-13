"""Permanent lane registry and path-ownership checks.

Mirrors the table in ``AGENTS.md``. ``ownership_violations`` is what CI and the
integration runner use to prove a lane only changed files it owns.

    python -m ctint.lanes check --base origin/main --head HEAD --branch agent/ct-e-integration

Which rule applies is decided by an explicit branch policy (``policy_for_branch``), never by
whatever the checkout happens to be called: GitHub checks pull requests out as a detached merge
commit, so CI passes the pull request's declared head ref. The policy is fail-closed: a branch
with no registered policy, or no branch at all, is an error rather than a silent pass.
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

# Single-owner consolidation branch: merges every lane, so the per-lane rule cannot hold. Its rule
# is "every changed path belongs to some lane, and no frozen contract changes".
CONSOLIDATION_BRANCH = "agent/ct-consolidation"
# Follow-on branches that work inside an existing lane's ownership (owner-assigned).
LANE_ALIASES: dict[str, str] = {"agent/ct-f-premium-ui": "D"}


class PolicyError(ValueError):
    """No ownership policy can be applied; callers must fail, not pass."""


@dataclass(frozen=True)
class Policy:
    kind: str  # "lane" or "consolidated"
    label: str
    lane: Lane | None = None


def branch_name(branch: str) -> str:
    return branch.strip().removeprefix("refs/heads/").removeprefix("origin/")


def lane_for_branch(branch: str) -> Lane | None:
    name = branch_name(branch)
    found = next((lane for lane in LANES.values() if lane.branch == name), None)
    if found is None and name in LANE_ALIASES:
        found = LANES[LANE_ALIASES[name]]
    return found


def policy_for_branch(
    branch: str | None, *, head_repo: str | None = None, base_repo: str | None = None
) -> Policy:
    """The ownership rule for a declared branch. Raises ``PolicyError`` rather than returning a pass.

    ``head_repo``/``base_repo`` (``owner/name``) are the pull request's head and base repositories:
    the consolidated rule only applies to a branch in the base repository itself, so a fork cannot
    obtain it by naming its branch ``agent/ct-consolidation``.
    """
    name = branch_name(branch or "")
    if not name or name == "HEAD":
        raise PolicyError(
            "no branch declared (detached HEAD): pass --branch with the pull request's head ref"
        )
    lane = lane_for_branch(name)
    if lane is not None:
        return Policy("lane", lane.session, lane)
    if name == CONSOLIDATION_BRANCH:
        if (head_repo or base_repo) and head_repo != base_repo:
            raise PolicyError(
                f"{name!r} from {head_repo or '?'} is not this repository's ({base_repo or '?'}) "
                "consolidation branch; the consolidated rule does not apply"
            )
        return Policy("consolidated", "consolidation (all lanes)")
    raise PolicyError(
        f"{name!r} has no ownership policy; register it in ctint.lanes or use a lane branch from AGENTS.md"
    )


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


def consolidated_violations(changed: list[str]) -> list[str]:
    """Ownership rule for one commit that merges every lane (single-owner consolidation).

    Per-lane ownership cannot hold when every lane is pinned to the same merged commit, so the
    equivalent rule is applied: every changed path must belong to some lane, and no frozen
    contract may change.
    """
    problems: list[str] = []
    for path in sorted(set(changed)):
        posix = path.replace("\\", "/")
        if posix.startswith(FROZEN_SHARED):
            problems.append(f"{posix}: frozen contract changed without an RFC")
        elif owner_of(posix) is None:
            problems.append(f"{posix}: shared baseline path outside every lane's ownership")
    return problems


def policy_violations(policy: Policy, changed: list[str]) -> list[str]:
    if policy.kind == "consolidated":
        return consolidated_violations(changed)
    if policy.lane is None:
        raise PolicyError(f"policy {policy.label!r} has no lane")
    return ownership_violations(policy.lane.key, changed)


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
    group.add_argument("--branch", help="declared head branch (in CI: the pull request's head ref)")
    check.add_argument("--head-repo", help="owner/name of the pull request's head repository")
    check.add_argument("--base-repo", help="owner/name of the repository being merged into")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.lane:
        lane = LANES[args.lane]
        policy = Policy("lane", lane.session, lane)
    else:
        try:
            policy = policy_for_branch(args.branch, head_repo=args.head_repo, base_repo=args.base_repo)
        except PolicyError as error:
            print(f"OWNERSHIP-POLICY-ERROR: {error}")
            return 2
    changed = changed_paths(repo, args.base, args.head)
    problems = policy_violations(policy, changed)
    for problem in problems:
        print(f"OWNERSHIP-VIOLATION {policy.label}: {problem}")
    print(
        f"lanes: {policy.label} changed {len(changed)} path(s), {len(problems)} violation(s)", file=sys.stderr
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
