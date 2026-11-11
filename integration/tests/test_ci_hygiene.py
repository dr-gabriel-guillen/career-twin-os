"""WS3 — CI must not become a path to secrets, write tokens or paid calls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ctint import sec_ci
from ctint.paths import repo_root

GOOD = """\
name: ci
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Workflows reference no secrets and never use pull_request_target
        env:
          HEAD_REF: ${{ github.head_ref }}
        run: |
          # secrets.FOO and pull_request_target are only mentioned in this comment
          echo "$HEAD_REF"
"""


def test_repository_workflows_are_clean() -> None:
    assert sec_ci.audit_workflows(repo_root()) == []
    assert (repo_root() / ".github" / "workflows" / "ci.yml").is_file()


def test_good_workflow_passes_even_when_it_names_the_forbidden_words() -> None:
    """Regression: CI run 42955735573 failed because a grep matched its own step name."""
    assert sec_ci.audit_workflow_text("ci.yml", GOOD) == []


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda t: t.replace("  pull_request:\n", "  pull_request_target:\n"), "dangerous trigger"),
        (
            lambda t: t.replace(
                "on:\n  pull_request:\n  push:\n    branches: [main]\n", "on: [push, workflow_run]\n"
            ),
            "dangerous trigger",
        ),
        (
            lambda t: t.replace(
                'echo "$HEAD_REF"', 'curl -H "x: ${{ secrets.API_KEY }}" https://paid.example'
            ),
            "repository secret",
        ),
        (
            lambda t: t.replace("HEAD_REF: ${{ github.head_ref }}", "TOKEN: ${{ secrets['GH_PAT'] }}"),
            "repository secret",
        ),
        (lambda t: t.replace("  contents: read", "  contents: write"), "write permission"),
        (lambda t: t.replace("permissions:\n  contents: read\n", ""), "no top-level 'permissions:'"),
        (lambda t: t.replace("permissions:\n  contents: read\n", "permissions: write-all\n"), "write"),
        (lambda t: t.replace("actions/checkout@v4", "some-org/some-action@v1"), "third-party action"),
        (lambda t: t.replace('echo "$HEAD_REF"', 'echo "${{ github.head_ref }}"'), "untrusted event text"),
        (
            lambda t: t.replace('echo "$HEAD_REF"', 'echo "${{ github.event.pull_request.title }}"'),
            "untrusted event text",
        ),
    ],
)
def test_unsafe_workflow_changes_are_detected(mutation: Callable[[str], str], fragment: str) -> None:
    mutated = mutation(GOOD)
    assert mutated != GOOD, "mutation did not apply"
    problems = sec_ci.audit_workflow_text("ci.yml", mutated)
    assert any(fragment in p for p in problems), problems


def test_cli_exit_codes(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(GOOD, encoding="utf-8")
    assert sec_ci.main(["--root", str(tmp_path)]) == 0
    (workflows / "bad.yaml").write_text(GOOD.replace("contents: read", "contents: write"), encoding="utf-8")
    assert sec_ci.main(["--root", str(tmp_path)]) == 1
