from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.model import BUCKETS, TASK_TYPES
from codex_cadence.store import utc_now

EXECUTOR_TASK_SCHEMA_VERSION = "generic-executor-task.v1"
EXECUTOR_RESULT_SCHEMA_VERSION = "generic-executor-result.v1"
EXECUTOR_STATUSES = ("succeeded", "failed", "blocked", "stopped")
EXECUTOR_CONFIDENCE_VALUES = ("high", "medium", "low")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_non_empty_string(item) for item in value)


def _repo_relative_path(value: Any) -> str | None:
    if not _non_empty_string(value):
        return None
    raw = str(value).replace("\\", "/").strip()
    if raw.startswith("/"):
        return None
    normalized = raw.strip("/")
    if not normalized:
        return "."
    if ":" in normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = _repo_relative_path(path)
    if normalized is None:
        return False
    for allowed_path in allowed_paths:
        allowed = _repo_relative_path(allowed_path)
        if allowed == ".":
            return True
        if normalized == allowed or normalized.startswith(f"{allowed}/"):
            return True
    return False


def _parse_utc(value: Any) -> datetime | None:
    if not _non_empty_string(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _task_summary(task: dict[str, Any]) -> str:
    summary = task.get("summary")
    if _non_empty_string(summary):
        return summary
    return task.get("title", "")


def build_executor_task_packet(
    *,
    task: dict[str, Any],
    snapshot: dict[str, Any],
    repo_path: str | Path,
    allowed_paths: list[str],
    required_checks: list[str],
    max_minutes: int,
    max_tasks: int,
    stop_conditions: list[str],
    evidence_path: str | Path,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTOR_TASK_SCHEMA_VERSION,
        "packet": "executor_task",
        "created_at": utc_now(),
        "operator_confirmation_required": True,
        "executor_started": False,
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "summary": _task_summary(task),
            "task_type": task.get("task_type"),
            "bucket": task.get("bucket"),
            "source": task.get("source"),
            "drivers": list(task.get("drivers", [])),
            "evidence": deepcopy(task.get("evidence", {})),
        },
        "repo": {
            "name": snapshot.get("repo"),
            "path": str(Path(repo_path).expanduser().resolve()),
            "branch": snapshot.get("branch"),
            "head": snapshot.get("head"),
        },
        "snapshot": deepcopy(snapshot),
        "allowed_paths": list(allowed_paths),
        "required_checks": list(required_checks),
        "limits": {
            "max_minutes": max_minutes,
            "max_tasks": max_tasks,
        },
        "stop_conditions": list(stop_conditions),
        "expected_output": {
            "schema_version": EXECUTOR_RESULT_SCHEMA_VERSION,
            "evidence_path": str(evidence_path),
        },
        "permissions": {
            "may_commit": False,
            "may_push": False,
            "may_open_pr": False,
            "named_host_adapter": None,
        },
        "limitations": [
            "generic_contract_only",
            "executor_not_started_by_cadence",
            "named_host_adapter_not_selected",
        ],
    }


def validate_executor_task_packet(packet: Any) -> tuple[bool, str]:
    if not isinstance(packet, dict):
        return False, "executor task packet must be a JSON object"
    if packet.get("schema_version") != EXECUTOR_TASK_SCHEMA_VERSION:
        return False, "executor task schema_version is invalid"
    if packet.get("packet") != "executor_task":
        return False, "executor task packet must be executor_task"
    if packet.get("operator_confirmation_required") is not True:
        return False, "executor task operator_confirmation_required must be true"
    if packet.get("executor_started") is not False:
        return False, "executor task executor_started must be false"
    task = packet.get("task")
    if not isinstance(task, dict):
        return False, "executor task task must be a JSON object"
    for field in ("id", "title", "summary", "task_type", "bucket"):
        if not _non_empty_string(task.get(field)):
            return False, f"executor task task.{field} is required"
    if task.get("task_type") not in TASK_TYPES:
        return False, "executor task task.task_type must be execution or discovery"
    if task.get("bucket") not in BUCKETS:
        return False, "executor task task.bucket must be one of XS, S, M, L, XL"
    repo = packet.get("repo")
    if not isinstance(repo, dict):
        return False, "executor task repo must be a JSON object"
    for field in ("path", "branch", "head"):
        if not _non_empty_string(repo.get(field)):
            return False, f"executor task repo.{field} is required"
    snapshot = packet.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("id") is None:
        return False, "executor task snapshot with id is required"
    allowed_paths = packet.get("allowed_paths")
    if not _is_string_list(allowed_paths) or not allowed_paths:
        return False, "executor task allowed_paths must be a non-empty list of strings"
    for index, allowed_path in enumerate(allowed_paths):
        if _repo_relative_path(allowed_path) is None:
            return False, f"executor task allowed_paths[{index}] must be repo-relative"
    if not _is_string_list(packet.get("required_checks")):
        return False, "executor task required_checks must be a list of strings"
    limits = packet.get("limits")
    if not isinstance(limits, dict):
        return False, "executor task limits must be a JSON object"
    for field in ("max_minutes", "max_tasks"):
        value = limits.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return False, f"executor task limits.{field} must be a positive integer"
    if not _is_string_list(packet.get("stop_conditions")) or not packet.get("stop_conditions"):
        return False, "executor task stop_conditions must be a non-empty list of strings"
    expected_output = packet.get("expected_output")
    if not isinstance(expected_output, dict):
        return False, "executor task expected_output must be a JSON object"
    if expected_output.get("schema_version") != EXECUTOR_RESULT_SCHEMA_VERSION:
        return False, "executor task expected_output.schema_version is invalid"
    if not _non_empty_string(expected_output.get("evidence_path")):
        return False, "executor task expected_output.evidence_path is required"
    permissions = packet.get("permissions")
    if not isinstance(permissions, dict):
        return False, "executor task permissions must be a JSON object"
    for field in ("may_commit", "may_push", "may_open_pr"):
        if permissions.get(field) is not False:
            return False, f"executor task permissions.{field} must be false"
    if permissions.get("named_host_adapter") is not None:
        return False, "executor task permissions.named_host_adapter must be null"
    return True, "ok"


