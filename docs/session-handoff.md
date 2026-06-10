# Current Session Handoff

Last updated: 2026-06-10

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `2de6cdcc1729a5c088ce14fbe447d09607b52c56` after PR #87 merged.
- Working branch intent: implement Task 20 read-only real executor invocation plan and approval binding before any real executor process-start path exists.
- Recent merged PRs: PR #72 merged governed execution start; PR #73 merged execution-run evidence binding to closeout; PR #74 merged read-only review response planning; PR #75 merged GitHub Actions cost controls; PR #76 merged read-only resume continuation; PR #77 merged local work ownership status and validation; PR #78 prepared the Tasks 13-17 roadmap and post-Task-12 handoff docs; PR #79 merged local work ownership claim and closeout; PR #80 merged ownership-bound governed execution start; PR #81 merged ownership-bound resume continuation; PR #82 merged read-only role-readiness and review-separation evidence; PR #83 refreshed living docs for Task 17 handoff; PR #84 merged read-only executor invocation readiness evidence; PR #85 merged the Tasks 18-22 roadmap; PR #86 merged audit hash-chain integrity evidence; PR #87 merged authenticated operator approval identity evidence.
- Completed roadmap marker: Tasks 13-17 from `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` are complete in `main`, including `work-ownership-claim.v1`, `work-ownership-closeout.v1`, ownership-bound `execution-start.v1`, ownership-aware `resume-continuation.v1`, read-only `role-readiness.v1`, and read-only `executor-invocation-readiness.v1` packet evidence.
- Current branch scope: `codex/task-20-real-executor-invocation-plan` adds
  read-only `executor-invocation-plan.v1` evidence that binds readiness,
  approval identity, audit-chain, adapter, rollback, command, environment,
  timeout, cwd, active epoch, active ownership, and result-path anchors. It
  must not invoke a real executor, modify code through an executor, create
  branches outside this task branch, write GitHub state beyond the
  operator-approved PR for this slice, merge, release, or publish packages.

## Current Capability Baseline

- Local `cadence-loop-policy.v1` handling can bound emitted executor task packets with allowed and denied paths, commands, required checks, runtime limits, stop conditions, and dry-run branch policy.
- Executor task packets carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- Executor task packets can carry `branch_policy`, and `git-pr-plan` can block dry-run plans that violate allowed base branches, denied target branches, required branch prefixes, or a current `main` checkout when `allow_current_branch_main` is false.
- `start-governed-execution` can consume a reviewed `generic-executor-task.v1`
  packet with exact checksum approval, recheck repo path, branch, `HEAD`,
  clean worktree, task-carried command and branch policy shape, active brake,
  active epoch state, and supplied local ownership evidence, bind matching
  active ownership to the started epoch, emit `execution-start.v1`, and append
  `execution_start_decision` audit evidence while reporting
  `executor_started: false`.
- `run-controlled-executor-fixture` writes a local `execution-run.v1` record
  under `<root>/execution-runs/` that binds task checksum, invocation id, result
  evidence checksum, validation packet checksum, repo path/branch/head anchors,
  and pending closeout status.
- `closeout-executor-result --run-record-file` can reject mismatched or partial
  run records before epoch mutation and update accepted records with closeout
  status, epoch id/status, and closeout checksum.
- `github-evidence-sync` can explicitly fetch read-only PR metadata, status checks, and review threads through `gh`, then save local PR JSON, review-thread JSON, and a summary packet for deterministic follow-on commands.
- `git-pr-materialize` can consume a reviewed `git-pr-plan.v1` packet and matching target-bound HMAC operator approval token backed by `CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`, re-run the local plan gates, create the proposed branch from the already-materialized current commit without switching the checkout, push it with Git hook verification disabled for that push, create or update a PR through `gh`, and append `git_pr_materialization_intent` plus `git_pr_materialization_result` audit records.
- `verify-resume` can emit a read-only `resume-verification.v1` packet that checks handoff signature and claimed state, clean-square evidence, persisted resume snapshot binding, repo branch/head, dirty-worktree state, active brake, active epoch state, and pickup-policy evidence before a fresh session continues.
- `resume-continuation` can consume a saved `resume-verification.v1` packet,
  recheck handoff id, claimer, repo branch/head, dirty-worktree state, active
  brake, active epoch state, clean-square evidence, pickup-policy evidence,
  packet freshness, and supplied active ownership evidence, then emit a
  read-only `resume-continuation.v1` packet that recommends
  `start_governed_execution` only when anchors still match.
