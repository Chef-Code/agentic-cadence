from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from codex_cadence.approvals import OPERATOR_APPROVAL_SCHEMA_VERSION
from codex_cadence.branch_policy import normalize_branch_policy
from codex_cadence.executor_contract import EXECUTION_RUN_CLOSEOUT_STATUSES
from codex_cadence.store import exclusive_lock, lock_path, read_json, utc_now

AUDIT_SCHEMA_VERSION = "cadence-audit.v1"
AUDIT_REPLAY_SCHEMA_VERSION = "audit-replay.v1"
AUDIT_CHAIN_SCHEMA_VERSION = "cadence-audit-chain.v1"
AUDIT_CHAIN_ROOT_HASH = "sha256:" + "0" * 64
LOOP_POLICY_SCHEMA_VERSION = "cadence-loop-policy.v1"
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
AUDIT_REPLAY_UPGRADE_BLOCKERS = {
    "audit_schema_version_unsupported",
    "audit_event_unsupported",
    "unsupported_audit_chain_record",
}
AUDIT_CHAIN_FIELDS = {
    "audit_chain_version",
    "chain_index",
    "previous_event_hash",
    "event_hash",
}
AUDIT_CHAIN_REPAIR_BLOCKERS = {
    "audit_chain_missing",
    "audit_chain_broken",
    "audit_event_hash_mismatch",
    "audit_chain_index_duplicate",
}
EXECUTION_RUN_AUDIT_ACTIONS = {"record_execution_run", "update_execution_run_closeout"}


def checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_event_hash(record: dict[str, Any]) -> str:
    """Return the deterministic hash for an audit record without its own hash field."""
    event_payload = {key: value for key, value in record.items() if key != "event_hash"}
    return checksum_json(event_payload)


def audit_events_path(root: Path) -> Path:
    return root / "audit" / "events.jsonl"


def audit_replay_blocker(code: str, message: str, line: int | None = None) -> dict[str, Any]:
    """Build a stable audit replay blocker object."""
    blocker: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        blocker["line"] = line
    return blocker


def audit_replay_recommendation(
    blockers: list[dict[str, Any]],
    *,
    records_seen: int,
    chain_records: int,
    legacy_chain_roots: int,
) -> str:
    """Choose the command-local recommendation for replay blockers."""
    if not blockers:
        if records_seen == 0:
            return "start_new_audit_chain"
        if legacy_chain_roots:
            return "continue_with_legacy_chain_root"
        return "use_audit_replay_evidence"
    codes = {blocker.get("code") for blocker in blockers}
    if codes and codes <= AUDIT_REPLAY_UPGRADE_BLOCKERS:
        return "upgrade_cadence"
    if any(code in AUDIT_CHAIN_REPAIR_BLOCKERS for code in codes):
        return "repair_audit_history"
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
    chain_head: str | None = None,
    chain_records: int = 0,
    legacy_chain_roots: int = 0,
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
        "audit_chain_version": AUDIT_CHAIN_SCHEMA_VERSION,
        "chain_head": chain_head,
        "chain_records": chain_records,
        "legacy_chain_roots": legacy_chain_roots,
        "blockers": blockers,
        "recommended_next_action": audit_replay_recommendation(
            blockers,
            records_seen=records_seen,
            chain_records=chain_records,
            legacy_chain_roots=legacy_chain_roots,
        ),
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


