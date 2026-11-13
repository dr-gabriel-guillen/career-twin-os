"""Lane registry and ownership enforcement (AGENTS.md: 'Do not edit other agents' modules')."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from ctint import lanes
from ctint.paths import repo_root


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=ct-test", "-c", "user.email=ct-test@fictional.example",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"README.md": "base\n", "contracts/x.schema.json": "{}\n"}, "base")
    return repo


def test_registry_matches_agents_md_table() -> None:
    """Fails if AGENTS.md and the code registry drift apart."""
    text = (repo_root() / "AGENTS.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (CAREER-TWIN-[A-E]) \| (\S+) \| (.+?) \|$", text, flags=re.MULTILINE)
    assert len(rows) == 5
    for session, branch, owned in rows:
        lane = lanes.LANES[session[-1]]
        assert lane.session == session
        assert lane.branch == branch
        assert lane.owned == tuple(p.strip() for p in owned.split(","))


def test_owned_prefixes_are_disjoint_and_exclude_frozen_contracts() -> None:
    prefixes = [p for lane in lanes.LANES.values() for p in lane.owned]
    assert len(prefixes) == len(set(prefixes))
    for a in prefixes:
        assert not any(a != b and (a.startswith(b) or b.startswith(a)) for b in prefixes)
        assert not a.startswith(lanes.FROZEN_SHARED)


@pytest.mark.parametrize(
    ("lane", "path", "fragment"),
    [
        ("A", "packages/experiments/x.py", "owned by CAREER-TWIN-B"),
        ("B", "contracts/experiment.schema.json", "frozen contract"),
        ("C", "README.md", "shared baseline"),
        ("D", "integration/ctint/runner.py", "owned by CAREER-TWIN-E"),
        ("E", "tools/reference_engine.py", "shared baseline"),
        ("E", "packages/ui/Button.tsx", "owned by CAREER-TWIN-D"),
        ("A", "packages/data-matching-evil/x.py", "shared baseline"),
        ("D", "apps\\web\\..\\..\\contracts\\x.json", None),
    ],
)
def test_ownership_violations(lane: str, path: str, fragment: str | None) -> None:
    problems = lanes.ownership_violations(lane, [path])
    if fragment is None:
        # A path that textually starts inside the owned prefix: git never emits '..' segments,
        # so this is accepted here and the real protection is that paths come from `git diff`.
        assert problems == []
    else:
        assert len(problems) == 1 and fragment in problems[0]


def test_owned_paths_pass() -> None:
    assert (
        lanes.ownership_violations(
            "E", ["integration/a.py", ".github/workflows/ci.yml", "docs/coordination/X.md"]
        )
        == []
    )
    assert (
        lanes.ownership_violations("A", ["connectors/greenhouse/parse.py", "packages/data-matching/a/b.py"])
        == []
    )


def test_changed_paths_uses_merge_base_and_sees_renames_and_deletes(toy_repo: Path) -> None:
    base = lanes.resolve_sha(toy_repo, "main")
    _git(toy_repo, "checkout", "-q", "-b", "agent/ct-a-data-matching")
    _commit(toy_repo, {"packages/data-matching/a.py": "x = 1\n"}, "a1")
    (toy_repo / "README.md").unlink()
    _git(toy_repo, "mv", "contracts/x.schema.json", "packages/data-matching/x.schema.json")
    head = _commit(toy_repo, {}, "a2: delete README, move a contract")
    _git(toy_repo, "checkout", "-q", "main")
    _commit(toy_repo, {"docs/other.md": "unrelated main change\n"}, "main moves on")
    changed = lanes.changed_paths(toy_repo, "main", head)
    assert sorted(changed) == [
        "README.md", "contracts/x.schema.json", "packages/data-matching/a.py", "packages/data-matching/x.schema.json",
    ]  # fmt: skip
    problems = lanes.ownership_violations("A", changed)
    assert len(problems) == 2
    assert lanes.main(["check", "--repo", str(toy_repo), "--base", base, "--head", head, "--lane", "A"]) == 1
    # An unregistered branch is a policy error (exit 2), never a silent pass.
    assert (
        lanes.main(
            ["check", "--repo", str(toy_repo), "--base", base, "--head", head, "--branch", "feature/x"]
        )
        == 2
    )


def test_cli_passes_for_a_clean_lane_branch(toy_repo: Path) -> None:
    _git(toy_repo, "checkout", "-q", "-b", "agent/ct-c-atlas")
    head = _commit(toy_repo, {"packages/atlas/pca.py": "x = 1\n", "analytics/report.md": "ok\n"}, "c1")
    assert (
        lanes.main(
            [
                "check",
                "--repo",
                str(toy_repo),
                "--base",
                "main",
                "--head",
                head,
                "--branch",
                "origin/agent/ct-c-atlas",
            ]
        )
        == 0
    )
    with pytest.raises(RuntimeError):
        lanes.resolve_sha(toy_repo, "no-such-ref")


def _declared_branch(root: Path) -> str | None:
    """The branch this checkout is declared to be, never inferred from a detached HEAD.

    GitHub checks pull requests out as a detached merge commit and declares the head branch in
    GITHUB_HEAD_REF; ``CT_LANE_BRANCH`` lets an operator declare it locally.
    """
    for variable in ("CT_LANE_BRANCH", "GITHUB_HEAD_REF"):
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    symbolic = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return symbolic or None


def test_checkout_follows_its_branch_policy() -> None:
    """Real gate on this checkout: its diff against main satisfies its branch's ownership policy."""
    root = repo_root()
    base = next((ref for ref in ("origin/main", "main") if _ref_exists(root, ref)), None)
    if base is None:
        pytest.skip(
            "UNKNOWN: no main ref in this checkout (CI runs the same check via `python -m ctint.lanes`)"
        )
    changed = lanes.changed_paths(root, base, "HEAD")
    if not changed:
        return  # main itself, or a branch with nothing to check
    branch = _declared_branch(root)
    if branch is None:
        pytest.skip(
            "UNKNOWN: detached HEAD with no declared branch (set CT_LANE_BRANCH); the CI guard "
            "`ctint.lanes check --branch <head ref>` is the authoritative check"
        )
    policy = lanes.policy_for_branch(branch)  # PolicyError fails the test: no silent pass
    assert lanes.policy_violations(policy, changed) == []


