# CareerTwin OS — current delivery board

**Maintained by CAREER-TWIN-E on branch `agent/ct-e-integration`.** This file changes on `main` only if the
owner merges PR #11. GitHub Issues are the live source; this board is a dated snapshot, and stale state here
must never override a newer comment.

**Snapshot:** 2026-11-11 (batch `CT-2026-11-10-B01`), consolidation: lanes A, B and C are archived, one agent
integrates. **10 of 10 stages PASS on real lane code inside the runner** at the pins in
[`integration/ACCEPTANCE.md`](../../integration/ACCEPTANCE.md), which holds the current matrix, security
repairs, launch command, merge order and owner decision gates. Lane hand-off of the batch:
[`integration/HANDOFF.md`](../../integration/HANDOFF.md).

| Session | Issue | Draft PR | Integrable by pinned SHA | Stages in the synthetic pipeline |
|---|---|---|---|---|
| CAREER-TWIN-A | [#2](https://github.com/dr-gabriel-guillen/career-twin-os/issues/2) | #7 | yes (manifest) | `jobs.import`, `profile.claims`, `match.rank`, `features.vectors` |
| CAREER-TWIN-B | [#3](https://github.com/dr-gabriel-guillen/career-twin-os/issues/3) | #8 | yes (manifest) | `resume.variant`, `application.record`, `experiment.run` |
| CAREER-TWIN-C | [#4](https://github.com/dr-gabriel-guillen/career-twin-os/issues/4) | #9 | yes (manifest) | `atlas.project`, `simulator.scenario` |
| CAREER-TWIN-D | [#5](https://github.com/dr-gabriel-guillen/career-twin-os/issues/5) | #10 | yes (manifest; needs `--allow-setup`) | `ui.e2e` |
| CAREER-TWIN-E | [#6](https://github.com/dr-gabriel-guillen/career-twin-os/issues/6) | #11 | n/a (verifier) | — |

Hub: [Issue #1](https://github.com/dr-gabriel-guillen/career-twin-os/issues/1). Lane A payload RFC: #12.

**Hard gates (unchanged):** repository PUBLIC; personal-data work prohibited until it is PRIVATE and the owner
approves. No merge, deploy, auto-apply, outreach or paid API. Draft PRs are not approval. An integration
verdict from lane E is not a release decision.
