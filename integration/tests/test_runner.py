"""WS4 — the disposable integration runner, exercised end to end on a throwaway multi-lane git repo.

Each test builds a tiny repository whose ``main`` holds the real frozen contracts and whose lane
branches carry small stdlib-only stage scripts. Negative cases cover canned output, tampering with
upstream artifacts, ownership violations, secrets, foreign stage claims and failing lane tests.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ctint import runner
from ctint.paths import repo_root

GEN_JOBS = r"""
import json, random, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
seed, count, out = int(args["--seed"]), int(args["--count"]), Path(args["--artifacts"])
rng = random.Random(seed)
jobs = []
for i in range(count):
    jobs.append({
        "id": f"job-toy-{seed}-{i:04d}", "source": "synthetic", "source_id": f"toy:{seed}:{i}",
        "title": rng.choice(["Forward Deployed Engineer", "Platform Engineer"]),
        "organization": "Fictional Toy Works", "url": f"https://jobs.fictional-toy.example/{seed}/{i}",
        "fetched_at": "2026-01-05T09:00:00Z", "status": "active", "skills": sorted(rng.sample(["python", "aws", "sql", "api-design"], 2)),
        "seniority": "senior", "employment_type": "full-time",
        "license_note": "Synthetic fixture. Fictional employer; not a real opening.",
    })