def _ref_exists(root: Path, ref: str) -> bool:
    try:
        lanes.resolve_sha(root, ref)
    except RuntimeError:
        return False
    return True


def test_consolidated_ownership_accepts_lane_paths_and_rejects_contracts_and_unowned() -> None:
    ok = ["apps/web/src/App.tsx", "packages/experiments/tools/e2e_stage.py", "integration/ctint/e2e.py"]
    assert lanes.consolidated_violations(ok) == []
    problems = lanes.consolidated_violations([*ok, "contracts/application-event.schema.json", "README.md"])
    assert any("frozen contract" in p for p in problems)
    assert any("README.md" in p for p in problems)


# --- explicit branch policy (CI checks out pull requests as a detached merge commit) -------------

CONSOLIDATED_OK = {
    "apps/web/src/App.tsx": "x\n",
    "packages/experiments/tools/e2e_stage.py": "x = 1\n",
    "packages/atlas/pca.py": "x = 1\n",
    "connectors/a.py": "x = 1\n",
    "integration/ctint/e2e.py": "x = 1\n",
}


def _check(repo: Path, head: str, *extra: str) -> int:
    return lanes.main(["check", "--repo", str(repo), "--base", "main", "--head", head, *extra])


def _consolidation_branch(repo: Path, files: dict[str, str]) -> str:
    _git(repo, "checkout", "-q", "-b", lanes.CONSOLIDATION_BRANCH)
    return _commit(repo, files, "consolidate")


def test_policy_registry_is_fail_closed() -> None:
    assert lanes.policy_for_branch("agent/ct-b-experiments").lane == lanes.LANES["B"]
    assert lanes.policy_for_branch("refs/heads/agent/ct-consolidation").kind == "consolidated"
    assert lanes.policy_for_branch("agent/ct-f-premium-ui").lane == lanes.LANES["D"]
    forged = [
        None, "", "HEAD", "main", "feature/x", "agent/ct-consolidation-x", "agent/ct-consolidation/x",
        "Agent/CT-Consolidation", "x/agent/ct-consolidation", "agent/ct-z-new-lane",
    ]  # fmt: skip
    for branch in forged:
        with pytest.raises(lanes.PolicyError):
            lanes.policy_for_branch(branch)


