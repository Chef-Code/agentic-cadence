# Progress Log

Status: living document
Last updated: 2026-06-13

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

## 2026-06-13 - Compose loop invocation plans

Summary:
- Added `controlled-loop-invocation-plan` to compose a saved
  `controlled-loop-start.v1` packet with saved
  `executor-invocation-readiness.v1` and `executor-invocation-plan.v1`
  packets.
- The packet rechecks controlled-start, readiness, and invocation-plan schemas,
  task id/checksum, epoch id, readiness file/checksum, target checksum, and
  target readiness checksum before recommending `invoke_real_executor`.
- Completed and blocked packets append no audit evidence; the command does not
  start a runner, start or retry an executor, continue a loop, write
  Git/GitHub state, merge, release, publish packages, assign roles, or schedule
  agents.

Completed slices:
- Task 40: read-only controlled loop invocation-plan composition.

Confidence change:
- Previous: 34%
- New: 35%
- Reason: Cadence can now prove the path from loop start to already-approved
  invocation plan before process start, but it still does not run a continuous
  loop, autonomously retry executors, or operate GitHub.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_composes_start_readiness_and_plan tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_mismatched_invocation_plan tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_missing_readiness_file_anchor tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_target_checksum_mismatch tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_missing_controlled_start_and_readiness_anchors tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_controlled_start_readiness_task_file_mismatch tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_controlled_start tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_readiness tests.test_cadence.CadenceCliTests.test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_invocation_plan -v`
- `python -m unittest tests.test_cadence -v` (340 tests, 3 expected Windows symlink skips)
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python scripts\validate_protocol.py`
- `python -m ruff check codex_cadence\cli.py tests\test_cadence.py`
- `git diff --check`

New risks or blockers:
- Continuous loop execution, autonomous executor retry, Git/GitHub writes,
  merge, release, package publication, role assignment, and agent scheduling
  remain out of scope.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/autonomous-loop-readiness.md`
- `docs/roadmap.md`
- `docs/progress-log.md`

## 2026-06-13 - Compose controlled loop starts

Summary:
- Added `controlled-loop-start` to compose a saved `loop-run-plan.v1` packet
  with an already produced `execution-start.v1` packet.
- The packet rechecks loop-plan schema, execution-start schema, planned
  executor task checksum, embedded executor task shape, task id, approved
  execution-start evidence, active epoch/audit binding, and explicit
  non-runner/non-executor boundaries on the success path before recommending
  `plan_executor_invocation`; blocked packets return recovery actions.
- Completed and blocked packets append no audit evidence; the command does not
  start a runner, start or retry an executor, continue a loop, write
  Git/GitHub state, merge, release, publish packages, assign roles, or schedule
  agents.

Completed slices:
- Task 39: read-only controlled loop-start composition.

Confidence change:
- Previous: 33%
- New: 34%
- Reason: Cadence can now prove that a saved loop-run plan and separately
  approved execution-start evidence line up before invocation planning, but it
  still does not run a continuous loop or invoke executors from this packet.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_unapproved_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_malformed_embedded_executor_task tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_side_effect_contaminated_inputs -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_unapproved_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_malformed_embedded_executor_task tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_side_effect_contaminated_inputs -v`
- `python -m unittest tests.test_cadence -v` (328 tests, 3 expected Windows symlink skips)
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python scripts\validate_protocol.py`
- `python -m ruff check codex_cadence\cli.py tests\test_cadence.py`
- `git diff --check`

New risks or blockers:
- Continuous loop execution, autonomous executor retry, Git/GitHub writes,
  merge, release, package publication, role assignment, and agent scheduling
  remain out of scope.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/progress-log.md`

## 2026-06-13 - Plan loop runs without starting a runner

Summary:
- Added `loop-run-plan` to wrap the read-only loop-tick decision path into a
  `loop-run-plan.v1` packet with planned next operator/orchestrator steps.
- The packet preserves executor task checksum evidence for explicit operator
  approval while keeping runner, executor, epoch, PR action, GitHub write,
  merge, release, package publication, role assignment, scheduling, and loop
  continuation side-effect flags false.
- Review hardening removed the approval-token hint from the plan so the packet
  identifies the approval target without emitting write-side authority.

Completed slices:
- Task 38: read-only loop run planning.

Confidence change:
- Previous: 33%
- New: 33%
- Reason: Cadence can now plan the next bounded loop step from a loop-tick
  decision, but this remains read-only planning and does not start a runner,
  executor, epoch, Git/GitHub write, or continuous loop.

Evidence:
- PR #110 merged on 2026-06-13 as `8bef90bcf7413d5d10584ac9c14646958aff9f48`.
- GitHub PR checks for #110 reported Python/protocol checks passing, Ubuntu
  and Windows package/first-run examples passing, and CodeRabbit passing before
  merge.

New risks or blockers:
- A human or external orchestrator still has to approve the executor task and
  run subsequent commands; no runner, retry, or continuation authority was
  added.

Docs updated:
- `README.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-13 - Plan merge decisions from saved evidence

Summary:
- Added `merge-decision-plan` to compose saved PR JSON, review-thread JSON,
  PR-readiness, audit-replay, required controlled PR-cycle, and optional
  role-readiness evidence into one read-only `merge-decision-plan.v1` packet.
- The planner rechecks PR number, head branch, base branch, and head SHA across
  supplied evidence, blocks unresolved actionable review comments, requires
  valid controlled PR-cycle audit evidence, and keeps merge authority outside
  Cadence.
- Successful plans require operator confirmation and report
  `merge_started: false`, `github_write_started: false`, and an empty command
  trace.

Completed slices:
- Task 37: read-only merge decision planning.

Confidence change:
- Previous: 31%
- New: 33%
- Reason: Cadence can now produce a merge decision plan from the saved
  PR-cycle evidence chain, but it still cannot merge, delete branches, release,
  publish packages, or continue the loop automatically.

Evidence:
- `python -m unittest tests.test_merge_decision -v`
- `python -m py_compile codex_cadence\merge_decision.py codex_cadence\cli.py`
- `python -m unittest tests.test_pr_readiness tests.test_cadence -v`
- `python scripts\validate_protocol.py`
- `python -m ruff check codex_cadence\merge_decision.py codex_cadence\cli.py tests\test_merge_decision.py`
- `git diff --check`

New risks or blockers:
- Merge authority remains intentionally absent. A human or external approved
  system must still perform the merge after inspecting the plan.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/progress-log.md`

## 2026-06-12 - Compose controlled PR-cycle evidence

Summary:
- Added `controlled-pr-cycle` to compose saved controlled-loop,
  Git/PR-materialization, post-write gate, review-response materialization,
  optional review-thread resolution, and final post-write gate evidence into
  one read-only `controlled-pr-cycle.v1` packet.
- The composer rechecks packet schemas, materialization approval state,
  checksums, PR number, head branch, base branch, head SHA, post-write gate
  bindings, and chronological order before recommending merge-readiness
  planning.
- Successful composition appends success-only `controlled_pr_cycle` audit
  evidence; blocked, mismatched, missing final gate, or drifted packets append
  no audit record.

Completed slices:
- Task 36: controlled PR-cycle evidence packet.

Confidence change:
- Previous: 29%
- New: 31%
- Reason: Cadence can now prove the saved PR/review/post-write chain is
  internally consistent before merge planning, but merge decision planning and
  merge authority remain separate and unbuilt.

Evidence:
- `python -m unittest tests.test_pr_cycle -v`
- `python -m py_compile codex_cadence\pr_cycle.py codex_cadence\cli.py codex_cadence\policy_audit.py`
- `python -m unittest tests.test_audit_replay -v`
- `python -m ruff check codex_cadence\pr_cycle.py codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_pr_cycle.py`
- `git diff --check`

New risks or blockers:
- Task 37 still needs read-only merge decision planning; `controlled-pr-cycle`
  deliberately does not merge, trigger paid review, schedule agents, or
  continue a loop.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-12 - Refresh evidence after review-thread resolution

Summary:
- Extended `post-write-pr-evidence-gate` to accept successful
  `review-thread-resolution-materialization.v1` packets.
- The gate now verifies approved resolved thread targets against fresh saved
  review-thread evidence before re-running PR readiness and candidate discovery.
- Review hardening makes the approved `approval_target.thread_ids` canonical,
  requires exact confirmed resolution-write matches, binds review-thread files
  to the same PR, and rejects stale embedded evidence metadata.
- Refreshed resolved targets are suppressed from follow-up candidates, while
  approved targets that remain unresolved block for operator inspection.

Completed slices:
- Task 35: post-resolution PR/review evidence refresh.

Confidence change:
- Previous: 27%
- New: 29%
- Reason: Cadence can now verify live PR/review state after approved thread
  resolution writes, but still lacks composed PR-cycle evidence and read-only
  merge decision planning.

Evidence:
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_accepts_thread_resolution_result_with_resolved_targets tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_thread_resolution_target_still_unresolved tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_requires_refreshed_thread_resolution_target_ids -v`
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_thread_resolution_approval_write_drift tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_wrong_pr_review_thread_evidence tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_malformed_thread_resolution_target_evidence tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_stale_embedded_review_thread_evidence -v`
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_accepts_thread_resolution_result_with_resolved_targets tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_thread_resolution_target_still_unresolved tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_requires_refreshed_thread_resolution_target_ids tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_thread_resolution_approval_write_drift tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_wrong_pr_review_thread_evidence tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_malformed_thread_resolution_target_evidence tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_stale_embedded_review_thread_evidence -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_post_write_pr_evidence_gate_cli_reads_materialization_and_sync_summary -v`
- `python -m unittest tests.test_pr_readiness -v`
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python scripts/validate_protocol.py`
- `python -m ruff check codex_cadence\github_evidence.py tests\test_pr_readiness.py`
- `git diff --check`
- `python -m unittest discover -s tests -v`

