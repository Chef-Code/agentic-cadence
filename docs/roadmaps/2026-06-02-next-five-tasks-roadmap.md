# Next Seven Tasks Roadmap

> **For agentic workers:** Use `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` as a public planning artifact. Implement each task on its own branch and update the living docs with evidence before merge.

**Goal:** Move Agentic Cadence from a clean advisory baseline toward the first governed execution loop without adding hidden live side effects.

**Architecture:** Keep Cadence as the governor: it owns policy, evidence, audit, branch governance, handoff, and next-decision logic. Executors remain replaceable components that receive bounded task packets and return evidence; they do not become the product authority.

**Tech Stack:** Python 3.11+, stdlib CLI, local JSON packets, Git, optional `gh` only for explicitly requested read-only sync work, `unittest`, GitHub Actions.

---

## Phase Ladder

These tasks preserve the long-term orchestration path:

1. **Phase 1: Single executor** - prove one replaceable executor component can receive bounded work and return evidence.
2. **Phase 2: Governed execution** - bind execution to policy, audit, stop controls, epochs, handoff, and next-decision logic.
3. **Phase 3: Git/PR governance** - add branch policy, dry-run transition planning, read-only GitHub evidence, and feedback candidates before any live write action.
4. **Phase 4: Multiple workers** - later, introduce worker ownership and conflict controls after the single-executor path is governed.
5. **Phase 5: Agent-team orchestration** - later, coordinate role-aware Planning, Builder, Reviewer, QA, Documentation, Release, and Handoff agents through GitHub-native evidence.

Tasks 1-5 intentionally land mostly in Phases 1-3. Tasks 6-7 extend that local governance path without crossing into autonomous merge, release, package publication, or full agent-team orchestration. Phases 4-5 remain explicit future gates, not hidden scope inside the executor fixture.

## Evidence Captured On 2026-06-02

- Post-#64 local `main` was clean and synced at merge commit `eb7baa1`.
- PR #63 completed Task 1 by refreshing the handoff and seeding the active business-memory backlog.
- PR #64 completed Task 2 by adding and hardening the controlled executor component fixture.
- `python scripts/validate_protocol.py` passed after PR #64 merged.
- PR #66 completed Task 3 by wiring executor closeout into epoch state and next-decision logic.
- PR #67 completed Task 4 by carrying local branch policy into executor task packets and dry-run Git/PR planning.
- The remaining immediate work starts at Task 5: add read-only GitHub evidence sync and feedback candidates.

## Task 1: Refresh Handoff And Seed The Active Backlog

**Status:** Complete in PR #63.

**Why now:** Cadence has no active candidates because open issues, open PRs, and business-memory candidates are empty or fulfilled. Before implementation work starts, the repo should record the post-#61 baseline and seed the next bounded work item explicitly.

**Files:**
- Modify: `docs/session-handoff.md`
- Modify: `docs/cadence/business-memory.md`
- Modify if confidence wording changes: `docs/roadmap.md`, `docs/implementation-slices.md`
- Test: `tests/test_ci_checks.py`

**Implementation outline:**
- Update the handoff document to state that #61 is merged and `main` is at `209ee61`.
- Replace the old "finish git-pr-plan branch" next action with the selected next slice.
- Add one active business-memory entry for the controlled executor loop path. The entry should describe why real executor invocation remains blocked until policy, audit, branch/PR materialization, evidence freshness, resume verification, and result evidence gates are stable.
- Add or update tests only if protocol validators need stronger checks against stale handoff state.

**Validation:**
- `python scripts/validate_protocol.py`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo -v`
- `python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_are_closed_and_parse_without_warnings -v`
- `git diff --check`

## Task 2: Add A Controlled Executor Component Fixture

**Status:** Complete in PR #64.

**Phase:** Phase 1 single executor, entering Phase 2 governed execution.

**Why now:** The first hard stop in the readiness document is execution. The safe first step is not a named host adapter and not turning Cadence into an executor; it is a controlled fixture that proves Cadence can govern a fake external executor component through task policy and result evidence before real executor invocation is allowed.

**Files:**
- Create: `codex_cadence/executor_runner.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_executor_contract.py`, `tests/test_cadence.py`
- Example: `examples/controlled-executor-fixture/run.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Add a fixture-only command path that runs only in tests and examples against a disposable repo or explicit test-owned command template.
- Treat the invoked command as a fake external executor component. Cadence should launch, bound, observe, and validate it; Cadence should not absorb executor responsibilities into the orchestrator core.
- Write the task packet to the expected path, invoke the fixture command with a timeout, then require it to write the expected result evidence file.
- Enforce task-carried allowed paths, command policy, runtime limit, and stop conditions before accepting the result.
- Record audit for invocation start and result validation.
- Keep real executor invocation blocked until branch/PR materialization, evidence freshness, resume verification, and remaining result-evidence gates land.
- Keep commits, pushes, PR creation, release, merge, and package publication forbidden.
- Do not claim named-host adapter support.

