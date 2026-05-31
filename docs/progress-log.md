# Progress Log

Status: living document
Last updated: 2026-05-31

This log records meaningful project progress, confidence changes, new risks,
and evidence. New discoveries count as progress when they change what the
project knows.

## Entry Template

```markdown
## YYYY-MM-DD - Short title

Summary:
- What changed.

Completed slices:
- Slice name or "None".

Confidence change:
- Previous: N%
- New: N%
- Reason:

Evidence:
- Tests, demos, PRs, review results, audit output, or command output.

New risks or blockers:
- Risk or "None".

Docs updated:
- List living docs updated.
```

## 2026-05-31 - Initial loop policy and audit records

Summary:
- Added an initial local loop policy file for `loop-tick --emit-executor-task`.
- Policy can supply executor task defaults for allowed paths, required checks,
  max runtime, and stop conditions.
- Policy can deny executor task packet emission when requested paths are
  outside `allowed_paths`, overlap `denied_paths`, or exceed the runtime cap.
- Added compact append-only `cadence-audit.v1` JSONL records for root-backed
  `loop-tick` decisions and `validate-executor-result` packets.

Completed slices:
- Policy, Audit, And Stop Controls: Partial.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: decisions and result validation are now more inspectable and locally
  bounded, but Cadence still does not invoke a real executor, start/complete
  execution epochs, create branches or PRs, or replay audit history.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_records_audit_entry_for_executor_task_decision tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_bounds_executor_task_packet tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_denies_disallowed_executor_path tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_reports_no_candidates_without_starting_execution tests.test_cadence.CadenceCliTests.test_loop_tick_stops_at_executor_contract_for_elected_candidate tests.test_cadence.CadenceCliTests.test_loop_tick_can_emit_generic_executor_task_without_starting_execution tests.test_cadence.CadenceCliTests.test_loop_tick_requires_approval_for_low_confidence_repo tests.test_cadence.CadenceCliTests.test_loop_tick_requires_approval_for_red_ci_signal tests.test_cadence.CadenceCliTests.test_loop_tick_blocks_when_cadence_state_disallows_work tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_exits_nonzero_for_invalid_evidence`
- `python -m unittest tests.test_executor_contract -q`
- `python -m py_compile codex_cadence\policy_audit.py codex_cadence\cli.py codex_cadence\store.py`

New risks or blockers:
- Policy does not yet cover command allow/deny rules, branch policy, PR
  approval policy, or active-loop stop behavior.
- Audit records are appended but there is no replay, integrity chain, or
  corrupted-record handling yet.

Docs updated:
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-31 - Agent-team orchestration vision alignment

Summary:
- Reframed Agentic Cadence as a GitHub-native governance and orchestration
  layer for autonomous software teams.
- Clarified that the current single-agent workflow remains Phase 1 rather than
  wasted work or the final product shape.
- Added future role language for Planning, Architecture, Builder, Reviewer,
  QA, Documentation, Release, and Handoff agents.
- Reinterpreted handoff as a general coordination primitive across sessions
  and roles.

Completed slices:
- None. This was documentation and roadmap alignment only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: the vision is clearer, but no new implementation capability was
  added.

Evidence:
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- The future agent-team model will require GitHub-native issue/task ownership,
  role identity, review separation, live synchronization, distributed locking,
  and audit semantics before it can be claimed as implemented.

Docs updated:
- `README.md`
- `docs/agent-team-orchestration.md`
- `docs/protocol.md`
- `docs/adapters.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-31 - Current documentation refresh after PR #51

Summary:
- Updated the living documentation set and adjacent README/protocol text to
  reflect the merged executor task trust-anchor baseline.
- Clarified that the generic executor contract now validates task-packet repo
  identity and absolute local cwd/path anchors, but still does not run a real
  executor or perform branch/PR automation.
- Kept the unattended-operation confidence rating at 10%.

Completed slices:
- None. This was documentation alignment only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: documentation now reflects the stronger task-packet boundary, but no
  new implementation capability was added beyond the merged PR #51 baseline.

Evidence:
- PR #51 merged as `321a355`.
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- None beyond the existing missing real executor invocation, policy/audit log,
  branch/commit/PR automation, live GitHub sync, and automatic resume loop.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/adapters.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-31 - Executor task trust-anchor validation

Summary:
- Executor task packet validation now validates the embedded local repo
  snapshot before accepting the packet as a task/result trust anchor.
- Task packets now fail validation when snapshot repo/cwd/branch/head does not
  match the packet repo anchor.
- Task packets now fail validation when the packet repo name is missing or
  blank, the cwd/path anchor is relative or unnormalizable, or the embedded
  snapshot is dirty or low-confidence.

Completed slices:
- Generic Executor Adapter Contract: Partial.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: task/result validation is safer, but Cadence still does not run a
  real executor, manage branches, open PRs, or resume unattended loops.