New risks or blockers:
- Controlled PR-cycle composition and merge decision planning remain unbuilt.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/autonomous-loop-readiness.md`
- `docs/progress-log.md`

## 2026-06-12 - Materialize approved review-thread resolutions

Summary:
- Added `review-thread-resolution-materialize` as the operator-approved
  write-side bridge from `review-thread-resolution-plan.v1` to exact GitHub
  review-thread resolution writes.
- The command requires a target-bound HMAC approval token using
  `CADENCE_REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET`, rechecks saved PR,
  review-thread, response-materialization, and post-write gate evidence
  immediately before mutation, and only resolves
  approved thread ids through `resolveReviewThread`.
- The result packet is `review-thread-resolution-materialization.v1` and
  records approval target evidence, command trace, GitHub thread ids,
  resolution status, blockers, limitations, and replayable
  `review_thread_resolution_intent` / `review_thread_resolution_result` audit
  events.

Completed slices:
- Task 34: operator-approved review-thread resolution materialization.

Confidence change:
- Previous: 25%
- New: 27%
- Reason: Cadence can now close approved review threads after exact operator
  approval and immediate evidence rechecks, but still needs post-resolution
  evidence refresh, composed PR-cycle evidence, and read-only merge decision
  planning before review-loop closure is complete.

Evidence:
- `python -m pytest tests/test_pr_readiness.py tests/test_audit_replay.py`
- `python -m compileall scripts codex_cadence transmission_control tests`
- `python scripts/validate_protocol.py`
- `python -m ruff check codex_cadence\review_response.py codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_pr_readiness.py tests\test_audit_replay.py`
- `python -m unittest tests.test_pr_readiness tests.test_cadence tests.test_audit_replay`
- `python -m unittest tests.test_candidates tests.test_ci_checks tests.test_codex_review_preflight tests.test_epochs tests.test_executor_contract tests.test_git_pr_plan tests.test_handoff_loop tests.test_release_dry_run tests.test_repo_state tests.test_task_planning`
- `python -m unittest tests.test_adapter_claim_verifier tests.test_adapter_contract_runner tests.test_adapter_smoke_example tests.test_adapter_template_example tests.test_external_host_binding_conformance tests.test_generic_host_signal_smoke_example tests.test_generic_shell_host_binding_example tests.test_host_signal_contract_schema`
- `python scripts/ci_smoke.py`
- `python scripts/verify_package.py`
- `python -m pip install .`
- Windows PowerShell first-run example with installed console script on `PATH`.
- Package example commands: adapter smoke, host signal contract schema, generic
  host-signal smoke, generic shell host-binding, generic shell replay, generic
  host/shell parity, and external host-binding conformance.
- Adapter contract evidence validation and adapter claim verifier against a temp
  compact evidence file.
- `git diff --check`

New risks or blockers:
- Task 35 still needs post-resolution GitHub evidence refresh before Cadence can
  compose authoritative PR-cycle evidence after resolved threads.
- Merge, release, package publication, paid review, label editing, role
  assignment, agent scheduling, and continuous-loop execution remain out of
  scope.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-12 - Add review-thread resolution planning

Summary:
- Added `review-thread-resolution-plan` as a read-only bridge from approved
  review-response writes plus refreshed post-write evidence to exact future
  review-thread resolution approval targets.
- The packet binds explicit target thread ids to PR number, branch, base, head
  SHA, refreshed review-thread evidence checksum, prior response materialization
  checksum, full materialization result checksum, and post-write gate checksum.
- Planning blocks stale or mismatched evidence, incomplete pagination, already
  resolved or outdated threads, non-actionable summary-only threads, missing
  targets, disallowed post-write gate blockers, wrong-PR review-thread evidence,
  threads that were not part of approved response materialization, and current
  actionable comments not covered by approved response materialization.

Completed slices:
- Task 33: read-only review-thread resolution planning.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence can now prepare exact review-thread resolution approval
  targets after approved responses and fresh evidence, but that is still
  read-only planning; Cadence still cannot resolve threads, merge, release,
  publish packages, assign roles, or run continuously.

Evidence:
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_review_thread_resolution_plan_binds_fresh_unresolved_thread_targets tests.test_pr_readiness.PrReadinessTests.test_review_thread_resolution_plan_blocks_ineligible_targets tests.test_pr_readiness.PrReadinessTests.test_review_thread_resolution_plan_blocks_stale_incomplete_mismatched_or_missing_evidence tests.test_pr_readiness.PrReadinessTests.test_cli_review_thread_resolution_plan_reads_saved_files_without_side_effects -v`
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_review_thread_resolution_plan_blocks_unresponded_current_comment_in_responded_thread tests.test_pr_readiness.PrReadinessTests.test_cli_review_thread_resolution_plan_uses_gate_refresh_timestamp_for_freshness -v`
- `python -m py_compile codex_cadence/review_response.py codex_cadence/github_evidence.py codex_cadence/cli.py`
- `python -m unittest tests.test_pr_readiness -v`
- `python -m unittest tests.test_cadence -v`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Task 34 still needs the exact operator-approved write-side bridge before any
  review thread can be resolved.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-12 - Prepare Tasks 33-37 roadmap after post-write gate

Summary:
- Refreshed stale post-Task-32 handoff and business-memory state after PR #103 merged.
- Added `docs/roadmaps/2026-06-12-tasks-33-37-roadmap.md` to sequence review-thread resolution planning, approved resolution materialization, post-resolution evidence refresh, controlled PR-cycle evidence composition, and read-only merge decision planning.
- Kept the next work focused on review-loop closure and evidence composition before any merge, release, package publication, paid review, role assignment, distributed lock, named host adapter, or continuous-loop authority.

Completed slices:
- Documentation and handoff preparation after Task 32.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: The next roadmap is clearer, but no implementation capability changed in this docs-only refresh.

Evidence:
- Local `main` is aligned with `origin/main` at `430fb5bb9ef22dd8aac62d662fac6cffda60df69`.
- `gh pr list --state open --limit 10` returned no open PRs.

New risks or blockers:
- Review-thread resolution, controlled PR-cycle evidence composition, merge decision planning, role assignment, distributed locking, merge, release, package publication, and continuous loop execution remain future work.

Docs updated:
- `docs/session-handoff.md`
- `docs/cadence/business-memory.md`
- `docs/roadmaps/2026-06-12-tasks-33-37-roadmap.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`

## 2026-06-11 - Add post-write PR evidence gate

Summary:
- Added `post-write-pr-evidence-gate` as a read-only bridge after approved
  Git/PR or review-response materialization results.
- The gate consumes fresh `github-evidence-sync` output, loads refreshed saved
  PR and review-thread evidence, verifies PR number, branch, base, and head SHA
  against the materialized target, then re-runs PR readiness and candidate
  discovery.
- It emits `post-write-pr-evidence-gate.v1` with bounded recommendations for
  `ready_for_review`, `refresh_required`, `follow_up_candidates`,
  `wait_for_checks`, `respond_to_review`, or `operator_review`, without GitHub
  writes or loop continuation.

Completed slices:
- Task 32: post-write PR evidence refresh and next-action gate.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence can now refresh and triage live PR state after approved
  writes, but still lacks autonomous scheduling, distributed locks, merge,
  release, package publication, and continuous loop execution.

Evidence:
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_accepts_fresh_matching_review_response_evidence tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_accepts_git_pr_materialization_with_pr_url_number tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_requires_github_evidence_sync_after_materialization tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_requires_fresh_github_evidence_sync tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_blocks_changed_pr_head_before_follow_up tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_recommends_review_response_from_refreshed_threads tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_feeds_failed_checks_back_to_candidate_discovery tests.test_pr_readiness.PrReadinessTests.test_post_write_gate_routes_pr_body_gaps_to_operator_review -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_post_write_pr_evidence_gate_cli_reads_materialization_and_sync_summary -v`
- `python -m py_compile codex_cadence/github_evidence.py codex_cadence/review_response.py codex_cadence/candidates.py codex_cadence/cli.py`
- `python -m unittest tests.test_cadence tests.test_pr_readiness tests.test_candidates -v`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Future automation must invoke `github-evidence-sync` after approved writes and
  feed that fresh summary into the gate before acting on PR state.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-11 - Add approved review response materialization

Summary:
- Added `review-response-materialize` as the write-side bridge from a reviewed
  `review-response-materialization-plan.v1` to approved PR body updates and
  review-thread reply posts.
- The command verifies an HMAC approval token bound to the plan checksum and
  target checksum, then rechecks saved PR freshness, PR/head anchors, evidence
  checksums, review-thread completeness, allowed write kinds, PR body preflight,
  actionable comment targets, and target text checksums before any `gh` write.
- Materialization packets preserve `command_trace`, GitHub write URLs/ids when
  returned by `gh`, `github_write_started`, partial-failure blockers, and
  replayable `review_response_materialization_intent` /
  `review_response_materialization_result` audit events.
- Review-thread resolution remains unsupported; the command does not claim
  reviews are resolved and does not merge, release, publish packages, edit
  labels, invoke paid review, assign roles, schedule agents, or continue a loop.

Completed slices:
- Task 31: operator-approved review response materialization.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence now has a bounded approved GitHub review-response write path,
  but still needs post-write evidence refresh, role assignment, agent
  scheduling, distributed locks, merge, release, package publication, and
  continuous loop execution.

Evidence:
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_review_response_materialize_updates_body_posts_review_reply_and_audits tests.test_pr_readiness.PrReadinessTests.test_review_response_materialize_blocks_missing_approval_without_github_or_audit_writes tests.test_pr_readiness.PrReadinessTests.test_review_response_materialize_rechecks_fresh_pr_threads_and_target_text_before_writes tests.test_pr_readiness.PrReadinessTests.test_review_response_materialize_failed_comment_reports_partial_write_and_result_audit tests.test_pr_readiness.PrReadinessTests.test_review_response_materialize_audit_append_failure_blocks_before_github_writes tests.test_audit_replay.AuditReplayCliTests.test_valid_records_are_counted_by_event_type tests.test_audit_replay.AuditReplayCliTests.test_materialization_audit_records_require_consistent_action_and_status -v`
- `python -m py_compile codex_cadence/review_response.py codex_cadence/github_evidence.py codex_cadence/cli.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_pr_readiness tests.test_cadence tests.test_audit_replay -v`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- A post-write evidence refresh gate is still needed before future automation
  can rely on materialized GitHub state.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-11 - Add review response materialization planning

Summary:
- Added `review-response-materialization-plan` as the read-only bridge from a
  reviewed `review-response-plan.v1` to exact future GitHub write targets.
- The packet binds saved PR JSON, saved review-thread JSON, optional candidate
  evidence, allowed write kinds, exact intended body text, and target text
  checksums into `review-response-materialization-plan.v1`.
- The plan rechecks PR number, branch, head SHA, evidence checksums,
  review-thread completeness, actionable comment targets, PR body preflight,
  response-plan checksum, and stale/future saved PR evidence before approval
  targeting.
- Duplicate same-target review-comment writes are grouped without duplicating
  write actions, and the packet sets `github_write_started: false`.

Completed slices:
- Task 30: review response materialization plan approval binding.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence can now produce an exact approval target for future review
  response writes, but still lacks approved GitHub write execution,
  post-write evidence refresh, role assignment, agent scheduling, distributed
  locks, merge, release, package publication, and continuous loop execution.

Evidence:
- `python -m py_compile codex_cadence\review_response.py codex_cadence\pr_readiness.py codex_cadence\github_evidence.py codex_cadence\cli.py`
- `python -m unittest tests.test_pr_readiness tests.test_cadence -v`
- `python scripts\validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Approved review-response writes remain planned for Task 31; this slice still
  does not call GitHub, update PR bodies, post comments, resolve review
  threads, spend paid review, or continue the loop automatically.

