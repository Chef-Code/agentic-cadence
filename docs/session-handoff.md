# Current Session Handoff

Last updated: 2026-06-02

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `6c6a3d4baf280541b75671f6e9a6a5d37abc895f` after PR #62 merged.
- Working branch intent: implement Task 1 from `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` by refreshing this handoff and seeding the active business-memory backlog.
- Recent merged PRs: PR #56 implemented the read-only `audit-replay` CLI path; PR #57 updated this handoff after PR #56 merged; PR #58 merged the command-policy and active-stop control slice; PR #59 hardened command-policy review findings; PR #60 added the dry-run Git/PR planning design; PR #61 implemented dry-run-only `git-pr-plan`; PR #62 added the next-five-tasks roadmap.
- Current branch scope: `codex/refresh-handoff-backlog` updates public docs and parser tests only. It does not add executor invocation, branch policy, live GitHub sync, branch creation, commits, pushes, pull requests, merges, releases, or package-publication behavior.

## Current Capability Baseline

- Local `cadence-loop-policy.v1` handling can bound emitted executor task packets with allowed and denied paths, commands, required checks, runtime limits, and stop conditions.
- Executor task packets carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- `validate-executor-result` checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- Command policy is hardened around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- `audit-replay` emits a read-only local packet for `cadence-audit.v1` JSONL history and reports stable blockers for corrupt or unsupported records.
- Dry-run-only `git-pr-plan` is merged. It turns validated executor evidence into proposed branch, commit, PR title, and PR body text without creating a branch, committing, pushing, calling GitHub, opening a pull request, merging, releasing, or publishing packages.
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md` is the current public planning artifact for the next bounded slices.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- `git-pr-plan` remains dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
- `git-pr-plan` readiness preserves fail-closed gates: brake-gated success needs the current brake check, `files_changed` alone is not materialized-change evidence, `materialized_change_evidence` must be explicit, and absent materialized evidence blocks Git/PR readiness.
- The active business-memory backlog entry is discovery input only. It does not authorize executor invocation, code modification, branch creation, commits, pushes, PR creation, merges, releases, package publication, or paid review spending.
- No executor invocation, branch policy, branch/PR automation, live GitHub sync, merge authority, release behavior, hash chain, or authenticated approval identity is added by this branch.
- Real executor invocation remains blocked until policy, audit, branch policy, and result evidence gates are stable and covered by tests.
- Keep public docs free of private machine paths and private repository assumptions.

## Validation To Re-run

```powershell
git status -sb
python scripts/validate_protocol.py
python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo -v
python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_seed_controlled_executor_backlog_and_parse_without_warnings -v
git diff --check
```

## Next Action

Finish validation for `codex/refresh-handoff-backlog`, review the handoff and active backlog wording, and merge only after the operator is satisfied. After this branch merges, start Task 2 from `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`: add the controlled executor component fixture.
