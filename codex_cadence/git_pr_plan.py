"""Dry-run Git and pull request transition planning."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import shutil
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.branch_policy import normalize_branch_policy
from codex_cadence.epochs import EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION
from codex_cadence.executor_contract import validate_executor_result_evidence, validate_executor_task_packet
from codex_cadence.executor_invocation import (
    DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
    _dirty_worktree_fingerprint,
    _local_dirty_files,
)
from codex_cadence.policy_audit import (
    append_audit_record,
    checksum_json,
    git_pr_dirty_commit_materialization_intent_audit_record,
    git_pr_dirty_commit_materialization_result_audit_record,
    git_pr_materialization_intent_audit_record,
    git_pr_materialization_result_audit_record,
)
from codex_cadence.pr_readiness import _pr_readiness_evidence, evaluate_pr_body_preflight
from codex_cadence.store import BRAKE_STATUSES, read_json, utc_now

GIT_PR_PLAN_SCHEMA_VERSION = "git-pr-plan.v1"
GIT_PR_MATERIALIZATION_SCHEMA_VERSION = "git-pr-materialization.v1"
GIT_PR_DIRTY_MATERIALIZATION_PLAN_SCHEMA_VERSION = "git-pr-dirty-materialization-plan.v1"
GIT_PR_DIRTY_COMMIT_MATERIALIZATION_SCHEMA_VERSION = "git-pr-dirty-commit-materialization.v1"
GIT_PR_MATERIALIZATION_APPROVAL_PREFIX = "approve-git-pr:"
GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV = "CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET"
SHA256_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured blocker or warning payload."""
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def _non_empty_string(value: Any) -> bool:
    """Return True for strings that contain non-whitespace text."""
    return isinstance(value, str) and bool(value.strip())


def _valid_sha256_checksum(value: Any) -> bool:
    return isinstance(value, str) and SHA256_CHECKSUM_PATTERN.fullmatch(value) is not None


def _run_git(cwd: Path, args: list[str], *, optional_locks: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a Git command in cwd, optionally disabling optional locks."""
    command = ["git", *args] if optional_locks else ["git", "--no-optional-locks", *args]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def _run_process(cwd: Path, argv: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a materialization command and preserve OSError as a failed process."""
    executable = shutil.which(argv[0])
    command = [executable or argv[0], *argv[1:]]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, input=input_text)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def git_pr_materialization_approval_payload(
    plan_packet: Any,
    *,
    remote: str,
    remote_url: str | None,
    pr_number: str | None,
) -> dict[str, Any]:
    """Build the operator-approved target bound to a materialization token."""
    return {
        "schema_version": "git-pr-materialization-approval.v1",
        "packet": "git_pr_materialization_approval",
        "plan_checksum": checksum_json(plan_packet),
        "remote": remote,
        "remote_url": remote_url,
        "pr_number": str(pr_number) if pr_number is not None else None,
        "operation": "update_pull_request" if pr_number is not None else "create_pull_request",
    }


def _materialization_approval_secret(approval_secret: str | bytes | None = None) -> bytes | None:
    secret = approval_secret if approval_secret is not None else os.environ.get(GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV)
    if isinstance(secret, bytes):
        return secret if secret else None
    if isinstance(secret, str) and secret:
        return secret.encode("utf-8")
    return None