## 2026-06-11 - Bind dirty commit evidence to PR materialization

Summary:
- Extended `git-pr-materialize` so a reviewed
  `git-pr-dirty-materialization-plan.v1` can be paired with the
  `git-pr-dirty-commit-materialization.v1` result from Task 28.
- The dirty PR bridge keeps the separate push/PR HMAC approval gate, rechecks
  the dirty branch head, parent, commit message, committed file set, plan and
  target checksums, branch policy, PR body, selected remote, remote URL,
  create/update target, clean worktree, and optional saved PR evidence before
  any network side effect.
- Dirty PR materialization pushes the already-created branch and creates or
  updates the approved PR; it does not create another local branch and does not
  infer dirty-worktree commit authority.
- `git_pr_materialization_intent` and `git_pr_materialization_result` audit
  records now carry dirty commit source file/checksum/commit anchors when that
  bridge is used.

Completed slices:
- Task 29: dirty commit evidence binding for approved PR materialization.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence now has a bounded dirty commit-to-PR bridge, but still lacks
  review-response writes, post-write evidence refresh, role assignment, agent
  scheduling, distributed locks, merge, release, package publication, and
  continuous loop execution.

Evidence:
- `python -m py_compile codex_cadence\git_pr_plan.py codex_cadence\github_evidence.py codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_git_pr_plan.py`
- `python -m unittest tests.test_git_pr_plan -v`
- `python -m unittest tests.test_cadence -v`
- `python scripts\ci_smoke.py`
- `python scripts\validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Approved GitHub review-response writes remain planned for Tasks 30-32; dirty
  PR materialization still does not merge, release, publish, resolve review
  threads, spend paid review, or continue the loop automatically.

## 2026-06-11 - Add approved dirty-worktree commit materialization

Summary:
- Added `git-pr-dirty-commit-materialize`, an operator-approved local bridge
  from reviewed `git-pr-dirty-materialization-plan.v1` packets to exactly one
  branch commit.
- The command verifies a target-bound HMAC approval token, re-runs dirty
  file/fingerprint/closeout/branch-policy/PR-body gates immediately before Git
  writes, creates and checks out only the approved branch, stages only the
  planned files, blocks Git clean/process filter-managed planned files before
  staging, disables Git hooks and commit signing for bounded writes, rolls back
  failed write paths to the source branch/index, and commits exactly the
  approved message.
- Added replayable `git_pr_dirty_commit_materialization_intent` and
  `git_pr_dirty_commit_materialization_result` audit event support.
- The command does not push, call GitHub, create/update PRs, merge, release,
  publish packages, assign roles, schedule agents, claim distributed locks, or
  invoke executors.

Completed slices:
- Task 28: operator-approved dirty-worktree local commit materialization.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence now has a narrow approved local commit bridge for dirty
  executor output, but still lacks the follow-on dirty commit to PR bridge,
  review-response writes, role assignment, agent scheduling, distributed locks,
  merge, release, package publication, and continuous loop execution.

Evidence:
- `python -m py_compile codex_cadence\git_pr_plan.py codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_git_pr_plan.py tests\test_audit_replay.py`
- `python -m unittest tests.test_git_pr_plan -v`
- `python -m unittest tests.test_cadence -v`
- `python -m unittest tests.test_audit_replay -v`
- `python -m unittest tests.test_ci_checks -v`
- `python scripts\validate_protocol.py`
- `python scripts\ci_smoke.py`
- `git diff --check`
- Local review agents: first pass found hook/message-boundary findings; both
  were fixed and post-fix review reported no material findings.

New risks or blockers:
- Dirty commit evidence still needs Task 29 before it can feed an approved
  push/PR materialization path.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md`

## 2026-06-11 - Prepare Tasks 28-32 roadmap after review candidates

Summary:
- Refreshed the handoff after PR #96 merged review follow-up candidate
  generation.
- Added `docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md` to sequence the
  next bounded work after Task 27.
- Marked the stale real-executor hardening business-memory item fulfilled and
  seeded the next active backlog item for exact operator-approved write-side
  bridges.
- Kept the next work focused on plan-first, approval-bound Git/PR and review
  response bridges before any autonomous merge, release, package publication,
  role assignment, distributed lock, or continuous loop authority.

Completed slices:
- Documentation and handoff preparation after Task 27.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: The next roadmap is clearer, but Cadence still lacks autonomous
  dirty-worktree commit materialization, PR/review-response writes, role
  assignment, agent scheduling, distributed locks, merge, release, package
  publication, and continuous loop execution.

Evidence:
- PR #96 merged as `4a66a0b9f706c4bde248192dcdd339e352c451a0`.
- `python -m codex_cadence.cli discover-candidates --cwd . --intent hybrid --discovery-mode local --proposal-allowance elect --elect --max-candidates 10 --max-candidates-per-source 5 --max-business-memory-candidates 5`

New risks or blockers:
- None beyond the existing plan-only dirty-worktree materialization and
  read-only review-response gaps targeted by Tasks 28-32.

Docs updated:
- `docs/session-handoff.md`
- `docs/cadence/business-memory.md`
- `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`
- `docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-11 - Generate review follow-up candidates from saved threads

Summary:
- Saved review-thread evidence now produces bounded `review_finding` execution
  candidates with source PR identity when available, thread/comment provenance,
  author, path/line, saved freshness labels, and target files.
- Duplicate actionable comments on the same thread/file/line follow-up target
  are grouped into one candidate with merged comment ids and occurrence count.
- Malformed or incomplete saved review-thread evidence now blocks candidate
  creation with `review_thread_evidence_incomplete` and recommends refreshing
  review-thread evidence instead of emitting partial candidates.
- `github-evidence-sync` annotates saved review-thread payloads with the PR
  number and URL from the already-read PR metadata so later local discovery can
  preserve source PR identity without another GitHub call.

Completed slices:
- Task 27: Generate Review Follow-Up Candidates From Saved Threads.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: saved review feedback can now become bounded follow-up candidates for
  the controlled loop to elect, but the formal readiness rating remains pinned
  because Cadence still does not autonomously post review replies, resolve
  threads, invoke paid review, assign roles, schedule agents, merge, release,
  publish packages, or run a continuous loop.

Evidence:
- `python -m unittest tests.test_candidates.CandidateDiscoveryBudgetTests.test_review_threads_file_preserves_pr_identity_freshness_and_target_files tests.test_candidates.CandidateDiscoveryBudgetTests.test_incomplete_review_threads_file_blocks_candidate_discovery -v`
- `python -m unittest tests.test_candidates.CandidateDiscoveryBudgetTests.test_review_threads_file_groups_duplicate_comments_by_follow_up_target -v`
- `python -m unittest tests.test_candidates.CandidateDiscoveryBudgetTests.test_review_threads_file_blocks_missing_comment_nodes tests.test_candidates.CandidateDiscoveryBudgetTests.test_review_threads_file_blocks_non_object_comment_nodes tests.test_pr_readiness.PrReadinessTests.test_review_response_plan_blocks_review_threads_missing_comment_nodes -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_discover_candidates_accepts_review_threads_file -v`
- `python -m py_compile codex_cadence\candidates.py codex_cadence\github_evidence.py codex_cadence\review_response.py codex_cadence\pr_readiness.py tests\test_candidates.py tests\test_cadence.py`
- `python -m ruff check codex_cadence\candidates.py codex_cadence\github_evidence.py tests\test_candidates.py tests\test_cadence.py`
- `python -m unittest tests.test_candidates -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_github_evidence_sync_writes_read_only_evidence_files -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_github_evidence_sync_writes_read_only_evidence_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_paginates_review_threads_and_comments tests.test_cadence.CadenceCliTests.test_github_evidence_sync_incomplete_review_threads_returns_blocker_without_files -v`
- `python -m unittest tests.test_pr_readiness -v`
- `python scripts\validate_protocol.py`
- `python scripts\ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Review follow-up candidates still depend on saved local evidence. Refreshing
  stale PR/review state, executing candidates, posting comments, resolving
  threads, and merging remain separately governed steps.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`

## 2026-06-11 - Preserve PR evidence freshness in write-side planning

Summary:
- `review-response-plan` evidence summaries now preserve `saved_input` and
  `stale` freshness labels instead of only reporting a stale boolean.
- `git-pr-materialize` accepts optional saved PR JSON, emits it as
  `pr_evidence`, and blocks stale or future-dated saved PR evidence before
  audit records, branch creation, push, or PR create/update work.
- Function-level live-like PR evidence remains explicit and is not stale-gated
  by the saved-file age policy.

Completed slices:
- Task 26: Preserve PR Evidence Freshness In Write-Side Planning.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: PR evidence freshness is now carried into write-side materialization
  packets, but Cadence still does not autonomously assign roles, schedule
  agents, merge, release, publish packages, or run a continuous loop.

Evidence:
- `python -m py_compile codex_cadence\git_pr_plan.py codex_cadence\cli.py codex_cadence\review_response.py tests\test_git_pr_plan.py tests\test_pr_readiness.py`
- `python -m unittest tests.test_pr_readiness.PrReadinessTests.test_review_response_plan_recommends_refresh_for_stale_pr_evidence tests.test_git_pr_plan.GitPrPlanTests.test_git_pr_materialize_malformed_pr_json_returns_stable_blocker_packet tests.test_git_pr_plan.GitPrPlanTests.test_git_pr_materialize_records_fresh_saved_pr_evidence_for_update tests.test_git_pr_plan.GitPrPlanTests.test_git_pr_materialize_blocks_stale_saved_pr_evidence_before_pr_update_preflight tests.test_git_pr_plan.GitPrPlanTests.test_git_pr_materialize_labels_caller_asserted_live_like_pr_evidence -v`
- `python -m ruff check codex_cadence\git_pr_plan.py codex_cadence\cli.py codex_cadence\review_response.py tests\test_git_pr_plan.py tests\test_pr_readiness.py`
- `python scripts\validate_protocol.py`
- `python -m unittest tests.test_git_pr_plan tests.test_pr_readiness -v`
- `python -m unittest tests.test_cadence -v`
- `python scripts\ci_smoke.py`
- `git diff --check`

New risks or blockers:
- None.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-11 - Add dirty-worktree Git/PR materialization plan binding

Summary:
- Added `git-pr-dirty-materialization-plan`, a read-only bridge from
  closeout-approved real-executor `materialized_changes` evidence to a reviewed
  commit/PR materialization input.
- The command emits `git-pr-dirty-materialization-plan.v1` and blocks stale or
  tampered dirty evidence with `dirty_worktree_fingerprint_mismatch` or
  `materialized_change_files_mismatch`.
- It now also requires `--closeout-file` and blocks stale closeout-to-invocation
  anchors with `closeout_invocation_mismatch`.
- The packet verifies the current dirty file set and dirty-worktree fingerprint
  against the real-invocation record, rechecks branch/base/branch-policy and
  PR-body gates, and emits exact proposed commit metadata plus `target_checksum`
  for later operator approval.
- Kept Git/PR writes out of scope: the command does not stage, commit, create
  branches, push, call GitHub, open or update PRs, merge, release, or publish.

Completed slices:
- Task 25: Add Dirty-Worktree Git/PR Materialization Plan Binding.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence can now prepare reviewed dirty-worktree materialization
  inputs, but it still does not perform dirty-worktree commit materialization,
  assign roles, schedule agents, claim distributed locks, merge, release,
  publish packages, or run a continuous autonomous loop.

Evidence:
- `python -m unittest tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_binds_closeout_approved_dirty_worktree_without_writes tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_dirty_fingerprint_tampering tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_extra_dirty_files tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_branch_and_base_drift tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_pr_body_and_branch_policy_failures -v`
- `python -m unittest tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_malformed_closeout_checksum tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_closeout_invocation_binding_mismatch -v`
- `python -m unittest tests.test_git_pr_plan.GitPrPlanTests.test_dirty_materialization_plan_blocks_real_invocation_contract_drift -v`
- `python -m py_compile codex_cadence/git_pr_plan.py codex_cadence/cli.py tests/test_git_pr_plan.py`
- `python -m ruff check codex_cadence/git_pr_plan.py codex_cadence/cli.py tests/test_git_pr_plan.py`

New risks or blockers:
- Dirty-worktree materialization is still a reviewed plan only. A later exact
  approval path must perform any commit/push/PR writes.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`

