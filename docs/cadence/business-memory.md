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

Status: active
Kind: risk
Workflow: Controlled executor loop governance
Time Saved: high
Risk: high
Pain: The roadmap's first execution path now has one controlled fixture component and local epoch closeout wiring, but real executor invocation remains blocked until branch policy, branch/PR materialization policy, live evidence sync, resume verification, and result evidence gates are stable.
Signals:
- `loop-tick --emit-executor-task` can produce bounded task packets, and PR #64 added `run-controlled-executor-fixture` to govern a fake external executor component in tests/examples.
- Task 3 current-tree work added `closeout-executor-result` so validated local executor evidence can complete or fail an active epoch and emit stop, handoff, validate-more-evidence, or dry-run Git/PR planning decisions.
- `validate-executor-result`, command policy, active stop checks, controlled fixture invocation audit, epoch closeout audit, and audit replay exist, but branch policy, live GitHub evidence sync, operator-approved Git/PR materialization, and resume verification are still missing.
- The next implementation slices should add branch policy, GitHub evidence sync, operator-approved Git/PR materialization, and resume verification before any named host adapter or live code-modifying executor is allowed.
Do not:
- Do not invoke a real executor, create branches, commit, push, open PRs, merge, release, or publish packages from this backlog entry.
- Do not make Cadence itself the product authority for implementation; it should govern a replaceable executor component.
- Do not treat fixture success as approval for named-host adapter support or unattended live repository writes.
