"""Candidate provenance: the checkout CI tests must carry exactly the pinned lane code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ctint import candidate


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
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    base = _commit(root, {"README.md": "base\n"}, "base")
    code = _commit(
        root,
        {"apps/web/src/a.tsx": "x\n", "packages/atlas/p.py": "x = 1\n", "packages/experiments/e.py": "x\n"},
        "candidate code",
    )
    return root, base, code


def _pins(root: Path, base: str, sha: str, name: str = "pins.candidate.json") -> Path:
    path = root.parent / name
    path.write_text(json.dumps({"base": base, "lanes": dict.fromkeys("ABCD", sha)}), encoding="utf-8")
    return path


def test_report_and_screenshot_commits_after_the_pin_are_allowed(repo: tuple[Path, str, str]) -> None:
    root, base, code = repo
    _commit(root, {"integration/CANDIDATE.md": "report\n", "apps/web/design/shot.png": "png\n"}, "report")
    problems, facts = candidate.verify(root, _pins(root, base, code))
    assert problems == []
    assert any("owned code identical" in f for f in facts)


def test_code_changed_after_the_pin_fails(repo: tuple[Path, str, str]) -> None:
    root, base, code = repo
    _commit(root, {"packages/atlas/p.py": "x = 2\n"}, "unpinned atlas change")
    problems, _ = candidate.verify(root, _pins(root, base, code))
    assert len(problems) == 1 and "lane C" in problems[0] and "re-pin" in problems[0]


def test_pin_not_in_history_or_abbreviated_fails(repo: tuple[Path, str, str]) -> None:
    root, base, code = repo
    _git(root, "checkout", "-q", "-b", "other")
    elsewhere = _commit(root, {"apps/web/src/a.tsx": "y\n"}, "side branch")
    _git(root, "checkout", "-q", "main")
    problems, _ = candidate.verify(root, _pins(root, base, elsewhere))
    assert any("not an ancestor" in p for p in problems)
    problems, _ = candidate.verify(root, _pins(root, base, code[:12]))
    assert any("not a full commit SHA" in p for p in problems)
    problems, _ = candidate.verify(root, _pins(root, base, "0" * 40))
    assert any("not in this repository" in p for p in problems)


def test_baseline_pins_file_is_not_a_candidate(repo: tuple[Path, str, str]) -> None:
    root, base, code = repo
    problems, _ = candidate.verify(root, _pins(root, base, code, name=candidate.BASELINE_PINS))
    assert any("baseline" in p for p in problems)
    assert candidate.main(["verify", "--repo", str(root), "--pins", str(_pins(root, base, code))]) == 0
