# Progress Log

Status: living document
Last updated: 2026-06-02

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

## 2026-06-02 - Add controlled executor fixture runner

Summary:
- Added the `run-controlled-executor-fixture` CLI path and
  `controlled-executor-fixture-run.v1` packet for tests/examples.
- The runner validates a generic executor task packet and formatted fixture
  command before launch, runs a fake external executor component with a timeout,
  requires result evidence at the approved path, validates active-brake stops,
  and appends invocation/result-validation audit records.
- Expanded disabled executor permissions to block merge, release, and
  package-publication command forms in addition to commit, push, PR creation,
  and forbidden head changes.

Completed slices:
- Controlled executor component fixture.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now govern a fake executor component, but real executor
  invocation, epoch closeout, branch policy, live Git/PR actions, release
  authority, package publication, and unattended loop execution remain blocked.

Evidence:
- `python -m unittest tests.test_executor_contract.ExecutorContractTests.test_validate_executor_command_rejects_disabled_live_git_actions tests.test_executor_contract.ExecutorContractTests.test_result_evidence_rejects_merge_release_or_package_publication_commands -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_executor_fixture_runs_success_and_validates_result tests.test_cadence.CadenceCliTests.test_controlled_executor_fixture_enforces_task_command_policy_before_running -v`
- `python -m py_compile codex_cadence/executor_runner.py codex_cadence/cli.py codex_cadence/executor_contract.py codex_cadence/policy_audit.py scripts/validate_protocol.py examples/controlled-executor-fixture/run.py`
- `python -m unittest tests.test_executor_contract tests.test_cadence -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`
- `python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_seed_controlled_executor_backlog_and_parse_without_warnings -v`

New risks or blockers:
- Fixture success is not real executor authority. Executor result closeout into
  epochs, branch policy, real implementation execution, live Git/PR automation,
  merge authority, release authority, and package-publication authority remain
  future gates.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/session-handoff.md`
- `docs/adapters.md`
- `docs/cadence/business-memory.md`

## 2026-06-01 - Implement dry-run Git/PR planning

Summary:
- Added the `git-pr-plan` CLI command and `git-pr-plan.v1` packet.
- The planner validates generic executor task/result evidence, checks local Git
  branch/head/base state, requires explicit `materialized_change_evidence`,
  preserves brake/runtime-root stop validation, generates proposed branch,
  commit, PR title, and PR body text, and runs PR body preflight when a
  template or required sections are supplied.
- The packet remains dry-run only with no Git mutation, no GitHub calls, no
  runtime mutation, and explicit non-authority fields for separate review.

Completed slices:
- First dry-run-only increment of Minimal Git/PR Automation.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Git/PR transition review is now packetized, but Cadence still does
  not invoke a real executor, create branches, commit, push, open pull
  requests, synchronize live GitHub state, or run an unattended loop.

Evidence:
- `python -m unittest discover -s tests -p test_git_pr_plan.py -v`
- `python -m unittest tests.test_executor_contract tests.test_pr_readiness -v`
- `python -m py_compile codex_cadence/git_pr_plan.py codex_cadence/cli.py`
- `python -m unittest tests.test_cadence -v`
- `python scripts/validate_protocol.py`
- `python -m unittest tests.test_ci_checks -v`
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python -m unittest discover -s tests -v`
- `git diff --check`
- `python scripts/ci_smoke.py`
- `python scripts/verify_package.py`

