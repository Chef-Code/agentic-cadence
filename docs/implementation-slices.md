# Implementation Slices

Status: living document
Last updated: 2026-06-22
Baseline: released 0.1.3 plus unreleased audit-replay with local hash-chain integrity evidence, authenticated local operator approval identity evidence, policy/stop-control, git-pr-plan, controlled executor fixture, governed execution-start epoch gating, local execution-run evidence records, local executor epoch closeout, real-invocation closeout binding, controlled single-tick run packet evidence, `controlled-pr-cycle` evidence composition, read-only merge decision planning, read-only controlled loop-start composition, read-only controlled loop invocation-plan composition, read-only controlled loop real-invocation composition, read-only controlled closeout composition, read-only controlled loop-run summary evidence, read-only controlled loop outcome planning, read-only controlled loop run manifest planning, read-only controlled loop run manifest approval, read-only controlled loop runner planning, read-only controlled loop runner execution approval, read-only controlled loop runner dry-run evidence, read-only controlled loop runner start-readiness evidence, read-only controlled loop runner start-approval evidence, controlled-loop-runner-start evidence, read-only controlled loop runner next-stage evidence, read-only controlled loop runner stage-execution readiness evidence, read-only controlled loop runner stage-execution approval evidence, read-only controlled loop runner stage-invocation boundary evidence, controlled loop runner single-stage execution evidence, read-only controlled loop runner stage-closeout evidence, read-only controlled loop runner stage-outcome planning evidence, read-only controlled loop runner next-stage continuation evidence, read-only controlled loop runner stage-input binding evidence, continuation-aware controlled loop runner stage-execution readiness evidence, continuation-aware controlled loop runner stage-execution approval evidence with executor-task approval binding, continuation-aware controlled loop runner stage-invocation boundary evidence, read-only GitHub evidence sync, branch policy, operator-approved dirty-worktree local commit materialization, operator-approved Git/PR materialization, read-only resume verification, ownership-aware read-only resume continuation, read-only review-response planning, operator-approved review-response materialization, post-write PR evidence gate, read-only review-thread resolution planning, read-only role-readiness evidence, read-only executor-invocation-readiness and invocation-plan evidence, local work ownership claim/closeout evidence, Tasks 1-64 complete in the current implementation

This document tracks the smallest implementation slices expected to move
Agentic Cadence from a governed protocol toolkit toward roughly 50% confidence
in constrained controlled operation with pre-approved unattended ticks. That
50% target remains a Phase 1 single-agent path. The longer-term product
direction is GitHub-native orchestration for multiple cooperating agents.

Each slice should ship with tests, evidence, and updates to the living docs.

## Current Working Baseline

