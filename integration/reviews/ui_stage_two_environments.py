"""Run lane D's ``ui.e2e`` stage on a finished integration run's artifacts under two environments.

    cd integration && python reviews/ui_stage_two_environments.py .. <d-sha> .work/<run>/artifacts <new-empty-dir>

1. ``scrubbed+bundled-chromium`` — lane E's scrubbed environment plus the local Playwright browser cache;
2. ``operator-env+chrome-channel`` — the operator's own environment with ``PW_CHANNEL=chrome`` (lane D's
   documented method).

Why: in E's runner the browser could not start (installed Chrome refuses DevTools from a profile under the
scrubbed LOCALAPPDATA; the bundled Chromium revision lane D's Playwright needs was not cached), so lane D's
UI was never exercised there. This separates "UI fails on the pipeline output" from "browser cannot start
in E's environment". Uses only lane D's own commands (``npm ci``, ``npm run e2e:integration``); ``npm ci``
needs the network (public npm registry). Writes ``<work-dir>/result.json`` and one log per variant.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

repo, sha, artifacts, work = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
sys.path.insert(0, str(repo / "integration"))
from ctint import runner  # noqa: E402

work.mkdir(parents=True, exist_ok=False)
wt = work / "wt"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(wt), sha], check=True)
npm = shutil.which("npm") or "npm"
results: dict[str, object] = {"d_sha": sha}
try:
    setup = subprocess.run(
        [npm, "ci", "--prefix", "apps/web"],
        cwd=wt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    results["npm_ci"] = setup.returncode
    variants = {
        "scrubbed+bundled-chromium": {
            **runner.offline_env(work / "home1"),
            "PLAYWRIGHT_BROWSERS_PATH": str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"),
        },
        "operator-env+chrome-channel": {**os.environ, "PW_CHANNEL": "chrome"},
    }
    for name, env in variants.items():
        art = work / f"artifacts-{name}"
        shutil.copytree(
            artifacts, art, ignore=shutil.ignore_patterns("ui_report.json", "*.rejected", "lane-b")
        )
        proc = subprocess.run(
            [npm, "run", "e2e:integration", "--prefix", "apps/web", "--", "--artifacts", str(art)],
            cwd=wt,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        report_path = art / "ui_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        log = (proc.stdout or "") + (proc.stderr or "")
        (work / f"log-{name}.txt").write_text(log, encoding="utf-8")
        results[name] = {
            "exit": proc.returncode,
            "report": {
                k: report.get(k) for k in ("tool", "keyboard_only", "data_source", "mock_sources", "tests")
            }
            if report
            else None,
            "detail": {
                k: (report or {}).get("detail", {}).get(k)
                for k in (
                    "pointer_events_observed",
                    "external_requests_observed",
                    "missing_artifacts",
                    "upstream_findings",
                    "steps",
                )
            },
            "error_lines": [line.strip()[:200] for line in log.splitlines() if "Error" in line][:5],
        }
finally:
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], check=False)
(work / "result.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps(results, indent=1, ensure_ascii=True)[:4000])