Evidence:
- `python -m unittest tests.test_executor_contract`
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python scripts/validate_protocol.py`
- `git diff --check`
- `python -m unittest discover -s tests`
- PR #51 merged as `321a355`.

New risks or blockers:
- Real executor invocation, timeout handling, branch/commit behavior, epoch
  completion/failure integration, and audit logging remain unimplemented.

Docs updated:
- `docs/protocol.md`
- `docs/adapters.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/progress-log.md`

## 2026-05-31 - Executor contract review hardening

Summary:
- Tightened result validation so successful executor evidence cannot be empty
  when no required checks are configured.
- Required successful executor evidence to include a resulting head attestation.
- Hardened disabled commit, push, and PR-creation checks against common
  absolute-path, git/gh global-option, and shell-wrapper command forms.

Completed slices:
- Generic Executor Adapter Contract: Partial.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: the generic boundary is stricter, but Agentic Cadence still does not
  run executors, manage branches, open PRs, or resume unattended loops.

Evidence:
- `python -m unittest tests.test_executor_contract`
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python scripts/validate_protocol.py`
- `python -m unittest discover -s tests`
- `git diff --check`

New risks or blockers:
- Task-packet snapshot trust-boundary validation was the follow-up and is now
  tracked by the later 2026-05-31 executor task trust-anchor validation entry.

Docs updated:
- `docs/protocol.md`
- `docs/adapters.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-05-30 - Generic executor contract Phase 1

Summary:
- Added a generic executor task/result contract without selecting a named host
  adapter.
- `loop-tick --emit-executor-task` can attach a bounded executor task packet for
  operator approval while keeping `executor_started`, `epoch_started`, and
  `pr_action_started` false.
- Added `validate-executor-result` to validate local executor result evidence
  against a task packet without running an executor.
- Hardened result validation after review so successful evidence must prove
  required checks passed, cannot report forbidden head changes, and cannot
  report disabled commit, push, or PR-creation commands.
- Hardened task-packet validation to reject unsupported protocol versions and
  updated the non-emitting loop-tick reason to point operators toward emitting
  a task packet.

Completed slices:
- Generic Executor Adapter Contract: Partial.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: the missing executor boundary is now explicit and testable, but no
  real executor, epoch execution flow, branch/commit/PR automation, or live
  review sync exists yet.

Evidence:
- `python -m unittest tests.test_executor_contract`
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_can_emit_generic_executor_task_without_starting_execution`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence`
- `python scripts/validate_protocol.py`
- `python -m unittest discover -s tests`
- Review hardening follow-up: `python -m unittest tests.test_executor_contract`
- Review hardening follow-up: `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_stops_at_executor_contract_for_elected_candidate tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_exits_nonzero_for_invalid_evidence`
- Review hardening follow-up: `python -m unittest tests.test_executor_contract tests.test_cadence`
- Review hardening follow-up: `python scripts/validate_protocol.py`
- Review hardening follow-up: `python -m unittest discover -s tests`
- CodeRabbit follow-up: `python -m unittest tests.test_executor_contract tests.test_cadence.CadenceCliTests.test_loop_tick_stops_at_executor_contract_for_elected_candidate tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_exits_nonzero_for_invalid_evidence`
- CodeRabbit follow-up: `python -m unittest tests.test_executor_contract tests.test_cadence`
- CodeRabbit follow-up: `python scripts/validate_protocol.py`
- CodeRabbit follow-up: `python -m unittest discover -s tests`

New risks or blockers:
- Real executor invocation, timeout handling, branch/commit behavior, epoch
  completion/failure integration, and audit logging remain unimplemented.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/adapters.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/decision-log.md`

## 2026-05-30 - Read-only single-tick loop Phase 1

Summary:
- Added `loop-tick`, a read-only Phase 1 loop-controller command.
- The command captures and persists a local repo snapshot, runs deterministic
  candidate discovery with election enabled, checks Cadence state, and emits a
  structured next-action packet.
- The command stops at `blocked`, `no_candidates`, `approval_required`, or
  `requires_executor_contract`, and reports that executor, epoch, and PR
  actions were not started.

