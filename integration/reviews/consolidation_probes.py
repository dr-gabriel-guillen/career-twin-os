"""Consolidation security probes: does the integrated system refuse what it must refuse?

Run against the artifacts of a finished integration run and a tree that holds the pinned lane code
(for example the workspace tree of ``python -m ctint.acceptance``):

    cd integration
    python reviews/consolidation_probes.py --artifacts .work/<run>/artifacts --tree <workspace>/tree

Every probe works on a private copy of the artifacts in a temporary directory. Two families:

* **producer probes** re-run lane B's real résumé stage on tampered inputs and check that the
  output never cites a claim it must not cite (private, contradicted, expired, removed);
* **validator probes** tamper with one artifact and check that lane E's validator for that stage
  reports it (a validator that stays silent would let a defective lane pass integration).

Exit code 0 = every probe behaved as required, 1 = at least one did not. The JSON report lists each
probe with what was changed and what was observed. Synthetic data only; nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctint import e2e  # noqa: E402
from ctint.runner import offline_env  # noqa: E402

Json = Any


def _load(path: Path) -> Json:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Json) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy(artifacts: Path, dest: Path, *, drop: tuple[str, ...] = ()) -> Path:
    """Copy the JSON artifacts (never lane state directories) into ``dest``."""
    dest.mkdir(parents=True)
    for path in artifacts.iterdir():
        if path.is_file() and path.name not in drop:
            shutil.copyfile(path, dest / path.name)
    return dest


class Probes:
    def __init__(self, artifacts: Path, tree: Path, scratch: Path) -> None:
        self.artifacts, self.tree, self.scratch = artifacts, tree, scratch
        self.env = offline_env(scratch / "home")
        self.results: list[dict[str, Any]] = []
        self.count = len(_load(artifacts / "jobs.json"))

    # ------------------------------------------------------------------ helpers

    def record(self, probe: str, family: str, change: str, ok: bool, observed: str) -> None:
        self.results.append(
            {"probe": probe, "family": family, "change": change, "ok": ok, "observed": observed[:400]}
        )

    def resume_stage(
        self, name: str, mutate: Callable[[list[dict[str, Any]]], None]
    ) -> tuple[int, Json, Path]:
        work = _copy(self.artifacts, self.scratch / name, drop=("resume_variant.json", "ui_report.json"))
        claims = _load(work / "claims.json")
        mutate(claims)
        _dump(work / "claims.json", claims)
        proc = subprocess.run(
            [sys.executable, "packages/experiments/tools/e2e_stage.py", "resume", "--artifacts", str(work)],
            cwd=self.tree, env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )  # fmt: skip
        out = work / "resume_variant.json"
        return proc.returncode, (_load(out) if out.is_file() else None), work

    def producer(
        self, probe: str, change: str, mutate: Callable[[list[dict[str, Any]]], None], forbidden: set[str]
    ) -> None:
        code, variant, _ = self.resume_stage(probe, mutate)
        cited = {b.get("claim_id") for b in (variant or {}).get("bullets", [])}
        leaked = sorted(cited & forbidden)
        if variant is None:
            self.record(
                probe, "producer", change, code != 0, f"stage refused (exit {code}), no résumé written"
            )
        else:
            self.record(
                probe, "producer", change, not leaked,
                f"exit {code}; {len(cited)} bullet(s); forbidden claims cited: {leaked or 'none'}",
            )  # fmt: skip

    def validator(self, probe: str, stage: str, change: str, mutate: Callable[[Path], None]) -> None:
        work = _copy(self.artifacts, self.scratch / probe)
        clean = e2e.VALIDATORS[stage](work, self.count)
        mutate(work)
        try:
            problems = e2e.VALIDATORS[stage](work, self.count)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, OSError) as exc:
            problems = [f"validator raised {type(exc).__name__}: {exc}"]  # the pipeline treats this as FAIL
        new = [p for p in problems if p not in clean]
        self.record(
            probe, "validator", change, bool(new) and not clean,
            (f"flagged: {new[0]}" if new else "NOT FLAGGED")
            + (f" (clean artifacts already had {len(clean)} problem(s))" if clean else ""),
        )  # fmt: skip

    # ------------------------------------------------------------------ probes

    def run(self) -> None:
        claims = _load(self.artifacts / "claims.json")
        variant = _load(self.artifacts / "resume_variant.json")
        cited = {b["claim_id"] for b in variant["bullets"]}
        everything = {c["id"] for c in claims}
        by_id = {c["id"]: c for c in claims}

        # Baseline facts about the real run.
        private_cited = sorted(i for i in cited if by_id[i]["visibility"] == "private")
        self.record("baseline.no-private-claim-cited", "baseline", "none", not private_cited,
                    f"{len(cited)} cited claim(s), private among them: {private_cited or 'none'}")  # fmt: skip
        not_verbatim = [
            b["claim_id"] for b in variant["bullets"] if b.get("text") != by_id[b["claim_id"]]["claim_text"]
        ]
        self.record("baseline.bullets-are-verbatim-claims", "baseline", "none", not not_verbatim,
                    f"bullets whose text differs from the cited claim's text: {not_verbatim or 'none'}")  # fmt: skip

        def set_all(field: str, value: str, ids: set[str]) -> Callable[[list[dict[str, Any]]], None]:
            def mutate(items: list[dict[str, Any]]) -> None:
                for item in items:
                    if item["id"] in ids:
                        item[field] = value

            return mutate

        self.producer("resume.every-claim-private", "every claim set to visibility=private",
                      set_all("visibility", "private", everything), everything)  # fmt: skip
        self.producer("resume.cited-claims-made-private", "the claims the real résumé cites set to private",
                      set_all("visibility", "private", cited), cited)  # fmt: skip
        self.producer("resume.cited-claims-contradicted", "the cited claims set to status=contradicted",
                      set_all("status", "contradicted", cited), cited)  # fmt: skip
        self.producer("resume.cited-claims-expired", "the cited claims given valid_to in the past",
                      set_all("valid_to", "2020-01-01T00:00:00Z", cited), cited)  # fmt: skip

        def remove_cited(items: list[dict[str, Any]]) -> None:
            items[:] = [item for item in items if item["id"] not in cited]

        self.producer(
            "resume.cited-claims-removed", "the cited claims deleted from claims.json", remove_cited, cited
        )

        # ---- validator probes
        first = next(iter(sorted(cited)))

        def edit(name: str, change: Callable[[Json], Json | None]) -> Callable[[Path], None]:
            def mutate(work: Path) -> None:
                doc = _load(work / name)
                replaced = change(doc)
                _dump(work / name, doc if replaced is None else replaced)

            return mutate

        def claim_private(doc: Json) -> None:
            next(c for c in doc if c["id"] == first)["visibility"] = "private"

        self.validator("validator.resume-cites-private-claim", "resume.variant",
                       "a cited claim becomes private after the résumé was written", edit("claims.json", claim_private))  # fmt: skip

        def claim_gone(doc: Json) -> Json:
            return [c for c in doc if c["id"] != first]

        self.validator("validator.resume-cites-unknown-claim", "resume.variant",
                       "a cited claim no longer exists (fabricated evidence)", edit("claims.json", claim_gone))  # fmt: skip

        def embellish(doc: Json) -> None:
            doc["bullets"][0]["text"] += " and grew revenue 300% as Chief Technology Officer"

        self.validator("validator.resume-bullet-embellished", "resume.variant",
                       "bullet text no longer matches the cited claim (invented achievement)",
                       edit("resume_variant.json", embellish))  # fmt: skip

        def unapproved(doc: Json) -> Json:
            return [e for e in doc if e.get("event_type") != "approved"]

        self.validator("validator.submission-without-approval", "application.record",
                       "the user's approval event removed, submission kept", edit("events.json", unapproved))  # fmt: skip

        def system_submits(doc: Json) -> None:
            for event in doc:
                if event.get("event_type") == "submitted":
                    event["actor"] = "system"

        self.validator("validator.system-submits", "application.record",
                       "submission attributed to the system", edit("events.json", system_submits))  # fmt: skip

        def invented_outcome(doc: Json) -> None:
            last = copy.deepcopy(doc[-1])
            last.update({"id": str(last.get("id", "ev")) + "-x", "event_type": "offer", "actor": "system"})
            doc.append(last)

        self.validator("validator.invented-application-outcome", "application.record",
                       "an offer event appended by the system", edit("events.json", invented_outcome))  # fmt: skip

        def false_certainty(doc: Json) -> None:
            doc["verdict"] = "EVIDENCE_FOR_DIFFERENCE"
            doc["claim"] = "Variant B is the winner and causes more interviews."

        self.validator("validator.experiment-false-certainty", "experiment.run",
                       "verdict rewritten to a difference with a causal winner claim, analysis unchanged",
                       edit("analysis.json", false_certainty))  # fmt: skip

        def double_enrol(doc: Json) -> None:
            clone = copy.deepcopy(doc[0])
            arms = sorted({a.get("arm") or a.get("variant") for a in doc} - {None})
            for key in ("arm", "variant"):
                if key in clone and len(arms) > 1:
                    clone[key] = next(a for a in arms if a != clone[key])
            doc.append(clone)

        self.validator("validator.experiment-double-enrolment", "experiment.run",
                       "one unit assigned a second time to the other arm", edit("assignments.json", double_enrol))  # fmt: skip

        def other_projection(doc: Json) -> None:
            for scenario in doc:
                scenario["outputs"]["model_ref"]["projection_id"] = "pca-0000000000000000-2d"

        self.validator("validator.scenario-on-another-projection", "simulator.scenario",
                       "scenario model_ref.projection_id differs from the atlas", edit("scenarios.json", other_projection))  # fmt: skip

        def mixed_projection(doc: Json) -> None:
            doc[0]["projection_id"] = "pca-0000000000000000-2d"

        self.validator("validator.atlas-mixed-projections", "atlas.project",
                       "one atlas point carries another projection id", edit("atlas_points.json", mixed_projection))  # fmt: skip

        def moved_point(doc: Json) -> None:
            for point in doc[: max(8, len(doc) // 4)]:
                point["coords"] = [point["coords"][1] * -3.0, point["coords"][0] * 3.0]

        self.validator("validator.atlas-coordinates-tampered", "atlas.project",
                       "a quarter of the points moved without refitting", edit("atlas_points.json", moved_point))  # fmt: skip

        def real_source(doc: Json) -> None:
            doc[0]["source"] = "linkedin"
            doc[0]["source_url"] = "https://www.linkedin.com/jobs/view/1234567890"

        self.validator("validator.non-synthetic-job-source", "jobs.import",
                       "one posting relabelled as a LinkedIn posting with a real-looking URL", edit("jobs.json", real_source))  # fmt: skip

        def probability(doc: Json) -> None:
            doc[0]["explanation"] = "You have an 87% chance of getting an offer."

        self.validator("validator.match-claims-hiring-probability", "match.rank",
                       "a match explanation states an offer probability", edit("matches.json", probability))  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--artifacts", required=True, help="artifacts directory of a finished run")
    parser.add_argument("--tree", required=True, help="directory holding the pinned lane code")
    parser.add_argument("--out", help="write the JSON report to this file")
    args = parser.parse_args(argv)
    artifacts, tree = Path(args.artifacts).resolve(), Path(args.tree).resolve()
    if not (tree / "packages" / "experiments" / "tools" / "e2e_stage.py").is_file():
        raise SystemExit(f"{tree} does not hold lane B's stage runner")
    with tempfile.TemporaryDirectory(prefix="ct-probes-") as scratch:
        probes = Probes(artifacts, tree, Path(scratch))
        probes.run()
    failed = [r for r in probes.results if not r["ok"]]
    report = {"artifacts": str(artifacts), "tree": str(tree), "probes": probes.results, "failed": len(failed)}
    if args.out:
        _dump(Path(args.out), report)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for r in probes.results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['probe']:<45} {r['observed']}")
    print(f"\n{len(probes.results) - len(failed)} of {len(probes.results)} probes behaved as required")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
