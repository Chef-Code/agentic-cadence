# Autonomous Loop Readiness

Status: living document
Last updated: 2026-06-16
Baseline: released 0.1.3 plus unreleased audit-replay with local hash-chain integrity evidence, authenticated local operator approval identity evidence, policy/stop-control, executor closeout, git-pr-plan, branch policy, read-only GitHub evidence sync, controlled executor fixture, governed execution-start epoch gating, local execution-run evidence records, operator-approved Git/PR materialization, read-only resume verification, ownership-aware read-only resume continuation, read-only review-response planning, operator-approved review-response materialization, post-write PR evidence gate, read-only review-thread resolution planning, operator-approved review-thread resolution materialization, post-resolution PR evidence refresh, `controlled-pr-cycle` evidence composition, read-only merge decision planning, read-only controlled loop-start composition, read-only controlled loop invocation-plan composition, read-only controlled real-invocation composition, read-only controlled closeout composition, read-only controlled loop-run summary evidence, read-only controlled loop outcome planning, read-only controlled loop run manifest planning, read-only controlled loop run manifest approval, read-only controlled loop runner planning, read-only controlled loop runner execution approval, read-only controlled loop runner dry-run evidence, read-only controlled loop runner start-readiness evidence, read-only controlled loop runner start-approval evidence, controlled loop runner start evidence, read-only role-readiness evidence, read-only executor-invocation-readiness and invocation-plan evidence, controlled real executor invocation evidence, real-invocation closeout binding, controlled single-tick run packet evidence, local work ownership claim/closeout evidence, and Tasks 28-52 complete in main or active review branches
Current unattended-operation confidence: 25% (deliberately stable headline token; progress-log records Task 52 projected capability at 47%)

This document answers how close Agentic Cadence is to the "press start and
build continuously" experience. The first usable path is a governed
single-agent loop. The larger direction is GitHub-native orchestration for
multiple cooperating agents.

## What The Magic Button Means

The Phase 1 target loop is:

```text
inspect repo
-> choose next bounded task
-> prepare implementation task
-> run executor under policy
-> validate changes
-> commit/push/open or update PR
-> trigger reviews
-> ingest CI and review feedback
-> create follow-up tasks
-> monitor context pressure and policy limits
-> hand off when needed
-> resume in a fresh session
-> continue safely
```

The mature target is an orchestrator loop:

```text
roadmap/backlog
-> task decomposition/election
-> role-aware agent assignment
-> branch/PR/CI/review
-> docs/handoff/merge decision
-> next governed task
```

The loop must also stop safely when policy, CI, review, dirty worktree,
context pressure, operator brake, or confidence conditions require it.

## What Works Today

If Agentic Cadence is pointed at a real repository today, the CLI and local
runtime can do these things end-to-end:

- initialize and manage local runtime state;
- report Cadence state and compatibility brake state;
- snapshot local git state;
- detect local dirty worktree, branch, head SHA, detached head, and supplied CI
  status;
- discover candidate work from local deterministic inputs;
- elect conservative next candidates when allowed;
- run one read-only `loop-tick` that snapshots local repo state,
  discovers/elects candidates, checks Cadence state, and emits a next-action
  packet;
- emit a generic executor task packet for operator approval without starting an
  executor;
- consume a reviewed generic executor task packet with
  `start-governed-execution`, recheck repo path, branch, `HEAD`, dirty
  worktree, task-carried command and branch policy shape, approval token, active
  brake, active epoch state, and supplied local ownership evidence, then start
  exactly one active epoch and bind that ownership record while still reporting
  `executor_started: false`;
- run an explicit test/example-only controlled executor fixture command that
  validates the task packet and command policy before launching a fake external
  executor component, then validates the fixture's result evidence;
- apply an initial local loop policy file to bound emitted executor task paths,
  command policy, required checks, runtime, and stop conditions while retaining
  built-in safety stops;
- append compact audit records for root-backed loop decisions, controlled
  executor fixture invocation, and executor result validation, including
  task/result checksums for result validation;
