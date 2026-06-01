# Current Session Handoff

Last updated: 2026-06-01

## Current State

- Repository: `Chef-Code/agentic-cadence`
- Local checkout: use a clean clone of `Chef-Code/agentic-cadence`; do not rely on a machine-specific path.
- Current base: `origin/main` at `8df4e494549e73625255eaf5402bcb06248dcd9e` after PR #59 merged.
- Working branch intent: document the first dry-run-only Git/PR planning slice from latest `origin/main`.
- Recent merged PRs: PR #56 implemented the read-only `audit-replay` CLI path; PR #57 updated this handoff after PR #56 merged; PR #58 merged the command-policy and active-stop control slice; PR #59 hardened command-policy review findings.
- Current branch scope: add the `git-pr-plan` dry-run packet design and contract docs without implementation, live branch, commit, push, GitHub, PR creation, merge, release, or package-publication side effects.

## Current Capability Baseline

- Added `allowed_commands` and `denied_commands` to local `cadence-loop-policy.v1` handling.
- Executor task packets now carry `command_policy`, and executor result validation rejects commands that match the denylist or fall outside a non-empty allowlist.
- `validate-executor-result` now checks the current brake before recording completion evidence; when `brake_not_drive` is a task stop condition and the brake is not `DRIVE`, non-`stopped` result evidence is invalid and recommends `stop_active_loop`.
- PR #59 further hardened command policy around shell grouping, Bash brace grouping, command substitutions, shell-wrapper payloads, Git aliases, and null top-level command-policy packets.
- The current branch has a design spec for `git-pr-plan` as a future coordination artifact for role-separated multi-agent workflows, but no implementation capability has shipped from this branch yet.

## Important Boundaries

- Command policy is carried in task packets so result validation checks the approved bounds, not a later mutable policy file.
- Active stop handling rejects completion evidence after the brake changes, but it still allows `status: stopped` evidence to report that the executor honored the stop.
- Non-`stopped` evidence for tasks with `brake_not_drive` needs a runtime root to check the current brake; without one, validation recommends `provide_runtime_root`.
- The `git-pr-plan` slice must remain dry-run only: suggested commands are never executed by Cadence, and the executor that produced result evidence is not the final authority for Git/PR approval.
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

After this design branch merges, implement the dry-run-only `git-pr-plan` slice test-first on a follow-up branch, update living docs with actual evidence, run local validation and review agents, and merge only after the operator is satisfied.