def validate_executor_result_evidence(evidence: Any, task_packet: dict[str, Any]) -> tuple[bool, str]:
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return False, f"invalid executor task packet: {task_reason}"
    if not isinstance(evidence, dict):
        return False, "executor result must be a JSON object"
    if evidence.get("schema_version") != EXECUTOR_RESULT_SCHEMA_VERSION:
        return False, "executor result schema_version is invalid"
    if evidence.get("packet") != "executor_result":
        return False, "executor result packet must be executor_result"
    if evidence.get("task_id") != task_packet["task"]["id"]:
        return False, "executor result task_id does not match task packet"
    if not _non_empty_string(evidence.get("executor_id")):
        return False, "executor result executor_id is required"
    started_at = _parse_utc(evidence.get("started_at"))
    if started_at is None:
        return False, "executor result started_at is invalid"
    ended_at = _parse_utc(evidence.get("ended_at"))
    if ended_at is None:
        return False, "executor result ended_at is invalid"
    if ended_at < started_at:
        return False, "executor result ended_at must be at or after started_at"
    status = evidence.get("status")
    if status not in EXECUTOR_STATUSES:
        return False, "executor result status is invalid"
    files_changed = evidence.get("files_changed")
    if not isinstance(files_changed, list) or any(not _non_empty_string(path) for path in files_changed):
        return False, "executor result files_changed must be a list of strings"
    allowed_paths = task_packet["allowed_paths"]
    for index, path in enumerate(files_changed):
        if not _path_allowed(path, allowed_paths):
            return False, f"executor result files_changed[{index}] is outside allowed_paths"
    commands_run = evidence.get("commands_run")
    if not isinstance(commands_run, list):
        return False, "executor result commands_run must be a list"
    for index, command in enumerate(commands_run):
        if not isinstance(command, dict):
            return False, f"executor result commands_run[{index}] must be a JSON object"
        if not _non_empty_string(command.get("command")):
            return False, f"executor result commands_run[{index}].command is required"
        exit_code = command.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            return False, f"executor result commands_run[{index}].exit_code must be an integer"
    validation_results = evidence.get("validation_results")
    if not isinstance(validation_results, list):
        return False, "executor result validation_results must be a list"
    for index, result in enumerate(validation_results):
        if not isinstance(result, dict):
            return False, f"executor result validation_results[{index}] must be a JSON object"
        if not _non_empty_string(result.get("name")):
            return False, f"executor result validation_results[{index}].name is required"
        if result.get("status") not in {"passed", "failed", "skipped"}:
            return False, f"executor result validation_results[{index}].status is invalid"
    if not _non_empty_string(evidence.get("summary")):
        return False, "executor result summary is required"
    if evidence.get("confidence") not in EXECUTOR_CONFIDENCE_VALUES:
        return False, "executor result confidence is invalid"
    if not _is_string_list(evidence.get("blockers")):
        return False, "executor result blockers must be a list of strings"
    if not isinstance(evidence.get("dirty_worktree"), bool):
        return False, "executor result dirty_worktree must be a boolean"
    if evidence.get("status") == "succeeded" and evidence.get("dirty_worktree") is not False:
        return False, "executor result dirty_worktree must be false when status is succeeded"
    resulting_head = evidence.get("resulting_head")
    if resulting_head is not None and not _non_empty_string(resulting_head):
        return False, "executor result resulting_head must be a string or null"
    return True, "ok"
