"""Output-directory policy (ctint/outdir.py).

Regression tests for the HIGH finding on PR #11: the runner deleted whatever existing
directory ``--out`` named. Every test works in pytest's temporary directory with
sentinel files; nothing here points at the real repository, the home directory or a
filesystem root as a deletion target.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ctint import e2e, outdir
from ctint.outdir import MARKER_NAME, OutputPathError, prepare_output_dir, validate_output_dir

SENTINEL = "DO-NOT-DELETE.txt"


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """A fake repository, home and current directory, all inside the pytest temp dir."""
    repo = tmp_path / "repo"
    work = repo / "integration" / ".work"
    for d in (
        repo / ".git" / "objects",
        repo / "apps" / "web",
        work,
        tmp_path / "home" / "docs",
        tmp_path / "cwd",
    ):
        d.mkdir(parents=True)
    for d in (repo, repo / "apps" / "web", tmp_path / "home", tmp_path / "home" / "docs", tmp_path / "cwd"):
        (d / SENTINEL).write_text("precious\n", encoding="utf-8")
    return {"tmp": tmp_path, "repo": repo, "work": work, "home": tmp_path / "home", "cwd": tmp_path / "cwd"}


def _prepare(world: dict[str, Path], requested: str | None, **kwargs: object) -> Path:
    return prepare_output_dir(
        requested,
        repo=world["repo"],
        work_root=world["work"],
        cwd=world["cwd"],
        home=world["home"],
        **kwargs,  # type: ignore[arg-type]
    )


def _sentinels(world: dict[str, Path]) -> list[Path]:
    return sorted(world["tmp"].rglob(SENTINEL))


def _victim(tmp_path: Path, name: str = "victim") -> Path:
    victim = tmp_path / name
    (victim / "nested").mkdir(parents=True)
    (victim / SENTINEL).write_text("precious\n", encoding="utf-8")
    (victim / "nested" / SENTINEL).write_text("precious\n", encoding="utf-8")
    return victim


# ------------------------------------------------------------------ legitimate use


def test_fresh_directory_is_created_and_marked(world: dict[str, Path]) -> None:
    out = _prepare(world, str(world["tmp"] / "runs" / "one"))
    assert out.is_dir() and list(out.iterdir()) == [out / MARKER_NAME]
    marker = json.loads((out / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["tool"] == "ctint.runner" and Path(marker["path"]) == out
    assert outdir.marker_matches(out)


def test_existing_empty_directory_is_accepted(world: dict[str, Path]) -> None:
    empty = world["tmp"] / "empty"
    empty.mkdir()
    assert _prepare(world, str(empty)) == empty.resolve()
    assert outdir.marker_matches(empty)


def test_default_directory_is_unique_and_never_reuses_or_deletes(world: dict[str, Path]) -> None:
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    first = _prepare(world, None, now=now)
    (first / "report.md").write_text("first run\n", encoding="utf-8")
    second = _prepare(world, None, now=now)  # same second: must not collide with, or remove, the first
    assert first != second and first.parent == second.parent == world["work"].resolve()
    assert first.name == "run-20261111T120000Z" and second.name == "run-20261111T120000Z-2"
    assert (first / "report.md").read_text(encoding="utf-8") == "first run\n"


def test_named_directory_under_the_work_tree_is_allowed(world: dict[str, Path]) -> None:
    out = _prepare(world, str(world["work"] / "ci-run"))
    assert out == (world["work"] / "ci-run").resolve() and outdir.marker_matches(out)


def test_previous_run_is_kept_without_overwrite_and_replaced_with_it(world: dict[str, Path]) -> None:
    out = _prepare(world, str(world["tmp"] / "runs" / "again"))
    (out / "report.md").write_text("old\n", encoding="utf-8")
    with pytest.raises(OutputPathError, match="previous run"):
        _prepare(world, str(out))
    assert (out / "report.md").read_text(encoding="utf-8") == "old\n"
    again = _prepare(world, str(out), overwrite=True)
    assert again == out and not (out / "report.md").exists() and outdir.marker_matches(out)


# ------------------------------------------------------------------ never delete what the tool did not create


@pytest.mark.parametrize("overwrite", [False, True])
def test_existing_unmarked_directory_is_refused_and_untouched(
    world: dict[str, Path], overwrite: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = _victim(world["tmp"])
    before = sorted(p.relative_to(victim) for p in victim.rglob("*"))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("shutil.rmtree must never be called for a directory the tool did not create")

    monkeypatch.setattr(shutil, "rmtree", forbidden)
    with pytest.raises(OutputPathError, match="was not created by this tool"):
        _prepare(world, str(victim), overwrite=overwrite)
    assert sorted(p.relative_to(victim) for p in victim.rglob("*")) == before
    assert not (victim / MARKER_NAME).exists(), "a refused directory must not even receive a marker"


@pytest.mark.parametrize(
    "forged",
    [
        "{not json",
        "[]",
        json.dumps({"tool": "someone-else", "marker_version": 1, "path": "PLACEHOLDER"}),
        json.dumps({"tool": "ctint.runner", "marker_version": 99, "path": "PLACEHOLDER"}),
        json.dumps({"tool": "ctint.runner", "marker_version": 1}),
        json.dumps({"tool": "ctint.runner", "marker_version": 1, "path": "/somewhere/else"}),
    ],
)
def test_a_forged_or_foreign_marker_proves_nothing(world: dict[str, Path], forged: str) -> None:
    victim = _victim(world["tmp"])
    (victim / MARKER_NAME).write_text(forged, encoding="utf-8")
    with pytest.raises(OutputPathError, match="was not created by this tool"):
        _prepare(world, str(victim), overwrite=True)
    assert (victim / SENTINEL).is_file() and (victim / "nested" / SENTINEL).is_file()


def test_a_marker_copied_from_a_real_run_does_not_authorise_another_directory(world: dict[str, Path]) -> None:
    real = _prepare(world, str(world["tmp"] / "runs" / "real"))
    victim = _victim(world["tmp"])
    shutil.copy(real / MARKER_NAME, victim / MARKER_NAME)
    with pytest.raises(OutputPathError, match="was not created by this tool"):
        _prepare(world, str(victim), overwrite=True)
    assert (victim / SENTINEL).is_file()
    # A run directory that was moved is no longer "this path" either.
    moved = world["tmp"] / "moved"
    shutil.move(str(real), str(moved))
    (moved / "data.txt").write_text("x", encoding="utf-8")
    with pytest.raises(OutputPathError, match="was not created by this tool"):
        _prepare(world, str(moved), overwrite=True)
    assert (moved / "data.txt").is_file()


# ------------------------------------------------------------------ dangerous locations


def test_dangerous_locations_are_refused_whatever_they_contain(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(world["cwd"])
    repo, home, tmp = world["repo"], world["home"], world["tmp"]
    anchor = Path(tmp.anchor)
    top_level = Path(*tmp.parts[:2])
    cases = {
        ".": "current directory",
        "..": "or contains it",  # the parent of cwd contains cwd (and, in this layout, the home too)
        str(world["cwd"]): "current directory",
        str(repo): "repository",
        str(repo.parent): "",  # contains the repository, the home and the cwd
        str(repo / "apps"): "inside the repository only",
        str(repo / "apps" / "web"): "inside the repository only",
        str(repo / "apps" / "brand-new"): "inside the repository only",
        str(repo / "integration"): "inside the repository only",
        str(world["work"]): "inside the repository only",
        str(repo / ".git"): ".git",
        str(repo / ".git" / "objects" / "x"): ".git",
        str(home): "home directory",
        str(home.parent): "",
        str(anchor): "filesystem root",
        str(top_level): "top-level",
        tempfile.gettempdir(): "",
        "": "empty",
        "   ": "empty",
        "bad\x00name": "NUL",
    }
    before = _sentinels(world)
    created_before = sorted(p for p in tmp.rglob("*") if p.is_dir())
    for requested, reason in cases.items():
        with pytest.raises(OutputPathError) as caught:
            _prepare(world, requested, overwrite=True)
        assert reason in str(caught.value), (requested, str(caught.value))
    assert _sentinels(world) == before, "no sentinel may disappear"
    assert sorted(p for p in tmp.rglob("*") if p.is_dir()) == created_before, (
        "a refusal must not create directories"
    )
    assert not list(tmp.rglob(MARKER_NAME)), "a refusal must not leave a marker"


def test_a_file_is_not_an_output_directory(world: dict[str, Path]) -> None:
    target = world["tmp"] / "a-file"
    target.write_text("data", encoding="utf-8")
    with pytest.raises(OutputPathError, match="not a directory"):
        _prepare(world, str(target), overwrite=True)
    assert target.read_text(encoding="utf-8") == "data"


def _link(link: Path, target: Path) -> None:
    """Create a directory link without needing privileges: a symlink, or a junction on Windows."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if done.returncode == 0:
            return
    pytest.skip("this platform cannot create a directory link without extra privileges")