The five 50% confidence slices below remain the framework for a controlled
loop. Tasks 1-17 completed several local governance increments inside those
slices, but the controlled loop is still incomplete. Three smaller
stabilization slices are now part of this baseline:

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
executor task/result contract, plus read-only GitHub evidence sync,
operator-approved Git/PR materialization, read-only resume verification,
read-only review-response planning, local work ownership status, validation,
claim, closeout, and failure packets, a governed execution-start gate,
read-only real executor invocation planning, controlled one-command real
executor invocation evidence with real-invocation closeout binding, and a
controlled single-tick packet that composes saved local evidence after
closeout. It still
does not add autonomous live GitHub synchronization, automatic resume
orchestration, agent-role assignment,
agent-pool coordination, distributed ownership locks, or enforced review
separation.
The first local policy/audit controls can bound
emitted executor task packets, append decision/result-validation audit records,
append hash-chain metadata to new audit records, and replay local audit history
with a read-only `audit-replay.v1` packet that reports chain head, chained
record count, and explicit legacy roots. `verify-operator-approval` can verify
local `operator-approval.v1` identity evidence for a target checksum, purpose,
operator id, key id, expiration, and HMAC signature, then append
`operator_approval_verification` audit evidence without granting executor,
GitHub, merge, release, or package authority.
Active execution controls are partial: `start-governed-execution` can consume
an exactly approved generic executor task packet, recheck repo/policy/brake
state and supplied local ownership evidence, bind matching active ownership to
the started epoch, and start one active epoch while reporting
`executor_started: false`;
result validation enforces task-carried command policy and active brake stop
evidence; `run-controlled-executor-fixture` can govern a fake external executor
component in tests/examples and now writes local `execution-run.v1` records that
bind task, invocation, result, validation, and repo anchors; and
`closeout-executor-result --run-record-file` can reject mismatched or partial
run records before epoch mutation, update accepted records with closeout
status, complete or fail terminal epochs, and emit the next dry-run decision.
`closeout-executor-result --real-invocation-file` can bind accepted real
invocation evidence to result validation, active ownership revalidation, epoch
closeout, and dry-run Git/PR planning without GitHub writes.
`git-pr-plan` can produce
a dry-run Git/PR transition plan for separate review,
`git-pr-dirty-materialization-plan` can bind closeout-approved dirty-worktree
materialized-change evidence to a reviewed commit/PR materialization input
without staging or committing, `git-pr-dirty-commit-materialize` can turn that
approved dirty plan into one local branch commit after exact target-bound
operator approval, clean/process filter safeguards, rollback-aware writes, and
rechecks, and `git-pr-materialize` can create a branch from the
already-materialized current commit without switching the checkout, push it with
Git hook verification disabled for that push, and create/update a PR only after
exact target-bound operator approval and local rechecks.
`verify-resume`
can check claimed handoff state, clean-square evidence, repo branch/head,
dirty-worktree state, active brake, active epoch state, and pickup-policy
evidence before a fresh session continues. `review-response-plan` can turn
saved PR JSON, saved review-thread JSON, optional candidate discovery output,
and PR-body evidence into bounded read-only follow-up recommendations without
writing to GitHub. `work-ownership-status` and `validate-work-ownership` can
read local `work-ownership.v1` records, surface active/closed/failed ownership
evidence, reject malformed or stale scoped records, block invalid registry and
repo-inspection evidence, and block duplicate active ownership for the same
repo, branch, and task. Validation additionally rejects closed or
repo-mismatched target records. `claim-work-ownership`,
`close-work-ownership`, and `fail-work-ownership` can create or move local
ownership records after branch, `HEAD`, dirty-worktree, duplicate/stale
ownership, malformed-record, and registry path-safety rechecks, then append
replayable `work_ownership_mutation` audit evidence.
`complete-work-ownership-from-closeout` can close active ownership only after
valid completed `executor_epoch_closeout` evidence, a supplied closeout
checksum, saved task checksum,
task/candidate/role/claimer/repo/branch/`HEAD`/epoch anchors, and audit append
all match, while failed executor closeout still requires the explicit
`fail-work-ownership` path. Execution-start can bind
matching active ownership records and append ownership checksums to
`execution_start_decision` audit evidence. Resume-continuation can recheck
supplied active ownership evidence for the resumed handoff/task, role, claimer,
repo, branch, and `HEAD` before recommending governed execution start.
`role-readiness` can consume `role-policy.v1`, local ownership records, saved
PR JSON, and saved review-thread evidence to verify allowed ownership roles and
builder/reviewer separation without GitHub writes. Controlled real executor
invocation now exists for one approved local command with
`real-executor-invocation.v1` evidence, closeout can bind accepted real
invocation records to epoch decisions and dry-run Git/PR planning, and
`controlled-loop-tick` can compose the saved loop/task/start/readiness/plan/
invocation/result/snapshot/closeout chain into `controlled-loop-tick.v1` with
success-only `controlled_loop_tick` audit evidence. `controlled-pr-cycle` can
compose saved controlled-loop, approved Git/PR materialization, post-write
gate, optional review-response materialization, optional review-thread
resolution materialization, and final post-write gate evidence into
`controlled-pr-cycle.v1` with success-only `controlled_pr_cycle` audit
evidence. `merge-decision-plan` can compose saved PR JSON, review-thread JSON,
PR-readiness, audit-replay, `controlled-pr-cycle` evidence, and optional
role-readiness evidence into a read-only `merge-decision-plan.v1` packet that
requires operator confirmation while reporting no Git, GitHub, merge, release,
package, role-assignment, scheduler, or loop-continuation side effects.
`loop-run-plan` can wrap the current read-only `loop-tick` decision into a
`loop-run-plan.v1` packet with planned next steps and explicit non-start flags
for the runner, executor, epoch, PR actions, GitHub writes, and merge.
`controlled-loop-start` can compose a saved loop-run plan with approved
`execution-start.v1` evidence, recheck the executor task checksum and
execution-start task anchor, and recommend executor-invocation planning without
starting a runner or executor. `controlled-loop-invocation-plan` can compose
that controlled start with saved executor-invocation readiness and invocation
plan evidence, recheck task, epoch, readiness, and target checksum anchors, and
recommend `invoke_real_executor` without starting or retrying an executor.
`controlled-loop-closeout` can compose the saved controlled real-invocation
packet with accepted closeout evidence, recheck pre/post invocation checksums,
terminal closeout status, audit anchors, and epoch closeout checksum, and
recommend `controlled_loop_tick` without closing epochs, rewriting records, or
continuing the loop. `controlled-loop-run-summary` can summarize the saved
`loop-run-plan` through `controlled-loop-tick` packet chain, rechecking
schemas, completed statuses, file anchors, and checksums before recommending
`review_controlled_loop_run` on success or
`inspect_controlled_loop_run_blockers` when blocked, without appending audit or
continuing the loop. `controlled-loop-outcome-plan` can compose the saved
controlled run summary, controlled closeout, and controlled tick into a
read-only terminal next-action packet that recommends bounded follow-up actions
such as `run_git_pr_plan`, `request_git_pr_materialization_approval`, or
`inspect_git_pr_plan_blockers` without starting a runner, retrying an executor,
continuing the loop, or writing Git/GitHub state.
`controlled-loop-run-manifest-plan` can compose the saved terminal controlled
run evidence and outcome plan into a read-only manifest that binds evidence
file paths, checksums, and controlled one-cycle command stages. It appends no
audit evidence and does not start a runner or executor, retry an executor,
continue a loop, start or close an epoch, execute Git commands, call GitHub,
create branches, commit, push, create PRs, merge, release, publish packages,
assign roles, or schedule agents.
`controlled-loop-run-manifest-approval` can verify a target-bound
`operator-approval.v1` for the saved manifest checksum and purpose
`controlled_loop_run_manifest`, emitting read-only approval evidence while
granting no runner, executor, continuation, Git/GitHub, merge, release,
publication, role, or scheduling authority. `controlled-loop-runner-plan` can
compose the approved manifest and approval evidence into a dry-run runner plan
that rechecks manifest, approval, and operator-approval-file checksums while
still starting no runner or executor and granting no continuation or write
authority. `controlled-loop-runner-execution-approval` can verify a
target-bound operator approval for that runner plan while still granting no
runner-start, executor, continuation, or write authority.
`controlled-loop-runner-dry-run` can consume the approved runner plan and
runner execution approval, recheck checksums, anchors, and operator approval,
and emit would-process command-stage evidence while still starting no runner or
executor and granting no retry, continuation, Git/GitHub, merge, release,
publication, role, or scheduling authority.
`controlled-loop-runner-start-readiness` can then consume the completed dry-run
packet, recheck dry-run anchors and the supplied runner-plan and approval
checksums, and emit readiness-only evidence before any future runner start while
still granting no runner-start, executor, continuation, Git/GitHub, merge,
release, publication, role, or scheduling authority.
`controlled-loop-runner-start-approval` can then verify a target-bound operator
approval for the start-readiness packet while still starting no runner or
executor and granting no continuation, Git/GitHub, merge, release, publication,
role, or scheduling authority.
`controlled-loop-runner-start` can then consume the approved start packet and
saved upstream evidence, recheck anchors/checksums/stage sequences, and record
the bounded one-cycle runner-start boundary with audit evidence while still
starting no executor, retrying no executor, continuing no loop, writing no
Git/GitHub state, merging nothing, releasing nothing, publishing no packages,
assigning no roles, and scheduling no agents.
`controlled-loop-runner-next-stage` can then consume the recorded runner-start
boundary plus saved runner-plan and dry-run evidence, recheck anchors,
checksums, and stage sequences, and select the first runner command stage
without executing it, without appending audit evidence, without invoking an
executor, without continuing the loop, and without writing Git/GitHub state.
`controlled-loop-runner-stage-execution-readiness` can then consume either the
selected initial next-stage packet or a continuation next-stage packet plus
matching stage-input binding evidence and a reviewed binding checksum, recheck
the upstream chain and selected stage, and prepare a deterministic
stage-execution approval target without executing the stage.
`controlled-loop-runner-stage-execution-approval` can then consume that
readiness target plus the saved upstream runner packets and a target-bound
`operator-approval.v1`, recheck the full chain, verify the approval signature,
and mark the selected stage as approved without executing it, appending audit
evidence, invoking an executor, continuing the loop, or writing Git/GitHub
state.
`controlled-loop-runner-stage-invocation-boundary` can then consume the
approved stage plus saved readiness, next-stage, runner-start, runner-plan,
dry-run, and operator-approval evidence, recheck the full chain, re-verify the
operator-approval signature and expected operator, bind the exact argv, cwd,
evidence-output, timeout, execution-authority, and side-effect policies, and
stop before process start.
`controlled-loop-runner-stage-execute` can then execute exactly one approved
stage command with `shell=False` and terminal command evidence,
`controlled-loop-runner-stage-closeout` can consume the saved execution,
stage-output artifact, boundary, approval, readiness, and upstream runner
evidence, recheck all anchors and output evidence, and classify the stage as
completed, failed, or blocked, `controlled-loop-runner-stage-outcome-plan` can
emit the next operator target, and
`controlled-loop-runner-next-stage-continuation` can select the exact N+1
runner-plan stage without executing it, and
`controlled-loop-runner-stage-input-binding` plus continuation-aware readiness
can bind the selected continuation stage to prior `loop-run-plan` output before
preparing a deterministic stage-execution approval target. Continuation-aware
stage-execution approval can now verify both the readiness approval and the
required `start_governed_execution` executor-task approval without executing the
stage. Continuation invocation-boundary preparation can now consume that
approval, reread the executor task binding, and emit the exact
`start-governed-execution` argv without starting a process. Continuation
execution, closeout, outcome planning, autonomous
branch/commit/push or PR creation, automatic session launch, distributed work
ownership, role assignment, and continuous loop orchestration remain missing.
Current unattended-operation confidence is 25%. Progress-log entries record
Task 64 projected capability at 59% while this stable headline remains 25%.

Tasks 1-64 are complete through the controlled-loop runner continuation
stage-invocation boundary slice. The current roadmap is
`docs/roadmaps/2026-06-20-tasks-61-66-roadmap.md`; Task 65 starts with one
approved continuation-stage execution while still avoiding retries, autonomous
loop continuation, and Git/GitHub writes.

