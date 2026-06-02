# Current Session Handoff

Last updated: 2026-06-02

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `eb7baa1de70c743ec511913c5564e6fdf58de11c` after PR #64 merged.
- Working branch intent: implement Task 3 from `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` by wiring validated executor results into epoch closeout and next-decision logic.
- Recent merged PRs: PR #56 implemented the read-only `audit-replay` CLI path; PR #57 updated this handoff after PR #56 merged; PR #58 merged the command-policy and active-stop control slice; PR #59 hardened command-policy review findings; PR #60 added the dry-run Git/PR planning design; PR #61 implemented dry-run-only `git-pr-plan`; PR #62 added the next-five-tasks roadmap; PR #63 refreshed this handoff and seeded the active business-memory backlog; PR #64 added and hardened the controlled executor fixture.
- Current branch scope: no active implementation branch is open. The next branch should start Task 3 and may refresh planning docs as needed; it must not add real executor invocation, branch policy, live GitHub sync, branch creation, commits, pushes, pull requests, merges, releases, or package-publication behavior.

## Current Capability Baseline

- Local `cadence-loop-policy.v1` handling can bound emitted executor task packets with allowed and denied paths, commands, required checks, runtime limits, and stop conditions.
- Executor task packets carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- `validate-executor-result` checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- Command policy is hardened around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- `audit-replay` emits a read-only local packet for `cadence-audit.v1` JSONL history and reports stable blockers for corrupt or unsupported records.
- `run-controlled-executor-fixture` can launch the bundled fake external executor fixture from an explicit current-Python, absolute-script command template in tests/examples, validate its task packet and command before start, require expected result evidence under the runtime root, reject stale result files, and append `executor_fixture_invocation` plus `executor_result_validation` audit records.
- Disabled executor permissions now also reject merge, release, and package-publication command forms, including `gh pr merge`, `gh release create`, `gh release upload`, mutating `git tag` forms while allowing read-only tag listing/verification, `twine upload`, Python launcher `-m twine upload` forms including versioned `python3.x`, `npm publish`, `pnpm publish`, `yarn publish`, `yarn npm publish`, `poetry publish`, `uv publish`, `hatch publish`, and `flit publish`.
- Dry-run-only `git-pr-plan` is merged. It turns validated executor evidence into proposed branch, commit, PR title, and PR body text without creating a branch, committing, pushing, calling GitHub, opening a pull request, merging, releasing, or publishing packages.
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` is the current public planning artifact for the next bounded slices; Tasks 1 and 2 are merged, Tasks 3-5 remain, and Tasks 6-7 are planned follow-on gates.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- `git-pr-plan` remains dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
- `git-pr-plan` readiness preserves fail-closed gates: brake-gated success needs the current brake check, `files_changed` alone is not materialized-change evidence, `materialized_change_evidence` must be explicit, and absent materialized evidence blocks Git/PR readiness.
- The active business-memory backlog entry is discovery input only. It does not authorize executor invocation, code modification, branch creation, commits, pushes, PR creation, merges, releases, package publication, or paid review spending.
- The controlled fake executor fixture is merged, but it is still only a tests/examples component. No real executor invocation, branch policy, branch/PR automation, live GitHub sync, merge authority, release behavior, hash chain, authenticated approval identity, or package-publication authority is available from that fixture.
- Real executor invocation remains blocked until epoch closeout, branch policy, audit, branch/PR materialization policy, and result evidence gates are stable and covered by tests.
- Keep public docs free of private machine paths and private repository assumptions.

## Validation To Re-run

```powershell
git status -sb
python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/executor_contract.py codex_cadence/git_pr_plan.py
python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_git_pr_plan -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```

## Next Action

Start Task 3 from `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`: wire validated executor results into epoch closeout and next-decision logic. Keep the slice bounded to local packets, epoch state, audit, and dry-run next decisions; live Git/PR actions, real executor invocation, merge, release, and package publication remain outside scope.
