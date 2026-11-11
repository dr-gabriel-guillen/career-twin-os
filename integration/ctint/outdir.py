"""Fail-closed policy for directories the integration tools write to.

Background: the runner used to do ``out = Path(args.out).resolve(); if out.exists():
shutil.rmtree(out)``. Any existing directory named by ``--out`` (``.``, ``..``, the
repository, a home directory, a symlink to any of them) was deleted recursively before
the run started. Reported as HIGH by the independent reviewer on PR #11.

Policy implemented here:

* The tools never delete a directory they did not create. "Created by this tool" is
  proven by a marker file that names the tool and the directory's own resolved path, so a
  marker copied or moved elsewhere proves nothing.
* An existing, non-empty directory without a matching marker is refused. Always.
* An existing directory WITH a matching marker is replaced only when the caller passes an
  explicit overwrite opt-in. Otherwise the run is refused and the old results stay.
* Some locations are refused whatever they contain: a filesystem root or a top-level
  directory, the home directory, the current directory, the system temp directory, the
  repository or anything containing it, anything inside ``.git``, and — inside the
  repository — anything except a named sub-directory of ``integration/.work`` (the one
  git-ignored tree reserved for generated output).
* The requested path itself may not be a symbolic link or a junction, and every check is
  made on the fully resolved path, so a link cannot be used to reach a refused location.
* Without ``--out`` a new, uniquely named directory is created under ``integration/.work``.

Every refusal raises :class:`OutputPathError` before anything is created or removed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

MARKER_NAME = ".ctint-run.json"
MARKER_TOOL = "ctint.runner"
MARKER_VERSION = 1


class OutputPathError(ValueError):
    """The requested output directory is refused. Nothing was created or deleted."""


def _is_link(path: Path) -> bool:
    """True for a symbolic link (dangling or not) and for a Windows junction."""
    return path.is_symlink() or path.is_junction()


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _same(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def _is_within(child: Path, parent: Path) -> bool:
    """``child`` is ``parent`` or lies below it. Paths on different drives are never within each other."""
    try:
        return os.path.commonpath([_key(child), _key(parent)]) == _key(parent)
    except ValueError:
        return False


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:  # e.g. a symlink loop
        raise OutputPathError(f"output path cannot be resolved: {path} ({exc})") from exc


def marker_matches(directory: Path) -> bool:
    """The directory carries a marker written by this tool FOR THIS EXACT PATH."""
    marker = directory / MARKER_NAME
    if _is_link(marker) or not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("tool") == MARKER_TOOL
        and data.get("marker_version") == MARKER_VERSION
        and isinstance(data.get("path"), str)
        and _same(Path(data["path"]), directory)
    )


def write_marker(directory: Path) -> None:
    payload = {
        "tool": MARKER_TOOL,
        "marker_version": MARKER_VERSION,
        "path": str(directory),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Generated output of the CareerTwin integration runner. Only a directory carrying this "
        "marker for its own path may be replaced, and only with --overwrite.",
    }
    (directory / MARKER_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def validate_output_dir(
    requested: str,
    *,
    repo: Path,
    work_root: Path,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Return the resolved output directory, or raise :class:`OutputPathError`.

    This function never creates or removes anything.
    """
    if not requested or not requested.strip():
        raise OutputPathError("output path is empty")
    if "\x00" in requested:
        raise OutputPathError("output path contains a NUL character")
    lexical = Path(os.path.abspath(requested))
    if _is_link(lexical):
        raise OutputPathError(f"output path is a symbolic link or junction: {lexical}")
    target = _resolved(lexical)
    repo_r = _resolved(repo)
    work_r = _resolved(work_root)
    cwd_r = _resolved(cwd if cwd is not None else Path.cwd())
    home_r = _resolved(home if home is not None else Path.home())
    temp_r = _resolved(Path(tempfile.gettempdir()))

    def refuse(reason: str) -> OutputPathError:
        return OutputPathError(f"refusing output directory {target}: {reason}")

    if target.exists() and not target.is_dir():
        raise refuse("it exists and is not a directory")
    if _same(target, Path(target.anchor)) or _same(target.parent, Path(target.anchor)):
        raise refuse("it is a filesystem root or a top-level directory")
    if any(part.lower() == ".git" for part in target.parts):
        raise refuse("it is inside a .git directory")
    for label, special in (
        ("the home directory", home_r),
        ("the current directory", cwd_r),
        ("the repository", repo_r),
    ):
        if _is_within(special, target):
            raise refuse(f"it is {label} or contains it")
    if _same(target, temp_r):
        raise refuse("it is the system temporary directory itself")
    if _is_within(target, repo_r) and (not _is_within(target, work_r) or _same(target, work_r)):
        raise refuse(
            f"inside the repository only a named sub-directory of {work_r} may be used for generated output"
        )
    # A path that is written under the work tree but resolves somewhere else went through a link.
    if _is_within(lexical, work_root) and not _is_within(target, work_r):
        raise refuse("it leaves the work directory through a link")
    return target


def _remove_previous_run(target: Path) -> None:
    """Remove a directory that carries this tool's marker for its own path. Re-checked immediately before."""
    if _is_link(target) or not marker_matches(target):
        raise OutputPathError(f"refusing to remove {target}: it does not carry this tool's marker")
    # shutil.rmtree removes links found inside the tree; it does not follow them.
    shutil.rmtree(target)


def prepare_output_dir(
    requested: str | None,
    *,
    repo: Path,
    work_root: Path,
    overwrite: bool = False,
    cwd: Path | None = None,
    home: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Create (or, with ``overwrite``, replace a marked previous run of) the output directory.

    Fails closed: any doubt raises :class:`OutputPathError` and leaves the filesystem untouched.
    """
    if requested is None:
        if _is_link(work_root):
            raise OutputPathError(f"the work directory is a symbolic link or junction: {work_root}")
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        for suffix in ("", *(f"-{n}" for n in range(2, 100))):
            candidate = work_root / f"run-{stamp}{suffix}"
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue  # never reuse, never delete: take the next free name
            write_marker(_resolved(candidate))
            return _resolved(candidate)
        raise OutputPathError(f"no free run directory name under {work_root}")

    target = validate_output_dir(requested, repo=repo, work_root=work_root, cwd=cwd, home=home)
    if target.exists() and any(target.iterdir()):
        if not marker_matches(target):
            raise OutputPathError(
                f"refusing output directory {target}: it already exists, is not empty and was not created by "
                "this tool. Nothing was deleted. Choose a new directory."
            )
        if not overwrite:
            raise OutputPathError(
                f"refusing output directory {target}: it holds a previous run. Nothing was deleted. "
                "Pass --overwrite to replace it, or choose a new directory."
            )
        _remove_previous_run(target)
    target.mkdir(parents=True, exist_ok=True)
    write_marker(target)
    return target
