# Pinned upstream samples

These files are **copies of other lanes' real output**, used only to test lane D's adapters (`tests/unit/upstream.test.ts`). They are not edited. All of it is synthetic. Lane D does not own these shapes; if a lane changes one, re-pin and let the tests show what broke.

| Folder | Lane | Commit | Files | How they were produced |
| --- | --- | --- | --- | --- |
| `lane-a/` | CAREER-TWIN-A | `2d933fb13015d1e8282d8bae633c0a3d9b754d07` | `rank_result.json` (`a-match/0.1`), `jobs_query.json` and `job_record.json` (`a-ext/0.1` envelope) | Copied from `packages/data-matching/examples/` with `git show <sha>:<path>` |
| `lane-c/` | CAREER-TWIN-C | `3d114b56676c3eaaab3cd0e02a886adadf4f621c` | `atlas.pca.json`, `atlas.axes.json` (`atlas-projection/0.1`), `scenarios.json` (`scenario-outputs/0.1`) | Copied from `analytics/reports/for-lane-d/` with `git show <sha>:<path>` |
| `e-stub/` | CAREER-TWIN-E | `a2e50e339bec831c9bb3f814d38b52bfb535771e` | The artifact layout lane E proposed on Issue #5 (13 files) | `git archive <sha> integration contracts tools fixtures` into a temp folder, then `python -m ctint.stub <stage> --artifacts <dir> --seed 7 --count 24` for each stage; `pin.json` names the top-scored posting |

Lane B had not pushed example payloads when these were pinned (B at `9be8096117c218caf17ad67f58bd4614ae62fb0d`). Lane B's résumé-variant, assignment and analysis-report dialects are therefore tested against records written from B's published field list (`packages/experiments/README.md`), not against a real B file. That gap is stated in `docs/ADAPTERS.md` and on Issue #3.

`e-stub/` is mock data by lane E's own label (`E-REFERENCE-STUB`). Lane D's artifact source reports those files under `mock_sources`.

To re-pin (from the repository root, after `git fetch`):

```bash
git show origin/agent/ct-a-data-matching:packages/data-matching/examples/rank_result.json > apps/web/tests/upstream-samples/lane-a/rank_result.json
```
