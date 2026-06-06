# Agentic Cadence Business Memory

This file captures durable operator and maintainer signals for Agentic Cadence
discovery. Entries describe why a future Cadence slice matters before an
implementation plan exists. Cadence reads only clean tracked content from this
file.

Entry schema:
- Use `##` headings for business-memory entries only.
- Supported `Kind` values: `direction`, `business_rule`, `problem`, `feature`,
  `nice_to_have`, `risk`, `constraint`, and `unknown`.
- Optional `Status` values are `active`, `fulfilled`, or `superseded`.
  Fulfilled and superseded entries are retained for memory but no longer surface
  as discovery candidates.
- Use `Fulfilled By` or `Superseded By` to point at the PR, entry, or decision
  that closed the original memory signal. When `Status` is omitted, either
  closure field also closes the entry; otherwise legacy entries default to
  active.
- Use `Workflow`, `Time Saved`, `Risk`, `Pain`, `Signals`, and `Do not` fields.
- `Time Saved` and `Risk` values should be `high`, `medium`, or `low`.
- Keep entry field values and list items on one physical line; wrapped
  continuation lines are treated as malformed discovery input.
- Business-memory entries are discovery input only; they do not authorize
  implementation, paid review spending, commits, pushes, or merges outside
  normal Cadence governance.

## Agent-Neutral Public Identity

Status: fulfilled
Fulfilled By: PR #29
Kind: direction
Workflow: Public package readiness
Time Saved: high
Risk: medium
Pain: The project began as a Codex-focused protocol, but the public tool should make room for Codex, Claude, Gemini, and future coding agents without implying that the core model only works for one host.
Signals:
- The repository is named `agentic-cadence`.
- The primary public command should be `agentic-cadence`.
- Existing `codex-cadence`, `codex-transmission`, `codex_cadence.*`, and `transmission_control.*` names should remain compatibility aliases until a deliberate migration removes them.
Do not:
- Do not break existing users of the Codex-era command names in the public readiness pass.
- Do not rename import packages without a separate migration plan.

## Context Pressure Needs A Durable Seed Handoff Loop

Status: fulfilled
Fulfilled By: PR #15
Kind: feature
Workflow: Context-window shutdown and pickup
Time Saved: high
Risk: high
Pain: Long-running agent sessions need a repeatable way to package current state, publish a signed handoff, validate it, record clean-square, and stop the old session without losing the next action.
Signals:
- Existing primitives work: `status`, `snapshot-repo`, `create-handoff`, `validate-handoff`, `next-handoff`, and `clean-square`.
- `prepare-handoff` packages those primitives into a deterministic old-session shutdown loop.
- Automatic context-pressure detection still requires a host-provided signal.
Do not:
- Do not claim or complete the new handoff from the old session.
- Do not launch a new agent window from this command.
- Do not infer token pressure from repository contents or transcript guesses.
- Do not commit, push, create PRs, spend paid review, or merge as part of handoff preparation.
- Do not treat a generated seed as permission to bypass Cadence state, task sizing, pickup policy, or operator merge gates.

## PR Readiness Needs A Single Decision Packet

Status: fulfilled
Fulfilled By: PR #10
Kind: risk
Workflow: PR review and merge readiness
Time Saved: high
Risk: high
Pain: Operators need one deterministic readiness packet that summarizes check state, reviewer state, PR body compliance, blockers, and recommended next action before a merge decision.
Signals:
- Duplicate CI runs, skipped review jobs, template compliance, and unresolved actionable findings should be visible in one packet.
- Readiness evaluation should consume saved PR metadata and local files without calling GitHub or merging.
Do not:
- Do not auto-merge solely because checks are green.
- Do not treat duplicate check names as blockers without head-SHA and run-status context.
- Do not ignore unresolved actionable reviewer findings.
- Do not spend paid review unless the elected review guardrail allows it.

## Audit Replay Needs A Design Before Real Execution