Historical roadmap anchors remain part of the current context: Tasks 1-7 from
`docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`, Tasks 8-12 from
`docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`, Tasks 13-17 from
`docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`, Tasks 18-22 from
`docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`, Tasks 23-27 from
`docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`, Tasks 28-32 from
`docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md`, Tasks 33-37 from
`docs/roadmaps/2026-06-12-tasks-33-37-roadmap.md`, and the Task 55-60 roadmap
at `docs/roadmaps/2026-06-18-tasks-55-60-roadmap.md`. Task 22 added real executor run closeout binding in `main` via PR #90. Task 23 added controlled single-tick run packet evidence in `main` via PR #92. Task 38 started
the runner-adjacent chain with a read-only `loop-run-plan` packet rather than
autonomous execution authority. Task 39 adds read-only `controlled-loop-start`
evidence for the saved loop-plan plus
approved execution-start boundary. Task 40 adds read-only
`controlled-loop-invocation-plan` evidence for the controlled start plus
executor-invocation readiness and invocation-plan boundary. Task 41 adds
read-only `controlled-loop-real-invocation` evidence for the recorded real
invocation before closeout. Task 42 adds read-only
`controlled-loop-closeout` evidence for accepted closeout before the aggregate
controlled tick. Task 43 adds read-only `controlled-loop-run-summary` evidence
for the saved runner-adjacent controlled packet chain after the aggregate tick.
Task 44 adds read-only `controlled-loop-outcome-plan` evidence for choosing the
next bounded operator action from a completed controlled run outcome. Task 45
adds read-only `controlled-loop-run-manifest-plan` evidence that binds terminal
controlled run evidence files and command stages for operator review before any
future one-cycle runner consumes them. Task 46 adds read-only
`controlled-loop-run-manifest-approval` evidence that verifies a target-bound
operator approval for the saved manifest before any future dry-run runner reads
it. Task 47 adds read-only `controlled-loop-runner-plan` evidence that composes
that approved manifest into a dry-run runner plan before any future execution
approval or runner start. Task 48 adds read-only
`controlled-loop-runner-execution-approval` evidence that verifies a
target-bound operator approval for the saved runner plan before any future
runner start. Task 49 adds read-only `controlled-loop-runner-dry-run` evidence
that consumes the approved runner plan and execution approval and emits
would-process command stages without starting a runner.
Task 50 adds read-only `controlled-loop-runner-start-readiness` evidence that
validates the completed dry-run packet, revalidates the supplied runner plan
and execution approval packets, rechecks anchors and checksums, verifies the
dry-run stage sequence still matches the approved runner plan, and stops before
any runner start.
Task 51 adds read-only `controlled-loop-runner-start-approval` evidence that
verifies a target-bound operator approval for the completed start-readiness
packet before any future runner start.
Task 52 adds controlled `controlled-loop-runner-start` evidence that consumes
the completed start approval plus saved readiness, dry-run, runner-plan, and
execution-approval packets, rechecks anchors and stage sequences, records the
approved one-cycle runner-start boundary, and stops before executor invocation
or loop continuation.
Task 53 adds read-only `controlled-loop-runner-next-stage` evidence that
consumes the completed controlled runner-start boundary plus saved runner-plan
and dry-run evidence, rechecks anchors and stage sequences, selects the first
runner command stage, and stops before stage execution.
Task 54 adds read-only `controlled-loop-runner-stage-execution-readiness`
evidence that consumes the completed next-stage packet plus saved runner-start,
runner-plan, and dry-run evidence, rechecks the upstream chain, prepares a
deterministic stage-execution approval target for stage 1, and stops before
stage execution.
Task 55 adds read-only `controlled-loop-runner-stage-execution-approval`
evidence that consumes the completed readiness packet plus saved upstream
runner packets and `operator-approval.v1`, rechecks the full chain, verifies
the approval purpose, target checksum, identity, and signature, and stops
before stage execution.
Task 56 adds read-only
`controlled-loop-runner-stage-invocation-boundary` evidence that consumes the
completed approval packet plus saved upstream runner packets, rechecks the
full chain and selected stage, re-verifies the saved operator approval, emits
exact argv/cwd/output/timeout boundary evidence, and stops before process
start.
Task 57 adds controlled `controlled-loop-runner-stage-execute` evidence that
consumes the invocation boundary plus saved upstream runner packets, rechecks
the full chain, saved operator approval, reviewed invocation-boundary checksum,
and exact argv/cwd/output/timeout boundary, executes exactly one approved stage
command with `shell=False`, captures terminal command evidence, and stops
without invoking an executor, retrying, executing a second stage, continuing
the loop, or writing Git/GitHub state.
Task 58 adds read-only `controlled-loop-runner-stage-closeout` evidence that
consumes saved stage-execution, invocation-boundary, approval, readiness,
next-stage, runner-start, runner-plan, dry-run, and stage-output evidence,
rechecks the full chain and saved operator approval, binds the approved output
file to captured stdout, classifies the stage as completed, failed, or
blocked, and stops before outcome planning, retry, continuation, executor
invocation, or Git/GitHub writes.
Task 59 adds read-only `controlled-loop-runner-stage-outcome-plan` evidence
that consumes saved stage-closeout plus the upstream runner chain, rechecks
closeout, execution, invocation-boundary, approval, readiness, next-stage,
runner-start, runner-plan, and dry-run anchors, and emits only a deterministic
operator target for next-stage continuation selection, runner completion, or
inspection/future operator-gated retry planning without selecting a stage,
retrying, continuing, appending audit evidence, or writing Git/GitHub state.
Task 60 adds read-only `controlled-loop-runner-next-stage-continuation`
evidence that consumes the reviewed stage-outcome plan plus saved closeout,
execution, runner-start, runner-plan, and dry-run evidence, selects exactly
stage N+1 from the approved runner plan, and emits no stage-execution readiness
target while stopping before execution, retry, loop continuation, audit append,
or Git/GitHub writes.
Task 61 adds read-only `controlled-loop-runner-stage-input-binding` evidence
that consumes the selected continuation packet, completed prior
`loop-run-plan` stage output, and an exact executor task file, resolves the
real `loop-run-plan.v1` output identity that has no top-level `valid` field,
and binds the stage-2 `start-governed-execution` input checksum before any
continuation readiness, approval-token generation, process start, epoch start,
loop continuation, audit append, or Git/GitHub writes.
Task 62 generalizes read-only
`controlled-loop-runner-stage-execution-readiness` so it accepts exactly one
selection source: the existing initial next-stage packet or a continuation
next-stage packet plus a matching stage-input binding packet and reviewed
stage-input binding checksum. Continuation-backed readiness emits
`stage_selection_source: continuation`, binds continuation and input-binding
identity into the deterministic approval target, preserves the initial
stage-1 path, and still stops before approval, process start, epoch start,
loop continuation, audit append, or Git/GitHub writes.

## Vision Framing

The existing slices are still the right foundation. Task election becomes work
ownership; epochs bound each agent's effort; executor packets become role
handoff contracts; validation gates and PR readiness become merge evidence; and
review findings become follow-up candidates.

Future work should avoid assuming there is only one agent. Even when a slice
uses one implementation agent, packet fields, audit records, branch policy,
handoff records, and review gates should remain compatible with a future
orchestrator that can coordinate Planning, Architecture, Builder, Reviewer, QA,
Documentation, Release, and Handoff agents.

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
- `loop-tick --policy-file` can apply initial local path/check/runtime/stop
  bounds before emitting an executor task, keeps built-in and policy stop
  conditions when CLI stop conditions are added, and can return
  `policy_denied`;
- root-backed `loop-tick` packets append compact `cadence-audit.v1` decision
  records;
- `verify-operator-approval` verifies `operator-approval.v1` packets for
  target checksum, purpose, operator id, key id, expiration, and HMAC signature,
  emits `operator-approval-verification.v1`, appends
  `operator_approval_verification`, and reports no executor, epoch, PR, merge,
  release, or package side effects;
- `start-governed-execution` can consume a reviewed `generic-executor-task.v1`
  packet with an exact checksum approval token, recheck current repo path,
  branch, `HEAD`, dirty-worktree state, task-carried command and branch policy,
  active brake, active epoch state, and supplied local ownership evidence, then
  start one active epoch, bind matching active ownership to that epoch, and
  emit `execution-start.v1` with `executor_started: false`;
- `resume-continuation` can consume supplied active local ownership evidence
  after existing saved/fresh resume blockers pass, then recommend
  `start_governed_execution`, `claim_work_ownership`,
  `refresh_ownership_evidence`, `close_or_fail_active_ownership`, or
  `inspect_resume_blockers` without mutating runtime state;
- `role-readiness` emits a read-only `role-readiness.v1` packet from local
  role policy, ownership, saved PR JSON, and saved review-thread evidence,
  blocking missing policy, unknown roles, stale ownership, missing builder or
  reviewer evidence, and same-claimer review conflicts;
- `closeout-executor-result` can consume local task/result/snapshot-after
  packets, mark a successful task complete while the epoch remains active when
  other tasks remain, complete or fail terminal epochs, append closeout audit,
  and choose continue, stop, handoff, validate-more-evidence, or dry-run Git/PR
  planning as the next decision;
- controlled real executor invocation and real-invocation closeout binding now
  exist;
- `controlled-loop-tick` reads saved `loop-tick`, task, execution-start,
  readiness, invocation-plan, real-invocation, result, snapshot-after,
  closeout, and optional dry-run Git/PR plan files, rechecks their path and
  checksum anchors, emits `controlled-loop-tick.v1`, and appends
  `controlled_loop_tick` audit evidence only after a completed composition;