def git_pr_materialization_approval_token(
    plan_packet: Any,
    *,
    remote: str = "origin",
    remote_url: str | None = None,
    pr_number: str | None = None,
    approval_secret: str | bytes | None = None,
) -> str:
    """Return the operator-held HMAC approval token for a materialization target."""
    secret = _materialization_approval_secret(approval_secret)
    if secret is None:
        raise ValueError(f"{GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV} is required for approval tokens")
    approval_payload = git_pr_materialization_approval_payload(
        plan_packet,
        remote=remote,
        remote_url=remote_url,
        pr_number=pr_number,
    )
    payload = json.dumps(approval_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return GIT_PR_MATERIALIZATION_APPROVAL_PREFIX + "hmac-sha256:" + digest


def git_pr_dirty_commit_materialization_approval_payload(plan_packet: Any) -> dict[str, Any]:
    """Build the operator-approved target for local dirty commit materialization."""
    proposed_commit = plan_packet.get("proposed_commit") if isinstance(plan_packet, dict) else {}
    if not isinstance(proposed_commit, dict):
        proposed_commit = {}
    return {
        "schema_version": "git-pr-dirty-commit-materialization-approval.v1",
        "packet": "git_pr_dirty_commit_materialization_approval",
        "plan_checksum": checksum_json(plan_packet),
        "target_checksum": plan_packet.get("target_checksum") if isinstance(plan_packet, dict) else None,
        "proposed_branch": plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None,
        "source_head": proposed_commit.get("source_head"),
        "operation": "dirty_commit_materialization",
    }


def git_pr_dirty_commit_materialization_approval_token(
    plan_packet: Any,
    *,
    approval_secret: str | bytes | None = None,
) -> str:
    """Return the operator-held HMAC approval token for a local dirty commit target."""
    secret = _materialization_approval_secret(approval_secret)
    if secret is None:
        raise ValueError(f"{GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV} is required for approval tokens")
    approval_payload = git_pr_dirty_commit_materialization_approval_payload(plan_packet)
    payload = json.dumps(approval_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return GIT_PR_MATERIALIZATION_APPROVAL_PREFIX + "hmac-sha256:" + digest


def _git_stdout(cwd: Path, args: list[str]) -> tuple[str | None, str | None]:
    """Return stripped stdout for a Git command or a compact error."""
    result = _run_git(cwd, args)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout).strip()
    return result.stdout.strip(), None


def _valid_branch_name(cwd: Path, branch_name: str) -> bool:
    """Check whether branch_name can be used as a local branch ref."""
    if not branch_name.strip():
        return False
    result = _run_git(cwd, ["check-ref-format", f"refs/heads/{branch_name}"])
    return result.returncode == 0


def _resolve_branch(cwd: Path, branch_name: str) -> tuple[str | None, str | None]:
    """Resolve a local branch name to a commit hash."""
    return _git_stdout(cwd, ["rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}"])


def _branch_exists(cwd: Path, branch_name: str) -> tuple[bool, str | None]:
    """Return whether a local branch ref exists, preserving lookup errors."""
    result = _run_git(cwd, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"])
    if result.returncode == 0:
        return True, None
    if result.returncode == 1:
        return False, None
    return False, (result.stderr or result.stdout).strip()


def _slugify(value: str) -> str:
    """Normalize arbitrary text into a bounded branch-name slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "task")[:48].strip("-") or "task"


def _generated_branch_name(branch_prefix: str, task_id: Any) -> str:
    """Build the proposed branch name from prefix and task id."""
    prefix = branch_prefix.strip().strip("/")
    slug = _slugify(str(task_id or "task"))
    return f"{prefix}/{slug}" if prefix else slug


def _read_brake_without_writes(runtime_root: Path | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read brake.json without initializing or mutating runtime state."""
    if runtime_root is None:
        return None, _issue(
            "runtime_root_required",
            "runtime root is required to validate brake_not_drive stop condition",
        )
    brake_file = runtime_root / "brake.json"
    try:
        brake = json.loads(brake_file.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, _issue(
            "runtime_brake_missing",
            "runtime root does not contain brake.json; initialize or provide the active runtime root",
            runtime_root=str(runtime_root),
        )
    except (OSError, json.JSONDecodeError) as exc:
        return None, _issue("runtime_brake_invalid", f"could not read runtime brake state: {exc}")
    if not isinstance(brake, dict) or brake.get("status") not in BRAKE_STATUSES:
        return None, _issue("runtime_brake_invalid", "runtime brake state is missing a valid status")
    return brake, None


def _cadence_state(brake: dict[str, Any]) -> dict[str, Any]:
    """Translate brake state into the public cadence state shape."""
    state_by_brake = {
        "DRIVE": "PLAY_ON",
        "NEUTRAL": "HUDDLE",
        "PARK": "TIMEOUT",
    }
    status = brake["status"]
    return {
        "state": state_by_brake[status],
        "legacy_brake": status,
        "can_start_work": status == "DRIVE",
        "requires_operator_resume": status == "PARK",
    }


def _inspect_git_state(
    cwd: Path,
    *,
    base_branch: str,
    proposed_branch: str,
    allow_dirty_worktree: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Collect Git state needed for PR planning and any blockers."""
    blockers: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "repository_path": str(cwd),
        "current_branch": None,
        "current_head": None,
        "base_branch": base_branch,
        "base_head": None,
        "worktree_clean": False,
        "dirty_paths": [],
    }

    inside, error = _git_stdout(cwd, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        blockers.append(_issue("git_repo_missing", error or "cwd is not inside a Git worktree"))
        return summary, blockers

    repo_root, error = _git_stdout(cwd, ["rev-parse", "--show-toplevel"])
    if repo_root is None:
        blockers.append(_issue("git_repo_missing", error or "could not resolve Git worktree root"))
        return summary, blockers
    summary["repository_path"] = str(Path(repo_root).resolve())

    current_head, error = _git_stdout(cwd, ["rev-parse", "--verify", "HEAD^{commit}"])
    if current_head is None:
        blockers.append(_issue("head_unresolved", error or "could not resolve current HEAD"))
    else:
        summary["current_head"] = current_head

    current_branch, _error = _git_stdout(cwd, ["branch", "--show-current"])
    if not current_branch:
        blockers.append(_issue("detached_head", "current checkout is detached; git-pr-plan requires a branch"))
    else:
        summary["current_branch"] = current_branch

    status = _run_git(cwd, ["status", "--porcelain", "--untracked-files=all"], optional_locks=False)
    if status.returncode != 0:
        blockers.append(_issue("worktree_status_failed", "could not inspect worktree status", detail=status.stderr.strip()))
    else:
        dirty_paths = [line for line in status.stdout.splitlines() if line.strip()]
        summary["dirty_paths"] = dirty_paths
        summary["worktree_clean"] = not dirty_paths
        if dirty_paths and not allow_dirty_worktree:
            blockers.append(
                _issue(
                    "dirty_worktree",
                    "git-pr-plan requires a clean worktree before Git/PR transition review",
                    changed_paths=len(dirty_paths),
                )
            )

    if not _valid_branch_name(cwd, base_branch):
        blockers.append(_issue("invalid_base_branch", f"base branch is not a valid Git branch name: {base_branch}"))
    else:
        base_head, error = _resolve_branch(cwd, base_branch)
        if base_head is None:
            blockers.append(_issue("base_branch_missing", f"local base branch does not resolve: {base_branch}", detail=error))
        else:
            summary["base_head"] = base_head

    if not _valid_branch_name(cwd, proposed_branch):
        blockers.append(
            _issue(
                "invalid_generated_branch",
                f"generated branch is not a valid Git branch name: {proposed_branch}",
            )
        )
    else:
        exists, error = _branch_exists(cwd, proposed_branch)
        if error is not None:
            blockers.append(_issue("generated_branch_lookup_failed", "could not inspect generated branch", detail=error))
        elif exists:
            blockers.append(
                _issue(
                    "generated_branch_exists",
                    f"generated branch already exists locally: {proposed_branch}",
                    branch=proposed_branch,
                )
            )

    return summary, blockers


def _same_resolved_path(left: Any, right: str | Path) -> bool:
    if not _non_empty_string(left):
        return False
    try:
        return Path(str(left)).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return str(left) == str(right)


def _path_from_context(context_file: str | Path, value: Any) -> Path | None:
    if not _non_empty_string(value):
        return None
    try:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = Path(context_file).expanduser().resolve(strict=False).parent / path
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _same_context_path(context_file: str | Path, value: Any, expected: str | Path) -> bool:
    resolved = _path_from_context(context_file, value)
    if resolved is None:
        return False
    try:
        return resolved == Path(expected).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return str(resolved) == str(expected)


def _local_changed_files_against_base(cwd: Path, base_head: Any, current_head: Any) -> tuple[set[str] | None, dict[str, Any] | None]:
    """Return files changed between base and current head."""
    if not (_non_empty_string(base_head) and _non_empty_string(current_head)):
        return None, None
    result = _run_git(cwd, ["diff", "--name-only", f"{base_head}...{current_head}", "--"], optional_locks=False)
    if result.returncode != 0:
        return None, _issue(
            "materialized_change_evidence_unverified",
            "could not inspect local diff against base branch",
            detail=(result.stderr or result.stdout).strip(),
        )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}, None


def _materialized_change_evidence(
    result_evidence: Any,
    *,
    local_changed_files: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate executor materialized evidence against metadata and local diff."""
    blockers: list[dict[str, Any]] = []
    absent = {
        "status": "absent",
        "source": None,
        "files": [],
        "limitations": [
            "files_changed_is_not_materialized_change_evidence",
            "planner_did_not_verify_local_diff_or_commit",
        ],
    }
    if not isinstance(result_evidence, dict):
        blockers.append(_issue("materialized_change_evidence_absent", "materialized change evidence is absent"))
        return absent, blockers

    raw = result_evidence.get("materialized_change_evidence")
    if not isinstance(raw, dict):
        blockers.append(
            _issue(
                "materialized_change_evidence_absent",
                "files_changed is insufficient without explicit materialized_change_evidence",
            )
        )
        return absent, blockers

    files = raw.get("files")
    result_files = {str(path).replace("\\", "/") for path in (result_evidence.get("files_changed") or [])}
    if raw.get("status") != "verified":
        blockers.append(_issue("materialized_change_evidence_invalid", "materialized_change_evidence.status must be verified"))
    if not _non_empty_string(raw.get("source")):
        blockers.append(_issue("materialized_change_evidence_invalid", "materialized_change_evidence.source is required"))
    if raw.get("task_id") != result_evidence.get("task_id"):
        blockers.append(_issue("materialized_change_evidence_invalid", "materialized_change_evidence.task_id must match result task_id"))
    if raw.get("resulting_head") != result_evidence.get("resulting_head"):
        blockers.append(
            _issue(
                "materialized_change_evidence_invalid",
                "materialized_change_evidence.resulting_head must match result resulting_head",
            )
        )
    if not isinstance(files, list) or not files or any(not _non_empty_string(path) for path in files):
        blockers.append(_issue("materialized_change_evidence_invalid", "materialized_change_evidence.files must be a non-empty list of strings"))
    else:
        normalized_files = [path.replace("\\", "/") for path in files]

    if isinstance(files, list) and files and all(_non_empty_string(path) for path in files) and not set(normalized_files).issubset(result_files):
        blockers.append(
            _issue(
                "materialized_change_evidence_invalid",
                "materialized_change_evidence.files must be a subset of result files_changed",
            )
        )
    if (
        local_changed_files is not None
        and isinstance(files, list)
        and files
        and all(_non_empty_string(path) for path in files)
        and set(normalized_files).issubset(result_files)
    ):
        missing_local_files = [path for path in normalized_files if path not in local_changed_files]
        if missing_local_files:
            blockers.append(
                _issue(
                    "materialized_change_evidence_unverified",
                    "materialized_change_evidence.files are not present in the local diff against the base branch",
                    files=missing_local_files,
                )
            )
        extra_local_files = sorted(path for path in local_changed_files if path not in set(normalized_files))
        if extra_local_files:
            blockers.append(
                _issue(
                    "materialized_change_evidence_extra_local_changes",
                    "local diff contains files not declared in materialized_change_evidence.files",
                    files=extra_local_files,
                )
            )

    if blockers:
        return absent, blockers
    limitations = [str(item) for item in raw.get("limitations") or [] if _non_empty_string(item)]
    if local_changed_files is not None:
        limitations = [item for item in limitations if item != "verified_against_result_metadata_not_local_diff"]
        if "verified_against_local_base_diff" not in limitations:
            limitations.append("verified_against_local_base_diff")
        default_limitations = ["verified_against_local_base_diff"]
    else:
        limitations = [item for item in limitations if item != "verified_against_local_base_diff"]
        if "verified_against_result_metadata_not_local_diff" not in limitations:
            limitations.append("verified_against_result_metadata_not_local_diff")
        default_limitations = ["verified_against_result_metadata_not_local_diff"]
    return {
        "status": "verified",
        "source": raw["source"],
        "files": list(files),
        "task_id": raw.get("task_id"),
        "resulting_head": raw.get("resulting_head"),
        "limitations": limitations or default_limitations,
    }, blockers


def _generated_pr_body(task: dict[str, Any], result_evidence: dict[str, Any]) -> str:
    """Render the proposed PR body from task and result evidence."""
    files_changed = result_evidence.get("files_changed") or []
    validation_results = result_evidence.get("validation_results") or []
    source = task.get("source") or "unknown"
    lines = [
        "## Summary",
        "",
        str(result_evidence.get("summary") or task.get("summary") or "").strip(),
        "",
        "## Task",
        "",
        f"- Task: `{task.get('id')}`",
        f"- Source: `{source}`",
        "",
        "## Files Changed",
        "",
    ]
    lines.extend(f"- `{path}`" for path in files_changed)
    lines.extend(["", "## Validation", ""])
    lines.extend(
        f"- `{result.get('name')}`: `{result.get('status')}`"
        for result in validation_results
        if isinstance(result, dict)
    )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Dry run only.",
            "- No branch, commit, push, or pull request was created by Cadence.",
            "",
        ]
    )
    return "\n".join(lines)


def _preflight_pr_body(body: str, required_body_sections: list[str]) -> dict[str, Any]:
    """Evaluate PR body sections, blocking when no contract is supplied."""
    if required_body_sections:
        return evaluate_pr_body_preflight(body, required_body_sections=required_body_sections)
    return {
        "ready_to_publish": False,
        "decision": "blocked",
        "recommended_next_action": "provide_template_or_sections",
        "blockers": [
            _issue(
                "required_body_section_contract_not_supplied",
                "no PR template or required body sections were supplied for body preflight",
            )
        ],
        "warnings": [],
        "template_summary": {"required_sections": [], "missing_sections": []},
    }


def _command_display(argv: list[str]) -> str:
    """Render argv as a shell-quoted display string."""
    return " ".join(shlex.quote(part) for part in argv)


def _command_examples(proposed_branch: str, proposed_commit_message: str, proposed_pr_title: str) -> list[dict[str, Any]]:
    """Return non-executable, operator-confirmed Git and PR command examples."""
    examples = [
        ("create_branch", ["git", "branch", proposed_branch, "HEAD"], None),
        ("commit_changes", ["git", "commit", "-m", proposed_commit_message], None),
        ("push_branch", ["git", "push", "--no-verify", "-u", "origin", proposed_branch], None),
        (
            "open_pull_request",
            ["gh", "pr", "create", "--title", proposed_pr_title, "--body-file", "proposed-pr-body.md"],
            "packet.proposed_pr_body",
        ),
    ]
    return [
        {
            "label": label,
            "argv": list(argv),
            "command": _command_display(argv),
            **({"body_source": body_source} if body_source else {}),
            "cadence_executable": False,
            "executor_authorized": False,
            "requires_operator_confirmation": True,
        }
        for label, argv, body_source in examples
    ]


def _recommendation(blockers: list[dict[str, Any]]) -> str:
    """Map blocker codes to the next recommended operator action."""
    codes = {blocker["code"] for blocker in blockers}
    if "runtime_root_required" in codes or "runtime_brake_missing" in codes:
        return "provide_runtime_root"
    if "active_brake_stop" in codes:
        return "stop_active_loop"
    if "required_body_section_contract_not_supplied" in codes:
        return "provide_template_or_sections"
    if "required_body_section_missing" in codes:
        return "update_pr_body"
    if blockers:
        return "address_blockers"
    return "review_git_pr_plan"


def _branch_policy_blockers(
    policy: dict[str, Any],
    *,
    source: str,
    base_branch: str,
    proposed_branch: str,
    current_branch: str | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    allowed_base_branches = policy.get("allowed_base_branches", [])
    if allowed_base_branches and base_branch not in allowed_base_branches:
        blockers.append(
            _issue(
                "branch_policy_base_branch_disallowed",
                f"base branch is not allowed by branch_policy: {base_branch}",
                source=source,
                base_branch=base_branch,
                allowed_base_branches=allowed_base_branches,
            )
        )
    denied_target_branches = policy.get("denied_target_branches", [])
    if proposed_branch in denied_target_branches:
        blockers.append(
            _issue(
                "branch_policy_target_branch_denied",
                f"proposed branch is denied by branch_policy: {proposed_branch}",
                source=source,
                proposed_branch=proposed_branch,
                denied_target_branches=denied_target_branches,
            )
        )
    required_prefixes = policy.get("required_branch_prefixes", [])
    if required_prefixes and not any(proposed_branch.startswith(prefix) for prefix in required_prefixes):
        blockers.append(
            _issue(
                "branch_policy_required_prefix_missing",
                f"proposed branch does not use a required branch_policy prefix: {proposed_branch}",
                source=source,
                proposed_branch=proposed_branch,
                required_branch_prefixes=required_prefixes,
            )
        )
    if policy.get("allow_current_branch_main") is False and current_branch == "main":
        blockers.append(
            _issue(
                "branch_policy_current_branch_main_disallowed",
                "current branch is main and branch_policy does not allow planning from main",
                source=source,
                current_branch=current_branch,
            )
        )
    return blockers


def evaluate_git_pr_plan(
    *,
    cwd: str | Path,
    task_packet: Any,
    result_evidence: Any,
    task_file: str | Path,
    result_file: str | Path,
    base_branch: str = "main",
    branch_prefix: str = "cadence",
    proposed_branch_override: str | None = None,
    branch_policy: Any | None = None,
    required_body_sections: list[str] | None = None,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a dry-run Git/PR transition plan from executor evidence."""
    repo_cwd = Path(cwd).expanduser().resolve()
    task_path = Path(task_file).expanduser().resolve(strict=False)
    result_path = Path(result_file).expanduser().resolve(strict=False)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_sections = [section for section in (required_body_sections or []) if section.strip()]

    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    generated_branch = _generated_branch_name(branch_prefix, task.get("id"))
    proposed_branch = proposed_branch_override.strip() if _non_empty_string(proposed_branch_override) else generated_branch
    proposed_commit_message = str(task.get("title") or "").strip()
    proposed_pr_title = proposed_commit_message
    branch_policy_sources: list[dict[str, Any]] = []
    task_branch_policy = None
    if isinstance(task_packet, dict) and "branch_policy" in task_packet:
        try:
            task_branch_policy = normalize_branch_policy(
                task_packet.get("branch_policy"),
                label="executor task branch_policy",
                require_object=True,
            )
            branch_policy_sources.append({"source": "task_packet", "policy": task_branch_policy})
        except ValueError:
            task_branch_policy = None
    policy_file_branch_policy = None
    if branch_policy is not None:
        try:
            policy_file_branch_policy = normalize_branch_policy(
                branch_policy,
                label="git-pr-plan branch_policy",
                require_object=True,
            )
            branch_policy_sources.append({"source": "policy_file", "policy": policy_file_branch_policy})
        except ValueError as exc:
            blockers.append(_issue("branch_policy_invalid", str(exc)))

    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        blockers.append(_issue("invalid_task_packet", task_reason))
        valid_result = False
        result_reason = "task packet is invalid"
    else:
        valid_result, result_reason = validate_executor_result_evidence(result_evidence, task_packet)
        if not valid_result:
            blockers.append(_issue("invalid_result_evidence", result_reason))
        expected_output = task_packet.get("expected_output")
        expected_path = expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
        if expected_path is not None and Path(expected_path).expanduser().resolve(strict=False) != result_path:
            blockers.append(
                _issue(
                    "result_file_mismatch",
                    "executor result file does not match task expected_output.evidence_path",
                )
            )

    status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    if status != "succeeded":
        blockers.append(_issue("result_not_successful", f"executor result status is not succeeded: {status}"))

    git_summary, git_blockers = _inspect_git_state(repo_cwd, base_branch=base_branch, proposed_branch=proposed_branch)
    blockers.extend(git_blockers)
    for source_policy in branch_policy_sources:
        blockers.extend(
            _branch_policy_blockers(
                source_policy["policy"],
                source=source_policy["source"],
                base_branch=base_branch,
                proposed_branch=proposed_branch,
                current_branch=git_summary.get("current_branch"),
            )
        )

    task_repo_path = repo.get("path")
    if isinstance(task_repo_path, str) and task_repo_path.strip():
        normalized_task_repo = str(Path(task_repo_path).expanduser().resolve(strict=False))
        if normalized_task_repo != git_summary["repository_path"]:
            blockers.append(
                _issue(
                    "repo_path_mismatch",
                    "current repo path does not match task packet repo.path",
                    task_repo_path=normalized_task_repo,
                    current_repo_path=git_summary["repository_path"],
                )
            )
    task_branch = repo.get("branch")
    current_branch = git_summary.get("current_branch")
    if _non_empty_string(task_branch) and current_branch and current_branch != task_branch:
        blockers.append(
            _issue(
                "current_branch_mismatch",
                f"current branch is {current_branch}, expected task branch {task_branch}",
                current_branch=current_branch,
                task_branch=task_branch,
            )
        )
    current_head = git_summary.get("current_head")
    resulting_head = result_evidence.get("resulting_head") if isinstance(result_evidence, dict) else None
    if _non_empty_string(resulting_head) and current_head and resulting_head != current_head:
        blockers.append(
            _issue(
                "head_mismatch",
                "executor result resulting_head does not match current HEAD",
                resulting_head=resulting_head,
                current_head=current_head,
            )
        )

    local_changed_files, local_diff_blocker = _local_changed_files_against_base(
        repo_cwd,
        git_summary.get("base_head"),
        git_summary.get("current_head"),
    )
    if local_diff_blocker is not None:
        blockers.append(local_diff_blocker)

    materialized_evidence, materialized_blockers = _materialized_change_evidence(
        result_evidence,
        local_changed_files=local_changed_files,
    )
    blockers.extend(materialized_blockers)

    stop_conditions = task_packet.get("stop_conditions") if isinstance(task_packet, dict) else []
    needs_brake_check = (
        valid_task
        and valid_result
        and isinstance(stop_conditions, list)
        and "brake_not_drive" in stop_conditions
        and status == "succeeded"
    )
    active_stop = None
    if needs_brake_check:
        brake, brake_blocker = _read_brake_without_writes(Path(runtime_root).expanduser().resolve() if runtime_root else None)
        if brake_blocker is not None:
            blockers.append(brake_blocker)
        elif brake is not None and brake["status"] != "DRIVE":
            active_stop = {
                "brake_status": brake["status"],
                "cadence": _cadence_state(brake),
                "reason": brake.get("reason"),
                "required_result_status": "stopped",
            }
            blockers.append(
                _issue(
                    "active_brake_stop",
                    "cadence brake is not DRIVE; executor result must report stopped before Git/PR planning",
                    brake_status=brake["status"],
                )
            )

    pr_body = _generated_pr_body(task, result_evidence if isinstance(result_evidence, dict) else {})
    pr_body_preflight = _preflight_pr_body(pr_body, required_sections)
    for blocker in pr_body_preflight.get("blockers", []):
        if isinstance(blocker, dict):
            blockers.append(blocker)
    for warning in pr_body_preflight.get("warnings", []):
        if isinstance(warning, dict):
            warnings.append(warning)

    recommended_next_action = _recommendation(blockers)
    ready = not blockers
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GIT_PR_PLAN_SCHEMA_VERSION,
        "packet": "git_pr_plan",
        "generated_at": utc_now(),
        "ready_to_review": ready,
        "decision": "ready" if ready else "blocked",
        "recommended_next_action": recommended_next_action,
        "dry_run": True,
        "operator_confirmation_required": True,
        "side_effects": [],
        "approval_state": "not_approved",
        "execution_authority": "none",
        "merge_readiness": "not_evaluated",
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "summary": task.get("summary"),
            "source": task.get("source"),
        },
        "repository": git_summary,
        "evidence_provenance": {
            "task_file": str(task_path),
            "result_file": str(result_path),
            "task_file_checksum": checksum_json(task_packet),
            "result_file_checksum": checksum_json(result_evidence),
            "executor_id": result_evidence.get("executor_id") if isinstance(result_evidence, dict) else None,
            "task_repo_head": repo.get("head"),
            "result_head": resulting_head,
            "materialized_change_evidence_source": materialized_evidence.get("source"),
        },
        "materialized_change_evidence": materialized_evidence,
        "proposed_branch": proposed_branch,
        "proposed_commit_message": proposed_commit_message,
        "proposed_pr_title": proposed_pr_title,
        "proposed_pr_body": pr_body,
        "pr_body_preflight": pr_body_preflight,
        "command_examples": _command_examples(proposed_branch, proposed_commit_message, proposed_pr_title) if ready else [],
        "branch_policy": {
            "task_packet": task_branch_policy,
            "policy_file": policy_file_branch_policy,
        },
        "blockers": blockers,
        "warnings": warnings,
        "active_stop": active_stop,
        "limitations": [
            "dry_run_only",
            "does_not_execute_git_commands",
            "does_not_call_github",
            "operator_confirmation_required",
            "executor_is_not_git_pr_approval_authority",
        ],
    }


def _dirty_materialization_recommendation(blockers: list[dict[str, Any]]) -> str:
    codes = {blocker["code"] for blocker in blockers}
    if not blockers:
        return "approve_dirty_git_pr_materialization"
    if "required_body_section_contract_not_supplied" in codes:
        return "provide_template_or_sections"
    if "required_body_section_missing" in codes:
        return "update_pr_body"
    if codes & {
        "dirty_worktree_fingerprint_mismatch",
        "materialized_change_files_mismatch",
        "real_invocation_not_closeout_approved",
    }:
        return "inspect_materialized_change_evidence"
    return "address_blockers"


def _dirty_materialization_absent() -> dict[str, Any]:
    return {
        "status": "absent",
        "source": None,
        "files": [],
        "limitations": ["dirty_worktree_materialized_change_evidence_absent"],
    }


def _dirty_closeout_core_checksum(closeout_packet: dict[str, Any]) -> str:
    core_packet = {
        key: value
        for key, value in closeout_packet.items()
        if key not in {"audit_record", "run_record", "real_invocation"}
    }
    return checksum_json(core_packet)


def _dirty_materialization_closeout_blockers(
    *,
    closeout_packet: Any,
    closeout_file: Path,
    task_packet: Any,
    real_invocation: Any,
    task_file: Path,
    result_file: Path,
    real_invocation_file: Path,
) -> tuple[str | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(closeout_packet, dict):
        return None, [_issue("closeout_evidence_missing", "executor closeout evidence must be a JSON object")]
    if closeout_packet.get("protocol_version") != PROTOCOL_VERSION:
        blockers.append(_issue("closeout_invalid", "executor closeout protocol_version is invalid"))
    if closeout_packet.get("schema_version") != EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "closeout_invalid",
                "executor closeout schema_version is unsupported",
                expected_schema=EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION,
                actual_schema=closeout_packet.get("schema_version"),
            )
        )
    if closeout_packet.get("packet") != "executor_epoch_closeout":
        blockers.append(_issue("closeout_invalid", "executor closeout packet is invalid"))
    if closeout_packet.get("valid") is not True or closeout_packet.get("closeout_status") != "completed":
        blockers.append(
            _issue(
                "real_invocation_not_closeout_approved",
                "executor closeout evidence must be valid and completed before dirty Git/PR materialization planning",
                closeout_status=closeout_packet.get("closeout_status"),
            )
        )
    if closeout_packet.get("task_checksum") != checksum_json(task_packet):
        blockers.append(_issue("closeout_task_mismatch", "executor closeout task checksum does not match supplied task packet"))
    if not _same_context_path(closeout_file, closeout_packet.get("task_file"), task_file):
        blockers.append(_issue("closeout_task_mismatch", "executor closeout task_file does not match supplied task file"))
    if not _same_context_path(closeout_file, closeout_packet.get("result_file"), result_file):
        blockers.append(_issue("closeout_result_mismatch", "executor closeout result_file does not match supplied result file"))
    if closeout_packet.get("executor_result_status") != "succeeded":
        blockers.append(
            _issue(
                "closeout_result_mismatch",
                "executor closeout result status must be succeeded for dirty Git/PR materialization planning",
                executor_result_status=closeout_packet.get("executor_result_status"),
            )
        )
    validation = closeout_packet.get("validation") if isinstance(closeout_packet.get("validation"), dict) else {}
    if validation.get("valid") is not True:
        blockers.append(_issue("closeout_validation_mismatch", "executor closeout validation evidence is not valid"))
    closeout_invocation = closeout_packet.get("real_invocation") if isinstance(closeout_packet.get("real_invocation"), dict) else {}
    if not closeout_invocation:
        blockers.append(_issue("closeout_invocation_mismatch", "executor closeout must include real_invocation binding evidence"))
    elif isinstance(real_invocation, dict):
        if not _same_context_path(closeout_file, closeout_invocation.get("path"), real_invocation_file):
            blockers.append(_issue("closeout_invocation_mismatch", "executor closeout real_invocation.path does not match supplied real invocation file"))
        if closeout_invocation.get("invocation_id") != real_invocation.get("invocation_id"):
            blockers.append(_issue("closeout_invocation_mismatch", "executor closeout invocation_id does not match supplied real invocation"))
        if closeout_invocation.get("after_checksum") != checksum_json(real_invocation):
            blockers.append(_issue("closeout_invocation_mismatch", "executor closeout real_invocation.after_checksum does not match supplied real invocation"))
        epoch_closeout_checksum = _dirty_closeout_core_checksum(closeout_packet)
        if closeout_invocation.get("epoch_closeout_checksum") != epoch_closeout_checksum:
            blockers.append(
                _issue(
                    "closeout_invocation_mismatch",
                    "executor closeout real_invocation.epoch_closeout_checksum does not match closeout evidence",
                    expected=epoch_closeout_checksum,
                    actual=closeout_invocation.get("epoch_closeout_checksum"),
                )
            )
        if real_invocation.get("epoch_closeout_checksum") != epoch_closeout_checksum:
            blockers.append(
                _issue(
                    "real_invocation_not_closeout_approved",
                    "real invocation epoch_closeout_checksum does not match executor closeout evidence",
                    expected=epoch_closeout_checksum,
                    actual=real_invocation.get("epoch_closeout_checksum"),
                )
            )
        if real_invocation.get("closeout_status") != closeout_packet.get("closeout_status"):
            blockers.append(
                _issue(
                    "real_invocation_not_closeout_approved",
                    "real invocation closeout_status does not match executor closeout evidence",
                    expected=closeout_packet.get("closeout_status"),
                    actual=real_invocation.get("closeout_status"),
                )
            )
    return _dirty_closeout_core_checksum(closeout_packet), blockers


def evaluate_dirty_git_pr_materialization_plan(
    *,
    cwd: str | Path,
    task_packet: Any,
    result_evidence: Any,
    real_invocation: Any,
    closeout_packet: Any,
    task_file: str | Path,
    result_file: str | Path,
    real_invocation_file: str | Path,
    closeout_file: str | Path,
    base_branch: str = "main",
    branch_prefix: str = "cadence",
    proposed_branch_override: str | None = None,
    branch_policy: Any | None = None,
    required_body_sections: list[str] | None = None,
    remote: str = "origin",
    pr_number: str | None = None,
    expected_base_head: str | None = None,
) -> dict[str, Any]:
    """Build a read-only plan for operator-approved dirty-worktree Git/PR materialization."""
    repo_cwd = Path(cwd).expanduser().resolve()
    task_path = Path(task_file).expanduser().resolve(strict=False)
    result_path = Path(result_file).expanduser().resolve(strict=False)
    invocation_path = Path(real_invocation_file).expanduser().resolve(strict=False)
    closeout_path = Path(closeout_file).expanduser().resolve(strict=False)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_sections = [section for section in (required_body_sections or []) if section.strip()]

    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    generated_branch = _generated_branch_name(branch_prefix, task.get("id"))
    proposed_branch = proposed_branch_override.strip() if _non_empty_string(proposed_branch_override) else generated_branch
    proposed_commit_message = str(task.get("title") or "").strip()
    proposed_pr_title = proposed_commit_message
    branch_policy_sources: list[dict[str, Any]] = []
    task_branch_policy = None
    if isinstance(task_packet, dict) and "branch_policy" in task_packet:
        try:
            task_branch_policy = normalize_branch_policy(
                task_packet.get("branch_policy"),
                label="executor task branch_policy",
                require_object=True,
            )
            branch_policy_sources.append({"source": "task_packet", "policy": task_branch_policy})
        except ValueError:
            task_branch_policy = None
    policy_file_branch_policy = None
    if branch_policy is not None:
        try:
            policy_file_branch_policy = normalize_branch_policy(
                branch_policy,
                label="git-pr-dirty-materialization-plan branch_policy",
                require_object=True,
            )
            branch_policy_sources.append({"source": "policy_file", "policy": policy_file_branch_policy})
        except ValueError as exc:
            blockers.append(_issue("branch_policy_invalid", str(exc)))

    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        blockers.append(_issue("invalid_task_packet", task_reason))
        valid_result = False
        result_reason = "task packet is invalid"
    else:
        valid_result, result_reason = validate_executor_result_evidence(
            result_evidence,
            task_packet,
            allow_succeeded_dirty_worktree=True,
        )
        if not valid_result:
            blockers.append(_issue("invalid_result_evidence", result_reason))
        expected_output = task_packet.get("expected_output")
        expected_path = expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
        if expected_path is not None and Path(expected_path).expanduser().resolve(strict=False) != result_path:
            blockers.append(_issue("result_file_mismatch", "executor result file does not match task expected_output.evidence_path"))

    status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    if status != "succeeded":
        blockers.append(_issue("result_not_successful", f"executor result status is not succeeded: {status}"))

    git_summary, git_blockers = _inspect_git_state(
        repo_cwd,
        base_branch=base_branch,
        proposed_branch=proposed_branch,
        allow_dirty_worktree=True,
    )
    blockers.extend(git_blockers)
    for source_policy in branch_policy_sources:
        blockers.extend(
            _branch_policy_blockers(
                source_policy["policy"],
                source=source_policy["source"],
                base_branch=base_branch,
                proposed_branch=proposed_branch,
                current_branch=git_summary.get("current_branch"),
            )
        )
    if _non_empty_string(expected_base_head) and git_summary.get("base_head") != expected_base_head:
        blockers.append(
            _issue(
                "base_head_mismatch",
                "local base branch head does not match expected base head",
                expected=expected_base_head,
                actual=git_summary.get("base_head"),
            )
        )

    task_repo_path = repo.get("path")
    if isinstance(task_repo_path, str) and task_repo_path.strip():
        normalized_task_repo = str(Path(task_repo_path).expanduser().resolve(strict=False))
        if normalized_task_repo != git_summary["repository_path"]:
            blockers.append(
                _issue(
                    "repo_path_mismatch",
                    "current repo path does not match task packet repo.path",
                    task_repo_path=normalized_task_repo,
                    current_repo_path=git_summary["repository_path"],
                )
            )
    task_branch = repo.get("branch")
    current_branch = git_summary.get("current_branch")
    if _non_empty_string(task_branch) and current_branch and current_branch != task_branch:
        blockers.append(
            _issue(
                "current_branch_mismatch",
                f"current branch is {current_branch}, expected task branch {task_branch}",
                current_branch=current_branch,
                task_branch=task_branch,
            )
        )
    current_head = git_summary.get("current_head")
    resulting_head = result_evidence.get("resulting_head") if isinstance(result_evidence, dict) else None
    if _non_empty_string(resulting_head) and current_head and resulting_head != current_head:
        blockers.append(
            _issue(
                "head_mismatch",
                "executor result resulting_head does not match current HEAD",
                resulting_head=resulting_head,
                current_head=current_head,
            )
        )

    real_invocation_checksum = checksum_json(real_invocation) if isinstance(real_invocation, dict) else None
    if not isinstance(real_invocation, dict):
        blockers.append(_issue("real_invocation_invalid", "real invocation evidence must be a JSON object"))
        invocation_materialized = _dirty_materialization_absent()
        repository_after: dict[str, Any] = {}
    else:
        invocation_materialized = (
            real_invocation.get("materialized_change_evidence")
            if isinstance(real_invocation.get("materialized_change_evidence"), dict)
            else _dirty_materialization_absent()
        )
        repository_after = real_invocation.get("repository_after") if isinstance(real_invocation.get("repository_after"), dict) else {}
        if real_invocation.get("schema_version") != "real-executor-invocation.v1":
            blockers.append(_issue("real_invocation_invalid", "real invocation schema_version is unsupported"))
        if real_invocation.get("packet") != "real_executor_invocation":
            blockers.append(_issue("real_invocation_invalid", "real invocation packet is invalid"))
        if real_invocation.get("valid") is not True or real_invocation.get("executor_started") is not True:
            blockers.append(_issue("real_invocation_invalid", "real invocation must be valid and executor-started"))
        if real_invocation.get("timed_out") is True:
            blockers.append(_issue("real_invocation_invalid", "real invocation must not be timed out"))
        if real_invocation.get("side_effect_mode") != "materialized_changes":
            blockers.append(
                _issue(
                    "real_invocation_not_materialized",
                    "real invocation must use side_effect_mode materialized_changes",
                )
            )
        if real_invocation.get("closeout_status") != "completed" or not _valid_sha256_checksum(
            real_invocation.get("epoch_closeout_checksum")
        ):
            blockers.append(
                _issue(
                    "real_invocation_not_closeout_approved",
                    "real invocation must be bound to completed executor closeout with a valid checksum before dirty Git/PR materialization planning",
                    closeout_status=real_invocation.get("closeout_status"),
                    epoch_closeout_checksum=real_invocation.get("epoch_closeout_checksum"),
                )
            )
        if real_invocation.get("result_evidence_checksum") != checksum_json(result_evidence):
            blockers.append(
                _issue(
                    "result_evidence_changed",
                    "executor result checksum no longer matches the closeout-approved real invocation",
                )
            )
        if not _same_resolved_path(real_invocation.get("record_file"), invocation_path):
            blockers.append(_issue("real_invocation_record_mismatch", "real invocation record_file does not match supplied real invocation file"))
        if not _same_resolved_path(real_invocation.get("result_file"), result_path):
            blockers.append(_issue("result_file_mismatch", "real invocation result_file does not match supplied result file"))
        if "task_file" in real_invocation and not _same_resolved_path(real_invocation.get("task_file"), task_path):
            blockers.append(_issue("task_file_mismatch", "real invocation task_file does not match supplied task file"))

    closeout_checksum, closeout_blockers = _dirty_materialization_closeout_blockers(
        closeout_packet=closeout_packet,
        closeout_file=closeout_path,
        task_packet=task_packet,
        real_invocation=real_invocation,
        task_file=task_path,
        result_file=result_path,
        real_invocation_file=invocation_path,
    )
    blockers.extend(closeout_blockers)

    if repository_after:
        if not _same_resolved_path(repository_after.get("cwd"), repo_cwd):
            blockers.append(_issue("repository_path_mismatch", "current repo path does not match real invocation repository_after.cwd"))
        if repository_after.get("branch") != git_summary.get("current_branch"):
            blockers.append(
                _issue(
                    "repository_branch_mismatch",
                    "current branch does not match real invocation repository_after.branch",
                    expected=repository_after.get("branch"),
                    actual=git_summary.get("current_branch"),
                )
            )
        if repository_after.get("head") != git_summary.get("current_head"):
            blockers.append(
                _issue(
                    "repository_head_mismatch",
                    "current HEAD does not match real invocation repository_after.head",
                    expected=repository_after.get("head"),
                    actual=git_summary.get("current_head"),
                )
            )
        if repository_after.get("dirty_worktree") is not True:
            blockers.append(_issue("dirty_worktree_missing", "real invocation repository_after must be dirty"))
    elif isinstance(real_invocation, dict):
        blockers.append(_issue("repository_after_missing", "real invocation repository_after evidence is required"))

    result_materialized = (
        result_evidence.get("materialized_change_evidence")
        if isinstance(result_evidence, dict) and isinstance(result_evidence.get("materialized_change_evidence"), dict)
        else {}
    )
    if invocation_materialized.get("status") != "verified":
        blockers.append(_issue("materialized_change_evidence_unverified", "real invocation materialized evidence must be verified"))
    if checksum_json({key: value for key, value in invocation_materialized.items() if key not in {"worktree_fingerprint_schema_version", "worktree_fingerprint_checksum"}}) != checksum_json(result_materialized):
        blockers.append(
            _issue(
                "materialized_change_evidence_mismatch",
                "real invocation materialized evidence does not match result evidence",
            )
        )
    materialized_files = invocation_materialized.get("files")
    normalized_materialized_files = (
        {str(path).replace("\\", "/") for path in materialized_files}
        if isinstance(materialized_files, list) and all(_non_empty_string(path) for path in materialized_files)
        else set()
    )
    if not normalized_materialized_files:
        blockers.append(_issue("materialized_change_evidence_unverified", "materialized evidence files are required"))

    dirty_files, dirty_files_blocker = _local_dirty_files(repo_cwd)
    if dirty_files_blocker is not None:
        blockers.append(
            _issue(
                "dirty_worktree_unreadable",
                "current dirty worktree files could not be inspected",
                invocation_blocker=dirty_files_blocker,
            )
        )
    elif dirty_files is not None and normalized_materialized_files != dirty_files:
        blockers.append(
            _issue(
                "materialized_change_files_mismatch",
                "current dirty worktree files must exactly match materialized-change evidence",
                expected_files=sorted(normalized_materialized_files),
                actual_files=sorted(dirty_files),
            )
        )

    dirty_fingerprint = None
    dirty_fingerprint_checksum = None
    if dirty_files is not None:
        dirty_fingerprint, fingerprint_blocker = _dirty_worktree_fingerprint(repo_cwd, dirty_files)
        if fingerprint_blocker is not None:
            blockers.append(
                _issue(
                    "dirty_worktree_unreadable",
                    "current dirty worktree fingerprint could not be computed",
                    invocation_blocker=fingerprint_blocker,
                )
            )
        elif dirty_fingerprint is not None:
            dirty_fingerprint_checksum = checksum_json(dirty_fingerprint)
            if invocation_materialized.get("worktree_fingerprint_schema_version") != DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION:
                blockers.append(
                    _issue(
                        "dirty_worktree_fingerprint_missing",
                        "real invocation materialized evidence is missing dirty-worktree fingerprint schema",
                        expected_schema=DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
                        actual_schema=invocation_materialized.get("worktree_fingerprint_schema_version"),
                    )
                )
            elif invocation_materialized.get("worktree_fingerprint_checksum") != dirty_fingerprint_checksum:
                blockers.append(
                    _issue(
                        "dirty_worktree_fingerprint_mismatch",
                        "current dirty worktree fingerprint does not match closeout-approved real invocation",
                        expected=invocation_materialized.get("worktree_fingerprint_checksum"),
                        actual=dirty_fingerprint_checksum,
                    )
                )

    pr_body = _generated_pr_body(task, result_evidence if isinstance(result_evidence, dict) else {})
    pr_body_preflight = _preflight_pr_body(pr_body, required_sections)
    for blocker in pr_body_preflight.get("blockers", []):
        if isinstance(blocker, dict):
            blockers.append(blocker)
    for warning in pr_body_preflight.get("warnings", []):
        if isinstance(warning, dict):
            warnings.append(warning)

    remote_url, remote_blockers = _remote_push_url(repo_cwd, remote)
    blockers.extend(remote_blockers)
    proposed_files = sorted(normalized_materialized_files)
    target = {
        "schema_version": "git-pr-dirty-materialization-target.v1",
        "packet": "git_pr_dirty_materialization_target",
        "operation": "dirty_worktree_git_pr_materialization",
        "task_id": task.get("id"),
        "source_head": current_head,
        "base_branch": base_branch,
        "base_head": git_summary.get("base_head"),
        "expected_base_head": expected_base_head,
        "proposed_branch": proposed_branch,
        "proposed_commit_message": proposed_commit_message,
        "proposed_pr_title": proposed_pr_title,
        "proposed_pr_body_checksum": checksum_json(pr_body),
        "remote": remote,
        "remote_url": remote_url,
        "pr_number": str(pr_number) if pr_number is not None else None,
        "materialized_change_files": proposed_files,
        "dirty_worktree_fingerprint_checksum": dirty_fingerprint_checksum,
        "task_file_checksum": checksum_json(task_packet) if isinstance(task_packet, dict) else None,
        "result_file_checksum": checksum_json(result_evidence) if isinstance(result_evidence, dict) else None,
        "real_invocation_checksum": real_invocation_checksum,
        "closeout_file_checksum": checksum_json(closeout_packet) if isinstance(closeout_packet, dict) else None,
        "epoch_closeout_checksum": closeout_checksum,
    }
    valid = not blockers
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GIT_PR_DIRTY_MATERIALIZATION_PLAN_SCHEMA_VERSION,
        "packet": "git_pr_dirty_materialization_plan",
        "generated_at": utc_now(),
        "valid": valid,
        "ready_to_review": valid,
        "decision": "ready" if valid else "blocked",
        "recommended_next_action": _dirty_materialization_recommendation(blockers),
        "dry_run": True,
        "operator_confirmation_required": True,
        "side_effects": [],
        "approval_state": "not_approved",
        "execution_authority": "none",
        "merge_readiness": "not_evaluated",
        "target": target,
        "target_checksum": checksum_json(target),
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "summary": task.get("summary"),
            "source": task.get("source"),
        },
        "repository": git_summary,
        "evidence_provenance": {
            "task_file": str(task_path),
            "result_file": str(result_path),
            "real_invocation_file": str(invocation_path),
            "closeout_file": str(closeout_path),
            "task_file_checksum": checksum_json(task_packet) if isinstance(task_packet, dict) else None,
            "result_file_checksum": checksum_json(result_evidence) if isinstance(result_evidence, dict) else None,
            "real_invocation_checksum": real_invocation_checksum,
            "closeout_file_checksum": checksum_json(closeout_packet) if isinstance(closeout_packet, dict) else None,
            "executor_id": result_evidence.get("executor_id") if isinstance(result_evidence, dict) else None,
            "result_head": resulting_head,
        },
        "real_invocation": {
            "file": str(invocation_path),
            "checksum": real_invocation_checksum,
            "invocation_id": real_invocation.get("invocation_id") if isinstance(real_invocation, dict) else None,
            "closeout_status": real_invocation.get("closeout_status") if isinstance(real_invocation, dict) else None,
            "epoch_closeout_checksum": real_invocation.get("epoch_closeout_checksum") if isinstance(real_invocation, dict) else None,
        },
        "closeout": {
            "file": str(closeout_path),
            "checksum": checksum_json(closeout_packet) if isinstance(closeout_packet, dict) else None,
            "epoch_closeout_checksum": closeout_checksum,
            "closeout_status": closeout_packet.get("closeout_status") if isinstance(closeout_packet, dict) else None,
        },
        "materialized_change_evidence": invocation_materialized,
        "dirty_worktree_fingerprint": dirty_fingerprint,
        "proposed_commit": {
            "message": proposed_commit_message,
            "files": proposed_files,
            "source_head": current_head,
            "base_branch": base_branch,
            "base_head": git_summary.get("base_head"),
            "dirty_worktree_fingerprint_checksum": dirty_fingerprint_checksum,
        },
        "proposed_branch": proposed_branch,
        "proposed_pr_title": proposed_pr_title,
        "proposed_pr_body": pr_body,
        "pr_body_preflight": pr_body_preflight,
        "branch_policy": {
            "task_packet": task_branch_policy,
            "policy_file": policy_file_branch_policy,
        },
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "dry_run_only",
            "does_not_stage_or_commit",
            "does_not_create_branch_push_or_pr",
            "does_not_call_github",
            "operator_confirmation_required",
            "dirty_worktree_must_match_closeout_approved_fingerprint",
        ],
    }


def _materialization_required_sections(plan_packet: dict[str, Any]) -> list[str]:
    preflight = plan_packet.get("pr_body_preflight") if isinstance(plan_packet.get("pr_body_preflight"), dict) else {}
    template_summary = preflight.get("template_summary") if isinstance(preflight.get("template_summary"), dict) else {}
    sections = template_summary.get("required_sections") if isinstance(template_summary, dict) else []
    required_sections: list[str] = []
    for section in sections:
        if _non_empty_string(section):
            required_sections.append(section)
        elif isinstance(section, dict) and _non_empty_string(section.get("section")):
            required_sections.append(section["section"])
    return required_sections


def _materialized_pr_body(plan_packet: dict[str, Any]) -> str:
    """Convert a reviewed dry-run PR body into a body suitable for an approved PR."""
    body = str(plan_packet.get("proposed_pr_body") or "")
    replacements = {
        "- Dry run only.": "- Operator-approved Git/PR materialization completed by Cadence.",
        "- No branch, commit, push, or pull request was created by Cadence.": (
            "- No auto-merge, release, package publication, or executor invocation was performed by Cadence."
        ),
    }
    for before, after in replacements.items():
        body = body.replace(before, after)
    return body


def _load_materialization_json(path: Any, code: str, message: str) -> tuple[Any | None, dict[str, Any] | None]:
    if not _non_empty_string(path):
        return None, _issue(code, message)
    try:
        return read_json(Path(path)), None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, _issue(code, f"{message}: {exc}")


def _materialization_command_trace(
    *,
    label: str,
    argv: list[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    resolved_argv = list(result.args) if isinstance(result.args, list) else []
    return {
        "label": label,
        "argv": argv,
        "resolved_executable": resolved_argv[0] if resolved_argv else None,
        "command": _command_display(argv),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _materialization_error_trace(*, label: str, message: str) -> dict[str, Any]:
    """Record a non-subprocess materialization failure in command-trace shape."""
    return {
        "label": label,
        "argv": [],
        "resolved_executable": None,
        "command": label,
        "returncode": 1,
        "stdout": "",
        "stderr": message,
    }


def _refresh_materialization_repository(cwd: Path, repository: Any) -> dict[str, Any]:
    """Refresh live Git state for packets emitted after materialized side effects."""
    refreshed = dict(repository) if isinstance(repository, dict) else {}
    repo_root, _error = _git_stdout(cwd, ["rev-parse", "--show-toplevel"])
    if repo_root is not None:
        refreshed["repository_path"] = str(Path(repo_root).resolve())
    current_head, _error = _git_stdout(cwd, ["rev-parse", "--verify", "HEAD^{commit}"])
    if current_head is not None:
        refreshed["current_head"] = current_head
    current_branch, _error = _git_stdout(cwd, ["branch", "--show-current"])
    if current_branch:
        refreshed["current_branch"] = current_branch
    status = _run_git(cwd, ["status", "--porcelain", "--untracked-files=all"], optional_locks=False)
    if status.returncode == 0:
        dirty_paths = [line for line in status.stdout.splitlines() if line.strip()]
        refreshed["dirty_paths"] = dirty_paths
        refreshed["worktree_clean"] = not dirty_paths
    return refreshed


def _materialization_recommendation(blockers: list[dict[str, Any]], *, side_effects_started: bool) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if not blockers:
        return "inspect_pull_request"
    if "pr_evidence_stale" in codes or "pr_evidence_from_future" in codes or "pr_evidence_unreadable" in codes:
        return "refresh_pr_evidence"
    if "operator_approval_missing" in codes or "operator_approval_mismatch" in codes:
        return "provide_operator_approval"
    if "stale_git_pr_plan" in codes or "git_pr_plan_recheck_changed" in codes:
        return "refresh_git_pr_plan"
    if side_effects_started:
        return "inspect_git_pr_materialization"
    return "address_blockers"


def _materialization_packet(
    *,
    valid: bool,
    decision: str,
    approval_state: str,
    plan_file: Path,
    plan_checksum: str | None,
    repository: dict[str, Any] | None,
    proposed_branch: str | None,
    proposed_pr_title: str | None,
    pr_url: str | None,
    intended_side_effects: list[str],
    side_effects: list[str],
    command_trace: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    pr_number: str | None,
    remote: str,
    remote_url: str | None = None,
    pr_evidence: dict[str, Any] | None = None,
    dirty_commit_materialization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    side_effects_started = bool(side_effects)
    packet = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GIT_PR_MATERIALIZATION_SCHEMA_VERSION,
        "packet": "git_pr_materialization",
        "generated_at": utc_now(),
        "valid": valid,
        "decision": decision,
        "recommended_next_action": _materialization_recommendation(blockers, side_effects_started=side_effects_started),
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": approval_state,
        "execution_authority": (
            "operator_approved_git_pr_materialization" if approval_state == "approved" else "none"
        ),
        "merge_readiness": "not_evaluated",
        "plan_file": str(plan_file),
        "plan_checksum": plan_checksum,
        "repository": repository or {},
        "proposed_branch": proposed_branch,
        "proposed_pr_title": proposed_pr_title,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "remote": remote,
        "remote_url": remote_url,
        "intended_side_effects": intended_side_effects,
        "side_effects": side_effects,
        "command_trace": command_trace,
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "operator_approved_git_pr_materialization_only",
            "does_not_auto_merge",
            "does_not_release",
            "does_not_publish_packages",
            "does_not_invoke_executor",
        ],
    }
    if pr_evidence is not None:
        packet["pr_evidence"] = pr_evidence
    if dirty_commit_materialization is not None:
        packet["dirty_commit_materialization"] = dirty_commit_materialization
    return packet


def _materialization_pr_evidence(
    *,
    pr_evidence: dict[str, Any] | None,
    source: str,
    captured_at: Any,
    max_age_minutes: int | None,
    path: str | Path | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if pr_evidence is None:
        return None, []
    evidence, waiting = _pr_readiness_evidence(
        source=source,
        captured_at=captured_at,
        max_age_minutes=max_age_minutes,
        now=None,
    )
    evidence["pr_json_checksum"] = checksum_json(pr_evidence)
    if path is not None:
        evidence["path"] = str(Path(path).expanduser().resolve(strict=False))
    blockers: list[dict[str, Any]] = []
    for item in waiting:
        blocker = dict(item)
        if blocker.get("code") == "pr_evidence_stale":
            blocker["message"] = "saved PR evidence is stale; refresh PR JSON before Git/PR materialization"
        elif blocker.get("code") == "pr_evidence_from_future":
            blocker["message"] = "PR evidence timestamp is in the future; refresh evidence before Git/PR materialization"
        blockers.append(blocker)
    return evidence, blockers


def _is_dirty_pr_materialization_plan(plan_packet: Any) -> bool:
    return (
        isinstance(plan_packet, dict)
        and plan_packet.get("schema_version") == GIT_PR_DIRTY_MATERIALIZATION_PLAN_SCHEMA_VERSION
        and plan_packet.get("packet") == "git_pr_dirty_materialization_plan"
    )


def _dirty_commit_materialization_summary(
    dirty_packet: Any,
    *,
    path: str | Path | None,
) -> dict[str, Any] | None:
    if dirty_packet is None:
        return None
    summary: dict[str, Any] = {}
    if path is not None:
        summary["path"] = str(Path(path).expanduser().resolve(strict=False))
    if isinstance(dirty_packet, dict):
        summary.update(
            {
                "source": "git_pr_dirty_commit_materialization",
                "checksum": checksum_json(dirty_packet),
                "plan_checksum": dirty_packet.get("plan_checksum"),
                "target_checksum": dirty_packet.get("target_checksum"),
                "proposed_branch": dirty_packet.get("proposed_branch"),
                "created_commit": dirty_packet.get("created_commit"),
                "source_head": dirty_packet.get("source_head"),
                "source_branch": dirty_packet.get("source_branch"),
                "approval_state": dirty_packet.get("approval_state"),
                "execution_authority": dirty_packet.get("execution_authority"),
            }
        )
        files = dirty_packet.get("materialized_files")
        if isinstance(files, list):
            summary["materialized_files"] = [str(path).replace("\\", "/") for path in files if _non_empty_string(path)]
    return summary or None


def _dirty_commit_materialization_source_evidence(
    *,
    cwd: Path,
    plan_packet: Any,
    dirty_packet: Any,
    dirty_packet_path: str | Path | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a dirty local commit result before PR materialization writes."""
    summary = _dirty_commit_materialization_summary(dirty_packet, path=dirty_packet_path)
    blockers: list[dict[str, Any]] = []
    command_trace: list[dict[str, Any]] = []
    if dirty_packet is None:
        return None, [], []
    if not isinstance(dirty_packet, dict):
        return summary, [
            _issue(
                "dirty_commit_materialization_invalid",
                "dirty commit materialization evidence must be a JSON object",
            )
        ], command_trace
    if dirty_packet.get("schema_version") != GIT_PR_DIRTY_COMMIT_MATERIALIZATION_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "dirty_commit_materialization_schema_invalid",
                "dirty commit materialization schema_version must be git-pr-dirty-commit-materialization.v1",
            )
        )
    if dirty_packet.get("packet") != "git_pr_dirty_commit_materialization":
        blockers.append(
            _issue(
                "dirty_commit_materialization_packet_invalid",
                "dirty commit materialization packet must be git_pr_dirty_commit_materialization",
            )
        )
    if dirty_packet.get("valid") is not True or dirty_packet.get("decision") != "materialized":
        blockers.append(
            _issue(
                "dirty_commit_materialization_not_materialized",
                "dirty commit materialization evidence must be a valid materialized result",
            )
        )
    if dirty_packet.get("approval_state") != "approved":
        blockers.append(
            _issue(
                "dirty_commit_materialization_not_approved",
                "dirty commit materialization evidence must have approved operator state",
            )
        )
    if dirty_packet.get("execution_authority") != "operator_approved_dirty_commit_materialization":
        blockers.append(
            _issue(
                "dirty_commit_materialization_authority_invalid",
                "dirty commit materialization evidence must be operator-approved dirty commit authority",
            )
        )
    if dirty_packet.get("plan_checksum") != checksum_json(plan_packet):
        blockers.append(
            _issue(
                "dirty_commit_materialization_plan_checksum_mismatch",
                "dirty commit materialization result must reference the approved dirty materialization plan",
                expected=checksum_json(plan_packet),
                actual=dirty_packet.get("plan_checksum"),
            )
        )
    if isinstance(plan_packet, dict) and dirty_packet.get("target_checksum") != plan_packet.get("target_checksum"):
        blockers.append(
            _issue(
                "dirty_commit_materialization_target_checksum_mismatch",
                "dirty commit materialization target checksum does not match the approved dirty plan",
                expected=plan_packet.get("target_checksum"),
                actual=dirty_packet.get("target_checksum"),
            )
        )

    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_commit = plan_packet.get("proposed_commit") if isinstance(plan_packet, dict) else {}
    if not isinstance(proposed_commit, dict):
        proposed_commit = {}
    if dirty_packet.get("proposed_branch") != proposed_branch:
        blockers.append(
            _issue(
                "dirty_commit_materialization_branch_mismatch",
                "dirty commit materialization branch does not match the approved dirty plan",
                expected=proposed_branch,
                actual=dirty_packet.get("proposed_branch"),
            )
        )
    if dirty_packet.get("source_head") != proposed_commit.get("source_head"):
        blockers.append(
            _issue(
                "dirty_commit_materialization_parent_mismatch",
                "dirty commit materialization parent does not match the approved dirty plan",
                expected=proposed_commit.get("source_head"),
                actual=dirty_packet.get("source_head"),
            )
        )

    planned_files, planned_file_blockers = _safe_materialization_files(proposed_commit.get("files"))
    materialized_files, materialized_file_blockers = _safe_materialization_files(dirty_packet.get("materialized_files"))
    blockers.extend(planned_file_blockers)
    blockers.extend(materialized_file_blockers)
    if planned_files and materialized_files and planned_files != materialized_files:
        blockers.append(
            _issue(
                "dirty_commit_materialization_files_mismatch",
                "dirty commit materialization file set does not match the approved dirty plan",
                expected_files=planned_files,
                actual_files=materialized_files,
            )
        )

    dirty_repo = dirty_packet.get("repository") if isinstance(dirty_packet.get("repository"), dict) else {}
    if _non_empty_string(dirty_repo.get("repository_path")) and not _same_resolved_path(dirty_repo.get("repository_path"), cwd):
        blockers.append(
            _issue(
                "dirty_commit_materialization_repository_mismatch",
                "dirty commit materialization repository path does not match current repository",
                expected=str(cwd),
                actual=dirty_repo.get("repository_path"),
            )
        )

    created_commit = dirty_packet.get("created_commit")
    source_head = dirty_packet.get("source_head")
    if not _non_empty_string(created_commit):
        blockers.append(_issue("dirty_commit_materialization_created_commit_missing", "dirty commit materialization must name the created commit"))
    if not _non_empty_string(source_head):
        blockers.append(_issue("dirty_commit_materialization_source_head_missing", "dirty commit materialization must name the source parent"))
    if not _non_empty_string(proposed_branch):
        blockers.append(_issue("dirty_commit_materialization_branch_missing", "dirty commit materialization requires a proposed branch"))

    if _non_empty_string(created_commit) and _non_empty_string(proposed_branch):
        branch_argv = ["git", "--no-optional-locks", "rev-parse", "--verify", f"refs/heads/{proposed_branch}^{{commit}}"]
        branch_result = _run_process(cwd, branch_argv)
        command_trace.append(_materialization_command_trace(label="preflight_dirty_commit_branch", argv=branch_argv, result=branch_result))
        if branch_result.returncode != 0:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_branch_missing",
                    "dirty commit materialization branch does not resolve locally",
                    proposed_branch=proposed_branch,
                    stderr=branch_result.stderr.strip(),
                )
            )
        elif branch_result.stdout.strip() != created_commit:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_branch_head_mismatch",
                    "dirty commit materialization branch head changed before PR materialization",
                    expected=created_commit,
                    actual=branch_result.stdout.strip(),
                )
            )

    if _non_empty_string(created_commit) and _non_empty_string(source_head):
        parent_argv = ["git", "--no-optional-locks", "rev-parse", "--verify", f"{created_commit}^"]
        parent_result = _run_process(cwd, parent_argv)
        command_trace.append(_materialization_command_trace(label="preflight_dirty_commit_parent", argv=parent_argv, result=parent_result))
        if parent_result.returncode != 0:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_parent_unreadable",
                    "could not inspect dirty commit materialization parent",
                    stderr=parent_result.stderr.strip(),
                )
            )
        elif parent_result.stdout.strip() != source_head:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_parent_mismatch",
                    "dirty commit materialization commit parent changed before PR materialization",
                    expected=source_head,
                    actual=parent_result.stdout.strip(),
                )
            )

        diff_argv = ["git", "--no-optional-locks", "diff", "--name-only", source_head, created_commit, "--"]
        diff_result = _run_process(cwd, diff_argv)
        command_trace.append(_materialization_command_trace(label="preflight_dirty_commit_files", argv=diff_argv, result=diff_result))
        if diff_result.returncode != 0:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_files_unreadable",
                    "could not inspect dirty commit materialization file set",
                    stderr=diff_result.stderr.strip(),
                )
            )
        else:
            committed_files = sorted(line.strip().replace("\\", "/") for line in diff_result.stdout.splitlines() if line.strip())
            if planned_files and committed_files != planned_files:
                blockers.append(
                    _issue(
                        "dirty_commit_materialization_committed_files_mismatch",
                        "dirty commit materialization committed file set does not match the approved dirty plan",
                        expected_files=planned_files,
                        actual_files=committed_files,
                    )
                )

        message_argv = ["git", "--no-optional-locks", "log", "-1", "--format=%B", created_commit]
        message_result = _run_process(cwd, message_argv)
        command_trace.append(_materialization_command_trace(label="preflight_dirty_commit_message", argv=message_argv, result=message_result))
        expected_message = proposed_commit.get("message")
        actual_message = message_result.stdout.replace("\r\n", "\n").rstrip("\n")
        if message_result.returncode != 0:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_message_unreadable",
                    "could not inspect dirty commit materialization message",
                    stderr=message_result.stderr.strip(),
                )
            )
        elif actual_message != expected_message:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_message_mismatch",
                    "dirty commit materialization commit message does not match the approved dirty plan",
                    expected=expected_message,
                    actual=actual_message,
                )
            )

    status_argv = ["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]
    status_result = _run_process(cwd, status_argv)
    command_trace.append(_materialization_command_trace(label="preflight_dirty_commit_worktree", argv=status_argv, result=status_result))
    if status_result.returncode != 0:
        blockers.append(
            _issue(
                "dirty_commit_materialization_worktree_unreadable",
                "could not inspect worktree before dirty PR materialization",
                stderr=status_result.stderr.strip(),
            )
        )
    elif status_result.stdout.strip():
        blockers.append(
            _issue(
                "dirty_commit_materialization_worktree_dirty",
                "worktree must be clean before dirty PR materialization",
                dirty_status=status_result.stdout.splitlines(),
            )
        )

    if isinstance(summary, dict) and isinstance(dirty_repo, dict):
        repository = dict(dirty_repo)
        if _non_empty_string(created_commit):
            repository["current_head"] = created_commit
        if _non_empty_string(proposed_branch):
            repository["current_branch"] = proposed_branch
        repository["worktree_clean"] = status_result.returncode == 0 and not status_result.stdout.strip()
        summary["repository"] = repository
    return summary, blockers, command_trace


def _dirty_pr_materialization_target_blockers(
    *,
    plan_packet: Any,
    remote: str,
    remote_url: str | None,
    pr_number: str | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    target = plan_packet.get("target") if isinstance(plan_packet, dict) and isinstance(plan_packet.get("target"), dict) else {}
    if not target:
        return blockers
    target_pr_number = str(target.get("pr_number")) if target.get("pr_number") is not None else None
    selected_pr_number = str(pr_number) if pr_number is not None else None
    target_operation = "update_pull_request" if target_pr_number is not None else "create_pull_request"
    selected_operation = "update_pull_request" if selected_pr_number is not None else "create_pull_request"
    if target.get("remote") != remote:
        blockers.append(
            _issue(
                "dirty_pr_materialization_target_remote_mismatch",
                "selected remote does not match the approved dirty PR materialization target",
                expected=target.get("remote"),
                actual=remote,
            )
        )
    if target.get("remote_url") != remote_url:
        blockers.append(
            _issue(
                "dirty_pr_materialization_target_remote_url_mismatch",
                "resolved remote push URL does not match the approved dirty PR materialization target",
                expected=target.get("remote_url"),
                actual=remote_url,
            )
        )
    if target_pr_number != selected_pr_number or target_operation != selected_operation:
        blockers.append(
            _issue(
                "dirty_pr_materialization_target_pr_number_mismatch",
                "selected PR create/update target does not match the approved dirty PR materialization target",
                expected_pr_number=target_pr_number,
                actual_pr_number=selected_pr_number,
                expected_operation=target_operation,
                actual_operation=selected_operation,
            )
        )
    return blockers


def _plan_structural_blockers(plan_packet: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(plan_packet, dict):
        return [_issue("git_pr_plan_invalid", "plan packet must be a JSON object")]
    if plan_packet.get("schema_version") != GIT_PR_PLAN_SCHEMA_VERSION:
        blockers.append(_issue("git_pr_plan_schema_invalid", "plan packet schema_version must be git-pr-plan.v1"))
    if plan_packet.get("packet") != "git_pr_plan":
        blockers.append(_issue("git_pr_plan_packet_invalid", "plan packet must be git_pr_plan"))
    if plan_packet.get("ready_to_review") is not True or plan_packet.get("decision") != "ready":
        blockers.append(_issue("git_pr_plan_not_ready", "plan packet is not ready for materialization"))
    if plan_packet.get("dry_run") is not True:
        blockers.append(_issue("git_pr_plan_not_dry_run", "plan packet must be a reviewed dry-run plan"))
    if plan_packet.get("operator_confirmation_required") is not True:
        blockers.append(
            _issue("git_pr_plan_operator_confirmation_missing", "plan packet must require operator confirmation")
        )
    if plan_packet.get("side_effects") != []:
        blockers.append(_issue("git_pr_plan_side_effects_present", "plan packet must not contain side effects"))
    if plan_packet.get("approval_state") != "not_approved":
        blockers.append(_issue("git_pr_plan_approval_state_invalid", "plan packet must start as not_approved"))
    if plan_packet.get("execution_authority") != "none":
        blockers.append(_issue("git_pr_plan_execution_authority_invalid", "plan packet must not grant authority"))
    if not _non_empty_string(plan_packet.get("proposed_branch")):
        blockers.append(_issue("git_pr_plan_proposed_branch_missing", "plan packet must include proposed_branch"))
    if not _non_empty_string(plan_packet.get("proposed_pr_title")):
        blockers.append(_issue("git_pr_plan_proposed_pr_title_missing", "plan packet must include proposed_pr_title"))
    if not _non_empty_string(plan_packet.get("proposed_pr_body")):
        blockers.append(_issue("git_pr_plan_proposed_pr_body_missing", "plan packet must include proposed_pr_body"))
    return blockers


def validate_git_pr_plan_dry_run_packet(plan_packet: Any) -> list[dict[str, Any]]:
    """Validate that a saved Git/PR plan is review-ready and non-authorizing."""
    return _plan_structural_blockers(plan_packet)


def _stale_plan_blockers(plan_packet: dict[str, Any], rechecked_plan: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    plan_repo = plan_packet.get("repository") if isinstance(plan_packet.get("repository"), dict) else {}
    rechecked_repo = rechecked_plan.get("repository") if isinstance(rechecked_plan.get("repository"), dict) else {}
    compared_fields = ("current_branch", "current_head", "base_branch", "base_head", "worktree_clean")
    changed = {
        field: {"planned": plan_repo.get(field), "current": rechecked_repo.get(field)}
        for field in compared_fields
        if plan_repo.get(field) != rechecked_repo.get(field)
    }
    if changed:
        blockers.append(
            _issue(
                "stale_git_pr_plan",
                "current Git state no longer matches the approved git-pr-plan packet",
                changed=changed,
            )
        )
    return blockers


def _changed_plan_blockers(plan_packet: dict[str, Any], rechecked_plan: dict[str, Any]) -> list[dict[str, Any]]:
    changed = {
        field: {"planned": plan_packet.get(field), "current": rechecked_plan.get(field)}
        for field in ("proposed_branch", "proposed_commit_message", "proposed_pr_title", "proposed_pr_body")
        if plan_packet.get(field) != rechecked_plan.get(field)
    }
    if not changed:
        return []
    return [
        _issue(
            "git_pr_plan_recheck_changed",
            "rechecked Git/PR plan differs from the approved packet",
            changed=changed,
        )
    ]


def _append_materialization_audit(root: Path, record: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        return append_audit_record(root, record), None
    except OSError as exc:
        return None, _issue("audit_write_failed", f"could not write materialization audit record: {exc}")


def _dirty_commit_filter_attribute_preflight(
    cwd: Path,
    materialized_files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Block Git clean/process filters before staging planned dirty files."""
    argv = ["git", "--no-optional-locks", "check-attr", "-z", "--stdin", "filter"]
    input_text = "\0".join(materialized_files) + "\0"
    result = _run_process(cwd, argv, input_text=input_text)
    trace = [_materialization_command_trace(label="preflight_filter_attributes", argv=argv, result=result)]
    if result.returncode != 0:
        return trace, [
            _issue(
                "git_pr_dirty_commit_filter_attribute_check_failed",
                "could not inspect Git filter attributes before dirty commit materialization",
                stderr=result.stderr.strip(),
            )
        ]
    planned_filters: list[dict[str, str]] = []
    fields = result.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    for index in range(0, len(fields) - 2, 3):
        path, _attribute, value = fields[index : index + 3]
        filter_value = value.strip()
        if filter_value and filter_value not in {"unspecified", "unset"}:
            planned_filters.append({"path": path.strip().replace("\\", "/"), "filter": filter_value})
    active_filters: list[dict[str, Any]] = []
    config_blockers: list[dict[str, Any]] = []
    configured_steps_by_filter: dict[str, list[str]] = {}
    for planned_filter in planned_filters:
        filter_name = planned_filter["filter"]
        configured_steps = configured_steps_by_filter.get(filter_name)
        if configured_steps is None:
            configured_steps = []
            for step in ("clean", "process"):
                config_argv = ["git", "--no-optional-locks", "config", "--get-all", f"filter.{filter_name}.{step}"]
                config_result = _run_process(cwd, config_argv)
                trace.append(
                    _materialization_command_trace(
                        label=f"preflight_filter_{step}_config",
                        argv=config_argv,
                        result=config_result,
                    )
                )
                if config_result.returncode == 0 and config_result.stdout.strip():
                    configured_steps.append(step)
                elif config_result.returncode not in (0, 1):
                    config_blockers.append(
                        _issue(
                            "git_pr_dirty_commit_filter_config_check_failed",
                            "could not inspect Git filter driver configuration before dirty commit materialization",
                            filter=filter_name,
                            step=step,
                            stderr=config_result.stderr.strip(),
                        )
                    )
            configured_steps_by_filter[filter_name] = configured_steps
        if configured_steps:
            active_filters.append({**planned_filter, "configured_steps": configured_steps})
    if config_blockers:
        return trace, config_blockers
    if active_filters:
        return trace, [
            _issue(
                "git_pr_dirty_commit_filter_attribute_present",
                "planned dirty materialization files must not use Git clean/process filter drivers",
                active_filters=active_filters,
            )
        ]
    return trace, []


def _dirty_commit_materialization_recommendation(
    blockers: list[dict[str, Any]],
    *,
    side_effects_started: bool,
) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if not blockers:
        return "inspect_local_commit"
    if "operator_approval_missing" in codes or "operator_approval_mismatch" in codes:
        return "provide_operator_approval"
    if (
        "stale_git_pr_dirty_materialization_plan" in codes
        or "git_pr_dirty_materialization_plan_recheck_changed" in codes
        or "git_pr_dirty_materialization_plan_recheck_blocked" in codes
    ):
        return "refresh_dirty_git_pr_materialization_plan"
    if side_effects_started:
        return "inspect_git_pr_dirty_commit_materialization"
    return "address_blockers"


def _dirty_commit_materialization_packet(
    *,
    valid: bool,
    decision: str,
    approval_state: str,
    plan_file: Path,
    plan_checksum: str | None,
    target_checksum: str | None,
    repository: dict[str, Any] | None,
    source_branch: str | None,
    source_head: str | None,
    proposed_branch: str | None,
    created_commit: str | None,
    materialized_files: list[str],
    intended_side_effects: list[str],
    side_effects: list[str],
    command_trace: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    side_effects_started = bool(side_effects)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GIT_PR_DIRTY_COMMIT_MATERIALIZATION_SCHEMA_VERSION,
        "packet": "git_pr_dirty_commit_materialization",
        "generated_at": utc_now(),
        "valid": valid,
        "decision": decision,
        "recommended_next_action": _dirty_commit_materialization_recommendation(
            blockers,
            side_effects_started=side_effects_started,
        ),
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": approval_state,
        "execution_authority": (
            "operator_approved_dirty_commit_materialization" if approval_state == "approved" else "none"
        ),
        "merge_readiness": "not_evaluated",
        "plan_file": str(plan_file),
        "plan_checksum": plan_checksum,
        "target_checksum": target_checksum,
        "repository": repository or {},
        "source_branch": source_branch,
        "source_head": source_head,
        "proposed_branch": proposed_branch,
        "created_commit": created_commit,
        "materialized_files": materialized_files,
        "intended_side_effects": intended_side_effects,
        "side_effects": side_effects,
        "command_trace": command_trace,
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "operator_approved_dirty_commit_materialization_only",
            "does_not_push",
            "does_not_create_pull_request",
            "does_not_call_github",
            "does_not_auto_merge",
            "does_not_release",
            "does_not_publish_packages",
            "does_not_invoke_executor",
        ],
    }


def _dirty_commit_plan_structural_blockers(plan_packet: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(plan_packet, dict):
        return [_issue("git_pr_dirty_materialization_plan_invalid", "plan packet must be a JSON object")]
    if plan_packet.get("schema_version") != GIT_PR_DIRTY_MATERIALIZATION_PLAN_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "git_pr_dirty_materialization_plan_schema_invalid",
                "plan packet schema_version must be git-pr-dirty-materialization-plan.v1",
            )
        )
    if plan_packet.get("packet") != "git_pr_dirty_materialization_plan":
        blockers.append(
            _issue("git_pr_dirty_materialization_plan_packet_invalid", "plan packet must be git_pr_dirty_materialization_plan")
        )
    if plan_packet.get("ready_to_review") is not True or plan_packet.get("decision") != "ready" or plan_packet.get("valid") is not True:
        blockers.append(_issue("git_pr_dirty_materialization_plan_not_ready", "dirty materialization plan is not ready"))
    if plan_packet.get("dry_run") is not True:
        blockers.append(_issue("git_pr_dirty_materialization_plan_not_dry_run", "dirty materialization plan must be dry-run"))
    if plan_packet.get("operator_confirmation_required") is not True:
        blockers.append(
            _issue(
                "git_pr_dirty_materialization_plan_operator_confirmation_missing",
                "dirty materialization plan must require operator confirmation",
            )
        )
    if plan_packet.get("side_effects") != []:
        blockers.append(
            _issue("git_pr_dirty_materialization_plan_side_effects_present", "dirty materialization plan must not contain side effects")
        )
    if plan_packet.get("approval_state") != "not_approved":
        blockers.append(
            _issue("git_pr_dirty_materialization_plan_approval_state_invalid", "dirty materialization plan must start as not_approved")
        )
    if plan_packet.get("execution_authority") != "none":
        blockers.append(
            _issue("git_pr_dirty_materialization_plan_execution_authority_invalid", "dirty materialization plan must not grant authority")
        )
    target = plan_packet.get("target") if isinstance(plan_packet.get("target"), dict) else None
    if target is None:
        blockers.append(_issue("git_pr_dirty_materialization_target_missing", "dirty materialization plan target is required"))
    else:
        if plan_packet.get("target_checksum") != checksum_json(target):
            blockers.append(
                _issue(
                    "git_pr_dirty_materialization_target_checksum_mismatch",
                    "dirty materialization target checksum does not match target payload",
                )
            )
        if target.get("operation") != "dirty_worktree_git_pr_materialization":
            blockers.append(
                _issue(
                    "git_pr_dirty_materialization_target_operation_invalid",
                    "dirty materialization target operation must be dirty_worktree_git_pr_materialization",
                )
            )
        if target.get("proposed_branch") != plan_packet.get("proposed_branch"):
            blockers.append(
                _issue(
                    "git_pr_dirty_materialization_target_branch_mismatch",
                    "target proposed branch must match plan proposed branch",
                )
            )
    proposed_commit = plan_packet.get("proposed_commit") if isinstance(plan_packet.get("proposed_commit"), dict) else {}
    if not _non_empty_string(plan_packet.get("proposed_branch")):
        blockers.append(_issue("git_pr_dirty_materialization_proposed_branch_missing", "proposed_branch is required"))
    commit_message = proposed_commit.get("message")
    if not _non_empty_string(commit_message):
        blockers.append(_issue("git_pr_dirty_materialization_commit_message_missing", "proposed commit message is required"))
    elif "\n" in str(commit_message) or "\r" in str(commit_message):
        blockers.append(
            _issue(
                "git_pr_dirty_materialization_commit_message_invalid",
                "proposed commit message must be a single line",
            )
        )
    if not _non_empty_string(proposed_commit.get("source_head")):
        blockers.append(_issue("git_pr_dirty_materialization_source_head_missing", "proposed commit source_head is required"))
    files = proposed_commit.get("files")
    if not isinstance(files, list) or not files or any(not _non_empty_string(path) for path in files):
        blockers.append(
            _issue(
                "git_pr_dirty_materialization_files_invalid",
                "proposed commit files must be a non-empty list of strings",
            )
        )
    elif target is not None and target.get("materialized_change_files") != files:
        blockers.append(
            _issue(
                "git_pr_dirty_materialization_target_files_mismatch",
                "target materialized files must match proposed commit files",
            )
        )
    return blockers


def validate_git_pr_dirty_materialization_plan_packet(plan_packet: Any) -> list[dict[str, Any]]:
    """Validate that a dirty materialization plan is review-ready and non-authorizing."""
    return _dirty_commit_plan_structural_blockers(plan_packet)


def _safe_materialization_files(files: Any) -> tuple[list[str], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(files, list) or not files:
        return [], [_issue("git_pr_dirty_materialization_files_invalid", "materialized files are required")]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in files:
        if not _non_empty_string(value):
            blockers.append(_issue("git_pr_dirty_materialization_files_invalid", "materialized file paths must be strings"))
            continue
        path = str(value).replace("\\", "/").strip()
        parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or "\0" in path
            or ":" in path
            or any(part in {"", ".", ".."} for part in parts)
        ):
            blockers.append(
                _issue(
                    "git_pr_dirty_materialization_files_invalid",
                    f"materialized file path is not a safe repo-relative path: {value}",
                )
            )
            continue
        if path not in seen:
            normalized.append(path)
            seen.add(path)
    return normalized, blockers


def _dirty_stale_plan_blockers(plan_packet: dict[str, Any], rechecked_plan: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    plan_repo = plan_packet.get("repository") if isinstance(plan_packet.get("repository"), dict) else {}
    rechecked_repo = rechecked_plan.get("repository") if isinstance(rechecked_plan.get("repository"), dict) else {}
    compared_fields = (
        "repository_path",
        "current_branch",
        "current_head",
        "base_branch",
        "base_head",
        "worktree_clean",
        "dirty_paths",
    )
    changed = {
        field: {"planned": plan_repo.get(field), "current": rechecked_repo.get(field)}
        for field in compared_fields
        if plan_repo.get(field) != rechecked_repo.get(field)
    }
    if changed:
        blockers.append(
            _issue(
                "stale_git_pr_dirty_materialization_plan",
                "current Git state no longer matches the approved dirty materialization plan",
                changed=changed,
            )
        )
    return blockers


def _dirty_changed_plan_blockers(plan_packet: dict[str, Any], rechecked_plan: dict[str, Any]) -> list[dict[str, Any]]:
    compared_fields = (
        "proposed_branch",
        "proposed_pr_title",
        "proposed_pr_body",
        "target_checksum",
        "dirty_worktree_fingerprint",
    )
    changed = {
        field: {"planned": plan_packet.get(field), "current": rechecked_plan.get(field)}
        for field in compared_fields
        if plan_packet.get(field) != rechecked_plan.get(field)
    }
    plan_commit = plan_packet.get("proposed_commit") if isinstance(plan_packet.get("proposed_commit"), dict) else {}
    rechecked_commit = rechecked_plan.get("proposed_commit") if isinstance(rechecked_plan.get("proposed_commit"), dict) else {}
    for field in ("message", "files", "source_head", "base_branch", "base_head", "dirty_worktree_fingerprint_checksum"):
        if plan_commit.get(field) != rechecked_commit.get(field):
            changed[f"proposed_commit.{field}"] = {"planned": plan_commit.get(field), "current": rechecked_commit.get(field)}
    if not changed:
        return []
    return [
        _issue(
            "git_pr_dirty_materialization_plan_recheck_changed",
            "rechecked dirty materialization plan differs from the approved packet",
            changed=changed,
        )
    ]


def git_pr_dirty_commit_materialization_load_error_packet(plan_file: str | Path, error: Exception) -> dict[str, Any]:
    """Build a stable blocker packet when a dirty materialization plan file cannot be loaded."""
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    return _dirty_commit_materialization_packet(
        valid=False,
        decision="blocked",
        approval_state="not_approved",
        plan_file=plan_path,
        plan_checksum=None,
        target_checksum=None,
        repository={},
        source_branch=None,
        source_head=None,
        proposed_branch=None,
        created_commit=None,
        materialized_files=[],
        intended_side_effects=[],
        side_effects=[],
        command_trace=[],
        blockers=[
            _issue(
                "git_pr_dirty_materialization_plan_unreadable",
                f"could not read dirty materialization plan packet: {error}",
            )
        ],
        warnings=[],
    )


def materialize_dirty_commit_plan(
    *,
    cwd: str | Path,
    plan_packet: Any,
    plan_file: str | Path,
    approval_token: str | None,
    runtime_root: str | Path,
) -> dict[str, Any]:
    """Materialize an approved dirty-worktree plan into one local Git branch commit."""
    repo_cwd = Path(cwd).expanduser().resolve()
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    runtime_path = Path(runtime_root).expanduser().resolve()
    plan_checksum = checksum_json(plan_packet)
    target = plan_packet.get("target") if isinstance(plan_packet, dict) and isinstance(plan_packet.get("target"), dict) else {}
    target_checksum = plan_packet.get("target_checksum") if isinstance(plan_packet, dict) else None
    repository = plan_packet.get("repository") if isinstance(plan_packet, dict) and isinstance(plan_packet.get("repository"), dict) else {}
    proposed_commit = (
        plan_packet.get("proposed_commit")
        if isinstance(plan_packet, dict) and isinstance(plan_packet.get("proposed_commit"), dict)
        else {}
    )
    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_branch = proposed_branch if isinstance(proposed_branch, str) else None
    source_branch = repository.get("current_branch") if isinstance(repository.get("current_branch"), str) else None
    source_head = proposed_commit.get("source_head") if isinstance(proposed_commit.get("source_head"), str) else None
    commit_message = proposed_commit.get("message") if isinstance(proposed_commit.get("message"), str) else None
    materialized_files, file_blockers = _safe_materialization_files(proposed_commit.get("files"))
    intended_side_effects = [
        "snapshot_index_tree",
        "create_branch",
        "checkout_branch",
        "stage_planned_files",
        "create_commit",
    ]
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    command_trace: list[dict[str, Any]] = []
    side_effects: list[str] = []
    created_commit = None

    blockers.extend(_dirty_commit_plan_structural_blockers(plan_packet))
    blockers.extend(file_blockers)

    approval_secret = _materialization_approval_secret()
    expected_token = (
        git_pr_dirty_commit_materialization_approval_token(plan_packet, approval_secret=approval_secret)
        if approval_secret is not None
        else None
    )
    if not approval_token:
        approval_state = "not_approved"
        blockers.append(
            _issue(
                "operator_approval_missing",
                "operator approval token is required before dirty commit materialization",
            )
        )
    elif approval_secret is None:
        approval_state = "approval_unresolved"
        blockers.append(
            _issue(
                "operator_approval_secret_missing",
                f"{GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV} is required to verify operator approval",
            )
        )
    elif expected_token is None or not hmac.compare_digest(approval_token, expected_token):
        approval_state = "approval_mismatch"
        blockers.append(
            _issue(
                "operator_approval_mismatch",
                "operator approval token does not match the approved dirty materialization target",
            )
        )
    else:
        approval_state = "approved"

    if blockers:
        return _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
            repository=repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=None,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=[],
            blockers=blockers,
            warnings=warnings,
        )

    provenance = plan_packet.get("evidence_provenance") if isinstance(plan_packet.get("evidence_provenance"), dict) else {}
    task_packet, task_blocker = _load_materialization_json(
        provenance.get("task_file"),
        "task_packet_unreadable",
        "could not read task packet from dirty materialization evidence provenance",
    )
    result_evidence, result_blocker = _load_materialization_json(
        provenance.get("result_file"),
        "result_evidence_unreadable",
        "could not read executor result evidence from dirty materialization evidence provenance",
    )
    real_invocation, invocation_blocker = _load_materialization_json(
        provenance.get("real_invocation_file"),
        "real_invocation_unreadable",
        "could not read real invocation evidence from dirty materialization evidence provenance",
    )
    closeout_packet, closeout_blocker = _load_materialization_json(
        provenance.get("closeout_file"),
        "closeout_unreadable",
        "could not read closeout evidence from dirty materialization evidence provenance",
    )
    for blocker in (task_blocker, result_blocker, invocation_blocker, closeout_blocker):
        if blocker is not None:
            blockers.append(blocker)
    if task_packet is not None and provenance.get("task_file_checksum") != checksum_json(task_packet):
        blockers.append(_issue("task_packet_changed", "task packet checksum no longer matches the approved dirty plan"))
    if result_evidence is not None and provenance.get("result_file_checksum") != checksum_json(result_evidence):
        blockers.append(_issue("result_evidence_changed", "executor result checksum no longer matches the approved dirty plan"))
    if real_invocation is not None and provenance.get("real_invocation_checksum") != checksum_json(real_invocation):
        blockers.append(_issue("real_invocation_changed", "real invocation checksum no longer matches the approved dirty plan"))
    if closeout_packet is not None and provenance.get("closeout_file_checksum") != checksum_json(closeout_packet):
        blockers.append(_issue("closeout_changed", "closeout checksum no longer matches the approved dirty plan"))

    if task_packet is not None and result_evidence is not None and real_invocation is not None and closeout_packet is not None:
        branch_policy = plan_packet.get("branch_policy") if isinstance(plan_packet.get("branch_policy"), dict) else {}
        base_branch = target.get("base_branch") if _non_empty_string(target.get("base_branch")) else repository.get("base_branch")
        if not _non_empty_string(base_branch):
            base_branch = "main"
        expected_base_head = target.get("base_head") if _non_empty_string(target.get("base_head")) else target.get("expected_base_head")
        rechecked_plan = evaluate_dirty_git_pr_materialization_plan(
            cwd=repo_cwd,
            task_packet=task_packet,
            result_evidence=result_evidence,
            real_invocation=real_invocation,
            closeout_packet=closeout_packet,
            task_file=provenance.get("task_file"),
            result_file=provenance.get("result_file"),
            real_invocation_file=provenance.get("real_invocation_file"),
            closeout_file=provenance.get("closeout_file"),
            base_branch=str(base_branch),
            branch_prefix="",
            proposed_branch_override=proposed_branch,
            branch_policy=branch_policy.get("policy_file") if isinstance(branch_policy, dict) else None,
            required_body_sections=_materialization_required_sections(plan_packet),
            remote=target.get("remote") if _non_empty_string(target.get("remote")) else "origin",
            pr_number=target.get("pr_number") if _non_empty_string(target.get("pr_number")) else None,
            expected_base_head=str(expected_base_head) if _non_empty_string(expected_base_head) else None,
        )
        blockers.extend(_dirty_stale_plan_blockers(plan_packet, rechecked_plan))
        blockers.extend(_dirty_changed_plan_blockers(plan_packet, rechecked_plan))
        if not rechecked_plan.get("ready_to_review"):
            blockers.append(
                _issue(
                    "git_pr_dirty_materialization_plan_recheck_blocked",
                    "rechecked dirty materialization plan is blocked immediately before commit materialization",
                    recheck_blockers=rechecked_plan.get("blockers", []),
                )
            )
        repository = rechecked_plan.get("repository") if isinstance(rechecked_plan.get("repository"), dict) else repository

    if blockers:
        return _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
            repository=repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=None,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
        )

    filter_trace, filter_blockers = _dirty_commit_filter_attribute_preflight(repo_cwd, materialized_files)
    command_trace.extend(filter_trace)
    blockers.extend(filter_blockers)
    if blockers:
        return _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
            repository=repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=None,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
        )

    intent_payload = _dirty_commit_materialization_packet(
        valid=True,
        decision="approved",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum,
        repository=repository,
        source_branch=source_branch,
        source_head=source_head,
        proposed_branch=proposed_branch,
        created_commit=None,
        materialized_files=materialized_files,
        intended_side_effects=intended_side_effects,
        side_effects=[],
        command_trace=[],
        blockers=[],
        warnings=warnings,
    )
    _audit_record, audit_blocker = _append_materialization_audit(
        runtime_path,
        git_pr_dirty_commit_materialization_intent_audit_record(intent_payload),
    )
    if audit_blocker is not None:
        return _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum,
            repository=repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=None,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=command_trace,
            blockers=[audit_blocker],
            warnings=warnings,
        )
    side_effects.append("audit_intent_record_appended")
    original_index_tree: str | None = None

    def rollback_after_failed_write() -> list[dict[str, Any]]:
        if "created_branch" not in side_effects:
            return []
        rollback_blockers: list[dict[str, Any]] = []
        rollback_commands = [
            (
                "rollback_reset_to_source_head",
                ["git", "-c", "core.hooksPath=", "reset", "--mixed", "--quiet", str(source_head)],
                "rollback_reset_to_source_head",
            ),
            (
                "rollback_restore_index_tree",
                ["git", "--no-optional-locks", "read-tree", "--reset", str(original_index_tree)],
                "rollback_index_restored",
            ),
            (
                "rollback_switch_source_branch",
                ["git", "-c", "core.hooksPath=", "switch", str(source_branch)],
                "rollback_source_branch_restored",
            ),
            (
                "rollback_delete_generated_branch",
                ["git", "-c", "core.hooksPath=", "branch", "-D", str(proposed_branch)],
                "rollback_generated_branch_deleted",
            ),
        ]
        for label, argv, side_effect in rollback_commands:
            result = _run_process(repo_cwd, argv)
            command_trace.append(_materialization_command_trace(label=label, argv=argv, result=result))
            if result.returncode == 0:
                side_effects.append(side_effect)
                continue
            rollback_blockers.append(
                _issue(
                    "git_pr_dirty_commit_materialization_rollback_failed",
                    f"{label} failed while rolling back dirty commit materialization",
                    command_label=label,
                    returncode=result.returncode,
                    stderr=result.stderr.strip(),
                )
            )
        return rollback_blockers

    def result_packet_after_failure(failure_blockers: list[dict[str, Any]]) -> dict[str, Any]:
        failure_blockers = [*failure_blockers, *rollback_after_failed_write()]
        refreshed_repository = _refresh_materialization_repository(repo_cwd, repository)
        failed_side_effects = [*side_effects, "audit_result_record_appended"]
        failed_packet = _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum,
            repository=refreshed_repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=created_commit,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=failed_side_effects,
            command_trace=command_trace,
            blockers=failure_blockers,
            warnings=warnings,
        )
        _result_audit, result_audit_blocker = _append_materialization_audit(
            runtime_path,
            git_pr_dirty_commit_materialization_result_audit_record(failed_packet),
        )
        if result_audit_blocker is None:
            return failed_packet
        return _dirty_commit_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum,
            repository=refreshed_repository,
            source_branch=source_branch,
            source_head=source_head,
            proposed_branch=proposed_branch,
            created_commit=created_commit,
            materialized_files=materialized_files,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            blockers=failure_blockers,
            warnings=[*warnings, result_audit_blocker],
        )

    index_snapshot_argv = ["git", "--no-optional-locks", "write-tree"]
    index_snapshot = _run_process(repo_cwd, index_snapshot_argv)
    command_trace.append(
        _materialization_command_trace(
            label="snapshot_index_tree",
            argv=index_snapshot_argv,
            result=index_snapshot,
        )
    )
    if index_snapshot.returncode != 0 or not index_snapshot.stdout.strip():
        blockers.append(
            _issue(
                "git_pr_dirty_commit_index_snapshot_failed",
                "could not snapshot the Git index before dirty commit materialization",
                stderr=index_snapshot.stderr.strip(),
            )
        )
        return result_packet_after_failure(blockers)
    original_index_tree = index_snapshot.stdout.strip()
    side_effects.append("index_tree_snapshotted")

    commands = [
        (
            "create_and_switch_branch",
            ["git", "-c", "core.hooksPath=", "switch", "-c", str(proposed_branch), str(source_head)],
            ["created_branch", "checked_out_branch"],
        ),
        ("stage_planned_files", ["git", "-c", "core.hooksPath=", "add", "--", *materialized_files], ["staged_files"]),
        (
            "create_commit",
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "--no-verify",
                "--no-gpg-sign",
                "-m",
                str(commit_message),
            ],
            ["created_commit"],
        ),
    ]
    for label, argv, command_side_effects in commands:
        result = _run_process(repo_cwd, argv)
        command_trace.append(_materialization_command_trace(label=label, argv=argv, result=result))
        if result.returncode != 0:
            blockers.append(
                _issue(
                    "git_pr_dirty_commit_materialization_command_failed",
                    f"{label} failed during approved dirty commit materialization",
                    command_label=label,
                    returncode=result.returncode,
                    stderr=result.stderr.strip(),
                )
            )
            return result_packet_after_failure(blockers)
        side_effects.extend(command_side_effects)
        if label == "stage_planned_files":
            staged = _run_git(repo_cwd, ["diff", "--cached", "--name-only"], optional_locks=False)
            staged_files = sorted(line.strip().replace("\\", "/") for line in staged.stdout.splitlines() if line.strip())
            if staged.returncode != 0 or staged_files != materialized_files:
                blockers.append(
                    _issue(
                        "staged_files_mismatch",
                        "staged files must exactly match the approved dirty materialization plan",
                        expected=materialized_files,
                        actual=staged_files,
                        stderr=staged.stderr.strip(),
                    )
                )
                return result_packet_after_failure(blockers)
        if label == "create_commit":
            created_commit, error = _git_stdout(repo_cwd, ["rev-parse", "--verify", "HEAD^{commit}"])
            if created_commit is None:
                blockers.append(
                    _issue(
                        "created_commit_unresolved",
                        "could not resolve the dirty materialization commit after git commit",
                        detail=error,
                    )
                )
                return result_packet_after_failure(blockers)
            parent_head, parent_error = _git_stdout(repo_cwd, ["rev-parse", "--verify", "HEAD^"])
            commit_body_result = _run_git(repo_cwd, ["log", "-1", "--format=%B"], optional_locks=False)
            commit_body = commit_body_result.stdout.replace("\r\n", "\n").rstrip("\n")
            committed_files_result = _run_git(
                repo_cwd,
                ["diff", "--name-only", "HEAD^", "HEAD"],
                optional_locks=False,
            )
            committed_files = sorted(
                line.strip().replace("\\", "/")
                for line in committed_files_result.stdout.splitlines()
                if line.strip()
            )
            mismatches = {
                key: {"expected": expected, "actual": actual}
                for key, expected, actual in (
                    ("parent_head", source_head, parent_head),
                    ("commit_message", commit_message, commit_body),
                    ("committed_files", materialized_files, committed_files),
                )
                if expected != actual
            }
            if committed_files_result.returncode != 0:
                mismatches["committed_files"] = {
                    "expected": materialized_files,
                    "actual": None,
                    "stderr": committed_files_result.stderr.strip(),
                }
            if parent_head is None:
                mismatches["parent_head"] = {"expected": source_head, "actual": None, "stderr": parent_error}
            if commit_body_result.returncode != 0:
                mismatches["commit_message"] = {
                    "expected": commit_message,
                    "actual": None,
                    "stderr": commit_body_result.stderr.strip(),
                }
            if mismatches:
                blockers.append(
                    _issue(
                        "created_commit_mismatch",
                        "created commit does not match the approved dirty materialization target",
                        mismatches=mismatches,
                    )
                )
                return result_packet_after_failure(blockers)

    repository = _refresh_materialization_repository(repo_cwd, repository)
    success_side_effects = [*side_effects, "audit_result_record_appended"]
    success_packet = _dirty_commit_materialization_packet(
        valid=True,
        decision="materialized",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum,
        repository=repository,
        source_branch=source_branch,
        source_head=source_head,
        proposed_branch=proposed_branch,
        created_commit=created_commit,
        materialized_files=materialized_files,
        intended_side_effects=intended_side_effects,
        side_effects=success_side_effects,
        command_trace=command_trace,
        blockers=[],
        warnings=warnings,
    )
    _result_audit, result_audit_blocker = _append_materialization_audit(
        runtime_path,
        git_pr_dirty_commit_materialization_result_audit_record(success_packet),
    )
    if result_audit_blocker is None:
        return success_packet
    return _dirty_commit_materialization_packet(
        valid=False,
        decision="blocked",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum,
        repository=repository,
        source_branch=source_branch,
        source_head=source_head,
        proposed_branch=proposed_branch,
        created_commit=created_commit,
        materialized_files=materialized_files,
        intended_side_effects=intended_side_effects,
        side_effects=side_effects,
        command_trace=command_trace,
        blockers=[result_audit_blocker],
        warnings=warnings,
    )


def git_pr_materialization_load_error_packet(plan_file: str | Path, error: Exception) -> dict[str, Any]:
    """Build a stable blocker packet when the plan file cannot be loaded."""
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    return _materialization_packet(
        valid=False,
        decision="blocked",
        approval_state="not_approved",
        plan_file=plan_path,
        plan_checksum=None,
        repository={},
        proposed_branch=None,
        proposed_pr_title=None,
        pr_url=None,
        intended_side_effects=[],
        side_effects=[],
        command_trace=[],
        blockers=[
            _issue(
                "git_pr_plan_unreadable",
                f"could not read git-pr-plan packet: {error}",
            )
        ],
        warnings=[],
        pr_number=None,
        remote="origin",
    )


def git_pr_materialization_pr_evidence_load_error_packet(
    *,
    plan_packet: Any,
    plan_file: str | Path,
    pr_json_file: str | Path,
    error: Exception,
    remote: str = "origin",
    pr_number: str | None = None,
) -> dict[str, Any]:
    """Build a stable blocker packet when optional PR evidence cannot be loaded."""
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    pr_path = Path(pr_json_file).expanduser().resolve(strict=False)
    repository = plan_packet.get("repository") if isinstance(plan_packet, dict) else {}
    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_pr_title = plan_packet.get("proposed_pr_title") if isinstance(plan_packet, dict) else None
    return _materialization_packet(
        valid=False,
        decision="blocked",
        approval_state="not_approved",
        plan_file=plan_path,
        plan_checksum=checksum_json(plan_packet),
        repository=repository if isinstance(repository, dict) else {},
        proposed_branch=proposed_branch if isinstance(proposed_branch, str) else None,
        proposed_pr_title=proposed_pr_title if isinstance(proposed_pr_title, str) else None,
        pr_url=None,
        intended_side_effects=[
            "create_branch",
            "push_branch",
            "update_pull_request" if pr_number else "create_pull_request",
        ],
        side_effects=[],
        command_trace=[],
        blockers=[
            _issue(
                "pr_evidence_unreadable",
                f"could not read saved PR evidence before Git/PR materialization: {error}",
                path=str(pr_path),
            )
        ],
        warnings=[],
        pr_number=pr_number,
        remote=remote,
    )


def git_pr_materialization_dirty_commit_load_error_packet(
    *,
    plan_packet: Any,
    plan_file: str | Path,
    dirty_commit_file: str | Path,
    error: Exception,
    remote: str = "origin",
    pr_number: str | None = None,
) -> dict[str, Any]:
    """Build a stable blocker packet when dirty commit source evidence cannot be loaded."""
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    dirty_path = Path(dirty_commit_file).expanduser().resolve(strict=False)
    repository = plan_packet.get("repository") if isinstance(plan_packet, dict) else {}
    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_pr_title = plan_packet.get("proposed_pr_title") if isinstance(plan_packet, dict) else None
    return _materialization_packet(
        valid=False,
        decision="blocked",
        approval_state="not_approved",
        plan_file=plan_path,
        plan_checksum=checksum_json(plan_packet),
        repository=repository if isinstance(repository, dict) else {},
        proposed_branch=proposed_branch if isinstance(proposed_branch, str) else None,
        proposed_pr_title=proposed_pr_title if isinstance(proposed_pr_title, str) else None,
        pr_url=None,
        intended_side_effects=[
            "push_branch",
            "update_pull_request" if pr_number else "create_pull_request",
        ],
        side_effects=[],
        command_trace=[],
        blockers=[
            _issue(
                "dirty_commit_materialization_unreadable",
                f"could not read dirty commit materialization evidence before Git/PR materialization: {error}",
                path=str(dirty_path),
            )
        ],
        warnings=[],
        pr_number=pr_number,
        remote=remote,
        dirty_commit_materialization={"path": str(dirty_path)},
    )


def _remote_push_url(cwd: Path, remote: str) -> tuple[str | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if not _non_empty_string(remote) or remote != remote.strip() or remote.startswith("-") or any(ch.isspace() for ch in remote):
        return None, [_issue("remote_name_invalid", f"remote is not a safe configured remote name: {remote}")]
    result = _run_git(cwd, ["remote", "get-url", "--push", remote])
    if result.returncode != 0:
        blockers.append(
            _issue(
                "remote_lookup_failed",
                f"could not resolve push URL for remote: {remote}",
                detail=(result.stderr or result.stdout).strip(),
            )
        )
        return None, blockers
    url = result.stdout.strip()
    if not url:
        blockers.append(_issue("remote_lookup_failed", f"remote has no push URL: {remote}"))
        return None, blockers
    return url, blockers


def _pr_update_preflight(
    *,
    cwd: Path,
    pr_number: str,
    proposed_branch: str,
    base_branch: str,
    expected_head: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read existing PR state before approving a PR edit target."""
    argv = ["gh", "pr", "view", pr_number, "--json", "number,headRefName,baseRefName,headRefOid"]
    result = _run_process(cwd, argv)
    trace = [_materialization_command_trace(label="preflight_pull_request", argv=argv, result=result)]
    if result.returncode != 0:
        return trace, [
            _issue(
                "pr_update_preflight_failed",
                f"could not inspect PR before update: {pr_number}",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
        ]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return trace, [_issue("pr_update_preflight_invalid_json", f"gh pr view returned invalid JSON: {exc}")]
    if not isinstance(payload, dict):
        return trace, [_issue("pr_update_preflight_invalid_json", "gh pr view did not return a JSON object")]
    mismatches = {
        field: {"expected": expected, "actual": payload.get(field)}
        for field, expected in {
            "headRefName": proposed_branch,
            "baseRefName": base_branch,
            "headRefOid": expected_head,
        }.items()
        if payload.get(field) != expected
    }
    if mismatches:
        return trace, [
            _issue(
                "pr_update_target_mismatch",
                "existing PR does not match the approved materialization target",
                mismatches=mismatches,
            )
        ]
    return trace, []


def _remote_branch_create_preflight(
    *,
    cwd: Path,
    remote: str,
    proposed_branch: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ensure PR-create materialization will not reuse an existing remote branch."""
    argv = ["git", "ls-remote", "--heads", remote, proposed_branch]
    result = _run_process(cwd, argv)
    trace = [_materialization_command_trace(label="preflight_remote_branch", argv=argv, result=result)]
    if result.returncode != 0:
        return trace, [
            _issue(
                "remote_branch_preflight_failed",
                f"could not inspect remote branch before materialization: {remote}/{proposed_branch}",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
        ]
    if result.stdout.strip():
        return trace, [
            _issue(
                "remote_branch_exists",
                "remote branch already exists; PR-create materialization requires a fresh remote branch",
                remote=remote,
                proposed_branch=proposed_branch,
            )
        ]
    return trace, []


def materialize_git_pr_plan(
    *,
    cwd: str | Path,
    plan_packet: Any,
    plan_file: str | Path,
    approval_token: str | None,
    runtime_root: str | Path,
    remote: str = "origin",
    pr_number: str | None = None,
    pr_evidence: dict[str, Any] | None = None,
    pr_evidence_source: str = "saved_pr_json",
    pr_evidence_captured_at: Any = None,
    max_pr_evidence_age_minutes: int | None = None,
    pr_evidence_path: str | Path | None = None,
    dirty_commit_materialization: Any | None = None,
    dirty_commit_materialization_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize an operator-approved git-pr-plan packet into local Git/gh side effects."""
    repo_cwd = Path(cwd).expanduser().resolve()
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    runtime_path = Path(runtime_root).expanduser().resolve()
    plan_checksum = checksum_json(plan_packet)
    remote_url, remote_blockers = _remote_push_url(repo_cwd, remote)
    approval_secret = _materialization_approval_secret()
    expected_token = (
        git_pr_materialization_approval_token(
            plan_packet,
            remote=remote,
            remote_url=remote_url,
            pr_number=pr_number,
            approval_secret=approval_secret,
        )
        if remote_url is not None and approval_secret is not None
        else None
    )
    repository = plan_packet.get("repository") if isinstance(plan_packet, dict) else {}
    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_pr_title = plan_packet.get("proposed_pr_title") if isinstance(plan_packet, dict) else None
    dirty_plan = _is_dirty_pr_materialization_plan(plan_packet)
    dirty_commit_summary = _dirty_commit_materialization_summary(
        dirty_commit_materialization,
        path=dirty_commit_materialization_path,
    )
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    command_trace: list[dict[str, Any]] = []
    side_effects: list[str] = []
    pr_url = None
    intended_side_effects = ["push_branch", "update_pull_request" if pr_number else "create_pull_request"]
    if not dirty_plan:
        intended_side_effects.insert(0, "create_branch")
    pr_evidence_summary, pr_evidence_blockers = _materialization_pr_evidence(
        pr_evidence=pr_evidence,
        source=pr_evidence_source,
        captured_at=pr_evidence_captured_at,
        max_age_minutes=max_pr_evidence_age_minutes,
        path=pr_evidence_path,
    )

    blockers.extend(pr_evidence_blockers)
    blockers.extend(remote_blockers)
    if dirty_plan:
        blockers.extend(_dirty_commit_plan_structural_blockers(plan_packet))
        if dirty_commit_materialization is None:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_missing",
                    "dirty commit materialization evidence is required for dirty Git/PR materialization plans",
                )
            )
    else:
        blockers.extend(_plan_structural_blockers(plan_packet))
        if dirty_commit_materialization is not None:
            blockers.append(
                _issue(
                    "dirty_commit_materialization_unexpected",
                    "dirty commit materialization evidence can only be used with git-pr-dirty-materialization-plan packets",
                )
            )
    if not approval_token:
        approval_state = "not_approved"
        blockers.append(
            _issue(
                "operator_approval_missing",
                "operator approval token is required before Git/PR materialization",
            )
        )
    elif remote_url is None:
        approval_state = "approval_unresolved"
        blockers.append(
            _issue(
                "operator_approval_target_unresolved",
                "materialization target must resolve before operator approval can be checked",
            )
        )
    elif approval_secret is None:
        approval_state = "approval_unresolved"
        blockers.append(
            _issue(
                "operator_approval_secret_missing",
                f"{GIT_PR_MATERIALIZATION_APPROVAL_SECRET_ENV} is required to verify operator approval",
            )
        )
    elif expected_token is None or not hmac.compare_digest(approval_token, expected_token):
        approval_state = "approval_mismatch"
        blockers.append(
            _issue(
                "operator_approval_mismatch",
                "operator approval token does not match the approved git-pr-plan packet",
            )
        )
    else:
        approval_state = "approved"

    if blockers:
        return _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch if isinstance(proposed_branch, str) else None,
            proposed_pr_title=proposed_pr_title if isinstance(proposed_pr_title, str) else None,
            pr_url=None,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )

    if dirty_plan:
        dirty_commit_summary, dirty_blockers, dirty_trace = _dirty_commit_materialization_source_evidence(
            cwd=repo_cwd,
            plan_packet=plan_packet,
            dirty_packet=dirty_commit_materialization,
            dirty_packet_path=dirty_commit_materialization_path,
        )
        blockers.extend(dirty_blockers)
        command_trace.extend(dirty_trace)
        if isinstance(dirty_commit_summary, dict) and isinstance(dirty_commit_summary.get("repository"), dict):
            repository = dirty_commit_summary["repository"]
        blockers.extend(
            _dirty_pr_materialization_target_blockers(
                plan_packet=plan_packet,
                remote=remote,
                remote_url=remote_url,
                pr_number=pr_number,
            )
        )
        branch_policy = plan_packet.get("branch_policy") if isinstance(plan_packet.get("branch_policy"), dict) else {}
        current_policy_branch = (
            dirty_commit_summary.get("source_branch")
            if isinstance(dirty_commit_summary, dict)
            else repository.get("current_branch") if isinstance(repository, dict) else None
        )
        for source, policy in (
            ("task_packet", branch_policy.get("task_packet") if isinstance(branch_policy, dict) else None),
            ("policy_file", branch_policy.get("policy_file") if isinstance(branch_policy, dict) else None),
        ):
            if isinstance(policy, dict):
                blockers.extend(
                    _branch_policy_blockers(
                        policy,
                        source=source,
                        base_branch=(
                            repository.get("base_branch")
                            if isinstance(repository, dict) and _non_empty_string(repository.get("base_branch"))
                            else "main"
                        ),
                        proposed_branch=proposed_branch if isinstance(proposed_branch, str) else "",
                        current_branch=current_policy_branch if isinstance(current_policy_branch, str) else None,
                    )
                )
        materialized_body = _materialized_pr_body(plan_packet)
        body_preflight = _preflight_pr_body(materialized_body, _materialization_required_sections(plan_packet))
        for blocker in body_preflight.get("blockers", []):
            if isinstance(blocker, dict):
                blockers.append(blocker)
        for warning in body_preflight.get("warnings", []):
            if isinstance(warning, dict):
                warnings.append(warning)
    else:
        provenance = plan_packet.get("evidence_provenance") if isinstance(plan_packet.get("evidence_provenance"), dict) else {}
        task_packet, task_blocker = _load_materialization_json(
            provenance.get("task_file"),
            "task_packet_unreadable",
            "could not read task packet from git-pr-plan evidence provenance",
        )
        if task_blocker is not None:
            blockers.append(task_blocker)
        result_evidence, result_blocker = _load_materialization_json(
            provenance.get("result_file"),
            "result_evidence_unreadable",
            "could not read executor result evidence from git-pr-plan evidence provenance",
        )
        if result_blocker is not None:
            blockers.append(result_blocker)
        if task_packet is not None and provenance.get("task_file_checksum") != checksum_json(task_packet):
            blockers.append(
                _issue(
                    "task_packet_changed",
                    "task packet checksum no longer matches the approved git-pr-plan packet",
                )
            )
        if result_evidence is not None and provenance.get("result_file_checksum") != checksum_json(result_evidence):
            blockers.append(
                _issue(
                    "result_evidence_changed",
                    "executor result checksum no longer matches the approved git-pr-plan packet",
                )
            )

        if task_packet is not None and result_evidence is not None:
            branch_policy = plan_packet.get("branch_policy") if isinstance(plan_packet.get("branch_policy"), dict) else {}
            rechecked_plan = evaluate_git_pr_plan(
                cwd=repo_cwd,
                task_packet=task_packet,
                result_evidence=result_evidence,
                task_file=provenance.get("task_file"),
                result_file=provenance.get("result_file"),
                base_branch=(
                    repository.get("base_branch")
                    if isinstance(repository, dict) and _non_empty_string(repository.get("base_branch"))
                    else "main"
                ),
                branch_prefix="",
                proposed_branch_override=proposed_branch if isinstance(proposed_branch, str) else None,
                branch_policy=branch_policy.get("policy_file") if isinstance(branch_policy, dict) else None,
                required_body_sections=_materialization_required_sections(plan_packet),
                runtime_root=runtime_path,
            )
            blockers.extend(_stale_plan_blockers(plan_packet, rechecked_plan))
            blockers.extend(_changed_plan_blockers(plan_packet, rechecked_plan))
            if not rechecked_plan.get("ready_to_review"):
                blockers.append(
                    _issue(
                        "git_pr_plan_recheck_blocked",
                        "rechecked Git/PR plan is blocked immediately before materialization",
                        recheck_blockers=rechecked_plan.get("blockers", []),
                    )
                )
            repository = rechecked_plan.get("repository") if isinstance(rechecked_plan.get("repository"), dict) else repository
            materialized_body = _materialized_pr_body(plan_packet)
            body_preflight = _preflight_pr_body(materialized_body, _materialization_required_sections(plan_packet))
            for blocker in body_preflight.get("blockers", []):
                if isinstance(blocker, dict):
                    blockers.append(blocker)
            for warning in body_preflight.get("warnings", []):
                if isinstance(warning, dict):
                    warnings.append(warning)
        else:
            materialized_body = _materialized_pr_body(plan_packet) if isinstance(plan_packet, dict) else ""

    if not pr_number and not blockers and isinstance(proposed_branch, str):
        remote_trace, remote_branch_blockers = _remote_branch_create_preflight(
            cwd=repo_cwd,
            remote=remote,
            proposed_branch=proposed_branch,
        )
        command_trace.extend(remote_trace)
        blockers.extend(remote_branch_blockers)

    if (
        pr_number
        and not blockers
        and isinstance(proposed_branch, str)
        and isinstance(repository, dict)
        and _non_empty_string(repository.get("base_branch"))
        and _non_empty_string(repository.get("current_head"))
    ):
        pr_trace, pr_blockers = _pr_update_preflight(
            cwd=repo_cwd,
            pr_number=pr_number,
            proposed_branch=proposed_branch,
            base_branch=repository["base_branch"],
            expected_head=repository["current_head"],
        )
        command_trace.extend(pr_trace)
        blockers.extend(pr_blockers)

    if blockers:
        return _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch if isinstance(proposed_branch, str) else None,
            proposed_pr_title=proposed_pr_title if isinstance(proposed_pr_title, str) else None,
            pr_url=None,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )

    intent_payload = _materialization_packet(
        valid=True,
        decision="approved",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        repository=repository if isinstance(repository, dict) else {},
        proposed_branch=proposed_branch,
        proposed_pr_title=proposed_pr_title,
        pr_url=None,
        intended_side_effects=intended_side_effects,
        side_effects=[],
        command_trace=[],
        blockers=[],
        warnings=warnings,
        pr_number=pr_number,
        remote=remote,
        remote_url=remote_url,
        pr_evidence=pr_evidence_summary,
        dirty_commit_materialization=dirty_commit_summary,
    )
    _audit_record, audit_blocker = _append_materialization_audit(
        runtime_path,
        git_pr_materialization_intent_audit_record(intent_payload),
    )
    if audit_blocker is not None:
        return _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch,
            proposed_pr_title=proposed_pr_title,
            pr_url=None,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=[],
            blockers=[audit_blocker],
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )
    side_effects.append("audit_intent_record_appended")

    commands = []
    if not dirty_plan:
        commands.append(
            (
                "create_branch",
                ["git", "branch", str(proposed_branch), str(repository.get("current_head"))],
                "created_branch",
            )
        )
    commands.append(("push_branch", ["git", "push", "--no-verify", "-u", remote, str(proposed_branch)], "pushed_branch"))
    for label, argv, side_effect in commands:
        result = _run_process(repo_cwd, argv)
        command_trace.append(_materialization_command_trace(label=label, argv=argv, result=result))
        if result.returncode != 0:
            blockers.append(
                _issue(
                    "git_pr_materialization_command_failed",
                    f"{label} failed during approved Git/PR materialization",
                    command_label=label,
                    returncode=result.returncode,
                    stderr=result.stderr.strip(),
                )
            )
            failed_side_effects = [*side_effects, "audit_result_record_appended"]
            failed_packet = _materialization_packet(
                valid=False,
                decision="blocked",
                approval_state=approval_state,
                plan_file=plan_path,
                plan_checksum=plan_checksum,
                repository=repository if isinstance(repository, dict) else {},
                proposed_branch=proposed_branch,
                proposed_pr_title=proposed_pr_title,
                pr_url=pr_url,
                intended_side_effects=intended_side_effects,
                side_effects=failed_side_effects,
                command_trace=command_trace,
                blockers=blockers,
                warnings=warnings,
                pr_number=pr_number,
                remote=remote,
                remote_url=remote_url,
                pr_evidence=pr_evidence_summary,
                dirty_commit_materialization=dirty_commit_summary,
            )
            _result_audit, result_audit_blocker = _append_materialization_audit(
                runtime_path,
                git_pr_materialization_result_audit_record(failed_packet),
            )
            if result_audit_blocker is None:
                return failed_packet
            else:
                failed_packet_without_audit = _materialization_packet(
                    valid=False,
                    decision="blocked",
                    approval_state=approval_state,
                    plan_file=plan_path,
                    plan_checksum=plan_checksum,
                    repository=repository if isinstance(repository, dict) else {},
                    proposed_branch=proposed_branch,
                    proposed_pr_title=proposed_pr_title,
                    pr_url=pr_url,
                    intended_side_effects=intended_side_effects,
                    side_effects=side_effects,
                    command_trace=command_trace,
                    blockers=blockers,
                    warnings=[*warnings, result_audit_blocker],
                    pr_number=pr_number,
                    remote=remote,
                    remote_url=remote_url,
                    pr_evidence=pr_evidence_summary,
                    dirty_commit_materialization=dirty_commit_summary,
                )
                return failed_packet_without_audit
        side_effects.append(side_effect)
        repository = _refresh_materialization_repository(repo_cwd, repository)

    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
            handle.write(materialized_body)
            body_file = Path(handle.name)
    except OSError as exc:
        failure_message = str(exc)
        blockers.append(
            _issue(
                "temporary_pr_body_creation_failed",
                "could not create temporary PR body file during approved Git/PR materialization",
                detail=failure_message,
            )
        )
        command_trace.append(
            _materialization_error_trace(
                label="temporary_pr_body_creation_failed",
                message=failure_message,
            )
        )
        failed_side_effects = [*side_effects, "audit_result_record_appended"]
        failed_packet = _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch,
            proposed_pr_title=proposed_pr_title,
            pr_url=pr_url,
            intended_side_effects=intended_side_effects,
            side_effects=failed_side_effects,
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )
        _result_audit, result_audit_blocker = _append_materialization_audit(
            runtime_path,
            git_pr_materialization_result_audit_record(failed_packet),
        )
        if result_audit_blocker is None:
            return failed_packet
        return _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch,
            proposed_pr_title=proposed_pr_title,
            pr_url=pr_url,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            blockers=blockers,
            warnings=[*warnings, result_audit_blocker],
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )
    try:
        if pr_number:
            gh_argv = ["gh", "pr", "edit", pr_number, "--title", str(proposed_pr_title), "--body-file", str(body_file)]
            gh_label = "update_pull_request"
            gh_side_effect = "updated_pull_request"
        else:
            base_branch = repository.get("base_branch") if isinstance(repository, dict) else "main"
            gh_argv = [
                "gh",
                "pr",
                "create",
                "--base",
                str(base_branch),
                "--head",
                str(proposed_branch),
                "--title",
                str(proposed_pr_title),
                "--body-file",
                str(body_file),
            ]
            gh_label = "create_pull_request"
            gh_side_effect = "created_pull_request"
        gh_result = _run_process(repo_cwd, gh_argv)
        command_trace.append(_materialization_command_trace(label=gh_label, argv=gh_argv, result=gh_result))
    finally:
        try:
            body_file.unlink()
        except OSError:
            warnings.append(_issue("temporary_pr_body_cleanup_failed", "could not remove temporary PR body file"))

    if gh_result.returncode != 0:
        blockers.append(
            _issue(
                "git_pr_materialization_command_failed",
                f"{gh_label} failed during approved Git/PR materialization",
                command_label=gh_label,
                returncode=gh_result.returncode,
                stderr=gh_result.stderr.strip(),
            )
        )
        failed_side_effects = [*side_effects, "audit_result_record_appended"]
        failed_packet = _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch,
            proposed_pr_title=proposed_pr_title,
            pr_url=None,
            intended_side_effects=intended_side_effects,
            side_effects=failed_side_effects,
            command_trace=command_trace,
            blockers=blockers,
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )
        _result_audit, result_audit_blocker = _append_materialization_audit(
            runtime_path,
            git_pr_materialization_result_audit_record(failed_packet),
        )
        if result_audit_blocker is None:
            return failed_packet
        else:
            return _materialization_packet(
                valid=False,
                decision="blocked",
                approval_state=approval_state,
                plan_file=plan_path,
                plan_checksum=plan_checksum,
                repository=repository if isinstance(repository, dict) else {},
                proposed_branch=proposed_branch,
                proposed_pr_title=proposed_pr_title,
                pr_url=None,
                intended_side_effects=intended_side_effects,
                side_effects=side_effects,
                command_trace=command_trace,
                blockers=blockers,
                warnings=[*warnings, result_audit_blocker],
                pr_number=pr_number,
                remote=remote,
                remote_url=remote_url,
                pr_evidence=pr_evidence_summary,
                dirty_commit_materialization=dirty_commit_summary,
            )

    side_effects.append(gh_side_effect)
    repository = _refresh_materialization_repository(repo_cwd, repository)
    pr_url = gh_result.stdout.strip().splitlines()[0] if gh_result.stdout.strip() else None
    success_side_effects = [*side_effects, "audit_result_record_appended"]
    success_packet = _materialization_packet(
        valid=True,
        decision="materialized",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        repository=repository if isinstance(repository, dict) else {},
        proposed_branch=proposed_branch,
        proposed_pr_title=proposed_pr_title,
        pr_url=pr_url,
        intended_side_effects=intended_side_effects,
        side_effects=success_side_effects,
        command_trace=command_trace,
        blockers=[],
        warnings=warnings,
        pr_number=pr_number,
        remote=remote,
        remote_url=remote_url,
        pr_evidence=pr_evidence_summary,
        dirty_commit_materialization=dirty_commit_summary,
    )
    _result_audit, result_audit_blocker = _append_materialization_audit(
        runtime_path,
        git_pr_materialization_result_audit_record(success_packet),
    )
    if result_audit_blocker is None:
        return success_packet
    else:
        return _materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            repository=repository if isinstance(repository, dict) else {},
            proposed_branch=proposed_branch,
            proposed_pr_title=proposed_pr_title,
            pr_url=pr_url,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            blockers=[result_audit_blocker],
            warnings=warnings,
            pr_number=pr_number,
            remote=remote,
            remote_url=remote_url,
            pr_evidence=pr_evidence_summary,
            dirty_commit_materialization=dirty_commit_summary,
        )
