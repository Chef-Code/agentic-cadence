from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence.store import read_json, utc_now

AUDIT_SCHEMA_VERSION = "cadence-audit.v1"
AUDIT_REPLAY_SCHEMA_VERSION = "audit-replay.v1"
LOOP_POLICY_SCHEMA_VERSION = "cadence-loop-policy.v1"
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
AUDIT_REPLAY_UPGRADE_BLOCKERS = {
    "audit_schema_version_unsupported",
    "audit_event_unsupported",
}


def checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_events_path(root: Path) -> Path:
    return root / "audit" / "events.jsonl"


def audit_replay_blocker(code: str, message: str, line: int | None = None) -> dict[str, Any]:
    """Build a stable audit replay blocker object."""
    blocker: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        blocker["line"] = line
    return blocker


def audit_replay_recommendation(blockers: list[dict[str, Any]]) -> str:
    """Choose the command-local recommendation for replay blockers."""
    if not blockers:
        return "use_audit_replay_evidence"
    codes = {blocker.get("code") for blocker in blockers}
    if codes and codes <= AUDIT_REPLAY_UPGRADE_BLOCKERS:
        return "upgrade_cadence"
    return "inspect_audit_log"


def audit_replay_packet(
    root: Path,
    *,
    audit_exists: bool,
    lines_seen: int,
    records_valid: int,
    records_invalid: int,
    events_by_type: dict[str, int],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the top-level audit-replay.v1 packet."""
    target = audit_events_path(root).expanduser().resolve(strict=False)
    records_seen = records_valid + records_invalid
    return {
        "protocol_version": "v1",
        "schema_version": AUDIT_REPLAY_SCHEMA_VERSION,
        "packet": "audit_replay",
        "audit_path": str(target),
        "audit_exists": audit_exists,
        "valid": not blockers,
        "lines_seen": lines_seen,
        "records_seen": records_seen,
        "records_valid": records_valid,
        "records_invalid": records_invalid,
        "events_by_type": events_by_type,
        "blockers": blockers,
        "recommended_next_action": audit_replay_recommendation(blockers),
    }


def required_string(record: dict[str, Any], field: str, line: int) -> list[dict[str, Any]]:
    """Validate a required non-empty string audit field."""
    if field not in record or record[field] is None or record[field] == "":
        return [audit_replay_blocker("audit_required_field_missing", f"{field} is required", line)]
    if not isinstance(record[field], str):
        return [audit_replay_blocker("audit_field_type_invalid", f"{field} must be a non-empty string", line)]
    return []


def required_bool(record: dict[str, Any], field: str, line: int) -> list[dict[str, Any]]:
    """Validate a required boolean audit field."""
    if field not in record or record[field] is None:
        return [audit_replay_blocker("audit_required_field_missing", f"{field} is required", line)]
    if not isinstance(record[field], bool):
        return [audit_replay_blocker("audit_field_type_invalid", f"{field} must be a boolean", line)]
    return []


def checksum_blocker(record: dict[str, Any], field: str, line: int, *, required: bool) -> list[dict[str, Any]]:
    """Validate checksum syntax for a present or required audit field."""
    if field not in record or record[field] is None or record[field] == "":
        if required:
            return [audit_replay_blocker("audit_required_field_missing", f"{field} is required", line)]
        return []
    if not isinstance(record[field], str) or CHECKSUM_PATTERN.fullmatch(record[field]) is None:
        return [audit_replay_blocker("audit_checksum_invalid", f"{field} must be a sha256 checksum", line)]
    return []


def required_checksum_present(record: dict[str, Any], field: str, line: int) -> list[dict[str, Any]]:
    """Validate that an event-required checksum field is present."""
    if field not in record or record[field] is None or record[field] == "":
        return [audit_replay_blocker("audit_required_field_missing", f"{field} is required", line)]
    return []


def present_checksum_blockers(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate every present audit checksum field once."""
    blockers: list[dict[str, Any]] = []
    for field in sorted(key for key in record if key.endswith("_checksum")):
        blockers.extend(checksum_blocker(record, field, line, required=False))
    return blockers


def validate_loop_tick_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate loop_tick_decision audit-record fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("tick_id", "action", "reason", "repo", "branch", "head", "snapshot_id"):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "operator_confirmation_required", line))
    blockers.extend(required_checksum_present(record, "payload_checksum", line))
    return blockers