def validate_execution_run_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate local execution-run audit-record fields."""
    blockers: list[dict[str, Any]] = []
    for field in (
        "action",
        "reason",
        "run_id",
        "invocation_id",
        "task_id",
        "repo",
        "branch",
        "head",
        "task_file",
        "result_file",
        "run_record_file",
        "closeout_status",
    ):
        blockers.extend(required_string(record, field, line))
    for field in (
        "payload_checksum",
        "run_record_checksum",
        "task_packet_checksum",
        "result_evidence_checksum",
        "validation_packet_checksum",
    ):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("action") not in EXECUTION_RUN_AUDIT_ACTIONS:
        blockers.append(
            audit_replay_blocker(
                "audit_execution_run_action_invalid",
                "execution_run_record action is invalid",
                line,
            )
        )
    if record.get("closeout_status") not in EXECUTION_RUN_CLOSEOUT_STATUSES:
        blockers.append(
            audit_replay_blocker(
                "audit_execution_run_closeout_status_invalid",
                "execution_run_record closeout_status is invalid",
                line,
            )
        )
    if record.get("closeout_status") not in (None, "pending"):
        blockers.extend(required_string(record, "epoch_id", line))
        blockers.extend(required_checksum_present(record, "epoch_closeout_checksum", line))
    return blockers


def validate_git_pr_materialization_intent_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate operator-approved Git/PR materialization intent audit fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("action", "reason", "plan_file", "repo", "branch", "head", "base_branch", "proposed_branch", "remote", "remote_url"):
        blockers.extend(required_string(record, field, line))
    for field in ("payload_checksum", "plan_checksum", "intended_side_effects_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("action") not in (None, "materialize_git_pr_plan"):
        blockers.append(
            audit_replay_blocker(
                "audit_materialization_action_invalid",
                "git_pr_materialization_intent action must be materialize_git_pr_plan",
                line,
            )
        )
    return blockers


def validate_git_pr_materialization_result_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate operator-approved Git/PR materialization result audit fields."""
    blockers: list[dict[str, Any]] = []
    for field in (
        "action",
        "reason",
        "materialization_status",
        "plan_file",
        "repo",
        "branch",
        "head",
        "base_branch",
        "proposed_branch",
        "remote",
        "remote_url",
    ):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "valid", line))
    for field in ("payload_checksum", "plan_checksum", "side_effects_checksum", "command_trace_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    valid = record.get("valid")
    action = record.get("action")
    status = record.get("materialization_status")
    expected_action = "materialized" if valid is True else "blocked" if valid is False else None
    expected_status = "completed" if valid is True else "blocked" if valid is False else None
    if expected_action is not None and action != expected_action:
        blockers.append(
            audit_replay_blocker(
                "audit_materialization_action_invalid",
                f"git_pr_materialization_result action must be {expected_action} when valid is {valid}",
                line,
            )
        )
    if expected_status is not None and status != expected_status:
        blockers.append(
            audit_replay_blocker(
                "audit_materialization_status_invalid",
                f"git_pr_materialization_result materialization_status must be {expected_status} when valid is {valid}",
                line,
            )
        )
    return blockers


def validate_operator_approval_verification_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate accepted operator approval identity verification audit fields."""
    blockers: list[dict[str, Any]] = []
    for field in (
        "action",
        "reason",
        "approval_status",
        "approval_schema_version",
        "purpose",
        "operator_id",
        "key_id",
        "issued_at",
        "expires_at",
    ):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "valid", line))
    for field in ("payload_checksum", "target_checksum", "approval_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("action") != "verify_operator_approval":
        blockers.append(
            audit_replay_blocker(
                "audit_operator_approval_action_invalid",
                "operator_approval_verification action must be verify_operator_approval",
                line,
            )
        )
    if record.get("approval_status") != "accepted":
        blockers.append(
            audit_replay_blocker(
                "audit_operator_approval_status_invalid",
                "operator_approval_verification approval_status must be accepted",
                line,
            )
        )
    if record.get("valid") is not True:
        blockers.append(
            audit_replay_blocker(
                "audit_operator_approval_valid_invalid",
                "operator_approval_verification audit records must describe accepted valid approvals",
                line,
            )
        )
    if record.get("approval_schema_version") != OPERATOR_APPROVAL_SCHEMA_VERSION:
        blockers.append(
            audit_replay_blocker(
                "audit_operator_approval_schema_invalid",
                "operator_approval_verification approval_schema_version must be operator-approval.v1",
                line,
            )
        )
    return blockers


def validate_execution_start_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate governed execution-start audit fields."""
    blockers: list[dict[str, Any]] = []
    for field in ("action", "reason", "task_file", "task_checksum", "repo", "branch", "head"):
        blockers.extend(required_string(record, field, line))
    for field in ("valid", "epoch_started", "executor_started"):
        blockers.extend(required_bool(record, field, line))
    blockers.extend(required_checksum_present(record, "payload_checksum", line))
    if record.get("valid") is True:
        blockers.extend(required_string(record, "epoch_id", line))
    if "ownership_id" in record or "ownership_record_checksum" in record:
        blockers.extend(required_string(record, "ownership_id", line))
        blockers.extend(required_checksum_present(record, "ownership_record_checksum", line))
    return blockers


def validate_work_ownership_mutation_audit_record(record: dict[str, Any], line: int) -> list[dict[str, Any]]:
    """Validate local work-ownership mutation audit fields."""
    blockers: list[dict[str, Any]] = []
    for field in (
        "action",
        "reason",
        "ownership_id",
        "ownership_status",
        "task_id",
        "candidate_id",
        "role",
        "claimer",
        "repo",
        "branch",
        "head",
        "record_file",
    ):
        blockers.extend(required_string(record, field, line))
    blockers.extend(required_bool(record, "valid", line))
    for field in ("payload_checksum", "ownership_record_checksum"):
        blockers.extend(required_checksum_present(record, field, line))
    if record.get("action") not in {"claim_work_ownership", "close_work_ownership", "fail_work_ownership"}:
        blockers.append(
            audit_replay_blocker(
                "audit_work_ownership_action_invalid",
                "work_ownership_mutation action is invalid",
                line,
            )
        )
    expected_status_by_action = {
        "claim_work_ownership": "ACTIVE",
        "close_work_ownership": "CLOSED",
        "fail_work_ownership": "FAILED",
    }
    expected_status = expected_status_by_action.get(record.get("action"))
    if record.get("ownership_status") not in {"ACTIVE", "CLOSED", "FAILED"}:
        blockers.append(
            audit_replay_blocker(
                "audit_work_ownership_status_invalid",
                "work_ownership_mutation ownership_status is invalid",
                line,
            )
        )
    elif expected_status is not None and record.get("ownership_status") != expected_status:
        blockers.append(
            audit_replay_blocker(
                "audit_work_ownership_status_invalid",
                f"work_ownership_mutation ownership_status must be {expected_status} for action {record.get('action')}",
                line,
            )
        )
    if record.get("action") in {"close_work_ownership", "fail_work_ownership"}:
        blockers.extend(required_string(record, "closeout_status", line))
        if record.get("closeout_status") != expected_status:
            blockers.append(
                audit_replay_blocker(
                    "audit_work_ownership_status_invalid",
                    f"work_ownership_mutation closeout_status must be {expected_status} for action {record.get('action')}",
                    line,
                )
            )
    elif record.get("action") == "claim_work_ownership" and record.get("closeout_status") not in (None, ""):
        blockers.append(
            audit_replay_blocker(
                "audit_work_ownership_status_invalid",
                "claim_work_ownership audit records must not include closeout_status",
                line,
            )
        )
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
    elif event == "execution_run_record":
        blockers.extend(validate_execution_run_audit_record(record, line))
    elif event == "executor_result_validation":
        blockers.extend(validate_executor_result_audit_record(record, line))
    elif event == "executor_epoch_closeout":
        blockers.extend(validate_executor_epoch_closeout_audit_record(record, line))
    elif event == "git_pr_materialization_intent":
        blockers.extend(validate_git_pr_materialization_intent_audit_record(record, line))
    elif event == "git_pr_materialization_result":
        blockers.extend(validate_git_pr_materialization_result_audit_record(record, line))
    elif event == "operator_approval_verification":
        blockers.extend(validate_operator_approval_verification_audit_record(record, line))
    elif event == "execution_start_decision":
        blockers.extend(validate_execution_start_audit_record(record, line))
    elif event == "work_ownership_mutation":
        blockers.extend(validate_work_ownership_mutation_audit_record(record, line))
    else:
        blockers.append(audit_replay_blocker("audit_event_unsupported", f"unsupported audit event: {event}", line))
        return None, blockers

    return event if not blockers else None, blockers


def audit_chain_blockers(
    record: dict[str, Any],
    line: int,
    *,
    expected_previous_hash: str,
    seen_chain_indexes: set[int],
    chain_started: bool,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Validate hash-chain metadata and return this record's chain head."""
    present_fields = {field for field in AUDIT_CHAIN_FIELDS if field in record and record[field] not in (None, "")}
    if not present_fields:
        if chain_started:
            return [
                audit_replay_blocker(
                    "audit_chain_missing",
                    "chained audit history cannot continue with a legacy record",
                    line,
                )
            ], None, False
        return [], audit_event_hash(record), False

    blockers: list[dict[str, Any]] = []
    missing_fields = sorted(AUDIT_CHAIN_FIELDS - present_fields)
    for field in missing_fields:
        blockers.append(audit_replay_blocker("audit_chain_missing", f"{field} is required", line))
    if blockers:
        return blockers, None, True

    if record.get("audit_chain_version") != AUDIT_CHAIN_SCHEMA_VERSION:
        return [
            audit_replay_blocker(
                "unsupported_audit_chain_record",
                f"unsupported audit_chain_version: {record.get('audit_chain_version')}",
                line,
            )
        ], None, True

    chain_index = record.get("chain_index")
    if type(chain_index) is not int or chain_index < 1:
        blockers.append(audit_replay_blocker("audit_chain_missing", "chain_index must be a positive integer", line))
    elif chain_index in seen_chain_indexes:
        blockers.append(audit_replay_blocker("audit_chain_index_duplicate", "chain_index is duplicated", line))
    elif chain_index != line:
        blockers.append(audit_replay_blocker("audit_chain_broken", "chain_index must match the audit line", line))

    previous_hash = record.get("previous_event_hash")
    if not isinstance(previous_hash, str) or CHECKSUM_PATTERN.fullmatch(previous_hash) is None:
        blockers.append(
            audit_replay_blocker("audit_chain_missing", "previous_event_hash must be a sha256 checksum", line)
        )
    elif previous_hash != expected_previous_hash:
        blockers.append(audit_replay_blocker("audit_chain_broken", "previous_event_hash does not match chain head", line))

    event_hash = record.get("event_hash")
    computed_event_hash = audit_event_hash(record)
    if not isinstance(event_hash, str) or CHECKSUM_PATTERN.fullmatch(event_hash) is None:
        blockers.append(audit_replay_blocker("audit_event_hash_mismatch", "event_hash must be a sha256 checksum", line))
    elif event_hash != computed_event_hash:
        blockers.append(audit_replay_blocker("audit_event_hash_mismatch", "event_hash does not match record payload", line))

    if blockers:
        return blockers, None, True
    return [], computed_event_hash, True


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
    chain_head: str | None = None
    chain_records = 0
    legacy_chain_roots = 0
    seen_chain_indexes: set[int] = set()
    chain_started = False
    expected_previous_hash = AUDIT_CHAIN_ROOT_HASH
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
                chain_blockers, record_chain_head, chained = audit_chain_blockers(
                    record,
                    lines_seen,
                    expected_previous_hash=expected_previous_hash,
                    seen_chain_indexes=seen_chain_indexes,
                    chain_started=chain_started,
                )
                if chain_blockers:
                    records_invalid += 1
                    blockers.extend(chain_blockers)
                    continue
                records_valid += 1
                if record_chain_head is not None:
                    chain_head = record_chain_head
                    expected_previous_hash = record_chain_head
                if chained:
                    chain_started = True
                    chain_records += 1
                    seen_chain_indexes.add(record["chain_index"])
                else:
                    legacy_chain_roots += 1
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
        chain_head=chain_head,
        chain_records=chain_records,
        legacy_chain_roots=legacy_chain_roots,
    )


def audit_append_chain_tip(target: Path) -> tuple[str, int]:
    """Return the previous event hash and next chain index for a new append."""
    if not target.exists():
        return AUDIT_CHAIN_ROOT_HASH, 1
    if not target.is_file():
        raise OSError("audit path exists but is not a regular file")
    if target.stat().st_size > 0:
        with target.open("rb") as raw:
            raw.seek(-1, 2)
            if raw.read(1) != b"\n":
                raise ValueError("cannot append after unterminated audit line")

    expected_previous_hash = AUDIT_CHAIN_ROOT_HASH
    seen_chain_indexes: set[int] = set()
    chain_started = False
    last_line = 0
    with target.open("r", encoding="utf-8") as handle:
        for last_line, line in enumerate(handle, start=1):
            text = line.rstrip("\r\n")
            if not text.strip():
                raise ValueError("cannot append after blank audit line")
            record = json.loads(text, parse_constant=reject_json_constant)
            event, record_blockers = validate_audit_record(record, last_line)
            if event is None or record_blockers:
                raise ValueError("cannot append after invalid audit record")
            chain_blockers, record_chain_head, chained = audit_chain_blockers(
                record,
                last_line,
                expected_previous_hash=expected_previous_hash,
                seen_chain_indexes=seen_chain_indexes,
                chain_started=chain_started,
            )
            if chain_blockers or record_chain_head is None:
                raise ValueError("cannot append after invalid audit chain")
            expected_previous_hash = record_chain_head
            if chained:
                chain_started = True
                seen_chain_indexes.add(record["chain_index"])

    return expected_previous_hash, last_line + 1


def append_audit_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    target = audit_events_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = dict(record)
    line.setdefault("schema_version", AUDIT_SCHEMA_VERSION)
    line.setdefault("recorded_at", utc_now())
    with exclusive_lock(lock_path(root, "audit-events")):
        previous_event_hash, chain_index = audit_append_chain_tip(target)
        line["audit_chain_version"] = AUDIT_CHAIN_SCHEMA_VERSION
        line["chain_index"] = chain_index
        line["previous_event_hash"] = previous_event_hash
        line["event_hash"] = audit_event_hash(line)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "event": line.get("event"),
        "path": str(target),
        "recorded_at": line.get("recorded_at"),
        "audit_chain_version": line.get("audit_chain_version"),
        "chain_index": line.get("chain_index"),
        "previous_event_hash": line.get("previous_event_hash"),
        "event_hash": line.get("event_hash"),
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


def execution_run_record_audit_record(
    run_record: dict[str, Any],
    *,
    run_record_file: str,
    action: str,
    reason: str,
) -> dict[str, Any]:
    repo = run_record.get("repo") if isinstance(run_record.get("repo"), dict) else {}
    record = {
        "event": "execution_run_record",
        "action": action,
        "reason": reason,
        "run_id": run_record.get("run_id"),
        "invocation_id": run_record.get("invocation_id"),
        "task_id": run_record.get("task_id"),
        "repo": repo.get("name"),
        "branch": repo.get("branch"),
        "head": repo.get("head"),
        "task_file": run_record.get("task_file"),
        "result_file": run_record.get("result_file"),
        "run_record_file": run_record_file,
        "closeout_status": run_record.get("closeout_status"),
        "epoch_id": run_record.get("epoch_id"),
        "payload_checksum": checksum_json(
            {
                "action": action,
                "reason": reason,
                "run_record_file": run_record_file,
                "run_record_checksum": checksum_json(run_record),
            }
        ),
        "run_record_checksum": checksum_json(run_record),
        "task_packet_checksum": run_record.get("task_packet_checksum"),
        "result_evidence_checksum": run_record.get("result_evidence_checksum"),
        "validation_packet_checksum": run_record.get("validation_packet_checksum"),
        "epoch_closeout_checksum": run_record.get("epoch_closeout_checksum"),
    }
    return {key: value for key, value in record.items() if value is not None}


def execution_start_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    repo = payload.get("repo") if isinstance(payload.get("repo"), dict) else {}
    ownership = payload.get("ownership") if isinstance(payload.get("ownership"), dict) else {}
    record = {
        "event": "execution_start_decision",
        "action": payload.get("recommended_next_action"),
        "reason": payload.get("reason"),
        "valid": payload.get("valid"),
        "epoch_started": payload.get("epoch_started"),
        "executor_started": payload.get("executor_started"),
        "epoch_id": payload.get("epoch_id"),
        "task_id": payload.get("task_id"),
        "task_file": payload.get("task_file"),
        "task_checksum": payload.get("task_checksum"),
        "repo": repo.get("name"),
        "branch": repo.get("branch"),
        "head": repo.get("head"),
        "payload_checksum": checksum_json(payload),
        "ownership_id": ownership.get("id"),
        "ownership_record_checksum": checksum_json(ownership) if ownership else None,
    }
    return {key: value for key, value in record.items() if value is not None}


def operator_approval_verification_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    record = {
        "event": "operator_approval_verification",
        "action": "verify_operator_approval",
        "reason": payload.get("reason"),
        "valid": payload.get("valid"),
        "approval_status": "accepted" if payload.get("valid") is True else "blocked",
        "approval_schema_version": payload.get("approval_schema_version"),
        "target_checksum": payload.get("target_checksum"),
        "approval_checksum": payload.get("approval_checksum"),
        "purpose": payload.get("purpose"),
        "operator_id": payload.get("operator_id"),
        "key_id": payload.get("key_id"),
        "issued_at": payload.get("issued_at"),
        "expires_at": payload.get("expires_at"),
        "payload_checksum": checksum_json(payload),
    }
    return {key: value for key, value in record.items() if value is not None}


def work_ownership_mutation_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    record_summary = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    packet = payload.get("packet")
    status = payload.get("closeout_status") or record_summary.get("status")
    if packet == "work_ownership_claim":
        action = "claim_work_ownership"
    elif status == "CLOSED":
        action = "close_work_ownership"
    elif status == "FAILED":
        action = "fail_work_ownership"
    else:
        action = payload.get("recommended_next_action")
    audit = {
        "event": "work_ownership_mutation",
        "action": action,
        "reason": payload.get("reason"),
        "valid": payload.get("valid"),
        "ownership_id": payload.get("ownership_id") or record_summary.get("id"),
        "ownership_status": status,
        "closeout_status": payload.get("closeout_status"),
        "task_id": record_summary.get("task_id") or request.get("task_id"),
        "candidate_id": record_summary.get("candidate_id"),
        "role": record_summary.get("role"),
        "claimer": record_summary.get("claimer") or request.get("claimer"),
        "repo": record_summary.get("repo") or request.get("repo"),
        "branch": record_summary.get("branch") or request.get("branch"),
        "head": record_summary.get("head") or request.get("head"),
        "record_file": record_summary.get("path"),
        "payload_checksum": checksum_json(payload),
        "ownership_record_checksum": checksum_json(record_summary),
    }
    return {key: value for key, value in audit.items() if value is not None}


def git_pr_materialization_intent_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    repository = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    record = {
        "event": "git_pr_materialization_intent",
        "action": "materialize_git_pr_plan",
        "reason": "operator_approved_git_pr_materialization_intent",
        "repo": repository.get("repository_path"),
        "branch": repository.get("current_branch"),
        "head": repository.get("current_head"),
        "base_branch": repository.get("base_branch"),
        "proposed_branch": payload.get("proposed_branch"),
        "remote": payload.get("remote"),
        "remote_url": payload.get("remote_url"),
        "pr_number": payload.get("pr_number"),
        "plan_file": payload.get("plan_file"),
        "payload_checksum": checksum_json(payload),
        "plan_checksum": payload.get("plan_checksum"),
        "intended_side_effects_checksum": checksum_json(payload.get("intended_side_effects", [])),
    }
    return {key: value for key, value in record.items() if value is not None}


def git_pr_materialization_result_audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    repository = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    record = {
        "event": "git_pr_materialization_result",
        "action": payload.get("decision"),
        "reason": "operator_approved_git_pr_materialization_result",
        "valid": payload.get("valid"),
        "materialization_status": "completed" if payload.get("valid") is True else "blocked",
        "repo": repository.get("repository_path"),
        "branch": repository.get("current_branch"),
        "head": repository.get("current_head"),
        "base_branch": repository.get("base_branch"),
        "proposed_branch": payload.get("proposed_branch"),
        "remote": payload.get("remote"),
        "remote_url": payload.get("remote_url"),
        "pr_number": payload.get("pr_number"),
        "pr_url": payload.get("pr_url"),
        "plan_file": payload.get("plan_file"),
        "payload_checksum": checksum_json(payload),
        "plan_checksum": payload.get("plan_checksum"),
        "side_effects_checksum": checksum_json(payload.get("side_effects", [])),
        "command_trace_checksum": checksum_json(payload.get("command_trace", [])),
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
            "branch_policy": normalize_branch_policy(
                None,
                label="loop policy branch_policy",
                absent_allow_current_branch_main=True,
            ),
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
    if "branch_policy" in policy:
        branch_policy = normalize_branch_policy(
            policy.get("branch_policy"),
            label="loop policy branch_policy",
            require_object=True,
            missing_allow_current_branch_main=True,
        )
    else:
        branch_policy = normalize_branch_policy(
            None,
            label="loop policy branch_policy",
            absent_allow_current_branch_main=True,
        )
    return {
        "source": str(source),
        "allowed_paths": normalize_path_list(policy, "allowed_paths"),
        "denied_paths": normalize_path_list(policy, "denied_paths"),
        "allowed_commands": normalize_string_list(policy, "allowed_commands"),
        "denied_commands": normalize_string_list(policy, "denied_commands"),
        "required_checks": normalize_string_list(policy, "required_checks"),
        "max_executor_time_minutes": max_minutes,
        "stop_conditions": normalize_string_list(policy, "stop_conditions"),
        "branch_policy": branch_policy,
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