- `work-ownership-status` and `validate-work-ownership` can read local
  `work-ownership.v1` records under
  `<root>/work-ownership/{active,closed,failed}`, validate task/candidate/role,
  claimer, repo, branch, optional PR/epoch/handoff, status, and timestamp
  fields, emit `work-ownership-status.v1` and
  `work-ownership-validation.v1` packets, and report stable blockers for
  duplicate active ownership, stale active ownership, closed evidence,
  malformed records, and repo/branch/task mismatches.
- Candidate discovery can ingest saved PR JSON through `--pr-json-file` and convert failing checks into stable `pr_check_failure` execution candidates.
- `pr-readiness --review-threads-file` can block unresolved actionable current review comments plus malformed or incomplete saved GraphQL `reviewThreads` JSON while ignoring resolved, outdated, and non-actionable feedback.
- `review-response-plan` can emit read-only `review-response-plan.v1` packets
  from saved PR JSON, saved review-thread JSON,
  optional candidate discovery output, and PR-body evidence, then group failed
  checks, unresolved actionable current review comments, missing PR body
  sections, and candidate matches into bounded read-only next-action
  recommendations.
- `role-readiness` can emit read-only `role-readiness.v1` packets from
  `role-policy.v1`, local ownership status, saved PR JSON, and saved
  review-thread evidence, then verify allowed ownership role labels and
  builder/reviewer separation without calling GitHub or mutating PR state.
- `executor-invocation-readiness` can emit read-only
  `executor-invocation-readiness.v1` packets from a reviewed executor task,
  active epoch, active ownership evidence, expected result path, and optional
  role-readiness evidence, then recheck repo path, branch, `HEAD`, dirty
  worktree, active brake, task checksum, ownership epoch binding, policy shape,
  required checks, and result-path boundaries while reporting
  `executor_started: false`.
- `validate-executor-result` checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- Command policy is hardened around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- `audit-replay` emits a read-only local packet for `cadence-audit.v1` JSONL history, reports chain head/count evidence for `cadence-audit-chain.v1` records, treats older unchained records as explicit legacy roots, and reports stable blockers for corrupt, unsupported, or hash-chain-invalid records, including execution-run and materialization intent/result audit events.
- `verify-operator-approval` verifies local `operator-approval.v1` packets for
  target checksum, purpose, operator id, key id, timestamps, and HMAC signature,
  emits `operator-approval-verification.v1`, appends
  `operator_approval_verification` audit evidence when accepted, and reports
  `executor_started: false`.
- `executor-invocation-plan` emits read-only
  `executor-invocation-plan.v1` packets that consume fresh successful
  `executor-invocation-readiness.v1`, purpose-scoped `operator-approval.v1`,
  clean audit replay, adapter metadata, rollback evidence, command,
  environment allowlist, timeout, cwd, active epoch, active ownership, and
  expected result path evidence, then recommend `invoke_real_executor` only
  when all anchors still match while reporting `executor_started: false`.
- `run-controlled-executor-fixture` can launch the bundled fake external executor fixture from an explicit current-Python, absolute-script command template in tests/examples, validate its task packet and command before start, require expected result evidence under the runtime root, reject stale result files, and append `executor_fixture_invocation` plus `executor_result_validation` audit records.
- Disabled executor permissions now also reject merge, release, and package-publication command forms, including `gh pr merge`, `gh release create`, `gh release upload`, mutating `git tag` forms while allowing read-only tag listing/verification, `twine upload`, Python launcher `-m twine upload` forms including versioned `python3.x`, `npm publish`, `pnpm publish`, `yarn publish`, `yarn npm publish`, `poetry publish`, `uv publish`, `hatch publish`, and `flit publish`.
- Dry-run-only `git-pr-plan` is merged. It turns validated executor evidence into proposed branch, commit, PR title, and PR body text without creating a branch, committing, pushing, calling GitHub, opening a pull request, merging, releasing, or publishing packages.
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` is complete for Tasks 1-7.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` is complete through Task 12.
- Task 17 from `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` is complete in `main` via PR #84.
- This branch implements Task 20 from
  `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`; the PR for this branch
  should review read-only real executor invocation planning and approval
  binding evidence.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Branch policy is carried in task packets so dry-run Git/PR planning checks the approved branch bounds; an extra local `git-pr-plan --policy-file` may add restrictions but does not perform live Git/PR actions.