**Validation:**
- Fake executor succeeds and writes valid evidence.
- Fake executor exits nonzero and produces failed evidence.
- Timeout returns stopped or failed evidence without accepting success.
- Active brake change rejects non-`stopped` completion.
- Command policy blocks disallowed executor command templates.
- The command does not commit, push, open a PR, merge, release, or publish packages.

Run:

```powershell
python -m py_compile codex_cadence/executor_runner.py codex_cadence/cli.py codex_cadence/executor_contract.py codex_cadence/policy_audit.py
python -m unittest tests.test_executor_contract tests.test_cadence -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 3: Wire Executor Results Into Epoch Closeout And Next Decision

**Status:** Merged in PR #66.

**Phase:** Phase 2 governed execution.

**Why now:** A validated executor result is currently a standalone packet. The loop needs to complete or fail an epoch and decide whether to stop, hand off, or produce a Git/PR plan.

**Files:**
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/epochs.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/git_pr_plan.py`
- Test: `tests/test_cadence.py`, `tests/test_epochs.py`, `tests/test_executor_contract.py`, `tests/test_git_pr_plan.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`

**Implementation outline:**
- Add a bounded command or `loop-tick` mode that consumes a validated result packet and the active epoch.
- Complete the epoch when evidence succeeded and validation passed.
- Fail the epoch when evidence failed, blocked, timed out, or violates policy.
- Emit the next decision: stop, handoff, validate more evidence, or generate a dry-run Git/PR plan.
- Keep live Git/PR actions outside this slice.
- Keep release and package publication outside this slice.

**Current-tree implementation:** `closeout-executor-result` consumes local
task/result/snapshot-after packets, validates executor evidence with the existing
contract, binds the task packet to the active epoch baseline snapshot, records
successful task completion without terminal closeout when other epoch tasks
remain, completes or fails terminal epochs, appends `executor_epoch_closeout`
audit with snapshot-after anchors, and optionally embeds a dry-run
`git-pr-plan.v1` packet after terminal success.

**Validation:**
- Success evidence completes the active epoch and emits a next decision.
- Failed or blocked evidence fails the active epoch with stable reason codes.
- Stale snapshot or head mismatch blocks closeout.
- Active epoch conflict is handled predictably.
- Re-running the same closeout cannot double-complete the epoch.

Run:

```powershell
python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/executor_contract.py codex_cadence/git_pr_plan.py
python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_git_pr_plan -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 4: Add Branch Policy To Local Loop Policy

**Status:** Merged in PR #67.

**Phase:** Phase 3 Git/PR governance, with policy carried back into Phase 2 execution packets.

**Why now:** The roadmap repeatedly identifies missing branch policy. It is the next safety boundary before executor output can become branch or PR work.

**Files:**
- Modify: `codex_cadence/policy_audit.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/git_pr_plan.py`
- Modify: `codex_cadence/cli.py`
- Test: `tests/test_cadence.py`, `tests/test_executor_contract.py`, `tests/test_git_pr_plan.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/implementation-slices.md`, `docs/roadmap.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Extend `cadence-loop-policy.v1` with a small `branch_policy` object.
- Start with fields that can be enforced locally: allowed base branches, denied target branches, required branch prefixes, and whether the current branch may be `main`.
- Copy branch policy into emitted executor task packets so later validation checks the approved policy, not a mutable policy file.
- Let `git-pr-plan` accept an optional policy file or task-carried branch policy and block generated plans that violate it.
- Keep all behavior dry-run and local.
- Keep live branch creation, commits, pushes, PR creation, release, merge, and package publication outside this slice.

**Current-tree implementation:** Local loop policy now accepts a normalized
`branch_policy` object with `allowed_base_branches`,
`denied_target_branches`, `required_branch_prefixes`, and
`allow_current_branch_main`. `loop-tick --emit-executor-task` copies the policy
into emitted executor task packets, executor task validation rejects malformed
task-carried branch policy, and `git-pr-plan` enforces task-carried plus
optional `--policy-file` branch policy as additive dry-run blockers. The slice
does not create branches, commit, push, call GitHub, open PRs, merge, release,
or publish packages.

