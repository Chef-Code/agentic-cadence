from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.branch_policy import normalize_branch_policy
from codex_cadence.executor_contract import checksum_json, validate_executor_command, validate_executor_task_packet
from codex_cadence.epochs import read_active_epoch_records
from codex_cadence.ownership import (
    DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    ownership_record_summary,
    validate_work_ownership,
)
from codex_cadence.repo_state import current_repo_evidence, git_repo_root, path_is_relative_to
from codex_cadence.roles import ROLE_READINESS_SCHEMA_VERSION
from codex_cadence.store import brake_path, read_json, utc_now

EXECUTOR_INVOCATION_READINESS_SCHEMA_VERSION = "executor-invocation-readiness.v1"


def readiness_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _expand_input_path(value: str | Path, *, code: str, message: str) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        return Path(value).expanduser(), None
    except (OSError, RuntimeError, ValueError) as exc:
        return None, readiness_blocker(code, f"{message}: {exc}", path=str(value))


def _resolve_input_path(path: Path, *, code: str, message: str) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        return path.resolve(strict=False), None
    except (OSError, RuntimeError, ValueError) as exc:
        return None, readiness_blocker(code, f"{message}: {exc}", path=str(path))


def _read_json_object(
    path: Path,
    *,
    code: str,
    label: str,
    invalid_code: str | None = None,
) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [readiness_blocker(code, f"{label} could not be read: {exc}", path=str(path))]
    if not isinstance(payload, dict):
        invalid = invalid_code or (code.replace("_unreadable", "_invalid") if code.endswith("_unreadable") else code)
        return None, [readiness_blocker(invalid, f"{label} must be a JSON object", path=str(path))]
    return payload, []


def _task_validation_blocker(reason: str) -> dict[str, Any]:
    if "command_policy" in reason:
        return readiness_blocker("command_policy_invalid", reason)
    if "branch_policy" in reason:
        return readiness_blocker("branch_policy_invalid", reason)
    if "required_checks" in reason:
        return readiness_blocker("required_checks_invalid", reason)
    if "expected_output" in reason:
        return readiness_blocker("result_path_invalid", reason)
    return readiness_blocker("executor_task_invalid", reason)


def _command_policy_blockers(task_packet: dict[str, Any]) -> list[dict[str, Any]]:
    command_policy = task_packet.get("command_policy")
    if not isinstance(command_policy, dict):
        return [readiness_blocker("command_policy_invalid", "executor task command_policy must be a JSON object")]
    blockers: list[dict[str, Any]] = []
    for field in ("allowed_commands", "denied_commands"):
        commands = command_policy.get(field, [])
        if not isinstance(commands, list) or any(not isinstance(command, str) or not command.strip() for command in commands):
            blockers.append(
                readiness_blocker(
                    "command_policy_invalid",
                    f"executor task command_policy.{field} must be a list of strings",
                    field=f"command_policy.{field}",
                )
            )
    return blockers


def _branch_policy_blockers(
    task_packet: dict[str, Any],
    *,
    current_branch: str | None = None,
) -> list[dict[str, Any]]:
    try:
        branch_policy = normalize_branch_policy(
            task_packet.get("branch_policy"),
            label="executor readiness branch_policy",
            require_object=True,
        )
    except ValueError as exc:
        return [readiness_blocker("branch_policy_invalid", str(exc))]
    blockers: list[dict[str, Any]] = []
    if branch_policy.get("allow_current_branch_main") is False and current_branch == "main":
        blockers.append(
            readiness_blocker(
                "branch_policy_current_branch_main_disallowed",
                "current branch is main and branch_policy does not allow real executor invocation from main",
                current_branch=current_branch,
            )
        )
    return blockers


