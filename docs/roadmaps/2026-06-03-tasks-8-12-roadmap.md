# Tasks 8-12 Roadmap

> **For agentic workers:** Use `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` as the public planning artifact after Tasks 1-7. Implement each task on its own branch and update the living docs with evidence before merge.

**Goal:** Move Agentic Cadence from separate governance primitives toward a first governed execution pickup path without adding hidden real-executor, autonomous GitHub, merge, release, or package-publication authority.

**Architecture:** Cadence remains the governor. It may start bounded local state transitions, emit packets, validate evidence, and materialize operator-approved PR plans, but implementation executors remain separate replaceable components.

**Tech Stack:** Python 3.11+, stdlib CLI, local JSON packets, Git, optional `gh` for explicit read-only sync and operator-approved PR create/update actions, `unittest`, GitHub Actions.

---

## Baseline Captured On 2026-06-04

- Local `main` is merged through PR #73 at
  `6371a74379f8ed3f9642bb00cea24b0302205e9b`.
- Tasks 1-7 from `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` are complete.
- PR #69 completed Task 6 by adding operator-approved `git-pr-materialize`.
- PR #70 completed Task 7 by adding read-only `verify-resume` and hardening review findings.
- PR #71 completed the Tasks 8-12 planning handoff from the previous roadmap.
- PR #72 completed Task 8 by adding governed execution-start epoch gating.
- PR #73 completed Task 9 by adding local execution-run evidence binding.
- PR #74 completed Task 10 by adding read-only review feedback response
  planning.
- PR #75 completed GitHub Actions cost controls.
- Task 8 current-tree work adds `start-governed-execution` and
  `execution-start.v1` epoch-start gating while keeping real executor
  invocation out of scope.
- Task 9 current-tree work adds local `execution-run.v1` records and
  supplied-run-record closeout binding while keeping real executor invocation
  out of scope.
- Task 10 current-tree work adds read-only `review-response-plan.v1` packets
  while keeping GitHub writes, review-agent invocation, and executor invocation
  out of scope.
- Task 11 current-tree work adds read-only `resume-continuation.v1` packets
  while keeping session launch, epoch start, executor invocation, and Git/PR
  writes out of scope.
- Current unattended-operation confidence remains 10%.

## Phase Ladder

Tasks 8-12 continue the same staged path:

1. **Phase 2: Governed execution** - connect existing task packets, epochs, approvals, and stop controls into an execution-start boundary.
2. **Phase 3: Git/PR governance** - keep PR writes explicit, approved, and evidence-backed while improving readiness and feedback planning.
3. **Phase 4: Multiple workers** - introduce local ownership records before any distributed agent pool or role-aware assignment system.
4. **Phase 5: Agent-team orchestration** - leave GitHub-native work assignment, role separation enforcement, autonomous merge, release, and package publication for future approved slices.

## Task 8: Add Governed Execution Start Gate

**Status:** Complete in current tree.

**Phase:** Phase 2 governed execution.

**Why now:** `loop-tick --emit-executor-task` can produce a bounded executor task packet, and Task 8 adds the missing local bridge from an approved task packet into one active epoch start while preserving policy, brake, repo, and audit gates.

**Files:**
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/epochs.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_cadence.py`, `tests/test_epochs.py`, `tests/test_executor_contract.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Added `start-governed-execution`, an explicit command that consumes a reviewed
  `generic-executor-task.v1` packet and starts an active epoch only when
  current repo path, branch, head, dirty-worktree state, task-carried command
  and branch policy shape, brake state, and approval gates still match.
- Emits a stable `execution-start.v1` packet with `epoch_started`,
  `executor_started: false`, `read_only: false`, stable blocker codes, and a
  recommended next action.
- Appends a compact `execution_start_decision` audit record for the approved
  execution-start decision.
- Keeps real executor invocation, code modification, branch creation, commits,
  pushes, PR writes, merge, release, and package publication outside this
  slice.

**Validation:**
- Valid approved task packet starts one active epoch and records audit evidence.
- Existing active epoch blocks before writing.
- Stale branch/head, dirty worktree, invalid or non-`DRIVE` brake, missing
  approval, malformed task packet, low-confidence snapshot, and audit-append
  failure block with stable packet codes.
