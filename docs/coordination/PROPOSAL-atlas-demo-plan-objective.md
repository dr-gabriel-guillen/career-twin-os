# Proposal (not applied): a demonstration-plan objective that does not overshoot the target role

Status: **proposal for lane C.** The projection mathematics and the simulator engine are unchanged. Per the owner's decision of 2026-11-13, the verified projection is preserved and nothing here is implemented.

## What was verified

On run `run-20261113T082245Z`, lane C's own engine reproduces the published coordinates exactly (projection `pca-dc3229688ed9cdf3-2d`). The drawn route equals the sum of the per-feature shifts, which lane E's `scenario_geometry` gate now checks. In the full feature space the four-step plan moves the candidate **closer** to Forward-Deployed Engineer (distance 2.253 → 2.071) and **away** from Frontend (3.090 → 3.315). About 90% of the movement lies outside the two drawn axes, which is why the 2-D endpoint looks near Frontend.

## The defect in the plan, not the projection

The demonstration plan picks features and raises each to a fixed level (0.80). Three of its four steps end above the FDE profile. Two of them (security, TypeScript) also *increase* the full-space distance, because the candidate already exceeded the profile before the step. The third (technical-discovery) still reduces the distance but overshoots by 0.27:

| Feature | Before | After (plan) | FDE profile |
|---|---|---|---|
| systems-integration | 0.00 | 0.80 | 0.88 |
| technical-discovery | 0.00 | 0.80 | 0.53 (overshoots) |
| security | 0.60 | 0.80 | 0.38 (overshoots) |
| typescript | 0.60 | 0.80 | 0.56 (overshoots) |

## Proposed objective

Choose steps that reduce the full-space distance to the target profile, never past it:

1. Take candidate features only where the candidate is below the target profile: `gap_i = target_i − x_i > 0`.
2. Raise each chosen feature to at most the profile: `after_i = min(x_i + step_i, target_i)`.
3. Rank steps by distance reduction per unit of effort; stop at the step budget.
4. Report the full-space distance before and after as the primary outcome, and the 2-D route as derived.

## Effect on the same run (analysis only, same four features, lane C's engine)

Capping each step at the profile gives a full-space distance to FDE of **2.008**, against 2.071 for the current plan and 2.253 before. Distance to Frontend becomes 3.236. The 2-D endpoint is (−0.452, 0.487); residual share outside the plane is 0.907. Choosing the features by gap, instead of keeping the current four, would reduce it further. The largest remaining gaps are customer-delivery (+0.57) and enterprise-sales (+0.34).

## Acceptance criteria for lane C, if adopted

- No step raises a feature above the target profile unless the user asks for it.
- The full-space distance to the target never increases.
- `scenario_geometry` still passes: the projection and route identities are unchanged.
- The UI keeps saying that the 2-D map is a projection and states the full-space movement.
