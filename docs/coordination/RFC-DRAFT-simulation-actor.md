# RFC draft (not filed, not applied): a `simulation` actor for application events

Status: **proposal only.** `contracts/` is frozen and unchanged. Per the owner's decision of 2026-11-13, simulated and user activity stay structurally separate without a contract change; this draft records what a later RFC could change, should the owner choose to open one.

## Problem

Lane B's experiment stage simulates approvals and submissions to exercise the approval gate. `contracts/application-event.schema.json` allows `actor` ∈ {`user`, `system`, `import`}, and `ct_applications` accepts an approval or a submission only from `user`. A simulated approval therefore has to say `actor: "user"`, so the record alone does not say that no person acted.

## What already keeps the streams apart (no contract change)

1. **Separate files.** Simulated events are written only to `experiment_events.json`. The application log is `events.json`. The web app loads them as two streams (`applicationEvents`, `labEvents`) and decides provenance by stream, never by id prefix or actor.
2. **Labelled source.** Simulated approvals and submissions carry `source: "simulation:experiment-approval"` / `"simulation:experiment-submission"` and a note saying they are simulated.
3. **Gates.** Lane E's `lab_boundary` fails a run if a simulated event shares an application id with the log or lacks a `simulation:` source. `crm_boundary` fails if the browser shows more applications than the log holds, or any simulated unit as an application.

## Proposed change (for a future RFC)

- Add `"simulation"` to the `actor` enum, version-bumped (`application-event` 0.1 → 0.2).
- `ct_applications`: an approval or submission with `actor: "simulation"` is valid only when written to the experiment stream, and is refused for the application log.
- Validators: the application log rejects `actor: "simulation"`; the experiment stream requires it for approvals and submissions.
- Readers of 0.1 data keep the stream-based separation, which stays the primary rule.

## Who must acknowledge

Lane B (writer), lane D (reader), lane E (validators and gates). Lane A and lane C do not read application events.

## Risks

- Every reader of `application-event` must accept 0.2, or the change breaks old consumers.
- The approval gate's "only the user approves" invariant gets an exception, which must stay confined to the experiment stream.
