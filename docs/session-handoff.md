# Current Session Handoff

Last updated: 2026-06-03

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `afe23bf3ba1c1447c31f6f882636aaaa0f0e7176` after PR #70 merged.
- Working branch intent: update roadmap and handoff documentation after Tasks 1-7 completed, then hand off to Task 8 from `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`.
- Recent merged PRs: PR #56 implemented the read-only `audit-replay` CLI path; PR #57 updated this handoff after PR #56 merged; PR #58 merged the command-policy and active-stop control slice; PR #59 hardened command-policy review findings; PR #60 added the dry-run Git/PR planning design; PR #61 implemented dry-run-only `git-pr-plan`; PR #62 added the next-five-tasks roadmap; PR #63 refreshed this handoff and seeded the active business-memory backlog; PR #64 added and hardened the controlled executor fixture; PR #66 wired local executor closeout and next-decision logic; PR #67 merged local `branch_policy` for loop policy, task packets, and dry-run Git/PR planning; PR #68 merged read-only GitHub evidence sync and feedback candidates; PR #69 merged operator-approved Git/PR materialization; PR #70 merged read-only resume verification and follow-up hardening.
- Current branch scope: `codex/task-8-12-roadmap-handoff` creates the Tasks 8-12 roadmap, marks the Tasks 1-7 roadmap complete, refreshes current-state living docs, and updates this handoff. It must not add runtime behavior, executor invocation, branch/PR automation changes, auto-merge, release, or package publication.

## Current Capability Baseline

- Local `cadence-loop-policy.v1` handling can bound emitted executor task packets with allowed and denied paths, commands, required checks, runtime limits, stop conditions, and dry-run branch policy.
- Executor task packets carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- Executor task packets can carry `branch_policy`, and `git-pr-plan` can block dry-run plans that violate allowed base branches, denied target branches, required branch prefixes, or a current `main` checkout when `allow_current_branch_main` is false.
- `github-evidence-sync` can explicitly fetch read-only PR metadata, status checks, and review threads through `gh`, then save local PR JSON, review-thread JSON, and a summary packet for deterministic follow-on commands.
- `git-pr-materialize` can consume a reviewed `git-pr-plan.v1` packet and matching target-bound HMAC operator approval token backed by `CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`, re-run the local plan gates, create the proposed branch from the already-materialized current commit without switching the checkout, push it with Git hook verification disabled for that push, create or update a PR through `gh`, and append `git_pr_materialization_intent` plus `git_pr_materialization_result` audit records.
- `verify-resume` can emit a read-only `resume-verification.v1` packet that checks handoff signature and claimed state, clean-square evidence, persisted resume snapshot binding, repo branch/head, dirty-worktree state, active brake, active epoch state, and pickup-policy evidence before a fresh session continues.
- Candidate discovery can ingest saved PR JSON through `--pr-json-file` and convert failing checks into stable `pr_check_failure` execution candidates.
- `pr-readiness --review-threads-file` can block unresolved actionable current review comments plus malformed or incomplete saved GraphQL `reviewThreads` JSON while ignoring resolved, outdated, and non-actionable feedback.
- `validate-executor-result` checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- Command policy is hardened around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- `audit-replay` emits a read-only local packet for `cadence-audit.v1` JSONL history and reports stable blockers for corrupt or unsupported records, including materialization intent/result audit events.
- `run-controlled-executor-fixture` can launch the bundled fake external executor fixture from an explicit current-Python, absolute-script command template in tests/examples, validate its task packet and command before start, require expected result evidence under the runtime root, reject stale result files, and append `executor_fixture_invocation` plus `executor_result_validation` audit records.
- Disabled executor permissions now also reject merge, release, and package-publication command forms, including `gh pr merge`, `gh release create`, `gh release upload`, mutating `git tag` forms while allowing read-only tag listing/verification, `twine upload`, Python launcher `-m twine upload` forms including versioned `python3.x`, `npm publish`, `pnpm publish`, `yarn publish`, `yarn npm publish`, `poetry publish`, `uv publish`, `hatch publish`, and `flit publish`.
- Dry-run-only `git-pr-plan` is merged. It turns validated executor evidence into proposed branch, commit, PR title, and PR body text without creating a branch, committing, pushing, calling GitHub, opening a pull request, merging, releasing, or publishing packages.
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` is complete for Tasks 1-7.
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` is the current public planning artifact for the next bounded slices.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Branch policy is carried in task packets so dry-run Git/PR planning checks the approved branch bounds; an extra local `git-pr-plan --policy-file` may add restrictions but does not perform live Git/PR actions.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- `git-pr-plan` remains dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
- `git-pr-plan` readiness preserves fail-closed gates: brake-gated success needs the current brake check, `files_changed` alone is not materialized-change evidence, `materialized_change_evidence` must be explicit, and absent materialized evidence blocks Git/PR readiness.
- `git-pr-materialize` is the only Task 6 write-side Git/PR path. Missing, mismatched, or unverifiable target-bound HMAC operator approval, stale plan evidence, dirty worktree state, branch-policy blockers, incomplete materialized evidence, or PR body preflight blockers must stop before branch creation, push, PR create/update, or audit records. After approval gates pass, intended and completed side effects must be audit replayable.
- `github-evidence-sync` is read-only live evidence capture. It may write local evidence files only as a complete set, but it must not start GitHub writes, create or edit pull requests, create branches, commit, push, merge, release, or publish packages.
- The active business-memory backlog entry is discovery input only. It does not authorize executor invocation, code modification, branch creation, commits, pushes, PR creation, merges, releases, package publication, or paid review spending.
- The controlled fake executor fixture is merged, but it is still only a tests/examples component. No real executor invocation, branch/PR automation, write-side GitHub sync, merge authority, release behavior, hash chain, authenticated approval identity, or package-publication authority is available from that fixture.
- Real executor invocation remains blocked even though resume verification, controlled fixture execution, result validation, local closeout, read-only GitHub evidence sync, and operator-approved PR materialization exist. The next approved work should add a governed execution-start gate before any real executor or named host adapter is allowed.
- Keep public docs free of private machine paths and private repository assumptions.

## Validation To Re-run

```powershell
git status -sb
python -m py_compile scripts/validate_protocol.py
python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Next Action

Start Task 8 from `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`: add the governed execution start gate that consumes a reviewed executor task packet, rechecks repo/policy/brake/approval state, starts one active epoch, emits stable blocker packets, and still reports `executor_started: false`. Real executor invocation, autonomous code modification, autonomous Git/PR writes, auto-merge, release, and package publication remain outside Task 8.
