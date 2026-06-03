"""Dry-run Git and pull request transition planning."""

from __future__ import annotations

import json
import re
import shutil
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.branch_policy import normalize_branch_policy
from codex_cadence.executor_contract import validate_executor_result_evidence, validate_executor_task_packet
from codex_cadence.policy_audit import (
    append_audit_record,
    checksum_json,
    git_pr_materialization_intent_audit_record,
    git_pr_materialization_result_audit_record,
)
from codex_cadence.pr_readiness import evaluate_pr_body_preflight
from codex_cadence.store import BRAKE_STATUSES, read_json, utc_now

GIT_PR_PLAN_SCHEMA_VERSION = "git-pr-plan.v1"
GIT_PR_MATERIALIZATION_SCHEMA_VERSION = "git-pr-materialization.v1"
GIT_PR_MATERIALIZATION_APPROVAL_PREFIX = "approve-git-pr:"


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured blocker or warning payload."""
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def _non_empty_string(value: Any) -> bool:
    """Return True for strings that contain non-whitespace text."""
    return isinstance(value, str) and bool(value.strip())


def _run_git(cwd: Path, args: list[str], *, optional_locks: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a Git command in cwd, optionally disabling optional locks."""
    command = ["git", *args] if optional_locks else ["git", "--no-optional-locks", *args]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def _run_process(cwd: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a materialization command and preserve OSError as a failed process."""
    executable = shutil.which(argv[0])
    command = [executable or argv[0], *argv[1:]]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
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


def git_pr_materialization_approval_token(
    plan_packet: Any,
    *,
    remote: str = "origin",
    remote_url: str | None = None,
    pr_number: str | None = None,
) -> str:
    """Return the exact operator approval token for a materialization target."""
    approval_payload = git_pr_materialization_approval_payload(
        plan_packet,
        remote=remote,
        remote_url=remote_url,
        pr_number=pr_number,
    )
    return GIT_PR_MATERIALIZATION_APPROVAL_PREFIX + checksum_json(approval_payload).removeprefix("sha256:")


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
        if dirty_paths:
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


def _materialization_recommendation(blockers: list[dict[str, Any]], *, side_effects_started: bool) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if not blockers:
        return "inspect_pull_request"
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
    expected_approval_token: str | None,
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
) -> dict[str, Any]:
    side_effects_started = bool(side_effects)
    return {
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
        "expected_approval_token": expected_approval_token,
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


def git_pr_materialization_load_error_packet(plan_file: str | Path, error: Exception) -> dict[str, Any]:
    """Build a stable blocker packet when the plan file cannot be loaded."""
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    return _materialization_packet(
        valid=False,
        decision="blocked",
        approval_state="not_approved",
        plan_file=plan_path,
        plan_checksum=None,
        expected_approval_token=None,
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


def materialize_git_pr_plan(
    *,
    cwd: str | Path,
    plan_packet: Any,
    plan_file: str | Path,
    approval_token: str | None,
    runtime_root: str | Path,
    remote: str = "origin",
    pr_number: str | None = None,
) -> dict[str, Any]:
    """Materialize an operator-approved git-pr-plan packet into local Git/gh side effects."""
    repo_cwd = Path(cwd).expanduser().resolve()
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    runtime_path = Path(runtime_root).expanduser().resolve()
    plan_checksum = checksum_json(plan_packet)
    remote_url, remote_blockers = _remote_push_url(repo_cwd, remote)
    expected_token = (
        git_pr_materialization_approval_token(
            plan_packet,
            remote=remote,
            remote_url=remote_url,
            pr_number=pr_number,
        )
        if remote_url is not None
        else None
    )
    repository = plan_packet.get("repository") if isinstance(plan_packet, dict) else {}
    proposed_branch = plan_packet.get("proposed_branch") if isinstance(plan_packet, dict) else None
    proposed_pr_title = plan_packet.get("proposed_pr_title") if isinstance(plan_packet, dict) else None
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    command_trace: list[dict[str, Any]] = []
    side_effects: list[str] = []
    pr_url = None
    intended_side_effects = [
        "create_branch",
        "push_branch",
        "update_pull_request" if pr_number else "create_pull_request",
    ]

    blockers.extend(remote_blockers)
    blockers.extend(_plan_structural_blockers(plan_packet))
    if not approval_token:
        approval_state = "not_approved"
        blockers.append(
            _issue(
                "operator_approval_missing",
                "operator approval token is required before Git/PR materialization",
            )
        )
    elif expected_token is None:
        approval_state = "approval_unresolved"
        blockers.append(
            _issue(
                "operator_approval_target_unresolved",
                "materialization target must resolve before operator approval can be checked",
            )
        )
    elif approval_token != expected_token:
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
            expected_approval_token=expected_token,
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
        )

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
            expected_approval_token=expected_token,
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
        )

    intent_payload = _materialization_packet(
        valid=True,
        decision="approved",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        expected_approval_token=expected_token,
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
    )
    audit_record, audit_blocker = _append_materialization_audit(
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
            expected_approval_token=expected_token,
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
        )
    side_effects.append("audit_intent_record_appended")

    commands = [
        (
            "create_branch",
            ["git", "branch", str(proposed_branch), str(repository.get("current_head"))],
            "created_branch",
        ),
        ("push_branch", ["git", "push", "--no-verify", "-u", remote, str(proposed_branch)], "pushed_branch"),
    ]
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
                expected_approval_token=expected_token,
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
            )
            result_audit, result_audit_blocker = _append_materialization_audit(
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
                    expected_approval_token=expected_token,
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
                )
                return failed_packet_without_audit
        side_effects.append(side_effect)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(materialized_body)
        body_file = Path(handle.name)
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
            expected_approval_token=expected_token,
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
        )
        result_audit, result_audit_blocker = _append_materialization_audit(
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
                expected_approval_token=expected_token,
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
            )

    side_effects.append(gh_side_effect)
    pr_url = gh_result.stdout.strip().splitlines()[0] if gh_result.stdout.strip() else None
    success_side_effects = [*side_effects, "audit_result_record_appended"]
    success_packet = _materialization_packet(
        valid=True,
        decision="materialized",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        expected_approval_token=expected_token,
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
    )
    result_audit, result_audit_blocker = _append_materialization_audit(
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
            expected_approval_token=expected_token,
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
        )