def _required_check_blockers(task_packet: dict[str, Any]) -> list[dict[str, Any]]:
    required_checks = task_packet.get("required_checks")
    if not isinstance(required_checks, list) or any(
        not isinstance(check, str) or not check.strip() for check in required_checks
    ):
        return [readiness_blocker("required_checks_invalid", "executor task required_checks must be a list of strings")]
    if not required_checks:
        return [
            readiness_blocker(
                "required_checks_missing",
                "real executor invocation readiness requires at least one required check",
            )
        ]
    valid_task, _task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return []
    blockers: list[dict[str, Any]] = []
    for check in required_checks:
        valid_check, check_reason = validate_executor_command(check, task_packet)
        if not valid_check:
            blockers.append(
                readiness_blocker(
                    "required_checks_invalid",
                    f"executor task required check is not allowed by the task command policy: {check_reason}",
                    check=check,
                )
            )
    return blockers


def _read_brake_packet(root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path = brake_path(root)
    brake, blockers = _read_json_object(path, code="brake_state_invalid", label="cadence brake state")
    if blockers:
        return None, blockers
    status = brake.get("status") if isinstance(brake, dict) else None
    if status not in {"DRIVE", "NEUTRAL", "PARK"}:
        return brake, [
            readiness_blocker(
                "brake_state_invalid",
                "cadence brake status is invalid",
                brake_status=status,
                path=str(path),
            )
        ]
    if status != "DRIVE":
        return brake, [
            readiness_blocker(
                "brake_not_drive",
                f"cadence brake is {status}; real executor readiness requires DRIVE",
                brake_status=status,
            )
        ]
    return brake, []


def _repo_blockers(
    *,
    cwd: Path,
    task_packet: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repo_packet = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    blockers: list[dict[str, Any]] = []
    requested_cwd = Path(cwd).expanduser().resolve(strict=False)
    repo_root = git_repo_root(cwd)
    inspected_cwd = repo_root if repo_root is not None else requested_cwd
    try:
        repository = current_repo_evidence(inspected_cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "cwd": str(inspected_cwd),
            "requested_cwd": str(requested_cwd),
            "branch": None,
            "head": None,
            "dirty_worktree": None,
        }, [readiness_blocker("repo_inspection_failed", f"current repo could not be inspected: {exc}", path=str(cwd))]
    repository["requested_cwd"] = str(requested_cwd)

    expected_path: Path | None = None
    expected_path_value = repo_packet.get("path")
    if isinstance(expected_path_value, str):
        expanded_expected_path, path_blocker = _expand_input_path(
            expected_path_value,
            code="repo_path_invalid",
            message="executor task repo.path could not be parsed",
        )
        if path_blocker is not None:
            blockers.append(path_blocker)
        elif expanded_expected_path is not None:
            expected_path, path_blocker = _resolve_input_path(
                expanded_expected_path,
                code="repo_path_invalid",
                message="executor task repo.path could not be resolved",
            )
            if path_blocker is not None:
                blockers.append(path_blocker)
    if not isinstance(expected_path_value, str):
        blockers.append(readiness_blocker("repo_path_invalid", "executor task repo.path is required"))
    actual_path, actual_path_blocker = _resolve_input_path(
        Path(repository.get("cwd")).expanduser(),
        code="repo_inspection_failed",
        message="current repo path could not be resolved",
    )
    if actual_path_blocker is not None:
        blockers.append(actual_path_blocker)
    elif expected_path is not None and actual_path != expected_path:
        blockers.append(
            readiness_blocker(
                "repo_path_mismatch",
                "current repo path does not match executor task repo.path",
                expected=str(expected_path),
                actual=str(actual_path),
            )
        )
    if repository.get("branch") != repo_packet.get("branch"):
        blockers.append(
            readiness_blocker(
                "repo_branch_mismatch",
                "current branch does not match executor task repo.branch",
                expected=repo_packet.get("branch"),
                actual=repository.get("branch"),
            )
        )
    if repository.get("head") != repo_packet.get("head"):
        blockers.append(
            readiness_blocker(
                "repo_head_mismatch",
                "current HEAD does not match executor task repo.head",
                expected=repo_packet.get("head"),
                actual=repository.get("head"),
            )
        )
    if repository.get("dirty_worktree") is not False:
        blockers.append(
            readiness_blocker(
                "dirty_worktree",
                "current worktree must be clean before real executor invocation readiness",
            )
        )
    return repository, blockers


def _active_epoch_summary(epoch: Any, path: Path | None) -> dict[str, Any] | None:
    if not isinstance(epoch, dict):
        return None
    tasks = epoch.get("tasks") if isinstance(epoch.get("tasks"), list) else []
    return {
        "path": str(path) if path else None,
        "id": epoch.get("id"),
        "status": epoch.get("status"),
        "repo": epoch.get("repo"),
        "branch": epoch.get("branch"),
        "task_ids": [task.get("id") for task in tasks if isinstance(task, dict)],
    }


def _epoch_blockers(
    *,
    root: Path,
    epoch_id: str,
    task_packet: dict[str, Any],
    task_checksum: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        active_epochs = read_active_epoch_records(root)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return None, [readiness_blocker("active_epoch_invalid", str(exc))]
    if not active_epochs:
        return None, [readiness_blocker("active_epoch_missing", "one active epoch is required before real executor invocation")]
    if len(active_epochs) > 1:
        return None, [
            readiness_blocker(
                "active_epoch_conflict",
                "expected exactly one active epoch before real executor invocation",
                active_epoch_count=len(active_epochs),
            )
        ]

    path, epoch = active_epochs[0]
    blockers: list[dict[str, Any]] = []
    if epoch.get("id") != epoch_id:
        blockers.append(
            readiness_blocker(
                "active_epoch_id_mismatch",
                "active epoch id does not match requested epoch id",
                expected=epoch_id,
                actual=epoch.get("id"),
            )
        )
    if epoch.get("status") != "ACTIVE":
        blockers.append(
            readiness_blocker(
                "active_epoch_status_invalid",
                "active epoch status must be ACTIVE",
                actual_status=epoch.get("status"),
            )
        )
    repo_packet = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    if epoch.get("repo") != repo_packet.get("name"):
        blockers.append(
            readiness_blocker(
                "active_epoch_repo_mismatch",
                "active epoch repo does not match executor task repo.name",
                expected=repo_packet.get("name"),
                actual=epoch.get("repo"),
            )
        )
    if epoch.get("branch") != repo_packet.get("branch"):
        blockers.append(
            readiness_blocker(
                "active_epoch_branch_mismatch",
                "active epoch branch does not match executor task repo.branch",
                expected=repo_packet.get("branch"),
                actual=epoch.get("branch"),
            )
        )
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    task_id = task.get("id")
    epoch_tasks = epoch.get("tasks") if isinstance(epoch.get("tasks"), list) else []
    matching_task = next(
        (candidate for candidate in epoch_tasks if isinstance(candidate, dict) and candidate.get("id") == task_id),
        None,
    )
    if matching_task is None:
        blockers.append(
            readiness_blocker(
                "active_epoch_task_missing",
                "active epoch does not include the executor task id",
                task_id=task_id,
            )
        )
    else:
        epoch_checksum = matching_task.get("executor_task_checksum")
        if not isinstance(epoch_checksum, str) or not epoch_checksum.strip():
            blockers.append(
                readiness_blocker(
                    "task_checksum_missing",
                    "active epoch task is missing executor_task_checksum",
                    task_id=task_id,
                )
            )
        elif epoch_checksum != task_checksum:
            blockers.append(
                readiness_blocker(
                    "task_checksum_mismatch",
                    "active epoch task checksum does not match reviewed executor task packet",
                    expected=task_checksum,
                    actual=epoch_checksum,
                    task_id=task_id,
                )
            )
    return _active_epoch_summary(epoch, path), blockers


def _result_path_blockers(
    *,
    root: Path,
    task_packet: dict[str, Any],
    expected_result_path: str | Path | None,
) -> list[dict[str, Any]]:
    if expected_result_path is None:
        return [readiness_blocker("result_path_missing", "expected result path is required")]
    supplied_path, path_blocker = _expand_input_path(
        expected_result_path,
        code="result_path_invalid",
        message="expected result path could not be parsed",
    )
    if path_blocker is not None or supplied_path is None:
        return [path_blocker] if path_blocker is not None else [
            readiness_blocker("result_path_invalid", "expected result path is invalid")
        ]
    if not supplied_path.is_absolute():
        return [
            readiness_blocker(
                "result_path_invalid",
                "expected result path must be absolute",
                path=str(expected_result_path),
            )
        ]
    supplied_resolved, path_blocker = _resolve_input_path(
        supplied_path,
        code="result_path_invalid",
        message="expected result path could not be resolved",
    )
    if path_blocker is not None or supplied_resolved is None:
        return [path_blocker] if path_blocker is not None else [
            readiness_blocker("result_path_invalid", "expected result path is invalid")
        ]
    expected_output = task_packet.get("expected_output") if isinstance(task_packet.get("expected_output"), dict) else {}
    packet_path_value = expected_output.get("evidence_path")
    if not isinstance(packet_path_value, str) or not packet_path_value.strip():
        return [readiness_blocker("result_path_invalid", "executor task expected_output.evidence_path is required")]
    packet_path, path_blocker = _expand_input_path(
        packet_path_value,
        code="result_path_invalid",
        message="executor task expected_output.evidence_path could not be parsed",
    )
    if path_blocker is not None or packet_path is None:
        return [path_blocker] if path_blocker is not None else [
            readiness_blocker("result_path_invalid", "executor task expected_output.evidence_path is invalid")
        ]
    if not packet_path.is_absolute():
        return [readiness_blocker("result_path_invalid", "executor task expected_output.evidence_path must be absolute")]
    packet_resolved, path_blocker = _resolve_input_path(
        packet_path,
        code="result_path_invalid",
        message="executor task expected_output.evidence_path could not be resolved",
    )
    if path_blocker is not None or packet_resolved is None:
        return [path_blocker] if path_blocker is not None else [
            readiness_blocker("result_path_invalid", "executor task expected_output.evidence_path is invalid")
        ]
    blockers: list[dict[str, Any]] = []
    if packet_resolved != supplied_resolved:
        blockers.append(
            readiness_blocker(
                "result_path_mismatch",
                "supplied expected result path does not match executor task expected_output.evidence_path",
                expected=str(packet_resolved),
                actual=str(supplied_resolved),
            )
        )
    logical_result_dir = root / "executor-results"
    try:
        runtime_result_dir = logical_result_dir.resolve(strict=False)
        if logical_result_dir.is_symlink():
            blockers.append(
                readiness_blocker(
                    "result_path_outside_runtime",
                    "runtime executor-results directory must not be a symlink",
                    path=str(logical_result_dir),
                )
            )
    except OSError as exc:
        blockers.append(
            readiness_blocker(
                "result_path_invalid",
                f"runtime executor-results directory could not be inspected: {exc}",
                path=str(logical_result_dir),
            )
        )
    if not path_is_relative_to(packet_resolved, runtime_result_dir):
        blockers.append(
            readiness_blocker(
                "result_path_outside_runtime",
                "executor result path must stay under the runtime executor-results directory",
                expected_directory=str(runtime_result_dir),
                actual=str(packet_resolved),
            )
        )
    if not path_is_relative_to(supplied_resolved, runtime_result_dir):
        blockers.append(
            readiness_blocker(
                "result_path_outside_runtime",
                "supplied expected result path must stay under the runtime executor-results directory",
                expected_directory=str(runtime_result_dir),
                actual=str(supplied_resolved),
            )
        )
    try:
        if packet_resolved.exists() and not packet_resolved.is_file():
            blockers.append(readiness_blocker("result_path_invalid", "executor result path must be a file"))
    except OSError as exc:
        blockers.append(readiness_blocker("result_path_invalid", f"executor result path could not be inspected: {exc}"))
    return blockers


def _ownership_blockers(
    *,
    root: Path,
    cwd: Path,
    target: str | None,
    task_packet: dict[str, Any],
    epoch_id: str,
    max_age_minutes: int | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(target, str) or not target.strip():
        return None, [readiness_blocker("ownership_record_missing", "active ownership evidence target is required")]
    repo_packet = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    validation = validate_work_ownership(
        root=root,
        target=target,
        cwd=cwd,
        repo=repo_packet.get("name"),
        branch=repo_packet.get("branch"),
        task_id=task.get("id"),
        require_active=True,
        max_age_minutes=max_age_minutes,
    )
    blockers = [blocker for blocker in validation.get("blockers", []) if isinstance(blocker, dict)]
    summary = validation.get("record") if isinstance(validation.get("record"), dict) else None
    path = Path(summary["path"]) if isinstance(summary, dict) and isinstance(summary.get("path"), str) else None
    record: Any | None = None
    if path is not None and not blockers:
        record, read_blockers = _read_json_object(
            path,
            code="ownership_record_unreadable",
            label="work ownership record",
        )
        blockers.extend(read_blockers)
    if isinstance(record, dict):
        if record.get("candidate_id") != task.get("id"):
            blockers.append(
                readiness_blocker(
                    "ownership_candidate_mismatch",
                    "work ownership candidate_id does not match executor task id",
                    expected_candidate_id=task.get("id"),
                    actual_candidate_id=record.get("candidate_id"),
                    path=str(path) if path else None,
                )
            )
        if record.get("epoch_id") != epoch_id:
            blockers.append(
                readiness_blocker(
                    "ownership_epoch_mismatch",
                    "work ownership epoch_id does not match active epoch id",
                    expected_epoch_id=epoch_id,
                    actual_epoch_id=record.get("epoch_id"),
                    path=str(path) if path else None,
                )
            )
        if record.get("head") != repo_packet.get("head"):
            blockers.append(
                readiness_blocker(
                    "ownership_head_mismatch",
                    "work ownership head does not match executor task repo.head",
                    expected_head=repo_packet.get("head"),
                    actual_head=record.get("head"),
                    path=str(path) if path else None,
                )
            )
        summary = ownership_record_summary(record, path, "active")
    return summary, blockers


def _role_readiness_summary(packet: Any, path: Path | None) -> dict[str, Any]:
    if not isinstance(packet, dict):
        return {
            "path": str(path) if path else None,
            "present": packet is not None,
            "checksum": None,
            "valid": None,
            "role_ready": None,
            "recommended_next_action": None,
            "blockers": [],
        }
    return {
        "path": str(path) if path else None,
        "present": True,
        "checksum": checksum_json(packet),
        "valid": packet.get("valid"),
        "role_ready": packet.get("role_ready"),
        "recommended_next_action": packet.get("recommended_next_action"),
        "blockers": packet.get("blockers") if isinstance(packet.get("blockers"), list) else [],
    }


def _role_readiness_blockers(
    *,
    role_readiness_file: Path | None,
    task_packet: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if role_readiness_file is None:
        return _role_readiness_summary(None, None), []
    packet, blockers = _read_json_object(
        role_readiness_file,
        code="role_readiness_unreadable",
        label="role readiness evidence",
    )
    if blockers:
        return _role_readiness_summary(packet, role_readiness_file), blockers
    role_blockers: list[dict[str, Any]] = []
    if (
        packet.get("protocol_version") != PROTOCOL_VERSION
        or packet.get("schema_version") != ROLE_READINESS_SCHEMA_VERSION
        or packet.get("packet") != "role_readiness"
    ):
        role_blockers.append(
            readiness_blocker(
                "role_readiness_invalid",
                "role readiness evidence must be a current role-readiness.v1 packet",
                path=str(role_readiness_file),
            )
        )
    if packet.get("read_only") is not True:
        role_blockers.append(
            readiness_blocker(
                "role_readiness_invalid",
                "role readiness evidence must be read-only",
                path=str(role_readiness_file),
            )
        )
    if packet.get("side_effects") != []:
        role_blockers.append(
            readiness_blocker(
                "role_readiness_invalid",
                "role readiness evidence must report side_effects as an empty list",
                path=str(role_readiness_file),
            )
        )
    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    repo_packet = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    for field, expected in (
        ("repo", repo_packet.get("name")),
        ("branch", repo_packet.get("branch")),
        ("task_id", task.get("id")),
    ):
        actual = scope.get(field)
        if expected is not None and actual != expected:
            role_blockers.append(
                readiness_blocker(
                    "role_readiness_scope_mismatch",
                    f"role readiness {field} does not match executor task",
                    field=field,
                    expected=expected,
                    actual=actual,
                    path=str(role_readiness_file),
                )
            )
    if packet.get("valid") is not True or packet.get("role_ready") is not True:
        role_blockers.append(
            readiness_blocker(
                "role_readiness_blocked",
                "role readiness evidence is not ready for real executor invocation",
                path=str(role_readiness_file),
                role_blockers=packet.get("blockers") if isinstance(packet.get("blockers"), list) else [],
            )
        )
    return _role_readiness_summary(packet, role_readiness_file), role_blockers


def _recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "invoke_real_executor"
    codes = {blocker.get("code") for blocker in blockers}
    if codes & {
        "executor_task_invalid",
        "repo_path_invalid",
        "repo_path_mismatch",
        "repo_branch_mismatch",
        "repo_head_mismatch",
        "task_checksum_missing",
        "task_checksum_mismatch",
        "task_file_unreadable",
    }:
        return "refresh_task_evidence"
    if codes & {
        "ownership_record_missing",
        "ownership_record_unreadable",
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_field_type_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
        "ownership_required_field_missing",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_registry_state_invalid",
        "ownership_repo_mismatch",
        "ownership_branch_mismatch",
        "ownership_task_mismatch",
        "ownership_candidate_mismatch",
        "ownership_epoch_mismatch",
        "ownership_head_mismatch",
        "ownership_closed",
        "ownership_stale",
        "duplicate_active_ownership",
    }:
        return "fix_ownership"
    if codes & {
        "active_epoch_missing",
        "active_epoch_conflict",
        "active_epoch_invalid",
        "active_epoch_id_mismatch",
        "active_epoch_status_invalid",
        "active_epoch_repo_mismatch",
        "active_epoch_branch_mismatch",
        "active_epoch_task_missing",
    }:
        return "close_or_fail_active_epoch"
    if codes & {
        "branch_policy_invalid",
        "branch_policy_current_branch_main_disallowed",
        "command_policy_invalid",
        "required_checks_invalid",
        "required_checks_missing",
        "result_path_invalid",
        "result_path_missing",
        "result_path_mismatch",
        "result_path_outside_runtime",
    }:
        return "inspect_policy_blockers"
    if codes & {
        "brake_not_drive",
        "brake_state_invalid",
        "dirty_worktree",
        "repo_inspection_failed",
        "role_readiness_blocked",
        "role_readiness_invalid",
        "role_readiness_scope_mismatch",
        "role_readiness_unreadable",
    }:
        return "operator_review"
    return "operator_review"


def _reason(valid: bool, blockers: list[dict[str, Any]]) -> str:
    if valid:
        return "real executor invocation readiness passed; executor not started"
    if blockers:
        return blockers[0]["message"]
    return "real executor invocation readiness blocked"


def evaluate_executor_invocation_readiness(
    *,
    root: Path,
    cwd: Path,
    task_file: Path,
    epoch_id: str,
    ownership_target: str | None,
    expected_result_path: str | Path | None,
    role_readiness_file: Path | None = None,
    max_ownership_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    task_file = Path(task_file)
    blockers: list[dict[str, Any]] = []

    task_packet: Any | None = None
    task_checksum: str | None = None
    task_packet, task_read_blockers = _read_json_object(
        task_file,
        code="task_file_unreadable",
        label="executor task packet",
        invalid_code="executor_task_invalid",
    )
    blockers.extend(task_read_blockers)
    if isinstance(task_packet, dict):
        task_checksum = checksum_json(task_packet)
        valid_task, task_reason = validate_executor_task_packet(task_packet)
        if not valid_task:
            blockers.append(_task_validation_blocker(task_reason))
        blockers.extend(_command_policy_blockers(task_packet))
        blockers.extend(_required_check_blockers(task_packet))
        blockers.extend(
            _result_path_blockers(
                root=root,
                task_packet=task_packet,
                expected_result_path=expected_result_path,
            )
        )

    repository: dict[str, Any] = {
        "cwd": str(cwd.expanduser().resolve(strict=False)),
        "branch": None,
        "head": None,
        "dirty_worktree": None,
    }
    active_epoch: dict[str, Any] | None = None
    ownership: dict[str, Any] | None = None
    role_readiness: dict[str, Any] = _role_readiness_summary(None, None)
    brake, brake_blockers = _read_brake_packet(root)
    blockers.extend(brake_blockers)

    if isinstance(task_packet, dict):
        repository, repo_blockers = _repo_blockers(cwd=cwd, task_packet=task_packet)
        blockers.extend(repo_blockers)
        current_branch = repository.get("branch") if isinstance(repository.get("branch"), str) else None
        blockers.extend(_branch_policy_blockers(task_packet, current_branch=current_branch))
        if task_checksum is not None:
            active_epoch, epoch_blockers = _epoch_blockers(
                root=root,
                epoch_id=epoch_id,
                task_packet=task_packet,
                task_checksum=task_checksum,
            )
            blockers.extend(epoch_blockers)
        ownership, ownership_blockers = _ownership_blockers(
            root=root,
            cwd=cwd,
            target=ownership_target,
            task_packet=task_packet,
            epoch_id=epoch_id,
            max_age_minutes=max_ownership_age_minutes,
        )
        blockers.extend(ownership_blockers)
        role_readiness, role_blockers = _role_readiness_blockers(
            role_readiness_file=role_readiness_file,
            task_packet=task_packet,
        )
        blockers.extend(role_blockers)

    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo_packet = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    command_policy = task_packet.get("command_policy") if isinstance(task_packet, dict) else None
    branch_policy = task_packet.get("branch_policy") if isinstance(task_packet, dict) else None
    expected_output = task_packet.get("expected_output") if isinstance(task_packet, dict) else None
    valid = not blockers
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTOR_INVOCATION_READINESS_SCHEMA_VERSION,
        "packet": "executor_invocation_readiness",
        "read_only": True,
        "checked_at": utc_now(),
        "valid": valid,
        "executor_invocation_ready": valid,
        "executor_started": False,
        "root": str(root),
        "cwd": str(cwd.expanduser().resolve(strict=False)),
        "task": {
            "file": str(task_file),
            "checksum": task_checksum,
            "id": task.get("id"),
            "schema_version": task_packet.get("schema_version") if isinstance(task_packet, dict) else None,
            "repo": {
                "name": repo_packet.get("name"),
                "path": repo_packet.get("path"),
                "branch": repo_packet.get("branch"),
                "head": repo_packet.get("head"),
            },
            "required_checks": list(task_packet.get("required_checks", [])) if isinstance(task_packet, dict) and isinstance(task_packet.get("required_checks"), list) else None,
            "expected_result_path": (
                expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
            ),
            "command_policy": command_policy if isinstance(command_policy, dict) else None,
            "branch_policy": branch_policy if isinstance(branch_policy, dict) else None,
        },
        "repository": {
            **repository,
            "expected_path": repo_packet.get("path"),
            "expected_branch": repo_packet.get("branch"),
            "expected_head": repo_packet.get("head"),
        },
        "brake": {
            "status": brake.get("status") if isinstance(brake, dict) else None,
            "path": str(brake_path(root)),
        },
        "active_epoch": active_epoch,
        "ownership": ownership,
        "role_readiness": role_readiness,
        "blockers": blockers,
        "recommended_next_action": _recommendation(blockers),
        "reason": _reason(valid, blockers),
        "side_effects": [],
        "limitations": [
            "read_only_preflight_only",
            "executor_not_started",
            "executor_process_metadata_out_of_scope",
            "executor_code_modification_out_of_scope",
            "branch_creation_commit_push_pr_merge_release_publish_out_of_scope",
        ],
    }
    return payload