- Missing repo paths and malformed active-epoch state block with stable packet
  codes.
- The command does not invoke an executor and reports `executor_started: false`.

Run:

```powershell
python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/executor_contract.py codex_cadence/policy_audit.py
python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 9: Bind Execution Run Evidence To Closeout

**Status:** Complete in current tree.

**Phase:** Phase 2 governed execution hardening.

**Why now:** Controlled fixture invocation and result validation audit records exist, but closeout still relies on local task/result/snapshot files supplied by the caller. A stable run ledger should bind fixture invocation, result validation, repo anchors, and epoch closeout evidence before live executors are considered.

**Files:**
- Modify: `codex_cadence/executor_runner.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/epochs.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/store.py`
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_cadence.py`, `tests/test_epochs.py`, `tests/test_executor_contract.py`, `tests/test_audit_replay.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Added local `execution-run.v1` records that bind task checksum, invocation
  id, result evidence checksum, validation packet checksum, repo path/branch/head
  anchors, and closeout status.
- `run-controlled-executor-fixture` now writes a local run record under
  `<root>/execution-runs/`, returns its path/checksum, and appends an
  `execution_run_record` audit event.
- `closeout-executor-result --run-record-file` validates supplied canonical
  runtime-local run records before epoch mutation and rejects malformed files,
  non-canonical paths, task checksum, result checksum, invocation/validation
  checksum, repo anchor, partial-record, and closeout-replay mismatches with
  stable blocker codes.
- Successful closeout updates the local run record with closeout status,
  epoch id/status, and closeout checksum, then appends an
  `execution_run_record` audit event for the update.
- Kept run records local and auditable; no remote backend, distributed lock, or
  real executor invocation was added.

**Validation:**
- Fixture success produces a run record that closeout accepts.
- Mismatched task checksum, result checksum, repo anchors, validation packet, invocation id, run-record path, or closeout replay blocks with stable codes.
- Partial run records fail closed.
- Audit replay recognizes the new compact record type.

Run:

```powershell
python -m py_compile codex_cadence/executor_runner.py codex_cadence/executor_contract.py codex_cadence/epochs.py codex_cadence/policy_audit.py codex_cadence/cli.py codex_cadence/store.py
python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_audit_replay -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 10: Add Review Feedback Response Plan

**Status:** Complete in current tree.

**Phase:** Phase 3 Git/PR governance.

**Why now:** `github-evidence-sync`, `pr-readiness`, and candidate discovery can identify failing checks and unresolved actionable review comments. Cadence still lacks a packet that turns saved feedback evidence into a bounded response plan without writing to GitHub.

**Files:**
- Create: `codex_cadence/review_response.py`
- Modify: `codex_cadence/cli.py`
- Reuse: `codex_cadence/pr_readiness.py`, `codex_cadence/github_evidence.py`
- Test: `tests/test_pr_readiness.py`, `tests/test_candidates.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`, `docs/cadence/business-memory.md`

**Implementation outline:**
- Added a read-only `review-response-plan.v1` packet that consumes saved PR JSON, saved review-thread JSON, and optional candidate discovery output.
- Groups actionable current feedback by check name, review thread, file path, and likely follow-up task.
- Recommends `emit_executor_task`, `refresh_pr_evidence`, `update_pr_body`, `wait_for_checks`, or `operator_review` without resolving GitHub comments.
- Keeps GitHub writes, review-thread resolution, branch creation, commits, pushes, merge, release, package publication, paid review spending, and review-agent invocation outside this slice.

**Validation:**
- Failed checks produce bounded response-plan items with stable fingerprints.
- Unresolved actionable current review comments produce response-plan items.
- Resolved, outdated, summary-only, and non-actionable comments are ignored.
- Stale saved PR evidence recommends refresh before acting.
- Malformed or incomplete review-thread evidence blocks with stable codes.

Run:

```powershell
python -m py_compile codex_cadence/review_response.py codex_cadence/cli.py codex_cadence/pr_readiness.py codex_cadence/github_evidence.py codex_cadence/candidates.py
python -m unittest tests.test_pr_readiness tests.test_candidates -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 11: Add Resume-To-Execution Continuation Gate

**Status:** Complete in current tree.

**Phase:** Phase 2 governed execution across sessions.

**Why now:** `verify-resume` can prove a fresh session may resume a claimed handoff, but no packet binds a specific successful resume verification to a subsequent execution-start decision. The next session still needs a deterministic bridge from pickup evidence to governed work start.

**Files:**
- Modify: `codex_cadence/handoff_loop.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/epochs.py`
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_handoff_loop.py`, `tests/test_cadence.py`, `tests/test_epochs.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Added a local `resume-continuation.v1` gate that consumes a saved
  `resume-verification.v1` packet and rechecks handoff id, claimer, repo
  branch/head, dirty-worktree state, active brake, active epoch state,
  clean-square evidence, pickup-policy evidence, and packet freshness.
- The packet recommends `start_governed_execution`, `claim_handoff`,
  `approve_handoff`, `recreate_handoff`, `close_or_fail_active_epoch`, or
  `inspect_resume_blockers`.
- It reports `read_only: true`, `executor_started: false`,
  `epoch_started: false`, and `side_effects: []`.
- It does not launch a new session, invoke an executor, start an epoch, or claim
  handoffs implicitly; any state mutation remains an explicit public CLI action.

**Validation:**
- Fresh matching resume verification recommends governed execution start.
- Stale resume packet, mismatched repo head, different claimer, missing clean-square, policy mismatch, active stop, and active epoch conflict block.
- The command does not claim handoffs, launch sessions, invoke executors, create branches, push, open PRs, merge, release, or publish packages.

Run:

```powershell
python -m py_compile codex_cadence/handoff_loop.py codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/policy_audit.py
python -m unittest tests.test_handoff_loop tests.test_cadence tests.test_epochs -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 12: Add Local Work Ownership Registry

**Status:** Implemented in current tree.

**Phase:** Phase 4 preparation, local-only.

**Why now:** Before multiple workers or role-aware assignments exist, Cadence needs a local ownership record that can show which task, branch, PR, epoch, handoff, and claimer are associated. This should prevent duplicate local starts without pretending to be a distributed lock.

**Files:**
- Create: `codex_cadence/ownership.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/store.py`
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_cadence.py`, `tests/test_ci_checks.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Add local `work-ownership.v1` records under the runtime root with task id, candidate id, role label, claimer, repo, branch, optional PR number, optional epoch id, optional handoff id, status, and timestamps.
- Add read-only `work-ownership-status` and `validate-work-ownership` commands for ownership records.
- Emit duplicate-ownership blockers from the ownership status and validation commands. Leave execution-start and resume-continuation enforcement as a later explicit integration point, and do not enforce distributed locks.
- Keep role assignment, agent pool scheduling, GitHub issue assignment, shared runtime, merge authority, release authority, and package publication outside this slice.

**Validation:**
- Valid local ownership records validate and surface in status packets.
- Duplicate active ownership for the same task/branch blocks ownership status recommendations without mutating execution-start or resume-continuation gates in this slice.
- Malformed, stale, closed, or repo-mismatched ownership evidence returns stable blockers.
- Records remain local filesystem evidence and do not call GitHub.

**Current-tree evidence:**
- `codex_cadence/ownership.py` validates `work-ownership.v1` records and emits `work-ownership-status.v1` / `work-ownership-validation.v1`.
- `codex_cadence/store.py` exposes `work-ownership` state directories for active, closed, and failed records.
- `tests/test_cadence.py` covers valid active status, duplicate active ownership, closed evidence, stale evidence, and repo mismatch.

Run:

```powershell
python -m py_compile codex_cadence/ownership.py codex_cadence/cli.py codex_cadence/store.py codex_cadence/policy_audit.py
python -m unittest tests.test_cadence tests.test_ci_checks -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Recommended Order

1. Create the next roadmap after Task 12 merges, because the Tasks 8-12 roadmap
   is complete in the current tree.

## Boundaries For All Five Tasks

- No hidden executor invocation.
- No autonomous code modification.
- No dirty-worktree commit path.
- No autonomous branch creation, push, or PR create/update.
- No GitHub issue assignment, review-thread resolution, or comment writes.
- No auto-merge.
- No release or package publication.
- No named host adapter claim.
- No shared remote runtime or distributed lock claim.