**Validation:**
- Policy accepts valid branch policy.
- Policy rejects malformed branch policy.
- `loop-tick --emit-executor-task` carries branch policy into the task packet.
- `git-pr-plan` blocks a protected target branch, invalid prefix, and disallowed base branch.
- Existing no-policy behavior stays compatible.

Run:

```powershell
python -m py_compile codex_cadence/policy_audit.py codex_cadence/executor_contract.py codex_cadence/git_pr_plan.py codex_cadence/cli.py
python -m unittest tests.test_cadence tests.test_executor_contract tests.test_git_pr_plan -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 5: Add Read-Only GitHub Evidence Sync And Feedback Candidates

**Status:** Merged in PR #68.

**Phase:** Phase 3 Git/PR governance, preparing later Phase 4 worker coordination.

**Why now:** Review and CI feedback are already documented as the fifth confidence slice. Local saved review ingestion exists, but live fetching and failing-check candidate creation are missing.

**Files:**
- Create: `codex_cadence/github_evidence.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/candidates.py`
- Modify: `codex_cadence/pr_readiness.py`
- Test: `tests/test_candidates.py`, `tests/test_pr_readiness.py`, `tests/test_cadence.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Add an explicit read-only command that shells out to `gh` only when the operator asks for live PR evidence.
- Save normalized PR metadata, status checks, and review threads to local JSON evidence files with freshness labels.
- Keep failures deterministic: missing `gh`, auth failure, rate limit, or network failure must produce blocker packets rather than partial readiness claims.
- Extend candidate discovery so failing checks and unresolved current actionable review comments become bounded candidates.
- Ignore resolved, outdated, non-actionable, and summary-only feedback.
- Keep GitHub writes, branch changes, PR edits, merges, releases, and package publication outside this slice.

**Current-tree implementation:** `github-evidence-sync` explicitly shells out to
read-only `gh pr view` and GitHub GraphQL review-thread reads, labels the live
evidence, and writes saved PR JSON, saved review-thread JSON, and a summary
packet only after both live reads succeed and all local evidence files can be
written as a set. Missing `gh`, auth failure, rate limit, network failure,
GitHub CLI spawn failure, command timeout, malformed JSON, and incomplete
paginated review-thread evidence return blocked packets without partial evidence
files. Review-thread and comment pagination is followed before saved review
evidence is accepted. Candidate discovery can ingest saved PR JSON through
`--pr-json-file` to create stable `pr_check_failure` candidates from failed
check runs or status contexts, and
`pr-readiness --review-threads-file` blocks unresolved actionable current
review comments plus malformed or incomplete saved review-thread evidence.

**Validation:**
- Mocked `gh` success produces normalized saved evidence.
- Missing or failing `gh` returns a blocked packet with no candidate mutation.
- Failing check becomes a candidate.
- Resolved and outdated review comments are ignored.
- Unresolved actionable review comments block merge readiness and become candidates.
- Malformed JSON and incomplete paginated review-thread evidence block without
  saved partial evidence.
- No GitHub write actions, merge actions, release actions, or package publication actions are performed.

Run:

```powershell
python -m py_compile codex_cadence/github_evidence.py codex_cadence/cli.py codex_cadence/candidates.py codex_cadence/pr_readiness.py
python -m unittest tests.test_candidates tests.test_pr_readiness tests.test_cadence -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 6: Add Operator-Approved Git/PR Materialization

**Status:** Implemented in current tree; pending review and merge.

**Phase:** Phase 3 Git/PR governance, after branch policy and read-only GitHub evidence sync.

**Why now:** `git-pr-plan` is intentionally dry-run. Once branch policy and live read-only evidence are stable, Cadence needs an explicit operator-approved path that can materialize a reviewed plan into local branch, push, and PR creation/update actions without giving executors autonomous Git authority.

**Files:**
- Modify: `codex_cadence/git_pr_plan.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/policy_audit.py`
- Modify: `codex_cadence/pr_readiness.py`
- Test: `tests/test_git_pr_plan.py`, `tests/test_cadence.py`, `tests/test_pr_readiness.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Add an explicit command that consumes a validated `git-pr-plan.v1` packet, policy evidence, and operator confirmation.
- Enforce current branch/head, branch policy, materialized-change evidence, PR body preflight, and freshness gates immediately before any Git or `gh` action.
- Create the branch at the already-materialized current commit without switching the checkout, push it, and open or update a PR only when the operator approval token and policy gates match the packet and target under review.
- Append audit records for intended and completed side effects.
- Keep auto-merge, release, package publication, and real executor invocation outside this slice.