def test_a_link_to_another_directory_is_refused_and_its_target_survives(world: dict[str, Path]) -> None:
    victim = _victim(world["tmp"])
    link = world["tmp"] / "innocent-looking-out"
    _link(link, victim)
    with pytest.raises(OutputPathError, match="symbolic link or junction"):
        _prepare(world, str(link), overwrite=True)
    assert (victim / SENTINEL).is_file() and (victim / "nested" / SENTINEL).is_file()


def test_a_link_inside_the_work_tree_cannot_reach_outside_it(world: dict[str, Path]) -> None:
    victim = _victim(world["tmp"])
    _link(world["work"] / "escape", victim)
    # The leaf is a new name below the link, so only the resolved-path checks can catch it.
    with pytest.raises(OutputPathError, match="leaves the work directory through a link"):
        _prepare(world, str(world["work"] / "escape" / "run"), overwrite=True)
    with pytest.raises(OutputPathError):
        _prepare(world, str(world["work"] / "escape"), overwrite=True)
    assert (victim / SENTINEL).is_file() and not (victim / "run").exists()


def test_a_link_into_the_repository_is_judged_by_where_it_leads(world: dict[str, Path]) -> None:
    _link(world["tmp"] / "shortcut", world["repo"] / "apps")
    with pytest.raises(OutputPathError, match="inside the repository only"):
        _prepare(world, str(world["tmp"] / "shortcut" / "web"), overwrite=True)
    assert (world["repo"] / "apps" / "web" / SENTINEL).is_file()