def test_consolidated_rule_requires_the_base_repository() -> None:
    same = lanes.policy_for_branch(lanes.CONSOLIDATION_BRANCH, head_repo="o/r", base_repo="o/r")
    assert same.kind == "consolidated"
    with pytest.raises(lanes.PolicyError, match="not this repository"):
        lanes.policy_for_branch(lanes.CONSOLIDATION_BRANCH, head_repo="fork/r", base_repo="o/r")
    with pytest.raises(lanes.PolicyError):
        lanes.policy_for_branch(lanes.CONSOLIDATION_BRANCH, head_repo="", base_repo="o/r")


def test_named_consolidation_branch_passes_lane_owned_paths(toy_repo: Path) -> None:
    head = _consolidation_branch(toy_repo, CONSOLIDATED_OK)
    assert _check(toy_repo, head, "--branch", lanes.CONSOLIDATION_BRANCH) == 0


def test_detached_pr_merge_checkout_uses_the_declared_branch(toy_repo: Path) -> None:
    """The CI case that failed: a detached merge commit, policy taken from the declared head ref."""
    head = _consolidation_branch(toy_repo, CONSOLIDATED_OK)
    _git(toy_repo, "checkout", "-q", "main")
    _git(toy_repo, "checkout", "-q", "--detach")
    _git(toy_repo, "merge", "-q", "--no-ff", "-m", "Merge pull request (synthetic)", head)
    assert _git(toy_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    pr = ["--head-repo", "o/r", "--base-repo", "o/r"]
    assert _check(toy_repo, "HEAD", "--branch", lanes.CONSOLIDATION_BRANCH, *pr) == 0
    # The same detached checkout with no declared branch, or a forged/unknown one, fails closed.
    assert _check(toy_repo, "HEAD", "--branch", "") == 2
    assert _check(toy_repo, "HEAD", "--branch", "HEAD") == 2
    assert _check(toy_repo, "HEAD", "--branch", "agent/ct-consolidation-evil") == 2
    assert _check(toy_repo, "HEAD", "--branch", lanes.CONSOLIDATION_BRANCH, "--head-repo", "fork/r",
                  "--base-repo", "o/r") == 2  # fmt: skip
    # Declaring a lane branch for a cross-lane diff keeps the strict per-lane rule.
    assert _check(toy_repo, "HEAD", "--branch", "agent/ct-e-integration") == 1


def test_consolidation_branch_rejects_frozen_contract_edit(toy_repo: Path) -> None:
    head = _consolidation_branch(toy_repo, {**CONSOLIDATED_OK, "contracts/x.schema.json": '{"changed": 1}\n'})
    assert _check(toy_repo, head, "--branch", lanes.CONSOLIDATION_BRANCH) == 1


def test_consolidation_branch_rejects_contract_deletion_and_unowned_paths(toy_repo: Path) -> None:
    _git(toy_repo, "checkout", "-q", "-b", lanes.CONSOLIDATION_BRANCH)
    _git(toy_repo, "rm", "-q", "contracts/x.schema.json")
    head = _commit(toy_repo, CONSOLIDATED_OK, "drop a contract")
    assert _check(toy_repo, head, "--branch", lanes.CONSOLIDATION_BRANCH) == 1
    _git(toy_repo, "checkout", "-q", "main")
    _git(toy_repo, "checkout", "-q", "-b", "agent/ct-a-data-matching")
    head = _commit(toy_repo, {"packages/data-matching/a.py": "x\n", "tools/new.py": "x\n"}, "a")
    assert _check(toy_repo, head, "--branch", "agent/ct-a-data-matching") == 1


def test_lane_alias_keeps_the_owning_lanes_rule(toy_repo: Path) -> None:
    _git(toy_repo, "checkout", "-q", "-b", "agent/ct-f-premium-ui")
    head = _commit(toy_repo, {"apps/web/src/a.tsx": "x\n", "packages/ui/b.tsx": "x\n"}, "f")
    assert _check(toy_repo, head, "--branch", "agent/ct-f-premium-ui") == 0
    head = _commit(toy_repo, {"integration/ctint/x.py": "x = 1\n"}, "f2")
    assert _check(toy_repo, head, "--branch", "agent/ct-f-premium-ui") == 1
