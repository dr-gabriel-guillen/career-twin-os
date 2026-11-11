"""WS4 — disposable integration runner.

Combines pinned lane SHAs in a temporary detached git worktree, checks each
lane statically (ownership, manifest, secrets, paid APIs), runs each lane's
declared offline tests, then runs the synthetic end-to-end pipeline.

    python -m ctint.runner --repo . --base origin/main \
        --lane A=origin/agent/ct-a-data-matching --lane B=... [--allow-stub] [--out DIR]

Guarantees:
* never checks out, commits to, merges or pushes any branch — the worktree is
  detached at the base SHA and removed afterwards; refs are compared before/after;
* only a lane's OWNED paths are overlaid from its SHA, so a lane cannot smuggle
  changes to shared files or to another lane into the integration build;
* lane commands run with a scrubbed environment (no credentials, dead proxy,
  empty HOME). This is not a kernel sandbox — see THREAT_MODEL.md residuals;
* the output directory is never deleted unless this tool created it and
  ``--overwrite`` is given; dangerous or linked paths are refused (ctint/outdir.py).

Exit status: 0 PASS · 1 FAIL · 2 BLOCKED / NOT-INTEGRATED / UNKNOWN.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from . import BATCH_ID, SESSION_ID, contracts, e2e, outdir, sec_paid, sec_scan
from .lanes import LANES, MANIFEST_NAME, Lane, changed_paths, git, ownership_violations, resolve_sha
from .paths import integration_dir
from .stub import STUBS, StubProvider

_KEEP_ENV = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    # Windows program-file roots: non-secret system paths that browser launchers use to find an installed
    # browser (lane D's Playwright `chrome` channel failed without them in run final3).
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)
DEAD_PROXY = "http://127.0.0.1:9"


def offline_env(home: Path) -> dict[str, str]:
    """Minimal environment for lane commands: nothing secret is inherited, proxies point nowhere."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
            "XDG_CONFIG_HOME": str(home), "XDG_CACHE_HOME": str(home),
            "CT_OFFLINE": "1", "CI": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "ALL_PROXY": DEAD_PROXY,
            "http_proxy": DEAD_PROXY, "https_proxy": DEAD_PROXY, "all_proxy": DEAD_PROXY,
            "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
            "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "npm_config_offline": "true",
            "NEXT_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1", "GIT_TERMINAL_PROMPT": "0",
        }
    )  # fmt: skip
    return env


_SECRETISH = re.compile(r"(?i)token|secret|passw|key|credential|auth|cookie|session|private|_pat\b|^pat$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def parse_pass_env(items: list[str]) -> dict[str, str]:
    """``NAME=VALUE`` pairs an operator explicitly passes to lane commands, e.g. ``PW_CHANNEL=chrome``.

    Added because lane D's browser E2E needs a browser selection that E's environment scrub removes
    (run final2: Playwright found no browser under the empty temporary HOME). Names that look like
    credentials are refused, and every passed variable is written to the report.
    """
    passed: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not _ENV_NAME.match(name):
            raise SystemExit(f"--pass-env expects NAME=VALUE, got {item!r}")
        if _SECRETISH.search(name) or _SECRETISH.search(value):
            raise SystemExit(f"--pass-env refuses credential-like variable {name!r}")
        passed[name] = value
    return passed


_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def setup_environment(env: dict[str, str]) -> dict[str, str]:
    """Environment for a lane's ``setup`` commands (package installs): network allowed, still no secrets.

    The first version kept ``offline_env``'s dead proxy unless the host itself had proxy variables, so
    lane D's ``npm ci`` could never reach the registry ("Exit handler never called", run cycle2c).
    """
    setup = {k: v for k, v in env.items() if k not in _PROXY_KEYS}
    for name in ("PIP_NO_INDEX", "npm_config_offline"):
        setup.pop(name, None)
    for key in _PROXY_KEYS:  # keep a real corporate proxy if the operator has one
        if os.environ.get(key):
            setup[key] = os.environ[key]
    return setup


def _expand(argv: list[str], values: dict[str, str]) -> list[str]:
    out = []
    for arg in argv:
        for key, value in values.items():
            arg = arg.replace("{" + key + "}", value)
        out.append(arg)
    return out


def run_command(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: int) -> tuple[bool, str, str]:
    """(ok, detail, combined log). Never raises for command failures."""
    # shell=False cannot find Windows launchers such as npm.cmd by bare name (observed with lane D's
    # manifest @ 7686e8c), so resolve argv[0] against the command's own PATH first.
    resolved = shutil.which(argv[0], path=env.get("PATH")) if argv else None
    if resolved:
        argv = [resolved, *argv[1:]]
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_s}s", ""
    except OSError as exc:
        return False, f"cannot execute {argv[0]!r}: {exc}", ""
    return proc.returncode == 0, f"exit code {proc.returncode}", (proc.stdout or "") + (proc.stderr or "")


