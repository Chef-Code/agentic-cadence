from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .executor_contract import validate_execution_run_record, validate_executor_task_packet
from .model import DEFAULT_EPOCH_POLICY
from .repo_state import validate_repo_snapshot
from .store import atomic_write_json, ensure_layout, epoch_path, epoch_state_dir
from .store import exclusive_lock, lock_path, read_json, snapshot_path, utc_now

EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION = "executor-epoch-closeout.v1"
EPOCH_DECISIONS = ("STOP", "CONTINUE", "HANDOFF", "ASK_APPROVAL")
STOP, CONTINUE, HANDOFF, ASK_APPROVAL = EPOCH_DECISIONS
EPOCH_ID_ATTEMPTS = 8
BRAKE_DECISION_STATUSES = ("DRIVE", "NEUTRAL", "PARK")
REPO_CONFIDENCE_VALUES = ("high", "medium", "low")
UNCERTAINTY_VALUES = ("low", "medium", "high")
EPOCH_HEALTH_VALUES = ("good", "watch", "degraded")
CANDIDATE_TASK_TYPES = ("execution", "discovery")
CANDIDATE_BUCKETS = ("XS", "S", "M", "L", "XL")
NEXT_EPOCH_REQUIREMENTS = ("green_ci_or_explicit_handoff", "none")
POLICY_VIOLATION_MARKERS = (
    "denied by command_policy",
    "outside allowed command_policy",
    "outside allowed_paths",
    "violates disabled",
    "contains unsupported shell expansion",
    "resulting_head must match task repo head when commits are forbidden",
)


def closeout_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def epoch_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"epoch-{stamp}-{secrets.token_hex(4)}"


def resolved_epoch_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("epoch policy must be a JSON object")
    return {**DEFAULT_EPOCH_POLICY, **deepcopy(policy or {})}


def policy_limit(policy: dict[str, Any], key: str) -> int:
    value = policy.get(key, DEFAULT_EPOCH_POLICY[key])
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"epoch policy {key} must be a non-negative integer")
    return value


def continuation_task_limit(max_tasks: int, uncertainty: str, epoch_health: str) -> int:
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks < 0:
        raise ValueError("max_tasks must be a non-negative integer")
    if uncertainty not in UNCERTAINTY_VALUES:
        raise ValueError(f"invalid uncertainty: {uncertainty}")
    if epoch_health not in EPOCH_HEALTH_VALUES:
        raise ValueError(f"invalid epoch_health: {epoch_health}")
    if max_tasks == 0:
        return 0
    if uncertainty == "medium" or epoch_health == "watch":
        return 1
    return max_tasks


def severity_value(value: str, ordered_values: tuple[str, ...]) -> int:
    if value not in ordered_values:
        allowed = ", ".join(ordered_values)
        raise ValueError(f"value must be one of: {allowed}")
    return ordered_values.index(value)