- replay local audit history with a read-only `audit-replay` command that
  validates `cadence-audit.v1` JSONL shape, supported events, line counts,
  checksum syntax, and hash-chain metadata, reports local chain head/counts,
  treats older records as explicit legacy roots, and blocks tampered chained
  records;
- verify reusable `operator-approval.v1` identity evidence with
  `verify-operator-approval`, including target checksum, purpose, operator id,
  key id, expiration, and HMAC signature, while still starting no executor,
  epoch, PR, merge, release, or package action, and append accepted
  `operator_approval_verification` audit evidence;
- validate that executor task packets are anchored to the embedded local repo
  snapshot by requiring matching repo name, absolute cwd/path, branch, head,
  clean worktree, built-in safety stops, absolute expected evidence path, and
  non-low repo confidence;
- validate local generic executor result evidence against a task packet,
  including elapsed runtime bounds, expected evidence-path binding, command
  allow/deny policy, disabled commit/push/PR/merge/release/package-publication
  permissions, and active brake stop handling;
- close out an active epoch from local executor task/result/snapshot-after
  packets, recording successful task evidence while other epoch tasks remain,
  completing terminal successful epochs, failing failed/blocked/stopped or
  policy-violating evidence with stable reason codes, and emitting a local next
  decision;
- generate a dry-run Git/PR transition plan from validated executor evidence,
  with local branch/head/base checks, explicit materialized-change evidence,
  task-carried branch policy, PR body preflight, operator-confirmation
  requirements, and no Git or GitHub side effects;
- materialize a reviewed Git/PR plan only after exact target-bound operator
  approval and local rechecks, creating a branch from the already-materialized
  clean commit, pushing it, and creating or updating a pull request with
  replayable audit records;
- explicitly fetch read-only live GitHub PR metadata, status checks, and review
  threads into saved local JSON evidence files through `github-evidence-sync`;
- convert saved current actionable review-thread comments into bounded
  `review_finding` execution candidates with source PR identity, thread/comment
  provenance, saved freshness labels, target files, duplicate same-target
  grouping, and fail-closed handling for incomplete thread evidence;
- size tasks and enforce pickup policy;
- start, check, complete, or fail bounded epochs;
- prepare a signed handoff packet and clean-square evidence;
- verify a handoff pickup with a read-only `verify-resume` packet that checks
  claimed state, clean-square evidence, repo branch/head, dirty worktree state,
  active brake, active epoch state, and pickup-policy evidence;
- bind a saved `resume-verification.v1` packet to a fresh read-only
  `resume-continuation.v1` packet that rechecks handoff id, claimer, repo
  branch/head, brake, active epoch state, clean-square, policy evidence, and
  packet freshness, and supplied local ownership evidence before recommending
  governed execution start;
- approve, claim, complete, or fail handoffs;
- evaluate saved PR JSON, saved review-thread JSON, and saved PR body/template
  files for readiness, including `saved_input`, `stale`, and caller-asserted
  `live_like` evidence.
- produce a read-only `review-response-plan.v1` packet from saved PR JSON,
  saved review-thread JSON, optional candidate discovery output, and PR-body
  evidence, grouping actionable feedback into bounded next-action
  recommendations without calling GitHub or invoking review agents.
- read and validate local `work-ownership.v1` records through
  `work-ownership-status` and `validate-work-ownership`, surfacing duplicate active ownership,
  stale evidence, malformed records, closed evidence, and repo/branch/task
  mismatches;
- explicitly create, close, and fail local `work-ownership.v1` records through
  `claim-work-ownership`, `close-work-ownership`, and `fail-work-ownership`
  after branch, `HEAD`, clean-worktree, duplicate/stale ownership, malformed
  registry, and path-safety rechecks, with replayable local audit evidence.
- bind matching active ownership to governed execution start through
  `start-governed-execution --ownership-target`, with rollback of both epoch
  and ownership binding when audit append fails.
- bind supplied matching active ownership to read-only resume continuation
  through `resume-continuation --ownership-target` without mutating ownership,
  starting an epoch, or invoking an executor.
- verify local role policy and builder/reviewer separation with read-only
  `role-readiness.v1` packets from `role-policy.v1`, local ownership status,
  saved PR JSON, and saved review-thread evidence.