**Current-tree implementation:** `git-pr-materialize` consumes a saved
`git-pr-plan.v1` packet plus the exact target-bound
`approve-git-pr:<materialization-target-sha256>` operator approval token. Before
side effects it re-reads task/result provenance, verifies checksums, reruns
`git-pr-plan` with the approved proposed branch, rechecks current
branch/head/base/worktree freshness, branch policy, complete local-diff coverage
by materialized-change evidence, and PR body preflight, then blocks stale or
mismatched evidence before audit, branch, push, or PR actions. The token binds
the plan checksum, selected remote, resolved push URL, and create-vs-update PR
target. Existing PR updates also run a read-only `gh pr view` preflight to prove
the PR head and base match the approved packet. Once gates pass, it appends
`git_pr_materialization_intent`, creates the proposed branch from the
already-materialized current commit without switching the checkout, pushes to
the selected remote with Git hook verification disabled for that push, creates
or updates a PR through `gh`, and appends `git_pr_materialization_result`.
Failed Git or `gh` commands return `git-pr-materialization.v1` blocker packets
with command trace and replayable audit evidence. The command does not run
`git commit` against a dirty worktree, auto-merge, release, publish packages,
or invoke an executor.

**Validation:**
- Approved dry-run plan materializes into the expected mocked Git/PR command sequence.
- Missing or mismatched approval blocks before side effects.
- Branch policy, stale head, dirty worktree, missing materialized evidence, and PR body failures block before side effects.
- Failed Git or `gh` commands return stable blocker packets and audit evidence.
- No merge, release, or package-publication commands are performed.

Run:

```powershell
python -m py_compile codex_cadence/git_pr_plan.py codex_cadence/cli.py codex_cadence/policy_audit.py codex_cadence/pr_readiness.py
python -m unittest tests.test_git_pr_plan tests.test_cadence tests.test_pr_readiness -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Task 7: Add Resume Verifier And Handoff Pickup Gate

**Status:** Not started.

**Phase:** Phase 2 governed execution hardening, preparing future Phase 4 worker coordination.

**Why now:** As execution and PR planning span more sessions, a fresh session needs a deterministic gate that proves the handoff, clean-square, repo state, Cadence state, policy, and claimed work still match before continuing.

**Files:**
- Modify: `codex_cadence/handoff_loop.py`
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/repo_state.py`
- Modify: `codex_cadence/epochs.py`
- Test: `tests/test_cadence.py`, `tests/test_handoff_loop.py`, `tests/test_epochs.py`
- Docs: `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`, `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/decision-log.md`

**Implementation outline:**
- Add a read-only resume-verification command that checks handoff signature, claimed state, clean-square evidence, repo branch/head, dirty worktree state, active brake, active epoch, and policy evidence.
- Return a resume decision packet with stable blocker codes and recommended next action.
- Require stale or mismatched handoffs to be re-created or explicitly failed rather than silently resumed.
- Keep new-session launch, host context-pressure detection, real executor invocation, branch creation, PR writes, merge, release, and package publication outside this slice.

**Validation:**
- Valid handoff, clean-square, repo head, policy, and Cadence state produce a resumable packet.
- Stale SHA, wrong branch, dirty worktree, missing clean-square, missing approval, double claim, active stop, and active epoch conflict block resume with stable reason codes.
- The command is read-only and does not claim, complete, fail, or mutate handoffs.

Run:

```powershell
python -m py_compile codex_cadence/handoff_loop.py codex_cadence/cli.py codex_cadence/repo_state.py codex_cadence/epochs.py
python -m unittest tests.test_cadence tests.test_handoff_loop tests.test_epochs -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Recommended Order

1. Task 1, already complete in PR #63, refreshed the stale repo handoff and seeded the backlog.
2. Task 2, already complete in PR #64, proved the first executor-as-component path with a controlled fixture.
3. Task 3, because executor evidence must affect epochs and next decisions before the loop is meaningfully governed.
4. Task 4, because branch policy is the Git/PR governance boundary before executor output becomes branch or PR work.
5. Task 5, because review and CI feedback only become useful after the loop can consume post-execution state.
6. Task 6, because live Git/PR materialization should come only after branch policy and read-only GitHub evidence make the approval decision reviewable.
7. Task 7, because resume verification should harden continuation before the same governed loop spans more sessions, roles, or handoffs.
