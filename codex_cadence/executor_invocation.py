from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.approvals import build_operator_approval_verification_packet, parse_approval_timestamp
from codex_cadence.epochs import read_active_epoch_records
from codex_cadence.executor_contract import checksum_json, validate_executor_command, validate_executor_task_packet
from codex_cadence.ownership import validate_work_ownership
from codex_cadence.policy_audit import replay_audit_log
from codex_cadence.repo_state import current_repo_evidence, git_repo_root, path_is_relative_to
from codex_cadence.store import brake_path, read_json

EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION = "executor-invocation-plan.v1"
EXECUTOR_INVOCATION_TARGET_SCHEMA_VERSION = "executor-invocation-target.v1"
EXECUTOR_ADAPTER_SCHEMA_VERSION = "executor-adapter.v1"
EXECUTOR_ROLLBACK_SCHEMA_VERSION = "executor-rollback.v1"
MAX_READINESS_AGE_SECONDS = 15 * 60
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
            "file": str(readiness_file),
            "checksum": readiness_checksum,
            "schema_version": readiness.get("schema_version") if isinstance(readiness, dict) else None,
        },
        "approval": {
            "file": str(approval_file),
            "valid": approval_result.get("valid") if isinstance(approval_result, dict) else False,
            "purpose": approval_packet.get("purpose") if isinstance(approval_packet, dict) else None,
            "operator_id": approval_packet.get("operator_id") if isinstance(approval_packet, dict) else None,
            "key_id": approval_packet.get("key_id") if isinstance(approval_packet, dict) else None,
        },
        "adapter": {
            "file": str(adapter_file),
            "checksum": adapter_checksum,
            "id": adapter.get("adapter_id") if isinstance(adapter, dict) else None,
            "kind": adapter.get("adapter_kind") if isinstance(adapter, dict) else None,
        },
        "rollback": {
            "file": str(rollback_file),
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