- verify a future real executor invocation boundary with read-only
  `executor-invocation-readiness.v1` packets that recheck the reviewed task
  packet, active epoch id/status, active brake, repo path/branch/`HEAD`,
  clean worktree, task checksum, ownership epoch binding, command and branch
  policy shape, required checks, result-path boundary under
  `<root>/executor-results`, and optional role-readiness evidence while
  reporting `executor_started: false`.
- bind that readiness to a read-only `executor-invocation-plan.v1` packet with
  operator approval identity, clean audit replay, adapter metadata, rollback
  evidence, command, environment allowlist, timeout, active epoch, active
  ownership, and result-path rechecks before any future process start.
- compose a saved `loop-run-plan.v1` packet with an already produced
  `execution-start.v1` packet through `controlled-loop-start`, rechecking the
  planned executor task checksum, execution-start task anchor, local active
  epoch, and start audit record before recommending executor-invocation
  planning, without starting a runner or executor.
- compose a saved `controlled-loop-start.v1` packet with saved
  `executor-invocation-readiness.v1` and `executor-invocation-plan.v1`
  packets through `controlled-loop-invocation-plan`, rechecking task, epoch,
  readiness, and target checksum anchors before recommending
  `invoke_real_executor`, without starting a runner or executor.
- compose a saved `controlled-loop-invocation-plan.v1` packet with saved
  `real-executor-invocation.v1` evidence through
  `controlled-loop-real-invocation`, rechecking the invocation-plan checksum,
  target checksum, plan-file anchor, record-file anchor, invocation audit
  record, result path/checksum, invocation id, and pending closeout status
  before recommending
  `closeout_executor_result`, without starting or retrying an executor.
- compose a saved `controlled-loop-real-invocation.v1` packet with saved
  `executor-epoch-closeout.v1` evidence through `controlled-loop-closeout`,
  rechecking the pre-closeout invocation checksum, closeout-bound path/id,
  terminal closeout status, updated real-invocation checksum, closeout audit,
  real-invocation closeout-update audit, and epoch closeout checksum before
  recommending `controlled_loop_tick`, without closing epochs, rewriting
  records, appending audit, or continuing the loop.
- start one approved real executor command through `invoke-real-executor`,
  write `real-executor-invocation.v1` evidence, and bind accepted
  real-invocation evidence into closeout and dry-run Git/PR planning without
  granting autonomous GitHub write authority.
- compose a saved single-tick chain with `controlled-loop-tick`, rechecking
  the `loop-tick`, task, execution-start, readiness, invocation-plan,
  real-invocation, result, snapshot-after, closeout, and optional dry-run
  Git/PR plan anchors into one `controlled-loop-tick.v1` packet and
  success-only `controlled_loop_tick` audit record.
- summarize a saved runner-adjacent controlled chain with
  `controlled-loop-run-summary`, rechecking `loop-run-plan`,
  controlled-start, controlled invocation-plan, controlled real-invocation,
  controlled closeout, and controlled tick packet checksums without appending
  audit, retrying executors, or continuing the loop.
- plan the next bounded operator action from a saved terminal controlled run
  with `controlled-loop-outcome-plan`, rechecking controlled run summary,
  controlled closeout, controlled tick checksums, source decision, task, epoch,
  and closeout anchors without appending audit, retrying executors, continuing
  the loop, or writing Git/GitHub state.
- compose a saved PR cycle with `controlled-pr-cycle`, rechecking
  controlled-loop, approved Git/PR materialization, post-write gate, optional
  review-response materialization, optional review-thread resolution
  materialization, final post-write gate, PR target, checksum, and chronological
  anchors into one `controlled-pr-cycle.v1` packet and success-only
  `controlled_pr_cycle` audit record.
- plan a merge decision with `merge-decision-plan` from saved PR JSON,
  review-thread JSON, PR-readiness, audit-replay, required
  `controlled-pr-cycle` evidence, and optional role-readiness evidence,
  while keeping
  `operator_confirmation_required: true`, `merge_started: false`, and no Git or
  GitHub write side effects.

These capabilities are still single-agent Phase 1 primitives, but they are not
throwaway work. They are the same primitives a future orchestrator needs for
task ownership, bounded effort, review separation, handoff contracts, and
merge decisions.