(out / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
"""
CANNED = r"""
import json, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
here = Path(__file__).with_name("frozen_jobs.json")
(Path(args["--artifacts"]) / "jobs.json").write_text(here.read_text(encoding="utf-8"), encoding="utf-8")
"""
TAMPER_CLAIMS = r"""
import json, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
out = Path(args["--artifacts"])
jobs = json.loads((out / "jobs.json").read_text(encoding="utf-8"))
jobs[0]["title"] = "Rewritten by the claims stage"
(out / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
(out / "claims.json").write_text("[]", encoding="utf-8")
"""


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


def _manifest(session: str, stages: dict[str, Any], tests: list[dict[str, Any]] | None = None) -> str:
    return json.dumps(
        {"manifest_version": "0.1", "session_id": session, "tests": tests or [], "stages": stages}
    )


def _jobs_stage(script: str) -> dict[str, Any]:
    return {
        "jobs.import": {
            "mock": False,
            "cmd": [
                "{python}",
                script,
                "--seed",
                "{seed}",
                "--count",
                "{count}",
                "--artifacts",
                "{artifacts}",
            ],
        }
    }


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    files = {
        f"contracts/{p.name}": p.read_text(encoding="utf-8")
        for p in (repo_root() / "contracts").glob("*.json")
    }
    files["README.md"] = "toy base\n"
    _commit(repo, files, "base with real frozen contracts")
    return repo


def _branch(repo: Path, name: str, files: dict[str, str]) -> str:
    _git(repo, "checkout", "-q", "-b", name, "main")
    sha = _commit(repo, files, f"{name} work")
    _git(repo, "checkout", "-q", "main")
    return sha


def _run(repo: Path, out: Path, lanes: dict[str, str], **flags: Any) -> tuple[dict[str, Any], int]:
    args = argparse.Namespace(
        repo=str(repo), base="main", lane=[f"{k}={v}" for k, v in lanes.items()], allow_stub=False,
        allow_setup=False, no_probes=False, seed=3, count=12, out=str(out),
    )  # fmt: skip
    for key, value in flags.items():
        setattr(args, key, value)
    return runner.run(args)


def _stage(report: dict[str, Any], stage_id: str) -> dict[str, Any]:
    return next(s for s in report["stages"] if s["stage"] == stage_id)


def test_clean_lane_stage_passes_and_nothing_else_is_pretended(toy: Path, tmp_path: Path) -> None:
    sha = _branch(toy, "agent/ct-a-data-matching", {
        "packages/data-matching/gen_jobs.py": GEN_JOBS,
        "packages/data-matching/integration-manifest.json": _manifest("CAREER-TWIN-A", _jobs_stage("packages/data-matching/gen_jobs.py")),
    })  # fmt: skip
    refs_before = _git(toy, "for-each-ref")
    report, code = _run(toy, tmp_path / "out", {"A": sha, "B": "none", "C": "none", "D": "none"})
    assert _stage(report, "jobs.import")["status"] == "PASS", _stage(report, "jobs.import")
    assert _stage(report, "profile.claims")["status"] == "BLOCKED"
    assert report["verdict"] == "BLOCKED" and code == 2
    assert report["lanes"][0]["status"] == "PASS" and report["lanes"][0]["sha"] == sha
    assert _git(toy, "for-each-ref") == refs_before, "the runner must not create or move refs"
    assert "wt" not in _git(toy, "worktree", "list"), "temporary worktree must be removed"
    assert _git(toy, "status", "--porcelain") == ""
    assert (
        (tmp_path / "out" / "report.md").read_text(encoding="utf-8").startswith("# Integration run — BLOCKED")
    )
    assert f"--lane A={sha}" in report["reproduce"]


def test_canned_output_is_detected(toy: Path, tmp_path: Path) -> None:
    frozen = subprocess.run([sys.executable, "-c", GEN_JOBS.replace('args["--artifacts"]', '"."'), "--seed", "3", "--count", "12", "--artifacts", "."],
                            cwd=tmp_path, capture_output=True, text=True)  # fmt: skip
    assert frozen.returncode == 0, frozen.stderr
    sha = _branch(toy, "agent/ct-a-data-matching", {
        "packages/data-matching/canned.py": CANNED,
        "packages/data-matching/frozen_jobs.json": (tmp_path / "jobs.json").read_text(encoding="utf-8"),
        "packages/data-matching/integration-manifest.json": _manifest("CAREER-TWIN-A", _jobs_stage("packages/data-matching/canned.py")),
    })  # fmt: skip
    report, code = _run(toy, tmp_path / "out", {"A": sha, "B": "none", "C": "none", "D": "none"})
    stage = _stage(report, "jobs.import")
    assert stage["status"] == "FAIL" and code == 1
    assert any("CANNED-OUTPUT" in p for p in stage["problems"]), stage["problems"]


def test_stage_that_rewrites_upstream_artifacts_fails(toy: Path, tmp_path: Path) -> None:
    stages = {**_jobs_stage("packages/data-matching/gen_jobs.py"),
              "profile.claims": {"mock": False, "cmd": ["{python}", "packages/data-matching/claims.py", "--artifacts", "{artifacts}"]}}  # fmt: skip
    sha = _branch(toy, "agent/ct-a-data-matching", {
        "packages/data-matching/gen_jobs.py": GEN_JOBS, "packages/data-matching/claims.py": TAMPER_CLAIMS,
        "packages/data-matching/integration-manifest.json": _manifest("CAREER-TWIN-A", stages),
    })  # fmt: skip
    report, _ = _run(toy, tmp_path / "out", {"A": sha, "B": "none", "C": "none", "D": "none"})
    stage = _stage(report, "profile.claims")
    assert stage["status"] == "FAIL"
    assert any("TAMPERED-UPSTREAM" in p and "jobs.json" in p for p in stage["problems"]), stage["problems"]
    assert _stage(report, "match.rank")["status"] == "BLOCKED", "rejected artifacts must not feed downstream"


def test_ownership_secret_and_foreign_stage_violations_fail_the_lane(toy: Path, tmp_path: Path) -> None:
    fake_key = "AKIA" + "Z" * 16
    sha = _branch(toy, "agent/ct-b-experiments", {
        "packages/experiments/x.py": f"KEY = '{fake_key}'\n",
        "contracts/experiment.schema.json": "{}\n",
        "packages/experiments/integration-manifest.json": _manifest("CAREER-TWIN-B", _jobs_stage("packages/experiments/x.py")),
    })  # fmt: skip
    report, code = _run(toy, tmp_path / "out", {"A": "none", "B": sha, "C": "none", "D": "none"})
    lane = next(lane for lane in report["lanes"] if lane["lane"] == "B")
    assert lane["status"] == "FAIL" and report["verdict"] == "FAIL" and code == 1
    assert any("frozen contract" in v for v in lane["ownership_violations"])
    assert any(f.startswith("AWS-ACCESS-KEY") for f in lane["secret_findings"])
    assert fake_key not in json.dumps(report), "reports must not echo secrets"
    assert any("another lane" in p for p in lane["manifest_problems"])
    assert not (toy / "contracts" / "experiment.schema.json").read_text(encoding="utf-8").startswith("{}"), (
        "the lane's contract edit must never reach the base checkout"
    )


def test_lane_tests_run_inside_the_worktree_and_failures_are_reported(toy: Path, tmp_path: Path) -> None:
    tests = [
        {"name": "ok", "cmd": ["{python}", "-c", "import sys; sys.exit(0)"]},
        {"name": "broken", "cmd": ["{python}", "-c", "import os, sys; print('HOME=' + os.environ.get('HOME', '')); sys.exit(3)"]},
    ]  # fmt: skip
    sha = _branch(toy, "agent/ct-c-atlas", {
        "packages/atlas/integration-manifest.json": _manifest("CAREER-TWIN-C", {}, tests),
    })  # fmt: skip
    report, code = _run(toy, tmp_path / "out", {"A": "none", "B": "none", "C": sha, "D": "none"})
    lane = next(lane for lane in report["lanes"] if lane["lane"] == "C")
    statuses = {t["name"]: t["status"] for t in lane["tests"]}
    assert statuses == {"ok": "PASS", "broken": "FAIL"}
    assert lane["status"] == "FAIL" and code == 1
    broken = next(t for t in lane["tests"] if t["name"] == "broken")
    assert "exit code 3" in broken["detail"]
    assert "ct-int-" in broken["log_tail"], "lane commands get an empty, temporary HOME"


def test_seed_only_and_unmanifested_lanes_are_distinguished(toy: Path, tmp_path: Path) -> None:
    seed_only = _branch(toy, "agent/ct-c-atlas", {"packages/atlas/README.md": "seed\n"})
    code_only = _branch(toy, "agent/ct-d-product-ux", {"apps/web/src/main.ts": "export {};\n"})
    report, _ = _run(toy, tmp_path / "out", {"A": "none", "B": "none", "C": seed_only, "D": code_only})
    status = {lane["lane"]: lane["status"] for lane in report["lanes"]}
    assert status == {"A": "BLOCKED", "B": "BLOCKED", "C": "BLOCKED", "D": "UNDECLARED"}


def test_unresolvable_ref_is_unknown_not_a_crash(toy: Path, tmp_path: Path) -> None:
    report, code = _run(toy, tmp_path / "out", {"A": "no-such-ref", "B": "none", "C": "none", "D": "none"})
    lane = next(lane for lane in report["lanes"] if lane["lane"] == "A")
    assert lane["sha"] is None and any("UNKNOWN" in n for n in lane["notes"])
    assert code == 2


def test_offline_env_scrubs_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN"):
        monkeypatch.setenv(name, "should-not-leak")
    env = runner.offline_env(tmp_path / "home")
    assert not {k for k, v in env.items() if v == "should-not-leak"}
    assert env["HTTPS_PROXY"] == runner.DEAD_PROXY and env["HOME"] == str(tmp_path / "home")
    assert env["PIP_NO_INDEX"] == "1"
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    kept = {k.upper(): v for k, v in runner.offline_env(tmp_path / "home2").items()}
    assert kept.get("PROGRAMFILES") == r"C:\Program Files"  # Windows env keys are case-insensitive


def test_setup_environment_reaches_the_network_but_keeps_secrets_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: lane D's `npm ci` failed in run cycle2c because the dead proxy leaked into setup."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NPM_TOKEN", "should-not-leak")
    setup = runner.setup_environment(runner.offline_env(tmp_path / "home"))
    assert runner.DEAD_PROXY not in setup.values()
    assert "PIP_NO_INDEX" not in setup and "npm_config_offline" not in setup
    assert "should-not-leak" not in setup.values()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.fictional-corp.example:3128")
    assert runner.setup_environment(runner.offline_env(tmp_path / "home"))["HTTPS_PROXY"].endswith(":3128")


def test_pass_env_accepts_plain_settings_and_refuses_credentials() -> None:
    assert runner.parse_pass_env(["PW_CHANNEL=chrome", "TZ=UTC"]) == {"PW_CHANNEL": "chrome", "TZ": "UTC"}
    for bad in ("GITHUB_TOKEN=x", "OPENAI_API_KEY=x", "DB_PASSWORD=x", "AWS_SECRET_ACCESS_KEY=x",
                "SESSION_COOKIE=x", "NAME=my-token-value", "no-equals", "1BAD=x"):  # fmt: skip
        with pytest.raises(SystemExit):
            runner.parse_pass_env([bad])


def test_passed_env_reaches_lane_commands_and_is_recorded(toy: Path, tmp_path: Path) -> None:
    tests = [
        {
            "name": "sees-channel",
            "cmd": [
                "{python}",
                "-c",
                "import os, sys; sys.exit(0 if os.environ.get('PW_CHANNEL') == 'chrome' else 4)",
            ],
        }
    ]
    sha = _branch(
        toy,
        "agent/ct-c-atlas",
        {"packages/atlas/integration-manifest.json": _manifest("CAREER-TWIN-C", {}, tests)},
    )
    report, _ = _run(
        toy,
        tmp_path / "out",
        {"A": "none", "B": "none", "C": sha, "D": "none"},
        pass_env=["PW_CHANNEL=chrome"],
    )
    lane = next(lane for lane in report["lanes"] if lane["lane"] == "C")
    assert lane["tests"][0]["status"] == "PASS"
    assert (
        report["passed_env"] == {"PW_CHANNEL": "chrome"}
        and "--pass-env PW_CHANNEL=chrome" in report["reproduce"]
    )


@pytest.fixture(autouse=True)
def _need_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