Status: fulfilled
Fulfilled By: PR #54 design; audit-replay CLI implementation
Kind: risk
Workflow: Policy/audit safety before executor invocation
Time Saved: medium
Risk: high
Pain: Local audit writes are useful only if future sessions can replay audit history and distinguish corrupt records from unsupported future records before trusting execution evidence.
Signals:
- PR #53 added compact `cadence-audit.v1` loop-decision and executor-result-validation records.
- PR #54 added `docs/designs/2026-05-31-audit-replay-design.md` with the planned `audit-replay.v1` packet, blocker codes, count semantics, and required tests.
- The `audit-replay` command now emits a read-only `audit-replay.v1` packet for local `cadence-audit.v1` JSONL history, including corrupt-record and unsupported-record blockers.
Do not:
- Do not treat clean audit replay as approval to invoke a real executor.
- Do not treat missing replay evidence as approval to invoke a real executor.

## Controlled Executor Loop Needs Stable Gates

Status: fulfilled
Fulfilled By: PR #64 controlled fixture; PR #66 epoch closeout; PR #67 branch policy; PR #68 read-only GitHub evidence sync; PR #69 operator-approved Git/PR materialization; PR #70 resume verifier
Kind: risk
Workflow: Controlled executor loop governance
Time Saved: high
Risk: high
Pain: The roadmap's first execution path needed stable gates before real executor invocation could be considered.
Signals:
- `loop-tick --emit-executor-task` can produce bounded task packets, and PR #64 added `run-controlled-executor-fixture` to govern a fake external executor component in tests/examples.
- Task 3 current-tree work added `closeout-executor-result` so validated local executor evidence can mark a task complete while other epoch tasks remain, complete or fail terminal epochs, and emit continue, stop, handoff, validate-more-evidence, or dry-run Git/PR planning decisions.
- `validate-executor-result`, command policy, branch policy, active stop checks, controlled fixture invocation audit, epoch closeout audit, audit replay, read-only GitHub evidence sync, operator-approved `git-pr-materialize`, and read-only `verify-resume` now exist.
Do not:
- Do not invoke a real executor, auto-merge, release, or publish packages from this backlog entry.
- Do not make Cadence itself the product authority for implementation; it should govern a replaceable executor component.
- Do not treat fixture success as approval for named-host adapter support or unattended live repository writes.

## Governed Execution Needs A Start Gate

Status: fulfilled
Fulfilled By: Task 8 current-tree `start-governed-execution` implementation
Kind: risk
Workflow: Controlled executor loop governance
Time Saved: high
Risk: high
Pain: Cadence can emit bounded executor task packets and validate result evidence, but no command yet consumed an approved task packet, rechecked current repo/policy/brake state, started one active epoch, and handed off a task packet while still refusing to invoke a real executor.
Signals:
- Tasks 1-7 are complete through PR #70, including controlled fixture execution, local epoch closeout, branch policy, read-only GitHub evidence sync, operator-approved Git/PR materialization, and read-only resume verification.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` names Task 8 as the governed execution start gate.
- Task 8 current-tree work bridges `loop-tick --emit-executor-task` and active epoch start without launching a real executor or granting autonomous Git/PR authority.
Do not:
- Do not invoke a real executor, modify product code, create branches, commit, push, open PRs, auto-merge, release, or publish packages from this backlog entry.
- Do not treat a local ownership or epoch record as a distributed lock.
- Do not claim named-host adapter support before generic evidence and operator approval exist.

## Execution Run Evidence Needs Binding

Status: fulfilled
Fulfilled By: Task 9 current-tree `execution-run.v1` and supplied-run-record closeout implementation
Kind: risk
Workflow: Controlled executor loop governance
Time Saved: high
Risk: high
Pain: Cadence could start one approved active epoch and validate or close out local executor evidence, but it lacked a run ledger binding fixture invocation, result validation, and epoch closeout into one replayable local chain.
Signals:
- `start-governed-execution` emits `execution-start.v1` and appends `execution_start_decision` after an approved active epoch start.
- `run-controlled-executor-fixture`, `validate-executor-result`, and `closeout-executor-result` already write separate local evidence and audit records.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` names Task 9 as the run-evidence binding slice before real executor invocation is considered.
- Task 9 current-tree work adds local `execution-run.v1` records, supplied-run-record closeout validation, closeout status updates, and `execution_run_record` audit replay support.
Do not:
- Do not invoke a real executor, create branches, commit, push, open PRs, auto-merge, release, or publish packages from this backlog entry.
- Do not treat fixture success or a valid run ledger as approval for named-host adapter support.
- Do not add distributed locking or remote audit storage before explicit approval.

## Review Feedback Needs A Bounded Response Plan

Status: fulfilled
Fulfilled By: Task 10 current-tree `review-response-plan.v1` implementation
Kind: risk
Workflow: PR review and merge readiness
Time Saved: high
Risk: high
Pain: Cadence could sync saved GitHub evidence and evaluate PR readiness, but operators still needed a local packet that turns failed checks and unresolved actionable review threads into bounded next work without writing to GitHub.
Signals:
- `github-evidence-sync` can save PR JSON and review-thread evidence without mutating GitHub.
- `pr-readiness` can identify stale checks, failed checks, and unresolved actionable reviewer feedback.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` names Task 10 as the review feedback response plan slice.
- Task 10 current-tree work adds read-only `review-response-plan.v1` packets that consume saved PR JSON, saved review-thread JSON, and optional candidate discovery output.
Do not:
- Do not resolve review threads, post comments, update PR bodies, create branches, commit, push, merge, release, or publish packages from this backlog entry.
- Do not spend paid review or invoke review agents from the response-plan packet.
- Do not treat a response plan as authority to execute work without the existing executor task, approval, epoch, policy, and closeout gates.

