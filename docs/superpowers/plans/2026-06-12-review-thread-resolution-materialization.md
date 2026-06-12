# Review Thread Resolution Materialization Plan

## Goal

Implement Task 34 from `docs/roadmaps/2026-06-12-tasks-33-37-roadmap.md`: operator-approved, exact-target review-thread resolution writes from a previously emitted `review-thread-resolution-plan.v1`.

## Constraints

- Require a target-bound HMAC approval token using `CADENCE_REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET`.
- Recheck saved PR anchors, head SHA, review-thread evidence completeness, target thread IDs, unresolved state, plan checksum, target checksum, and response materialization checksum immediately before GitHub writes.
- Resolve only the approved review thread IDs through a narrow GitHub mutation.
- Emit `review-thread-resolution-materialization.v1`.
- Append replayable `review_thread_resolution_intent` before writes and `review_thread_resolution_result` after success or started-write failure.
- Do not post comments, edit PR bodies, edit labels, invoke paid review, merge, release, publish packages, assign roles, schedule agents, or continue the loop.

## Implementation Steps

1. Add failing tests for direct materialization and CLI materialization:
   - happy path resolves exact mocked target and appends intent/result audit records.
   - missing or wrong approval blocks with no audit or GitHub writes.
   - stale evidence, head drift, target checksum drift, and already-resolved target block before writes.
   - mutation failure returns stable blockers and recovery evidence after the write boundary.
   - audit replay accepts new intent/result event types and rejects inconsistent action/status.
2. Add approval payload/token helpers and protocol constants.
3. Add structural validation and recheck helpers for `review-thread-resolution-plan.v1`.
4. Add materialization packet construction and GitHub GraphQL mutation execution.
5. Add CLI command `review-thread-resolution-materialize`.
6. Add policy audit builders and replay validation for `review_thread_resolution_intent` and `review_thread_resolution_result`.
7. Update protocol/progress docs as needed.
8. Run targeted tests, then the repo’s standard Python/protocol verification.

## Verification

- `python -m pytest tests/test_pr_readiness.py tests/test_audit_replay.py`
- Existing protocol/package checks used by this repo, discovered from project scripts/docs before final verification.