Completed slices:
- Single-Tick Loop Orchestrator: Partial.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: the command stitches existing read-only primitives together, but it
  still cannot execute code, start/complete work epochs, create PRs, ingest live
  review state, or resume continuously.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_reports_no_candidates_without_starting_execution tests.test_cadence.CadenceCliTests.test_loop_tick_stops_at_executor_contract_for_elected_candidate tests.test_cadence.CadenceCliTests.test_loop_tick_requires_approval_for_low_confidence_repo tests.test_cadence.CadenceCliTests.test_loop_tick_requires_approval_for_low_confidence_without_candidates tests.test_cadence.CadenceCliTests.test_loop_tick_requires_approval_for_red_ci_signal tests.test_cadence.CadenceCliTests.test_loop_tick_blocks_when_cadence_state_disallows_work`
- `python -m unittest tests.test_cadence`
- `python -m unittest tests.test_cadence tests.test_repo_state tests.test_candidates tests.test_epochs`
- `python -m unittest discover -s tests` passed 492 tests with 4 skips.
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- `requires_executor_contract` is now the explicit next stop for an elected
  candidate on a clean repo.
- No executor evidence schema, validation runner, PR automation, or continuous
  mode exists yet.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-30 - Current documentation refresh

Summary:
- Updated the living documentation set and adjacent README/protocol text to
  reflect the merged PR #47 baseline.
- Clarified that readiness/freshness labels are stabilization evidence, not a
  completed loop-runner, executor, live-sync, PR-automation, or resume slice.
- Recorded verified PR #47 merge/check evidence without increasing the
  unattended-operation confidence rating.

Completed slices:
- None. This was documentation alignment only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: no implementation capability changed. The current tree is still a
  governed protocol toolkit rather than an unattended autonomous builder.

Evidence:
- `gh pr view 47 --json number,state,mergedAt,mergeCommit,statusCheckRollup,reviewDecision,reviews,comments`
- `git diff --check`
- `python scripts/validate_protocol.py`
- Local Windows `python -m unittest discover -s tests` passed 485 tests with 4
  skips.

New risks or blockers:
- None beyond the existing missing loop runner, executor contract, live GitHub
  synchronization, PR automation, and automatic resume orchestration.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-30 - Readiness and freshness labels

Summary:
- Added `readiness_evidence` metadata to repo snapshots and PR-readiness
  packets.
- Labeled repo snapshots as `local_only` local-git evidence with explicit
  limitations for unfetched PR and review state.
- Labeled PR readiness inputs as `saved_input`, `stale`, or caller-asserted
  `live_like` evidence.
- Added `--max-pr-json-age-minutes` so stale or future-dated saved PR JSON
  waits and recommends `refresh_pr_evidence` before acting on stale blockers.
- Enforced snapshot readiness evidence during validation and rejected negative
  saved-PR max-age values at the CLI boundary.
- Clarified that caller-asserted `live_like` evidence is labeled, but is not
  gated by saved-JSON age policy.

Completed slices:
- Readiness and freshness labels.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: this makes evidence freshness explicit and prevents stale saved PR
  JSON from looking ready when an age limit is supplied, but it does not add
  live GitHub synchronization, an executor, loop runner, PR creation, or
  autonomous resume capability.

Evidence:
- PR #47 merged as `aca95c6`.
- GitHub reported PR checks green before merge, including Python/protocol
  checks, package install/first-run examples on Ubuntu and Windows, and
  CodeRabbit status success.
- `python -m unittest tests.test_repo_state tests.test_pr_readiness tests.test_cadence`
- Local Windows `python -m unittest discover -s tests` passed 485 tests with 4
  skips.
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Live PR, review, and CI synchronization is still not implemented.
- Caller-asserted `live_like` evidence is labeled, not independently verified
  by Cadence.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-29 - Runtime-root safety guard

Summary:
- Added a CLI guard that rejects unignored runtime roots inside the target git
  repo unless the operator explicitly passes `--allow-repo-local-root`.
- Allowed repo-local runtime roots when the path is ignored by git.
- Limited the guard to commands that actually use Cadence runtime state so
  no-root planning and discovery commands do not get false-positive blocks.

Completed slices:
- Runtime-root safety guard.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: this removes a repo-state footgun, but it does not add the missing
  executor, loop runner, PR creation, live review sync, or autonomous resume
  capability required for unattended continuous operation.

Evidence:
- `python -m unittest tests.test_cadence`
- `python -m unittest discover -s tests`
- `python scripts/validate_protocol.py`
- `git diff --check`
- New CLI tests cover blocked unignored repo-local runtime roots, allowed
  ignored repo-local runtime roots, explicit operator override, cross-command
  root-using behavior, no-cwd current-repo behavior, and no-root planning and
  discovery commands.

New risks or blockers:
- Future host adapters that bypass the CLI must preserve the same guard or
  prove an equivalent runtime-root policy.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-29 - Living readiness documentation initialized

Summary:
- Established the living documentation set for roadmap, autonomous-loop
  readiness, implementation slices, progress tracking, and decisions.
- Captured the blunt readiness assessment against the "press start and build
  continuously" vision.
- Recorded the first 50% confidence target slices.

Completed slices:
- None. This was documentation and governance work only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: no implementation capability changed. The project remains a governed
  protocol toolkit, not an unattended autonomous builder.

Evidence:
- Released 0.1.3 baseline documentation and code review.
- Current implemented commands and docs show local state inspection,
  candidate discovery, task sizing, handoffs, PR readiness from saved inputs,
  release dry-run, and generic adapter contracts.
- At that point, gaps remained: no executor contract, no continuous loop
  runner, no branch/commit/push/PR creation, no live GitHub sync, no automatic
  session-resume orchestration.

New risks or blockers:
- Documentation must be kept current as implementation slices land, or the
  roadmap will drift back into aspiration.
- Runtime roots placed inside target repositories can create dirty-worktree
  signals unless ignored or guarded.

Docs updated:
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
