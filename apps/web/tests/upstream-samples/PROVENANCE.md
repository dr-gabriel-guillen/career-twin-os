# Pinned upstream samples

These files are **copies of other lanes' real output**, used only to test lane D's adapters (`tests/unit/upstream.test.ts`). They are not edited. All of it is synthetic. Lane D does not own these shapes; if a lane changes one, re-pin and let the tests show what broke.

| Folder | Lane | Commit | Files | How they were produced |
| --- | --- | --- | --- | --- |
| `lane-a/` | CAREER-TWIN-A | `399415569da4f089a8dee2635fb1b13ce246b035` | `rank_result.json` (`a-match/0.1`), `jobs_query.json` and `job_record.json` (`a-ext/0.1` envelope) | Copied from `packages/data-matching/examples/` with `git show <sha>:<path>` |
| `lane-c/` | CAREER-TWIN-C | `3d114b56676c3eaaab3cd0e02a886adadf4f621c` | `atlas.pca.json`, `atlas.axes.json` (`atlas-projection/0.1`), `scenarios.json` (`scenario-outputs/0.1`) | Copied from `analytics/reports/for-lane-d/` with `git show <sha>:<path>` |
| `e-stub/` | CAREER-TWIN-E | `a2e50e339bec831c9bb3f814d38b52bfb535771e` | The artifact layout lane E proposed on Issue #5 (13 files) | `git archive <sha> integration contracts tools fixtures` into a temp folder, then `python -m ctint.stub <stage> --artifacts <dir> --seed 7 --count 24` for each stage; `pin.json` names the top-scored posting |
| `pipeline/` | A, then B, then C | A `399415569da4f089a8dee2635fb1b13ce246b035`, B `84757ed116668cd9d246966f1324b4ca28c916b9`, C `85c0536fb87bdf24e37a60265fb897f6eedac164` | One real integrated run: 15 of the files the three lanes wrote into a shared artifacts folder | Each lane's tree extracted with `git archive <sha>` into its own temp folder, then the stage commands from each lane's `integration-manifest.json`, in order, with `--seed 7 --count 80`: A `jobs.import`, `profile.claims`, `match.rank`, `features.vectors`; `pin.json` = first entry of A's shortlist; B `resume.variant`, `application.record`, `experiment.run`; C `atlas.project`, `simulator.scenario` |

`pipeline/` is the important one: it is what lane E's runner hands to lane D's `ui.e2e` stage. `vectors.json`, `claims_vault.json`, `jobs_import_report.json` and lane B's `lane-b/` journal folder were produced by the run but are not read by the UI and were not copied.

`e-stub/jobs.poisoned.json` is lane E injection corpus: `ctint.sec_inject.poison_jobs(jobs, seed=7)` applied to `e-stub/jobs.json` at the same commit (24 postings whose `requirements` carry active HTML, prompt-injection canaries, bidi and zero-width characters).

`e-stub/` is mock data by lane E's own label (`E-REFERENCE-STUB`). Lane D's artifact source reports those files under `mock_sources`.

To re-pin (from the repository root, after `git fetch`):

```bash
git show origin/agent/ct-a-data-matching:packages/data-matching/examples/rank_result.json > apps/web/tests/upstream-samples/lane-a/rank_result.json
```