## 2026-06-11 - Add closeout-bound ownership completion evidence

Summary:
- Added `complete-work-ownership-from-closeout`, which closes an active local
  `work-ownership.v1` record only after saved valid
  `executor_epoch_closeout` evidence is completed and its supplied checksum,
  saved task checksum, task id, candidate id, role, claimer, repo, branch,
  `HEAD`, and epoch id all match.
- Extended `work_ownership_mutation` audit evidence with optional
  closeout-bound anchors: `epoch_id`, `executor_closeout_file`,
  `executor_closeout_checksum`, and `executor_closeout_status`.
- Preserved manual `close-work-ownership` and `fail-work-ownership` behavior;
  failed executor closeout evidence still requires explicit
  `fail-work-ownership` if the operator wants local ownership marked failed.

Completed slices:
- Task 24: Add Closeout-Bound Ownership Completion Evidence.

Confidence change:
- Previous: 25%
- New: 25%
- Reason: Cadence can now close local ownership from accepted executor
  closeout evidence, but it still does not assign roles, schedule agents,
  claim distributed locks, create branches, commit, push, open PRs, merge,
  release, publish packages, or run a continuous autonomous loop.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_complete_work_ownership_from_closeout_closes_matching_active_record_with_audit tests.test_cadence.CadenceCliTests.test_complete_work_ownership_from_closeout_blocks_mismatched_anchors_before_move tests.test_cadence.CadenceCliTests.test_complete_work_ownership_from_closeout_blocks_failed_closeout_without_move tests.test_cadence.CadenceCliTests.test_complete_work_ownership_from_closeout_rolls_back_move_when_audit_append_fails tests.test_audit_replay.AuditReplayCliTests.test_work_ownership_audit_accepts_closeout_bound_executor_anchors tests.test_audit_replay.AuditReplayCliTests.test_work_ownership_audit_rejects_malformed_closeout_bound_executor_anchors -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_complete_work_ownership_from_closeout_blocks_mutated_task_file_before_move tests.test_audit_replay.AuditReplayCliTests.test_work_ownership_audit_rejects_closeout_anchors_on_non_close_actions -v`

New risks or blockers:
- Ownership completion remains local evidence only. It does not create remote
  locks or infer review authority from executor closeout packets.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`

## 2026-06-11 - Add controlled single-tick run packet

Summary:
- Added `controlled-loop-tick`, which composes saved `loop-tick`, task,
  execution-start, invocation-readiness, invocation-plan, real-invocation,
  result, snapshot-after, closeout, and optional Git/PR plan evidence into one
  `controlled-loop-tick.v1` packet.
- Added success-only `controlled_loop_tick` audit replay support with stable
  checksum and local file anchors.
- Review follow-up tightened final real-invocation closeout binding, explicit
  path anchors, snapshot-after validation, optional Git/PR plan anchoring, and
  success-only audit replay action validation.
- Kept the command as existing-evidence composition only: it does not retry the
  executor, rewrite invocation or closeout records, execute Git commands, call
  GitHub, create branches, push, open PRs, merge, release, publish packages,
  assign roles, schedule agents, or claim distributed locks. The packet carries
  limitation tokens including `composes_existing_local_evidence_only`,
  `does_not_retry_executor`, and
  `does_not_rewrite_invocation_or_closeout_records`.

Completed slices:
- Task 23: Add Controlled Single-Tick Run Packet.

Confidence change:
- Previous: 20%
- New: 25%
- Reason: Cadence can now prove a saved single-tick evidence chain after
  closeout, but continuous orchestration, autonomous Git/PR writes, dirty
  worktree commit authority, review-response writes, session launch,
  agent-pool coordination, merge, release, and package publication remain out
  of scope.

Evidence:
- `python -m py_compile codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_cadence.py tests\test_audit_replay.py`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_accepts_existing_real_invocation_closeout_chain tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_mismatched_readiness_without_audit_append tests.test_audit_replay.AuditReplayCliTests.test_controlled_loop_tick_audit_record_replays -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_accepts_existing_real_invocation_closeout_chain tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_mismatched_readiness_without_audit_append tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_malformed_closeout_validation_without_traceback tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_stale_pre_closeout_invocation_record tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_unanchored_optional_git_pr_plan tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_real_invocation_path_anchor_drift tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_closeout_real_invocation_path_drift tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_blocks_invalid_snapshot_after_shape tests.test_cadence.CadenceCliTests.test_controlled_loop_tick_reports_audit_append_failure_without_audit_record tests.test_audit_replay.AuditReplayCliTests.test_controlled_loop_tick_audit_record_replays tests.test_audit_replay.AuditReplayCliTests.test_controlled_loop_tick_audit_record_rejects_block_action tests.test_audit_replay.AuditReplayCliTests.test_controlled_loop_tick_audit_record_requires_invocation_id -v`
- `python -m py_compile codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_cadence.py tests\test_audit_replay.py scripts\validate_protocol.py`
- `python -m unittest tests.test_cadence tests.test_audit_replay -v`
- `python -m unittest tests.test_executor_contract tests.test_epochs tests.test_ci_checks -v`
- `python scripts\validate_protocol.py`
- `python scripts\ci_smoke.py`
- `python -m ruff check codex_cadence\cli.py codex_cadence\policy_audit.py tests\test_cadence.py tests\test_audit_replay.py`
- `git diff --check`

New risks or blockers:
- Task 23 still depends on separately produced local evidence files. It does
  not run a continuous loop, automatically choose and invoke the next executor,
  materialize dirty worktree changes into commits, or write PR/review updates.

Docs updated:
- `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`,
  `docs/implementation-slices.md`, `docs/progress-log.md`,
  `docs/roadmap.md`, `docs/session-handoff.md`,
  `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`.

## 2026-06-11 - Prepare Tasks 23-27 roadmap after real closeout

Summary:
- Refreshed the handoff after PR #90 merged real executor invocation closeout
  binding.
- Added `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md` to sequence the
  next bounded work after Task 22.
- Kept the next work focused on composing existing authority before expanding
  it: controlled single-tick orchestration, closeout-bound ownership
  completion, dirty-worktree materialization planning, saved PR evidence
  freshness, and review follow-up candidate generation.

Completed slices:
- Documentation and handoff preparation after Task 22.

Confidence change:
- Previous: 20%
- New: 20%
- Reason: The next roadmap is clearer, but Cadence still lacks continuous
  orchestration, dirty-worktree commit authority, write-side review response,
  named host adapters, agent scheduling, distributed locks, merge, release, and
  package publication.

Evidence:
- PR #90 merged as `4079cc033023ac7026c585a14b25b77f38452733`.
- `python -m py_compile scripts/validate_protocol.py tests/test_ci_checks.py`
- `python -m unittest tests.test_ci_checks -v`
- `python scripts/validate_protocol.py`
- `git diff --check`

New risks or blockers:
- None beyond the existing Task 23 controlled single-tick orchestration gap and
  later Git/PR, review-response, role-assignment, distributed-lock, merge,
  release, and package-publication gaps.

Docs updated:
- `docs/session-handoff.md`
- `docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md`
- `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`
- `docs/roadmap.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`

## 2026-06-11 - Bind real executor invocation closeout

Summary:
- Added `closeout-executor-result --real-invocation-file`, which validates
  canonical `real-executor-invocation.v1` evidence against result validation,
  plan/readiness checksums, active epoch id, active ownership evidence, repo
  before/after anchors, materialized-change evidence, and audit-chain
  continuity before epoch mutation.
- `invoke-real-executor` now appends a `real_executor_invocation_record` audit
  event whose checksum anchors the just-written invocation JSON before
  closeout can trust its binding fields.
- Accepted real invocation closeout updates the invocation record with closeout
  status and checksum anchors, can complete the governed epoch, and can embed a
  dry-run Git/PR plan without committing, pushing, opening PRs, or writing
  GitHub state.
- Review follow-up added dirty-worktree fingerprint checks for materialized
  changes, a dedicated `update_real_executor_invocation_closeout` audit event,
  stricter active ownership anchor revalidation, and structured failure output
  when post-run invocation audit append fails.
- Bot review follow-up also persists absolute invocation plan/evidence file
  anchors plus `invocation_cwd`, so real invocation closeout can replay records
  from a different operator cwd.
- Local review follow-up added action/status validation for
  `real_executor_invocation_record` replay and structured recovery payloads for
  closeout-time audit append failures.

Completed slices:
- Task 22: Bind Real Executor Run Evidence To Closeout And Git/PR Planning.

Confidence change:
- Previous: 15%
- New: 20%
- Reason: Cadence can now bind accepted real-run evidence into local closeout
  and dry-run planning, but autonomous GitHub writes, review response writes,
  session orchestration, named host adapters, merge, release, and package
  publication remain out of scope.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_real_executor_invocation_record_is_accepted_by_closeout_and_git_pr_plan -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_real_executor_invocation_closeout_blocks_result_file_tampering tests.test_cadence.CadenceCliTests.test_real_executor_invocation_closeout_blocks_mutable_record_checksum_tampering tests.test_cadence.CadenceCliTests.test_real_executor_invocation_closeout_blocks_false_snapshot_after_dirty_state tests.test_cadence.CadenceCliTests.test_real_executor_invocation_closeout_blocks_unreported_dirty_files tests.test_cadence.CadenceCliTests.test_real_executor_invocation_closeout_blocks_tampered_repo_transition -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_executor_fixture_run_record_is_accepted_by_closeout tests.test_cadence.CadenceCliTests.test_closeout_executor_result_completes_epoch_and_embeds_dry_run_git_pr_plan -v`
