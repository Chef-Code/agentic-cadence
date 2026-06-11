from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.approvals import build_operator_approval_verification_packet, parse_approval_timestamp
from codex_cadence.epochs import read_active_epoch_records
from codex_cadence.executor_contract import (
    checksum_json,
    validate_executor_command,
    validate_executor_task_packet,
)
from codex_cadence.ownership import validate_work_ownership
from codex_cadence.policy_audit import replay_audit_log
from codex_cadence.repo_state import current_repo_evidence, git_repo_root, path_is_relative_to, runtime_root_safety_issue
from codex_cadence.store import atomic_write_json, brake_path, read_json, real_executor_invocation_path, utc_now

EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION = "executor-invocation-plan.v1"
EXECUTOR_INVOCATION_TARGET_SCHEMA_VERSION = "executor-invocation-target.v1"
EXECUTOR_ADAPTER_SCHEMA_VERSION = "executor-adapter.v1"
EXECUTOR_ROLLBACK_SCHEMA_VERSION = "executor-rollback.v1"
REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION = "real-executor-invocation.v1"
DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION = "dirty-worktree-fingerprint.v1"
MAX_READINESS_AGE_SECONDS = 15 * 60
MAX_INVOCATION_PLAN_AGE_SECONDS = 15 * 60
REAL_EXECUTOR_SIDE_EFFECT_MODES = ("evidence_only", "materialized_changes")
_GIT_EXECUTABLE: str | None = None
FORWARDED_OWNERSHIP_BLOCKER_CODES = (
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
    "duplicate_active_ownership",
)