def max_severity(values: list[str], ordered_values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("at least one severity value is required")
    return max(values, key=lambda value: severity_value(value, ordered_values))


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def epoch_elapsed_minutes(epoch: dict[str, Any], now: datetime | None = None) -> int:
    started_at = parse_utc(epoch.get("started_at"))
    current = now or datetime.now(timezone.utc)
    elapsed_seconds = max(0, (current - started_at).total_seconds())
    return int(elapsed_seconds // 60)


def checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def completed_epoch_history(root: Path) -> list[dict[str, Any]]:
    ensure_layout(root)
    records = []
    for path in epoch_state_dir(root, "completed").glob("*.json"):
        data = read_json(path)
        if data.get("status") != "COMPLETED":
            raise ValueError(f"completed epoch status must be COMPLETED: {path.name}")
        completed_at = data.get("completed_at")
        if not isinstance(completed_at, str):
            raise ValueError(f"completed epoch missing completed_at: {path.name}")
        epoch_id_value = data.get("id")
        if not isinstance(epoch_id_value, str):
            raise ValueError(f"completed epoch missing id: {path.name}")
        records.append((parse_utc(completed_at), epoch_id_value, data))
    return [data for _completed_at, _epoch_id_value, data in sorted(records)]


def completed_continue_count(root: Path) -> int:
    count = 0
    for data in reversed(completed_epoch_history(root)):
        decision = data.get("decision")
        if decision == CONTINUE:
            count += 1
            continue
        if decision in EPOCH_DECISIONS:
            break
        raise ValueError(f"completed epoch decision is invalid: {decision}")
    return count


def validate_epoch_tasks(tasks: list[dict[str, Any]], policy: dict[str, Any]) -> None:
    max_tasks = policy_limit(policy, "max_tasks_per_epoch")
    if len(tasks) > max_tasks:
        raise ValueError(f"epoch task count {len(tasks)} exceeds max_tasks_per_epoch {max_tasks}")

    discovery_count = 0
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"epoch task {index} must be a JSON object")
        task_type = task.get("task_type")
        if task_type not in CANDIDATE_TASK_TYPES:
            raise ValueError(f"epoch task {index} task_type must be execution or discovery")
        drivers = task.get("drivers", [])
        if drivers is not None and (not isinstance(drivers, list) or any(not isinstance(driver, str) for driver in drivers)):
            raise ValueError(f"epoch task {index} drivers must be a list of strings")
        if task.get("executable") is False:
            raise ValueError(f"epoch task {index} must be executable")
        if "bucket" in task and task.get("bucket") not in CANDIDATE_BUCKETS:
            raise ValueError(f"epoch task {index} bucket must be one of XS, S, M, L, XL")
        if task.get("bucket") == "XL":
            raise ValueError(f"epoch task {index} XL task requires approval or decomposition before epoch start")
        if task.get("source") == "agent_proposal":
            if (
                task.get("requires_user_allowance") is not True
                or task.get("allowance") != "elect"
                or task.get("executable") is not True
            ):
                raise ValueError(f"epoch task {index} agent proposal requires elect allowance and executable=true")
        allow_self_evolution = policy.get("allow_self_evolution", DEFAULT_EPOCH_POLICY["allow_self_evolution"])
        if allow_self_evolution not in {"propose_only", "disabled"}:
            raise ValueError("epoch policy allow_self_evolution must be propose_only or disabled")
        if "self_evolution" in drivers:
            if allow_self_evolution == "disabled":
                raise ValueError(f"epoch task {index} self-evolution is disabled by epoch policy")
            if task_type == "execution":
                raise ValueError(f"epoch task {index} self-evolution execution requires protocol approval")
        if task_type == "discovery":
            discovery_count += 1

    max_discovery_tasks = policy_limit(policy, "max_discovery_tasks_per_epoch")
    if discovery_count > max_discovery_tasks:
        raise ValueError(
            f"epoch discovery task count {discovery_count} exceeds max_discovery_tasks_per_epoch {max_discovery_tasks}"
        )


def start_epoch(
    root: Path,
    repo: str | None,
    branch: str | None,
    tasks: list[dict[str, Any]],
    snapshot_before: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_layout(root)
    if not repo:
        raise ValueError("epoch repo is required")
    if not branch:
        raise ValueError("epoch branch is required")
    resolved_policy = resolved_epoch_policy(policy)
    validate_epoch_tasks(tasks, resolved_policy)
    valid_snapshot, reason = validate_repo_snapshot(snapshot_before, expected_repo=repo, expected_branch=branch)
    if not valid_snapshot:
        raise ValueError(f"invalid snapshot_before: {reason}")
    now = utc_now()
    with exclusive_lock(lock_path(root, "active-epoch")):
        active_epochs = list(epoch_state_dir(root, "active").glob("*.json"))
        if active_epochs:
            raise RuntimeError("an active epoch already exists")
        for _ in range(EPOCH_ID_ATTEMPTS):
            data = {
                "id": epoch_id(),
                "status": "ACTIVE",
                "repo": repo,
                "branch": branch,
                "tasks": deepcopy(tasks),
                "completed_tasks": [],
                "snapshot_before": deepcopy(snapshot_before),
                "policy": deepcopy(resolved_policy),
                "started_at": now,
                "updated_at": now,
            }
            target = epoch_path(root, "active", data["id"])
            if target.exists():
                continue
            atomic_write_json(target, data)
            return data
    raise FileExistsError(f"could not allocate unique epoch id after {EPOCH_ID_ATTEMPTS} attempts")


def read_active_epoch_records(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    active_dir = epoch_state_dir(root, "active")
    if not active_dir.exists():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(active_dir.glob("*.json")):
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"active epoch record must be a JSON object: {path} (found {type(data).__name__})")
        records.append((path, data))
    return records


def load_active_epoch(root: Path, epoch_id_value: str) -> dict[str, Any]:
    ensure_layout(root)
    active_epochs = list(epoch_state_dir(root, "active").glob("*.json"))
    if len(active_epochs) != 1:
        raise RuntimeError(f"expected exactly one active epoch, found {len(active_epochs)}")
    path = epoch_path(root, "active", epoch_id_value)
    if not path.exists():
        raise FileNotFoundError(f"active epoch not found: {epoch_id_value}")
    data = read_json(path)
    if data.get("id") != epoch_id_value:
        raise ValueError("active epoch id does not match path")
    if data.get("status") != "ACTIVE":
        raise ValueError("active epoch status must be ACTIVE")
    return data


def validate_continue_self_check(root: Path, epoch: dict[str, Any]) -> None:
    check = epoch.get("last_self_check")
    if not isinstance(check, dict) or check.get("decision") != CONTINUE:
        raise ValueError("CONTINUE requires a persisted CONTINUE self-check")
    if check.get("epoch_id") != epoch.get("id"):
        raise ValueError("CONTINUE self-check epoch does not match active epoch")
    if check.get("epoch_grounded") is not True:
        raise ValueError("CONTINUE self-check must be epoch grounded")
    if check.get("current_snapshot_grounded") is not True:
        raise ValueError("CONTINUE self-check must include a current repo snapshot")
    if check.get("brake_status") != "DRIVE":
        raise ValueError("CONTINUE self-check must observe DRIVE brake")
    if not check.get("elected_next"):
        raise ValueError("CONTINUE self-check must elect next work")
    if check.get("epoch_policy_checksum") != checksum_json(epoch.get("policy", {})):
        raise ValueError("CONTINUE self-check policy no longer matches active epoch")
    snapshot_before = epoch.get("snapshot_before")
    if not isinstance(snapshot_before, dict):
        raise ValueError("active epoch snapshot_before must be a JSON object")
    if check.get("snapshot_before_id") != snapshot_before.get("id"):
        raise ValueError("CONTINUE self-check snapshot_before does not match active epoch")
    if check.get("snapshot_before_checksum") != checksum_json(snapshot_before):
        raise ValueError("CONTINUE self-check snapshot_before checksum does not match active epoch")
    snapshot_after_id = check.get("snapshot_after_id")
    if not isinstance(snapshot_after_id, str):
        raise ValueError("CONTINUE self-check must include snapshot_after_id")
    snapshot_after_record = snapshot_path(root, snapshot_after_id)
    if not snapshot_after_record.exists():
        raise FileNotFoundError(f"CONTINUE self-check snapshot_after record not found: {snapshot_after_id}")
    snapshot_after = read_json(snapshot_after_record)
    if snapshot_after.get("id") != snapshot_after_id:
        raise ValueError("CONTINUE self-check snapshot_after id does not match record")
    valid_snapshot_after, snapshot_error = validate_repo_snapshot(
        snapshot_after,
        expected_repo=epoch.get("repo"),
        expected_branch=epoch.get("branch"),
    )
    if not valid_snapshot_after:
        raise ValueError(f"CONTINUE self-check snapshot_after is invalid: {snapshot_error}")
    validate_snapshot_after_epoch(epoch, snapshot_after)
    if check.get("snapshot_after_checksum") != checksum_json(snapshot_after):
        raise ValueError("CONTINUE self-check snapshot_after checksum does not match snapshot record")
    policy = epoch.get("policy", {})
    continue_count = completed_continue_count(root)
    if check.get("completed_continue_count") != continue_count:
        raise ValueError("CONTINUE self-check completed epoch count no longer matches history")
    max_epochs = policy_limit(policy, "max_epochs_without_user_approval")
    if continue_count >= max_epochs:
        raise ValueError("CONTINUE exceeds max_epochs_without_user_approval")
    if policy.get("next_epoch_requires", DEFAULT_EPOCH_POLICY["next_epoch_requires"]) == "green_ci_or_explicit_handoff":
        if check.get("current_snapshot_ci") != "green":
            raise ValueError("CONTINUE self-check must observe green CI")
    max_minutes = policy_limit(policy, "max_minutes_per_epoch")
    if epoch_elapsed_minutes(epoch) > max_minutes:
        raise ValueError("CONTINUE exceeds max_minutes_per_epoch")
    if check.get("current_snapshot_ci") != snapshot_after.get("ci"):
        raise ValueError("CONTINUE self-check current_snapshot_ci does not match snapshot_after")
    snapshot_confidence = snapshot_after.get("repo_confidence")
    check_confidence = check.get("repo_confidence")
    if snapshot_confidence in REPO_CONFIDENCE_VALUES:
        if check_confidence not in REPO_CONFIDENCE_VALUES:
            raise ValueError("CONTINUE self-check repo_confidence is invalid")
        if severity_value(check_confidence, REPO_CONFIDENCE_VALUES) < severity_value(snapshot_confidence, REPO_CONFIDENCE_VALUES):
            raise ValueError("CONTINUE self-check repo_confidence is less conservative than snapshot_after")
    rerun = self_check_decision(
        brake_status=check.get("brake_status"),
        repo_confidence=check.get("repo_confidence"),
        uncertainty=check.get("uncertainty"),
        epoch_health=check.get("epoch_health"),
        elected_next=check.get("elected_next"),
        policy=policy,
        epoch_grounded=check.get("epoch_grounded") is True,
        current_snapshot_grounded=check.get("current_snapshot_grounded") is True,
        current_snapshot_ci=snapshot_after.get("ci"),
        epoch_elapsed_minutes=epoch_elapsed_minutes(epoch),
        completed_continue_count=continue_count,
    )
    if rerun["decision"] != CONTINUE:
        raise ValueError(f"CONTINUE self-check no longer passes governance: {rerun['reason']}")


def validate_snapshot_after_epoch(epoch: dict[str, Any], snapshot_after: dict[str, Any]) -> None:
    snapshot_before = epoch.get("snapshot_before")
    if not isinstance(snapshot_before, dict):
        raise ValueError("active epoch snapshot_before must be a JSON object")
    if snapshot_after.get("id") == snapshot_before.get("id"):
        raise ValueError("snapshot_after must be distinct from snapshot_before")
    captured_at = parse_utc(snapshot_after.get("captured_at"))
    started_at = parse_utc(epoch.get("started_at"))
    if captured_at <= started_at:
        raise ValueError("snapshot_after must be captured after epoch start")


def complete_epoch(root: Path, epoch_id_value: str, decision: str, summary: str | None = None) -> dict[str, Any]:
    if decision not in EPOCH_DECISIONS:
        raise ValueError(f"invalid epoch decision: {decision}")
    with exclusive_lock(lock_path(root, "active-epoch")):
        active_path = epoch_path(root, "active", epoch_id_value)
        completed_path = epoch_path(root, "completed", epoch_id_value)
        if completed_path.exists():
            raise FileExistsError(f"completed epoch already exists: {epoch_id_value}")
        data = load_active_epoch(root, epoch_id_value)
        if decision == CONTINUE:
            validate_continue_self_check(root, data)
        now = utc_now()
        data.update(
            {
                "status": "COMPLETED",
                "decision": decision,
                "summary": summary,
                "completed_at": now,
                "updated_at": now,
            }
        )
        write_terminal_epoch(completed_path, active_path, data)
        return data


def fail_epoch(root: Path, epoch_id_value: str, reason: str) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("failure reason is required")
    with exclusive_lock(lock_path(root, "active-epoch")):
        active_path = epoch_path(root, "active", epoch_id_value)
        failed_path = epoch_path(root, "failed", epoch_id_value)
        if failed_path.exists():
            raise FileExistsError(f"failed epoch already exists: {epoch_id_value}")
        data = load_active_epoch(root, epoch_id_value)
        now = utc_now()
        data.update(
            {
                "status": "FAILED",
                "failure_reason": reason,
                "failed_at": now,
                "updated_at": now,
            }
        )
        write_terminal_epoch(failed_path, active_path, data)
        return data


def write_terminal_epoch(target_path: Path, active_path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(target_path, data)
    try:
        active_path.unlink()
    except Exception:
        try:
            target_path.unlink()
        except FileNotFoundError:
            pass
        raise


def executor_result_failure_reason(validation_valid: bool, validation_reason: str, result_evidence: Any) -> str:
    if validation_valid and isinstance(result_evidence, dict):
        status = result_evidence.get("status")
        if status == "failed":
            return "executor_result_failed"
        if status == "blocked":
            return "executor_result_blocked"
        if status == "stopped":
            blockers = result_evidence.get("blockers")
            blocker_text = " ".join(str(blocker).lower() for blocker in blockers) if isinstance(blockers, list) else ""
            if "timeout" in blocker_text:
                return "executor_result_timed_out"
            return "executor_result_stopped"
    if any(marker in validation_reason for marker in POLICY_VIOLATION_MARKERS):
        return "executor_result_policy_violation"
    return "executor_result_invalid"


def invalid_executor_result_fails_epoch(validation: dict[str, Any], result_evidence: Any) -> bool:
    if validation.get("valid") is True:
        return False
    if validation.get("recommended_next_action") == "stop_active_loop":
        return False
    reason = validation.get("reason")
    if not isinstance(reason, str):
        reason = ""
    return executor_result_failure_reason(False, reason, result_evidence) == "executor_result_policy_violation"


def closeout_next_decision(
    *,
    closeout_status: str,
    result_status: str | None,
    failure_reason: str | None,
    validation: dict[str, Any],
) -> dict[str, Any]:
    if closeout_status == "completed":
        return {
            "decision": "generate_git_pr_plan",
            "recommended_next_action": "run_git_pr_plan",
            "reason": "executor result succeeded; dry-run Git/PR plan can be generated",
        }
    if closeout_status == "task_completed":
        return {
            "decision": "continue",
            "recommended_next_action": "wait_for_next_executor_result",
            "reason": "executor task completed; epoch has remaining tasks",
        }
    if closeout_status == "failed":
        if result_status == "stopped" or failure_reason == "executor_result_timed_out":
            return {
                "decision": "stop",
                "recommended_next_action": "stop_active_loop",
                "reason": "executor stopped before completing the epoch",
            }
        return {
            "decision": "handoff",
            "recommended_next_action": "prepare_handoff",
            "reason": "executor result did not complete successfully",
        }
    if closeout_status == "already_closed":
        return {
            "decision": "stop",
            "recommended_next_action": "inspect_epoch_state",
            "reason": "epoch is already terminal",
        }
    if validation.get("recommended_next_action") == "stop_active_loop":
        return {
            "decision": "stop",
            "recommended_next_action": "stop_active_loop",
            "reason": "active stop prevents result closeout",
        }
    return {
        "decision": "validate_more_evidence",
        "recommended_next_action": "fix_executor_evidence",
        "reason": "executor result evidence cannot close the active epoch yet",
    }


def _task_id_from_packet(task_packet: Any) -> str | None:
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    task_id_value = task.get("id")
    return task_id_value if isinstance(task_id_value, str) and task_id_value.strip() else None


def _repo_from_packet(task_packet: Any) -> dict[str, Any]:
    return task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}


def _snapshot_from_packet(task_packet: Any) -> dict[str, Any]:
    return task_packet.get("snapshot") if isinstance(task_packet, dict) and isinstance(task_packet.get("snapshot"), dict) else {}


def _epoch_task_ids(epoch: dict[str, Any]) -> set[str]:
    tasks = epoch.get("tasks")
    if not isinstance(tasks, list):
        return set()
    task_ids = set()
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            task_ids.add(task["id"])
    return task_ids


def _validate_epoch_task_ids_for_closeout(epoch: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = epoch.get("tasks")
    if not isinstance(tasks, list):
        return [closeout_blocker("invalid_active_epoch", "active epoch tasks must be a JSON list")]
    seen: set[str] = set()
    blockers: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id_value = task.get("id") if isinstance(task, dict) else None
        if not isinstance(task_id_value, str) or not task_id_value.strip():
            blockers.append(
                closeout_blocker(
                    "invalid_active_epoch",
                    "active epoch task ids must be non-empty strings",
                    task_index=index,
                )
            )
            continue
        if task_id_value in seen:
            blockers.append(
                closeout_blocker(
                    "invalid_active_epoch",
                    "active epoch task ids must be unique for executor closeout",
                    task_index=index,
                    task_id=task_id_value,
                )
            )
        seen.add(task_id_value)
    return blockers


def _ordered_epoch_task_ids(epoch: dict[str, Any]) -> list[str]:
    tasks = epoch.get("tasks")
    if not isinstance(tasks, list):
        return []
    task_ids: list[str] = []
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            task_ids.append(task["id"])
    return task_ids


def _parse_optional_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_utc(value)
    except ValueError:
        return None


def _validate_executor_epoch_binding(
    epoch: dict[str, Any],
    task_packet: Any,
    result_evidence: Any,
    snapshot_after: Any,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return [closeout_blocker("invalid_task_packet", task_reason)]
    if not isinstance(result_evidence, dict):
        return [closeout_blocker("invalid_result_evidence", "executor result must be a JSON object")]
    blockers.extend(_validate_epoch_task_ids_for_closeout(epoch))
    task_id_value = _task_id_from_packet(task_packet)
    if task_id_value is None:
        blockers.append(closeout_blocker("invalid_task_packet", "executor task id is required"))
    elif task_id_value not in _epoch_task_ids(epoch):
        blockers.append(
            closeout_blocker(
                "task_not_in_epoch",
                "executor task is not recorded in the active epoch",
                task_id=task_id_value,
            )
        )

    snapshot_before = epoch.get("snapshot_before")
    if not isinstance(snapshot_before, dict):
        blockers.append(closeout_blocker("invalid_active_epoch", "active epoch snapshot_before must be a JSON object"))
        snapshot_before = {}
    task_snapshot = _snapshot_from_packet(task_packet)
    if task_snapshot.get("id") != snapshot_before.get("id") or checksum_json(task_snapshot) != checksum_json(snapshot_before):
        blockers.append(
            closeout_blocker(
                "stale_task_snapshot",
                "executor task snapshot does not match the active epoch baseline snapshot",
                task_snapshot_id=task_snapshot.get("id"),
                epoch_snapshot_id=snapshot_before.get("id"),
            )
        )

    task_repo = _repo_from_packet(task_packet)
    for field in ("name", "branch", "head"):
        epoch_field = "repo" if field == "name" else field
        epoch_value = snapshot_before.get("head") if field == "head" else epoch.get(epoch_field)
        if task_repo.get(field) != epoch_value:
            blockers.append(
                closeout_blocker(
                    "task_epoch_head_mismatch" if field == "head" else "task_epoch_mismatch",
                    f"executor task repo.{field} does not match active epoch {epoch_field}",
                    task_value=task_repo.get(field),
                    epoch_value=epoch_value,
                )
            )

    valid_after, after_reason = validate_repo_snapshot(
        snapshot_after,
        expected_repo=epoch.get("repo"),
        expected_branch=epoch.get("branch"),
    )
    if not valid_after:
        blockers.append(closeout_blocker("invalid_snapshot_after", after_reason))
    elif isinstance(snapshot_after, dict):
        try:
            validate_snapshot_after_epoch(epoch, snapshot_after)
        except (ValueError, FileNotFoundError) as exc:
            blockers.append(closeout_blocker("stale_snapshot_after", str(exc)))
        expected_head = result_evidence.get("resulting_head") if isinstance(result_evidence.get("resulting_head"), str) else None
        expected_head = expected_head or task_repo.get("head")
        if snapshot_after.get("head") != expected_head:
            blockers.append(
                closeout_blocker(
                    "head_mismatch",
                    "snapshot_after head does not match executor result head",
                    snapshot_after_head=snapshot_after.get("head"),
                    result_head=expected_head,
                )
            )
        snapshot_captured_at = _parse_optional_utc(snapshot_after.get("captured_at"))
        result_ended_at = _parse_optional_utc(result_evidence.get("ended_at"))
        if snapshot_captured_at is not None and result_ended_at is not None and snapshot_captured_at < result_ended_at:
            blockers.append(
                closeout_blocker(
                    "stale_snapshot_after",
                    "snapshot_after must be captured at or after executor result ended_at",
                    snapshot_after_captured_at=snapshot_after.get("captured_at"),
                    executor_result_ended_at=result_evidence.get("ended_at"),
                )
            )
    return blockers


def _terminal_epoch_payload(
    epoch: dict[str, Any],
    *,
    task_packet: Any,
    result_evidence: Any,
    validation: dict[str, Any],
    task_file: str,
    result_file: str,
    snapshot_after: dict[str, Any],
) -> dict[str, Any]:
    task_id_value = _task_id_from_packet(task_packet)
    return {
        "task_id": task_id_value,
        "executor_id": result_evidence.get("executor_id") if isinstance(result_evidence, dict) else None,
        "result_status": result_evidence.get("status") if isinstance(result_evidence, dict) else None,
        "result_summary": result_evidence.get("summary") if isinstance(result_evidence, dict) else None,
        "validation_valid": validation.get("valid"),
        "validation_reason": validation.get("reason"),
        "task_file": task_file,
        "result_file": result_file,
        "snapshot_before_id": epoch.get("snapshot_before", {}).get("id") if isinstance(epoch.get("snapshot_before"), dict) else None,
        "snapshot_before_checksum": checksum_json(epoch.get("snapshot_before", {})),
        "snapshot_after_id": snapshot_after.get("id"),
        "snapshot_after_checksum": checksum_json(snapshot_after),
        "task_packet_checksum": checksum_json(task_packet),
        "result_file_checksum": checksum_json(result_evidence),
    }


def closeout_executor_result_epoch(
    root: Path,
    *,
    epoch_id_value: str,
    task_packet: Any,
    result_evidence: Any,
    validation: dict[str, Any],
    task_file: str,
    result_file: str,
    snapshot_after: dict[str, Any],
    run_record: Any | None = None,
    run_record_blockers: list[dict[str, Any]] | None = None,
    before_terminal_complete: Callable[[], None] | None = None,
) -> dict[str, Any]:
    ensure_layout(root)
    result_status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    validation_reason = validation.get("reason") if isinstance(validation.get("reason"), str) else ""
    with exclusive_lock(lock_path(root, "active-epoch")):
        completed_path = epoch_path(root, "completed", epoch_id_value)
        failed_path = epoch_path(root, "failed", epoch_id_value)
        if completed_path.exists() or failed_path.exists():
            terminal_path = completed_path if completed_path.exists() else failed_path
            terminal_epoch = read_json(terminal_path)
            closeout_status = "already_closed"
            blockers = [
                closeout_blocker(
                    "epoch_already_closed",
                    "epoch is already terminal and cannot be closed out again",
                    epoch_status=terminal_epoch.get("status"),
                )
            ]
            return {
                "valid": False,
                "closeout_status": closeout_status,
                "epoch_status": terminal_epoch.get("status"),
                "reason": "epoch is already terminal",
                "failure_reason": terminal_epoch.get("failure_reason"),
                "blockers": blockers,
                "terminal_epoch": terminal_epoch,
                "side_effects": [],
                "next_decision": closeout_next_decision(
                    closeout_status=closeout_status,
                    result_status=result_status,
                    failure_reason=terminal_epoch.get("failure_reason"),
                    validation=validation,
                ),
            }

        active_epochs = list(epoch_state_dir(root, "active").glob("*.json"))
        active_path = epoch_path(root, "active", epoch_id_value)
        if len(active_epochs) != 1 or not active_path.exists():
            blockers = [
                closeout_blocker(
                    "active_epoch_conflict",
                    "expected exactly the requested active epoch before executor closeout",
                    active_epoch_count=len(active_epochs),
                )
            ]
            return {
                "valid": False,
                "closeout_status": "blocked",
                "epoch_status": "ACTIVE" if active_path.exists() else None,
                "reason": "active epoch conflict blocks executor closeout",
                "failure_reason": None,
                "blockers": blockers,
                "terminal_epoch": None,
                "side_effects": [],
                "next_decision": closeout_next_decision(
                    closeout_status="blocked",
                    result_status=result_status,
                    failure_reason=None,
                    validation=validation,
                ),
            }

        epoch = read_json(active_path)
        if epoch.get("id") != epoch_id_value:
            blockers = [closeout_blocker("active_epoch_conflict", "active epoch id does not match path")]
        elif epoch.get("status") != "ACTIVE":
            blockers = [closeout_blocker("active_epoch_conflict", "active epoch status must be ACTIVE")]
        else:
            blockers = _validate_executor_epoch_binding(epoch, task_packet, result_evidence, snapshot_after)
            if run_record_blockers:
                blockers.extend(run_record_blockers)
            if run_record is not None:
                blockers.extend(
                    validate_execution_run_record(
                        run_record,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                        validation_packet=validation,
                        task_file=task_file,
                        result_file=result_file,
                        expected_closeout_status="pending",
                        epoch_id=epoch_id_value,
                    )
                )

        if blockers:
            return {
                "valid": False,
                "closeout_status": "blocked",
                "epoch_status": epoch.get("status"),
                "reason": blockers[0]["message"],
                "failure_reason": None,
                "blockers": blockers,
                "terminal_epoch": None,
                "side_effects": [],
                "next_decision": closeout_next_decision(
                    closeout_status="blocked",
                    result_status=result_status,
                    failure_reason=None,
                    validation=validation,
                ),
            }

        validation_valid = validation.get("valid") is True
        should_complete = validation_valid and result_status == "succeeded"
        should_fail = (
            validation_valid and result_status in {"failed", "blocked", "stopped"}
        ) or invalid_executor_result_fails_epoch(validation, result_evidence)
        if not should_complete and not should_fail:
            blockers = [
                closeout_blocker(
                    "executor_result_not_closeable",
                    validation_reason or "executor result evidence is not ready for epoch closeout",
                )
            ]
            return {
                "valid": False,
                "closeout_status": "blocked",
                "epoch_status": "ACTIVE",
                "reason": blockers[0]["message"],
                "failure_reason": None,
                "blockers": blockers,
                "terminal_epoch": None,
                "side_effects": [],
                "next_decision": closeout_next_decision(
                    closeout_status="blocked",
                    result_status=result_status,
                    failure_reason=None,
                    validation=validation,
                ),
            }

        now = utc_now()
        closeout_data = _terminal_epoch_payload(
            epoch,
            task_packet=task_packet,
            result_evidence=result_evidence,
            validation=validation,
            task_file=task_file,
            result_file=result_file,
            snapshot_after=snapshot_after,
        )
        task_id_value = _task_id_from_packet(task_packet)
        completed_tasks = list(epoch.get("completed_tasks") or [])
        if should_complete and task_id_value is not None and task_id_value not in completed_tasks:
            completed_tasks.append(task_id_value)
        epoch_task_ids = _ordered_epoch_task_ids(epoch)
        remaining_task_ids = [task_id for task_id in epoch_task_ids if task_id not in completed_tasks]
        closeout_data["completed_task_ids"] = list(completed_tasks)
        closeout_data["remaining_task_ids"] = remaining_task_ids
        if should_complete and remaining_task_ids:
            closeouts = epoch.get("executor_closeouts")
            if not isinstance(closeouts, list):
                closeouts = []
            closeouts.append(closeout_data)
            epoch.update(
                {
                    "completed_tasks": completed_tasks,
                    "executor_closeouts": closeouts,
                    "updated_at": now,
                }
            )
            atomic_write_json(active_path, epoch)
            closeout_status = "task_completed"
            failure_reason = None
            side_effects = ["epoch_task_completed"]
        elif should_complete:
            if before_terminal_complete is not None:
                before_terminal_complete()
            closeouts = epoch.get("executor_closeouts")
            if isinstance(closeouts, list):
                closeouts = [*closeouts, closeout_data]
            else:
                closeouts = [closeout_data]
            epoch.update(
                {
                    "status": "COMPLETED",
                    "decision": STOP,
                    "summary": result_evidence.get("summary") if isinstance(result_evidence, dict) else None,
                    "completed_tasks": completed_tasks,
                    "executor_closeout": closeout_data,
                    "executor_closeouts": closeouts,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            write_terminal_epoch(completed_path, active_path, epoch)
            closeout_status = "completed"
            failure_reason = None
            side_effects = ["epoch_completed"]
        else:
            failure_reason = executor_result_failure_reason(validation_valid, validation_reason, result_evidence)
            epoch.update(
                {
                    "status": "FAILED",
                    "failure_reason": failure_reason,
                    "failure_summary": result_evidence.get("summary") if isinstance(result_evidence, dict) else validation_reason,
                    "executor_closeout": closeout_data,
                    "failed_at": now,
                    "updated_at": now,
                }
            )
            write_terminal_epoch(failed_path, active_path, epoch)
            closeout_status = "failed"
            side_effects = ["epoch_failed"]
        return {
            "valid": True,
            "closeout_status": closeout_status,
            "epoch_status": epoch.get("status"),
            "reason": "executor result succeeded" if should_complete else "executor result closed the epoch as failed",
            "failure_reason": failure_reason,
            "blockers": [],
            "terminal_epoch": epoch,
            "side_effects": side_effects,
            "next_decision": closeout_next_decision(
                closeout_status=closeout_status,
                result_status=result_status,
                failure_reason=failure_reason,
                validation=validation,
            ),
        }


def record_self_check(root: Path, epoch_id_value: str, check: dict[str, Any]) -> dict[str, Any]:
    with exclusive_lock(lock_path(root, "active-epoch")):
        active_path = epoch_path(root, "active", epoch_id_value)
        data = load_active_epoch(root, epoch_id_value)
        data["last_self_check"] = deepcopy(check)
        data["updated_at"] = utc_now()
        atomic_write_json(active_path, data)
        return deepcopy(data["last_self_check"])


def expansion_penalty(candidate: dict[str, Any]) -> int:
    penalty = 0
    if candidate.get("task_type") == "discovery":
        penalty += 25
    if candidate.get("uncertainty") == "medium":
        penalty += 15
    elif candidate.get("uncertainty") == "high":
        penalty += 35
    if candidate.get("dependency_fan_out") == "high":
        penalty += 25
    return penalty


def validate_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError(f"candidate {index} must be a JSON object")
    if candidate.get("task_type") not in CANDIDATE_TASK_TYPES:
        raise ValueError(f"candidate {index} task_type must be execution or discovery")
    if candidate.get("bucket") not in CANDIDATE_BUCKETS:
        raise ValueError(f"candidate {index} bucket must be one of XS, S, M, L, XL")
    if "score" in candidate and (isinstance(candidate["score"], bool) or not isinstance(candidate["score"], (int, float))):
        raise ValueError(f"candidate {index} score must be numeric")
    drivers = candidate.get("drivers", [])
    if drivers is not None and (not isinstance(drivers, list) or any(not isinstance(driver, str) for driver in drivers)):
        raise ValueError(f"candidate {index} drivers must be a list of strings")
    if "source" in candidate and not isinstance(candidate["source"], str):
        raise ValueError(f"candidate {index} source must be a string")
    if "uncertainty" in candidate and candidate["uncertainty"] not in UNCERTAINTY_VALUES:
        raise ValueError(f"candidate {index} uncertainty must be one of low, medium, high")
    if "dependency_fan_out" in candidate and candidate["dependency_fan_out"] not in {"low", "medium", "high"}:
        raise ValueError(f"candidate {index} dependency_fan_out must be one of low, medium, high")
    if "executable" in candidate and not isinstance(candidate["executable"], bool):
        raise ValueError(f"candidate {index} executable must be a boolean")
    if "requires_user_allowance" in candidate and not isinstance(candidate["requires_user_allowance"], bool):
        raise ValueError(f"candidate {index} requires_user_allowance must be a boolean")
    return candidate


def is_electable_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("executable") is False:
        return False
    if candidate.get("source") == "agent_proposal":
        return (
            candidate.get("requires_user_allowance") is True
            and candidate.get("allowance") == "elect"
            and candidate.get("executable") is True
        )
    if candidate.get("requires_user_allowance") is True and candidate.get("allowance") != "elect":
        return False
    if candidate.get("requires_user_allowance") is True and candidate.get("executable") is not True:
        return False
    return True


def mutually_exclusive_ids(candidate: dict[str, Any]) -> set[str]:
    relationships = candidate.get("relationships")
    if not isinstance(relationships, dict):
        return set()
    values = relationships.get("mutually_exclusive_with", [])
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def conflicts_with_elected(candidate: dict[str, Any], elected: list[dict[str, Any]]) -> bool:
    candidate_id = candidate.get("id")
    candidate_exclusions = mutually_exclusive_ids(candidate)
    for elected_candidate in elected:
        elected_id = elected_candidate.get("id")
        if isinstance(elected_id, str) and elected_id in candidate_exclusions:
            return True
        if isinstance(candidate_id, str) and candidate_id in mutually_exclusive_ids(elected_candidate):
            return True
    return False


def elect_candidates(
    candidates: list[dict[str, Any]],
    max_tasks: int,
    max_discovery_tasks: int | None = None,
) -> list[dict[str, Any]]:
    if max_tasks <= 0:
        return []
    if max_discovery_tasks is not None and (
        isinstance(max_discovery_tasks, bool) or not isinstance(max_discovery_tasks, int) or max_discovery_tasks < 0
    ):
        raise ValueError("max_discovery_tasks must be a non-negative integer")
    validated = [
        candidate
        for index, candidate in enumerate(candidates)
        if is_electable_candidate(validate_candidate(candidate, index))
    ]
    ranked = sorted(
        validated,
        key=lambda candidate: (
            candidate.get("score", 0) - expansion_penalty(candidate),
            candidate.get("score", 0),
        ),
        reverse=True,
    )
    elected = []
    discovery_count = 0
    for candidate in ranked:
        if len(elected) >= max_tasks:
            break
        if conflicts_with_elected(candidate, elected):
            continue
        if candidate.get("task_type") == "discovery":
            if max_discovery_tasks is not None and discovery_count >= max_discovery_tasks:
                continue
            discovery_count += 1
        copied = deepcopy(candidate)
        copied["elected"] = True
        elected.append(copied)
    return elected


def self_check_decision(
    brake_status: str,
    repo_confidence: str,
    uncertainty: str,
    epoch_health: str,
    elected_next: list[dict[str, Any]],
    policy: dict[str, Any],
    epoch_grounded: bool = False,
    current_snapshot_grounded: bool = True,
    current_snapshot_ci: str | None = None,
    epoch_elapsed_minutes: int | None = None,
    completed_continue_count: int = 0,
) -> dict[str, str]:
    if brake_status not in BRAKE_DECISION_STATUSES:
        raise ValueError(f"invalid brake_status: {brake_status}")
    if repo_confidence not in REPO_CONFIDENCE_VALUES:
        raise ValueError(f"invalid repo_confidence: {repo_confidence}")
    if uncertainty not in UNCERTAINTY_VALUES:
        raise ValueError(f"invalid uncertainty: {uncertainty}")
    if epoch_health not in EPOCH_HEALTH_VALUES:
        raise ValueError(f"invalid epoch_health: {epoch_health}")
    if not isinstance(elected_next, list):
        raise ValueError("elected_next must be a JSON list")
    candidate_uncertainties = []
    for index, candidate in enumerate(elected_next):
        validate_candidate(candidate, index)
        if candidate.get("uncertainty") in UNCERTAINTY_VALUES:
            candidate_uncertainties.append(candidate["uncertainty"])
        if not is_electable_candidate(candidate):
            raise ValueError(f"elected_next candidate {index} is not electable")
    effective_uncertainty = max_severity([uncertainty, *candidate_uncertainties], UNCERTAINTY_VALUES)
    if not isinstance(policy, dict):
        raise ValueError("self-check policy must be a JSON object")
    if (
        isinstance(completed_continue_count, bool)
        or not isinstance(completed_continue_count, int)
        or completed_continue_count < 0
    ):
        raise ValueError("completed_continue_count must be a non-negative integer")
    allow_recursive_discovery = policy.get("allow_recursive_discovery", False)
    if not isinstance(allow_recursive_discovery, bool):
        raise ValueError("self-check policy allow_recursive_discovery must be a boolean")
    allow_self_evolution = policy.get("allow_self_evolution", DEFAULT_EPOCH_POLICY["allow_self_evolution"])
    if allow_self_evolution not in {"propose_only", "disabled"}:
        raise ValueError("self-check policy allow_self_evolution must be propose_only or disabled")
    next_epoch_requires = policy.get("next_epoch_requires", DEFAULT_EPOCH_POLICY["next_epoch_requires"])
    if next_epoch_requires not in NEXT_EPOCH_REQUIREMENTS:
        raise ValueError("self-check policy next_epoch_requires is invalid")
    max_tasks = policy_limit(policy, "max_tasks_per_epoch")
    if len(elected_next) > max_tasks:
        return {"decision": ASK_APPROVAL, "reason": "elected task count exceeds epoch policy"}
    max_discovery_tasks = policy_limit(policy, "max_discovery_tasks_per_epoch")
    discovery_count = sum(1 for candidate in elected_next if candidate.get("task_type") == "discovery")
    if discovery_count > max_discovery_tasks:
        return {"decision": ASK_APPROVAL, "reason": "elected discovery task count exceeds epoch policy"}

    if brake_status != "DRIVE":
        return {"decision": STOP, "reason": f"brake is {brake_status}"}
    if elected_next and not epoch_grounded:
        return {"decision": ASK_APPROVAL, "reason": "epoch snapshot required for continuation"}
    if elected_next and not current_snapshot_grounded:
        return {"decision": ASK_APPROVAL, "reason": "current repo snapshot required for continuation"}
    if repo_confidence == "low":
        return {"decision": ASK_APPROVAL, "reason": "repo confidence is low"}
    if effective_uncertainty == "high":
        return {"decision": HANDOFF, "reason": "uncertainty is high"}
    if epoch_health == "degraded":
        return {"decision": HANDOFF, "reason": "epoch health is degraded"}
    if not elected_next:
        return {"decision": STOP, "reason": "no elected next task"}
    max_minutes = policy_limit(policy, "max_minutes_per_epoch")
    if epoch_elapsed_minutes is not None and epoch_elapsed_minutes > max_minutes:
        return {"decision": HANDOFF, "reason": "epoch exceeded max_minutes_per_epoch"}
    effective_task_limit = continuation_task_limit(max_tasks, effective_uncertainty, epoch_health)
    if len(elected_next) > effective_task_limit:
        return {"decision": ASK_APPROVAL, "reason": "elected task count exceeds effective continuation limit"}
    if next_epoch_requires == "green_ci_or_explicit_handoff" and current_snapshot_ci != "green":
        return {"decision": HANDOFF, "reason": "green CI or explicit handoff required"}
    max_epochs = policy_limit(policy, "max_epochs_without_user_approval")
    if completed_continue_count >= max_epochs:
        return {"decision": ASK_APPROVAL, "reason": "max_epochs_without_user_approval reached"}
    if any(candidate.get("task_type") == "discovery" for candidate in elected_next) and not allow_recursive_discovery:
        return {"decision": ASK_APPROVAL, "reason": "recursive discovery requires approval"}
    if any(candidate.get("task_type") == "execution" and "self_evolution" in candidate.get("drivers", []) for candidate in elected_next):
        return {"decision": ASK_APPROVAL, "reason": "self-evolution execution requires protocol approval"}
    if allow_self_evolution == "disabled" and any("self_evolution" in candidate.get("drivers", []) for candidate in elected_next):
        return {"decision": ASK_APPROVAL, "reason": "self-evolution is disabled by epoch policy"}
    if any(candidate.get("bucket") == "XL" for candidate in elected_next):
        return {"decision": ASK_APPROVAL, "reason": "XL task requires approval"}
    return {"decision": CONTINUE, "reason": "self-check passed"}
