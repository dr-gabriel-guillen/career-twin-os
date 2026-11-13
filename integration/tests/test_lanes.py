"""Lane registry and ownership enforcement (AGENTS.md: 'Do not edit other agents' modules')."""

from __future__ import annotations

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
    assert (
        lanes.main(
            ["check", "--repo", str(toy_repo), "--base", base, "--head", head, "--branch", "feature/x"]
        )
        == 0
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


def test_lane_e_branch_only_touches_owned_paths() -> None:
    """Real gate on this checkout: E's diff against main stays inside E's ownership."""
    root = repo_root()
    base = next((ref for ref in ("origin/main", "main") if _ref_exists(root, ref)), None)
    if base is None:
        pytest.skip(
            "UNKNOWN: no main ref in this checkout (CI runs the same check via `python -m ctint.lanes`)"
        )
    changed = lanes.changed_paths(root, base, "HEAD")
    branch = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, check=False).stdout.strip()
    if branch == CONSOLIDATION_BRANCH:
        # The single-owner consolidation branch merges every lane: each changed path must still belong
        # to a lane, and no frozen contract may change.
        assert [p for p in changed if lanes.owner_of(p) is None] == []
        assert [p for p in changed if p.startswith(lanes.FROZEN_SHARED)] == []
        return
    assert lanes.ownership_violations("E", changed) == []


CONSOLIDATION_BRANCH = "agent/ct-consolidation"


def _ref_exists(root: Path, ref: str) -> bool:
    try:
        lanes.resolve_sha(root, ref)
    except RuntimeError:
        return False
    return True
