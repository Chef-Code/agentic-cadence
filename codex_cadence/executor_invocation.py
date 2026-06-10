from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.approvals import build_operator_approval_verification_packet, parse_approval_timestamp
from codex_cadence.executor_contract import checksum_json
from codex_cadence.ownership import validate_work_ownership
from codex_cadence.policy_audit import replay_audit_log
from codex_cadence.repo_state import current_repo_evidence, git_repo_root, path_is_relative_to
from codex_cadence.store import brake_path, read_json, utc_now

EXECUTOR_INVOCATION_PLAN_SCHEMA_VERSION = "executor-invocation-plan.v1"
EXECUTOR_INVOCATION_TARGET_SCHEMA_VERSION = "executor-invocation-target.v1"
EXECUTOR_ADAPTER_SCHEMA_VERSION = "executor-adapter.v1"
EXECUTOR_ROLLBACK_SCHEMA_VERSION = "executor-rollback.v1"
MAX_READINESS_AGE_SECONDS = 15 * 60


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


def _command_blockers(command: str, readiness: dict[str, Any]) -> list[dict[str, Any]]:
    task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
    command_policy = task.get("command_policy") if isinstance(task.get("command_policy"), dict) else {}
    allowed = command_policy.get("allowed_commands", []) if isinstance(command_policy, dict) else []
    denied = command_policy.get("denied_commands", []) if isinstance(command_policy, dict) else []
    command_text = command.lower()
    if not _non_empty_string(command):
        return [plan_blocker("executor_command_denied", "executor command is required")]
    if any(denied_command.lower() in command_text for denied_command in denied if isinstance(denied_command, str)):
        return [plan_blocker("executor_command_denied", "executor command is denied by command_policy")]
    if allowed and not any(command_text.startswith(str(allowed_command).lower()) for allowed_command in allowed):
        return [plan_blocker("executor_command_denied", "executor command is outside allowed command_policy")]
    disabled_fragments = (
        "git push",
        "git commit",
        "gh pr create",
        "gh pr merge",
        "gh release create",
        "twine upload",
        "npm publish",
        "pnpm publish",
        "yarn publish",
        "poetry publish",
        "uv publish",
        "hatch publish",
        "flit publish",
    )
    if any(fragment in command_text for fragment in disabled_fragments):
        return [plan_blocker("executor_command_denied", "executor command violates disabled permissions")]
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


def _result_path_blockers(root: Path, expected_result_path: str | Path, readiness: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    result_path = Path(expected_result_path).expanduser().resolve(strict=False)
    root_path = root.expanduser().resolve(strict=False)
    if not path_is_relative_to(result_path, root_path):
        blockers.append(plan_blocker("result_path_invalid", "expected result path must stay inside the runtime root"))
    task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
    if _normal_path(result_path) != _normal_path(task.get("expected_result_path", "")):
        blockers.append(plan_blocker("result_path_invalid", "expected result path does not match readiness"))
    return blockers


def _active_epoch_blockers(root: Path, readiness: dict[str, Any]) -> list[dict[str, Any]]:
    active_epoch = readiness.get("active_epoch") if isinstance(readiness.get("active_epoch"), dict) else {}
    epoch_id = active_epoch.get("id")
    if not _non_empty_string(epoch_id):
        return [plan_blocker("active_epoch_mismatch", "readiness active_epoch id is required")]
    active_dir = root / "epochs" / "active"
    epoch_path = active_dir / f"{epoch_id}.json"
    if not epoch_path.exists():
        return [plan_blocker("active_epoch_mismatch", "readiness active epoch is no longer active", epoch_id=epoch_id)]
    epoch, blockers = _read_json_object(epoch_path, code="active_epoch_mismatch", label="active epoch")
    if blockers:
        return blockers
    if epoch.get("status") != "ACTIVE":
        return [plan_blocker("active_epoch_mismatch", "active epoch status must be ACTIVE")]
    return []


def _ownership_blockers(root: Path, cwd: Path, readiness: dict[str, Any]) -> list[dict[str, Any]]:
    task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
    repo = task.get("repo") if isinstance(task.get("repo"), dict) else {}
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
    approval_packet: Any | None = None
    approval_result: dict[str, Any] | None = None

    if readiness is not None:
        blockers.extend(_readiness_blockers(readiness, now=checked_at))
        repository, repo_blockers = _repo_recheck_blockers(cwd=Path(cwd), readiness=readiness)
        blockers.extend(repo_blockers)
        blockers.extend(_brake_blockers(root))
        blockers.extend(_active_epoch_blockers(root, readiness))
        blockers.extend(_ownership_blockers(root, Path(cwd), readiness))
        blockers.extend(_result_path_blockers(root, expected_result_path, readiness))
        blockers.extend(_command_blockers(command, readiness))

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
                if code == "operator_approval_target_mismatch":
                    code = "approval_target_mismatch"
                elif code == "operator_approval_expired":
                    code = "approval_expired"
                elif code == "operator_approval_purpose_mismatch":
                    code = "approval_purpose_mismatch"
                elif code == "operator_approval_secret_missing":
                    code = "approval_missing"
                elif code == "operator_approval_signature_invalid":
                    code = "approval_signature_invalid"
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