This repository also includes validation and review guardrails that prove the
baseline is testable:

- package, protocol, first-run, and generic adapter contract checks in CI;
- an elected Codex Review GitHub Actions workflow for this repository.

Those workflow guardrails are not installed or triggered by the Agentic Cadence
CLI when it is pointed at an arbitrary target repository. External repositories
must provide equivalent workflow wiring before those checks are available.

## What Does Not Work Today

Agentic Cadence cannot currently:

- implement code changes by itself;
- choose a task, hand it to a real executor, close it out, and continue without
  separately approved evidence files;
- decompose work across an agent pool;
- assign role-specific agents such as Planning, Architecture, Builder,
  Reviewer, QA, Documentation, Release, or Handoff agents;
- assign or enforce authenticated reviewer identity separate from the builder;
- autonomously create a branch;
- autonomously commit dirty-worktree changes;
- autonomously push to a remote;
- autonomously open or update a pull request;
- autonomously resolve review feedback or review threads;
- autonomously create, edit, or resolve GitHub PRs or review comments;
- trigger follow-up implementation from live CI or review failures;
- infer context pressure without explicit host input;
- launch a fresh coding-agent session;
- resume a full new-session loop automatically;
- run continuously without an external operator or orchestrator;
- merge, release, or publish packages.

## Implemented Versus Planned

