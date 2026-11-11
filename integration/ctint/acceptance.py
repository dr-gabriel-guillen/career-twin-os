"""Local acceptance candidate: one command from pinned lane SHAs to a running UI.

    cd integration
    python -m ctint.acceptance --install     # first run: lane D's `npm ci` needs the npm registry
    python -m ctint.acceptance               # later runs are offline

What it does, in order:

1. reads ``acceptance-pins.json`` (base SHA + one SHA per lane A-D);
2. assembles those SHAs in a workspace under ``integration/.work/`` — the base tree plus each lane's
   owned paths, exported with ``git archive``. No branch is merged, moved or checked out;
3. runs the synthetic pipeline on the real lane code (stages of lanes A, B and C, in the scrubbed
   offline environment of the integration runner) and validates every artifact with lane E's
   contract and cross-stage validators;
4. builds lane D's production bundle, puts this run's artifacts next to it and serves it on
   ``127.0.0.1`` only;
5. writes ``provenance.json``: the pinned SHAs, seed, count, per-stage status and a SHA-256 for every
   artifact the UI is showing.

It is a demonstration on fictional data. It does not prove ranking quality on real postings, it is not
a deployment, and it never deletes a directory it did not create (see ``ctint/outdir.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import contracts, e2e, outdir
from .lanes import LANES, MANIFEST_NAME, git, resolve_sha
from .paths import integration_dir
from .runner import CommandProvider, offline_env, parse_pass_env, run_command, setup_environment

PINS_NAME = "acceptance-pins.json"
LOCK_NAME = "tree.lock.json"
TOOL = "ctint.acceptance"
UI_LANES = ("A", "B", "C", "D")
_SHA = re.compile(r"^[0-9a-f]{40}$")
#: stage shown as SKIP when the browser check was not requested
UI_STAGE = "ui.e2e"


class AcceptanceError(RuntimeError):
    """The candidate could not be assembled or run. The message says what to do."""


# --------------------------------------------------------------------------- pins


def load_pins(path: Path) -> dict[str, Any]:
    """``{"base": sha, "lanes": {"A": sha, "B": sha, "C": sha, "D": sha}}`` with full 40-hex SHAs only."""
    try:
        pins = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcceptanceError(f"cannot read pins file {path}: {exc}") from exc
    lanes = pins.get("lanes") if isinstance(pins, dict) else None
    if not isinstance(lanes, dict) or sorted(lanes) != list(UI_LANES):
        raise AcceptanceError(f"{path}: 'lanes' must name exactly A, B, C and D")
    for label, value in (("base", pins.get("base")), *lanes.items()):
        if not isinstance(value, str) or not _SHA.match(value):
            raise AcceptanceError(f"{path}: {label} must be a full 40-character commit SHA, got {value!r}")
    return {"base": pins["base"], "lanes": {key: lanes[key] for key in UI_LANES}}


# --------------------------------------------------------------------------- tree assembly


def _archive(repo: Path, sha: str, paths: tuple[str, ...] = ()) -> bytes:
    """``git archive`` of a commit as tar bytes, byte-exact (no line-ending conversion)."""
    proc = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), "archive", "--format=tar", sha, "--", *paths],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AcceptanceError(
            f"git archive {sha[:12]} failed: {proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    return proc.stdout


def _owned_by_a_lane(name: str) -> bool:
    posix = name.replace("\\", "/")
    return any((posix + "/").startswith(prefix) for key in UI_LANES for prefix in LANES[key].owned)


def assemble_tree(repo: Path, pins: dict[str, Any], dest: Path) -> dict[str, int]:
    """Export the base commit, then each lane's owned paths from its pinned commit, into ``dest``.

    Paths a lane owns are taken ONLY from that lane's commit: nothing under them is copied from the
    base, so a file the lane deleted cannot survive. ``dest`` must not exist yet.
    """
    if dest.exists():
        raise AcceptanceError(f"{dest} already exists")
    dest.mkdir(parents=True)
    counts: dict[str, int] = {}

    def extract(blob: bytes, keep: Any = None) -> int:
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            members = [m for m in tar.getmembers() if keep is None or keep(m.name)]
            # filter="data" refuses absolute paths, parent-directory escapes, links that leave the
            # destination and special files.
            tar.extractall(dest, members=members, filter="data")  # noqa: S202
            return sum(1 for m in members if m.isfile())

    counts["base"] = extract(_archive(repo, pins["base"]), keep=lambda name: not _owned_by_a_lane(name))
    for key in UI_LANES:
        sha = pins["lanes"][key]
        present = tuple(
            prefix.rstrip("/")
            for prefix in LANES[key].owned
            if git(repo, "ls-tree", "-d", "--name-only", sha, prefix.rstrip("/")).strip()
        )
        if not present:
            raise AcceptanceError(f"lane {key} @ {sha[:12]} has none of its owned paths {LANES[key].owned}")
        counts[key] = extract(_archive(repo, sha, present))
    return counts


def prepare_workspace(
    requested: str | None, *, repo: Path, pins: dict[str, Any], rebuild: bool
) -> tuple[Path, bool]:
    """Return ``(workspace, tree_is_fresh)``. The workspace is kept between runs (it holds node_modules).

    A workspace is reused only when it carries this tool's marker and was assembled from the same pins.
    Different pins need ``--rebuild``; a directory this tool did not create is always refused.
    """
    work_root = integration_dir() / ".work"
    target = outdir.validate_output_dir(
        requested or str(work_root / "acceptance"), repo=repo, work_root=work_root
    )
    if target.exists() and any(target.iterdir()) and not outdir.marker_matches(target):
        raise AcceptanceError(
            f"refusing workspace {target}: it exists, is not empty and was not created by this tool. "
            "Nothing was deleted. Choose another --workspace."
        )
    target.mkdir(parents=True, exist_ok=True)
    outdir.write_marker(target)
    tree, lock = target / "tree", target / LOCK_NAME
    if tree.exists():
        try:
            same = json.loads(lock.read_text(encoding="utf-8")) == pins
        except (OSError, ValueError):
            same = False
        if same:
            return target, False
        if not rebuild:
            raise AcceptanceError(
                f"{tree} was assembled from different pins. Pass --rebuild to replace it "
                "(this also removes its node_modules), or use another --workspace."
            )
        if tree.is_symlink() or tree.is_junction() or not outdir.marker_matches(target):
            raise AcceptanceError(
                f"refusing to remove {tree}: not a plain directory inside a marked workspace"
            )
        lock.unlink(missing_ok=True)
        shutil.rmtree(tree)
    assemble_tree(repo, pins, tree)
    lock.write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
    return target, True


# --------------------------------------------------------------------------- pipeline


def lane_manifest(tree: Path, key: str) -> dict[str, Any]:
    for prefix in LANES[key].owned:
        candidate = tree / prefix / MANIFEST_NAME
        if candidate.is_file():
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    for candidate in sorted(tree.glob(f"*/*/{MANIFEST_NAME}")):
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and loaded.get("session_id") == LANES[key].session:
            return loaded
    raise AcceptanceError(f"lane {key}: no {MANIFEST_NAME} in the assembled tree")


def run_stages(
    tree: Path, pins: dict[str, Any], artifacts: Path, logs: Path, env: dict[str, str], *,
    seed: int, count: int, verify_ui: bool, probes: bool,
) -> list[e2e.StageResult]:  # fmt: skip
    manifests = {key: lane_manifest(tree, key) for key in UI_LANES}
    providers: dict[str, e2e.Provider | None] = {}
    for stage in e2e.STAGES:
        declared = manifests[stage.lane].get("stages", {}).get(stage.id)
        if declared is None or (stage.id == UI_STAGE and not verify_ui):
            providers[stage.id] = None
            continue
        providers[stage.id] = CommandProvider(
            f"{LANES[stage.lane].session}@{pins['lanes'][stage.lane][:12]}", bool(declared["mock"]),
            list(declared["cmd"]), tree, env, int(declared.get("timeout_s", 300)),
        )  # fmt: skip
    previous = os.environ.get("CT_REPO_ROOT")
    os.environ["CT_REPO_ROOT"] = str(tree)  # validators read the frozen contracts of the assembled tree
    contracts._validator.cache_clear()
    try:
        results = e2e.run_pipeline(providers, artifacts, seed=seed, count=count, probes=probes, logs=logs)
    finally:
        if previous is None:
            os.environ.pop("CT_REPO_ROOT", None)
        else:
            os.environ["CT_REPO_ROOT"] = previous
        contracts._validator.cache_clear()
    if not verify_ui:
        for result in results:
            if result.stage == UI_STAGE:
                result.status = "SKIP"
                result.problems = []
                result.notes = ["browser check not requested (add --verify-ui)"]
    return results


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance(
    repo: Path,
    pins: dict[str, Any],
    artifacts: Path,
    results: list[e2e.StageResult],
    *,
    seed: int,
    count: int,
) -> dict[str, Any]:
    try:
        harness = git(repo, "rev-parse", "HEAD").strip()
        dirty = bool(git(repo, "status", "--porcelain", "--", "integration").strip())
    except RuntimeError:
        harness, dirty = "unknown", True
    return {
        "tool": TOOL,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic_only": True,
        "note": "Fictional data produced by the pinned lane commits below. Not real postings, not a real "
        "person, not evidence of real-world ranking quality.",
        "base": pins["base"],
        "lanes": {key: {"session": LANES[key].session, "sha": pins["lanes"][key]} for key in UI_LANES},
        "harness": {"session": LANES["E"].session, "sha": harness, "uncommitted_changes": dirty},
        "seed": seed,
        "count": count,
        "stages": [
            {"stage": r.stage, "lane": r.lane, "status": r.status, "provider": r.provider, "mock": r.mock}
            for r in results
        ],
        "artifacts": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in sorted(artifacts.iterdir())
            if path.is_file() and path.suffix == ".json" and path.name != "provenance.json"
        },
    }


# --------------------------------------------------------------------------- UI


def _node(env: dict[str, str]) -> str:
    node = shutil.which("node", path=env.get("PATH"))
    if not node:
        raise AcceptanceError("node was not found on PATH (lane D needs Node.js >= 22.18)")
    return node


def build_ui(tree: Path, env: dict[str, str], logs: Path, *, install: bool) -> None:
    web = tree / "apps" / "web"
    if not (web / "node_modules" / "vite" / "bin" / "vite.js").is_file():
        if not install:
            raise AcceptanceError(
                "lane D's dependencies are not installed in the workspace. Re-run with --install: it runs "
                "`npm ci --prefix apps/web` from the pinned lockfile and needs the npm registry (network). "
                "No browser is downloaded."
            )
        ok, detail, log = run_command(
            ["npm", "ci", "--prefix", "apps/web"], tree, setup_environment(env), 1800
        )
        (logs / "ui-npm-ci.log").write_text(log, encoding="utf-8", errors="replace")
        if not ok:
            raise AcceptanceError(f"npm ci failed ({detail}); see {logs / 'ui-npm-ci.log'}")
    ok, detail, log = run_command(["npm", "run", "build", "--prefix", "apps/web"], tree, env, 900)
    (logs / "ui-build.log").write_text(log, encoding="utf-8", errors="replace")
    if not ok:
        raise AcceptanceError(f"UI build failed ({detail}); see {logs / 'ui-build.log'}")


def publish_artifacts(tree: Path, artifacts: Path, record: dict[str, Any]) -> Path:
    """Copy this run's JSON artifacts next to the built bundle (same origin, read-only for the UI)."""
    served = tree / "apps" / "web" / "dist" / "artifacts"
    if served.is_symlink() or served.is_junction():
        raise AcceptanceError(f"{served} is a link; refusing to write through it")
    served.mkdir(parents=True, exist_ok=True)
    for path in sorted(artifacts.iterdir()):
        if path.is_file() and path.suffix == ".json" and not path.is_symlink():
            shutil.copyfile(path, served / path.name)
    (served / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return served


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def serve(tree: Path, env: dict[str, str], port: int) -> subprocess.Popen[bytes]:
    if _port_open(port):
        raise AcceptanceError(f"port {port} is already in use on 127.0.0.1; pass another --port")
    web = tree / "apps" / "web"
    server = subprocess.Popen(
        [_node(env), "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=web, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AcceptanceError(f"the preview server exited with code {server.returncode}")
        if _port_open(port):
            return server
        time.sleep(0.25)
    server.terminate()
    raise AcceptanceError("the preview server did not start within 60 s")


# --------------------------------------------------------------------------- report


def render(pins: dict[str, Any], results: list[e2e.StageResult], run_dir: Path) -> str:
    lines = ["", "CareerTwin OS - local acceptance candidate (synthetic data only)", ""]
    lines.append(f"  base  {pins['base']}")
    lines.extend(f"  {key}     {pins['lanes'][key]}  {LANES[key].session}" for key in UI_LANES)
    lines += ["", f"  {'stage':<20} {'lane':<5} {'status':<8} detail"]
    for r in results:
        detail = "; ".join([*r.problems, *r.notes])[:140]
        lines.append(f"  {r.stage:<20} {r.lane:<5} {r.status:<8} {detail}")
    lines += ["", f"  evidence: {run_dir}", ""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Assemble, run and serve the local acceptance candidate"
    )
    parser.add_argument("--repo", default="..", help="path to the git repository (default: ..)")
    parser.add_argument("--pins", help=f"pins file (default: integration/{PINS_NAME})")
    parser.add_argument("--workspace", help="workspace directory (default: integration/.work/acceptance)")
    parser.add_argument(
        "--rebuild", action="store_true", help="replace a workspace tree assembled from other pins"
    )
    parser.add_argument(
        "--install", action="store_true", help="allow `npm ci` for lane D (needs the npm registry)"
    )
    parser.add_argument(
        "--verify-ui", action="store_true", help="also run lane D's keyboard-only browser stage"
    )
    parser.add_argument(
        "--probes", action="store_true", help="also run the input-sensitivity probes (slower)"
    )
    parser.add_argument(
        "--pass-env", action="append", metavar="NAME=VALUE",
        help="pass one non-secret variable to lane commands, e.g. PW_CHANNEL=chrome for --verify-ui",
    )  # fmt: skip
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--no-serve", action="store_true", help="assemble, run and build, then exit")
    return parser


def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    pins = load_pins(Path(args.pins) if args.pins else integration_dir() / PINS_NAME)
    for label, sha in (("base", pins["base"]), *pins["lanes"].items()):
        try:
            resolved = resolve_sha(repo, sha)
        except RuntimeError as exc:
            raise AcceptanceError(
                f"{label} {sha[:12]} is not in this repository; run `git fetch origin` ({exc})"
            ) from exc
        if resolved != sha:
            raise AcceptanceError(f"{label}: {sha} resolved to {resolved}")
    workspace, fresh = prepare_workspace(args.workspace, repo=repo, pins=pins, rebuild=args.rebuild)
    tree = workspace / "tree"
    print(f"workspace: {workspace} ({'assembled now' if fresh else 'reused, same pins'})", flush=True)
    run_dir = outdir.prepare_output_dir(None, repo=repo, work_root=workspace / "runs")
    artifacts, logs = run_dir / "artifacts", run_dir / "logs"
    logs.mkdir()
    env = offline_env(workspace / "home")
    env.update(parse_pass_env(args.pass_env or []))
    if args.verify_ui:
        build_ui(tree, env, logs, install=args.install)  # the browser stage needs node_modules first
    results = run_stages(
        tree, pins, artifacts, logs, env, seed=args.seed, count=args.count, verify_ui=args.verify_ui, probes=args.probes
    )  # fmt: skip
    record = provenance(repo, pins, artifacts, results, seed=args.seed, count=args.count)
    (run_dir / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(render(pins, results, run_dir), flush=True)
    failed = [r.stage for r in results if r.status not in ("PASS", "SKIP")]
    if failed:
        print(
            f"NOT READY: {', '.join(failed)} did not pass on real lane code. The UI is not started.",
            file=sys.stderr,
        )
        return 1
    build_ui(tree, env, logs, install=args.install)
    publish_artifacts(tree, artifacts, record)
    if args.no_serve:
        print("built; not serving (--no-serve)")
        return 0
    server = serve(tree, env, args.port)
    base = f"http://127.0.0.1:{args.port}"
    print(f"  UI on this run's pipeline artifacts:  {base}/?source=artifacts#/jobs")
    print(f"  UI on lane D's built-in fixtures:     {base}/#/jobs")
    print(f"  provenance of what is shown:          {base}/artifacts/provenance.json")
    print("  Local only (127.0.0.1). Press Ctrl+C to stop.", flush=True)
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server.poll() is None:
            server.terminate()
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (AcceptanceError, outdir.OutputPathError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