def plan_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def invocation_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _read_json_object(path: Path, *, code: str, label: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        packet = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [plan_blocker(code, f"{label} could not be read: {exc}", path=str(path))]
    if not isinstance(packet, dict):
        return None, [plan_blocker(code, f"{label} must be a JSON object", path=str(path))]
    return packet, []


def _normal_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def executor_invocation_target_descriptor(
    *,
    readiness_packet: dict[str, Any],
    adapter_packet: dict[str, Any],
    rollback_packet: dict[str, Any],
    command: str,
    cwd: str | Path,
    expected_result_path: str | Path,
    environment_allowlist: list[str],
    timeout_seconds: int,
    audit_chain_head: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": EXECUTOR_INVOCATION_TARGET_SCHEMA_VERSION,
        "readiness_checksum": checksum_json(readiness_packet),
        "adapter_checksum": checksum_json(adapter_packet),
        "rollback_checksum": checksum_json(rollback_packet),
        "command": command,
        "cwd": _normal_path(cwd),
        "expected_result_path": _normal_path(expected_result_path),
        "environment_allowlist": list(environment_allowlist),
        "timeout_seconds": timeout_seconds,
        "audit_chain_head": audit_chain_head,
    }


def _readiness_blockers(readiness: dict[str, Any], *, now: datetime) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if readiness.get("schema_version") != "executor-invocation-readiness.v1" or readiness.get("packet") != "executor_invocation_readiness":
        blockers.append(
            plan_blocker(
                "readiness_not_invocable",
                "readiness evidence must be an executor-invocation-readiness.v1 packet",
            )
        )
    if readiness.get("read_only") is not True or readiness.get("side_effects") != []:
        blockers.append(plan_blocker("readiness_not_invocable", "readiness evidence must be read-only"))
    if readiness.get("valid") is not True or readiness.get("executor_invocation_ready") is not True:
        blockers.append(plan_blocker("readiness_not_invocable", "readiness evidence is not invocable"))
    checked_at = parse_approval_timestamp(readiness.get("checked_at"))
    if checked_at is None:
        blockers.append(plan_blocker("readiness_packet_stale", "readiness checked_at is invalid"))
    else:
        age_seconds = (now - checked_at).total_seconds()
        if age_seconds < 0 or age_seconds > MAX_READINESS_AGE_SECONDS:
            blockers.append(
                plan_blocker(
                    "readiness_packet_stale",
                    "readiness evidence is stale",
                    max_age_seconds=MAX_READINESS_AGE_SECONDS,
                )
            )
    return blockers


def _adapter_blockers(adapter: dict[str, Any], *, command: str, timeout_seconds: int, environment_allowlist: list[str]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if adapter.get("schema_version") != EXECUTOR_ADAPTER_SCHEMA_VERSION or adapter.get("packet") != "executor_adapter":
        blockers.append(plan_blocker("adapter_contract_invalid", "adapter evidence must be executor-adapter.v1"))
    if not _non_empty_string(adapter.get("adapter_id")):
        blockers.append(plan_blocker("adapter_contract_invalid", "adapter_id is required"))
    if adapter.get("adapter_kind") != "local_process":
        blockers.append(plan_blocker("adapter_contract_invalid", "adapter_kind must be local_process"))
    if adapter.get("command_template") != command:
        blockers.append(plan_blocker("adapter_contract_invalid", "adapter command_template must match requested command"))
    adapter_env = adapter.get("environment_allowlist")
    if not isinstance(adapter_env, list) or any(not _non_empty_string(value) for value in adapter_env):
        blockers.append(plan_blocker("adapter_contract_invalid", "adapter environment_allowlist must be a list of strings"))
    elif any(value not in adapter_env for value in environment_allowlist):
        blockers.append(plan_blocker("adapter_contract_invalid", "requested environment allowlist exceeds adapter allowlist"))
    max_timeout = adapter.get("max_timeout_seconds")
    if type(max_timeout) is not int or max_timeout <= 0 or timeout_seconds > max_timeout:
        blockers.append(plan_blocker("executor_timeout_invalid", "requested timeout exceeds adapter max_timeout_seconds"))
    if adapter.get("process_start_allowed") is not False:
        blockers.append(plan_blocker("adapter_contract_invalid", "planning adapter evidence must not allow process start"))
    return blockers


def _readiness_task_packet(
    readiness: dict[str, Any],
    readiness_file: Path,
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    task_summary = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
    task_file = task_summary.get("file")
    if not _non_empty_string(task_file):
        return None, None, [plan_blocker("task_file_unreadable", "readiness task file is required")]
    task_path = Path(task_file).expanduser()
    if not task_path.is_absolute():
        task_path = readiness_file.parent / task_path
    task_packet, blockers = _read_json_object(task_path, code="task_file_unreadable", label="executor task packet")
    if blockers or task_packet is None:
        return None, None, blockers

    task_checksum = checksum_json(task_packet)
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        blockers.append(plan_blocker("executor_task_invalid", task_reason))
    if task_checksum != task_summary.get("checksum"):
        blockers.append(
            plan_blocker(
                "task_checksum_mismatch",
                "current executor task packet checksum does not match readiness",
                expected=task_summary.get("checksum"),
                actual=task_checksum,
            )
        )
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    readiness_repo = task_summary.get("repo") if isinstance(task_summary.get("repo"), dict) else {}
    if task.get("id") != task_summary.get("id"):
        blockers.append(
            plan_blocker(
                "task_checksum_mismatch",
                "current executor task id does not match readiness",
                expected=task_summary.get("id"),
                actual=task.get("id"),
            )
        )
    for field in ("name", "path", "branch", "head"):
        if repo.get(field) != readiness_repo.get(field):
            blockers.append(
                plan_blocker(
                    "task_checksum_mismatch",
                    f"current executor task repo {field} does not match readiness",
                    expected=readiness_repo.get(field),
                    actual=repo.get(field),
                )
            )
    expected_output = task_packet.get("expected_output") if isinstance(task_packet.get("expected_output"), dict) else {}
    actual_result_path = expected_output.get("evidence_path")
    expected_result_path = task_summary.get("expected_result_path")
    if _non_empty_string(actual_result_path) and _non_empty_string(expected_result_path):
        result_path_matches_readiness = _normal_path(actual_result_path) == _normal_path(expected_result_path)
    else:
        result_path_matches_readiness = actual_result_path == expected_result_path
    if not result_path_matches_readiness:
        blockers.append(
            plan_blocker(
                "task_checksum_mismatch",
                "current executor task result path does not match readiness",
                expected=expected_result_path,
                actual=actual_result_path,
            )
        )
    return task_packet, task_checksum, blockers


def _rollback_blockers(rollback: dict[str, Any], *, readiness: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
    readiness_repo = task.get("repo") if isinstance(task.get("repo"), dict) else {}
    rollback_repo = rollback.get("repo") if isinstance(rollback.get("repo"), dict) else {}
    if rollback.get("schema_version") != EXECUTOR_ROLLBACK_SCHEMA_VERSION or rollback.get("packet") != "executor_rollback_evidence":
        blockers.append(plan_blocker("rollback_policy_invalid", "rollback evidence must be executor-rollback.v1"))
    if rollback.get("read_only") is not True or rollback.get("side_effects") != []:
        blockers.append(plan_blocker("rollback_policy_invalid", "rollback evidence must be read-only"))
    if rollback.get("task_checksum") != task.get("checksum"):
        blockers.append(plan_blocker("rollback_policy_invalid", "rollback evidence task checksum does not match readiness"))
    for field in ("path", "branch", "head"):
        if rollback_repo.get(field) != readiness_repo.get(field):
            blockers.append(
                plan_blocker(
                    "rollback_policy_invalid",
                    f"rollback repo {field} does not match readiness",
                    expected=readiness_repo.get(field),
                    actual=rollback_repo.get(field),
                )
            )
    if not _non_empty_string(rollback.get("strategy")):
        blockers.append(plan_blocker("rollback_policy_invalid", "rollback strategy is required"))
    commands = rollback.get("rollback_commands")
    if not isinstance(commands, list) or any(not _non_empty_string(command) for command in commands):
        blockers.append(plan_blocker("rollback_policy_invalid", "rollback_commands must be a list of strings"))
    return blockers


def _command_blockers(command: str, task_packet: dict[str, Any]) -> list[dict[str, Any]]:
    valid_command, reason = validate_executor_command(command, task_packet)
    if not valid_command:
        return [plan_blocker("executor_command_denied", reason)]
    return []


def _repo_recheck_blockers(*, cwd: Path, readiness: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    repo_root = git_repo_root(cwd) or cwd
    try:
        repository = current_repo_evidence(repo_root)
    except (OSError, RuntimeError, ValueError) as exc:
        return {"cwd": str(repo_root), "branch": None, "head": None, "dirty_worktree": None}, [
            plan_blocker("repo_inspection_failed", f"current repo could not be inspected: {exc}", path=str(cwd))
        ]
    repository["requested_cwd"] = _normal_path(cwd)
    expected_repo = readiness.get("repository") if isinstance(readiness.get("repository"), dict) else {}
    for field, code in (("cwd", "repo_path_mismatch"), ("branch", "repo_branch_mismatch"), ("head", "repo_head_mismatch")):
        if repository.get(field) != expected_repo.get(field):
            blockers.append(
                plan_blocker(
                    code,
                    f"current repo {field} does not match readiness",
                    expected=expected_repo.get(field),
                    actual=repository.get(field),
                )
            )
    if repository.get("dirty_worktree") is not False:
        blockers.append(plan_blocker("dirty_worktree", "current worktree must be clean before executor invocation planning"))
    return repository, blockers


def _brake_blockers(root: Path) -> list[dict[str, Any]]:
    brake, blockers = _read_json_object(brake_path(root), code="brake_state_invalid", label="cadence brake state")
    if blockers:
        return blockers
    if brake.get("status") != "DRIVE":
        return [plan_blocker("brake_not_drive", "cadence brake must be DRIVE for executor invocation planning")]
    return []


def _result_path_blockers(root: Path, expected_result_path: str | Path | None, task_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if expected_result_path is None:
        return [plan_blocker("result_path_missing", "expected result path is required")]
    blockers: list[dict[str, Any]] = []
    supplied_path = Path(expected_result_path).expanduser()
    if not supplied_path.is_absolute():
        return [plan_blocker("result_path_invalid", "expected result path must be absolute", path=str(expected_result_path))]
    try:
        supplied_resolved = supplied_path.resolve(strict=False)
    except OSError as exc:
        return [plan_blocker("result_path_invalid", f"expected result path could not be resolved: {exc}", path=str(expected_result_path))]

    expected_output = task_packet.get("expected_output") if isinstance(task_packet.get("expected_output"), dict) else {}
    packet_path_value = expected_output.get("evidence_path")
    if not _non_empty_string(packet_path_value):
        return [plan_blocker("result_path_invalid", "executor task expected_output.evidence_path is required")]
    packet_path = Path(packet_path_value).expanduser()
    if not packet_path.is_absolute():
        return [plan_blocker("result_path_invalid", "executor task expected_output.evidence_path must be absolute")]
    try:
        packet_resolved = packet_path.resolve(strict=False)
    except OSError as exc:
        return [plan_blocker("result_path_invalid", f"executor task expected_output.evidence_path could not be resolved: {exc}")]

    if packet_resolved != supplied_resolved:
        blockers.append(
            plan_blocker(
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
                plan_blocker(
                    "result_path_outside_runtime",
                    "runtime executor-results directory must not be a symlink",
                    path=str(logical_result_dir),
                )
            )
    except OSError as exc:
        blockers.append(
            plan_blocker(
                "result_path_invalid",
                f"runtime executor-results directory could not be inspected: {exc}",
                path=str(logical_result_dir),
            )
        )
        runtime_result_dir = logical_result_dir
    if not path_is_relative_to(packet_resolved, runtime_result_dir):
        blockers.append(
            plan_blocker(
                "result_path_outside_runtime",
                "executor result path must stay under the runtime executor-results directory",
                expected_directory=str(runtime_result_dir),
                actual=str(packet_resolved),
            )
        )
    if not path_is_relative_to(supplied_resolved, runtime_result_dir):
        blockers.append(
            plan_blocker(
                "result_path_outside_runtime",
                "supplied expected result path must stay under the runtime executor-results directory",
                expected_directory=str(runtime_result_dir),
                actual=str(supplied_resolved),
            )
        )
    try:
        if packet_resolved.exists() and not packet_resolved.is_file():
            blockers.append(plan_blocker("result_path_invalid", "executor result path must be a file"))
    except OSError as exc:
        blockers.append(plan_blocker("result_path_invalid", f"executor result path could not be inspected: {exc}"))
    return blockers


def _active_epoch_blockers(
    root: Path,
    readiness: dict[str, Any],
    task_packet: dict[str, Any],
    task_checksum: str,
) -> list[dict[str, Any]]:
    try:
        active_epochs = read_active_epoch_records(root)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return [plan_blocker("active_epoch_invalid", str(exc))]
    if not active_epochs:
        return [plan_blocker("active_epoch_missing", "one active epoch is required before executor invocation planning")]
    if len(active_epochs) > 1:
        return [
            plan_blocker(
                "active_epoch_conflict",
                "expected exactly one active epoch before executor invocation planning",
                active_epoch_count=len(active_epochs),
            )
        ]

    path, epoch = active_epochs[0]
    blockers: list[dict[str, Any]] = []
    active_epoch = readiness.get("active_epoch") if isinstance(readiness.get("active_epoch"), dict) else {}
    epoch_id = active_epoch.get("id")
    if not _non_empty_string(epoch_id):
        blockers.append(plan_blocker("active_epoch_mismatch", "readiness active_epoch id is required"))
    elif epoch.get("id") != epoch_id:
        blockers.append(
            plan_blocker(
                "active_epoch_mismatch",
                "active epoch id does not match readiness",
                expected=epoch_id,
                actual=epoch.get("id"),
                path=str(path),
            )
        )
    if epoch.get("status") != "ACTIVE":
        blockers.append(plan_blocker("active_epoch_mismatch", "active epoch status must be ACTIVE", actual_status=epoch.get("status"), path=str(path)))
    repo = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    if epoch.get("repo") != repo.get("name"):
        blockers.append(
            plan_blocker(
                "active_epoch_repo_mismatch",
                "active epoch repo does not match executor task repo.name",
                expected=repo.get("name"),
                actual=epoch.get("repo"),
            )
        )
    if epoch.get("branch") != repo.get("branch"):
        blockers.append(
            plan_blocker(
                "active_epoch_branch_mismatch",
                "active epoch branch does not match executor task repo.branch",
                expected=repo.get("branch"),
                actual=epoch.get("branch"),
            )
        )
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    task_id = task.get("id")
    epoch_tasks = epoch.get("tasks") if isinstance(epoch.get("tasks"), list) else []
    completed_tasks = epoch.get("completed_tasks") if isinstance(epoch.get("completed_tasks"), list) else []
    if task_id in completed_tasks:
        blockers.append(plan_blocker("active_epoch_task_completed", "active epoch task is already completed", task_id=task_id))
    task_ids = [
        candidate.get("id")
        for candidate in epoch_tasks
        if isinstance(candidate, dict) and _non_empty_string(candidate.get("id"))
    ]
    duplicate_task_ids = sorted({candidate_id for candidate_id in task_ids if task_ids.count(candidate_id) > 1})
    if duplicate_task_ids:
        blockers.append(
            plan_blocker(
                "active_epoch_task_duplicate",
                "active epoch includes duplicate task ids",
                task_ids=duplicate_task_ids,
            )
        )
    matching_tasks = [candidate for candidate in epoch_tasks if isinstance(candidate, dict) and candidate.get("id") == task_id]
    if not matching_tasks:
        blockers.append(plan_blocker("active_epoch_task_missing", "active epoch does not include the executor task id", task_id=task_id))
    elif len(matching_tasks) > 1:
        blockers.append(
            plan_blocker(
                "active_epoch_task_duplicate",
                "active epoch includes duplicate executor task ids",
                task_id=task_id,
                match_count=len(matching_tasks),
            )
        )
    else:
        matching_task = matching_tasks[0]
        epoch_checksum = matching_task.get("executor_task_checksum")
        if not _non_empty_string(epoch_checksum):
            blockers.append(plan_blocker("task_checksum_missing", "active epoch task is missing executor_task_checksum", task_id=task_id))
        elif epoch_checksum != task_checksum:
            blockers.append(
                plan_blocker(
                    "task_checksum_mismatch",
                    "active epoch task checksum does not match current executor task packet",
                    expected=task_checksum,
                    actual=epoch_checksum,
                    task_id=task_id,
                )
            )
    return blockers


def _ownership_blockers(root: Path, cwd: Path, readiness: dict[str, Any], task_packet: dict[str, Any]) -> list[dict[str, Any]]:
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    active_epoch = readiness.get("active_epoch") if isinstance(readiness.get("active_epoch"), dict) else {}
    ownership = readiness.get("ownership") if isinstance(readiness.get("ownership"), dict) else {}
    target = ownership.get("id") or ownership.get("path")
    if not _non_empty_string(target):
        return [plan_blocker("ownership_record_missing", "readiness ownership evidence target is required")]

    validation = validate_work_ownership(
        root=root,
        target=target,
        cwd=cwd,
        repo=repo.get("name"),
        branch=repo.get("branch"),
        task_id=task.get("id"),
        require_active=True,
    )
    blockers = [blocker for blocker in validation.get("blockers", []) if isinstance(blocker, dict)]
    record = validation.get("record") if isinstance(validation.get("record"), dict) else {}
    if record:
        if record.get("candidate_id") != task.get("id"):
            blockers.append(
                plan_blocker(
                    "ownership_candidate_mismatch",
                    "work ownership candidate_id does not match readiness task id",
                    expected_candidate_id=task.get("id"),
                    actual_candidate_id=record.get("candidate_id"),
                )
            )
        if record.get("epoch_id") != active_epoch.get("id"):
            blockers.append(
                plan_blocker(
                    "ownership_epoch_mismatch",
                    "work ownership epoch_id does not match readiness active epoch id",
                    expected_epoch_id=active_epoch.get("id"),
                    actual_epoch_id=record.get("epoch_id"),
                )
            )
        if record.get("head") != repo.get("head"):
            blockers.append(
                plan_blocker(
                    "ownership_head_mismatch",
                    "work ownership head does not match readiness repo head",
                    expected_head=repo.get("head"),
                    actual_head=record.get("head"),
                )
            )
    return blockers


def _recommendation(blockers: list[dict[str, Any]]) -> str:
    return "invoke_real_executor" if not blockers else "operator_review"


def _reason(valid: bool, blockers: list[dict[str, Any]]) -> str:
    if valid:
        return "executor invocation plan accepted; executor not started"
    if blockers:
        return blockers[0]["message"]
    return "executor invocation plan blocked"


def build_executor_invocation_plan(
    *,
    root: Path,
    cwd: Path,
    readiness_file: Path,
    approval_file: Path,
    approval_secret: str | bytes | None,
    adapter_file: Path,
    rollback_file: Path,
    command: str,
    environment_allowlist: list[str],
    timeout_seconds: int,
    expected_result_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(root)
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    blockers: list[dict[str, Any]] = []

    readiness, read_blockers = _read_json_object(readiness_file, code="readiness_unreadable", label="readiness evidence")
    adapter, adapter_read_blockers = _read_json_object(adapter_file, code="adapter_contract_invalid", label="adapter evidence")
    rollback, rollback_read_blockers = _read_json_object(rollback_file, code="rollback_evidence_missing", label="rollback evidence")
    blockers.extend(read_blockers)
    blockers.extend(adapter_read_blockers)
    blockers.extend(rollback_read_blockers)

    audit_replay = replay_audit_log(root)
    if audit_replay.get("valid") is not True:
        blockers.append(plan_blocker("audit_chain_not_clean", "audit replay must be clean before executor invocation planning"))

    repository: dict[str, Any] = {"cwd": _normal_path(cwd), "branch": None, "head": None, "dirty_worktree": None}
    target: dict[str, Any] | None = None
    task_packet: dict[str, Any] | None = None
    task_checksum: str | None = None
    approval_packet: Any | None = None
    approval_result: dict[str, Any] | None = None

    if readiness is not None:
        blockers.extend(_readiness_blockers(readiness, now=checked_at))
        task_packet, task_checksum, task_blockers = _readiness_task_packet(readiness, Path(readiness_file))
        blockers.extend(task_blockers)
        repository, repo_blockers = _repo_recheck_blockers(cwd=Path(cwd), readiness=readiness)
        blockers.extend(repo_blockers)
        blockers.extend(_brake_blockers(root))
        if task_packet is not None and task_checksum is not None:
            blockers.extend(_active_epoch_blockers(root, readiness, task_packet, task_checksum))
            blockers.extend(_ownership_blockers(root, Path(cwd), readiness, task_packet))
            blockers.extend(_result_path_blockers(root, expected_result_path, task_packet))
            blockers.extend(_command_blockers(command, task_packet))

    if adapter is not None:
        blockers.extend(
            _adapter_blockers(
                adapter,
                command=command,
                timeout_seconds=timeout_seconds,
                environment_allowlist=environment_allowlist,
            )
        )
    if rollback is not None and readiness is not None:
        blockers.extend(_rollback_blockers(rollback, readiness=readiness))

    if timeout_seconds <= 0:
        blockers.append(plan_blocker("executor_timeout_invalid", "executor timeout must be positive"))

    if readiness is not None and adapter is not None and rollback is not None:
        target = executor_invocation_target_descriptor(
            readiness_packet=readiness,
            adapter_packet=adapter,
            rollback_packet=rollback,
            command=command,
            cwd=cwd,
            expected_result_path=expected_result_path,
            environment_allowlist=environment_allowlist,
            timeout_seconds=timeout_seconds,
            audit_chain_head=audit_replay.get("chain_head"),
        )
        try:
            approval_packet = read_json(approval_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(plan_blocker("approval_missing", f"approval evidence could not be read: {exc}", path=str(approval_file)))
        else:
            approval_result = build_operator_approval_verification_packet(
                approval=approval_packet,
                approval_file=approval_file,
                expected_target_checksum=checksum_json(target),
                expected_purpose="real_executor_invocation",
                approval_secret=approval_secret,
                now=checked_at,
            )
            for approval_blocker in approval_result["blockers"]:
                code = approval_blocker["code"]
                code = {
                    "operator_approval_invalid": "approval_invalid",
                    "operator_approval_schema_invalid": "approval_schema_invalid",
                    "operator_approval_target_invalid": "approval_target_invalid",
                    "operator_approval_target_mismatch": "approval_target_mismatch",
                    "operator_approval_purpose_missing": "approval_purpose_missing",
                    "operator_approval_purpose_mismatch": "approval_purpose_mismatch",
                    "operator_approval_operator_missing": "approval_identity_invalid",
                    "operator_approval_key_id_weak": "approval_identity_invalid",
                    "operator_approval_timestamp_invalid": "approval_timestamp_invalid",
                    "operator_approval_window_too_long": "approval_window_too_long",
                    "operator_approval_expired": "approval_expired",
                    "operator_approval_issued_in_future": "approval_issued_in_future",
                    "operator_approval_secret_missing": "approval_missing",
                    "operator_approval_signature_invalid": "approval_signature_invalid",
                }.get(code, code)
                blockers.append(plan_blocker(code, approval_blocker["message"]))

    readiness_checksum = checksum_json(readiness) if isinstance(readiness, dict) else None
    adapter_checksum = checksum_json(adapter) if isinstance(adapter, dict) else None
    rollback_checksum = checksum_json(rollback) if isinstance(rollback, dict) else None
    target_checksum = checksum_json(target) if target is not None else None
    valid = not blockers
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION,
        "packet": "executor_invocation_plan",
        "read_only": True,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "valid": valid,
        "executor_invocation_planned": valid,
        "executor_started": False,
        "target": target,
        "target_checksum": target_checksum,
        "readiness": {
            "file": _normal_path(readiness_file),
            "checksum": readiness_checksum,
            "schema_version": readiness.get("schema_version") if isinstance(readiness, dict) else None,
        },
        "approval": {
            "file": _normal_path(approval_file),
            "valid": approval_result.get("valid") if isinstance(approval_result, dict) else False,
            "purpose": approval_packet.get("purpose") if isinstance(approval_packet, dict) else None,
            "operator_id": approval_packet.get("operator_id") if isinstance(approval_packet, dict) else None,
            "key_id": approval_packet.get("key_id") if isinstance(approval_packet, dict) else None,
        },
        "adapter": {
            "file": _normal_path(adapter_file),
            "checksum": adapter_checksum,
            "id": adapter.get("adapter_id") if isinstance(adapter, dict) else None,
            "kind": adapter.get("adapter_kind") if isinstance(adapter, dict) else None,
        },
        "rollback": {
            "file": _normal_path(rollback_file),
            "checksum": rollback_checksum,
        },
        "audit_chain": {
            "valid": audit_replay.get("valid"),
            "chain_head": audit_replay.get("chain_head"),
            "chain_records": audit_replay.get("chain_records"),
            "legacy_chain_roots": audit_replay.get("legacy_chain_roots"),
        },
        "repository": repository,
        "command": {
            "command": command,
            "environment_allowlist": list(environment_allowlist),
            "timeout_seconds": timeout_seconds,
            "cwd": _normal_path(cwd),
            "expected_result_path": _normal_path(expected_result_path),
        },
        "blockers": blockers,
        "recommended_next_action": _recommendation(blockers),
        "reason": _reason(valid, blockers),
        "side_effects": [],
        "limitations": [
            "read_only_invocation_planning_only",
            "executor_not_started",
            "process_start_out_of_scope",
            "code_modification_branch_commit_push_pr_merge_release_publish_out_of_scope",
        ],
    }


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utc_now()


def _parse_utc_timestamp(value: Any) -> datetime | None:
    return parse_approval_timestamp(value)


def _generated_invocation_id() -> str:
    stamp = _iso_now().replace(":", "").replace("-", "").replace(".", "")
    return f"real-executor-invocation-{stamp}-{secrets.token_hex(4)}"


def _recheck_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "bind_real_executor_closeout"
    codes = {blocker.get("code") for blocker in blockers}
    if "plan_packet_stale" in codes:
        return "refresh_executor_invocation_plan"
    if "executor_process_timeout" in codes:
        return "inspect_executor_timeout"
    if "executor_result_missing" in codes:
        return "inspect_executor_result"
    return "operator_review"


def _invocation_reason(valid: bool, blockers: list[dict[str, Any]]) -> str:
    if valid:
        return "real executor invocation completed"
    if blockers:
        return blockers[0]["message"]
    return "real executor invocation blocked"


def _blocked_invocation_payload(
    *,
    plan_file: Path,
    side_effect_mode: str,
    blockers: list[dict[str, Any]],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_checksum = checksum_json(plan) if isinstance(plan, dict) else None
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION,
        "packet": "real_executor_invocation",
        "valid": False,
        "executor_started": False,
        "timed_out": False,
        "checked_at": _iso_now(),
        "side_effect_mode": side_effect_mode,
        "invocation_cwd": _normal_path(Path.cwd()),
        "plan_file": _normal_path(plan_file),
        "plan_checksum": plan_checksum,
        "command": None,
        "process": None,
        "result_file": None,
        "result_present": False,
        "stdout_log": None,
        "stderr_log": None,
        "record_file": None,
        "repository_before": None,
        "repository_after": None,
        "rollback": None,
        "audit_chain": None,
        "materialized_change_evidence": None,
        "blockers": blockers,
        "recommended_next_action": _recheck_recommendation(blockers),
        "reason": _invocation_reason(False, blockers),
        "side_effects": [],
        "limitations": [
            "blocked_before_process_start",
            "git_pr_automation_not_started",
            "commit_push_pr_merge_release_publish_out_of_scope",
        ],
    }


def _plan_age_blockers(
    plan: dict[str, Any],
    *,
    now: datetime,
    max_plan_age_seconds: int,
) -> list[dict[str, Any]]:
    checked_at = _parse_utc_timestamp(plan.get("checked_at"))
    if checked_at is None:
        return [invocation_blocker("plan_packet_stale", "executor invocation plan checked_at is invalid")]
    age_seconds = (now - checked_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_plan_age_seconds:
        return [
            invocation_blocker(
                "plan_packet_stale",
                "executor invocation plan is stale",
                max_age_seconds=max_plan_age_seconds,
            )
        ]
    return []


def _plan_invocable_blockers(plan: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if plan.get("schema_version") != EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION or plan.get("packet") != "executor_invocation_plan":
        blockers.append(invocation_blocker("plan_not_invocable", "plan must be executor-invocation-plan.v1"))
    if plan.get("read_only") is not True or plan.get("side_effects") != []:
        blockers.append(invocation_blocker("plan_not_invocable", "plan must be read-only with no side effects"))
    if plan.get("valid") is not True or plan.get("executor_invocation_planned") is not True:
        blockers.append(invocation_blocker("plan_not_invocable", "plan is not invocable"))
    if plan.get("executor_started") is not False:
        blockers.append(invocation_blocker("plan_not_invocable", "plan must not have started an executor"))
    if plan.get("recommended_next_action") != "invoke_real_executor":
        blockers.append(invocation_blocker("plan_not_invocable", "plan does not recommend real executor invocation"))
    target = plan.get("target")
    target_checksum = plan.get("target_checksum")
    if not isinstance(target, dict) or not _non_empty_string(target_checksum):
        blockers.append(invocation_blocker("plan_not_invocable", "plan target evidence is required"))
    elif checksum_json(target) != target_checksum:
        blockers.append(invocation_blocker("plan_not_invocable", "plan target checksum does not match target evidence"))
    command = plan.get("command")
    if not isinstance(command, dict):
        blockers.append(invocation_blocker("plan_not_invocable", "plan command evidence is required"))
    else:
        for field in ("command", "cwd", "expected_result_path"):
            if not _non_empty_string(command.get(field)):
                blockers.append(invocation_blocker("plan_not_invocable", f"plan command.{field} is required"))
        if type(command.get("timeout_seconds")) is not int or command.get("timeout_seconds") <= 0:
            blockers.append(invocation_blocker("plan_not_invocable", "plan command.timeout_seconds must be positive"))
        env_allow = command.get("environment_allowlist")
        if not isinstance(env_allow, list) or any(not _non_empty_string(value) for value in env_allow):
            blockers.append(invocation_blocker("plan_not_invocable", "plan command.environment_allowlist must be a list of strings"))
    for field in ("readiness", "approval", "adapter", "rollback"):
        section = plan.get(field)
        if not isinstance(section, dict) or not _non_empty_string(section.get("file")):
            blockers.append(invocation_blocker("plan_not_invocable", f"plan {field}.file is required"))
    return blockers


def _plan_recheck_inputs(plan: dict[str, Any]) -> dict[str, Any]:
    command = plan.get("command") if isinstance(plan.get("command"), dict) else {}
    readiness = plan.get("readiness") if isinstance(plan.get("readiness"), dict) else {}
    approval = plan.get("approval") if isinstance(plan.get("approval"), dict) else {}
    adapter = plan.get("adapter") if isinstance(plan.get("adapter"), dict) else {}
    rollback = plan.get("rollback") if isinstance(plan.get("rollback"), dict) else {}
    return {
        "cwd": Path(command.get("cwd")),
        "readiness_file": Path(readiness.get("file")),
        "approval_file": Path(approval.get("file")),
        "adapter_file": Path(adapter.get("file")),
        "rollback_file": Path(rollback.get("file")),
        "command": command.get("command"),
        "environment_allowlist": list(command.get("environment_allowlist") or []),
        "timeout_seconds": command.get("timeout_seconds"),
        "expected_result_path": command.get("expected_result_path"),
    }


def _normalize_recheck_blocker(blocker: dict[str, Any]) -> dict[str, Any]:
    code = blocker.get("code")
    message = blocker.get("message", "executor invocation recheck failed")
    if isinstance(code, str) and code.startswith("approval_"):
        return invocation_blocker("approval_recheck_failed", message, source_code=code)
    if code == "rollback_evidence_missing":
        return dict(blocker)
    if isinstance(code, str) and code.startswith("rollback_"):
        return invocation_blocker("rollback_recheck_failed", message, source_code=code)
    return dict(blocker)


def _command_argv(command: str) -> tuple[list[str] | None, list[dict[str, Any]]]:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, [invocation_blocker("executor_command_denied", f"approved command could not be parsed: {exc}")]
    if not argv:
        return None, [invocation_blocker("executor_command_denied", "approved command is empty")]
    return argv, []


def _bounded_environment(environment_allowlist: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in environment_allowlist:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _process_output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _git_executable() -> str:
    global _GIT_EXECUTABLE
    if _GIT_EXECUTABLE is None:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git executable could not be resolved")
        git_path = Path(git)
        if not git_path.is_absolute():
            raise RuntimeError("git executable did not resolve to an absolute path")
        _GIT_EXECUTABLE = str(git_path)
    return _GIT_EXECUTABLE


def _git_environment() -> dict[str, str]:
    return dict(os.environ)


def _result_evidence(
    result_file: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not result_file.exists():
        return None, [invocation_blocker("executor_result_missing", "executor result evidence file was not written")]
    try:
        result = read_json(result_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [invocation_blocker("executor_result_missing", f"executor result evidence could not be read: {exc}")]
    if not isinstance(result, dict):
        return None, [invocation_blocker("executor_result_missing", "executor result evidence must be a JSON object")]
    return result, []


def _local_branch_refs(cwd: Path) -> dict[str, str]:
    result = subprocess.run(
        [_git_executable(), "for-each-ref", "--format=%(refname:short)%00%(objectname)", "refs/heads"],
        cwd=cwd,
        env=_git_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git for-each-ref failed"
        raise RuntimeError(detail)
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            continue
        name, separator, object_id = line.partition("\0")
        if separator and name and object_id:
            refs[name] = object_id
    return dict(sorted(refs.items()))


def _branch_ref_changes(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    return {
        "added": {name: after[name] for name in sorted(after.keys() - before.keys())},
        "removed": {name: before[name] for name in sorted(before.keys() - after.keys())},
        "changed": {
            name: {"before": before[name], "after": after[name]}
            for name in sorted(before.keys() & after.keys())
            if before[name] != after[name]
        },
    }


def _repo_evidence_or_blocker(cwd: Path, *, code: str, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        evidence = current_repo_evidence(cwd)
        evidence["local_branch_refs"] = _local_branch_refs(cwd)
        return evidence, []
    except (OSError, RuntimeError, ValueError) as exc:
        return (
            {
                "cwd": str(cwd),
                "branch": None,
                "head": None,
                "dirty_worktree": None,
                "local_branch_refs": None,
            },
            [invocation_blocker(code, message, error=str(exc), path=str(cwd))],
        )


def _local_dirty_files(cwd: Path) -> tuple[set[str] | None, dict[str, Any] | None]:
    repo_root = git_repo_root(cwd) or cwd
    result = subprocess.run(
        [_git_executable(), "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--"],
        cwd=repo_root,
        env=_git_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None, invocation_blocker(
            "materialized_change_evidence_missing",
            "could not inspect local dirty-worktree files",
            detail=(result.stderr or result.stdout).strip(),
        )
    files: set[str] = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            _old_path, path = path.rsplit(" -> ", 1)
        if path:
            files.add(path.replace("\\", "/"))
    return files, None


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _git_diff_checksum(cwd: Path, *args: str) -> tuple[str | None, dict[str, Any] | None]:
    result = subprocess.run(
        [_git_executable(), "--no-optional-locks", *args],
        cwd=cwd,
        env=_git_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
        return None, invocation_blocker(
            "materialized_change_evidence_missing",
            "could not fingerprint local dirty-worktree diff",
            detail=detail,
        )
    return _sha256_bytes(result.stdout), None


def _dirty_worktree_fingerprint(cwd: Path, dirty_files: set[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    repo_root = git_repo_root(cwd) or cwd
    staged_checksum, staged_blocker = _git_diff_checksum(repo_root, "diff", "--cached", "--binary", "--")
    if staged_blocker is not None:
        return None, staged_blocker
    unstaged_checksum, unstaged_blocker = _git_diff_checksum(repo_root, "diff", "--binary", "--")
    if unstaged_blocker is not None:
        return None, unstaged_blocker

    root = repo_root.resolve(strict=False)
    entries: list[dict[str, Any]] = []
    for path in sorted(dirty_files):
        normalized_path = path.replace("\\", "/")
        target = (repo_root / normalized_path).resolve(strict=False)
        if not path_is_relative_to(target, root):
            return None, invocation_blocker(
                "materialized_change_evidence_missing",
                "dirty worktree path escaped the repository root",
                path=normalized_path,
            )
        try:
            if not target.exists():
                entries.append({"path": normalized_path, "type": "missing"})
            elif target.is_file():
                payload = target.read_bytes()
                entries.append(
                    {
                        "path": normalized_path,
                        "type": "file",
                        "size": len(payload),
                        "mode": target.stat().st_mode & 0o777,
                        "content_checksum": _sha256_bytes(payload),
                    }
                )
            elif target.is_dir():
                entries.append({"path": normalized_path, "type": "directory"})
            else:
                entries.append({"path": normalized_path, "type": "special"})
        except OSError as exc:
            return None, invocation_blocker(
                "materialized_change_evidence_missing",
                "could not fingerprint dirty worktree file",
                path=normalized_path,
                error=str(exc),
            )

    return {
        "schema_version": DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
        "dirty_files": sorted(dirty_files),
        "staged_diff_checksum": staged_checksum,
        "unstaged_diff_checksum": unstaged_checksum,
        "file_entries": entries,
    }, None


def _load_rechecked_task_packet(recheck: dict[str, Any]) -> dict[str, Any] | None:
    readiness_file_value = recheck.get("readiness", {}).get("file") if isinstance(recheck.get("readiness"), dict) else None
    if not _non_empty_string(readiness_file_value):
        return None
    readiness, blockers = _read_json_object(Path(readiness_file_value), code="readiness_unreadable", label="readiness evidence")
    if blockers or readiness is None:
        return None
    task_packet, _task_checksum, task_blockers = _readiness_task_packet(readiness, Path(readiness_file_value))
    if task_blockers:
        return None
    return task_packet


def _materialized_change_evidence(
    result_evidence: dict[str, Any] | None,
    *,
    task_packet: dict[str, Any] | None,
    dirty_files: set[str] | None,
    observed_head: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    raw = result_evidence.get("materialized_change_evidence") if isinstance(result_evidence, dict) else None
    if not isinstance(raw, dict):
        return {
            "status": "absent",
            "source": None,
            "files": [],
            "limitations": ["executor_result_materialized_change_evidence_absent"],
        }, [invocation_blocker("materialized_change_evidence_missing", "materialized change evidence is absent")]
    files = raw.get("files")
    if raw.get("status") != "verified" or not isinstance(files, list) or not files or any(not _non_empty_string(path) for path in files):
        blockers.append(invocation_blocker("materialized_change_evidence_missing", "materialized change evidence is invalid"))
        return {
            "status": "invalid",
            "source": raw.get("source"),
            "files": files if isinstance(files, list) else [],
            "limitations": ["executor_result_materialized_change_evidence_invalid"],
        }, blockers
    normalized_files = {str(path).replace("\\", "/") for path in files}
    result_files_raw = result_evidence.get("files_changed") if isinstance(result_evidence, dict) else None
    if not isinstance(result_files_raw, list) or any(not _non_empty_string(path) for path in result_files_raw):
        blockers.append(invocation_blocker("materialized_change_evidence_missing", "result files_changed must be a list of strings"))
        result_files: set[str] = set()
    else:
        result_files = {str(path).replace("\\", "/") for path in result_files_raw}
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    if task.get("id") is not None and raw.get("task_id") != task.get("id"):
        blockers.append(invocation_blocker("materialized_change_evidence_missing", "materialized change evidence task_id does not match the executor task"))
    if isinstance(result_evidence, dict):
        if raw.get("task_id") != result_evidence.get("task_id"):
            blockers.append(invocation_blocker("materialized_change_evidence_missing", "materialized change evidence task_id does not match result evidence"))
        if raw.get("resulting_head") != result_evidence.get("resulting_head"):
            blockers.append(
                invocation_blocker(
                    "materialized_change_evidence_missing",
                    "materialized change evidence resulting_head does not match result evidence",
                )
            )
        if observed_head is not None and result_evidence.get("resulting_head") != observed_head:
            blockers.append(
                invocation_blocker(
                    "materialized_change_evidence_missing",
                    "executor result resulting_head does not match the observed repository head",
                    expected=observed_head,
                    actual=result_evidence.get("resulting_head"),
                )
            )
        if observed_head is not None and raw.get("resulting_head") != observed_head:
            blockers.append(
                invocation_blocker(
                    "materialized_change_evidence_missing",
                    "materialized change evidence resulting_head does not match the observed repository head",
                    expected=observed_head,
                    actual=raw.get("resulting_head"),
                )
            )
        if not normalized_files.issubset(result_files):
            blockers.append(
                invocation_blocker(
                    "materialized_change_evidence_missing",
                    "materialized change evidence files must be a subset of result files_changed",
                    files=sorted(normalized_files - result_files),
                )
            )
    if dirty_files is not None and normalized_files != dirty_files:
        blockers.append(
            invocation_blocker(
                "materialized_change_evidence_missing",
                "materialized change evidence files must match the local dirty worktree",
                expected_files=sorted(dirty_files),
                actual_files=sorted(normalized_files),
            )
        )
    if blockers:
        return {
            "status": "invalid",
            "source": raw.get("source"),
            "task_id": raw.get("task_id"),
            "resulting_head": raw.get("resulting_head"),
            "files": list(files),
            "limitations": ["executor_result_materialized_change_evidence_invalid"],
        }, blockers
    return {
        "status": "verified",
        "source": raw.get("source"),
        "task_id": raw.get("task_id"),
        "resulting_head": raw.get("resulting_head"),
        "files": list(files),
        "limitations": [str(item) for item in raw.get("limitations") or [] if _non_empty_string(item)],
    }, []


def invoke_real_executor(
    *,
    root: Path,
    plan_file: Path,
    approval_secret: str | bytes | None,
    side_effect_mode: str,
    allow_repo_local_root: bool = False,
    max_plan_age_seconds: int = MAX_INVOCATION_PLAN_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(root)
    plan_path = Path(plan_file)
    if side_effect_mode not in REAL_EXECUTOR_SIDE_EFFECT_MODES:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[
                invocation_blocker(
                    "plan_not_invocable",
                    "side_effect_mode must be evidence_only or materialized_changes",
                )
            ],
        )

    try:
        plan = read_json(plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[invocation_blocker("plan_not_invocable", f"plan file could not be read: {exc}", path=str(plan_path))],
        )
    if not isinstance(plan, dict):
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[invocation_blocker("plan_not_invocable", "plan file must contain a JSON object")],
        )

    checked_at = (now or _utc_now_dt()).astimezone(timezone.utc)
    preflight_blockers = _plan_invocable_blockers(plan)
    preflight_blockers.extend(
        _plan_age_blockers(
            plan,
            now=checked_at,
            max_plan_age_seconds=max_plan_age_seconds,
        )
    )
    if preflight_blockers:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=preflight_blockers,
            plan=plan,
        )

    inputs = _plan_recheck_inputs(plan)
    safety_issue = None if allow_repo_local_root else runtime_root_safety_issue(root, inputs["cwd"])
    if safety_issue is not None:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[invocation_blocker("runtime_root_unsafe", safety_issue, path=str(root), cwd=str(inputs["cwd"]))],
            plan=plan,
        )
    recheck = build_executor_invocation_plan(
        root=root,
        cwd=inputs["cwd"],
        readiness_file=inputs["readiness_file"],
        approval_file=inputs["approval_file"],
        approval_secret=approval_secret,
        adapter_file=inputs["adapter_file"],
        rollback_file=inputs["rollback_file"],
        command=inputs["command"],
        environment_allowlist=inputs["environment_allowlist"],
        timeout_seconds=inputs["timeout_seconds"],
        expected_result_path=inputs["expected_result_path"],
        now=checked_at,
    )
    recheck_blockers = [_normalize_recheck_blocker(blocker) for blocker in recheck.get("blockers", []) if isinstance(blocker, dict)]
    if recheck.get("valid") is not True:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=recheck_blockers or [invocation_blocker("plan_not_invocable", "plan recheck failed")],
            plan=plan,
        )
    if recheck.get("target_checksum") != plan.get("target_checksum"):
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[
                invocation_blocker(
                    "plan_not_invocable",
                    "current invocation target checksum does not match saved plan",
                    expected=plan.get("target_checksum"),
                    actual=recheck.get("target_checksum"),
                )
            ],
            plan=plan,
        )

    command_info = recheck["command"]
    command_text = command_info["command"]
    argv, argv_blockers = _command_argv(command_text)
    if argv_blockers or argv is None:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=argv_blockers,
            plan=plan,
        )

    cwd = Path(command_info["cwd"]).expanduser().resolve(strict=False)
    expected_result_path = Path(command_info["expected_result_path"]).expanduser().resolve(strict=False)
    timeout_seconds = int(command_info["timeout_seconds"])
    environment_allowlist = list(command_info.get("environment_allowlist") or [])
    if expected_result_path.exists():
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=[
                invocation_blocker(
                    "executor_result_stale",
                    "executor result evidence file already exists before invocation",
                    path=str(expected_result_path),
                )
            ],
            plan=plan,
        )
    repository_before, repo_before_blockers = _repo_evidence_or_blocker(
        cwd,
        code="repo_inspection_failed",
        message="current repo could not be inspected before executor invocation",
    )
    if repo_before_blockers:
        return _blocked_invocation_payload(
            plan_file=plan_path,
            side_effect_mode=side_effect_mode,
            blockers=repo_before_blockers,
            plan=plan,
        )
    invocation_id = _generated_invocation_id()
    record_file = real_executor_invocation_path(root, invocation_id)
    stdout_log = record_file.with_suffix(".stdout.log")
    stderr_log = record_file.with_suffix(".stderr.log")

    started_at = _iso_now()
    observed_start = time.monotonic()
    timed_out = False
    process_started = False
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    process_blockers: list[dict[str, Any]] = []
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=_bounded_environment(environment_allowlist),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        process_started = True
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        process_started = True
        timed_out = True
        exit_code = 124
        stdout = _process_output_text(exc.stdout)
        stderr = _process_output_text(exc.stderr)
        process_blockers.append(
            invocation_blocker(
                "executor_process_timeout",
                "real executor process exceeded the approved timeout",
                timeout_seconds=timeout_seconds,
            )
        )
    except OSError as exc:
        stderr = str(exc)
        process_blockers.append(
            invocation_blocker(
                "executor_process_failed",
                "real executor process could not be started",
                error=str(exc),
            )
        )
    elapsed_seconds = time.monotonic() - observed_start
    ended_at = _iso_now()

    _write_text(stdout_log, stdout)
    _write_text(stderr_log, stderr)

    repository_after, repo_after_blockers = _repo_evidence_or_blocker(
        cwd,
        code="unexpected_repo_modification",
        message="current repo could not be inspected after executor invocation",
    )
    process_blockers.extend(repo_after_blockers)
    result_evidence, result_blockers = _result_evidence(expected_result_path)
    process_blockers.extend(result_blockers)
    if exit_code not in (0, None) and not timed_out:
        process_blockers.append(
            invocation_blocker(
                "executor_process_failed",
                "real executor process exited with a nonzero status",
                exit_code=exit_code,
            )
        )

    head_changed = repository_after.get("head") != repository_before.get("head")
    branch_changed = repository_after.get("branch") != repository_before.get("branch")
    before_branch_refs = (
        repository_before.get("local_branch_refs") if isinstance(repository_before.get("local_branch_refs"), dict) else {}
    )
    after_branch_refs = (
        repository_after.get("local_branch_refs") if isinstance(repository_after.get("local_branch_refs"), dict) else {}
    )
    branch_ref_changes = _branch_ref_changes(before_branch_refs, after_branch_refs)
    branch_refs_changed = any(branch_ref_changes.values())
    dirty_after = repository_after.get("dirty_worktree") is True
    dirty_files: set[str] | None = None
    if dirty_after:
        dirty_files, dirty_file_blocker = _local_dirty_files(cwd)
        if dirty_file_blocker is not None:
            process_blockers.append(dirty_file_blocker)
    task_packet = _load_rechecked_task_packet(recheck)
    materialized_evidence, materialized_blockers = _materialized_change_evidence(
        result_evidence,
        task_packet=task_packet,
        dirty_files=dirty_files,
        observed_head=repository_after.get("head"),
    )
    if head_changed or branch_changed or branch_refs_changed:
        process_blockers.append(
            invocation_blocker(
                "unexpected_repo_modification",
                "real executor changed the repository branch, HEAD, or local branch refs",
                before_branch=repository_before.get("branch"),
                after_branch=repository_after.get("branch"),
                before_head=repository_before.get("head"),
                after_head=repository_after.get("head"),
                local_branch_ref_changes=branch_ref_changes,
            )
        )
    elif side_effect_mode == "evidence_only" and dirty_after:
        process_blockers.append(
            invocation_blocker(
                "unexpected_repo_modification",
                "evidence_only real executor invocation left the target repository dirty",
            )
        )
    elif side_effect_mode == "materialized_changes" and dirty_after:
        if not materialized_blockers and dirty_files is not None:
            fingerprint, fingerprint_blocker = _dirty_worktree_fingerprint(cwd, dirty_files)
            if fingerprint_blocker is not None:
                process_blockers.append(fingerprint_blocker)
            elif fingerprint is not None:
                materialized_evidence = dict(materialized_evidence)
                materialized_evidence["worktree_fingerprint_schema_version"] = DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION
                materialized_evidence["worktree_fingerprint_checksum"] = checksum_json(fingerprint)
        process_blockers.extend(materialized_blockers)

    valid = not process_blockers
    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION,
        "packet": "real_executor_invocation",
        "valid": valid,
        "executor_started": process_started,
        "timed_out": timed_out,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "started_at": started_at,
        "ended_at": ended_at,
        "invocation_id": invocation_id,
        "side_effect_mode": side_effect_mode,
        "invocation_cwd": _normal_path(Path.cwd()),
        "plan_file": _normal_path(plan_path),
        "plan_checksum": checksum_json(plan),
        "plan_target_checksum": plan.get("target_checksum"),
        "rechecked_plan_checksum": checksum_json(recheck),
        "command": {
            "command": command_text,
            "argv": list(argv),
            "cwd": str(cwd),
            "environment_allowlist": environment_allowlist,
            "timeout_seconds": timeout_seconds,
            "expected_result_path": str(expected_result_path),
        },
        "process": {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_seconds": elapsed_seconds,
        },
        "result_file": str(expected_result_path),
        "result_present": expected_result_path.exists(),
        "result_evidence_checksum": checksum_json(result_evidence) if isinstance(result_evidence, dict) else None,
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "record_file": str(record_file),
        "closeout_status": "pending",
        "repository_before": repository_before,
        "repository_after": repository_after,
        "rollback": {
            "file": recheck.get("rollback", {}).get("file") if isinstance(recheck.get("rollback"), dict) else None,
            "checksum": recheck.get("rollback", {}).get("checksum") if isinstance(recheck.get("rollback"), dict) else None,
        },
        "audit_chain": recheck.get("audit_chain"),
        "materialized_change_evidence": materialized_evidence,
        "blockers": process_blockers,
        "recommended_next_action": _recheck_recommendation(process_blockers),
        "reason": _invocation_reason(valid, process_blockers),
        "side_effects": [
            *([] if not process_started else ["real_executor_process_started"]),
            "stdout_stderr_captured",
            "real_executor_invocation_record_written",
        ],
        "limitations": [
            "single_approved_command_only",
            "no_branch_commit_push_pr_merge_release_publish",
            "closeout_binding_deferred_to_executor_result_closeout",
        ],
    }
    atomic_write_json(record_file, payload)
    return payload