| Loop capability | Current status | Evidence |
| --- | --- | --- |
| Local repo snapshot | Implemented, local only | `codex_cadence/repo_state.py` |
| Candidate discovery | Implemented, read-only | `codex_cadence/candidates.py` |
| Task sizing | Implemented | `codex_cadence/model.py` |
| Epoch governance | Implemented | `codex_cadence/epochs.py`, `closeout-executor-result` |
| Handoff lifecycle | Implemented | `codex_cadence/handoff_loop.py`, `codex_cadence/cli.py` |
| PR body/readiness checks | Implemented from saved inputs | `codex_cadence/pr_readiness.py` |
| Elected Codex Review workflow | Implemented in GitHub Actions | `.github/workflows/codex-review.yml` |
| Single loop tick | Partial, controlled local evidence | `loop-tick` emits next action and stops before execution; `loop-run-plan` wraps that decision into a read-only next-step packet; `controlled-loop-start` composes a saved plan with approved execution-start evidence; `controlled-loop-invocation-plan` composes the controlled start with readiness and invocation-plan evidence before process start; `controlled-loop-real-invocation` composes the recorded real invocation with that controlled plan before closeout; `controlled-loop-closeout` composes accepted closeout with the controlled real-invocation packet before the aggregate tick; `controlled-loop-tick` composes saved local evidence after closeout without retrying or continuing; `controlled-loop-run-summary` summarizes the saved runner-adjacent chain; `controlled-loop-outcome-plan` maps the reviewed terminal outcome to the next bounded operator action without continuation; `controlled-loop-run-manifest-plan` binds the terminal evidence and outcome plan into a reviewable command/evidence manifest; `controlled-loop-run-manifest-approval` verifies a target-bound operator approval for that manifest; `controlled-loop-runner-plan` turns the approved manifest into a dry-run runner plan; `controlled-loop-runner-execution-approval` verifies target-bound operator approval for that runner plan; `controlled-loop-runner-dry-run` rechecks the approved plan and approval and emits would-process stage evidence; `controlled-loop-runner-start-readiness` and `controlled-loop-runner-start-approval` gate the start boundary; `controlled-loop-runner-start` records the approved one-cycle runner-start boundary and audit record, all without starting an executor, retrying executors, continuing the loop, starting or closing an epoch, executing Git commands, calling GitHub, creating branches, committing, pushing, creating PRs, merging, releasing, publishing packages, assigning roles, or scheduling agents |
| Local policy/audit controls | Partial | `loop-tick --policy-file`, task command policy, task-carried branch policy, active brake stop handling, governed execution-start audit, local `execution-run.v1` records, `<root>/audit/events.jsonl`, hash-chained new audit appends, read-only `audit-replay`, audited `operator-approval.v1` verification through `verify-operator-approval`, success-only `controlled_loop_tick`, and success-only `controlled_pr_cycle` audit evidence; no external identity provider or autonomous GitHub authority |
| Agent-team orchestration | Partial read-only evidence | `role-readiness` can verify local `role-policy.v1`, scoped ownership role labels, and saved review-thread separation evidence; no agent pool, role assignment, role registry, or GitHub-native assignment workflow |
| Continuous loop runner | Not built | Planned slice |
| Executor adapter contract | Partial generic contract | Task/result packet validation, a fake controlled fixture runner, supplied-run-record closeout binding, read-only `executor-invocation-readiness` preflight, read-only `executor-invocation-plan` approval/adapter/rollback binding, controlled `invoke-real-executor` local process-start records, and real-invocation closeout binding exist, but no named host adapter or autonomous GitHub/merge authority |
| Autonomous implementation | Not built | Requires host/session orchestration, named adapter support, autonomous Git/PR flow, merge governance, and release governance |
| Live GitHub sync | Partial, read-only evidence capture and post-write gate | `github-evidence-sync` fetches PR JSON and review threads into local files without GitHub writes; `post-write-pr-evidence-gate` binds fresh saved evidence, file metadata, PR anchors, review-thread PR identity, and approved thread-resolution targets to approved Git/PR, review-response, and review-thread-resolution write results before the next recommendation |
| Git/PR transition planning | Partial, dry-run plus approved materialization | `git-pr-plan` emits reviewable branch/commit/PR plans without side effects; `git-pr-materialize` can create branch, push, and create/update PR only after exact target-bound operator approval and local rechecks |
| Branch/commit/push/PR creation | Partial, operator-approved only | Exact approved dirty-worktree commit, push, and PR create/update bridges exist; no autonomous branch/PR writes, merge, release, or package publication |
| Review response loop | Partial approved writes plus post-write refresh | Saved review files, synced review threads, failed checks, and PR-body evidence can become response-plan items; approved PR body/comment writes and approved review-thread resolution writes exist; post-write gate rechecks fresh evidence before the next bounded action; `controlled-pr-cycle` can compose the saved PR/review/post-write chain before merge planning; no automatic response loop |
| Merge decision | Partial read-only planning | `merge-decision-plan` can bind saved PR, review-thread, readiness, audit, controlled-cycle, and optional role-readiness evidence into an operator-confirmed plan; no merge authority, branch deletion, release, or package publication |
| Local work ownership | Partial, execution/resume-bound local evidence | `work-ownership-status` and `validate-work-ownership` validate local `work-ownership.v1` records; `claim-work-ownership`, `close-work-ownership`, and `fail-work-ownership` create/move local records with audit evidence; `start-governed-execution --ownership-target` can bind matching active ownership to the started epoch; `resume-continuation --ownership-target` can recheck matching active ownership before recommending execution start; no distributed lock, role assignment, or scheduler |
| Context-pressure monitor | Partial explicit signal only | Host/session signal required |
| New-session launch/resume | Partial read-only gates | `prepare-handoff`, clean-square evidence, `verify-resume`, and `resume-continuation.v1` packets exist; external orchestration still launches sessions and performs recommended actions |

## Can It Run The Full Loop Today?

No.

