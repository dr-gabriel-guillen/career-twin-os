# CareerTwin OS — mandatory instructions for every agent

## Identity and status
You have exactly one permanent session ID, one assigned GitHub Issue, and one owned branch.
Before coding: identify SESSION_ID, assigned Issue, branch name and HEAD SHA. Read the entire Issue (including comments), this document, and `docs/coordination/PROTOCOL.md`. Post `STARTED` on your Issue.

## Source of truth and ownership
The GitHub Issue is the source of execution requirements; `main` is the frozen shared baseline. Contracts in `contracts/` are frozen and can only change through an RFC Issue with acknowledgments from affected agents. Do not edit other agents' modules, rewrite their branches, modify their tests, or silently change API semantics.

| Session | Branch | Owned paths |
| --- | --- | --- |
| CAREER-TWIN-A | agent/ct-a-data-matching | packages/data-matching/, connectors/ |
| CAREER-TWIN-B | agent/ct-b-experiments | packages/applications/, packages/experiments/ |
| CAREER-TWIN-C | agent/ct-c-atlas | packages/atlas/, analytics/ |
| CAREER-TWIN-D | agent/ct-d-product-ux | apps/web/, packages/ui/ |
| CAREER-TWIN-E | agent/ct-e-integration | integration/, .github/, docs/coordination/ |

Do not merge or deploy. Draft PRs may target main, but owner approval is required to merge.

## Communication protocol
Post checkpoints on your own Issue with current SHA, completed work, tests and failures, interface changes, blockers and next step. Cross-agent requests use `NEEDS-INTERFACE`, `REVIEW-REQUEST`, `DELIVERED`, `ACKNOWLEDGED` and `BLOCKED`; link both Issues and PRs. Read cross-agent feedback at least twice per extended batch. Same-provider reviews do not count as independent external expert validation. Agent E integrates in temporary worktrees; E cannot approve owner policy or merge branches.

## Safety
Repository was found PUBLIC during setup. Work synthetic/offline only. No real profile details, CVs, contact lists, application history, private work/client data, secrets, LinkedIn exports, credentials or actual user identity. Never harvest LinkedIn or bypass terms, rate controls, CAPTCHAs or authentication. No auto-apply, outreach, recruiting messages, billable API calls or AWS deployments. All job fixtures must be explicitly fictional. No misleading accreditation, compensation or hiring probabilities.

## Definition of done
Runnable code + reproducible command, typed versioned interfaces, tests including negative cases and observed failures, no unreviewed severe defect, manual-approval gates, draft PR, and handoff with exact SHA. Continue unblocked work even when a dependency is blocked. Do not inflate test counts or invent evidence.
