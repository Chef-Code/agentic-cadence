#!/usr/bin/env python3
"""Validate that protocol docs and CLI enforcement stay aligned."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BUSINESS_MEMORY_TAXONOMY = (
    "direction",
    "business_rule",
    "problem",
    "feature",
    "nice_to_have",
    "risk",
    "constraint",
    "unknown",
)
EXPECTED_BUSINESS_MEMORY_STATUSES = ("active", "fulfilled", "superseded")

REQUIRED_TOKENS = {
    "SKILL.md": (
        "name: agentic-cadence",
        "create-handoff` requires `--task-type`",
        "handoff has no persisted estimate",
        "approve-handoff",
        "--snapshot-before-file",
        "--snapshot-after-file",
        "empty administrative checkpoint epoch",
        "fail-epoch",
        "--ci-status green",
        "max_minutes_per_epoch",
        "max_epochs_without_user_approval",
        "Medium uncertainty",
        "cadence.state",
        "PLAY_ON",
        "HUDDLE",
        "TIMEOUT",
        "legacy `brake.status",
        "brake remains `DRIVE`",
        "Self-Evolution",
        "openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c",
        "OPENAI_API_KEY",
        "free preflight",
        "codex_review_preflight.py",
        "codex-review-skip",
        "codex-review-elect",
        "codex-review-force",
        "codex-review:v1",
        "canonical workflow-owned",
        "PR-number-scoped concurrency cancellation",
        "docs-only changes",
        "`synchronize` cancels obsolete in-flight elected reviews",
        "unrelated label events",
        "Guardrail changes",
        "manual operator review",
        "discover-candidates",
        "--discovery-mode off",
        "--proposal-allowance elect",
        "--review-threads-file",
        "--pr-template-file",
        "pr-body-preflight",
        "--body-file",
        "publish_pr_body",
        "update_pr_body",
        "provide_template_or_sections",
        "release-dry-run",
        ".github/workflows/release-dry-run.yml",
        "group: release-dry-run-${{ inputs.tag }}",
        "timeout-minutes: 10",
        "operator_confirmation_required",
        "release-dry-run.json",
        "release-notes.md",
        "create_tag_after_operator_confirmation",
        "do_not_publish_package",
        "reviewThreads",
        "isResolved",
        "isOutdated",
        "outdated",
        "missing required PR body or template sections",
        "rewrite the PR body",
        "create a PR",
        "docs/cadence/business-memory.md",
        "source: business_memory",
        "maturity: discovery",
        "classification",
        "classification_confidence",
        "Status",
        "active",
        "fulfilled",
        "superseded",
        "classification: unknown",
        "unclassified_signal",
        "direction",
        "business_rule",
        "problem",
        "feature",
        "nice_to_have",
        "risk",
        "constraint",
        "unknown",
        "repo_anchors: []",
        "evidence.path",
        "evidence.line",
        "evidence.heading",
        "discovery-only",
        "--max-business-memory-candidates",
        "audit-replay",
        "audit-replay.v1",
        "audit_line_invalid_json",
        "audit_schema_version_unsupported",
        "clean audit replay",
        "command_policy",
        "compound commands",
        "provide_runtime_root",
        "status: stopped",
        "shell grouping",
        "command substitutions",
    ),
    "README.md": (
        "unreleased read-only audit replay with local hash-chain integrity evidence",
        "--root examples/first-run/work/runtime validate-executor-result",
        "compound commands",
        "shell grouping",
        "command substitutions",
        "shell-wrapper payloads",
        "provide_runtime_root",
        "run-controlled-executor-fixture",
        "executor_fixture_invocation",
        "controlled-executor-fixture-run.v1",
        "absolute path",
        "current Python interpreter",
        "inside the runtime root",
        "github-evidence-sync",
        "--review-threads-file",
        "--pr-json-file",
        "branch-policy-gated dry-run Git/PR planning",
        "git-pr-dirty-materialization-plan",
        "git-pr-dirty-materialization-plan.v1",
        "--closeout-file",
        "target_checksum",
        "dirty-worktree fingerprint",
        "git-pr-materialize",
        "git-pr-materialization.v1",
        "git_pr_materialization_intent",
        "read-only `verify-resume`",
        "read-only `resume-continuation`",
        "resume-continuation.v1",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "executor_started: false",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator-approval-verification.v1",
        "operator_approval_verification",
        "CADENCE_OPERATOR_APPROVAL_SECRET",
        "--approval-secret-env",
        "read-only `review-response-plan`",
        "review-response-plan.v1",
        "read-only `executor-invocation-readiness`",
        "executor-invocation-readiness.v1",
        "executor-invocation-plan",
        "executor-invocation-plan.v1",
        "invoke-real-executor",
        "real-executor-invocation.v1",
        "real_executor_invocation",
        "real-executor-invocations",
        "real-invocation closeout binding",
        "closeout-executor-result --real-invocation-file",
        "exactly one evidence artifact",
        "real_executor_invocation_record",
        "invocation_record_checksum",
        "update_real_executor_invocation_closeout",
        "worktree_fingerprint_checksum",
        "plan_checksum",
        "plan_target_checksum",
        "rechecked_plan_checksum",
        "result_evidence_checksum",
        "result_file",
        "invocation_cwd",
        "plan_file",
        "command.expected_result_path",
        "audit_chain.chain_head",
        "repository_before",
        "repository_after",
        "rollback.checksum",
        "stdout_log",
        "stderr_log",
        "evidence_only",
        "materialized_changes",
        "plan_packet_stale",
        "plan_not_invocable",
        "approval_recheck_failed",
        "rollback_recheck_failed",
        "runtime_root_unsafe",
        "repo_inspection_failed",
        "executor_process_timeout",
        "executor_process_failed",
        "executor_result_stale",
        "executor_result_missing",
        "local_branch_refs",
        "unexpected_repo_modification",
        "materialized_change_evidence_missing",
        "--real-invocation-file",
        "invocation_record_missing",
        "invocation_checksum_mismatch",
        "invocation_epoch_mismatch",
        "invocation_result_missing",
        "invocation_result_invalid",
        "materialized_change_mismatch",
        "audit_chain_mismatch",
        "ownership_closeout_blocked",
        "run_record_audit_append_failed",
        "recover_closeout_audit",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "controlled_loop_tick_audit_appended",
        "controlled_loop_tick_audit_append_failed",
        "does_not_retry_executor",
        "does_not_rewrite_invocation_or_closeout_records",
        "task_file_unreadable",
        "executor_task_invalid",
        "task_checksum_mismatch",
        "approval_invalid",
        "approval_target_mismatch",
        "approval_identity_invalid",
        "approval_timestamp_invalid",
        "audit_chain_not_clean",
        "rollback_evidence_missing",
        "adapter_contract_invalid",
        "executor_command_denied",
        "executor_timeout_invalid",
        "active_epoch_missing",
        "ownership_epoch_mismatch",
        "result_path_outside_runtime",
        "complete-work-ownership-from-closeout",
        "executor_closeout_checksum",
        "ownership_closeout_not_completed",
        "ownership_closeout_checksum_mismatch",
        "ownership_closeout_task_checksum_mismatch",
        "ownership_candidate_mismatch",
        "ownership_role_mismatch",
        "read-only `work-ownership-status`",
        "read-only `validate-work-ownership`",
        "work-ownership-status.v1",
        "work-ownership-validation.v1",
    ),
    "docs/protocol.md": (
        "New handoffs must declare `--task-type`",
        "five core concepts",
        "`status.brake.status` remains present for compatibility",
        "cadence.legacy_brake",
        "PLAY_ON",
        "HUDDLE",
        "TIMEOUT",
        "handoffs without persisted estimates",
        "approve-handoff",
        "pickup_approved=true",
        "--snapshot-before-file",
        "--snapshot-after-file",
        "empty administrative checkpoint",
        "fail-epoch",
        "--ci-status green",
        "max_minutes_per_epoch",
        "max_epochs_without_user_approval",
        "next_epoch_requires",
        "Medium uncertainty",
        "persisted `CONTINUE` self-check",
        "Self-evolution execution tasks are blocked",
        ".github/workflows/codex-review.yml",
        "openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c",
        "sandbox: read-only",
        "free preflight",
        "codex_review_preflight.py",
        "codex-review-skip",
        "codex-review-elect",
        "codex-review-force",
        "codex-review:v1",
        "canonical workflow-owned",
        "classify changed paths",
        "Docs-only PRs",
        "`synchronize` is a cancel-only event",
        "Unrelated label events",
        "Guardrail changes",
        "manual operator review",
        "Candidate discovery is read-only",
        "Reference implementation: `codex_cadence/cli.py`",
        "`scripts/cadence.py` is a source-tree wrapper",
        "`scripts/transmission.py` delegates to `transmission_control.cli`",
        "discovery_mode: off",
        "proposal allowance",
        "--pr-json-file",
        "pr_check_failure",
        "github-evidence-sync",
        "does not write partial evidence",
        "GitHub CLI spawn failure",
        "GitHub cursors",
        "outside the current",
        "--review-threads-file",
        "--pr-template-file",
        "pr-body-preflight",
        "--body-file",
        "provide_template_or_sections",
        "git-pr-dirty-materialization-plan",
        "git-pr-dirty-materialization-plan.v1",
        "git_pr_dirty_materialization_plan",
        "--closeout-file",
        "target_checksum",
        "closeout_invocation_mismatch",
        "dirty_worktree_fingerprint_mismatch",
        "materialized_change_files_mismatch",
        "release-dry-run",
        ".github/workflows/release-dry-run.yml",
        "group: release-dry-run-${{ inputs.tag }}",
        "timeout-minutes: 10",
        "operator_confirmation_required",
        "release-dry-run.json",
        "release-notes.md",
        "create_github_release_after_operator_confirmation",
        "do_not_publish_package",
        "reviewThreads",
        "isResolved",
        "isOutdated",
        "outdated",
        "missing template sections",
        "rewrite the PR body",
        "rewrite the body file",
        "create a PR",
        "docs/cadence/business-memory.md",
        "source: business_memory",
        "maturity: discovery",
        "classification",
        "classification_confidence",
        "Status",
        "active",
        "fulfilled",
        "superseded",
        "classification: unknown",
        "unclassified_signal",
        "direction",
        "business_rule",
        "problem",
        "feature",
        "nice_to_have",
        "risk",
        "constraint",
        "unknown",
        "repo_anchors: []",
        "evidence.path",
        "evidence.line",
        "evidence.heading",
        "repo anchors",
        "discovery-only",
        "--max-business-memory-candidates",
        "audit-replay",
        "audit-replay.v1",
        "audit_chain_version",
        "chain_index",
        "previous_event_hash",
        "event_hash",
        "chain_head",
        "chain_records",
        "legacy_chain_roots",
        "audit_line_invalid_json",
        "audit_schema_version_unsupported",
        "audit_chain_missing",
        "audit_chain_broken",
        "audit_event_hash_mismatch",
        "audit_chain_index_duplicate",
        "unsupported_audit_chain_record",
        "start_new_audit_chain",
        "continue_with_legacy_chain_root",
        "repair_audit_history",
        "upgrade_cadence",
        "inspect_audit_log",
        "no target repository `cwd`",
        "allowed_commands",
        "denied_commands",
        "command_policy",
        "stop_active_loop",
        "compound-command",
        "shell-grouping",
        "command-substitution",
        "effective command segment",
        "shell grouping",
        "command substitutions",
        "shell-wrapper payloads",
        "provide_runtime_root",
        "run-controlled-executor-fixture",
        "executor_fixture_invocation",
        "controlled-executor-fixture-run.v1",
        "git-pr-materialize",
        "git-pr-materialization.v1",
        "git_pr_materialization_intent",
        "git_pr_materialization_result",
        "plan_checksum",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "approve-executor-task:<task-packet-checksum>",
        "executor_started: false",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator-approval-verification.v1",
        "operator_approval_verification",
        "CADENCE_OPERATOR_APPROVAL_SECRET",
        "--approval-secret-env",
        "operator_approval_file_unreadable",
        "operator_approval_invalid",
        "operator_approval_schema_invalid",
        "operator_approval_target_invalid",
        "operator_approval_purpose_missing",
        "operator_approval_window_too_long",
        "operator_approval_operator_missing",
        "operator_approval_signature_invalid",
        "signature_verified: true",
        "checked_at",
        "repo_inspection_failed",
        "brake_state_invalid",
        "audit_append_failed",
        "epoch_rollback_failed",
        "inspect_runtime_state",
        "review-response-plan",
        "review-response-plan.v1",
        "executor-invocation-readiness",
        "executor-invocation-readiness.v1",
        "executor-invocation-plan",
        "executor-invocation-plan.v1",
        "executor_invocation_plan",
        "executor-invocation-target.v1",
        "executor-adapter.v1",
        "executor-rollback.v1",
        "invoke-real-executor",
        "real-executor-invocation.v1",
        "real_executor_invocation",
        "real-executor-invocations",
        "closeout-executor-result --real-invocation-file",
        "real_executor_invocation_record",
        "invocation_record_checksum",
        "plan_checksum",
        "plan_target_checksum",
        "rechecked_plan_checksum",
        "result_evidence_checksum",
        "result_file",
        "command.expected_result_path",
        "audit_chain",
        "chain_head",
        "evidence_only",
        "materialized_changes",
        "plan_packet_stale",
        "plan_not_invocable",
        "approval_recheck_failed",
        "rollback_recheck_failed",
        "runtime_root_unsafe",
        "repo_inspection_failed",
        "executor_process_timeout",
        "executor_process_failed",
        "executor_result_stale",
        "executor_result_missing",
        "local_branch_refs",
        "unexpected_repo_modification",
        "materialized_change_evidence_missing",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "controlled_tick_status",
        "controlled_loop_tick_audit_appended",
        "controlled_loop_tick_audit_append_failed",
        "recover_controlled_tick_audit",
        "composes_existing_local_evidence_only",
        "does_not_retry_executor",
        "does_not_rewrite_invocation_or_closeout_records",
        "loop_tick_evidence_missing",
        "task_evidence_missing",
        "execution_start_evidence_missing",
        "readiness_evidence_missing",
        "invocation_plan_evidence_missing",
        "real_invocation_evidence_missing",
        "result_evidence_missing",
        "snapshot_after_evidence_missing",
        "closeout_evidence_missing",
        "git_pr_plan_evidence_missing",
        "controlled_tick_packet_mismatch",
        "loop_tick_identity_missing",
        "loop_tick_task_mismatch",
        "loop_tick_not_ready",
        "execution_start_invalid",
        "execution_start_task_mismatch",
        "snapshot_after_invalid",
        "readiness_not_invocable",
        "readiness_task_mismatch",
        "readiness_epoch_mismatch",
        "invocation_plan_not_invocable",
        "invocation_plan_readiness_mismatch",
        "real_invocation_invalid",
        "real_invocation_identity_missing",
        "real_invocation_plan_mismatch",
        "real_invocation_record_mismatch",
        "real_invocation_result_mismatch",
        "real_invocation_closeout_mismatch",
        "result_task_mismatch",
        "closeout_invalid",
        "closeout_epoch_mismatch",
        "closeout_task_mismatch",
        "closeout_result_mismatch",
        "closeout_snapshot_mismatch",
        "closeout_invocation_mismatch",
        "closeout_validation_mismatch",
        "closeout_not_completed",
        "git_pr_plan_unanchored",
        "git_pr_plan_mismatch",
        "git_pr_plan_not_ready",
        "git_pr_plan_not_dry_run",
        "git_pr_plan_operator_confirmation_missing",
        "git_pr_plan_side_effects_present",
        "git_pr_plan_approval_state_invalid",
        "git_pr_plan_execution_authority_invalid",
        "git_pr_plan_proposed_branch_missing",
        "git_pr_plan_proposed_pr_title_missing",
        "git_pr_plan_proposed_pr_body_missing",
        "task_file_unreadable",
        "executor_task_invalid",
        "task_checksum_mismatch",
        "approval_invalid",
        "approval_schema_invalid",
        "approval_target_invalid",
        "approval_target_mismatch",
        "approval_purpose_missing",
        "approval_purpose_mismatch",
        "approval_identity_invalid",
        "approval_timestamp_invalid",
        "approval_window_too_long",
        "approval_issued_in_future",
        "approval_signature_invalid",
        "audit_chain_not_clean",
        "rollback_evidence_missing",
        "rollback_policy_invalid",
        "adapter_contract_invalid",
        "executor_command_denied",
        "executor_timeout_invalid",
        "ownership_record_missing",
        "ownership_record_unreadable",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_required_field_missing",
        "ownership_field_type_invalid",
        "ownership_id_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
        "ownership_stale",
        "ownership_closed",
        "ownership_repo_mismatch",
        "ownership_branch_mismatch",
        "ownership_task_mismatch",
        "ownership_candidate_mismatch",
        "ownership_epoch_mismatch",
        "ownership_role_mismatch",
        "ownership_head_mismatch",
        "complete-work-ownership-from-closeout",
        "ownership_closeout_unreadable",
        "ownership_closeout_invalid",
        "ownership_closeout_schema_unsupported",
        "ownership_closeout_packet_invalid",
        "ownership_closeout_epoch_missing",
        "executor_closeout_file",
        "executor_closeout_checksum",
        "executor_closeout_status",
        "ownership_closeout_not_completed",
        "ownership_closeout_checksum_mismatch",
        "ownership_closeout_task_missing",
        "ownership_closeout_task_unreadable",
        "ownership_closeout_task_invalid",
        "ownership_closeout_task_checksum_mismatch",
        "duplicate_active_ownership",
        "invoke_real_executor",
        "repo_path_invalid",
        "repo_path_mismatch",
        "repo_branch_mismatch",
        "repo_head_mismatch",
        "active_epoch_missing",
        "active_epoch_conflict",
        "active_epoch_invalid",
        "active_epoch_task_missing",
        "active_epoch_task_duplicate",
        "active_epoch_task_completed",
        "task_checksum_missing",
        "task_checksum_mismatch",
        "branch_policy_current_branch_main_disallowed",
        "required_checks_invalid",
        "result_path_missing",
        "result_path_mismatch",
        "result_path_outside_runtime",
        "role_readiness_unreadable",
        "role_readiness_blocked",
        "resume-continuation",
        "resume-continuation.v1",
        "resume_verification_anchor_mismatch",
        "--max-resume-age-minutes",
        "work-ownership-status",
        "validate-work-ownership",
        "work-ownership.v1",
        "work-ownership-status.v1",
        "work-ownership-validation.v1",
        "duplicate_active_ownership",
        "repo_inspection_failed",
        "ownership_registry_state_invalid",
        "ownership_record_missing",
        "ownership_record_unreadable",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_required_field_missing",
        "ownership_field_type_invalid",
        "ownership_id_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
        "ownership_stale",
        "ownership_closed",
        "ownership_repo_mismatch",
        "ownership_branch_mismatch",
        "ownership_task_mismatch",
        "does_not_invoke_review_agents_or_paid_review",
        "shell=False",
        "inside the runtime root",
        "current Python interpreter",
        "untrusted Python executable paths",
        "git shell-alias",
        "gh pr merge",
        "versioned `python3.x`",
        "yarn publish",
        "hatch publish",
        "flit publish",
    ),
    "docs/roadmap.md": (
        "audit-replay with local hash-chain integrity evidence",
        "active-stop result-validation controls",
        "local branch policy for dry-run Git/PR planning",
        "read-only GitHub evidence sync",
        "operator-approved Git/PR materialization",
        "authenticated local operator approval identity evidence",
        "governed execution-start epoch gating",
        "start-governed-execution",
        "execution-start.v1",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator_approval_verification",
        "git-pr-materialize",
        "github-evidence-sync",
        "read-only review-response planning",
        "review-response-plan",
        "read-only executor-invocation-readiness and invocation-plan evidence",
        "executor-invocation-readiness",
        "executor-invocation-plan",
        "operator approval identity",
        "adapter metadata",
        "rollback evidence",
        "controlled real executor invocation evidence",
        "real-invocation closeout binding",
        "controlled single-tick run packet evidence",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "Current unattended-operation confidence: 25%",
        "Current confidence for unattended continuous operation is 25%",
        "read-only resume continuation",
        "resume-continuation.v1",
        "Local work ownership registry",
        "work-ownership-status",
        "work-ownership-validation.v1",
        "continuous GitHub reconciliation and write-side PR/review actions",
        "local audit hash-chain",
    ),
    "docs/autonomous-loop-readiness.md": (
        "Local policy/audit controls",
        "task-carried branch policy",
        "Partial, read-only evidence capture",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator_approval_verification",
        "dry-run `git-pr-plan`",
        "git-pr-materialize",
        "operator-approved only",
        "start-governed-execution",
        "execution-start.v1",
        "executor_started: false",
        "read-only resume continuation",
        "resume-continuation.v1",
        "review-response-plan.v1",
        "executor-invocation-readiness.v1",
        "executor-invocation-plan",
        "executor-invocation-plan.v1",
        "adapter metadata",
        "rollback evidence",
        "controlled real executor invocation evidence",
        "real-invocation closeout binding",
        "real-executor-invocation.v1",
        "controlled single-tick run packet evidence",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "Current unattended-operation confidence: 25%",
        "Current rating: 25%",
        "invoke_real_executor",
        "Local work ownership",
        "work-ownership.v1",
        "duplicate active ownership",
    ),
    "docs/implementation-slices.md": (
        "Active execution controls are partial",
        "branch policy is carried into emitted executor task packets",
        "dry-run Git/PR transition plan",
        "blocked no materialized changes tied to result evidence",
        "github-evidence-sync",
        "pr_check_failure",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator_approval_verification",
        "git-pr-materialize",
        "target-bound HMAC operator approval",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "executor_started: false",
        "review-response-plan",
        "executor-invocation-readiness",
        "executor-invocation-readiness.v1",
        "invocation-plan evidence",
        "executor-invocation-plan.v1",
        "adapter metadata",
        "rollback evidence",
        "Task 22 added real executor run closeout binding in `main` via PR #90",
        "Task 23 added controlled single-tick run packet evidence",
        "complete-work-ownership-from-closeout",
        "valid completed `executor_epoch_closeout` evidence",
        "saved task checksum",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "controlled single-tick run packet evidence",
        "docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md",
        "docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md",
        "The current confidence rating is 25%",
        "--real-invocation-file",
        "resume-continuation",
        "resume-continuation.v1",
        "work-ownership-status",
        "work-ownership.v1",
        "duplicate active ownership",
    ),
    "docs/designs/2026-06-01-git-pr-dry-run-plan-design.md": (
        "git-pr-plan",
        'schema_version: "git-pr-plan.v1"',
        "dry_run: true",
        "operator_confirmation_required: true",
        "side_effects: []",
        "Suggested commands are never executed by Cadence",
        'approval_state: "not_approved"',
        'execution_authority: "none"',
        'merge_readiness: "not_evaluated"',
        "materialized_change_evidence",
        "provide_runtime_root",
        "current checkout is on a branch, not detached",
        "base branch resolves locally to a commit",
        "generated branch does not already exist locally",
        "No `git checkout`, `git switch`, `git commit`, `git push`, or `gh pr create`",
        "No GitHub API calls",
    ),
    "docs/progress-log.md": (
        "compound shell commands",
        "shell grouping",
        "command substitutions",
        "shell-wrapper payloads",
        "provide_runtime_root",
        "github-evidence-sync",
        "pr_check_failure",
        "git-pr-materialize",
        "git-pr-materialization.v1",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator-approval-verification.v1",
        "operator_approval_verification",
        "review-response-plan.v1",
        "executor-invocation-readiness.v1",
        "executor-invocation-plan",
        "executor-invocation-plan.v1",
        "invoke-real-executor",
        "real-executor-invocation.v1",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "Task 23: Add Controlled Single-Tick Run Packet",
        "does_not_retry_executor",
        "Task 24: Add Closeout-Bound Ownership Completion Evidence",
        "complete-work-ownership-from-closeout",
        "executor_closeout_checksum",
        "Task 25: Add Dirty-Worktree Git/PR Materialization Plan Binding",
        "git-pr-dirty-materialization-plan",
        "git-pr-dirty-materialization-plan.v1",
        "--closeout-file",
        "closeout_invocation_mismatch",
        "dirty_worktree_fingerprint_mismatch",
        "materialized_change_files_mismatch",
        "test_complete_work_ownership_from_closeout_blocks_mutated_task_file_before_move",
        "test_work_ownership_audit_rejects_closeout_anchors_on_non_close_actions",
        "Task 21: Add Controlled Real Executor Invocation Runner",
        "materialized_changes",
        "resume-continuation.v1",
        "resume_verification_anchor_mismatch",
        "work-ownership.v1",
        "work-ownership-status.v1",
        "work-ownership-validation.v1",
        "duplicate active ownership",
    ),
    "docs/session-handoff.md": (
        "shell grouping",
        "command substitutions",
        "shell-wrapper payloads",
        "provide_runtime_root",
        "The controlled fake executor fixture remains",
        "PR #74 merged read-only review response planning",
        "Tasks 18-22 from `docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md`",
        "branch_policy",
        "github-evidence-sync",
        "git-pr-materialize",
        "git_pr_materialization_intent",
        "git_pr_materialization_result",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "verify-operator-approval",
        "operator-approval.v1",
        "operator_approval_verification",
        "executor_started: false",
        "--pr-json-file",
        "run-controlled-executor-fixture",
        "executor_fixture_invocation",
        "reject stale result files",
        "review-response-plan.v1",
        "resume-continuation.v1",
        "work-ownership.v1",
        "work-ownership-status.v1",
        "work-ownership-validation.v1",
        "role-readiness.v1",
        "executor-invocation-readiness.v1",
        "executor-invocation-plan.v1",
        "PR #87 merged authenticated operator approval identity evidence",
        "PR #88 merged read-only real executor invocation plan evidence",
        "PR #89 merged controlled real executor invocation evidence",
        "invoke-real-executor",
        "real-executor-invocation.v1",
        "PR #90 merged real executor invocation closeout binding",
        "PR #91 merged the Tasks 23-27 roadmap",
        "PR #96 merged review follow-up candidates from saved threads",
        "PR #100 merged review response materialization planning",
        "Start Task 31",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick",
        "controlled_loop_tick_audit_appended",
    ),
    "docs/roadmaps/2026-06-09-tasks-18-22-roadmap.md": (
        "Task 20: Add Real Executor Invocation Plan And Approval Binding",
        "Implemented in `main` via PR #88",
        "Implemented in `main` via PR #89",
        "Task 22: Bind Real Executor Run Evidence To Closeout And Git/PR Planning",
        "Implemented in `main` via PR #90",
        "Captured starting unattended-operation confidence remained 10%",
        "executor-invocation-plan.v1",
        "operator-approval.v1",
        "real_executor_invocation",
        "approval_target_mismatch",
        "audit_chain_not_clean",
        "rollback_evidence_missing",
        "adapter_contract_invalid",
        "executor-contract-denied commands",
        "executor_command_denied",
        "executor_timeout_invalid",
        "active epoch task checksum",
        "active ownership revalidation",
        "<root>/executor-results",
        "Task 21: Add Controlled Real Executor Invocation Runner",
    ),
    "docs/roadmaps/2026-06-11-tasks-23-27-roadmap.md": (
        "Task 23: Add Controlled Single-Tick Run Packet",
        "Implemented in `main` via PR #92",
        "controlled-loop-tick",
        "controlled-loop-tick.v1",
        "controlled_loop_tick_audit_appended",
        "controlled_loop_tick_audit_append_failed",
        "does_not_retry_executor",
        "does_not_rewrite_invocation_or_closeout_records",
        "Task 24: Add Closeout-Bound Ownership Completion Evidence",
        "Implemented in `main` via PR #93",
        "complete-work-ownership-from-closeout",
        "executor_closeout_checksum",
        "Task 25: Add Dirty-Worktree Git/PR Materialization Plan Binding",
        "Implemented in `main` via PR #94",
        "git-pr-dirty-materialization-plan",
        "git-pr-dirty-materialization-plan.v1",
        "--closeout-file",
        "closeout_invocation_mismatch",
        "dirty_worktree_fingerprint_mismatch",
        "materialized_change_files_mismatch",
        "Task 26: Preserve PR Evidence Freshness In Write-Side Planning",
        "Task 27: Generate Review Follow-Up Candidates From Saved Threads",
        "No continuous unattended loop",
        "No autonomous branch creation, commit, push, PR create/update",
        "Captured starting unattended-operation confidence is 20%",
        "Current unattended-operation confidence after Task 23 is 25%",
    ),
    "docs/roadmaps/2026-06-11-tasks-28-32-roadmap.md": (
        "Tasks 28-32 Roadmap",
        "Task 28: Add Operator-Approved Dirty Commit Materialization",
        "git-pr-dirty-commit-materialize",
        "git-pr-dirty-commit-materialization.v1",
        "Task 29: Bind Dirty Commit Evidence To PR Materialization",
        "git-pr-dirty-commit-materialization.v1",
        "Task 30: Add Review Response Materialization Plan Approval Binding",
        "review-response-materialization-plan.v1",
        "github_write_started: false",
        "Task 31: Add Operator-Approved Review Response Materialization",
        "review-response-materialization.v1",
        "Task 32: Add Post-Write PR Evidence Refresh And Next-Action Gate",
        "post-write-pr-evidence-gate.v1",
        "No autonomous branch creation, commit, push, PR create/update",
        "No review-thread resolution until a later explicit slice",
        "Captured starting unattended-operation confidence is 25%",
    ),
    "codex_cadence/cli.py": (
        "create_parser.add_argument(\"--task-type\"",
        "required=True",
        "approve-handoff",
        "self_check_parser.add_argument(\"--snapshot-after-file\")",
        "fail_epoch_parser",
        "snapshot_parser.add_argument(\"--ci-status\"",
        "continuation_task_limit",
        "effective_max_tasks",
        "completed_continue_count",
        "snapshot_after_checksum",
        "record_lock_path",
        "start_epoch_parser.add_argument(\"--snapshot-before-file\", required=True)",
        "CONTINUE requires brake to remain DRIVE",
        "cadence_state",
        "\"PLAY_ON\"",
        "\"HUDDLE\"",
        "\"TIMEOUT\"",
        "self_evolution_propose_only",
        "discover-candidates",
        "discover_candidates_command",
        "CandidateBudget",
        "business_memory",
        "--max-business-memory-candidates",
        "--pr-json-file",
        "pr_json_file",
        "--review-threads-file",
        "review_threads_file",
        "--pr-template-file",
        "pr_template_file",
        "pr-body-preflight",
        "pr_body_preflight_command",
        "load_pr_body",
        "evaluate_pr_body_preflight",
        "--body-file",
        "release-dry-run",
        "release_dry_run_command",
        "evaluate_release_dry_run",
        "audit-replay",
        "audit_replay_command",
        "guards_runtime_root_only",
        "effective_allowed_commands",
        "effective_denied_commands",
        "stop_active_loop",
        "provide_runtime_root",
        "runtime root is required to validate brake_not_drive stop condition",
        "missing_runtime_root_for_stop",
        "github_evidence_out_dir_safety_issue",
        "github evidence out-dir is inside a git worktree",
        "git-pr-dirty-materialization-plan",
        "git_pr_dirty_materialization_plan_command",
        "evaluate_dirty_git_pr_materialization_plan",
        "expected_base_head",
        "closeout_file",
        "--closeout-file",
        "git-pr-materialize",
        "git_pr_materialize_command",
        "materialize_git_pr_plan",
        "validate_git_pr_plan_dry_run_packet",
        "verify-operator-approval",
        "verify_operator_approval_command",
        "OPERATOR_APPROVAL_SECRET_ENV",
        "operator_approval_secret_from_args",
        "build_operator_approval_verification_packet",
        "operator_approval_verification_audit_record",
        "start-governed-execution",
        "start_governed_execution_command",
        "EXECUTION_START_SCHEMA_VERSION",
        "executor_started",
        "brake_state_invalid",
        "audit_append_failed",
        "epoch_rollback_failed",
        "inspect_runtime_state",
        "review-response-plan",
        "review_response_plan_command",
        "evaluate_review_response_plan",
        "executor-invocation-readiness",
        "executor_invocation_readiness_command",
        "evaluate_executor_invocation_readiness",
        "executor-invocation-plan",
        "executor_invocation_plan_command",
        "build_executor_invocation_plan",
        "invoke-real-executor",
        "invoke_real_executor_command",
        "REAL_EXECUTOR_SIDE_EFFECT_MODES",
        "--real-invocation-file",
        "validate_closeout_real_invocation",
        "_closeout_real_invocation_audit_blockers",
        "real_invocation_blocker",
        "real_executor_invocation_audit_record",
        "audit_append_failed",
        "record_real_executor_invocation",
        "record_real_executor_invocation_blocked",
        "real_executor_invocation_audit_append_failed",
        "invocation_record_write_failed",
        "update_real_executor_invocation_closeout",
        "run_record_audit_append_failed",
        "execution_run_audit_append_failed",
        "execution_run_record_update_rolled_back",
        "execution_run_record_update_unreconciled",
        "recover_closeout_audit",
        "worktree_fingerprint_checksum",
        "invocation_record_missing",
        "invocation_checksum_mismatch",
        "invocation_epoch_mismatch",
        "invocation_result_missing",
        "invocation_result_invalid",
        "materialized_change_mismatch",
        "audit_chain_mismatch",
        "ownership_closeout_blocked",
        "inspect_real_run_blockers",
        "controlled-loop-tick",
        "controlled_loop_tick_command",
        "CONTROLLED_LOOP_TICK_SCHEMA_VERSION",
        "controlled_loop_tick_audit_record",
        "controlled_loop_tick_audit_appended",
        "controlled_loop_tick_audit_append_failed",
        "does_not_retry_executor",
        "does_not_rewrite_invocation_or_closeout_records",
        "loop_tick_evidence_missing",
        "task_evidence_missing",
        "execution_start_evidence_missing",
        "readiness_evidence_missing",
        "invocation_plan_evidence_missing",
        "real_invocation_evidence_missing",
        "result_evidence_missing",
        "snapshot_after_evidence_missing",
        "closeout_evidence_missing",
        "git_pr_plan_evidence_missing",
        "controlled_tick_packet_mismatch",
        "loop_tick_identity_missing",
        "loop_tick_task_mismatch",
        "loop_tick_not_ready",
        "execution_start_invalid",
        "execution_start_task_mismatch",
        "snapshot_after_invalid",
        "readiness_not_invocable",
        "readiness_task_mismatch",
        "readiness_epoch_mismatch",
        "invocation_plan_not_invocable",
        "invocation_plan_readiness_mismatch",
        "real_invocation_invalid",
        "real_invocation_identity_missing",
        "real_invocation_plan_mismatch",
        "real_invocation_record_mismatch",
        "real_invocation_result_mismatch",
        "real_invocation_closeout_mismatch",
        "result_task_mismatch",
        "closeout_invalid",
        "closeout_epoch_mismatch",
        "closeout_task_mismatch",
        "closeout_result_mismatch",
        "closeout_snapshot_mismatch",
        "closeout_invocation_mismatch",
        "closeout_validation_mismatch",
        "closeout_not_completed",
        "git_pr_plan_unanchored",
        "git_pr_plan_mismatch",
        "--side-effect-mode",
        "--max-plan-age-minutes",
        "--approval-secret-env",
        "--adapter-file",
        "--rollback-file",
        "--env-allow",
        "resume-continuation",
        "resume_continuation_command",
        "DEFAULT_RESUME_CONTINUATION_MAX_AGE_MINUTES",
        "work-ownership-status",
        "work_ownership_status_command",
        "validate-work-ownership",
        "validate_work_ownership_command",
        "complete-work-ownership-from-closeout",
        "complete_work_ownership_from_closeout_command",
        "task_checksum",
        "ownership_closeout_unreadable",
        "ownership_closeout_invalid",
        "ownership_closeout_schema_unsupported",
        "ownership_closeout_packet_invalid",
        "ownership_closeout_epoch_missing",
        "ownership_closeout_not_completed",
        "ownership_closeout_checksum_mismatch",
        "ownership_closeout_task_missing",
        "ownership_closeout_task_unreadable",
        "ownership_closeout_task_invalid",
        "ownership_closeout_task_checksum_mismatch",
        "DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES",
    ),
    "codex_cadence/git_pr_plan.py": (
        "GIT_PR_DIRTY_MATERIALIZATION_PLAN_SCHEMA_VERSION",
        "EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION",
        "_dirty_materialization_closeout_blockers",
        "evaluate_dirty_git_pr_materialization_plan",
        "git_pr_dirty_materialization_plan",
        "closeout_file_checksum",
        "closeout_invocation_mismatch",
        "real_invocation_not_materialized",
        "repository_branch_mismatch",
        "repository_head_mismatch",
        "dirty_worktree_fingerprint_missing",
        "dirty_worktree_fingerprint_mismatch",
        "materialized_change_files_mismatch",
        "real_invocation_not_closeout_approved",
        "base_head_mismatch",
        "branch_policy_base_branch_disallowed",
        "required_body_section_missing",
        "_plan_structural_blockers",
        "validate_git_pr_plan_dry_run_packet",
        "git_pr_plan_not_ready",
        "git_pr_plan_not_dry_run",
        "git_pr_plan_operator_confirmation_missing",
        "git_pr_plan_side_effects_present",
        "git_pr_plan_approval_state_invalid",
        "git_pr_plan_execution_authority_invalid",
        "git_pr_plan_proposed_branch_missing",
        "git_pr_plan_proposed_pr_title_missing",
        "git_pr_plan_proposed_pr_body_missing",
    ),
    "codex_cadence/executor_readiness.py": (
        "EXECUTOR_INVOCATION_READINESS_SCHEMA_VERSION",
        "executor-invocation-readiness.v1",
        "executor_invocation_readiness",
        "read_only",
        "executor_invocation_ready",
        "executor_started",
        "side_effects",
        "invoke_real_executor",
        "refresh_task_evidence",
        "fix_ownership",
        "close_or_fail_active_epoch",
        "inspect_policy_blockers",
        "operator_review",
        "task_file_unreadable",
        "executor_task_invalid",
        "repo_inspection_failed",
        "repo_path_invalid",
        "repo_path_mismatch",
        "repo_branch_mismatch",
        "repo_head_mismatch",
        "dirty_worktree",
        "brake_state_invalid",
        "brake_not_drive",
        "active_epoch_missing",
        "active_epoch_conflict",
        "active_epoch_invalid",
        "active_epoch_id_mismatch",
        "active_epoch_status_invalid",
        "active_epoch_repo_mismatch",
        "active_epoch_branch_mismatch",
        "active_epoch_task_missing",
        "task_checksum_missing",
        "task_checksum_mismatch",
        "ownership_record_missing",
        "ownership_candidate_mismatch",
        "ownership_epoch_mismatch",
        "ownership_head_mismatch",
        "command_policy_invalid",
        "branch_policy_invalid",
        "branch_policy_current_branch_main_disallowed",
        "required_checks_invalid",
        "required_checks_missing",
        "result_path_missing",
        "result_path_invalid",
        "result_path_mismatch",
        "result_path_outside_runtime",
        "role_readiness_unreadable",
        "role_readiness_invalid",
        "role_readiness_blocked",
        "role_readiness_scope_mismatch",
        "read_only_preflight_only",
        "executor_not_started",
        "executor_process_metadata_out_of_scope",
        "executor_code_modification_out_of_scope",
        "branch_creation_commit_push_pr_merge_release_publish_out_of_scope",
    ),
    "codex_cadence/executor_invocation.py": (
        "EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION",
        "executor-invocation-plan.v1",
        "EXECUTOR_INVOCATION_TARGET_SCHEMA_VERSION",
        "executor-invocation-target.v1",
        "EXECUTOR_ADAPTER_SCHEMA_VERSION",
        "executor-adapter.v1",
        "EXECUTOR_ROLLBACK_SCHEMA_VERSION",
        "executor-rollback.v1",
        "REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION",
        "real-executor-invocation.v1",
        "REAL_EXECUTOR_SIDE_EFFECT_MODES",
        "MAX_READINESS_AGE_SECONDS",
        "MAX_INVOCATION_PLAN_AGE_SECONDS",
        "build_executor_invocation_plan",
        "invoke_real_executor",
        "shutil.which(\"git\")",
        "_git_executable",
        "executor_invocation_target_descriptor",
        "executor_invocation_plan",
        "executor_invocation_planned",
        "real_executor_invocation",
        "executor_started",
        "side_effects",
        "plan_packet_stale",
        "plan_not_invocable",
        "approval_recheck_failed",
        "rollback_recheck_failed",
        "runtime_root_unsafe",
        "repo_inspection_failed",
        "executor_process_timeout",
        "executor_process_failed",
        "executor_result_stale",
        "executor_result_missing",
        "local_branch_refs",
        "unexpected_repo_modification",
        "materialized_change_evidence_missing",
        "evidence_only",
        "materialized_changes",
        "real_executor_process_started",
        "real_executor_invocation_record_written",
        "invocation_cwd",
        "plan_file",
        "readiness_unreadable",
        "readiness_packet_stale",
        "readiness_not_invocable",
        "_readiness_task_packet",
        "validate_executor_task_packet",
        "validate_executor_command",
        "read_active_epoch_records",
        "task_file_unreadable",
        "executor_task_invalid",
        "task_checksum_mismatch",
        "approval_missing",
        "approval_invalid",
        "approval_schema_invalid",
        "approval_target_invalid",
        "approval_target_mismatch",
        "approval_expired",
        "approval_purpose_missing",
        "approval_purpose_mismatch",
        "approval_identity_invalid",
        "approval_timestamp_invalid",
        "approval_window_too_long",
        "approval_issued_in_future",
        "approval_signature_invalid",
        "audit_chain_not_clean",
        "rollback_evidence_missing",
        "rollback_policy_invalid",
        "adapter_contract_invalid",
        "executor_command_denied",
        "executor_timeout_invalid",
        "repo_inspection_failed",
        "repo_path_mismatch",
        "repo_branch_mismatch",
        "repo_head_mismatch",
        "dirty_worktree",
        "brake_state_invalid",
        "brake_not_drive",
        "active_epoch_missing",
        "active_epoch_conflict",
        "active_epoch_invalid",
        "active_epoch_mismatch",
        "active_epoch_repo_mismatch",
        "active_epoch_branch_mismatch",
        "active_epoch_task_missing",
        "active_epoch_task_duplicate",
        "active_epoch_task_completed",
        "task_checksum_missing",
        "ownership_record_missing",
        "ownership_stale",
        "duplicate_active_ownership",
        "ownership_epoch_mismatch",
        "ownership_head_mismatch",
        "result_path_missing",
        "result_path_mismatch",
        "result_path_outside_runtime",
        "result_path_invalid",
        "read_only_invocation_planning_only",
        "process_start_out_of_scope",
    ),
    "codex_cadence/store.py": (
        "WORK_OWNERSHIP_STATES",
        "real_executor_invocation_dir",
        "real_executor_invocation_path",
        "work_ownership_state_dir",
        "work_ownership_path",
    ),
    "codex_cadence/ownership.py": (
        "WORK_OWNERSHIP_SCHEMA_VERSION",
        "work-ownership.v1",
        "WORK_OWNERSHIP_STATUS_SCHEMA_VERSION",
        "work-ownership-status.v1",
        "WORK_OWNERSHIP_VALIDATION_SCHEMA_VERSION",
        "work-ownership-validation.v1",
        "validate_work_ownership_record",
        "work_ownership_status",
        "validate_work_ownership",
        "duplicate_active_ownership",
        "repo_inspection_failed",
        "ownership_registry_state_invalid",
        "ownership_record_missing",
        "ownership_record_unreadable",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_required_field_missing",
        "ownership_field_type_invalid",
        "ownership_id_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
        "ownership_stale",
        "ownership_closed",
        "ownership_repo_mismatch",
        "ownership_branch_mismatch",
        "ownership_task_mismatch",
        "ownership_candidate_mismatch",
        "ownership_role_mismatch",
        "ownership_epoch_mismatch",
        "executor_closeout_checksum",
    ),
    "codex_cadence/handoff_loop.py": (
        "RESUME_CONTINUATION_SCHEMA_VERSION",
        "resume-continuation.v1",
        "resume_continuation",
        "resume_verification_stale",
        "resume_verification_not_resumable",
        "resume_claimer_mismatch",
        "resume_verification_anchor_mismatch",
        "start_governed_execution",
    ),
    "codex_cadence/review_response.py": (
        "REVIEW_RESPONSE_PLAN_SCHEMA_VERSION",
        "review-response-plan.v1",
        "evaluate_review_response_plan",
        "review_thread_evidence_invalid",
        "candidate_discovery_invalid",
        "refresh_pr_evidence",
        "emit_executor_task",
        "update_pr_body",
        "wait_for_checks",
        "operator_review",
        "does_not_call_github",
        "does_not_resolve_review_threads",
        "does_not_invoke_review_agents_or_paid_review",
    ),
    "codex_cadence/github_evidence.py": (
        "GITHUB_EVIDENCE_SYNC_SCHEMA_VERSION",
        "DEFAULT_GH_TIMEOUT_SECONDS",
        "REVIEW_THREAD_COMMENTS_QUERY",
        "gh pr view",
        "api",
        "graphql",
        "read_only_gh",
        "does_not_write_github",
        "does_not_create_branch",
        "does_not_commit",
        "does_not_push",
        "does_not_open_pr",
        "does_not_merge",
        "does_not_release",
        "does_not_publish_packages",
        "gh_missing",
        "gh_auth_failed",
        "gh_spawn_failed",
        "repo_slug_invalid",
        "github_rate_limited",
        "github_network_failed",
        "gh_command_timeout",
        "threadsCursor",
        "commentsCursor",
        "pr_check_failure_findings",
        "review_thread_findings_from_payload",
        "pageInfo",
        "hasNextPage",
        "gh_json_invalid",
        "gh_json_not_object",
        "no_partial_evidence_written",
        "review_thread_evidence_incomplete",
        "evidence_write_failed",
    ),
    "codex_cadence/pr_readiness.py": (
        "review_thread_evidence_invalid",
        "review_thread_findings_from_payload",
    ),
    "codex_cadence/executor_contract.py": (
        "_COMMAND_SEPARATORS",
        "_command_text_for_lexing",
        "_raw_command_substitutions",
        "_effective_command_segments",
        "_command_allowed_by_policy",
        "allowed_commands",
        "denied_commands",
        "allow_succeeded_dirty_worktree",
        "materialized_change_evidence is required when succeeded result is dirty",
    ),
    "codex_cadence/policy_audit.py": (
        "AUDIT_REPLAY_SCHEMA_VERSION",
        "replay_audit_log",
        "audit_replay_packet",
        "audit_line_invalid_json",
        "audit_file_decode_failed",
        "audit_schema_version_unsupported",
        "audit_event_unsupported",
        "audit_checksum_invalid",
        "CHECKSUM_PATTERN",
        "git_pr_materialization_intent",
        "git_pr_materialization_result",
        "validate_git_pr_materialization_intent_audit_record",
        "validate_git_pr_materialization_result_audit_record",
        "operator_approval_verification",
        "validate_operator_approval_verification_audit_record",
        "operator_approval_verification_audit_record",
        "real_executor_invocation_record",
        "validate_real_executor_invocation_audit_record",
        "real_executor_invocation_audit_record",
        "invocation_record_checksum",
        "record_real_executor_invocation",
        "record_real_executor_invocation_blocked",
        "update_real_executor_invocation_closeout",
        "controlled_loop_tick",
        "validate_controlled_loop_tick_audit_record",
        "controlled_loop_tick_audit_record",
        "complete_controlled_loop_tick",
        "audit_controlled_loop_tick_action_invalid",
        "audit_controlled_loop_tick_valid_invalid",
        "audit_controlled_loop_tick_executor_not_started",
        "audit_controlled_loop_tick_git_pr_plan_anchor_incomplete",
        "executor_closeout_checksum",
        "audit_work_ownership_closeout_anchor_incomplete",
        "audit_work_ownership_closeout_action_invalid",
        "audit_work_ownership_closeout_status_invalid",
        "audit_real_executor_closeout_invalid",
        "audit_real_executor_closeout_action_mismatch",
        "audit_operator_approval_signature_unverified",
        "audit_operator_approval_purpose_invalid",
        "audit_operator_approval_operator_invalid",
        "audit_operator_approval_key_id_invalid",
        "audit_operator_approval_timestamp_invalid",
        "audit_operator_approval_window_too_long",
        "execution_start_decision",
        "validate_execution_start_audit_record",
        "execution_start_audit_record",
        "allowed_commands",
        "denied_commands",
        "effective_allowed_commands",
        "effective_denied_commands",
    ),
    "codex_cadence/approvals.py": (
        "OPERATOR_APPROVAL_SCHEMA_VERSION",
        "OPERATOR_APPROVAL_VERIFICATION_SCHEMA_VERSION",
        "OPERATOR_APPROVAL_PURPOSES",
        "MAX_OPERATOR_APPROVAL_WINDOW",
        "operator_approval_signature",
        "build_operator_approval_verification_packet",
        "operator_approval_invalid",
        "operator_approval_schema_invalid",
        "operator_approval_target_invalid",
        "operator_approval_purpose_missing",
        "operator_approval_operator_missing",
        "operator_approval_key_id_weak",
        "operator_approval_timestamp_invalid",
        "operator_approval_window_too_long",
        "operator_approval_expired",
        "operator_approval_issued_in_future",
        "operator_approval_purpose_mismatch",
        "operator_approval_target_mismatch",
        "operator_approval_secret_missing",
        "operator_approval_signature_invalid",
    ),
    ".github/workflows/codex-review.yml": (
        "pull_request_target",
        "branches: [main]",
        "types: [labeled, synchronize]",
        "labeled",
        "github.event.action == 'labeled'",
        "github.event.action == 'synchronize'",
        "github.event.label.name == 'codex-review-elect'",
        "github.event.label.name == 'elect-codex-review'",
        "github.event.label.name == 'codex-review-force'",
        "github.event.label.name == 'force-codex-review'",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "github.event.pull_request.head.repo.full_name != github.repository",
        "Codex Review skipped for fork PRs",
        "untrusted PR code",
        "github.event.pull_request.draft == false",
        "concurrency:",
        "group: codex-review-${{ github.event.pull_request.number }}",
        "cancel-in-progress: >-",
        "codex_review_preflight.py",
        "needs: preflight",
        "needs.preflight.outputs.should_run == 'true'",
        "preflight_notice",
        "post_feedback",
        "fork_notice",
        "timeout-minutes: 2",
        "timeout-minutes: 5",
        "timeout-minutes: 20",
        "Check live PR state",
        "Re-check live PR state before paid review",
        "steps.live_pr.outputs.can_review == 'true'",
        "steps.review_pr.outputs.can_review == 'true'",
        "pull.draft === false",
        "pull.head.sha === context.payload.pull_request.head.sha",
        "pr_draft",
        "stale_head",
        "ref: ${{ github.event.pull_request.base.sha }}",
        "ref: ${{ github.event.pull_request.head.sha }}",
        "path: trusted-preflight",
        "path: pr-head",
        "working-directory: pr-head",
        "python ../trusted-preflight/scripts/codex_review_preflight.py",
        "--head-ref \"${HEAD_SHA}\"",
        "issues: read",
        "pull-requests: read",
        "BASE_REF: ${{ github.event.pull_request.base.ref }}",
        "HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
        '"+refs/heads/${BASE_REF}:refs/remotes/origin/${BASE_REF}"',
        "openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c",
        "openai-api-key: ${{ secrets.OPENAI_API_KEY }}",
        "prompt: |",
        "Do not modify files",
        "actionable findings",
        "protocol drift",
        "race conditions",
        "missing tests",
        "safety-strategy: drop-sudo",
        "sandbox: read-only",
        "final-message",
        "codex-review:v1 head=",
        "CODEX_REVIEW_DEDUPE_KEY",
        "PR_LABELS_JSON",
        "codex-review-elect",
    ),
    ".github/workflows/pr.yml": (
        "pull_request:",
        "branches: [main]",
        "types: [opened, synchronize, reopened, ready_for_review]",
        "group: pr-checks-${{ github.event.pull_request.number }}",
        "cancel-in-progress: true",
        "timeout-minutes: 20",
        "timeout-minutes: 15",
        "Classify changed paths",
        "persist-credentials: false",
        "code_required",
        "package_required",
        "Skip expensive code checks for docs-only changes",
        "Skip package checks for docs-only changes",
        "python scripts/validate_protocol.py",
        "python -m unittest discover -s tests -v",
        "ubuntu-latest",
        "windows-latest",
    ),
    ".github/workflows/release-dry-run.yml": (
        "workflow_dispatch",
        "version:",
        "tag:",
        "target_ref:",
        "permissions:",
        "contents: read",
        "ref: main",
        "fetch-depth: 0",
        "fetch-tags: true",
        "persist-credentials: false",
        "python scripts/cadence.py release-dry-run",
        "--version \"$RELEASE_VERSION\"",
        "--tag \"$RELEASE_TAG\"",
        "--target-ref \"$TARGET_REF\"",
        "python scripts/prepare_release_dry_run_artifacts.py",
        "python scripts/enforce_release_dry_run_result.py",
        "release-dry-run.json",
        "release-notes.md",
        "group: release-dry-run-${{ inputs.tag }}",
        "cancel-in-progress: true",
        "timeout-minutes: 10",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "ready_to_release",
        "operator_confirmation_required",
    ),
    "scripts/prepare_release_dry_run_artifacts.py": (
        "def escape_command",
        "%25",
        "%0A",
        "%0D",
        "::warning title=",
        "::error title=",
        "GITHUB_OUTPUT",
        "release-notes.md",
    ),
    "scripts/enforce_release_dry_run_result.py": (
        "READY_TO_RELEASE",
        "OPERATOR_CONFIRMATION_REQUIRED",
        "No tags, GitHub releases, or package publications are created by this workflow.",
    ),
}

FORBIDDEN_TOKENS = {
    ".github/workflows/codex-review.yml": (
        "refs/pull/${{ github.event.pull_request.number }}/merge",
        "refs/remotes/pull/${PR_NUMBER}/head",
    ),
    "docs/session-handoff.md": (
        "Draft PR #58",
        "Continue PR #58",
        "codex/policy-stop-controls",
        "design and implement the first dry-run-only Git/PR planning slice",
        "contract and implementation without live",
        "Implement the dry-run-only `git-pr-plan` slice test-first",
        "codex/controlled-executor-fixture",
        "This branch starts only a controlled fake executor fixture",
    ),
    "docs/roadmap.md": (
        "Cadence does not fetch, synchronize, or",
        "live GitHub fetching and reconciliation remain future work",
        "Task 21, controlled real executor invocation runner, is implemented in the current tree",
        "Cadence does not yet hand work to a real executor",
        "No real executor applies code changes yet",
        "or invoke a real executor.",
        "still no real executor start authority consuming those gates",
        "Authenticated approval identity, exact real-executor invocation planning, controlled real executor invocation",
    ),
    "docs/autonomous-loop-readiness.md": (
        "Requires real executor integration",
        "do not govern a real executor or",
        "provide tamper evidence",
        "The real implementation executor",
    ),
}

FORBIDDEN_RELEASE_ACTIONS = (
    "actions/create-release",
    "marvinpinto/action-automatic-releases",
    "ncipollo/release-action",
    "pypa/gh-action-pypi-publish",
    "softprops/action-gh-release",
    "svenstaro/upload-release-action",
)

SHELL_COMMAND_PREFIX = r"^(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*(?:sudo\s+)?"
FORBIDDEN_RELEASE_COMMAND_PATTERNS = {
    "gh release create": re.compile(SHELL_COMMAND_PREFIX + r"gh\s+release\s+create\b"),
    "git tag": re.compile(SHELL_COMMAND_PREFIX + r"git\s+tag\b"),
    "git push": re.compile(SHELL_COMMAND_PREFIX + r"git\s+push\b"),
    "git merge": re.compile(SHELL_COMMAND_PREFIX + r"git\s+merge\b"),
    "twine upload": re.compile(SHELL_COMMAND_PREFIX + r"(?:python(?:3)?\s+-m\s+)?twine\s+upload\b"),
}


def validate_frontmatter(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(f"{path.relative_to(ROOT)} must start with YAML frontmatter")
        return
    try:
        _start, frontmatter, _body = text.split("---\n", 2)
    except ValueError:
        errors.append(f"{path.relative_to(ROOT)} frontmatter is not closed")
        return
    for token in ("name:", "description:"):
        if token not in frontmatter:
            errors.append(f"{path.relative_to(ROOT)} frontmatter missing {token}")


def validate_tokens(errors: list[str]) -> None:
    for relative, tokens in REQUIRED_TOKENS.items():
        path = ROOT / relative
        if not path.exists():
            errors.append(f"missing required file: {relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                errors.append(f"{relative} missing required token: {token}")
    for relative, tokens in FORBIDDEN_TOKENS.items():
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token in text:
                errors.append(f"{relative} must not contain forbidden token: {token}")


def indented_block_after(text: str, header: str) -> str:
    """Return the indented YAML-like block immediately following a header line."""
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line == header)
    except StopIteration:
        return ""

    header_indent = len(header) - len(header.lstrip(" "))
    block = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= header_indent:
            break
        block.append(line)
    return "\n".join(block)


def mapping_at_indent(text: str, indent: int) -> dict[str, str]:
    """Collect simple key/value mappings found at a specific indentation level."""
    mapping = {}
    prefix = " " * indent
    for line in text.splitlines():
        if not line.startswith(prefix) or line.startswith(prefix + " "):
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        mapping[key] = value.strip()
    return mapping


def key_values_at_indent(text: str, indent: int, key: str) -> tuple[str, ...]:
    """Return values for exact YAML-like key declarations at one indentation level."""
    values = []
    prefix = " " * indent
    child_prefix = " " * (indent + 1)
    for line in text.splitlines():
        if not line.startswith(prefix) or line.startswith(child_prefix):
            continue
        stripped = line.strip()
        if not stripped.startswith(f"{key}:"):
            continue
        values.append(stripped.split(":", 1)[1].strip())
    return tuple(values)


def key_names_at_indent(text: str, indent: int) -> tuple[str, ...]:
    """Return YAML-like key names found at one indentation level, preserving duplicates."""
    names = []
    prefix = " " * indent
    child_prefix = " " * (indent + 1)
    for line in text.splitlines():
        if not line.startswith(prefix) or line.startswith(child_prefix):
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        names.append(stripped.split(":", 1)[0])
    return tuple(names)


def workflow_uses_values(text: str) -> tuple[str, ...]:
    """Return external action references from workflow uses entries."""
    values = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.match(r"^(?:-\s*)?uses:\s*(?P<value>\S+)\s*$", stripped)
        if match is not None:
            values.append(match.group("value").strip("'\""))
    return tuple(values)


def workflow_run_blocks(text: str) -> tuple[str, ...]:
    """Return inline and block scalar run commands from a workflow document."""
    lines = text.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        match = re.match(r"^(?:-\s*)?run:\s*(?P<value>.*)$", stripped)
        if match is None:
            index += 1
            continue

        indent = len(line) - len(line.lstrip(" "))
        value = match.group("value").strip()
        if value and value not in ("|", ">"):
            blocks.append(value)
            index += 1
            continue

        block = []
        index += 1
        while index < len(lines):
            child = lines[index]
            child_indent = len(child) - len(child.lstrip(" "))
            if child.strip() and child_indent <= indent:
                break
            block.append(child[indent + 2 :] if len(child) > indent + 2 else "")
            index += 1
        blocks.append("\n".join(block))
    return tuple(blocks)


def workflow_non_run_lines(text: str) -> tuple[str, ...]:
    """Return workflow lines while omitting multiline run command bodies."""
    lines = text.splitlines()
    visible = []
    index = 0
    while index < len(lines):
        line = lines[index]
        visible.append(line)
        stripped = line.strip()
        match = re.match(r"^(?:-\s*)?run:\s*(?P<value>.*)$", stripped)
        if match is None or match.group("value").strip() not in ("|", ">"):
            index += 1
            continue

        indent = len(line) - len(line.lstrip(" "))
        index += 1
        while index < len(lines):
            child = lines[index]
            child_indent = len(child) - len(child.lstrip(" "))
            if child.strip() and child_indent <= indent:
                break
            index += 1
    return tuple(visible)


def workflow_has_job_permissions(text: str) -> bool:
    """Report whether the workflow declares job-level permissions."""
    in_jobs = False
    for line in workflow_non_run_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_jobs = stripped.split(":", 1)[0] == "jobs"
            continue
        if in_jobs and indent >= 4 and re.match(r"permissions\s*:", stripped):
            return True
    return False


def shell_command_segments(line: str) -> tuple[str, ...]:
    """Split shell text on common command separators for mutation scanning."""
    return tuple(
        segment.strip()
        for segment in re.split(r"(?:&&|\|\||(?<!\|)\|(?!\|)|;|\bthen\b|\bdo\b)", line)
        if segment.strip()
    )


def validate_release_workflow_mutations(relative: str, text: str, errors: list[str]) -> None:
    """Reject release/publish actions and mutating release commands in workflow steps."""
    for value in workflow_uses_values(text):
        action_ref = value.lower().split("@", 1)[0]
        for action in FORBIDDEN_RELEASE_ACTIONS:
            if action_ref == action:
                errors.append(f"{relative} must not use release or publishing action: {action}")

    for block in workflow_run_blocks(text):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for segment in shell_command_segments(stripped):
                for label, pattern in FORBIDDEN_RELEASE_COMMAND_PATTERNS.items():
                    if pattern.search(segment):
                        errors.append(f"{relative} must not run forbidden release command: {label}")


def validate_codex_review_workflow(errors: list[str]) -> None:
    """Validate that paid Codex Review stays explicitly elected and cancel-bounded."""
    relative = ".github/workflows/codex-review.yml"
    path = ROOT / relative
    if not path.exists():
        errors.append(f"missing required file: {relative}")
        return

    text = path.read_text(encoding="utf-8")
    visible_text = "\n".join(workflow_non_run_lines(text))

    on_block = indented_block_after(visible_text, "on:")
    event_keys = key_names_at_indent(on_block, 2)
    if event_keys != ("pull_request_target",):
        errors.append(f"{relative} must declare only pull_request_target")

    trigger_block = indented_block_after(visible_text, "  pull_request_target:")
    if key_values_at_indent(trigger_block, 4, "branches") != ("[main]",):
        errors.append(f"{relative} pull_request_target branches must be exactly [main]")
    if key_values_at_indent(trigger_block, 4, "types") != ("[labeled, synchronize]",):
        errors.append(f"{relative} pull_request_target types must be exactly [labeled, synchronize]")

    concurrency_block = indented_block_after(visible_text, "concurrency:")
    if "group: codex-review-${{ github.event.pull_request.number }}" not in concurrency_block:
        errors.append(f"{relative} concurrency group must stay PR-scoped")
    if "cancel-in-progress: true" in concurrency_block:
        errors.append(f"{relative} concurrency cancellation must be conditional")
    for token in (
        "cancel-in-progress: >-",
        "github.event.action == 'synchronize'",
        "github.event.label.name == 'codex-review-elect'",
        "github.event.label.name == 'elect-codex-review'",
        "github.event.label.name == 'codex-review-force'",
        "github.event.label.name == 'force-codex-review'",
    ):
        if token not in concurrency_block:
            errors.append(f"{relative} concurrency cancellation missing: {token}")

    preflight_block = indented_block_after(visible_text, "  preflight:")
    preflight_condition = preflight_block.split("    runs-on:", 1)[0]
    if "github.event.action == 'labeled'" not in preflight_condition:
        errors.append(f"{relative} preflight must run paid-review checks only on labeled events")
    if "github.event.action == 'synchronize'" in preflight_condition:
        errors.append(f"{relative} synchronize must be cancel-only and not run paid-review preflight")
    for label in (
        "github.event.label.name == 'codex-review-elect'",
        "github.event.label.name == 'elect-codex-review'",
        "github.event.label.name == 'codex-review-force'",
        "github.event.label.name == 'force-codex-review'",
    ):
        if label not in preflight_condition:
            errors.append(f"{relative} preflight missing elected label gate: {label}")

    fork_notice_block = indented_block_after(visible_text, "  fork_notice:")
    fork_notice_condition = fork_notice_block.split("    runs-on:", 1)[0]
    if "github.event.action == 'labeled'" not in fork_notice_condition:
        errors.append(f"{relative} fork notice must run only on labeled elected events")

    for job_name, timeout_minutes in (
        ("preflight", "5"),
        ("preflight_notice", "2"),
        ("codex", "20"),
        ("post_feedback", "5"),
        ("fork_notice", "2"),
    ):
        job_block = indented_block_after(visible_text, f"  {job_name}:")
        if f"timeout-minutes: {timeout_minutes}" not in job_block:
            errors.append(f"{relative} {job_name} must have timeout-minutes: {timeout_minutes}")

    try:
        preflight_index = text.index("python ../trusted-preflight/scripts/codex_review_preflight.py")
        openai_key_index = text.index("openai-api-key: ${{ secrets.OPENAI_API_KEY }}")
    except ValueError:
        errors.append(f"{relative} missing trusted preflight or OpenAI key wiring")
    else:
        if preflight_index > openai_key_index:
            errors.append(f"{relative} trusted preflight must run before OpenAI API key use")


def validate_release_dry_run_workflow(errors: list[str]) -> None:
    """Validate that the release dry-run workflow remains manual and read-only."""
    relative = ".github/workflows/release-dry-run.yml"
    path = ROOT / relative
    if not path.exists():
        errors.append(f"missing required file: {relative}")
        return

    text = path.read_text(encoding="utf-8")
    if "workflow_dispatch:" not in text:
        errors.append(f"{relative} must use workflow_dispatch")

    on_block = indented_block_after(text, "on:")
    if set(mapping_at_indent(on_block, 2)) != {"workflow_dispatch"}:
        errors.append(f"{relative} must declare only workflow_dispatch")
    validate_release_workflow_mutations(relative, text, errors)

    permissions_block = indented_block_after(text, "permissions:")
    if mapping_at_indent(permissions_block, 2) != {"contents": "read"}:
        errors.append(f"{relative} workflow permissions must be exactly contents: read")
    if workflow_has_job_permissions(text):
        errors.append(f"{relative} must not define job-level permissions")

    concurrency_block = indented_block_after(text, "concurrency:")
    if mapping_at_indent(concurrency_block, 2) != {
        "group": "release-dry-run-${{ inputs.tag }}",
        "cancel-in-progress": "true",
    }:
        errors.append(f"{relative} concurrency must cancel by release tag")

    dry_run_job_block = indented_block_after(text, "  dry-run:")
    if "timeout-minutes: 10" not in dry_run_job_block:
        errors.append(f"{relative} dry-run job must have timeout-minutes: 10")

    for input_name in ("version", "tag"):
        block = indented_block_after(text, f"      {input_name}:")
        if not block:
            errors.append(f"{relative} missing {input_name} input")
        elif "required: true" not in block:
            errors.append(f"{relative} {input_name} input must be required")

    target_ref_block = indented_block_after(text, "      target_ref:")
    if not target_ref_block:
        errors.append(f"{relative} missing target_ref input")
    elif "required: false" not in target_ref_block:
        errors.append(f"{relative} target_ref input must be optional")

    try:
        upload_index = text.index("      - name: Upload release dry-run artifacts")
        enforce_index = text.index("      - name: Enforce release dry-run result")
    except ValueError:
        errors.append(f"{relative} missing artifact upload or enforcement step")
    else:
        if upload_index > enforce_index:
            errors.append(f"{relative} must upload artifacts before enforcing failure")

    dry_run_block = indented_block_after(text, "      - name: Run release dry run")
    if not dry_run_block:
        errors.append(f"{relative} missing release dry-run step")
    else:
        if "tee release-dry-run.json" in dry_run_block:
            errors.append(f"{relative} must write dry-run output outside the checkout before artifact copy")
        if 'tee "$RUNNER_TEMP/release-dry-run.json"' not in dry_run_block:
            errors.append(f"{relative} must tee release dry-run output into RUNNER_TEMP")
        if 'cp "$RUNNER_TEMP/release-dry-run.json" release-dry-run.json' not in dry_run_block:
            errors.append(f"{relative} must copy the dry-run artifact into the checkout after evaluation")

    enforce_block = indented_block_after(text, "      - name: Enforce release dry-run result")
    if not enforce_block:
        errors.append(f"{relative} missing enforcement step")
    else:
        if "READY_TO_RELEASE" not in enforce_block:
            errors.append(f"{relative} must fail when ready_to_release is false")
        if "OPERATOR_CONFIRMATION_REQUIRED" not in enforce_block:
            errors.append(f"{relative} must fail when operator_confirmation_required is false")
        if "scripts/enforce_release_dry_run_result.py" not in enforce_block:
            errors.append(f"{relative} must run the release dry-run enforcement script")


def tuple_assignment(relative: str, name: str, errors: list[str]) -> tuple[str, ...] | None:
    path = ROOT / relative
    if not path.exists():
        errors.append(f"missing required file: {relative}")
        return None
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
            errors.append(f"{relative} {name} must be a tuple of strings")
            return None
        return value
    errors.append(f"{relative} missing {name}")
    return None


def taxonomy_sentence_tokens(text: str) -> tuple[str, ...]:
    match = re.search(r"(?:taxonomy values|these taxonomy values)[^.]*\.", text, flags=re.IGNORECASE)
    if match is None:
        return ()
    return tuple(re.findall(r"`([^`]+)`", match.group(0)))


def status_sentence_tokens(text: str) -> tuple[str, ...]:
    match = re.search(
        r"(?:optional\s+(?:business-memory\s+)?`?status`?\s+values\s+are)[^.]*\.",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return ()
    return tuple(token for token in re.findall(r"`([^`]+)`", match.group(0)) if token.lower() != "status")


def normalized_words(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def business_memory_contract_text(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", text)
    relevant = [
        paragraph
        for paragraph in paragraphs
        if any(token in paragraph.lower() for token in ("business-memory", "business memory", "business_memory"))
    ]
    return normalized_words("\n".join(relevant))


def validate_business_memory_contract(errors: list[str]) -> None:
    implementation_taxonomy = tuple_assignment(
        "codex_cadence/candidates.py",
        "BUSINESS_MEMORY_CLASSIFICATIONS",
        errors,
    )
    if implementation_taxonomy != EXPECTED_BUSINESS_MEMORY_TAXONOMY:
        errors.append(
            "codex_cadence/candidates.py BUSINESS_MEMORY_CLASSIFICATIONS must exactly match "
            f"{EXPECTED_BUSINESS_MEMORY_TAXONOMY}"
        )
    implementation_statuses = tuple_assignment(
        "codex_cadence/candidates.py",
        "BUSINESS_MEMORY_STATUSES",
        errors,
    )
    if implementation_statuses != EXPECTED_BUSINESS_MEMORY_STATUSES:
        errors.append(
            "codex_cadence/candidates.py BUSINESS_MEMORY_STATUSES must exactly match "
            f"{EXPECTED_BUSINESS_MEMORY_STATUSES}"
        )

    for relative in ("SKILL.md", "docs/protocol.md"):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        taxonomy = taxonomy_sentence_tokens(text)
        if taxonomy != EXPECTED_BUSINESS_MEMORY_TAXONOMY:
            errors.append(
                f"{relative} business-memory taxonomy must exactly match "
                f"{EXPECTED_BUSINESS_MEMORY_TAXONOMY}; got {taxonomy}"
            )

    for relative in ("SKILL.md", "docs/protocol.md", "docs/cadence/business-memory.md"):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        statuses = status_sentence_tokens(text)
        if statuses != EXPECTED_BUSINESS_MEMORY_STATUSES:
            errors.append(
                f"{relative} business-memory status values must exactly match "
                f"{EXPECTED_BUSINESS_MEMORY_STATUSES}; got {statuses}"
            )

    for relative in ("SKILL.md", "docs/protocol.md"):
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        normalized = business_memory_contract_text(text)
        for token in (
            "docs/cadence/business-memory.md",
            "source: business_memory",
            "maturity: discovery",
            "classification_confidence",
            "status",
            "active",
            "fulfilled",
            "superseded",
            "classification: unknown",
            "unclassified_signal",
            "repo_anchors: []",
            "evidence.path",
            "evidence.line",
            "evidence.heading",
            "discovery-only",
            "must not directly",
            "execution",
            "modify files",
            "commit",
            "push",
            "merge",
            "task sizing",
            "snapshots",
            "cadence state",
            "self-check",
            "governance policy",
            "--max-business-memory-candidates",
        ):
            if token not in normalized:
                errors.append(f"{relative} business-memory contract missing: {token}")

    cadence_cli = (ROOT / "codex_cadence" / "cli.py").read_text(encoding="utf-8")
    for token in (
        'discover_parser.add_argument("--max-business-memory-candidates"',
        "max_business_memory_candidates=args.max_business_memory_candidates",
    ):
        if token not in cadence_cli:
            errors.append(f"codex_cadence/cli.py business-memory CLI wiring missing: {token}")


def main() -> int:
    errors: list[str] = []
    validate_frontmatter(ROOT / "SKILL.md", errors)
    validate_tokens(errors)
    validate_codex_review_workflow(errors)
    validate_release_dry_run_workflow(errors)
    validate_business_memory_contract(errors)
    if errors:
        for error in errors:
            print(f"protocol validation error: {error}", file=sys.stderr)
        return 1
    print("Protocol validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