- The governed execution-start gate validates task-carried command and branch
  policy fields from the reviewed packet and carries them into the epoch; it
  does not reread a mutable policy file. Its approval token is checksum review
  evidence only; it remains backward compatible until a later migration consumes
  `operator-approval.v1` evidence.
- `verify-operator-approval` is identity evidence only. Accepted approval
  verification is not executor authority, Git/PR materialization authority,
  merge authority, release authority, or package-publication authority.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- `git-pr-plan` remains dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
- `git-pr-plan` readiness preserves fail-closed gates: brake-gated success needs the current brake check, `files_changed` alone is not materialized-change evidence, `materialized_change_evidence` must be explicit, and absent materialized evidence blocks Git/PR readiness.
- `git-pr-materialize` is the only Task 6 write-side Git/PR path. Missing, mismatched, or unverifiable target-bound HMAC operator approval, stale plan evidence, dirty worktree state, branch-policy blockers, incomplete materialized evidence, or PR body preflight blockers must stop before branch creation, push, PR create/update, or audit records. After approval gates pass, intended and completed side effects must be audit replayable.
- `github-evidence-sync` is read-only live evidence capture. It may write local evidence files only as a complete set, but it must not start GitHub writes, create or edit pull requests, create branches, commit, push, merge, release, or publish packages.
- `review-response-plan` is local response planning only. It must not resolve
  review threads, post comments, update PR bodies, invoke review agents, spend
  paid review, start executors, create branches, commit, push, merge, release,
  or publish packages.
- `resume-continuation` is local read-only continuation planning only. It must
  not claim handoffs, launch sessions, start epochs, invoke executors, create
  branches, commit, push, merge, release, or publish packages.
- Local work ownership is evidence only. Execution-start and
  resume-continuation can consume supplied active ownership records, but those
  records are not distributed locks and do not assign roles, schedule agents,
  or write GitHub issues.
- `role-readiness` is evidence only. It verifies local role policy and saved
  review-thread separation evidence, but it does not assign roles, schedule
  agents, call GitHub, invoke paid review, resolve review threads, or mutate
  PR state.
- `executor-invocation-readiness` is evidence only. It can recommend
  `invoke_real_executor`, but it does not start a process, emit process
  metadata, modify code, create branches, commit, push, write PRs, merge,
  release, publish packages, assign roles, schedule agents, or write GitHub
  state.
- `executor-invocation-plan` is evidence only. It can recommend
  `invoke_real_executor` after binding fresh readiness, approval, audit,
  adapter, rollback, command, timeout, epoch, ownership, and result-path
  evidence, but it does not start a process, append audit records, modify code,
  create branches, commit, push, write PRs, merge, release, publish packages,
  assign roles, schedule agents, or write GitHub state.
- The active business-memory backlog entry is discovery input only. It does not authorize executor invocation, code modification, branch creation, commits, pushes, PR creation, merges, releases, package publication, or paid review spending.
- The controlled fake executor fixture is merged, but it is still only a tests/examples component. No real executor invocation, branch/PR automation, write-side GitHub sync, merge authority, release behavior, hash chain, authenticated approval identity, or package-publication authority is available from that fixture.
- Real executor invocation remains blocked even though governed execution
  start, resume verification, resume-continuation, controlled fixture
  execution, local execution-run records, result validation, local closeout,
  read-only GitHub evidence sync, and operator-approved PR materialization
  exist. A valid local run record is evidence for closeout, not authority to
  run a real executor or named host adapter.
- Keep public docs free of private machine paths and private repository assumptions.

## Validation To Re-run

```powershell
git status -sb
python -m py_compile codex_cadence/executor_invocation.py codex_cadence/executor_readiness.py codex_cadence/approvals.py codex_cadence/policy_audit.py codex_cadence/cli.py tests/test_cadence.py
python -m unittest tests.test_cadence -v
python -m unittest tests.test_executor_contract tests.test_epochs tests.test_ci_checks -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

For this branch, use the Task 20 invocation-plan validation block above. The
Task 17 runtime validation block remains recorded in
`docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` for the merged
`executor-invocation-readiness.v1` slice.

## Next Action

Finish review on the Task 20 PR for
`codex/task-20-real-executor-invocation-plan`, address any new findings, and
merge only after checks and review are clean. Task 21 from
`docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`, controlled real executor
invocation runner, is the next bounded slice after this branch. Code
modification by an executor, dirty-worktree commit, autonomous push, PR writes
outside the approved PR for this task, role assignment, agent pools, GitHub
issue assignment, shared runtimes, distributed locks, release, and package
publication remain outside this branch.