It can inspect and suggest. It can run a read-only loop tick that produces a
structured next action and wrap that decision in a `loop-run-plan.v1` packet
that lists the next bounded operator/orchestrator steps. It can emit a generic
executor task packet for operator approval, validate the packet's local
snapshot trust anchor, start one active epoch from an exactly approved task
packet through `start-governed-execution`, compose the saved loop plan and
approved start with `controlled-loop-start`,
run a controlled fake executor fixture from an explicit command template for
tests/examples, write a local execution-run record, validate local executor
result evidence, close out the active epoch with supplied run-record or
real-invocation binding, produce a dry-run Git/PR transition plan for separate
review, and
materialize that plan only after exact target-bound operator approval and local
rechecks. It can
govern handoff and continuation decisions, including read-only resume and
resume-continuation gates that return stable blocker codes before a fresh
session continues or external orchestration starts governed execution. It can
evaluate saved PR evidence, fetch read-only live PR/check/review-thread
evidence into saved files, turn saved failed-check, review-thread, and PR-body
evidence into response-plan items, write only approved PR body/comment
responses, and run the read-only post-write evidence gate in
`codex_cadence/github_evidence.py` before recommending `ready_for_review`,
review response, follow-up candidates, waiting, or operator review. It still
lacks autonomous scheduling and merge authority. It can validate, claim,
close, and fail local `work-ownership.v1` records, detect duplicate active
ownership for the same repo, branch, and task, bind matching active ownership
to a governed execution start, recheck matching active ownership at
resume-continuation, and replay accepted ownership mutations through the local
audit log before future multi-worker coordination exists. It can compose saved
loop, task, start, readiness, invocation, result, snapshot-after, closeout, and
optional Git/PR plan evidence into a `controlled-loop-tick.v1` packet after
the fact. It cannot
perform the core build loop by itself, and it cannot yet coordinate a team of
role-specific agents.

The current loop stops after:

```text
inspect repo -> discover/elect candidate -> emit blocked/no_candidates/approval_required/requires_executor_contract/approve_executor_task -> optional loop-run-plan -> approved start_governed_execution -> optional controlled-loop-start
```

It can also emit `policy_denied` when a supplied local loop policy blocks the
requested executor-task bounds, and it can verify a post-closeout saved chain
with `controlled-loop-tick` once external/operator steps have produced the
required evidence files.

At `requires_executor_contract`, a human or external agent still has to request
an executor task packet. At `approve_executor_task`, a human or external agent
still has to approve the exact task packet before Cadence starts one governed
epoch. A successful `execution-start.v1` packet does not start a real executor;
it only creates local epoch state and recommends external executor handoff.
`controlled-loop-start` can recheck the saved loop plan against that approved
start and recommend executor-invocation planning, but it still does not start a
runner, invoke an executor, continue the loop, or write Git/GitHub state. The
read-only `executor-invocation-readiness.v1` packet can prove whether a real
invocation is locally ready, and `executor-invocation-plan` binds that
readiness to operator approval, adapter metadata, rollback evidence, a command,
environment allowlist, timeout, result path, and the current audit-chain head.
`controlled-loop-invocation-plan` can then compose the controlled start,
readiness, and invocation plan into one read-only pre-invocation packet before
any process start.
Matching plans still use `recommended_next_action: invoke_real_executor`;
process start only happens when an operator runs `invoke-real-executor`.
`invoke-real-executor` can now start exactly one approved command and write
`real-executor-invocation.v1` evidence, and `closeout-executor-result
--real-invocation-file` can bind that evidence to result validation, epoch
closeout, active ownership revalidation, and dry-run Git/PR planning. These
commands still do not commit, push, open PRs, resolve threads, merge, release,
publish packages, assign roles, schedule agents, or write GitHub state.
`controlled-loop-closeout` can recheck the saved controlled real-invocation
packet against accepted closeout evidence and the updated invocation record
before recommending the aggregate tick, but it does not close epochs, rewrite
records, append audit, or continue the loop.
`controlled-loop-tick` can then recheck that saved local chain and emit one
completed or blocked `controlled-loop-tick.v1` packet with success-only
`controlled_loop_tick` audit evidence; it does not retry the executor or
rewrite invocation or closeout records. `controlled-pr-cycle` can recheck the
saved PR/review/post-write chain, and `merge-decision-plan` can bind that
cycle to saved PR-readiness, review-thread, audit-replay, and optional
role-readiness evidence before an operator considers merging. These packets
still do not merge, delete branches, release, publish packages, assign roles,
schedule agents, or continue the loop. The
controlled fixture path can prove policy, timeout, audit, run-record, and
result-evidence behavior with fake local evidence, and local closeout can
record task completion or terminally complete/fail the active epoch from that
evidence, but it does not implement product changes. The
dry-run `git-pr-plan` handoff remains
review-only until an operator invokes `git-pr-materialize` with a matching plan
approval token. Real code changes, autonomous Git/PR materialization,
autonomous dirty-worktree commits, autonomous review feedback response writes,
review-thread resolution, and new-session launch remain external or future
governance slices. Resume verification and
resume-continuation can block stale or mismatched pickup state, but they do not
claim handoffs, start epochs, invoke executors, or launch sessions. Local
`operator-approval.v1` evidence can now identify an approver for a target and
purpose, but it is not process-start authority by itself. At
`policy_denied`, an operator must adjust the task bounds or policy before
execution can be considered. Audit history is now locally inspectable through
`audit-replay`, but clean replay evidence is not approval to execute work.