- `python -m py_compile codex_cadence\cli.py codex_cadence\executor_contract.py tests\test_cadence.py`
- `python -m ruff check codex_cadence\cli.py codex_cadence\executor_contract.py tests\test_cadence.py`

New risks or blockers:
- No autonomous dirty-worktree commit path, GitHub PR/review writes, named host
  adapter, merge, release, package publication, agent scheduling, or
  distributed work ownership exists.

Docs updated:
- `docs/protocol.md`, `docs/autonomous-loop-readiness.md`,
  `docs/implementation-slices.md`, `docs/progress-log.md`,
  `docs/roadmap.md`, `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`,
  `docs/session-handoff.md`.

## 2026-06-10 - Add controlled real executor invocation runner

Summary:
- Added `invoke-real-executor`, which consumes a fresh
  `executor-invocation-plan.v1`, re-runs the plan gates immediately before
  process start, launches one approved command with `shell=False`, captures
  stdout/stderr, and writes `real-executor-invocation.v1` records.
- Added `evidence_only` and `materialized_changes` side-effect modes so
  clean-evidence runs and dirty-worktree materialization are recorded
  differently without committing, pushing, opening PRs, merging, releasing, or
  publishing packages.

Completed slices:
- Task 21: Add Controlled Real Executor Invocation Runner.

Confidence change:
- Previous: 10%
- New: 15%
- Reason: Cadence can now start one approved local executor process and record
  invocation evidence, but closeout binding and named host adapters remain
  future work.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_invoke_real_executor_runs_approved_plan_and_writes_invocation_record tests.test_cadence.CadenceCliTests.test_invoke_real_executor_blocks_stale_or_uninvocable_plan_before_start tests.test_cadence.CadenceCliTests.test_invoke_real_executor_enforces_result_and_side_effect_modes -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_invoke_real_executor_blocks_hidden_branch_ref_changes tests.test_cadence.CadenceCliTests.test_invoke_real_executor_honors_repo_local_runtime_root_override -v`

New risks or blockers:
- `real-executor-invocation.v1` is invocation evidence only until Task 22 binds
  it to result validation, epoch closeout, active ownership revalidation, and
  Git/PR planning gates.

Docs updated:
- `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`,
  `docs/implementation-slices.md`, `docs/progress-log.md`, `docs/roadmap.md`,
  `docs/session-handoff.md`, `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`.

## 2026-06-10 - Add real executor invocation plan evidence

Summary:
- Added read-only `executor-invocation-plan` evidence that binds successful
  invocation readiness to operator approval, adapter metadata, rollback
  evidence, current audit-chain head, command, environment allowlist, timeout,
  cwd, active epoch, active ownership, and expected result path.
- Kept the plan command read-only with `executor_started: false` and no audit,
  Git, GitHub, merge, release, or package-publication side effects.

Completed slices:
- Task 20: Add Real Executor Invocation Plan And Approval Binding.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now build exact invocation plans, but it still lacks the
  controlled real executor process-start runner and real-run evidence capture.

Evidence:
- `python -m py_compile codex_cadence/executor_invocation.py codex_cadence/executor_readiness.py codex_cadence/approvals.py codex_cadence/policy_audit.py codex_cadence/cli.py tests/test_cadence.py`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_executor_invocation_plan_accepts_matching_evidence_without_side_effects tests.test_cadence.CadenceCliTests.test_executor_invocation_plan_blocks_unready_inputs_without_side_effects tests.test_cadence.CadenceCliTests.test_executor_invocation_plan_blocks_stale_and_mismatched_anchors -v`
- `python -m unittest tests.test_cadence -v`
- `python -m unittest tests.test_executor_contract tests.test_epochs tests.test_ci_checks -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- `executor-invocation-plan.v1` is not process-start authority; Task 21 still
  needs to recheck the plan immediately before launching a controlled real
  executor process.

Docs updated:
- `README.md`, `docs/protocol.md`, `docs/autonomous-loop-readiness.md`,
  `docs/implementation-slices.md`, `docs/roadmap.md`,
  `docs/progress-log.md`, `docs/decision-log.md`.

## 2026-06-10 - Add authenticated operator approval identity evidence

Summary:
- Added `operator-approval.v1` packets and `verify-operator-approval` for local
  HMAC-backed operator identity evidence.
- Added accepted verification audit evidence through
  `operator-approval-verification.v1` packets and
  `operator_approval_verification`.
- Bounded approval validity windows to 60 minutes and tightened audit replay so
  accepted approval records must carry verified signatures, supported purposes,
  valid identity fields, and coherent checked-at timestamps.
- Kept approval verification separate from executor, epoch, GitHub, merge,
  release, and package-publication authority.

Completed slices:
- Task 19: Add Authenticated Operator Approval Identity Evidence.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence now has target-bound local approver identity evidence, but it
  still lacks real executor invocation planning, controlled real executor
  start, autonomous branch/PR workflow, merge, release, and package
  publication.

Evidence:
- `python -m py_compile codex_cadence/approvals.py codex_cadence/cli.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_cadence tests.test_audit_replay -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- `operator-approval.v1` is local packet/HMAC evidence only; external identity
  providers and key lifecycle remain future work.
- Audit replay validates the accepted approval record semantics, but it still
  cannot recompute HMAC signatures without a future key-management model.
- Existing execution-start and Git/PR materialization approval tokens remain
  backward compatible until a later slice deliberately migrates those gates to
  consume the reusable approval evidence.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-09 - Implement audit hash-chain integrity evidence

Summary:
- Added `cadence-audit-chain.v1` metadata to newly appended local audit
  records.
- Extended read-only `audit-replay.v1` packets with chain head/count evidence
  and legacy-root reporting.
- Added stable replay blockers for missing chain metadata, broken predecessor
  hashes, event-hash mismatches, duplicate chain indexes, and unsupported chain
  versions.

Completed slices:
- Task 18: Add Audit Hash-Chain Integrity Evidence.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Local audit history is now tamper-evident for new records, but
  Cadence still lacks authenticated approval identity, exact real-executor
  invocation planning, a controlled real executor start, agent assignment,
  continuous orchestration, merge, release, and package publication.

Evidence:
- `python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py`
- `python -m unittest tests.test_audit_replay -v`
- `python -m unittest tests.test_cadence -v`
- `python -m unittest tests.test_ci_checks -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Authenticated operator approval identity remains the next hardening gap.
- Legacy audit histories remain valid as explicit roots, but old records do
  not gain retroactive chain metadata.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/session-handoff.md`
- `docs/cadence/business-memory.md`

## 2026-06-09 - Prepare Tasks 18-22 roadmap after executor readiness

Summary:
- Refreshed the handoff after PR #84 merged read-only
  `executor-invocation-readiness.v1` evidence.
- Added `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md` to sequence the
  next bounded work after Task 17.
- Marked the real-executor-readiness business-memory entry fulfilled and added
  a new active risk entry for audit-chain, approval-identity, and invocation
  planning before process start.

Completed slices:
- Documentation and handoff preparation after Task 17.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: The next roadmap is clearer, but Cadence still does not invoke a real
  executor, implement code autonomously, write GitHub state without explicit
  approval, merge, release, publish packages, assign roles, schedule agents, or
  provide distributed locking.

Evidence:
- PR #84 merged as `a2736bc6ac3af843cc66391dc891d51a6f1c217b`.
- `python -m py_compile scripts/validate_protocol.py tests/test_candidates.py`
- `python -m unittest tests.test_candidates tests.test_ci_checks -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The next implementation slice should harden audit-chain integrity before any
  real executor process start.
- Authenticated operator approval identity and exact invocation planning remain
  future work after audit-chain integrity.

Docs updated:
- `docs/session-handoff.md`
- `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/cadence/business-memory.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-08 - Add executor invocation readiness preflight

Summary:
- Added read-only `executor-invocation-readiness.v1` packets that consume a
  reviewed executor task, active epoch, active ownership evidence, expected
  result path, and optional role-readiness evidence before any future real
  executor invocation.
- Rechecked repo path, branch, `HEAD`, dirty worktree, active brake, active
  epoch id/status, task checksum, ownership binding, command policy, branch
  policy, required checks, and result-path boundaries.
- Kept `executor_started: false`, `side_effects: []`, and executor process
  metadata out of scope.

Completed slices:
- Task 17 from `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now prove a future real executor invocation boundary is
  locally ready, but it still does not invoke a real executor, implement code,
  autonomously write Git/PR state, merge, release, or publish packages.

Evidence:
- `python -m py_compile codex_cadence/executor_readiness.py codex_cadence/executor_contract.py codex_cadence/epochs.py codex_cadence/ownership.py codex_cadence/cli.py`
- `python -m unittest tests.test_cadence tests.test_executor_contract tests.test_epochs -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Real executor invocation and named host adapters remain future work.
- A successful readiness packet is not authority to start a process or modify
  code; an external orchestrator still needs an explicitly approved invocation
  path.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-08 - Refresh handoff after Task 16

Summary:
- Refreshed the current handoff and living roadmap docs after PR #82 merged
  Task 16 into `main`.
- Marked Tasks 13-16 from
  `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` as complete in `main`.
- Identified Task 17, read-only real executor invocation readiness planning,
  as the next implementation slice.

Completed slices:
- Documentation and handoff preparation after Task 16.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Documentation now matches the merged role-readiness baseline, but
  real executor invocation, autonomous implementation, role assignment, agent
  pools, distributed locks, autonomous Git/PR writes, merge, release, and
  package publication remain future work.

Evidence:
- PR #82 merged as `9dc8cb18f4acc093d11eda146767bf8963cb0509`.
- `python -m py_compile scripts/validate_protocol.py`
- `python -m unittest tests.test_ci_checks tests.test_candidates -v` (134 tests, 2 expected Windows symlink skips)
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- None beyond the existing Task 17 real-executor readiness gap and later
  real executor invocation, autonomous implementation, GitHub write-side
  orchestration, merge, release, and package publication gaps.