## Resume Verification Needs An Execution Continuation Gate

Status: fulfilled
Fulfilled By: Task 11 current-tree `resume-continuation.v1` implementation
Kind: risk
Workflow: Context-window shutdown and pickup
Time Saved: high
Risk: high
Pain: Cadence could verify resume evidence and separately start governed execution, but it lacked a deterministic packet that binds a successful resume verification to a subsequent governed execution-start decision.
Signals:
- `verify-resume` can inspect claimed or ready handoffs without mutating them.
- `start-governed-execution` can consume an approved generic executor task and start one active epoch without invoking a real executor.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` names Task 11 as the resume-to-execution continuation gate.
- Task 11 current-tree work adds read-only `resume-continuation.v1` packets that recheck saved resume verifier anchors before recommending `start_governed_execution`.
Do not:
- Do not launch a new session, claim handoffs implicitly, invoke an executor, create branches, commit, push, open PRs, merge, release, or publish packages from this backlog entry.
- Do not treat a resume verification packet as fresh forever; continuation must recheck repo, handoff, brake, clean-square, policy, and active epoch state.
- Do not add distributed ownership or scheduler behavior before the local ownership slice exists.

## Local Work Ownership Needs A Registry

Status: fulfilled
Fulfilled By: Task 12 / PR #77
Kind: risk
Workflow: Multi-worker coordination
Time Saved: high
Risk: high
Pain: Cadence could start governed local work and resume handoffs, but it did not yet have a local record that showed which task, branch, PR, epoch, handoff, role, and claimer were associated before multiple workers are introduced.
Signals:
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` names Task 12 as the local work ownership registry.
- Task 11 current-tree work leaves ownership enforcement outside `resume-continuation`.
- Multi-worker coordination needs duplicate active ownership blockers before any agent pool, role assignment, or distributed scheduler exists.
- Task 12 current-tree work adds `work-ownership.v1`, `work-ownership-status.v1`, and `work-ownership-validation.v1` local packets.
Do not:
- Do not treat local ownership records as distributed locks.
- Do not add role assignment, agent pool scheduling, GitHub issue assignment, shared runtime, merge authority, release authority, or package-publication authority from this backlog entry.
- Do not mutate execution-start or resume-continuation gates in the ownership registry slice.

## Next Roadmap Needs Tasks 13-17

Status: active
Kind: direction
Workflow: Roadmap planning
Time Saved: high
Risk: medium
Pain: The Tasks 8-12 roadmap is complete in the current tree, so the project needs the next bounded roadmap before starting more implementation slices.
Signals:
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` is complete through Task 12.
- Task 12 leaves write-side ownership creation, role assignment, distributed locks, execution-start ownership enforcement, and resume-continuation ownership enforcement as future work.
- The session handoff points to roadmap creation as the next post-Task-12 action.
Do not:
- Do not start role assignment, agent pool scheduling, GitHub issue assignment, distributed locking, merge authority, release authority, or package-publication authority without a new roadmap.
- Do not mutate execution-start or resume-continuation gates from this planning backlog entry.
- Do not create branches, commits, PRs, merges, releases, or package publication from discovery alone.