def validate_executor_result_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate executor_result_validation audit-record fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("action", "reason", "task_file", "result_file"):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "valid", line))
    for field in ("payload_checksum", "task_packet_checksum", "result_evidence_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("valid") is True:
        for field in ("task_id", "repo", "branch", "head"):
            blockers.extend(required_string(record, field, line))
    return blockers


def validate_executor_epoch_closeout_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate executor_epoch_closeout audit-record fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("action", "reason", "epoch_id", "closeout_status", "task_file", "result_file", "snapshot_after_file"):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "valid", line))
    for field in ("payload_checksum", "task_packet_checksum", "result_evidence_checksum", "snapshot_after_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("valid") is True:
        for field in ("epoch_status", "task_id", "repo", "branch", "head"):
            blockers.extend(required_string(record, field, line))
    return blockers


def validate_executor_fixture_invocation_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate controlled fixture invocation audit-record fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("action", "reason", "task_file", "result_file", "command", "task_id", "repo", "branch", "head"):
        blockers.extend(required_string(record, field, line))
    for field in ("payload_checksum", "task_packet_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    return blockers


def validate_audit_record(record: Any, line: int) -> tuple[str | None, list[dict[str, Any]]]:
    """Validate one decoded audit record and return its countable event."""
    if not isinstance(record, dict):
        return None, [audit_replay_blocker("audit_record_not_object", "audit record must be a JSON object", line)]

    schema_version = record.get("schema_version")
    if schema_version is None:
        return None, [audit_replay_blocker("audit_schema_version_missing", "schema_version is required", line)]
    if not isinstance(schema_version, str):
        return None, [audit_replay_blocker("audit_schema_version_type_invalid", "schema_version must be a string", line)]
    if schema_version != AUDIT_SCHEMA_VERSION:
        return None, [
            audit_replay_blocker(
                "audit_schema_version_unsupported",
                f"unsupported audit schema_version: {schema_version}",
                line,
            )
        ]

    blockers: list[dict[str, Any]] = []
    blockers.extend(required_string(record, "recorded_at", line))
    blockers.extend(present_checksum_blockers(record, line))

    if "event" not in record or record["event"] is None or record["event"] == "":
        blockers.append(audit_replay_blocker("audit_event_missing", "event is required", line))
        return None, blockers
    if not isinstance(record["event"], str):
        blockers.append(audit_replay_blocker("audit_event_type_invalid", "event must be a string", line))
        return None, blockers

    event = record["event"]
    if event == "loop_tick_decision":
        blockers.extend(validate_loop_tick_audit_record(record, line))
    elif event == "executor_fixture_invocation":
        blockers.extend(validate_executor_fixture_invocation_audit_record(record, line))
    elif event == "executor_result_validation":
        blockers.extend(validate_executor_result_audit_record(record, line))
    elif event == "executor_epoch_closeout":
        blockers.extend(validate_executor_epoch_closeout_audit_record(record, line))
    else:
        blockers.append(audit_replay_blocker("audit_event_unsupported", f"unsupported audit event: {event}", line))
        return None, blockers

    return event if not blockers else None, blockers


def reject_json_constant(value: str) -> None:
    """Reject non-standard JSON constants accepted by Python's parser."""
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def replay_audit_log(root: Path) -> dict[str, Any]:
    """Replay the local audit JSONL log without mutating runtime state."""
    target = audit_events_path(root).expanduser().resolve(strict=False)
    if not target.exists():
        return audit_replay_packet(
            root,
            audit_exists=False,
            lines_seen=0,
            records_valid=0,
            records_invalid=0,
            events_by_type={},
            blockers=[],
        )
    if not target.is_file():
        return audit_replay_packet(
            root,
            audit_exists=True,
            lines_seen=0,
            records_valid=0,
            records_invalid=0,
            events_by_type={},
            blockers=[
                audit_replay_blocker(
                    "audit_path_not_file",
                    "audit path exists but is not a regular file",
                )
            ],
        )

    lines_seen = 0
    records_valid = 0
    records_invalid = 0
    events_by_type: dict[str, int] = {}
    blockers: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for lines_seen, line in enumerate(handle, start=1):
                text = line.rstrip("\r\n")
                if not text.strip():
                    records_invalid += 1
                    blockers.append(audit_replay_blocker("audit_line_blank", "audit line is blank", lines_seen))
                    continue
                try:
                    record = json.loads(text, parse_constant=reject_json_constant)
                except (json.JSONDecodeError, ValueError):
                    records_invalid += 1
                    blockers.append(
                        audit_replay_blocker("audit_line_invalid_json", "audit line is not valid JSON", lines_seen)
                    )
                    continue
                event, record_blockers = validate_audit_record(record, lines_seen)
                if record_blockers:
                    records_invalid += 1
                    blockers.extend(record_blockers)
                    continue
                records_valid += 1
                if event is not None:
                    events_by_type[event] = events_by_type.get(event, 0) + 1
    except UnicodeDecodeError:
        return audit_replay_packet(
            root,
            audit_exists=True,
            lines_seen=0,
            records_valid=0,
            records_invalid=0,
            events_by_type={},
            blockers=[audit_replay_blocker("audit_file_decode_failed", "audit file is not valid UTF-8")],
        )
    except OSError:
        return audit_replay_packet(
            root,
            audit_exists=True,
            lines_seen=0,
            records_valid=0,
            records_invalid=0,
            events_by_type={},
            blockers=[audit_replay_blocker("audit_file_unreadable", "audit file could not be read")],
        )

    return audit_replay_packet(
        root,
        audit_exists=True,
        lines_seen=lines_seen,
        records_valid=records_valid,
        records_invalid=records_invalid,
        events_by_type=events_by_type,
        blockers=blockers,
    )


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
        "repo": snapshot.get("repo") or snapshot.get("cwd"),
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
    task_packet: Any,
    result_evidence: Any,
) -> dict[str, Any]:
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
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


def executor_epoch_closeout_audit_record(
    payload: dict[str, Any],
    task_packet: Any,
    result_evidence: Any,
) -> dict[str, Any]:
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    next_decision = payload.get("next_decision") if isinstance(payload.get("next_decision"), dict) else {}
    record = {
        "event": "executor_epoch_closeout",
        "action": next_decision.get("decision") or payload.get("recommended_next_action"),
        "reason": payload.get("reason"),
        "valid": payload.get("valid"),
        "epoch_id": payload.get("epoch_id"),
        "epoch_status": payload.get("epoch_status"),
        "closeout_status": payload.get("closeout_status"),
        "failure_reason": payload.get("failure_reason"),
        "task_id": task.get("id"),
        "repo": repo.get("name"),
        "branch": repo.get("branch"),
        "head": repo.get("head"),
        "task_file": payload.get("task_file"),
        "result_file": payload.get("result_file"),
        "snapshot_after_file": payload.get("snapshot_after_file"),
        "payload_checksum": checksum_json(payload),
        "task_packet_checksum": checksum_json(task_packet),
        "result_evidence_checksum": checksum_json(result_evidence),
        "snapshot_after_checksum": payload.get("snapshot_after_checksum"),
    }
    return {key: value for key, value in record.items() if value is not None}


def executor_fixture_invocation_audit_record(payload: dict[str, Any], task_packet: Any) -> dict[str, Any]:
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    record = {
        "event": "executor_fixture_invocation",
        "action": "start_controlled_executor_fixture",
        "reason": payload.get("reason"),
        "task_id": task.get("id"),
        "repo": repo.get("name"),
        "branch": repo.get("branch"),
        "head": repo.get("head"),
        "task_file": payload.get("task_file"),
        "result_file": payload.get("result_file"),
        "command": payload.get("command"),
        "payload_checksum": checksum_json(payload),
        "task_packet_checksum": checksum_json(task_packet),
    }
    return {key: value for key, value in record.items() if value is not None}


def repo_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.replace("\\", "/").strip()
    if "\0" in raw:
        return None
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
    return [value.strip() for value in values]


def load_loop_policy(path: str | None) -> dict[str, Any]:
    if path is None:
        return {
            "source": None,
            "allowed_paths": [],
            "denied_paths": [],
            "allowed_commands": [],
            "denied_commands": [],
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
        "allowed_commands": normalize_string_list(policy, "allowed_commands"),
        "denied_commands": normalize_string_list(policy, "denied_commands"),
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
        "effective_allowed_commands": list(dict.fromkeys(policy["allowed_commands"])),
        "effective_denied_commands": list(dict.fromkeys(policy["denied_commands"])),
        "effective_required_checks": required_checks,
        "effective_max_minutes": max_minutes,
        "effective_stop_conditions": stop_conditions,
    }