- `controlled-loop-start` reads saved `loop-run-plan.v1` and
  `execution-start.v1` files, rechecks packet schemas, the planned executor
  task checksum, execution-start task id/checksum anchors, the local active
  epoch, and start audit record, then recommends executor-invocation planning
  without starting a runner, executor, or loop continuation;
- `controlled-loop-invocation-plan` reads saved `controlled-loop-start.v1`,
  `executor-invocation-readiness.v1`, and `executor-invocation-plan.v1`
  files, rechecks task, epoch, readiness, and target checksum anchors, then
  recommends `invoke_real_executor` without starting a runner, executor, or
  loop continuation;
- `controlled-loop-real-invocation` reads a saved
  `controlled-loop-invocation-plan.v1` packet and saved
  `real-executor-invocation.v1` record, rechecks the invocation-plan checksum,
  target checksum, plan-file anchor, record-file anchor, invocation audit
  record, result path/checksum, invocation id, and pending closeout status,
  then recommends
  `closeout_executor_result` without starting or retrying an executor,
  appending audit, or continuing the loop;
- `controlled-loop-closeout` reads a saved
  `controlled-loop-real-invocation.v1` packet and saved
  `executor-epoch-closeout.v1` evidence, rechecks the pre-closeout invocation
  checksum, closeout-bound path/id, terminal closeout status, updated
  invocation checksum, closeout audit, real-invocation closeout-update audit,
  and epoch closeout checksum, then recommends `controlled_loop_tick` without
  closing epochs, rewriting records, appending audit, or continuing the loop;
- `controlled-loop-run-summary` reads saved `loop-run-plan.v1`,
  `controlled-loop-start.v1`, `controlled-loop-invocation-plan.v1`,
  `controlled-loop-real-invocation.v1`, `controlled-loop-closeout.v1`, and
  `controlled-loop-tick.v1` packets, rechecks the runner-adjacent checksum
  chain, and recommends `review_controlled_loop_run` without appending audit,
  starting work, retrying executors, or continuing the loop;
- `controlled-loop-outcome-plan` reads saved
  `controlled-loop-run-summary.v1`, `controlled-loop-closeout.v1`, and
  `controlled-loop-tick.v1` packets, rechecks terminal checksums, file anchors,
  task, epoch, closeout status, and source decision, then recommends the next
  bounded operator action without appending audit, starting work, retrying
  executors, continuing the loop, or writing Git/GitHub state;
- `controlled-loop-run-manifest-plan` reads saved terminal controlled run
  evidence and the saved outcome plan, rechecks outcome-plan file/checksum
  anchors, and records a reviewable evidence-file manifest plus controlled
  one-cycle command-stage sequence without appending audit evidence, starting a
  runner or executor, retrying executors, continuing the loop, starting or
  closing an epoch, executing Git commands, calling GitHub, creating branches,
  committing, pushing, creating PRs, merging, releasing, publishing packages,
  assigning roles, or scheduling agents;
- `controlled-loop-run-manifest-approval` reads a saved completed manifest plan
  and a target-bound operator approval, verifies purpose
  `controlled_loop_run_manifest`, and emits read-only approval evidence without
  appending audit evidence, starting a runner or executor, retrying executors,
  continuing the loop, starting or closing an epoch, executing Git commands,
  calling GitHub, creating branches, committing, pushing, creating PRs, merging,
  releasing, publishing packages, assigning roles, or scheduling agents;
- `controlled-loop-runner-plan` reads a saved completed manifest plan and the
  saved manifest approval evidence, rechecks manifest and approval checksums,
  rereads the saved operator approval file, and emits a dry-run runner plan
  with every planned command stage marked `not_started`, without appending
  audit evidence, starting a runner or executor, retrying executors, continuing
  the loop, starting or closing an epoch, executing Git commands, calling
  GitHub, creating branches, committing, pushing, creating PRs, merging,
  releasing, publishing packages, assigning roles, or scheduling agents;
- `controlled-loop-runner-execution-approval` reads a saved completed runner
  plan and a target-bound operator approval, verifies purpose
  `controlled_loop_runner_execution`, and emits read-only approval evidence
  without appending audit evidence, starting a runner or executor, retrying
  executors, continuing the loop, starting or closing an epoch, executing Git
  commands, calling GitHub, creating branches, committing, pushing, creating
  PRs, merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- `controlled-loop-runner-dry-run` reads a saved completed runner plan and
  saved completed runner execution approval, rechecks plan and approval
  checksums, file anchors, operator approval checksum, and operator approval
  signature, then emits every approved command stage as `would_process` without
  appending audit evidence, starting a runner or executor, retrying executors,
  continuing the loop, starting or closing an epoch, executing Git commands,
  calling GitHub, creating branches, committing, pushing, creating PRs,
  merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- `controlled-loop-runner-start-readiness` reads a saved completed dry-run,
  runner plan, and execution approval, rechecks anchors and stage sequence, and
  emits readiness-only evidence without appending audit evidence, starting a
  runner or executor, retrying executors, continuing the loop, starting or
  closing an epoch, executing Git commands, calling GitHub, creating branches,
  committing, pushing, creating PRs, merging, releasing, publishing packages,
  assigning roles, or scheduling agents;
- `controlled-loop-runner-start-approval` reads saved completed start-readiness
  evidence and a target-bound operator approval, verifies purpose
  `controlled_loop_runner_start`, and emits approval-only evidence without
  appending audit evidence, starting a runner or executor, retrying executors,
  continuing the loop, starting or closing an epoch, executing Git commands,
  calling GitHub, creating branches, committing, pushing, creating PRs,
  merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- `controlled-loop-runner-start` reads saved completed start-approval,
  start-readiness, dry-run, runner-plan, and execution-approval evidence,
  rechecks anchors, checksums, approval identity, and stage sequences, then
  appends one `controlled_loop_runner_start` audit record for the bounded
  one-cycle runner-start boundary without starting an executor, retrying
  executors, continuing the loop, starting or closing an epoch, executing Git
  commands, calling GitHub, creating branches, committing, pushing, creating
  PRs, merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- `controlled-loop-runner-next-stage` reads saved completed runner-start,
  runner-plan, and dry-run evidence, rechecks anchors, checksums, and stage
  sequence, then selects the first runner stage without appending audit
  evidence, executing a runner stage, invoking an executor, retrying executors,
  continuing the loop, executing Git commands, calling GitHub, creating
  branches, committing, pushing, creating PRs, merging, releasing, publishing
  packages, assigning roles, or scheduling agents;
- `controlled-loop-runner-stage-execution-readiness` reads saved completed
  initial next-stage evidence or continuation next-stage plus stage-input
  binding evidence, runner-start, runner-plan, and dry-run evidence, rechecks
  the upstream chain and selected stage, then prepares a deterministic
  stage-execution approval target without appending audit evidence, executing a
  runner stage, invoking an executor, retrying executors, continuing the loop,
  executing Git commands, calling GitHub, creating branches, committing,
  pushing, creating PRs, merging, releasing, publishing packages, assigning
  roles, or scheduling agents;
- `controlled-loop-runner-stage-execution-approval` reads saved completed
  stage-execution readiness, either initial next-stage evidence or
  continuation next-stage plus stage-input binding evidence, runner-start,
  runner-plan, dry-run, and operator approval evidence, rechecks the full runner
  chain, verifies the approval purpose, expected operator id, and target
  checksum with approval-secret-backed signature validation, requires the
  command-specific executor-task approval and current executor-task file
  checksum binding for continuation `start-governed-execution`, then records
  approval-only evidence without appending audit evidence, executing a runner
  stage, invoking an executor, retrying
  executors, continuing the loop, executing Git commands, calling GitHub,
  creating branches, committing, pushing, creating PRs, merging, releasing,
  publishing packages, assigning roles, or scheduling agents;