Docs updated:
- `docs/session-handoff.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/agent-team-orchestration.md`
- `docs/cadence/business-memory.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-07 - Add role-readiness evidence

Summary:
- Added a local `role-policy.v1` shape and read-only `role-readiness` command.
- `role-readiness.v1` consumes local ownership status, saved PR JSON, and saved
  review-thread evidence to verify allowed ownership role labels and
  builder/reviewer separation.
- Missing or malformed policy, missing or mismatched PR evidence, unknown
  ownership roles, duplicate or stale ownership, stale ownership heads, missing
  builder ownership, missing reviewer evidence, and same-claimer review
  conflicts block with stable recommended next actions.
- Resolved or outdated review-thread comments are ignored for separation
  conflicts.
- Builder replies in otherwise actionable review threads are ignored as
  reviewer evidence when independent reviewer evidence is present.

Completed slices:
- Task 16 merged in PR #82: role-policy and review-separation readiness
  evidence.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Role/readiness evidence closes one orchestration-preparation gap, but
  real executor invocation, autonomous Git/PR writes, role assignment, agent
  pools, distributed locks, merge, release, and package publication remain
  future work.

Evidence:
- `python -m py_compile codex_cadence/roles.py codex_cadence/ownership.py codex_cadence/pr_readiness.py codex_cadence/review_response.py codex_cadence/cli.py`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_role_readiness_accepts_separated_builder_and_review_evidence tests.test_cadence.CadenceCliTests.test_role_readiness_ignores_builder_replies_when_independent_reviewer_exists tests.test_cadence.CadenceCliTests.test_role_readiness_blocks_missing_policy_unknown_role_and_same_claimer_review tests.test_cadence.CadenceCliTests.test_role_readiness_blocks_stale_ownership_and_missing_builder_evidence tests.test_cadence.CadenceCliTests.test_role_readiness_forwards_duplicate_active_ownership tests.test_cadence.CadenceCliTests.test_role_readiness_blocks_pr_and_review_evidence_refresh_inputs tests.test_cadence.CadenceCliTests.test_role_readiness_reports_readable_non_object_evidence_as_invalid tests.test_cadence.CadenceCliTests.test_role_readiness_blocks_mismatched_pr_repo_and_ownership_anchors tests.test_cadence.CadenceCliTests.test_role_readiness_uses_default_ownership_freshness_window tests.test_cadence.CadenceCliTests.test_role_readiness_ignores_resolved_or_outdated_review_threads_for_separation_conflicts -v`
- `python -m unittest tests.test_cadence tests.test_pr_readiness -v` (300 tests, 2 expected Windows symlink skips)
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Reviewer evidence currently comes from saved actionable review-thread
  authors; explicit approval-review author evidence remains future work.
- Role readiness is evidence only, not role assignment, authenticated identity,
  agent-pool scheduling, or distributed locking.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/agent-team-orchestration.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-06 - Bind work ownership to resume continuation

Summary:
- Added ownership-aware `resume-continuation` flags for a supplied active
  `work-ownership.v1` record.
- Resume continuation now rechecks matching ownership evidence after existing
  saved/fresh resume blockers and before recommending governed execution start.
- Missing, mismatched, duplicate, stale, closed, failed, or malformed ownership
  blocks with ownership-specific recommended next actions while existing
  resume blockers keep precedence.
- The command remains read-only: it does not mutate ownership, start an epoch,
  invoke an executor, or write Git/PR state.

Completed slices:
- Task 15 merged in PR #81: work-ownership-bound resume continuation.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Resume continuation can now consume local ownership evidence, but
  role policy, review separation, real executor invocation, autonomous Git/PR
  writes, merge, release, and package publication remain future work.

Evidence:
- `python -m py_compile codex_cadence/handoff_loop.py codex_cadence/ownership.py codex_cadence/cli.py`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`
- `python -m unittest tests.test_handoff_loop.HandoffLoopTests.test_resume_continuation_binds_matching_work_ownership_without_mutation tests.test_handoff_loop.HandoffLoopTests.test_resume_continuation_blocks_bad_work_ownership_evidence tests.test_handoff_loop.HandoffLoopTests.test_resume_continuation_existing_blockers_precede_work_ownership_validation -v`
- `python -m unittest tests.test_handoff_loop -v`
- `python -m unittest tests.test_cadence -v` (241 tests, 2 expected Windows symlink skips)
- `python -m unittest tests.test_candidates -v` (84 tests, 2 expected Windows symlink skips)
- `python -m unittest tests.test_ci_checks -v`
- `python -m unittest tests.test_executor_contract tests.test_epochs tests.test_candidates tests.test_audit_replay tests.test_repo_state tests.test_task_planning -v` (241 tests, 2 expected Windows symlink skips)
- `python -m unittest tests.test_git_pr_plan tests.test_pr_readiness tests.test_release_dry_run tests.test_ci_checks tests.test_codex_review_preflight -v`
- `python -m unittest tests.test_adapter_template_example tests.test_adapter_smoke_example tests.test_adapter_claim_verifier tests.test_adapter_contract_runner tests.test_generic_host_signal_smoke_example tests.test_generic_shell_host_binding_example tests.test_external_host_binding_conformance tests.test_host_signal_contract_schema -v` (98 tests, 2 expected Windows symlink skips)

New risks or blockers:
- Ownership is still local evidence, not a distributed lock or scheduler.
- Role policy, review separation, real executor readiness, distributed locks,
  agent pools, GitHub issue assignment, merge, release, and package publication
  remain future work.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-06 - Bind work ownership to governed execution start

Summary:
- Added ownership-aware `start-governed-execution` flags for a supplied active
  `work-ownership.v1` record.
- Execution-start now rechecks matching ownership evidence after existing
  execution-start blockers and before epoch mutation, then binds the started
  `epoch_id` back to the active ownership record.
- Missing, mismatched, duplicate, stale, or malformed ownership blocks before
  epoch mutation, while existing approval/repo/brake/active-epoch blockers keep
  precedence.
- Accepted ownership-bound starts append compact `execution_start_decision`
  audit evidence with ownership id and ownership-record checksum. Audit append
  failure restores both the active epoch and ownership binding.

Completed slices:
- Task 14 merged in PR #80: work-ownership-bound governed execution start.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Execution-start can now consume and bind local ownership evidence,
  but resume-continuation ownership enforcement, role policy, review
  separation, real executor invocation, autonomous Git/PR writes, merge,
  release, and package publication remain future work.

Evidence:
- `python -m py_compile codex_cadence/ownership.py codex_cadence/epochs.py codex_cadence/cli.py codex_cadence/policy_audit.py`
- `python scripts/validate_protocol.py`
- `git diff --check`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_start_governed_execution_binds_matching_work_ownership tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_mismatched_and_duplicate_work_ownership tests.test_cadence.CadenceCliTests.test_start_governed_execution_existing_blockers_precede_work_ownership_validation tests.test_cadence.CadenceCliTests.test_start_governed_execution_rolls_back_ownership_binding_when_audit_append_fails -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_start_governed_execution_binds_matching_work_ownership tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_mismatched_and_duplicate_work_ownership tests.test_cadence.CadenceCliTests.test_start_governed_execution_existing_blockers_precede_work_ownership_validation tests.test_cadence.CadenceCliTests.test_start_governed_execution_rolls_back_ownership_binding_when_audit_append_fails tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_approval tests.test_cadence.CadenceCliTests.test_start_governed_execution_rechecks_repo_inside_runtime_lock tests.test_cadence.CadenceCliTests.test_start_governed_execution_rolls_back_epoch_when_audit_append_fails -v`
- `python -m unittest tests.test_audit_replay -v`
- `python -m unittest tests.test_cadence tests.test_epochs tests.test_audit_replay -v` (329 tests, 2 expected Windows symlink skips)
- `python scripts/ci_smoke.py`

New risks or blockers:
- Ownership is still local evidence, not a distributed lock or scheduler.
- Resume-continuation ownership enforcement, role policy, review separation,
  real executor readiness, distributed locks, agent pools, GitHub issue
  assignment, merge, release, and package publication remain future work.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-06 - Add local work ownership claim and closeout

Summary:
- Added explicit `claim-work-ownership`, `close-work-ownership`, and
  `fail-work-ownership` commands for local `work-ownership.v1` evidence under
  `<runtime-root>/work-ownership/{active,closed,failed}`.
- Claims recheck branch, `HEAD`, clean worktree state, duplicate active
  ownership, stale active ownership, malformed ownership records, invalid role
  or claimer labels, and registry path safety before writing one active
  record.
- Closeout commands move one active record to `closed` or `failed`, attach
  closeout evidence, and accepted mutations append replayable
  `work_ownership_mutation` audit records.

Completed slices:
- Task 13 merged in PR #79: local work ownership claim and closeout.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now write and close local ownership evidence under
  governance, but ownership is not yet enforced by execution-start or
  resume-continuation and there is still no role assignment, distributed lock,
  real executor invocation, autonomous Git/PR write, merge, release, or
  package publication path.

Evidence:
- `python -m py_compile codex_cadence/ownership.py codex_cadence/cli.py codex_cadence/store.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_cadence -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Ownership records remain local evidence only. Tasks 14 and 15 still need to
  make execution-start and resume-continuation consume ownership evidence.
- Role policy, review separation, real executor readiness, distributed locks,
  agent pools, GitHub issue assignment, merge, release, and package
  publication remain future work.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`

## 2026-06-05 - Prepare post-Task-12 handoff and Tasks 13-17 roadmap

Summary:
- Refreshed the current handoff to start from PR #77's merge commit on
  `main`.
- Added `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` for the next bounded
  sequence: local ownership claim/closeout, ownership-bound execution start,
  ownership-bound resume continuation, role/readiness evidence, and real
  executor invocation readiness planning.
- Marked the Tasks 8-12 roadmap follow-up and business-memory planning entry as
  fulfilled by the new roadmap.

Completed slices:
- Documentation and handoff preparation after Task 12.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: The next implementation sequence is clearer, but no runtime behavior
  changed and real executor invocation, role assignment, distributed locks,
  merge, release, and package publication remain unimplemented.

Evidence:
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- None beyond the existing missing write-side ownership creation,
  ownership-bound execution/resume gates, role-readiness evidence, real
  executor invocation, distributed locks, agent pools, merge, release, and
  package publication.

Docs updated:
- `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`
- `docs/session-handoff.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/agent-team-orchestration.md`
- `docs/cadence/business-memory.md`

## 2026-06-05 - Add local work ownership registry

Summary:
- Added local `work-ownership.v1` evidence records under
  `<runtime-root>/work-ownership/{active,closed,failed}` for task, candidate,
  role, claimer, repo, branch, optional PR, optional epoch, optional handoff,
  status, and timestamp binding.
- Added read-only `work-ownership-status.v1` and
  `work-ownership-validation.v1` packets with stable blockers for duplicate active ownership, malformed records, stale evidence, closed evidence, and
  repo/branch/task mismatches.
- Kept execution-start and resume-continuation enforcement outside this slice;
  ownership records are local evidence, not distributed locks.

