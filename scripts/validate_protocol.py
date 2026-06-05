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
        "unreleased read-only audit replay, command-policy enforcement, and active-stop result-validation controls",
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
        "git-pr-materialize",
        "git-pr-materialization.v1",
        "git_pr_materialization_intent",
        "read-only `verify-resume`",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "executor_started: false",
        "read-only `review-response-plan`",
        "review-response-plan.v1",
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
        "release-dry-run",
        ".github/workflows/release-dry-run.yml",
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
        "audit_line_invalid_json",
        "audit_schema_version_unsupported",
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
        "repo_inspection_failed",
        "brake_state_invalid",
        "audit_append_failed",
        "epoch_rollback_failed",
        "inspect_runtime_state",
        "review-response-plan",
        "review-response-plan.v1",
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
        "audit replay evidence, command-policy",
        "active-stop result-validation controls",
        "local branch policy for dry-run Git/PR planning",
        "read-only GitHub evidence sync",
        "operator-approved Git/PR materialization",
        "governed execution-start epoch gating",
        "start-governed-execution",
        "execution-start.v1",
        "git-pr-materialize",
        "github-evidence-sync",
        "read-only review-response planning",
        "review-response-plan",
        "continuous GitHub reconciliation and write-side PR/review actions",
        "still no hash chain or",
    ),
    "docs/autonomous-loop-readiness.md": (
        "Local policy/audit controls",
        "task-carried branch policy",
        "Partial, read-only evidence capture",
        "no hash chain or authenticated approval identity",
        "dry-run `git-pr-plan`",
        "git-pr-materialize",
        "operator-approved only",
        "start-governed-execution",
        "execution-start.v1",
        "executor_started: false",
        "review-response-plan.v1",
    ),
    "docs/implementation-slices.md": (
        "Active execution controls are partial",
        "branch policy is carried into emitted executor task packets",
        "dry-run Git/PR transition plan",
        "blocked no materialized changes tied to result evidence",
        "github-evidence-sync",
        "pr_check_failure",
        "git-pr-materialize",
        "target-bound HMAC operator approval",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "executor_started: false",
        "review-response-plan",
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
        "review-response-plan.v1",
    ),
    "docs/session-handoff.md": (
        "shell grouping",
        "command substitutions",
        "shell-wrapper payloads",
        "provide_runtime_root",
        "The controlled fake executor fixture is merged",
        "Task 10 from `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`",
        "Task 11 from `docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md`",
        "branch_policy",
        "github-evidence-sync",
        "git-pr-materialize",
        "git_pr_materialization_intent",
        "git_pr_materialization_result",
        "start-governed-execution",
        "execution-start.v1",
        "execution_start_decision",
        "executor_started: false",
        "--pr-json-file",
        "run-controlled-executor-fixture",
        "executor_fixture_invocation",
        "reject stale result files",
        "review-response-plan.v1",
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
        "git-pr-materialize",
        "git_pr_materialize_command",
        "materialize_git_pr_plan",
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
        "execution_start_decision",
        "validate_execution_start_audit_record",
        "execution_start_audit_record",
        "allowed_commands",
        "denied_commands",
        "effective_allowed_commands",
        "effective_denied_commands",
    ),
    ".github/workflows/codex-review.yml": (
        "pull_request_target",
        "branches: [main]",
        "types: [labeled]",
        "labeled",
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
        "cancel-in-progress: true",
        "codex_review_preflight.py",
        "needs: preflight",
        "needs.preflight.outputs.should_run == 'true'",
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
        "timeout-minutes: 15",
        "Classify changed paths",
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
