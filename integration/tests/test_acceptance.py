"""Local acceptance launcher (ctint/acceptance.py): pins, tree assembly, workspace policy, provenance.

Everything runs against a toy git repository in pytest's temporary directory. The real pipeline run
through this launcher is recorded in ACCEPTANCE.md; these tests cover the parts that must fail closed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ctint import acceptance, e2e, outdir
from ctint.acceptance import AcceptanceError
from ctint.paths import integration_dir


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def toy(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Base commit plus one commit per lane, each on its own branch from the base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "true")  # the hostile setting: exports must still be byte-exact
    base = _commit(
        repo,
        {
            "README.md": "base\n",
            "contracts/x.schema.json": "{}\n",
            "apps/web/placeholder.txt": "from base: must not survive\n",
            "packages/README.md": "shared, not owned by a lane\n",
        },
        "base",
    )
    lanes = {}
    for key, files in {
        "A": {"packages/data-matching/a.py": "A = 1\n", "connectors/c.py": "C = 1\n"},
        "B": {"packages/experiments/b.py": "B = 1\n"},
        "C": {"packages/atlas/c.py": "C = 1\n", "README.md": "lane C edited a file it does not own\n"},
        "D": {"apps/web/index.html": "<p>lf only</p>\n", "apps/web/placeholder.txt": None},
    }.items():
        _git(repo, "checkout", "-q", "-b", f"lane-{key}", "main")
        lanes[key] = _commit(repo, files, f"lane {key}")
        _git(repo, "checkout", "-q", "main")
    return repo, {"base": base, "lanes": lanes}


# ------------------------------------------------------------------ pins


def test_committed_pins_file_is_valid() -> None:
    pins = acceptance.load_pins(integration_dir() / acceptance.PINS_NAME)
    assert sorted(pins["lanes"]) == ["A", "B", "C", "D"] and len(pins["base"]) == 40


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        json.dumps({"base": "a" * 40, "lanes": {"A": "b" * 40}}),
        json.dumps({"base": "main", "lanes": dict.fromkeys("ABCD", "b" * 40)}),
        json.dumps({"base": "a" * 40, "lanes": {**dict.fromkeys("ABC", "b" * 40), "D": "origin/agent/ct-d"}}),
        json.dumps({"base": "a" * 40, "lanes": {**dict.fromkeys("ABC", "b" * 40), "D": "b" * 39}}),
        json.dumps({"base": "a" * 40, "lanes": {**dict.fromkeys("ABCD", "b" * 40), "E": "b" * 40}}),
    ],
)
def test_pins_must_be_full_shas_for_exactly_four_lanes(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "pins.json"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(AcceptanceError):
        acceptance.load_pins(path)


def test_a_pin_that_is_not_in_the_repository_stops_before_anything_is_created(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    repo, pins = toy
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(json.dumps({**pins, "lanes": {**pins["lanes"], "B": "c" * 40}}), encoding="utf-8")
    workspace = tmp_path / "ws"
    code = acceptance.main(
        ["--repo", str(repo), "--pins", str(pins_path), "--workspace", str(workspace), "--no-serve"]
    )
    assert code == 2 and not workspace.exists()


# ------------------------------------------------------------------ tree assembly


def test_tree_takes_owned_paths_only_from_their_lane_and_everything_else_from_base(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    repo, pins = toy
    tree = tmp_path / "tree"
    counts = acceptance.assemble_tree(repo, pins, tree)
    assert (tree / "packages" / "data-matching" / "a.py").is_file() and (
        tree / "connectors" / "c.py"
    ).is_file()
    assert (tree / "packages" / "experiments" / "b.py").is_file() and (
        tree / "packages" / "atlas" / "c.py"
    ).is_file()
    assert (tree / "contracts" / "x.schema.json").is_file() and (tree / "packages" / "README.md").is_file()
    # A lane's edit outside its owned paths is NOT taken: the base version stays.
    assert (tree / "README.md").read_bytes() == b"base\n"
    # A file under a lane-owned path that only the base has does not survive the lane deleting it.
    assert not (tree / "apps" / "web" / "placeholder.txt").exists()
    # Byte-exact export even with core.autocrlf=true in the repository configuration.
    assert (tree / "apps" / "web" / "index.html").read_bytes() == b"<p>lf only</p>\n"
    assert not (tree / ".git").exists(), "an export, not a checkout: no branch can be moved from here"
    assert counts == {"base": 3, "A": 2, "B": 1, "C": 1, "D": 1}
    with pytest.raises(AcceptanceError, match="already exists"):
        acceptance.assemble_tree(repo, pins, tree)


def test_a_lane_commit_without_its_owned_paths_is_refused(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    repo, pins = toy
    wrong = {
        **pins,
        "lanes": {**pins["lanes"], "B": pins["lanes"]["A"]},
    }  # lane A's commit has no lane B path
    with pytest.raises(AcceptanceError, match="none of its owned paths"):
        acceptance.assemble_tree(repo, wrong, tmp_path / "tree")


def test_assembly_does_not_touch_the_repository(toy: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    repo, pins = toy
    before = (_git(repo, "for-each-ref"), _git(repo, "status", "--porcelain"), _git(repo, "worktree", "list"))
    acceptance.assemble_tree(repo, pins, tmp_path / "tree")
    assert (
        _git(repo, "for-each-ref"),
        _git(repo, "status", "--porcelain"),
        _git(repo, "worktree", "list"),
    ) == before


# ------------------------------------------------------------------ workspace policy


def test_workspace_is_reused_for_the_same_pins_and_needs_rebuild_for_others(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    repo, pins = toy
    requested = str(tmp_path / "ws")
    workspace, fresh = acceptance.prepare_workspace(requested, repo=repo, pins=pins, rebuild=False)
    assert fresh and outdir.marker_matches(workspace)
    (workspace / "tree" / "apps" / "web" / "node_modules").mkdir()
    again, fresh_again = acceptance.prepare_workspace(requested, repo=repo, pins=pins, rebuild=False)
    assert again == workspace and not fresh_again
    assert (workspace / "tree" / "apps" / "web" / "node_modules").is_dir(), "installed dependencies are kept"

    other = {**pins, "lanes": {**pins["lanes"], "D": pins["lanes"]["D"]}, "base": pins["lanes"]["A"]}
    with pytest.raises(AcceptanceError, match="different pins"):
        acceptance.prepare_workspace(requested, repo=repo, pins=other, rebuild=False)
    assert (workspace / "tree" / "apps" / "web" / "node_modules").is_dir(), "a refusal removes nothing"
    _, rebuilt = acceptance.prepare_workspace(requested, repo=repo, pins=other, rebuild=True)
    assert rebuilt and not (workspace / "tree" / "apps" / "web" / "node_modules").exists()
    assert json.loads((workspace / acceptance.LOCK_NAME).read_text(encoding="utf-8")) == other


@pytest.mark.parametrize("rebuild", [False, True])
def test_a_directory_this_tool_did_not_create_is_never_used_as_workspace(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path, rebuild: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, pins = toy
    victim = tmp_path / "victim"
    (victim / "tree").mkdir(parents=True)
    (victim / "tree" / "DO-NOT-DELETE.txt").write_text("precious\n", encoding="utf-8")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("shutil.rmtree must not be called for a foreign directory")

    monkeypatch.setattr(shutil, "rmtree", forbidden)
    with pytest.raises(AcceptanceError, match="was not created by this tool"):
        acceptance.prepare_workspace(str(victim), repo=repo, pins=pins, rebuild=rebuild)
    assert (victim / "tree" / "DO-NOT-DELETE.txt").is_file() and not (victim / outdir.MARKER_NAME).exists()
    for dangerous in (str(repo), str(repo / "apps"), str(tmp_path.parent)):
        with pytest.raises(outdir.OutputPathError):
            acceptance.prepare_workspace(dangerous, repo=repo, pins=pins, rebuild=rebuild)


# ------------------------------------------------------------------ provenance and publishing


def test_provenance_records_pins_stage_status_and_a_hash_per_artifact(
    toy: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    repo, pins = toy
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "jobs.json").write_bytes(b"[]\n")
    (artifacts / "notes.txt").write_text("not an artifact\n", encoding="utf-8")
    results = [e2e.StageResult("jobs.import", "A", "PASS", provider="CAREER-TWIN-A@x", mock=False)]
    record = acceptance.provenance(repo, pins, artifacts, results, seed=7, count=150)
    assert record["synthetic_only"] is True and record["base"] == pins["base"]
    assert {key: value["sha"] for key, value in record["lanes"].items()} == pins["lanes"]
    assert record["artifacts"] == {
        "jobs.json": {"sha256": hashlib.sha256(b"[]\n").hexdigest(), "bytes": 3}
    }, "only JSON artifacts are listed, each with the hash of the exact bytes"
    assert record["stages"] == [
        {"stage": "jobs.import", "lane": "A", "status": "PASS", "provider": "CAREER-TWIN-A@x", "mock": False}
    ]
    assert (
        record["seed"] == 7
        and record["count"] == 150
        and record["harness"]["sha"] == _git(repo, "rev-parse", "HEAD")
    )

    tree = tmp_path / "tree"
    (tree / "apps" / "web" / "dist").mkdir(parents=True)
    served = acceptance.publish_artifacts(tree, artifacts, record)
    assert sorted(p.name for p in served.iterdir()) == ["jobs.json", "provenance.json"]
    assert json.loads((served / "provenance.json").read_text(encoding="utf-8")) == record


def test_ui_dependencies_are_never_installed_without_the_explicit_flag(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "apps" / "web").mkdir(parents=True)
    with pytest.raises(AcceptanceError, match="--install"):
        acceptance.build_ui(tree, {"PATH": ""}, tmp_path, install=False)
    assert not (tree / "apps" / "web" / "node_modules").exists()


@pytest.fixture(autouse=True)
def _need_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
