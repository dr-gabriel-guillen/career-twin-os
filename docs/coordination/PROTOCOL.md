# Cross-agent coordination protocol — v1

**Coordinator:** GitHub Issue labelled `COORDINATION` (or project coordinator Issue #1 if labels are unavailable).
**Permanent source of instructions:** one GitHub Issue for each lane. `main` stores baseline contracts; Issues record current batch and feedback. Agents have no direct communication channel outside the repository.

## Message conventions
Post comments in the assigned Issue or linked PR, with:
```
[CAREER-TWIN-<A|B|C|D|E>] [STATE] [BATCH_ID]
HEAD: <full SHA>
RELATES: <linked issue or PR>
REQUEST / FINDING: <specific reproducible behavior>
IMPACT: <API, contract, test, release>
EVIDENCE: <file + test output + minimal reproduction>
REQUIRED ACTION: <exact owner lane response>
```

Allowed STATE values: `STARTED`, `CHECKPOINT`, `NEEDS-INTERFACE`, `REVIEW-REQUEST`, `DELIVERED`, `ACKNOWLEDGED`, `BLOCKED`, `REJECTED`, `OWNER-DECISION`.

## Dependency flow
1. Agent A/B/C proposes API or schema change as `NEEDS-INTERFACE` in an RFC Issue with before/after examples. Tag all consuming sessions by ID.
2. Every affected lane replies `ACKNOWLEDGED` or `REJECTED` and reasons. ACK is *not* merge approval.
3. While waiting, the proposing agent continues independent tasks. No breaking shared change is applied unilaterally.
4. After delivery, link draft PR and reproducible tests in `DELIVERED`.
5. Agent E computes disposable integration branch/worktree from immutable SHAs, runs cross-lane contracts and browser tests and posts results, including missing modules/unknowns. E cannot silently fix other agents' files.
6. Owner alone decides merges, release, data ingestion, LinkedIn authorization and paid services.

## Handoff template
SESSION_ID, BATCH_ID, SOURCE_HEAD, BASE_SHA, changed paths, evidence hashes, tests pass/fail/skipped, blockers, regressions, links to cross-agent feedback, precise next work, no-data-exposure statement.

Do not report a green stub harness as a fully integrated product. No automated task polling is promised: agents must execute and actively read GitHub to exchange messages.