def test_links_inside_a_previous_run_are_removed_not_followed(world: dict[str, Path]) -> None:
    victim = _victim(world["tmp"])
    out = _prepare(world, str(world["tmp"] / "runs" / "with-link"))
    _link(out / "pointer", victim)
    _prepare(world, str(out), overwrite=True)
    assert (victim / SENTINEL).is_file() and (victim / "nested" / SENTINEL).is_file()
    assert not (out / "pointer").exists()


def test_validation_alone_never_touches_the_filesystem(world: dict[str, Path]) -> None:
    before = sorted(world["tmp"].rglob("*"))
    target = validate_output_dir(
        str(world["tmp"] / "new" / "dir"),
        repo=world["repo"],
        work_root=world["work"],
        cwd=world["cwd"],
        home=world["home"],
    )
    assert target == (world["tmp"] / "new" / "dir").resolve()
    assert sorted(world["tmp"].rglob("*")) == before


# ------------------------------------------------------------------ pipeline probe directory


def test_pipeline_refuses_to_delete_a_probe_directory_it_did_not_create(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    foreign = _victim(tmp_path, "artifacts.probe")
    with pytest.raises(OutputPathError, match="will not delete a directory it did not create"):
        e2e.run_pipeline({}, artifacts, probes=True)
    assert (foreign / SENTINEL).is_file() and (foreign / "nested" / SENTINEL).is_file()