@dataclass
class CommandProvider:
    name: str
    mock: bool
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_s: int = 300
    is_stub: bool = False

    def run(self, stage: e2e.Stage, artifacts: Path, seed: int, count: int) -> e2e.RunOutcome:
        values = {
            "python": sys.executable, "artifacts": str(artifacts), "seed": str(seed), "count": str(count),
            "repo": str(self.cwd),
        }  # fmt: skip
        ok, detail, log = run_command(_expand(self.argv, values), self.cwd, self.env, self.timeout_s)
        return e2e.RunOutcome(ok, detail if ok else f"{detail}; {log.strip()[-300:]}", log)


@dataclass
class LaneReport:
    lane: str
    session: str
    ref: str
    sha: str | None = None
    status: str = "BLOCKED"
    delivered: bool = False
    merge_base: str | None = None
    based_on_current_base: bool | None = None
    changed_paths: int = 0
    ownership_violations: list[str] = field(default_factory=list)
    manifest: str | None = None
    manifest_problems: list[str] = field(default_factory=list)
    secret_findings: list[str] = field(default_factory=list)
    paid_findings: list[str] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stages: dict[str, Any] = field(default_factory=dict)
    has_code: bool = False


def _manifest_validator() -> Draft202012Validator:
    schema = json.loads((integration_dir() / "lane-manifest.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _ls_tree(repo: Path, sha: str, prefixes: tuple[str, ...]) -> list[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", "-z", sha, "--", *[p.rstrip("/") for p in prefixes])
    return [p for p in out.split("\x00") if p]


def inspect_lane(repo: Path, worktree: Path, lane: Lane, ref: str, base_sha: str) -> LaneReport:
    """Static gates for one lane and overlay of its owned paths into the worktree."""
    report = LaneReport(lane.key, lane.session, ref)
    try:
        sha = resolve_sha(repo, ref)
    except RuntimeError as exc:
        report.notes.append(f"UNKNOWN: ref {ref!r} cannot be resolved ({exc})")
        return report
    report.sha = sha
    report.merge_base = git(repo, "merge-base", base_sha, sha).strip() or None
    report.based_on_current_base = report.merge_base == base_sha
    if not report.based_on_current_base:
        report.notes.append(f"branch forked from {report.merge_base}, not from the pinned base {base_sha}")
    changed = changed_paths(repo, base_sha, sha)
    report.changed_paths = len(changed)
    report.ownership_violations = ownership_violations(lane.key, changed)
    files = _ls_tree(repo, sha, lane.owned)
    for prefix in lane.owned:
        if any(f.startswith(prefix) for f in files):
            git(worktree, "checkout", sha, "--", prefix.rstrip("/"))
    report.secret_findings = [str(f) for f in sec_scan.scan(worktree, files)]
    report.paid_findings = [str(f) for f in sec_paid.scan(worktree, files)]
    manifests = [f for f in files if f.rsplit("/", 1)[-1] == MANIFEST_NAME]
    if not manifests:
        only_docs = all(f.lower().endswith((".md", ".txt")) for f in files)
        report.notes.append(
            "NOT DELIVERED: no integration-manifest.json in owned paths"
            + (
                " (owned paths contain only seed documentation)"
                if only_docs
                else " (code present but not integrable)"
            )
        )
    elif len(manifests) > 1:
        report.manifest_problems.append(f"multiple manifests: {manifests}")
    else:
        report.manifest = manifests[0]
        try:
            data = json.loads((worktree / manifests[0]).read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            report.manifest_problems.append(f"{manifests[0]}: unreadable JSON ({exc})")
            data = None
        if data is not None:
            errors = sorted(
                f"{'/'.join(map(str, e.absolute_path)) or '/'}: {e.message}"
                for e in _manifest_validator().iter_errors(data)
            )
            report.manifest_problems.extend(f"{manifests[0]}: {e}" for e in errors)
            if not errors:
                if data["session_id"] != lane.session:
                    report.manifest_problems.append(
                        f"{manifests[0]}: session_id {data['session_id']!r} in {lane.session}'s path"
                    )
                foreign = sorted(s for s in data["stages"] if e2e.STAGE_BY_ID[s].lane != lane.key)
                if foreign:
                    report.manifest_problems.append(
                        f"{manifests[0]}: declares stages owned by another lane: {foreign}"
                    )
                if not report.manifest_problems:
                    report.delivered = True
                    report.stages = data["stages"]
                    report.tests = [dict(t, status="NOT-RUN") for t in data["tests"]]
                    if data.get("setup"):
                        report.notes.append(f"declares {len(data['setup'])} setup command(s)")
    hard_fail = (
        report.ownership_violations
        or report.manifest_problems
        or report.secret_findings
        or report.paid_findings
    )
    report.has_code = any(not f.lower().endswith((".md", ".txt")) for f in files)
    if hard_fail:
        report.status = "FAIL"
    elif report.delivered:
        report.status = "PASS"
    else:
        # Code present but no manifest: not integrable by pinned SHA yet (distinct from "nothing pushed").
        report.status = "UNDECLARED" if report.has_code else "BLOCKED"
    return report


def run_lane_tests(report: LaneReport, worktree: Path, env: dict[str, str], logs: Path) -> None:
    values = {"python": sys.executable, "repo": str(worktree), "artifacts": "", "seed": "0", "count": "0"}
    for test in report.tests:
        started = time.perf_counter()
        ok, detail, log = run_command(
            _expand(test["cmd"], values), worktree, env, int(test.get("timeout_s", 600))
        )
        test.update(
            status="PASS" if ok else "FAIL", detail=detail, duration_s=round(time.perf_counter() - started, 2)
        )
        (logs / f"lane-{report.lane}-test-{len(list(logs.glob('*')))}.log").write_text(
            log, encoding="utf-8", errors="replace"
        )
        if not ok:
            test["log_tail"] = log.strip()[-800:]
            report.status = "FAIL"
    if report.delivered and not report.tests:
        report.notes.append("UNKNOWN: lane declares no tests")


def overall_verdict(lanes: list[LaneReport], stages: list[e2e.StageResult]) -> str:
    if any(lane.status == "FAIL" for lane in lanes):
        return "FAIL"
    return e2e.verdict(stages)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Integration run — {report['verdict']}",
        "",
        f"- Batch `{report['batch']}` · generated by `{report['generated_by']}` · {report['started_at']}",
        f"- Base `{report['base']['ref']}` = `{report['base']['sha']}`",
        f"- Seed {report['seed']}, {report['count']} synthetic postings, hermetic: {report['hermetic']}, stubs allowed: {report['allow_stub']}",
        f"- Stage summary: {report['stage_summary']}",
        "",
        "## Lanes (pinned SHAs)",
        "",
        "| Lane | SHA | Status | Delivered | Ownership | Manifest | Secrets | Paid/outreach | Tests |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for lane in report["lanes"]:
        tests = lane["tests"]
        test_cell = "—" if not tests else f"{sum(t['status'] == 'PASS' for t in tests)}/{len(tests)} pass"
        lines.append(
            f"| {lane['session']} | `{(lane['sha'] or 'unresolved')[:12]}` | **{lane['status']}** | "
            f"{'yes' if lane['delivered'] else 'no'} | {len(lane['ownership_violations'])} violation(s) | "
            f"{'ok' if lane['manifest'] and not lane['manifest_problems'] else ('invalid' if lane['manifest_problems'] else 'missing')} | "
            f"{len(lane['secret_findings'])} | {len(lane['paid_findings'])} | {test_cell} |"
        )
    lines += [
        "",
        "## Pipeline stages",
        "",
        "| # | Stage | Lane | Status | Provider | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for number, stage in enumerate(report["stages"], start=1):
        detail = "; ".join(stage["problems"][:2] + stage["notes"][:1]).replace("|", "\\|")[:260]
        lines.append(
            f"| {number} | `{stage['stage']}` | {stage['lane']} | **{stage['status']}** | {stage['provider'] or '—'} | {detail} |"
        )
    details = []
    for lane in report["lanes"]:
        for key in ("ownership_violations", "manifest_problems", "secret_findings", "paid_findings", "notes"):
            details += [f"- {lane['session']} {key}: {item}" for item in lane[key]]
        details += [
            f"- {lane['session']} test {t['name']!r}: {t['status']} ({t.get('detail', '')})"
            for t in lane["tests"]
        ]
    for stage in report["stages"]:
        details += [f"- stage {stage['stage']}: {p}" for p in stage["problems"]]
    if details:
        lines += ["", "## Exact findings", "", *details]
    lines += [
        "",
        "## Reproduce",
        "",
        "```",
        report["reproduce"],
        "```",
        "",
        "STUB / MOCK stages are never integration evidence. PASS requires every stage to run on real, non-mock lane code.",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    repo = Path(args.repo).resolve()
    base_sha = resolve_sha(repo, args.base)
    refs_before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    lane_refs: dict[str, str] = {}
    for key, registered in LANES.items():
        if key != "E":
            lane_refs[key] = f"origin/{registered.branch}"
    for item in args.lane or []:
        key, _, ref = item.partition("=")
        if key not in lane_refs or not ref:
            raise SystemExit(f"--lane expects A|B|C|D=<ref>, got {item!r}")
        lane_refs[key] = ref
    # Fail closed: the runner never deletes a directory it did not create (see ctint/outdir.py).
    # A refused --out raises OutputPathError here, before the worktree or anything else is created.
    out = outdir.prepare_output_dir(
        args.out or None,
        repo=repo,
        work_root=integration_dir() / ".work",
        overwrite=bool(getattr(args, "overwrite", False)),
    )
    (out / "logs").mkdir(parents=True)
    temp = Path(tempfile.mkdtemp(prefix="ct-int-"))
    worktree = temp / "wt"
    env = offline_env(temp / "home")
    passed = parse_pass_env(getattr(args, "pass_env", None) or [])
    env.update(passed)
    lanes: list[LaneReport] = []
    stages: list[e2e.StageResult] = []
    started = datetime.now(UTC)
    previous_root = os.environ.get("CT_REPO_ROOT")
    try:
        git(repo, "worktree", "add", "--detach", str(worktree), base_sha)
        for key, ref in lane_refs.items():
            if ref.lower() == "none":
                skipped = LaneReport(key, LANES[key].session, ref)
                skipped.notes.append("UNKNOWN: lane excluded from this run by the operator")
                lanes.append(skipped)
                continue
            lanes.append(inspect_lane(repo, worktree, LANES[key], ref, base_sha))
        os.environ["CT_REPO_ROOT"] = str(worktree)
        contracts._validator.cache_clear()
        hermetic = True
        for lane in lanes:
            if not lane.delivered:
                continue
            if args.allow_setup and lane.manifest:
                hermetic = False
                setup_env = setup_environment(env)
                manifest = json.loads((worktree / lane.manifest).read_text(encoding="utf-8"))
                for command in manifest.get("setup", []):
                    ok, detail, log = run_command(
                        _expand(command, {"python": sys.executable, "repo": str(worktree)}),
                        worktree,
                        setup_env,
                        1800,
                    )
                    lane.notes.append(
                        f"setup {' '.join(command)[:80]!r}: {'ok' if ok else 'FAILED ' + detail}"
                    )
                    if not ok:
                        lane.status = "FAIL"
                        lane.notes.append(log.strip()[-400:])
            run_lane_tests(lane, worktree, env, out / "logs")
        providers: dict[str, e2e.Provider | None] = {}
        by_key = {lane.lane: lane for lane in lanes}
        for stage in e2e.STAGES:
            lane_report = by_key.get(stage.lane)
            declared = lane_report.stages.get(stage.id) if lane_report and lane_report.delivered else None
            if declared is not None and lane_report is not None:
                providers[stage.id] = CommandProvider(
                    f"{lane_report.session}@{(lane_report.sha or '')[:12]}", bool(declared["mock"]), list(declared["cmd"]),
                    worktree, env, int(declared.get("timeout_s", 300)),
                )  # fmt: skip
            elif args.allow_stub and stage.id in STUBS:
                providers[stage.id] = StubProvider()
            else:
                providers[stage.id] = None
        stages = e2e.run_pipeline(
            providers, out / "artifacts", seed=args.seed, count=args.count, probes=not args.no_probes,
            logs=out / "logs",
        )  # fmt: skip
    finally:
        if previous_root is None:
            os.environ.pop("CT_REPO_ROOT", None)
        else:
            os.environ["CT_REPO_ROOT"] = previous_root
        contracts._validator.cache_clear()
        try:
            git(repo, "worktree", "remove", "--force", str(worktree))
        except RuntimeError:
            shutil.rmtree(worktree, ignore_errors=True)
        with contextlib.suppress(RuntimeError):
            git(repo, "worktree", "prune")
        shutil.rmtree(temp, ignore_errors=True)
    refs_after = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    verdict = overall_verdict(lanes, stages)
    harness_errors = (
        [] if refs_before == refs_after else ["HARNESS-ERROR: repository refs changed during the run"]
    )
    if harness_errors:
        verdict = "FAIL"
    lane_args = " ".join(f"--lane {lane.lane}={lane.sha or lane.ref}" for lane in lanes)
    flags = " ".join(
        f
        for f, on in (
            ("--allow-stub", args.allow_stub),
            ("--allow-setup", args.allow_setup),
            ("--no-probes", args.no_probes),
        )
        if on
    )
    flags = " ".join([flags, *(f"--pass-env {k}={v}" for k, v in sorted(passed.items()))]).strip()
    report: dict[str, Any] = {
        "batch": BATCH_ID,
        "generated_by": SESSION_ID,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "base": {"ref": args.base, "sha": base_sha},
        "seed": args.seed,
        "count": args.count,
        "allow_stub": bool(args.allow_stub),
        "hermetic": hermetic,
        "passed_env": dict(sorted(passed.items())),
        "python": sys.version.split()[0],
        "lanes": [asdict(lane) for lane in lanes],
        "stages": [stage.as_dict() for stage in stages],
        "stage_summary": e2e.summarize(stages),
        "harness_errors": harness_errors,
        "reproduce": f"cd integration && python -m ctint.runner --repo .. --base {base_sha} {lane_args} --seed {args.seed} --count {args.count} {flags}".strip(),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    code = 0 if verdict == "PASS" else 1 if verdict == "FAIL" else 2
    return report, code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctint.runner", description="Disposable pinned-SHA integration run")
    parser.add_argument("--repo", default="..", help="path to the git repository (default: ..)")
    parser.add_argument("--base", default="origin/main", help="base ref or SHA (default: origin/main)")
    parser.add_argument(
        "--lane", action="append", metavar="X=REF", help="pin lane A|B|C|D to a ref/SHA, or X=none to exclude"
    )
    parser.add_argument(
        "--allow-stub", action="store_true", help="fill undelivered stages with E-REFERENCE-STUB (never PASS)"
    )
    parser.add_argument(
        "--allow-setup", action="store_true", help="run lanes' setup commands (needs network; non-hermetic)"
    )
    parser.add_argument("--no-probes", action="store_true", help="skip canned-output probes")
    parser.add_argument(
        "--pass-env", action="append", metavar="NAME=VALUE",
        help="pass one explicitly named, non-secret variable to lane commands (recorded in the report)",
    )  # fmt: skip
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument(
        "--out",
        help="report directory (default: a new integration/.work/run-<utc>). Must be new, empty, or a previous "
        "run of this tool; inside the repository only integration/.work/<name> is accepted. Never deleted "
        "unless it carries this tool's marker AND --overwrite is given.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="replace --out if, and only if, it is a previous run directory created by this tool",
    )  # fmt: skip
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report, code = run(args)
    except outdir.OutputPathError as exc:
        print(f"ctint.runner: {exc}", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(render_markdown(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
