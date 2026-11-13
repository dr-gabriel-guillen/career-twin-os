"""Workflow hygiene audit for ``.github/workflows`` (threat T-11: CI as an exfiltration path).

Structural, line-based checks that need no YAML parser:

* no ``${{ secrets.* }}`` expression — a public repo's CI must not hold paid or personal credentials;
* no ``pull_request_target`` / ``workflow_run`` trigger (they run with write tokens on untrusted code);
* top-level ``permissions`` present and read-only; no job grants a ``write`` scope;
* untrusted event text (branch names, titles, bodies) is never interpolated into a ``run:`` script;
* only first-party ``actions/*`` actions are used.

    python -m ctint.sec_ci --root ..

The first version of this check was a ``grep`` inside the workflow itself and
failed on its own step name in CI run 42955735573; hence comments and ``name:``
lines are ignored here.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SECRETS = re.compile(r"\$\{\{[^}]*\bsecrets\s*[.\[]")
_DANGEROUS_TRIGGER = re.compile(r"\b(pull_request_target|workflow_run)\b")
_WRITE_SCOPE = re.compile(r"^\s*[a-z-]+\s*:\s*write\s*$|^\s*permissions\s*:\s*write-all\s*$")
_USES = re.compile(r"^\s*-?\s*uses\s*:\s*([^\s#]+)")
_UNTRUSTED = re.compile(
    r"\$\{\{[^}]*github\.(?:head_ref|event\.(?:pull_request\.(?:title|body|head\.ref|head\.label)"
    r"|issue\.(?:title|body)|comment\.body|review\.body|head_commit\.message))"
)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(line number, content) with comments removed and ``name:`` labels dropped."""
    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not stripped.strip() or re.match(r"^\s*-?\s*name\s*:", stripped):
            continue
        lines.append((number, stripped))
    return lines


def audit_workflow_text(path: str, text: str) -> list[str]:
    problems: list[str] = []
    lines = _logical_lines(text)
    in_on = False
    in_run = False
    run_indent = 0
    has_permissions = False
    for number, line in lines:
        indent = len(line) - len(line.lstrip())
        if in_run and indent <= run_indent:
            in_run = False
        if indent == 0:
            in_on = bool(re.match(r"^(on|\"on\"|'on')\s*:", line))
            if re.match(r"^permissions\s*:", line):
                has_permissions = True
        trigger = _DANGEROUS_TRIGGER.search(line) if in_on else None
        if trigger is not None:
            problems.append(f"{path}:{number}: dangerous trigger {trigger.group(1)}")
        if _SECRETS.search(line):
            problems.append(f"{path}:{number}: references a repository secret")
        if _WRITE_SCOPE.match(line):
            problems.append(f"{path}:{number}: grants a write permission ({line.strip()})")
        uses = _USES.match(line)
        if uses and not uses.group(1).startswith(("actions/", "./")):
            problems.append(f"{path}:{number}: third-party action {uses.group(1)}")
        if re.match(r"^\s*-?\s*run\s*:", line):
            in_run, run_indent = True, indent
        if in_run and _UNTRUSTED.search(line):
            problems.append(f"{path}:{number}: untrusted event text interpolated into a run script")
    if not has_permissions:
        problems.append(f"{path}: no top-level 'permissions:' block (token defaults may be read-write)")
    return problems


def audit_workflows(root: Path) -> list[str]:
    directory = root / ".github" / "workflows"
    files = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]) if directory.is_dir() else []
    problems: list[str] = []
    for file in files:
        rel = file.relative_to(root).as_posix()
        problems.extend(audit_workflow_text(rel, file.read_text(encoding="utf-8")))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitHub workflow files")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    problems = audit_workflows(Path(args.root).resolve())
    for problem in problems:
        print(f"CI-HYGIENE {problem}")
    print(f"sec_ci: {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