Completed slices:
- Task 12 current-tree implementation: local work ownership registry.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now expose duplicate local ownership evidence before
  multi-worker coordination exists, but it still cannot assign roles, schedule
  agent pools, invoke a real executor, write PRs, auto-merge, release, or
  publish packages.

Evidence:
- `python -m py_compile codex_cadence/ownership.py codex_cadence/cli.py codex_cadence/store.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_cadence tests.test_ci_checks -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The registry is local-only and read-only in this slice. It does not create
  ownership records, assign roles, coordinate agents, enforce distributed
  locks, or gate execution-start/resume-continuation yet.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`

## 2026-06-05 - Add resume continuation gate

Summary:
- Added read-only `resume-continuation.v1` packets that consume a saved
  `resume-verification.v1` packet and recheck handoff id, claimer, repo
  branch/head, active brake, active epoch state, clean-square evidence,
  pickup-policy evidence, and packet freshness.
- A fresh matching packet recommends `start_governed_execution` while still
  reporting `executor_started: false`, `epoch_started: false`, and
  `side_effects: []`.
- Stale packets, repo drift, different claimers, and non-resumable saved
  verifier packets block with stable codes such as
  `resume_verification_stale`, `resume_verification_not_resumable`,
  `resume_claimer_mismatch`, and `resume_verification_anchor_mismatch`.

Completed slices:
- Task 11 current-tree implementation: resume-to-execution continuation gate.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now bind resume evidence to a governed execution-start
  recommendation, but it still cannot launch sessions, invoke a real executor,
  implement changes, write PRs, auto-merge, release, or publish packages.

Evidence:
- `python -m py_compile codex_cadence/handoff_loop.py codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_handoff_loop tests.test_cadence tests.test_epochs -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The packet is a read-only bridge only. It does not claim handoffs, start
  epochs, invoke executors, create branches, push, open PRs, merge, release, or
  publish packages.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`

## 2026-06-04 - Add review feedback response plan

Summary:
- Added read-only `review-response-plan.v1` packets for saved PR JSON, saved
  review-thread JSON, optional candidate discovery output, and PR-body evidence.
- Grouped actionable feedback by failed check, review thread, file path, and
  likely follow-up task, with optional candidate matches when discovery output
  is supplied.
- Limited recommendations to `emit_executor_task`, `refresh_pr_evidence`,
  `update_pr_body`, `wait_for_checks`, and `operator_review`, with no GitHub
  writes or review-agent invocation.

Completed slices:
- Task 10 current-tree implementation: review feedback response planning.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now plan bounded review/CI follow-up work from saved
  evidence, but it still cannot invoke a real executor, implement changes,
  resolve comments, update PRs, launch sessions, auto-merge, release, or
  publish packages.

Evidence:
- `python -m py_compile codex_cadence/review_response.py codex_cadence/cli.py codex_cadence/pr_readiness.py codex_cadence/github_evidence.py codex_cadence/candidates.py`
- `python -m unittest tests.test_pr_readiness tests.test_candidates -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The packet is response planning only. It does not resolve GitHub comments,
  update pull request bodies, invoke review agents, start executors, create
  branches, commit, push, merge, release, or publish packages.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`
- `docs/session-handoff.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`

## 2026-06-03 - Bind execution run evidence to closeout

Summary:
- Added local `execution-run.v1` records that bind task checksum, invocation id,
  result evidence checksum, validation packet checksum, repo path/branch/head
  anchors, and closeout status.
- Updated `run-controlled-executor-fixture` to write a local execution-run
  record under `<root>/execution-runs/`, return the record path/checksum, and
  append an `execution_run_record` audit event.
- Added `closeout-executor-result --run-record-file` so closeout rejects
  mismatched or partial run records before epoch mutation and updates accepted
  records with closeout status, epoch id/status, and closeout checksum.
- Extended `audit-replay` to validate compact `execution_run_record` audit
  events.

Completed slices:
- Task 9 current-tree implementation: execution run evidence binding to
  closeout.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence now has local run-ledger evidence for the controlled fixture
  path and closeout gate, but real executor invocation, autonomous
  implementation, automatic fresh-session launch, autonomous Git/PR writes,
  auto-merge, release, and package publication remain blocked.

Evidence:
- `python -m unittest tests.test_executor_contract.ExecutorContractTests.test_builds_valid_executor_task_packet tests.test_executor_contract.ExecutorContractTests.test_execution_run_record_binds_task_result_validation_and_repo tests.test_executor_contract.ExecutorContractTests.test_execution_run_record_reports_stable_mismatch_code tests.test_cadence.CadenceCliTests.test_controlled_executor_fixture_run_record_is_accepted_by_closeout tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_run_record_task_checksum_mismatch tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_run_record_result_checksum_mismatch tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_run_record_validation_checksum_mismatch tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_run_record_repo_anchor_mismatch tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_partial_run_record tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_run_record_closeout_replay tests.test_audit_replay.AuditReplayCliTests.test_valid_records_are_counted_by_event_type tests.test_audit_replay.AuditReplayCliTests.test_execution_run_audit_record_requires_run_anchors -v`
- `python -m py_compile codex_cadence/executor_runner.py codex_cadence/executor_contract.py codex_cadence/epochs.py codex_cadence/policy_audit.py codex_cadence/cli.py codex_cadence/store.py`
- `python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_audit_replay -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The run ledger is local filesystem evidence, not a remote backend,
  distributed lock, hash chain, or authenticated approval identity.
- A valid controlled fixture run record is not approval for real executor
  invocation or named-host adapter support.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`
- `docs/session-handoff.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`

## 2026-06-03 - Add governed execution start gate

Summary:
- Added `start-governed-execution`, a local write-side gate that consumes a
  reviewed `generic-executor-task.v1` packet, requires an exact checksum-bound
  approval token, rechecks repo path, branch, `HEAD`, dirty-worktree state,
  task-carried command and branch policy shape, active brake, and active epoch
  state, then starts one active epoch.
- Added the `execution-start.v1` packet with stable blocker codes,
  `epoch_started`, `executor_started: false`, `pr_action_started: false`, and
  a recommended next action.
- Added compact `execution_start_decision` audit records and audit replay
  support for successful execution-start decisions.

Completed slices:
- Task 8 current-tree implementation: governed execution start gate.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Cadence can now bridge an approved task packet to one active epoch,
  but real executor invocation, run-evidence ledger binding, autonomous
  implementation, automatic fresh-session launch, autonomous Git/PR writes,
  auto-merge, release, and package publication remain blocked.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_start_governed_execution_starts_epoch_after_approval tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_approval tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_approval_mismatch tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_stale_head tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_branch_mismatch tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_dirty_worktree tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_repo_path tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_non_drive_brake tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_active_epoch tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_malformed_active_epoch_state tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_malformed_task_packet tests.test_cadence.CadenceCliTests.test_start_governed_execution_records_replayable_audit -v`
- `python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/executor_contract.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_audit_replay -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- The execution-start approval token is checksum-bound review evidence only; it
  is not authenticated approver identity or tamper evidence.
- Task 9 should bind execution-start, invocation, result validation, and
  closeout evidence before any real executor invocation is considered.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`
- `docs/session-handoff.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/cadence/business-memory.md`

## 2026-06-03 - Create Tasks 8-12 roadmap and refresh handoff

Summary:
- Added `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md` for the next five
  bounded implementation slices after Tasks 1-7 completed.
- Refreshed the session handoff, roadmap, readiness, implementation-slice, and
  business-memory docs to reflect PR #69 and PR #70 merged on `main`.
- Marked the previous Tasks 1-7 roadmap complete and updated the next action to
  Task 8, the governed execution start gate.

Completed slices:
- Documentation and planning handoff refresh; no runtime capability change.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: The next plan is clearer, but real executor invocation, autonomous
  implementation, automatic fresh-session launch, autonomous Git/PR writes,
  auto-merge, release, and package publication remain blocked.

Evidence:
- `python -m py_compile scripts/validate_protocol.py`
- `python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_are_closed_and_parse_without_warnings tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_seed_governed_execution_backlog_and_parse_without_warnings -v`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo -v`
- `python -m unittest discover -s tests -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- None beyond the existing missing governed execution-start gate and real
  executor integration.

Docs updated:
- `README.md`
- `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/session-handoff.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/cadence/business-memory.md`

## 2026-06-03 - Add read-only resume verifier

Summary:
- Added `verify-resume`, a read-only pickup gate that emits a
  `resume-verification.v1` packet before a fresh session continues a handoff.
- The packet checks handoff signature and claimed state, clean-square evidence,
  current repo branch and `HEAD`, dirty-worktree state, active Cadence brake,
  active epoch state, and pickup-policy evidence with stable blocker codes and
  a recommended next action.
- Prepared handoffs now carry a structured resume snapshot binding so stale
  branch/head state can be rejected without parsing seed-message text.
- Review follow-up binds resume verification to the persisted snapshot record
  and signed handoff message, validates claimed-record content, returns stable
  blocker packets for malformed readable evidence, and recommends approval
  before claim for approval-gated ready handoffs.

Completed slices:
- Task 7 current-tree implementation: resume verifier and handoff pickup gate.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Fresh-session pickup is now locally verifiable, but new-session
  launch, host context-pressure detection, real executor invocation,
  autonomous execution, auto-merge, release, and package publication remain
  blocked.

Evidence:
- `python -m py_compile codex_cadence/handoff_loop.py codex_cadence/cli.py codex_cadence/repo_state.py codex_cadence/epochs.py`
- `python -m unittest tests.test_handoff_loop -v`
- `python -m unittest tests.test_epochs -v`
- `python -m unittest tests.test_cadence tests.test_handoff_loop tests.test_epochs -v`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Resume verification is a gate packet only; an external operator or
  orchestrator still has to claim handoffs, launch sessions, and perform any
  recommended next action.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-02 - Add operator-approved Git/PR materialization

Summary:
- Added `git-pr-materialize`, which consumes a reviewed `git-pr-plan.v1`
  packet plus exact target-bound operator approval, rechecks current Git state,
  branch policy, complete local-diff coverage by materialized-change evidence,
  PR body preflight, and evidence freshness before any write-side Git or `gh`
  side effects.
- Materialization now appends `git_pr_materialization_intent` and
  `git_pr_materialization_result` audit events, creates the proposed branch
  from the already-materialized current commit without switching the checkout,
  pushes it with Git hook verification disabled for that push, and creates or
  updates a PR through `gh` only after gates pass. Existing PR updates first
  verify the PR head/base through a read-only `gh pr view` preflight. Blocked
  and completed attempts emit `git-pr-materialization.v1` packets.
- Review follow-up tightened materialized-evidence coverage, bound approval to
  the selected remote and PR target, avoided checkout hooks by creating the
  branch without switching, pushed with Git hook verification disabled for that
  push, and made result-audit checksums match returned packets.
- Second review follow-up refreshed stale docs, added successful existing-PR
  update coverage, refreshed live repository state before emitting post-side
  effect packets, and converted temporary PR body file creation failures into
  structured blocked materialization packets with replayable result audit.
- Third review follow-up added a read-only remote-branch preflight for PR-create
  materialization so approved plans cannot update an existing remote branch
  when creating a new PR.
- Fourth review follow-up moved materialization approval from public checksum
  tokens to HMAC tokens backed by
  `CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET` and removed expected-token
  disclosure from returned packets.

Completed slices:
- Task 6 current-tree implementation: operator-approved Git/PR materialization.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: PR materialization is now locally gated and audited, but real executor
  invocation, resume verification, autonomous execution, auto-merge, release,
  and package publication remain blocked.

Evidence:
- `python -m unittest tests.test_git_pr_plan tests.test_audit_replay`
- `python -m unittest tests.test_git_pr_plan tests.test_audit_replay -v`
- `python -m unittest tests.test_git_pr_plan tests.test_audit_replay tests.test_cadence tests.test_pr_readiness -v`
- `python -m unittest tests.test_ci_checks -v`
- `python -m py_compile codex_cadence/git_pr_plan.py codex_cadence/cli.py codex_cadence/policy_audit.py codex_cadence/pr_readiness.py scripts/validate_protocol.py`
- `python scripts/validate_protocol.py`
- `python scripts/ci_smoke.py`
- `git diff --check`

New risks or blockers:
- Approval is HMAC-backed by a local operator secret, but still does not bind an
  authenticated approver identity or hash-chain-backed authorization.
- The command materializes an already-clean current commit; dirty-worktree
  commit creation remains outside scope.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/session-handoff.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-02 - Add read-only GitHub evidence sync

Summary:
- Added an explicit read-only `github-evidence-sync` path that captures live PR
  metadata, status checks, and review threads into saved local evidence files.
- Candidate discovery can now turn saved PR check failures into
  `pr_check_failure` candidates, and PR readiness can block on unresolved
  actionable current review-thread comments from saved evidence.

Completed slices:
- Task 5 current-tree implementation: read-only GitHub evidence sync and
  feedback candidates.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: CI and review evidence can now enter the local loop from explicit
  read-only sync, but real executor invocation, operator-approved Git/PR
  materialization, merge, release, package publication, resume verification,
  and unattended loop execution remain blocked.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_github_evidence_sync_writes_read_only_evidence_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_missing_gh_returns_blocker_without_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_malformed_repo_returns_blocker_without_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_malformed_json_returns_blocker_without_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_incomplete_review_threads_returns_blocker_without_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_write_failure_removes_partial_files tests.test_cadence.CadenceCliTests.test_github_evidence_sync_failing_gh_returns_blockers_without_files tests.test_cadence.CadenceCliTests.test_discover_candidates_accepts_pr_json_file tests.test_cadence.CadenceCliTests.test_discover_candidates_accepts_review_threads_file tests.test_candidates.CandidateDiscoveryBudgetTests.test_pr_json_failed_checks_create_execution_candidates tests.test_candidates.CandidateDiscoveryBudgetTests.test_pr_json_failed_check_fingerprints_are_stable_and_deduped tests.test_pr_readiness.PrReadinessTests.test_unresolved_actionable_review_threads_block_readiness tests.test_pr_readiness.PrReadinessTests.test_malformed_review_threads_block_readiness tests.test_pr_readiness.PrReadinessTests.test_incomplete_review_threads_block_readiness tests.test_pr_readiness.PrReadinessTests.test_cli_pr_readiness_reads_review_threads_file -v`
