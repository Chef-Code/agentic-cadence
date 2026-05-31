# Implementation Slices

Status: living document
Last updated: 2026-05-31
Baseline: released 0.1.3 tree

This document tracks the smallest implementation slices expected to move
Agentic Cadence from a governed protocol toolkit toward roughly 50% confidence
in constrained controlled operation with pre-approved unattended ticks.

Each slice should ship with tests, evidence, and updates to the living docs.

## Current Baseline After PR #51

The five 50% confidence slices below remain the work needed for a controlled
loop. Three smaller stabilization slices are now part of this baseline:

- Runtime-root safety guard: root-using CLI commands reject unignored
  repo-local runtime roots unless the operator explicitly allows them, while
  ignored repo-local runtime roots remain allowed.
- Readiness and freshness labels: repo snapshots and PR-readiness packets
  include `readiness_evidence`; snapshot validation enforces local snapshot
  evidence; saved PR JSON can be `saved_input` or `stale`; stale or
  future-dated saved PR evidence waits and recommends refresh before acting on
  blockers; negative max-age values are rejected; caller-asserted `live_like`
  evidence is not gated by saved-JSON age policy.
- Executor task trust-anchor validation: task-packet validation binds the
  embedded local repo snapshot to the packet `repo/cwd/branch/head` anchor and
  rejects missing repo identity, malformed snapshots, dirty snapshots,
  low-confidence snapshots, relative or unnormalizable cwd/path anchors, and
  mismatched snapshots before execution is approved.

These changes reduce state-awareness footguns. The current tree also includes
the Phase 1 read-only `loop-tick` command for the first slice and a generic
executor task/result contract, but it still does not add live GitHub
synchronization, branch/commit/push or PR creation, real executor invocation,
or automatic resume orchestration. Current unattended-operation confidence
remains 10%.

## Slice Status Key

- `Not started`: no implementation exists.
- `Partial`: some supporting primitives exist, but the slice is not complete.
- `In progress`: active PR or branch exists.
- `Complete`: implementation and validation evidence are merged.

## 1. Single-Tick Loop Orchestrator

Status: Partial

Goal: add one command that runs exactly one bounded loop cycle:

```text
snapshot
-> discover/elect candidate
-> check policy
-> start epoch
-> hand task to executor
-> collect result evidence
-> run/record validation
-> complete or fail epoch
-> decide stop, continue, PR, or handoff
```

Current evidence:

- repo snapshots exist;
- candidate discovery exists;
- epoch governance exists;
- self-check continuation rules exist;
- `loop-tick` captures and persists a snapshot, runs candidate discovery with
  election enabled, checks Cadence state, and emits `blocked`,
  `no_candidates`, `approval_required`, `requires_executor_contract`, or
  `approve_executor_task`;
- `loop-tick` is explicitly read-only and reports `executor_started: false`,
  `epoch_started: false`, and `pr_action_started: false`;
- no command yet starts an epoch, hands work to an executor, records validation,
  completes or fails the epoch, or chooses PR/handoff follow-up after execution.

Why it matters: this moves Cadence from advisor to controller without requiring
full autonomy.

Likely files:

- `codex_cadence/cli.py`
- `codex_cadence/candidates.py`
- `codex_cadence/epochs.py`
- `codex_cadence/model.py`
- `codex_cadence/store.py`
- tests
- examples

Risk: medium

Suggested implementation size: medium

Validation needed:

- success fixture;
- executor failure fixture;
- dirty worktree stops before execution: complete for Phase 1;
- stop brake prevents execution: complete for Phase 1;
- approval-required path: complete for Phase 1;
- active epoch conflict;
- stale snapshot rejection.

Codex implementation rule: Codex can implement this directly if it remains
generic, bounded, and does not push, merge, or release.

## 2. Generic Executor Adapter Contract

Status: Partial

Goal: define a generic contract for how Cadence asks an implementation executor
to perform a task and how that executor returns evidence.

Minimum task packet should include:

- task id;
- title and summary;
- repo identity and absolute repo path;
- embedded local snapshot with matching repo/cwd/branch/head;
- allowed paths;
- required checks;
- time/task limits;
- stop conditions;
- expected output evidence path;
- clean worktree and non-low repo confidence.

Minimum result evidence should include:

- executor id;
- start/end timestamps;
- status: `succeeded`, `failed`, `blocked`, or `stopped`;
- files changed;
- commands run;
- validation results;
- summary;
- confidence;
- blockers;
- dirty-worktree status;
- resulting head SHA for successful results.

Current evidence:

- adapter template exists;
- generic host-signal contract exists;
- adapter contract runner exists;
- `generic-executor-task.v1` and `generic-executor-result.v1` validation
  helpers exist;
- `loop-tick --emit-executor-task` can attach a generic executor task packet
  for operator approval without starting execution;
- `validate-executor-result` can validate local result evidence against the
  task packet;
- task-packet validation checks the embedded local repo snapshot, requires its
  repo/cwd/branch/head to match the packet repo anchor, and rejects missing
  repo identity, dirty, low-confidence, relative-path, or mismatched snapshots;
- successful result evidence must include command evidence, validation
  evidence, and a resulting head attestation;
- disabled commit, push, and PR-creation permissions reject common
  absolute-path, git/gh global-option, and shell-wrapper command forms;
- no real executor or named host adapter exists.

Why it matters: Cadence cannot implement work until execution is a formal,
bounded, inspectable boundary.

Likely files:

- `docs/adapters.md`
- `examples/adapter-template`
- `examples/adapter-contract-runner`
- `codex_cadence/model.py`
- `codex_cadence/cli.py`
- tests

Risk: medium

Suggested implementation size: medium

Validation needed:

- done: fake executor success, failure, and stopped/timeout-shaped evidence;
- done: blocked executor result evidence;
- done: malformed timestamp order;
- done: required-check command and validation enforcement for successful
  evidence;
- done: forbidden commit, push, PR creation, and head-change evidence
  rejection;
- done: malformed, missing-name, relative-path, dirty, low-confidence,
  branch/head-mismatched, or repo/cwd anchor-mismatched embedded task
  snapshots;
- done: disallowed changed path;
- done: dirty successful result rejection;
- remaining: real executor timeout behavior, external executor invocation,
  branch/commit handling, and epoch completion/failure integration.

Codex implementation rule: Codex can implement the generic contract directly.
Named host adapters require explicit operator approval.

## 3. Policy, Audit, And Stop Controls

Status: Partial

Goal: add repo/operator policy for unattended loop limits and an append-only
audit log for loop actions.

Policy should cover:

- allowed commands;
- allowed paths;
- denied paths;
- required checks;
- max runtime;
- max tasks per loop;
- max automatic continuations;
- branch naming;
- PR creation approval;
- review-spend approval;
- stop and handoff thresholds.

Audit records should cover:

- action id;
- timestamp;
- repo;
- branch/head;
- selected candidate;
- approval basis;
- command intent;
- executor evidence path;
- validation result;
- next decision;
- stop or handoff reason.

Current evidence:

- Cadence state/brake exists;
- epoch limits exist in code;
- handoff records exist;
- no complete policy file or loop audit log exists.

Why it matters: unattended confidence comes from bounded blast radius and
recoverable evidence.

Likely files:

- `codex_cadence/model.py`
- `codex_cadence/store.py`
- `codex_cadence/cli.py`
- tests
- docs

Risk: medium

Suggested implementation size: medium

Validation needed:

- policy allow/deny tests;
- denied command test;
- denied path test;
- stop brake during active loop;
- audit append ordering;
- audit replay summary;
- corrupted audit record handling.

Codex implementation rule: Codex can implement local policy and audit controls.
Destructive cleanup behavior or permissive default autonomy requires operator
approval.

## 4. Minimal Git/PR Automation

Status: Not started

Goal: create a dry-run-first path for turning successful task evidence into a
branch, commit, push, and pull request workflow.

Initial scope should prefer packets before side effects:

1. generate branch/commit/PR plan;
2. validate PR body against template;
3. require operator approval;
4. optionally run live `gh` commands after approval;
5. fetch saved PR evidence for existing readiness checks.

Current evidence:

- `pr-body-preflight` exists;
- `pr-readiness` exists for saved PR JSON;
- saved PR-readiness evidence is labeled as `saved_input`, `stale`, or
  caller-asserted `live_like`, with stale saved evidence waiting before
  blockers when an age policy is supplied;
- release dry-run follows operator-confirmation pattern;
- no branch, commit, push, or PR creation command exists.

Why it matters: the autonomous build loop needs to reach PR state before review
feedback can become useful loop input.

Likely files:

- `codex_cadence/cli.py`
- `codex_cadence/pr_readiness.py`
- scripts
- tests
- docs

Risk: medium to high

Suggested implementation size: medium to large

Validation needed:

- branch plan dry-run;
- PR body preflight success/failure;
- mocked `gh pr create`;
- mocked failed push;
- mocked pending/failing/passing checks;
- freshness labels preserved when saved PR evidence is reused.

Codex implementation rule: Codex can implement dry-run packets directly. Live
push or PR creation behavior requires operator approval.

## 5. CI/Review Feedback Back Into Candidate Discovery

Status: Partial

Goal: convert failing checks and unresolved actionable review feedback into
bounded next candidates.

Current evidence:

- candidate discovery can ingest saved review findings;
- candidate discovery can ingest saved GitHub review-thread files;
- PR readiness reports blockers;
- PR readiness labels stale saved PR state so it is not treated as merge-ready
  when an explicit age policy says it must be refreshed;
- no live sync or automatic response loop exists.

Why it matters: unattended operation fails quickly if Cadence cannot react to
CI failures or review comments.

Likely files:

- `codex_cadence/candidates.py`
- `codex_cadence/pr_readiness.py`
- `codex_cadence/repo_state.py`
- tests
- docs

Risk: medium

Suggested implementation size: medium

Validation needed:

- failing check becomes candidate;
- unresolved current review comment becomes candidate;
- resolved review comment is ignored;
- outdated review comment is ignored;
- non-actionable review summary is ignored;
- merge readiness remains blocked while actionable feedback exists.

Codex implementation rule: Codex can implement local ingestion directly. Live
GitHub synchronization or permission changes require operator approval.

## Expected Confidence Impact

The current confidence rating is 10%.

If all five slices are complete with evidence, expected confidence for
low-risk constrained operation with pre-approved unattended ticks is 45% to
55%.

This does not mean production-autonomous operation. It means a controlled loop
can run under policy, make bounded progress, stop safely, and leave an audit
trail.

## Slice Completion Checklist

Every implementation slice should update:

- tests or examples proving the behavior;
- `docs/progress-log.md`;
- `docs/autonomous-loop-readiness.md` if loop capability changed;
- `docs/roadmap.md` if priorities or confidence changed;
- `docs/decision-log.md` if architecture or governance choices changed.
