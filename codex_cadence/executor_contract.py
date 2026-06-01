from __future__ import annotations

import os
import shlex
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.model import BUCKETS, TASK_TYPES
from codex_cadence.repo_state import validate_repo_snapshot
from codex_cadence.store import utc_now

EXECUTOR_TASK_SCHEMA_VERSION = "generic-executor-task.v1"
EXECUTOR_RESULT_SCHEMA_VERSION = "generic-executor-result.v1"
EXECUTOR_STATUSES = ("succeeded", "failed", "blocked", "stopped")
EXECUTOR_CONFIDENCE_VALUES = ("high", "medium", "low")
DEFAULT_EXECUTOR_STOP_CONDITIONS = ["brake_not_drive", "operator_stop", "context_pressure", "timeout"]
_SHELL_COMMANDS = {
    "bash",
    "bash.exe",
    "dash",
    "dash.exe",
    "sh",
    "sh.exe",
    "zsh",
    "zsh.exe",
}
_POWERSHELL_COMMANDS = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_CMD_COMMANDS = {"cmd", "cmd.exe"}
_GIT_OPTIONS_WITH_VALUE = {
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
_GIT_OPTIONS_WITH_EQUALS = (
    "-c",
    "--config-env=",
    "--exec-path=",
    "--git-dir=",
    "--namespace=",
    "--super-prefix=",
    "--work-tree=",
)
_GH_OPTIONS_WITH_VALUE = {
    "-r",
    "--config-dir",
    "--editor",
    "--git-protocol",
    "--hostname",
    "--repo",
}
_GH_OPTIONS_WITH_EQUALS = (
    "--config-dir=",
    "--editor=",
    "--git-protocol=",
    "--hostname=",
    "--repo=",
)
_COMMAND_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")"}


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