- `python -m py_compile codex_cadence/github_evidence.py codex_cadence/cli.py codex_cadence/candidates.py codex_cadence/pr_readiness.py`

New risks or blockers:
- Sync is explicit, read-only, and local-file based; Task 6 still needs to
  re-check freshness and branch policy immediately before any approved branch,
  commit, push, or PR action.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-06-02 - Add local branch policy to dry-run planning

Summary:
- Extended local `cadence-loop-policy.v1` handling with a dry-run
  `branch_policy` object.
- `loop-tick --emit-executor-task` now carries branch policy into executor task
  packets, and task-packet validation rejects malformed task-carried branch
  policy.
- `git-pr-plan` enforces task-carried and optional local `--policy-file` branch
  policy as additive dry-run blockers for disallowed base branches, denied
  target branches, missing required generated-branch prefixes, and current
  `main` checkouts when policy forbids them.

Completed slices:
- Task 4 current-tree implementation: local branch policy for executor task
  packets and dry-run Git/PR planning.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Branch policy is now local and dry-run enforceable, but live GitHub
  sync, operator-approved Git/PR materialization, merge, release, package
  publication, real executor invocation, and unattended loop execution remain
  blocked.

Evidence:
- `python -m unittest tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_emits_executor_branch_policy tests.test_cadence.CadenceCliTests.test_loop_tick_policy_file_rejects_malformed_branch_policy tests.test_executor_contract.ExecutorContractTests.test_task_packet_rejects_malformed_branch_policy tests.test_git_pr_plan.GitPrPlanTests.test_blocks_task_carried_branch_policy_violations tests.test_git_pr_plan.GitPrPlanTests.test_blocks_task_carried_branch_policy_current_main tests.test_git_pr_plan.GitPrPlanTests.test_policy_file_branch_policy_blocks_git_pr_plan -v`

New risks or blockers:
- Branch policy is not a live Git/PR materialization gate yet; Task 6 still
  needs to re-check branch policy immediately before any approved branch,
  commit, push, or PR action.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmap.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/session-handoff.md`
- `docs/progress-log.md`

## 2026-06-02 - Wire executor results into epoch closeout

Summary:
- Added `closeout-executor-result` and the `executor-epoch-closeout.v1` packet.
- The command consumes local task/result/snapshot-after packets, validates
  executor evidence, records partial task success while other epoch tasks
  remain, completes terminal successful epochs, fails failed/blocked/stopped or
  policy-violating evidence with stable reason codes, blocks stale or
  conflicting epoch state, and can embed a dry-run `git-pr-plan.v1` packet after
  terminal success.
- Added compact `executor_epoch_closeout` audit records and audit-replay support
  with snapshot-after path/checksum anchors.
- Addressed review findings by requiring snapshot-after freshness at or after
  executor result `ended_at` and validating optional PR-template inputs before
  terminal epoch state mutation.

Completed slices:
- Task 3 current-tree implementation: executor result closeout and next decision.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: Epoch closeout is now wired locally, but real executor invocation,
  live GitHub sync, operator-approved Git/PR materialization, merge, release,
  package publication, and unattended loop execution remain blocked.

Evidence:
- `python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/policy_audit.py`
- `python -m unittest tests.test_epochs.EpochLifecycleTests.test_executor_result_failure_reason_uses_stable_codes -v`
- `python -m unittest tests.test_audit_replay.AuditReplayCliTests.test_valid_records_are_counted_by_event_type -v`
- `python -m unittest tests.test_cadence.CadenceCliTests.test_closeout_executor_result_completes_epoch_and_embeds_dry_run_git_pr_plan tests.test_cadence.CadenceCliTests.test_closeout_executor_result_fails_epoch_for_blocked_evidence tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_stale_task_snapshot_without_closing_epoch tests.test_cadence.CadenceCliTests.test_closeout_executor_result_blocks_active_epoch_conflict tests.test_cadence.CadenceCliTests.test_closeout_executor_result_rerun_reports_already_closed_without_second_completion -v`

New risks or blockers:
- At the time of this entry, branch policy was still missing; the later Task 4
  entry adds local dry-run branch-policy enforcement.
- The command closes local epoch state only; it does not invoke real executors or
  perform live Git/PR actions.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/cadence/business-memory.md`
- `docs/progress-log.md`

## 2026-06-02 - Extend roadmap and refresh handoff after PR 64

Summary:
- Updated the session handoff and business-memory backlog to record PR #64 as
  merged and make Task 3 the next implementation slice.
- Extended the roadmap from five tasks to seven by adding operator-approved
  Git/PR materialization and resume verification as follow-on gates.
- Updated protocol validation so the handoff freshness guard requires the
  post-merge fixture boundary instead of the pre-merge Task 2 branch wording.
- Review-agent follow-up tightened the same guard to forbid retired Task 2
  branch tokens, required the Task 3 next-action wording, and clarified Task 6
  as a live-executor gate.

Completed slices:
- None. This was planning and handoff alignment only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: The update clarifies sequencing, but it does not implement epoch
  closeout, branch policy, live GitHub sync, Git/PR materialization, resume
  verification, real executor invocation, or unattended loop execution.

Evidence:
- `python -m py_compile scripts/validate_protocol.py`
- `python scripts/validate_protocol.py`
- `python -m unittest tests.test_candidates.CandidateDiscoveryGovernanceTests.test_repo_business_memory_current_entries_seed_controlled_executor_backlog_and_parse_without_warnings -v`
- `python -m unittest tests.test_ci_checks.CiChecksTests.test_protocol_validator_accepts_current_repo tests.test_ci_checks.CiChecksTests.test_protocol_validator_rejects_stale_handoff_pr_state -v`
- `git diff --check`

New risks or blockers:
- None beyond the existing gates documented in the roadmap.

Docs updated:
- `docs/session-handoff.md`
- `docs/cadence/business-memory.md`
- `docs/roadmaps/2026-06-02-next-five-tasks-roadmap.md`
- `docs/progress-log.md`
- `scripts/validate_protocol.py`
- `tests/test_ci_checks.py`

## 2026-06-02 - Add controlled executor fixture runner

Summary:
- Added the `run-controlled-executor-fixture` CLI path and
  `controlled-executor-fixture-run.v1` packet for tests/examples.
- The runner validates a generic executor task packet and formatted fixture
  command before launch, runs a fake external executor component with a timeout,
  requires result evidence at the approved path, validates active-brake stops,
  and appends invocation/result-validation audit records.
- Review follow-up made the fixture boundary structural: the runner now
  requires the current Python interpreter plus bundled fixture script by
  absolute path, uses argv execution with `shell=False`, keeps result evidence
  under the runtime root, refuses stale result files, preserves stopped
  evidence over observed runtime cleanup overhead, and rejects successful
  evidence from nonzero fixture exits.
- Expanded disabled executor permissions to block merge, release, and
  package-publication command forms, including git shell aliases after command
  separators and versioned Python `twine` launchers, in addition to commit,
  push, PR creation, and forbidden head changes.
- Bot review follow-up made template placeholder formatting argv-safe for paths
  with spaces, moved the fixture CLI to the runtime-root-only guard, converted
  missing repos and malformed fixture evidence into controlled invalid packets,
  aligned the example fixture process exit with evidence, and allowed read-only
  `git tag` listing/verification while still blocking tag mutation.

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