- `controlled-loop-runner-stage-invocation-boundary` reads saved completed
  stage-execution approval, stage-execution readiness, next-stage,
  runner-start, runner-plan, and dry-run evidence, rechecks the full runner
  chain, rereads and verifies the saved operator approval file, requires the
  approved selected stage to match the requested stage and runner-plan command,
  then emits exact argv, cwd, output, timeout, execution-authority,
  side-effect policy, and invocation-boundary checksum evidence without
  appending audit evidence, starting a process, executing a runner stage,
  invoking an executor, retrying executors, continuing the loop, executing Git
  commands, calling GitHub, creating branches, committing, pushing, creating
  PRs, merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- `controlled-loop-runner-stage-execute` reads saved completed invocation
  boundary, stage-execution approval, stage-execution readiness, next-stage,
  runner-start, runner-plan, and dry-run evidence, rechecks the full runner
  chain plus saved operator approval signature, reviewed boundary checksum,
  and exact argv/cwd/output/timeout boundary, invokes exactly one approved
  internal Cadence stage command with `shell=False`, captures stdout, stderr,
  exit code, output-file evidence, timestamps, and command result checksum,
  appends at most one execution audit record after process start, and still
  does not invoke an executor, retry executors, execute a second stage,
  continue the loop, execute Git commands, call GitHub, create branches,
  commit, push, create PRs, merge, release, publish packages, assign roles, or
  schedule agents;
- `controlled-loop-runner-stage-closeout` reads saved completed stage
  execution, invocation boundary, stage-execution approval, stage-execution
  readiness, next-stage, runner-start, runner-plan, dry-run, and stage-output
  evidence, rechecks the full runner chain plus saved operator approval
  signature, approval purpose, approval target checksum, boundary checksum,
  command-result checksum, and output checksum, classifies the stage as
  completed, failed, or blocked, appends no audit evidence, and still does not
  start a process, invoke an executor, retry executors, select another stage,
  continue the loop, execute Git commands, call GitHub, create branches,
  commit, push, create PRs, merge, release, publish packages, assign roles, or
  schedule agents;
- `controlled-loop-runner-stage-outcome-plan` reads saved stage-closeout,
  stage-execution, invocation boundary, approval, readiness, next-stage,
  runner-start, runner-plan, and dry-run evidence, rechecks the full chain and
  saved operator approval, and emits a deterministic read-only outcome target
  for continuation selection, runner completion, or inspection/future
  operator-gated retry planning without selecting a stage, executing a retry,
  continuing the loop, appending audit evidence, executing Git commands,
  calling GitHub, creating branches, committing, pushing, creating PRs,
  merging, releasing, publishing packages, assigning roles, or scheduling
  agents;
- no command runs a continuous governed loop tick end to end or retries failed
  real executor invocations.

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
- initial policy-denied path before executor task emission: complete for
  Phase 1;
- loop-tick decision audit record: complete for Phase 1;
- active epoch conflict: complete for governed execution start;
- stale snapshot rejection: complete for governed execution start.
- controlled-loop-tick matching saved evidence: complete for Task 23;
- controlled-loop-tick mismatched evidence blocks without audit append:
  complete for Task 23.
- controlled-loop-start matching saved plan/start evidence: complete for
  Task 39.
- controlled-loop-start mismatched execution-start evidence blocks without
  appending audit evidence: complete for Task 39.
- controlled-loop-run-summary matching saved runner-adjacent packet chain:
  complete for Task 43.
- controlled-loop-run-summary mismatched intermediate packet evidence blocks
  without appending audit evidence: complete for Task 43.
- controlled-loop-outcome-plan matching terminal controlled outcome evidence:
  complete for Task 44.
- controlled-loop-outcome-plan stale terminal evidence blocks without
  appending audit evidence: complete for Task 44.
- controlled-loop-run-manifest-plan matching terminal evidence records a
  reviewable run manifest without appending audit evidence: complete for
  Task 45.
- controlled-loop-run-manifest-plan stale outcome evidence blocks without
  appending audit evidence: complete for Task 45.
- controlled-loop-run-manifest-approval target-bound operator approval records
  read-only approval evidence without appending audit evidence: complete for
  Task 46.
- controlled-loop-run-manifest-approval mismatched operator approval target
  blocks without appending audit evidence: complete for Task 46.
- controlled-loop-runner-plan matching approved manifest evidence emits a
  dry-run runner plan without appending audit evidence: complete for Task 47.
- controlled-loop-runner-plan stale manifest or blocked approval evidence
  blocks without appending audit evidence: complete for Task 47.
- controlled-loop-runner-execution-approval target-bound operator approval
  records read-only approval evidence without appending audit evidence:
  complete for Task 48.
- controlled-loop-runner-execution-approval mismatched approval or stale runner
  plan evidence blocks without appending audit evidence: complete for Task 48.
- controlled-loop-runner-dry-run approved runner plan and execution approval
  emits would-process stage evidence without appending audit evidence: complete
  for Task 49.
- controlled-loop-runner-dry-run stale/tampered plan, mismatched approval,
  started authority flags, or malformed planned steps block without appending
  audit evidence: complete for Task 49.
- controlled-loop-runner-start-readiness approved dry-run evidence emits
  readiness-only stage evidence without appending audit evidence: complete for
  Task 50.
- controlled-loop-runner-start-readiness stale runner-plan or approval anchors,
  blocked plan or approval evidence, started authority flags, missing
  non-execution guarantees, malformed dry-run stages, non-would-process stages,
  or stage sequence mismatches block without appending audit evidence: complete
  for Task 50.
- controlled-loop-runner-start-approval approved readiness evidence and
  target-bound operator approval emits approval-only evidence without appending
  audit evidence: complete for Task 51.
- controlled-loop-runner-start-approval stale readiness targets, blocked
  readiness evidence, started authority flags, or malformed readiness stages
  block without appending audit evidence: complete for Task 51.
- controlled-loop-runner-start approved start evidence records the bounded
  runner-start boundary and appends one audit record without executor
  invocation, retry, or loop continuation: complete for Task 52.
- controlled-loop-runner-start stale start approval targets, blocked
  start-approval evidence, malformed readiness stages, stale dry-run evidence,
  stale runner-plan evidence, or blocked execution approval block without
  appending audit evidence: complete for Task 52.
- controlled-loop-runner-next-stage approved start evidence selects the first
  runner stage without executing a command, invoking an executor, appending
  audit evidence, or continuing the loop: complete for Task 53.
- controlled-loop-runner-next-stage blocked start, stale runner-plan checksum,
  stale dry-run checksum, or unsupported later stage block without side
  effects: complete for Task 53.
- controlled-loop-runner-next-stage hand-edited runner-start packets missing
  recorded start stages or audit proof block without side effects: complete for
  Task 53.
- controlled-loop-runner-stage-execution-readiness approved next-stage evidence
  prepares a deterministic stage-execution approval target without executing a
  command, invoking an executor, appending audit evidence, or continuing the
  loop: complete for Task 54.
- controlled-loop-runner-stage-execution-readiness stale next-stage, stale
  runner-start, stale runner-plan, stale dry-run, started authority flags, or
  unsupported later stage block without side effects: complete for Task 54.
- controlled-loop-runner-stage-execution-approval matching readiness and
  target-bound operator approval records approval-only evidence without
  executing a command, invoking an executor, appending audit evidence, or
  continuing the loop: complete for Task 55.
- controlled-loop-runner-stage-execution-approval wrong purpose, wrong target,
  expired/future/unsigned/bad-signature/wrong-operator approval, stale
  readiness, stale next-stage, stale runner-start, stale runner-plan, or stale
  dry-run evidence block without side effects: complete for Task 55.
- controlled-loop-runner-stage-invocation-boundary matching approval chain
  prepares exact argv, cwd, output, timeout, execution-authority, side-effect,
  and boundary-checksum evidence without starting a process, appending audit
  evidence, invoking an executor, or continuing the loop: complete for Task 56.
- controlled-loop-runner-stage-invocation-boundary mutated selected command,
  mismatched stage number, unknown stage command, invalid timeout, invalid
  output path, missing side-effect policy, missing execution authority,
  mismatched approval target, and stale operator-approval signature block
  without side effects: complete for Task 56.
- controlled-loop-runner-stage-execute approved boundary executes the selected
  command once with `shell=False`, writes captured output evidence, appends one
  execution audit record after process start, and does not invoke an executor,
  retry, execute another stage, or continue the loop: complete for Task 57.