New risks or blockers:
- The current materialized-change evidence contract verifies result metadata,
  not a branchable commit. Live branch, commit, push, PR creation, approval
  identity, branch ownership, and GitHub synchronization remain future work.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/session-handoff.md`

## 2026-05-31 - Add command policy and active stop controls

Summary:
- Extended local `cadence-loop-policy.v1` handling with `allowed_commands` and
  `denied_commands`.
- Executor task packets now carry `command_policy`, and executor result
  validation rejects reported commands that match the denylist or fall outside
  a non-empty allowlist.
- `validate-executor-result` now checks the current brake before recording
  completion evidence. If `brake_not_drive` is a task stop condition and the
  brake is not `DRIVE`, non-`stopped` result evidence is invalid and
  recommends `stop_active_loop`.
- Command policy now applies to every effective command segment, including
  compound shell commands, shell grouping, command substitutions, and
  shell-wrapper payloads.
- Rootless validation of otherwise-valid non-`stopped` completion evidence now
  fails closed with `provide_runtime_root` when `brake_not_drive` is a task stop
  condition.

Completed slices:
- Phase 1 denied command test.
- Phase 1 stop brake during active loop.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: command bounds and stop evidence are tighter, but Cadence still does
  not invoke a real executor, create branches or PRs, synchronize live GitHub
  state, or run an unattended loop.

Evidence:
- `python -m unittest tests.test_executor_contract.ExecutorContractTests.test_result_evidence_enforces_task_command_policy tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_malformed_command_policy`
- `python -m unittest tests.test_executor_contract.ExecutorContractTests.test_result_evidence_enforces_task_command_policy tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_malformed_command_policy tests.test_executor_contract.ExecutorContractTests.test_result_evidence_rejects_null_command_policy_fields_without_crashing`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_emits_executor_command_policy tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_success_after_active_brake_stop tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_unignored_repo_local_audit_root`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_validate_executor_result_requires_root_for_completion_with_brake_stop_condition tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_success_after_active_brake_stop tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence`
- `python -m unittest tests.test_executor_contract tests.test_cadence`
- `python scripts/validate_protocol.py`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo tests.test_ci_checks.CiChecksTests.test_public_release_audit_current_tree_passes`
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python scripts/ci_smoke.py`
- `python scripts/verify_package.py`
- `git diff --check`
- `python -m unittest discover -s tests`

New risks or blockers:
- Branch policy, PR approval policy, hash chaining, authenticated approval
  identity, real executor invocation, PR automation, live GitHub sync, and
  automatic resume loop remain missing.

Docs updated:
- `CHANGELOG.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/session-handoff.md`

## 2026-05-31 - Implement read-only audit replay

Summary:
- Added the `audit-replay` CLI command for read-only validation of local
  `cadence-audit.v1` JSONL history.
- Replay now reports an `audit-replay.v1` packet with line and record counts,
  valid event counts, stable blockers for corrupt or unsupported records, and
  command-local recommendations.
- Kept replay read-only: it does not append audit records, repair logs,
  recompute compact checksums from original packet bodies, or approve executor
  invocation.

Completed slices:
- Phase 1 audit replay summary and corrupted audit record handling for local
  compact audit records.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: audit history is now inspectable, but unattended execution still
  lacks a real executor, active-loop stop handling, Git/PR automation, and
  approval identity.

Evidence:
- `python scripts/validate_protocol.py`
- `python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py`
- `python -m compileall scripts codex_cadence transmission_control tests`
- `git diff --check`
- `python -m unittest tests.test_cadence tests.test_audit_replay`
- `python -m unittest discover -s tests`
- `python scripts/ci_smoke.py`
- `python scripts/verify_package.py`

New risks or blockers:
- Command allow/deny policy, branch policy, active-loop stop handling, real
  executor invocation, PR automation, live GitHub sync, and automatic resume
  loop remain missing.

Docs updated:
- `README.md`
- `CHANGELOG.md`
- `SKILL.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`

## 2026-05-31 - Current documentation refresh after PR #54

Summary:
- Updated the living documentation set and handoff notes to reflect that PR
  #54 merged the audit replay design spec after PR #53 added local
  policy/audit writes.
- Clarified that `audit-replay.v1` is designed but the `audit-replay` command
  is not implemented.
- Kept unattended-operation confidence at 10%.

Completed slices:
- None. This was documentation and handoff alignment only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: the next audit verification slice now has a merged design contract,
  but no new runtime capability was added by this documentation refresh.

Evidence:
- PR #53 merged as `55093a1`.
- PR #54 merged as `ea5c24f`.
- `git diff --check`
- `python scripts/validate_protocol.py`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_public_release_audit_current_tree_passes tests.test_ci_checks.CiChecksTests.test_public_tree_excludes_private_context_docs`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo tests.test_ci_checks.CiChecksTests.test_roadmap_captures_current_edges_and_target_state tests.test_ci_checks.CiChecksTests.test_release_readiness_docs_cover_public_baseline tests.test_ci_checks.CiChecksTests.test_candidate_discovery_docs_cover_business_memory tests.test_ci_checks.CiChecksTests.test_prepare_handoff_docs_describe_stop_packet_and_host_signal_boundary`
- `python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_are_closed_and_parse_without_warnings`

New risks or blockers:
- None beyond the existing missing audit replay implementation, command
  allow/deny policy, branch policy, active-loop stop handling, real executor
  invocation, PR automation, live GitHub sync, and automatic resume loop.

Docs updated:
- `README.md`
- `CHANGELOG.md`
- `SKILL.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`

## 2026-05-31 - Initial loop policy and audit records

Summary:
- Added an initial local loop policy file for `loop-tick --emit-executor-task`.
- Policy can supply executor task defaults and caps for allowed paths and max
  runtime; policy required checks and stop conditions remain in force when CLI
  checks or stop conditions are added; built-in safety stops remain in force.
- Policy can deny executor task packet emission when requested paths are
  outside `allowed_paths`, overlap `denied_paths`, or exceed the runtime cap.
- Added compact append-only `cadence-audit.v1` JSONL records for root-backed
  `loop-tick` decisions and `validate-executor-result` packets, including
  task/result content checksums for result-validation audit records.
- Tightened executor result validation so elapsed result time must stay within
  the emitted task runtime limit and the result file must match the task
  packet's absolute expected evidence path.
- Hardened root-backed result validation so malformed task packets cannot
  bypass repo-local runtime-root safety even when the command is launched from
  outside that repo or carries malformed repo path shapes.
- Tightened task-packet validation so built-in safety stops and absolute
  expected evidence paths are required before result evidence can validate.

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
- `python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py codex_cadence/store.py`
- `python -m unittest tests.test_cadence tests.test_executor_contract`
- `python scripts/validate_protocol.py`
- `git diff --check`
- `python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py codex_cadence/store.py codex_cadence/executor_contract.py`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_keeps_builtin_stop_conditions_with_cli_additions tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_bounds_executor_task_packet tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_keeps_policy_stop_conditions_with_cli_additions tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_unexpected_result_file_path tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_repo_local_root_with_malformed_task_packet tests.test_cadence.CadenceCliTests.test_validate_executor_result_audits_malformed_task_packet tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_reports_valid_evidence tests.test_cadence.CadenceCliTests.test_validate_executor_result_command_exits_nonzero_for_invalid_evidence`
- `python -m unittest tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_missing_builtin_stop_conditions tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_relative_expected_evidence_path tests.test_cadence.CadenceCliTests.test_validate_executor_result_audits_malformed_repo_path_shape tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_repo_local_root_from_outside_repo_with_malformed_task_packet`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_validate_executor_result_audits_malformed_repo_path_string tests.test_cadence.CadenceCliTests.test_validate_executor_result_audits_malformed_repo_path_shape tests.test_cadence.CadenceCliTests.test_validate_executor_result_rejects_repo_local_root_from_outside_repo_with_malformed_task_packet tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_missing_builtin_stop_conditions tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_relative_expected_evidence_path`
- `python -m unittest tests.test_cadence tests.test_executor_contract`
- `python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py codex_cadence/store.py codex_cadence/executor_contract.py codex_cadence/repo_state.py`
- `python -m unittest discover -s tests` timed out after 304 seconds and is
  not counted as passing evidence for this slice.

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
