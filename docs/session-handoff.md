# Current Session Handoff

Last updated: 2026-06-01

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `c18403472b7f30ac8fabc73987b33303ccd4940d` after PR #60 merged.
- Working branch intent: finish and validate the dry-run-only `git-pr-plan` implementation branch from latest `origin/main`.
- Recent merged PRs: PR #56 implemented the read-only `audit-replay` CLI path; PR #57 updated this handoff after PR #56 merged; PR #58 merged the command-policy and active-stop control slice; PR #59 hardened command-policy review findings; PR #60 added the dry-run Git/PR planning design.
- Current branch scope: `git-pr-plan-dry-run` adds the local dry-run planning packet, tests, and public docs without live branch, commit, push, GitHub, PR creation, merge, release, or package-publication side effects.

## Current Capability Baseline

- Added `allowed_commands` and `denied_commands` to local `cadence-loop-policy.v1` handling.
- Executor task packets now carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- `validate-executor-result` now checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- PR #59 further hardened command policy around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- The merged design spec for `git-pr-plan` lives at `docs/designs/2026-06-01-git-pr-dry-run-plan-design.md`; the current branch implements the dry-run packet and CLI path, pending final validation and review.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- The `git-pr-plan` slice must remain dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
- `git-pr-plan` readiness must preserve the merged spec's fail-closed gates: brake-gated success needs the current brake check, `files_changed` alone is not materialized-change evidence, `materialized_change_evidence` must be explicit, and absent materialized evidence blocks Git/PR readiness.
- The packet should include evidence provenance and non-authority fields such as `approval_state: "not_approved"`, `execution_authority: "none"`, and `merge_readiness: "not_evaluated"`.
- The first implementation must block detached HEAD, current-branch mismatch, missing local base branch, generated branch collisions, dirty worktree, head mismatch, invalid branch names, invalid task/result evidence, non-success results, and missing PR template sections.
- No executor invocation, branch policy, branch/PR automation, live GitHub sync, merge authority, release behavior, hash chain, or authenticated approval identity is added by this branch.
- No live branch creation, live commit, live push, or live PR creation is added by this branch.
- Keep public docs free of private machine paths and private repository assumptions.

## Validation To Re-run

```powershell
git status -sb
git diff --check
python scripts/validate_protocol.py
python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py
python -m compileall scripts codex_cadence transmission_control tests
python -m unittest tests.test_executor_contract tests.test_cadence
python -m unittest discover -s tests
python scripts/ci_smoke.py
python scripts/verify_package.py
```

## Next Action

Finish validation for the `git-pr-plan-dry-run` branch, review the packet contract and documentation updates, and merge only after the operator is satisfied.