def _normalized_filesystem_path(value: Any) -> str | None:
    if not _non_empty_string(value):
        return None
    if "\0" in value:
        return None
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        return os.path.normcase(str(path.expanduser().resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


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


def _normalized_command(command: str) -> str:
    return " ".join(command.strip().lower().split())


def _command_name(token: str) -> str:
    return PurePosixPath(token.replace("\\", "/")).name.lower()


def _command_text_for_lexing(command: str) -> str:
    return command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ; ")


def _command_tokens(command: str) -> list[str]:
    shell_text = _command_text_for_lexing(command)
    try:
        lexer = shlex.shlex(shell_text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return [token.lower() for token in lexer]
    except ValueError:
        return _normalized_command(shell_text).split()


def _command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _COMMAND_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _option_uses_value(token: str, options_with_value: set[str], options_with_equals: tuple[str, ...]) -> bool:
    if token in options_with_value:
        return True
    return any(token.startswith(prefix) and token != prefix for prefix in options_with_equals)


def _next_git_subcommand(tokens: list[str], index: int) -> str | None:
    current = index + 1
    while current < len(tokens):
        token = tokens[current]
        if token == "--":
            current += 1
            break
        if token in _COMMAND_SEPARATORS:
            return None
        if _option_uses_value(token, _GIT_OPTIONS_WITH_VALUE, _GIT_OPTIONS_WITH_EQUALS):
            current += 2 if token in _GIT_OPTIONS_WITH_VALUE else 1
            continue
        if token.startswith("-"):
            current += 1
            continue
        return token
    return tokens[current] if current < len(tokens) else None


def _git_invokes(tokens: list[str], subcommand: str) -> bool:
    for index, token in enumerate(tokens):
        if (
            _command_name(token) in {"git", "git.exe"}
            and _next_git_subcommand(tokens, index) == subcommand
        ):
            return True
    return False


def _next_gh_subcommands(tokens: list[str], index: int, count: int) -> list[str]:
    current = index + 1
    subcommands: list[str] = []
    while current < len(tokens) and len(subcommands) < count:
        token = tokens[current]
        if token in _COMMAND_SEPARATORS:
            break
        if _option_uses_value(token, _GH_OPTIONS_WITH_VALUE, _GH_OPTIONS_WITH_EQUALS):
            current += 2 if token in _GH_OPTIONS_WITH_VALUE else 1
            continue
        if token.startswith("-"):
            current += 1
            continue
        subcommands.append(token)
        current += 1
    return subcommands


def _gh_invokes(tokens: list[str], subcommands: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if (
            _command_name(token) in {"gh", "gh.exe"}
            and _next_gh_subcommands(tokens, index, len(subcommands)) == subcommands
        ):
            return True
    return False


def _embedded_shell_commands(tokens: list[str]) -> list[str]:
    embedded: list[str] = []
    for index, token in enumerate(tokens):
        command = _command_name(token)
        if command in _SHELL_COMMANDS:
            for option_index in range(index + 1, len(tokens) - 1):
                option = tokens[option_index]
                if option == "-c" or (
                    option.startswith("-") and not option.startswith("--") and "c" in option[1:]
                ):
                    embedded.append(tokens[option_index + 1])
                    break
        if command in _CMD_COMMANDS:
            for option_index in range(index + 1, len(tokens) - 1):
                if tokens[option_index] in {"/c", "/k"}:
                    embedded.append(tokens[option_index + 1])
                    break
        if command in _POWERSHELL_COMMANDS:
            for option_index in range(index + 1, len(tokens) - 1):
                if tokens[option_index] in {"-command", "-c"}:
                    embedded.append(tokens[option_index + 1])
                    break
    return embedded


def _raw_command_substitutions(command: str) -> list[str]:
    substitutions: list[str] = []
    index = 0
    while index < len(command):
        if not command.startswith("$(", index):
            index += 1
            continue
        depth = 1
        index += 2
        start = index
        while index < len(command) and depth > 0:
            if command.startswith("$(", index):
                depth += 1
                index += 2
                continue
            char = command[index]
            if char == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth == 0:
            substitution = command[start:index].strip()
            if substitution:
                substitutions.append(substitution)
        index += 1
    index = 0
    while index < len(command):
        if command[index] != "`":
            index += 1
            continue
        index += 1
        start = index
        escaped = False
        while index < len(command):
            char = command[index]
            if char == "\\" and not escaped:
                escaped = True
                index += 1
                continue
            if char == "`" and not escaped:
                substitution = command[start:index].strip()
                if substitution:
                    substitutions.append(substitution)
                break
            escaped = False
            index += 1
        index += 1
    return substitutions


def _effective_command_segments(command: str) -> list[str]:
    tokens = _command_tokens(command)
    segments: list[str] = []
    for substitution in _raw_command_substitutions(command):
        segments.extend(_effective_command_segments(substitution))
    for segment_tokens in _command_segments(tokens):
        embedded = _embedded_shell_commands(segment_tokens)
        if embedded:
            for embedded_command in embedded:
                segments.extend(_effective_command_segments(embedded_command))
            continue
        segment = " ".join(segment_tokens).strip()
        if segment:
            segments.append(segment)
    if not segments:
        normalized = _normalized_command(command)
        if normalized:
            segments.append(normalized)
    return segments


def _single_command_invokes(command: str, invocation: str) -> bool:
    tokens = _command_tokens(command)
    invocation_tokens = _normalized_command(invocation).split()
    if invocation_tokens[:1] == ["git"] and len(invocation_tokens) == 2:
        return _git_invokes(tokens, invocation_tokens[1])
    elif invocation_tokens[:1] == ["gh"] and len(invocation_tokens) > 1:
        return _gh_invokes(tokens, invocation_tokens[1:])
    return tokens[: len(invocation_tokens)] == invocation_tokens


def _command_invokes(command: str, invocation: str) -> bool:
    return any(_single_command_invokes(segment, invocation) for segment in _effective_command_segments(command))


def _command_allowed_by_policy(command: str, allowed_commands: list[str]) -> bool:
    segments = _effective_command_segments(command)
    if not segments:
        return False
    return all(
        any(_single_command_invokes(segment, allowed) for allowed in allowed_commands)
        for segment in segments
    )


def _successful_result_has_evidence(commands_run: list[Any], validation_results: list[Any]) -> tuple[bool, str]:
    if not validation_results:
        return False, "executor result successful status requires validation evidence"
    if not commands_run:
        return False, "executor result successful status requires command evidence"
    return True, "ok"


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
    allowed_commands: list[str] | None = None,
    denied_commands: list[str] | None = None,
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
        "command_policy": {
            "allowed_commands": list(allowed_commands or []),
            "denied_commands": list(denied_commands or []),
        },
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
    if packet.get("protocol_version") != PROTOCOL_VERSION:
        return False, "executor task protocol_version is invalid"
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
    for field in ("name", "path", "branch", "head"):
        if not _non_empty_string(repo.get(field)):
            return False, f"executor task repo.{field} is required"
    snapshot = packet.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("id") is None:
        return False, "executor task snapshot with id is required"
    valid_snapshot, snapshot_reason = validate_repo_snapshot(snapshot)
    if not valid_snapshot:
        return False, f"executor task snapshot is invalid: {snapshot_reason}"
    if snapshot.get("repo") != repo["name"]:
        return False, "executor task snapshot repo must match repo.name"
    snapshot_cwd = _normalized_filesystem_path(snapshot.get("cwd"))
    repo_path = _normalized_filesystem_path(repo.get("path"))
    if snapshot_cwd is None or repo_path is None:
        return False, "executor task snapshot cwd and repo.path must be absolute local paths"
    if snapshot_cwd != repo_path:
        return False, "executor task snapshot cwd must match repo.path"
    if snapshot.get("branch") != repo["branch"]:
        return False, "executor task snapshot branch must match repo.branch"
    if snapshot.get("head") != repo["head"]:
        return False, "executor task snapshot head must match repo.head"
    if snapshot.get("dirty_worktree") is not False:
        return False, "executor task snapshot dirty_worktree must be false"
    if snapshot.get("repo_confidence") == "low":
        return False, "executor task snapshot repo_confidence must not be low"
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
    stop_conditions = packet.get("stop_conditions")
    if not _is_string_list(stop_conditions) or not stop_conditions:
        return False, "executor task stop_conditions must be a non-empty list of strings"
    if not all(condition in stop_conditions for condition in DEFAULT_EXECUTOR_STOP_CONDITIONS):
        return False, "executor task stop_conditions must include built-in safety stops"
    command_policy = packet.get("command_policy", {})
    if command_policy is None:
        command_policy = {}
    if not isinstance(command_policy, dict):
        return False, "executor task command_policy must be a JSON object"
    for field in ("allowed_commands", "denied_commands"):
        commands = command_policy.get(field, [])
        if not _is_string_list(commands):
            return False, f"executor task command_policy.{field} must be a list of strings"
    expected_output = packet.get("expected_output")
    if not isinstance(expected_output, dict):
        return False, "executor task expected_output must be a JSON object"
    if expected_output.get("schema_version") != EXECUTOR_RESULT_SCHEMA_VERSION:
        return False, "executor task expected_output.schema_version is invalid"
    evidence_path = expected_output.get("evidence_path")
    if not _non_empty_string(evidence_path):
        return False, "executor task expected_output.evidence_path is required"
    if _normalized_filesystem_path(evidence_path) is None:
        return False, "executor task expected_output.evidence_path must be absolute"
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
    if (ended_at - started_at).total_seconds() > task_packet["limits"]["max_minutes"] * 60:
        return False, "executor result elapsed time exceeds task limit"
    status = evidence.get("status")
    if status not in EXECUTOR_STATUSES:
        return False, "executor result status is invalid"
    permissions = task_packet["permissions"]
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
    command_policy = task_packet.get("command_policy", {})
    if command_policy is None:
        command_policy = {}
    allowed_commands = command_policy.get("allowed_commands", []) if isinstance(command_policy, dict) else []
    denied_commands = command_policy.get("denied_commands", []) if isinstance(command_policy, dict) else []
    command_exit_codes: dict[str, list[int]] = {}
    for index, command in enumerate(commands_run):
        if not isinstance(command, dict):
            return False, f"executor result commands_run[{index}] must be a JSON object"
        command_text = command.get("command")
        if not _non_empty_string(command_text):
            return False, f"executor result commands_run[{index}].command is required"
        exit_code = command.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            return False, f"executor result commands_run[{index}].exit_code must be an integer"
        if any(_command_invokes(command_text, denied) for denied in denied_commands):
            return False, f"executor result commands_run[{index}] is denied by command_policy"
        if allowed_commands and not _command_allowed_by_policy(command_text, allowed_commands):
            return False, f"executor result commands_run[{index}] is outside allowed command_policy"
        if permissions.get("may_commit") is False and _command_invokes(command_text, "git commit"):
            return False, f"executor result commands_run[{index}] violates disabled commit permission"
        if permissions.get("may_push") is False and _command_invokes(command_text, "git push"):
            return False, f"executor result commands_run[{index}] violates disabled push permission"
        if permissions.get("may_open_pr") is False and _command_invokes(command_text, "gh pr create"):
            return False, f"executor result commands_run[{index}] violates disabled PR creation permission"
        command_exit_codes.setdefault(command_text, []).append(exit_code)
    validation_results = evidence.get("validation_results")
    if not isinstance(validation_results, list):
        return False, "executor result validation_results must be a list"
    validation_statuses_by_command: dict[str, list[str]] = {}
    for index, result in enumerate(validation_results):
        if not isinstance(result, dict):
            return False, f"executor result validation_results[{index}] must be a JSON object"
        if not _non_empty_string(result.get("name")):
            return False, f"executor result validation_results[{index}].name is required"
        if result.get("status") not in {"passed", "failed", "skipped"}:
            return False, f"executor result validation_results[{index}].status is invalid"
        if _non_empty_string(result.get("command")):
            validation_statuses_by_command.setdefault(result["command"], []).append(result["status"])
    if status == "succeeded":
        if not task_packet["required_checks"]:
            has_evidence, evidence_reason = _successful_result_has_evidence(commands_run, validation_results)
            if not has_evidence:
                return False, evidence_reason
        for required_check in task_packet["required_checks"]:
            exit_codes = command_exit_codes.get(required_check)
            if not exit_codes:
                return False, f"executor result missing required check command: {required_check}"
            if not any(exit_code == 0 for exit_code in exit_codes):
                return False, f"executor result required check command failed: {required_check}"
            validation_statuses = validation_statuses_by_command.get(required_check)
            if not validation_statuses:
                return False, f"executor result missing required check validation: {required_check}"
            if "passed" not in validation_statuses:
                return False, f"executor result required check validation did not pass: {required_check}"
        if any(result.get("status") != "passed" for result in validation_results):
            return False, "executor result successful status requires all validation_results to pass"
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
    if evidence.get("status") == "succeeded" and resulting_head is None:
        return False, "executor result resulting_head is required when status is succeeded"
    if resulting_head is not None and not _non_empty_string(resulting_head):
        return False, "executor result resulting_head must be a string or null"
    if permissions.get("may_commit") is False and resulting_head is not None and resulting_head != task_packet["repo"]["head"]:
        return False, "executor result resulting_head must match task repo head when commits are forbidden"
    return True, "ok"