- controlled-loop-runner-stage-execute mutated boundary, self-consistent
  boundary checksum drift, and invalid saved operator approval signatures block
  before process start without appending audit evidence: complete for Task 57.
- controlled-loop-runner-stage-execute nonzero stage exit records terminal
  failed-stage evidence without retrying or continuing: complete for Task 57.
- controlled-loop-runner-stage-execute empty successful stdout, subprocess
  start failure, and undeclared stage side effects block with structured
  packets: complete for Task 57.
- controlled-loop-runner-stage-closeout successful execution binds the
  approved output file to captured stdout, classifies the stage completed, and
  emits no audit/process/retry/continuation/Git/GitHub side effects: complete
  for Task 58.
- controlled-loop-runner-stage-closeout missing output, tampered output, and
  mutated execution command-result evidence block without starting processes
  or appending audit evidence: complete for Task 58.
- controlled-loop-runner-stage-closeout failed stage execution records a
  terminal failed closeout and recommends failure inspection without retrying,
  selecting another stage, or continuing the loop: complete for Task 58.
- controlled-loop-runner-stage-outcome-plan maps completed non-final closeout
  to a continuation-selection target, completed final-stage decision logic to a
  runner-completion target, verifies the reviewed closeout checksum, and maps
  failed or blocked closeouts to inspection plus future operator-gated
  retry-planning targets without retrying, selecting a next stage, appending
  audit evidence, or continuing the loop: complete for Task 59.
- controlled-loop-runner-next-stage-continuation consumes reviewed outcome
  planning, completed closeout, execution, runner-start, runner-plan, and
  dry-run evidence, verifies the reviewed outcome-plan checksum, selects
  exactly stage N+1, and emits no stage-readiness target while still avoiding
  execution, retry, audit append, Git/GitHub writes, and loop continuation:
  complete for Task 60.
- controlled-loop-runner-stage-input-binding consumes selected continuation
  evidence plus completed prior `loop-run-plan` output and an exact executor
  task file, records the selected stage and executor-task checksums, and emits
  no approval token, readiness target, process start, epoch start, audit
  append, Git/GitHub write, or loop continuation: complete for Task 61.
- controlled-loop-runner-stage-execution-readiness accepts exactly one
  selection source, preserves initial stage-1 readiness behavior, and prepares
  continuation-backed readiness from matching continuation plus stage-input
  binding evidence and a reviewed binding checksum with
  `stage_selection_source: continuation`: complete for Task 62.
- controlled-loop-runner-stage-execution-readiness blocks mixed selection
  sources, missing stage-input binding, mismatched continuation stage number,
  missing or stale reviewed binding checksums, stale continuation checksums,
  invalid continuation/input-binding authority fields, and drifted
  stage-input binding selected-stage identity without side effects: complete
  for Task 62.
- controlled-loop-runner-stage-execution-approval accepts continuation-backed
  readiness without changing stage-1 approval behavior, requires the separate
  `start_governed_execution` operator approval for stage-2
  `start-governed-execution`, rereads the current executor-task file before
  deriving the future approval token, and blocks non-adjacent continuation
  stage sequences without side effects: complete for Task 63.
- controlled-loop-runner-stage-invocation-boundary accepts continuation-backed
  approval/readiness without changing stage-1 boundary behavior, constructs the
  exact stage-2 `start-governed-execution` argv from the verified executor task
  file, derived approval token, and repo cwd, and rejects ownership arguments
  without starting a process, epoch, audit append, Git/GitHub write, or loop
  continuation: complete for Task 64.

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
- `run-controlled-executor-fixture` can launch a fake external executor
  component from an explicit command template, require it to write the expected
  evidence file, and validate the result;
- task-packet validation checks the embedded local repo snapshot, requires its
  repo/cwd/branch/head to match the packet repo anchor, and rejects missing
  repo identity, missing built-in safety stops, relative expected evidence
  paths, dirty, low-confidence, relative-path, or mismatched snapshots;
- successful result evidence must include command evidence, validation
  evidence, a resulting head attestation, elapsed time within the task runtime
  limit, and the absolute expected evidence path;
- disabled commit, push, PR-creation, merge, release, and package-publication
  permissions reject common absolute-path, git/gh global-option,
  shell-wrapper, release, and publish command forms;
- `executor-invocation-readiness` emits read-only
  `executor-invocation-readiness.v1` packets that recheck reviewed task,
  active epoch id/status, active brake, repo path/branch/`HEAD`, clean
  worktree, task checksum, active ownership binding, command and branch policy
  shape, required checks, expected result path under
  `<root>/executor-results`, and optional role-readiness evidence while
  reporting `executor_started: false`;
- `executor-invocation-plan` emits read-only `executor-invocation-plan.v1`
  packets that bind readiness to operator approval identity, clean audit
  replay, adapter metadata, rollback evidence, command, environment allowlist,
  timeout, active epoch, active ownership, and result-path rechecks before any
  future process start;
- `invoke-real-executor` starts one approved local process from a fresh plan,
  captures stdout/stderr, writes `real-executor-invocation.v1` records, and
  enforces `evidence_only` versus `materialized_changes` side-effect modes;
- no named host adapter exists.

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
- done: forbidden merge, release, and package-publication command rejection;
- done: controlled fixture success, failed-result, timeout/stopped, active-stop,
  command-policy, and audit-replay paths;
- done: malformed, missing-name, relative-path, dirty, low-confidence,
  branch/head-mismatched, or repo/cwd anchor-mismatched embedded task
  snapshots;
- done: disallowed changed path;
- done: dirty successful result rejection;
- done: read-only real executor invocation readiness preflight for matching
  task, epoch, ownership, policy, result path, and optional role evidence;
- done: controlled real executor timeout, missing-result, evidence-only clean
  repo, and materialized dirty-worktree evidence paths;
- remaining: branch/commit handling and one-command loop integration.

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
- `loop-tick --policy-file` accepts a local `cadence-loop-policy.v1` JSON file
  with `allowed_paths`, `denied_paths`, `allowed_commands`,
  `denied_commands`, `required_checks`, `max_executor_time_minutes`, and
  `stop_conditions`, and `branch_policy`;
- the policy file supplies defaults and caps for emitted executor task paths
  and runtime, keeps built-in stops plus policy required checks and stop
  conditions when CLI checks or stop conditions are added, and denies requested
  executor paths outside `allowed_paths`, overlapping `denied_paths`, or
  runtime above `max_executor_time_minutes`;
- branch policy is carried into emitted executor task packets and `git-pr-plan`
  blocks dry-run plans that violate allowed base branches, denied target
  branches, required generated-branch prefixes, or a current `main` checkout
  when `allow_current_branch_main` is false;
- root-backed `loop-tick` appends compact `cadence-audit.v1` decision records
  to `<root>/audit/events.jsonl`;
- root-backed `validate-executor-result` appends compact
  `executor_result_validation` audit records with packet and evidence
  checksums;
- root-backed `run-controlled-executor-fixture` appends a compact
  `executor_fixture_invocation` audit record before starting the fake external
  fixture and an `executor_result_validation` record after evidence validation;
- root-backed `closeout-executor-result` appends a compact
  `executor_epoch_closeout` audit record with task/result and snapshot-after
  anchors after a local epoch closeout decision;
- root-backed `start-governed-execution` appends a compact
  `execution_start_decision` audit record after an approved active epoch start;
- `audit-replay` implements the read-only `audit-replay.v1` packet shape,
  blocker codes, counting rules, supported event validation, and corrupted-log
  failure behavior from the merged design;
- command allow/deny policy is carried into executor task packets and enforced
  during result validation;
- active brake stops now prevent recording non-`stopped` executor completion
  evidence when `brake_not_drive` is one of the task stop conditions;
- `verify-resume` emits a read-only `resume-verification.v1` packet with
  stable blocker codes for handoff signature/state, clean-square, repo
  branch/head, dirty-worktree, active brake, active epoch, and pickup-policy
  evidence, including persisted resume snapshot binding checks;