## What Would Break First

The first hard stop in a real unattended run is now autonomous Git/PR
materialization and autonomous review-response writes after real-run closeout
evidence.
Cadence can emit a bounded
executor task packet, reject malformed, dirty, low-confidence, relative-path,
or mismatched snapshot anchors, start one approved active epoch through
`start-governed-execution`, prove read-only executor invocation readiness
through `executor-invocation-readiness`, bind a read-only real-executor
invocation plan through `executor-invocation-plan`, start one approved real
executor command through `invoke-real-executor`, run a fake controlled fixture,
close local fixture evidence into an epoch decision, and bind accepted
`real-executor-invocation.v1` evidence into epoch closeout, active ownership
revalidation, and dry-run Git/PR planning. It can also verify local
authenticated operator approval identity evidence for a target checksum and
purpose, then compose the saved evidence into a `controlled-loop-tick.v1`
packet. It still does not autonomously materialize dirty worktree changes into
commits, push, write PR/review responses, resolve review threads, merge,
release, or coordinate agent pools.

The next likely failures are:

1. real executor invocation records are now local closeout evidence, but no
   autonomous commit/push/PR workflow follows from that evidence;
2. no autonomous branch/commit/push/PR workflow exists; the current Git/PR
   increment requires explicit operator approval and only creates a branch from
   an already-materialized clean commit;
3. live synchronization is read-only and operator-triggered. Repo snapshots are
   local git snapshots, and synced PR/check/review-thread evidence is saved to
   local files before later commands consume it. Cadence labels `local_only`,
   `saved_input`, `stale`, and caller-asserted `live_like` evidence, but it
   still does not reconcile or mutate live PR state continuously;
4. review comments, failing checks, and PR body gaps can become candidates or
   response-plan items from saved evidence, but no automatic response loop
   implements fixes, posts comments, resolves review threads, or writes PR
   updates;
5. context pressure is only known when a host explicitly reports it;
6. `role-readiness` can prove local builder/reviewer separation from saved
   evidence, but no authenticated role identity, assignment workflow, or
   agent-pool scheduler exists;
7. CLI root-using commands now block unignored repo-local runtime roots unless
   an operator explicitly allows them; residual risk remains for manual
   filesystem changes or future adapters that bypass the CLI guard.

## Minimum Realistic Demo Loop

The minimum credible demo should not claim full autonomy. It should show
Cadence as the loop controller:

```text
snapshot repo
-> discover/elect next task
-> require approval
-> emit implementation packet
-> external executor or Codex session implements
-> run configured validation
-> record result
-> complete or fail epoch
-> prepare PR/readiness/handoff decision
```

The implementation step can remain external for the first demo. The important
progress is that Cadence owns the loop state, policy gates, evidence records,
and stop conditions.

## 50% Confidence Criteria

Agentic Cadence should not be considered near 50% confidence until the project
has evidence for:

- one bounded loop tick command;
- generic executor evidence contract;
- policy file with command/path/runtime/check limits;
- append-only audit log;
- stop/brake behavior during active loop work;
- dry-run-first branch and PR packet generation;
- CI and review feedback becoming candidate input;
- fixture tests for success, failure, stale state, dirty state, and stop state.

Even at 50%, the expected mode is low-risk controlled operation with strong
constraints and pre-approved unattended ticks, not production-autonomous work.

## Current Confidence Rating

Current rating: 25%.

Reasoning:

- Safety and governance primitives are real.
- A read-only `loop-tick` now stitches snapshot, candidate election, Cadence
  state, and next-action reporting into one packet; `loop-run-plan` can wrap
  that decision into a bounded next-step plan, and `controlled-loop-start` can
  compose that plan with approved execution-start evidence while
  `controlled-loop-invocation-plan` composes the controlled start with
  readiness and invocation-plan evidence, and
  `controlled-loop-real-invocation` composes the recorded real invocation with
  that controlled plan before closeout, while `controlled-loop-closeout`
  composes accepted closeout evidence before the aggregate tick,
  `controlled-loop-run-summary` summarizes the saved runner-adjacent chain,
  `controlled-loop-outcome-plan` maps the reviewed terminal outcome to a
  bounded next operator action, `controlled-loop-run-manifest-plan` binds the
  terminal evidence files, checksums, and one-cycle command stages into a
  reviewable manifest, and `controlled-loop-run-manifest-approval` verifies a
  target-bound operator approval for that manifest, and
  `controlled-loop-runner-plan` converts the approved manifest into a dry-run
  runner plan, `controlled-loop-runner-execution-approval` verifies
  target-bound operator approval for that runner plan, and
  `controlled-loop-runner-dry-run` rechecks both before emitting would-process
  stage evidence, and `controlled-loop-runner-start-readiness` revalidates the
  supplied runner plan and approval while rechecking the completed dry-run
  stage sequence before any future runner start, and
  `controlled-loop-runner-start-approval` verifies target-bound operator
  approval for that readiness packet, and `controlled-loop-runner-start`
  records the approved one-cycle runner-start boundary with one audit record,
  all without starting an executor, retrying an executor, continuing the loop,
  starting or closing an epoch, executing Git commands, calling GitHub, creating
  branches, committing, pushing, creating PRs, merging, releasing, publishing
  packages, assigning roles, or scheduling agents.
- The generic executor task/result contract is now explicit and testable, and
  is wired through read-only executor invocation readiness, read-only invocation
  planning, fake controlled executor fixtures, and controlled one-command real
  executor invocation with real-run closeout binding, but not unattended
  GitHub/review/session orchestration.
- `controlled-loop-tick` can now prove that a saved local single-tick evidence
  chain is internally consistent and append `controlled_loop_tick` audit
  evidence for the completed composition, but it depends on separately
  produced evidence files and does not retry or continue.
- `controlled-pr-cycle` and `merge-decision-plan` can now prove saved
  PR/review/post-write and merge-readiness evidence coherency before operator
  merge consideration, but they still provide no merge authority.
- Executor task packets now fail closed on malformed local snapshots, missing
  repo identity, relative or unnormalizable cwd/path anchors, repo/cwd/branch/head
  mismatches, dirty worktrees, and low-confidence repo state.
- Initial local policy/audit controls can bound emitted executor task packets,
  record loop/execution-start/execution-run/result-validation decisions, reject
  commands outside task command policy, stop non-`stopped` result completion
  after the brake changes, run a controlled fixture, bind supplied run evidence
  to closeout, recheck real-executor invocation readiness without side effects,
  run one approved real executor command, bind audit-anchored real invocation
  evidence into closeout, compose the saved local and PR chains into controlled
  packets, plan merge readiness, and replay local audit history. Remaining gaps are
  host/session orchestration, autonomous Git/PR flow, merge authority, release
  governance, and package-publication governance.
- The handoff and task/epoch model is useful.
- Candidate discovery is deterministic and conservative.
- Adapter contracts are tested at the public CLI boundary.
- Readiness packets now distinguish `local_only`, `saved_input`, `stale`, and
  caller-asserted `live_like` evidence, and read-only live GitHub evidence can
  be captured into saved PR and review-thread files for later deterministic
  readiness, candidate-discovery, and review-response planning commands.
- Saved actionable review-thread evidence can now seed bounded follow-up
  candidates without calling GitHub, posting comments, resolving threads, or
  bypassing approval, ownership, or executor gates.
- Named host/session orchestration, autonomous PR automation, live review
  response writes, continuous loop runner, merge governance, release
  governance, and package-publication governance are not built.

The rating should stay low until a controlled loop can make a real change in a
fixture repo, validate it, record evidence, and stop cleanly.

## Update Rule

Update this document whenever a PR changes what the loop can actually do, where
it stops, what fails first, or the confidence rating.
