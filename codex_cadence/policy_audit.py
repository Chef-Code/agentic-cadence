from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence.store import read_json, utc_now

AUDIT_SCHEMA_VERSION = "cadence-audit.v1"
LOOP_POLICY_SCHEMA_VERSION = "cadence-loop-policy.v1"


def checksum_json(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_events_path(root: Path) -> Path:
    return root / "audit" / "events.jsonl"


def append_audit_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    target = audit_events_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = dict(record)
    line.setdefault("schema_version", AUDIT_SCHEMA_VERSION)
    line.setdefault("recorded_at", utc_now())
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "event": line.get("event"),
        "path": str(target),
        "recorded_at": line.get("recorded_at"),
    }


def loop_tick_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    executor_task = payload.get("executor_task") if isinstance(payload.get("executor_task"), dict) else None
    task = executor_task.get("task") if isinstance(executor_task, dict) else None
    record = {
        "event": "loop_tick_decision",
        "tick_id": payload.get("tick_id"),
        "action": payload.get("recommended_next_action"),
        "reason": payload.get("reason"),
        "repo": snapshot.get("repo"),
        "branch": snapshot.get("branch"),
        "head": snapshot.get("head"),
        "snapshot_id": snapshot.get("id"),
        "executor_task_id": task.get("id") if isinstance(task, dict) else None,
        "operator_confirmation_required": payload.get("operator_confirmation_required"),
        "payload_checksum": checksum_json(payload),
    }
    return {key: value for key, value in record.items() if value is not None}


def executor_result_validation_audit_record(
    payload: dict[str, Any],
    task_packet: dict[str, Any],
    result_evidence: dict[str, Any],
) -> dict[str, Any]:
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
    record = {
        "event": "executor_result_validation",
        "action": payload.get("recommended_next_action"),
        "reason": payload.get("reason"),
        "valid": payload.get("valid"),
        "task_id": task.get("id"),
        "repo": repo.get("name"),
        "branch": repo.get("branch"),
        "head": repo.get("head"),
        "task_file": payload.get("task_file"),
        "result_file": payload.get("result_file"),
        "payload_checksum": checksum_json(payload),
        "task_packet_checksum": checksum_json(task_packet),
        "result_evidence_checksum": checksum_json(result_evidence),
    }
    return {key: value for key, value in record.items() if value is not None}


def repo_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.replace("\\", "/").strip()
    if raw.startswith("/") or ":" in raw:
        return None
    normalized = raw.strip("/")
    if not normalized:
        return "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        return None
    return path.as_posix()


def path_within(path: str, parent: str) -> bool:
    if parent == ".":
        return True
    return path == parent or path.startswith(f"{parent}/")


def normalize_path_list(policy: dict[str, Any], field: str) -> list[str]:
    values = policy.get(field, [])
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"loop policy {field} must be a list")
    normalized: list[str] = []
    for index, value in enumerate(values):
        path = repo_relative_path(value)
        if path is None:
            raise ValueError(f"loop policy {field}[{index}] must be repo-relative")
        normalized.append(path)
    return normalized


def normalize_string_list(policy: dict[str, Any], field: str) -> list[str]:
    values = policy.get(field, [])
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"loop policy {field} must be a list of non-empty strings")
    return list(values)


def load_loop_policy(path: str | None) -> dict[str, Any]:
    if path is None:
        return {
            "source": None,
            "allowed_paths": [],
            "denied_paths": [],
            "required_checks": [],
            "max_executor_time_minutes": None,
            "stop_conditions": [],
        }
    source = Path(path)
    policy = read_json(source)
    if not isinstance(policy, dict):
        raise ValueError("loop policy must be a JSON object")
    if policy.get("schema_version") != LOOP_POLICY_SCHEMA_VERSION:
        raise ValueError("loop policy schema_version is invalid")
    max_minutes = policy.get("max_executor_time_minutes")
    if max_minutes is not None and (isinstance(max_minutes, bool) or not isinstance(max_minutes, int) or max_minutes <= 0):
        raise ValueError("loop policy max_executor_time_minutes must be a positive integer")
    return {
        "source": str(source),
        "allowed_paths": normalize_path_list(policy, "allowed_paths"),
        "denied_paths": normalize_path_list(policy, "denied_paths"),
        "required_checks": normalize_string_list(policy, "required_checks"),
        "max_executor_time_minutes": max_minutes,
        "stop_conditions": normalize_string_list(policy, "stop_conditions"),
    }


def resolve_executor_policy(
    policy: dict[str, Any],
    *,
    requested_allowed_paths: list[str],
    requested_required_checks: list[str],
    requested_max_minutes: int | None,
    requested_stop_conditions: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    policy_allowed = policy["allowed_paths"]
    denied_paths = policy["denied_paths"]
    allowed_paths = list(requested_allowed_paths or policy_allowed or ["."])
    normalized_allowed = []
    for path in allowed_paths:
        normalized = repo_relative_path(path)
        if normalized is None:
            raise ValueError(f"executor allowed path {path} must be repo-relative")
        if any(path_within(normalized, denied) or path_within(denied, normalized) for denied in denied_paths):
            return {"reason": f"executor allowed path {normalized} is denied by policy"}, policy
        if policy_allowed and not any(path_within(normalized, allowed) for allowed in policy_allowed):
            return {"reason": f"executor allowed path {normalized} is outside policy allowed_paths"}, policy
        normalized_allowed.append(normalized)
    max_policy_minutes = policy["max_executor_time_minutes"]
    max_minutes = requested_max_minutes if requested_max_minutes is not None else 30
    if max_policy_minutes is not None:
        if requested_max_minutes is not None and requested_max_minutes > max_policy_minutes:
            return {"reason": "executor time limit exceeds policy max_executor_time_minutes"}, policy
        max_minutes = requested_max_minutes if requested_max_minutes is not None else max_policy_minutes
    required_checks = list(dict.fromkeys([*policy["required_checks"], *requested_required_checks]))
    stop_conditions = list(dict.fromkeys([*policy["stop_conditions"], *requested_stop_conditions]))
    return None, {
        **policy,
        "effective_allowed_paths": normalized_allowed,
        "effective_required_checks": required_checks,
        "effective_max_minutes": max_minutes,
        "effective_stop_conditions": stop_conditions,
    }