- `resume-continuation` emits a read-only `resume-continuation.v1` packet that
  consumes a saved resume verifier packet, rechecks handoff id, claimer, repo
  branch/head, active brake, active epoch state, clean-square evidence, pickup
  policy, packet freshness, and supplied local ownership evidence, and
  recommends `start_governed_execution` without starting an epoch or executor;
- branch policy is enforced during dry-run planning and immediately before
  operator-approved Git/PR materialization; autonomous branch, commit, push, or
  PR materialization does not exist yet.

Why it matters: unattended confidence comes from bounded blast radius and
recoverable evidence.

Likely files:

- `codex_cadence/model.py`
- `codex_cadence/policy_audit.py`
- `codex_cadence/store.py`
- `codex_cadence/cli.py`
- tests
- docs

Risk: medium

Suggested implementation size: medium

Validation needed:

- initial policy allow/deny tests for executor task paths, required checks,
  max runtime, and stop conditions: complete for Phase 1;
- loop decision audit record: complete for Phase 1;
- executor result validation audit record: complete for Phase 1;
- controlled executor fixture invocation audit record: complete for Phase 1;
- governed execution-start decision audit record: complete for Phase 1;
- denied command test: complete for Phase 1;
- stop brake during active loop: complete for Phase 1;
- resume verifier gate and blocker taxonomy: complete for Phase 1;
- resume-continuation bridge and blocker taxonomy: complete for Phase 2;
- role-readiness evidence and blocker taxonomy: complete for Phase 5
  preparation;
- audit append ordering;
- audit replay summary and corrupted audit record handling: complete for Phase
  1 local JSONL records.

Codex implementation rule: Codex can implement local policy and audit controls.
Destructive cleanup behavior or permissive default autonomy requires operator
approval.

## 4. Minimal Git/PR Automation

Status: Partial

Goal: produce a dry-run Git/PR transition plan from validated executor result
evidence, while reserving live branch, commit, push, and pull request
materialization for later approved slices.

The first implementation increment is the local `git-pr-plan` dry-run packet.
It plans the Git/PR transition from validated executor result evidence, but it
does not create a branch, commit, push, call GitHub, or open a pull request.
This keeps the first contract useful for review and future role coordination
without giving the implementation executor final Git/PR approval authority.

Initial dry-run scope:

1. generate branch/commit/PR plan;
2. validate PR body against template;
3. require operator approval;
4. report suggested commands as suggestions only;
5. preserve role-separation language so the executor evidence producer is not
   treated as the final authority for Git/PR transition approval.

Later increments may add live `gh` or Git commands after explicit approval and
stable packet contracts. Those live side effects are outside the first
`git-pr-plan` slice.

Current evidence:

- `pr-body-preflight` exists;
- `pr-readiness` exists for saved PR JSON;
- saved PR-readiness evidence is labeled as `saved_input`, `stale`, or
  caller-asserted `live_like`, with stale saved evidence waiting before
  blockers when an age policy is supplied;
- `review-response-plan` consumes saved PR JSON, saved review-thread JSON,
  optional candidate discovery output, and PR-body evidence to recommend
  bounded next actions without GitHub writes, and its evidence summary now
  preserves `saved_input` or `stale` freshness labels;
- release dry-run follows operator-confirmation pattern;
- a design spec for the first dry-run-only `git-pr-plan` packet exists at
  `docs/designs/2026-06-01-git-pr-dry-run-plan-design.md`;
- `git-pr-plan` now emits `git-pr-plan.v1` packets with `dry_run: true`,
  `operator_confirmation_required: true`, no side effects, explicit
  non-authority fields, evidence provenance, materialized-change evidence, PR
  body preflight, and non-executable command examples;
- readiness blocks invalid task/result evidence, brake-gated success without a
  runtime root, active brake stops, non-success results, absent materialized
  change evidence, dirty worktrees, HEAD mismatches, detached heads, branch
  mismatches, missing local base branches, generated branch collisions, missing
  PR template sections, and invalid branch names;
- `git-pr-materialize` consumes a reviewed `git-pr-plan.v1` packet plus exact
  target-bound HMAC operator approval, reruns local plan gates, audits
  intended/completed side effects, creates the proposed branch from the clean
  current commit without switching the checkout, pushes with Git hook
  verification disabled for that push, and creates or updates a PR through
  `gh`;
- `git-pr-materialize` can carry supplied saved PR JSON as `pr_evidence` and
  blocks stale or future-dated saved PR evidence before write-side audit, branch,
  push, or PR create/update side effects;
- `git-pr-materialize` can also consume a reviewed
  `git-pr-dirty-materialization-plan.v1` plus a saved
  `git-pr-dirty-commit-materialization.v1` result, recheck the dirty branch
  head, parent, message, file set, plan/target checksums, branch policy, PR body,
  remote target, and saved PR evidence, then push the already-created dirty
  branch and create/update the approved PR with dirty source anchors in the
  materialization packet and audit records;
- `git-pr-dirty-commit-materialize` consumes a reviewed
  `git-pr-dirty-materialization-plan.v1` plus target-bound HMAC approval,
  re-runs dirty file/fingerprint/closeout/branch-policy/PR-body gates, audits
  intended/completed local commit materialization, creates and checks out only
  the approved branch, blocks planned files with Git clean/process filters,
  stages only planned files, disables commit signing, rolls back failed write
  paths, and creates exactly the approved commit message without pushing or
  calling GitHub;
- controlled real executor invocation exists, but no autonomous branch,
  push, PR creation, merge, release, package publication, or closeout-bound
  real-run automation exists.

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

- done: ready branch plan dry-run with verified materialized-change evidence;
- done: PR body preflight success/failure;
- done: proof that the CLI does not call `gh` or mutate Git/runtime state;
- done: blocked invalid task packet;
- done: blocked invalid result evidence;
- done: blocked missing runtime root for brake-gated successful result evidence;
- done: blocked active brake stop for non-`stopped` result evidence;
- done: blocked non-success result;
- done: blocked no materialized changes tied to result evidence;
- done: blocked dirty worktree;
- done: blocked current `HEAD` mismatch;
- done: blocked detached head;
- done: blocked current branch mismatch;
- done: blocked missing local base branch;
- done: blocked generated branch already exists;
- done: blocked missing PR template section;
- done: blocked invalid branch names;
- done: stale saved PR evidence in review-response planning recommends
  refresh before acting on failed checks, review threads, or PR body issues;
- done: freshness labels preserved when saved PR evidence is reused by
  write-side Git/PR materialization paths;
- done: approved dirty-worktree materialization plan can become exactly one
  local branch commit without push/PR/GitHub side effects.

Codex implementation rule: Codex can implement dry-run packets and the existing
operator-approved materialization paths directly. Autonomous branch creation,
pull request writes without exact operator approval, merge, release, and package
publication require later explicit approval.

## 5. CI/Review Feedback Back Into Candidate Discovery

Status: Partial

Goal: convert failing checks and unresolved actionable review feedback into
bounded next candidates.

Current evidence:

- candidate discovery can ingest saved review findings;
- candidate discovery can ingest saved GitHub review-thread files;
- current-tree Task 5 adds `github-evidence-sync`, an explicit read-only live
  fetch that uses `gh pr view` and GitHub GraphQL review-thread reads, then
  writes saved PR JSON, saved review-thread JSON, and a summary packet only
  after both reads succeed;
- missing `gh`, GitHub CLI spawn failure, auth failure, rate limit, network
  failure, command timeout, and malformed JSON return blocked packets without
  partial evidence files;
- review-thread and comment pagination is followed before saving evidence;
  incomplete paginated review-thread evidence still blocks instead of being
  saved as valid readiness input;
- candidate discovery can ingest saved PR JSON with `--pr-json-file` and turn
  failed check runs or status contexts into `pr_check_failure` execution
  candidates;
- candidate discovery can turn saved current actionable review-thread comments
  into bounded `review_finding` execution candidates with source PR identity,
  thread/comment provenance, saved freshness labels, target files, and duplicate
  same-target comment grouping;
- malformed, incomplete, or non-repo-relative review-thread evidence now blocks
  candidate creation instead of producing partial follow-up candidates;
- PR readiness reports blockers;
- PR readiness can ingest saved review-thread JSON with
  `--review-threads-file` and block unresolved actionable current review
  comments;
