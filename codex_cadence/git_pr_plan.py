"""Dry-run Git and pull request transition planning."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.executor_contract import validate_executor_result_evidence, validate_executor_task_packet
from codex_cadence.policy_audit import checksum_json
from codex_cadence.pr_readiness import evaluate_pr_body_preflight
from codex_cadence.store import BRAKE_STATUSES, utc_now

GIT_PR_PLAN_SCHEMA_VERSION = "git-pr-plan.v1"


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
        ("create_branch", ["git", "switch", "-c", proposed_branch], None),
        ("commit_changes", ["git", "commit", "-m", proposed_commit_message], None),
        ("push_branch", ["git", "push", "-u", "origin", proposed_branch], None),
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


def evaluate_git_pr_plan(
    *,
    cwd: str | Path,
    task_packet: Any,
    result_evidence: Any,
    task_file: str | Path,
    result_file: str | Path,
    base_branch: str = "main",
    branch_prefix: str = "cadence",
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
    proposed_branch = _generated_branch_name(branch_prefix, task.get("id"))
    proposed_commit_message = str(task.get("title") or "").strip()
    proposed_pr_title = proposed_commit_message

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