- PR readiness labels stale saved PR state so it is not treated as merge-ready
  when an explicit age policy says it must be refreshed;
- `review-response-plan` can group failed checks, unresolved actionable current
  review-thread comments, missing PR body sections, and optional candidate
  matches into a read-only `review-response-plan.v1` packet with bounded next
  actions;
- `review-response-materialization-plan` can bind a reviewed response plan,
  saved PR/review-thread/candidate evidence, and exact intended PR body or
  review-comment write text into a read-only
  `review-response-materialization-plan.v1` packet with target checksums,
  PR/head/evidence rechecks, body preflight, actionable-comment target checks,
  duplicate same-target comment write grouping, and
  `github_write_started: false`;
- `review-response-materialize` can consume an approved
  `review-response-materialization-plan.v1`, recheck saved PR/thread evidence,
  update only approved PR body text and post approved review-thread replies,
  and append replayable review-response materialization intent/result audit
  records;
- `post-write-pr-evidence-gate` can consume an approved Git/PR,
  review-response, or review-thread-resolution materialization result plus
  fresh `github-evidence-sync` output, verify refreshed file metadata and PR
  number/branch/base/head anchors, bind review-thread evidence to the same PR,
  require approved thread-resolution targets to exactly match confirmed
  resolution writes, verify those targets against refreshed review-thread
  evidence, re-run PR readiness and candidate discovery, and recommend only
  `ready_for_review`, `refresh_required`, `follow_up_candidates`,
  `wait_for_checks`, `respond_to_review`, or `operator_review`;
- `review-thread-resolution-plan` can consume saved PR JSON, saved
  review-thread JSON, a successful approved review-response materialization
  result, a matching post-write gate packet, and explicit thread ids to emit a
  read-only `review-thread-resolution-plan.v1` approval target with PR/head,
  refreshed evidence, full materialization, and post-write gate checksums;
- review-thread resolution planning blocks stale or mismatched evidence,
  wrong-PR review-thread evidence, disallowed post-write gate blockers,
  incomplete pagination, resolved, outdated, non-actionable, missing,
  unresponded target threads, or current actionable comments not covered by the
  approved response materialization before approval;
- `review-thread-resolution-materialize` can consume an approved
  `review-thread-resolution-plan.v1`, recheck saved PR/thread,
  response-materialization, and post-write gate evidence, resolve only approved
  review thread ids through `resolveReviewThread`, and append replayable
  review-thread resolution intent/result audit records;
- `controlled-pr-cycle` can consume saved `controlled-loop-tick.v1`,
  approved Git/PR materialization, post-write gate, optional approved
  review-response materialization plus post-write gate, and optional approved
  review-thread resolution materialization plus final post-write gate packets,
  recheck packet schemas, checksums, PR anchors, materialization targets, and
  chronological order, emit `controlled-pr-cycle.v1`, and append success-only
  `controlled_pr_cycle` audit evidence;
- `merge-decision-plan` can consume saved PR JSON, review-thread JSON,
  PR-readiness, audit-replay, required `controlled-pr-cycle` evidence, and
  optional role-readiness evidence, recheck PR target anchors, require controlled-cycle
  audit evidence, block unresolved review comments, emit
  `merge-decision-plan.v1`, and require operator confirmation before any merge
  outside Cadence;
- `loop-run-plan` can consume the current `loop-tick` decision path and emit a
  `loop-run-plan.v1` packet with planned next steps while reporting no runner,
  executor, epoch, PR-action, GitHub-write, merge, release, package,
  role-assignment, scheduling, or loop-continuation side effects;
- `controlled-loop-start` can consume saved loop-run-plan and execution-start
  evidence, verify that the approved start matches the planned executor task,
  and recommend executor-invocation planning without starting a runner,
  executor, GitHub write, or loop continuation;
- `controlled-loop-invocation-plan`, `controlled-loop-real-invocation`, and
  `controlled-loop-closeout` can
  consume saved readiness/plan, recorded real-invocation, and accepted
  closeout evidence, verify the task, epoch, target, result, audit,
  pending-closeout, and accepted closeout anchors, and recommend the next
  operator-controlled step without autonomous retry or continuation;
- `controlled-loop-run-summary`, `controlled-loop-outcome-plan`,
  `controlled-loop-run-manifest-plan`, and
  `controlled-loop-run-manifest-approval`, and
  `controlled-loop-runner-plan`, and
  `controlled-loop-runner-execution-approval` can summarize the saved
  runner-adjacent chain, map the terminal outcome to a bounded next operator
  action, bind the evidence-file manifest plus controlled command stages,
  verify target-bound manifest approval, produce a dry-run runner plan, and
  verify target-bound runner execution approval without appending audit
  evidence, starting a runner or executor, retrying executors, continuing the
  loop, starting or closing an epoch, executing Git commands, calling GitHub,
  creating branches, committing, pushing, creating PRs, merging, releasing,
  publishing packages, assigning roles, or scheduling agents;
- no branch creation, commit, push, merge, release, package publication,
  continuous reconciliation, automatic response loop execution, paid review,
  label editing, role assignment, or agent scheduling exists.

Why it matters: unattended operation fails quickly if Cadence cannot react to
CI failures or review comments.

Likely files:

- `codex_cadence/candidates.py`
- `codex_cadence/pr_readiness.py`
- `codex_cadence/review_response.py`
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
- merge readiness remains blocked while actionable feedback exists;
- malformed or incomplete review-thread evidence blocks readiness;
- review-response planning groups failed checks and review threads by stable
  follow-up target;
- stale saved PR evidence recommends `refresh_pr_evidence` before response
  items are emitted;
- missing PR body sections recommend `update_pr_body` without editing the PR;
- done: read-only review-thread resolution planning emits an exact approval
  target for fresh unresolved responded-to thread ids and deduplicates duplicate
  target requests;
- done: review-thread resolution planning blocks stale, incomplete, mismatched,
  wrong-PR, failed-gate, already resolved, outdated, non-actionable, missing,
  unresponded target, and unresponded current-comment evidence before approval;
- done: `controlled-pr-cycle` composition accepts internally consistent saved
  Git/PR, review-response, review-thread resolution, and post-write gate
  chains while appending success-only audit evidence;
- done: `controlled-pr-cycle` composition blocks missing final post-resolution
  gates and PR target/checksum drift without appending audit evidence;
- read-only live fetch failures block without partial local evidence files.

Codex implementation rule: Codex can implement local ingestion and explicit
read-only GitHub evidence capture directly. GitHub writes, review-comment
updates, branch creation, commits, pushes, merge, release, package publication,
paid review, and permission changes require operator approval.

## Expected Confidence Impact

The current confidence rating is 25%.

If all five slices are complete with evidence, expected confidence for
low-risk constrained operation with pre-approved unattended ticks is 45% to
57%.

This does not mean production-autonomous operation. It means a controlled loop
can run under policy, make bounded progress, stop safely, and leave an audit
trail.

## Future Agent-Team Orchestration Slices

These are not part of the immediate 50% confidence target, but they should
shape design choices now:

- GitHub-native work registry: bind elected work to an issue, task id, or
  recorded decision before implementation starts.
- Agent role registry: record role, permissions, owner identity, branch, task,
  and handoff contract for each active agent.
- Branch and PR ownership: prevent duplicate work by making branch/PR claims
  visible to other agents before they start.
- Review separation: prove that the Reviewer Agent is distinct from the
  Builder Agent when policy requires separation.
- QA and documentation gates: route test failures and living-doc updates to
  explicit roles instead of letting implementation sessions silently decide.
- Release and merge decision packets: separate merge authority from build and
  review work, with evidence-backed readiness.
- Cross-role handoff contracts: support Builder-to-Reviewer,
  Reviewer-to-Builder, QA-to-Builder, Documentation-to-Release, and
  session-to-session handoffs using the same durable handoff model.

## Slice Completion Checklist

Every implementation slice should update:

- tests or examples proving the behavior;
- `docs/progress-log.md`;
- `docs/autonomous-loop-readiness.md` if loop capability changed;
- `docs/roadmap.md` if priorities or confidence changed;
- `docs/decision-log.md` if architecture or governance choices changed.
