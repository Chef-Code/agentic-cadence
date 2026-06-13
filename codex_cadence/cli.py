#!/usr/bin/env python3
"""Agentic Cadence command line tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.approvals import OPERATOR_APPROVAL_PURPOSES, build_operator_approval_verification_packet
from codex_cadence.candidates import DISCOVERY_INTENTS, DISCOVERY_MODES, PROPOSAL_ALLOWANCES, CandidateBudget
from codex_cadence.candidates import discover_candidates
from codex_cadence.executor_contract import (
    DEFAULT_EXECUTOR_STOP_CONDITIONS,
    EXECUTION_START_SCHEMA_VERSION,
    execution_run_blocker,
    build_executor_task_packet,
    validate_executor_result_evidence,
    validate_execution_run_record,
    validate_executor_task_packet,
)
from codex_cadence.executor_invocation import (
    DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
    REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION,
    REAL_EXECUTOR_SIDE_EFFECT_MODES,
    _branch_ref_changes,
    _dirty_worktree_fingerprint,
    _local_branch_refs,
    _local_dirty_files,
    build_executor_invocation_plan,
    invoke_real_executor,
)
from codex_cadence.executor_readiness import evaluate_executor_invocation_readiness
from codex_cadence.executor_runner import run_controlled_executor_fixture
from codex_cadence.git_pr_plan import (
    evaluate_dirty_git_pr_materialization_plan,
    evaluate_git_pr_plan,
    git_pr_dirty_commit_materialization_load_error_packet,
    git_pr_materialization_dirty_commit_load_error_packet,
    git_pr_materialization_load_error_packet,
    git_pr_materialization_pr_evidence_load_error_packet,
    materialize_dirty_commit_plan,
    materialize_git_pr_plan,
    validate_git_pr_plan_dry_run_packet,
)
from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate, sync_github_evidence
from codex_cadence.epochs import complete_epoch as complete_epoch_record
from codex_cadence.epochs import CONTINUE, ASK_APPROVAL
from codex_cadence.epochs import EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION
from codex_cadence.epochs import closeout_executor_result_epoch
from codex_cadence.epochs import completed_continue_count
from codex_cadence.epochs import continuation_task_limit
from codex_cadence.epochs import epoch_elapsed_minutes
from codex_cadence.epochs import fail_epoch as fail_epoch_record
from codex_cadence.epochs import checksum_json as checksum_epoch_json
from codex_cadence.epochs import elect_candidates, load_active_epoch, read_active_epoch_records, record_self_check, self_check_decision
from codex_cadence.epochs import policy_limit
from codex_cadence.epochs import REPO_CONFIDENCE_VALUES, UNCERTAINTY_VALUES
from codex_cadence.epochs import start_epoch as start_epoch_record
from codex_cadence.epochs import validate_snapshot_after_epoch
from codex_cadence.handoff_loop import (
    DEFAULT_RESUME_CONTINUATION_MAX_AGE_MINUTES,
    prepare_handoff,
    resume_continuation,
    verify_resume,
)
from codex_cadence.model import BUCKETS, TASK_TYPES, estimate_task, governance_permissions, policy_for_bucket
from codex_cadence.ownership import (
    DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    claim_work_ownership,
    closeout_work_ownership,
    find_work_ownership_record,
    ownership_record_summary,
    validate_work_ownership,
    validate_work_ownership_record,
    work_ownership_status,
)
from codex_cadence.policy_audit import (
    append_audit_record,
    audit_event_hash,
    audit_events_path,
    controlled_loop_tick_audit_record,
    execution_start_audit_record,
    execution_run_record_audit_record,
    executor_epoch_closeout_audit_record,
    executor_result_validation_audit_record,
    load_loop_policy,
    loop_tick_audit_record,
    operator_approval_verification_audit_record,
    real_executor_invocation_audit_record,
    replay_audit_log,
    resolve_executor_policy,
    validate_audit_record,
    validate_controlled_loop_tick_audit_record,
    work_ownership_mutation_audit_record,
)
from codex_cadence.pr_cycle import compose_controlled_pr_cycle_from_files
from codex_cadence.pr_readiness import (
    evaluate_pr_body_preflight,
    evaluate_pr_readiness,
    load_pr_body,
    load_pr_json,
    load_template_sections,
)
from codex_cadence.review_response import (
    evaluate_review_thread_resolution_plan,
    evaluate_review_response_materialization_plan,
    evaluate_review_response_plan,
    materialize_review_thread_resolution_plan,
    materialize_review_response_plan,
)
from codex_cadence.roles import evaluate_role_readiness
from codex_cadence.release import evaluate_release_dry_run
from codex_cadence.repo_state import (
    current_repo_evidence,
    git_repo_root,
    path_is_relative_to,
    runtime_root_location_safety_issue,
    runtime_root_safety_issue,
    snapshot_repo,
    validate_repo_snapshot,
)
from codex_cadence.store import (
    HANDOFF_STATES,
    approval_path,
    atomic_write_json,
    brake_path,
    default_root,
    ensure_layout,
    epoch_path,
    exclusive_lock,
    execution_run_dir,
    execution_run_path,
    handoff_path,
    handoff_state_dir,
    lock_path,
    read_brake,
    read_json,
    record_lock_path,
    real_executor_invocation_dir,
    real_executor_invocation_path,
    snapshot_path,
    utc_now,
)

def emit(data: dict[str, Any] | list[dict[str, Any]]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "handoff")[:48].strip("-") or "handoff"


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def positive_int(value: str) -> int:
    parsed = non_negative_int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def cadence_state(brake: dict[str, Any]) -> dict[str, Any]:
    legacy_brake = brake["status"]
    state_by_brake = {
        "DRIVE": "PLAY_ON",
        "NEUTRAL": "HUDDLE",
        "PARK": "TIMEOUT",
    }
    return {
        "state": state_by_brake[legacy_brake],
        "legacy_brake": legacy_brake,
        "can_start_work": legacy_brake == "DRIVE",
        "requires_operator_resume": legacy_brake == "PARK",
    }


def generate_handoff_id(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{slugify(title)}-{secrets.token_hex(4)}"


def parse_metadata(values: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"metadata must be key=value, got: {value}")
        key, item = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"metadata key cannot be empty: {value}")
        metadata[key] = item
    return metadata


def read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    if args.message is not None:
        return args.message
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("handoff message is required: use --message, --message-file, or stdin")


def checksum_message(message: str) -> str:
    return "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()


def checksum_json(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_estimate(args: argparse.Namespace, message: str) -> dict[str, Any] | None:
    task_type = getattr(args, "task_type", None)
    if not task_type:
        return None
    return estimate_task(
        title=args.title,
        message=message,
        task_type=task_type,
        drivers=getattr(args, "driver", []) or [],
    )


def estimate_input(args: argparse.Namespace) -> dict[str, Any] | None:
    task_type = getattr(args, "task_type", None)
    if not task_type:
        return None
    return {"task_type": task_type, "drivers": list(getattr(args, "driver", []) or [])}


def checksum_estimate_binding(title: str, message: str, source: dict[str, Any], estimate: dict[str, Any]) -> str:
    return checksum_json(
        {
            "title": title,
            "message_checksum": checksum_message(message),
            "estimate_input": source,
            "estimate": estimate,
        }
    )


def parse_json_file(path: str | None, default: Any) -> Any:
    if path is None:
        return default
    return read_json(Path(path))


def create_signature(handoff_id: str, checksum: str, status: str = "READY") -> str:
    return f"<!-- codex-handoff:{PROTOCOL_VERSION} id={handoff_id} status={status} sha={checksum.removeprefix('sha256:')} -->"


def create_handoff(args: argparse.Namespace) -> int:
    root = args.root
    ensure_layout(root)
    message = read_message(args)
    if not getattr(args, "task_type", None):
        raise ValueError("--task-type is required for new handoffs")
    estimate = build_estimate(args, message)
    source = estimate_input(args)
    now = utc_now()
    handoff_id = args.id or generate_handoff_id(args.title)
    checksum = checksum_message(message)
    data = {
        "protocol_version": PROTOCOL_VERSION,
        "id": handoff_id,
        "title": args.title,
        "status": "READY",
        "guardrail": args.guardrail,
        "repo": args.repo,
        "branch": args.branch,
        "created_at": now,
        "updated_at": now,
        "metadata": parse_metadata(args.metadata or []),
        "checksum": checksum,
        "signature": create_signature(handoff_id, checksum),
        "message": message,
        "estimate": estimate,
        "estimate_input": source,
        "estimate_checksum": checksum_estimate_binding(args.title, message, source, estimate) if estimate and source else None,
    }
    target = handoff_path(root, "ready", handoff_id)
    if target.exists():
        raise FileExistsError(f"handoff already exists: {handoff_id}")
    atomic_write_json(target, data)
    if args.signature_file:
        signature_path = Path(args.signature_file)
        signature_path.parent.mkdir(parents=True, exist_ok=True)
        signature_path.write_text(data["signature"] + "\n", encoding="utf-8")
    emit(data)
    return 0


def plan_task(args: argparse.Namespace) -> int:
    message = read_message(args)
    estimate = estimate_task(
        title=args.title,
        message=message,
        task_type=args.task_type,
        drivers=args.driver or [],
    )
    emit({"title": args.title, "repo": args.repo, "branch": args.branch, "estimate": estimate})
    return 0


def capture_repo_snapshot(
    root: Path,
    *,
    cwd: str | Path,
    repo: str | None,
    active_pr: int | None,
    known_failures: list[str],
    ci_status: str,
) -> dict[str, Any]:
    ensure_layout(root)
    snapshot = snapshot_repo(
        Path(cwd),
        repo=repo,
        active_pr=active_pr,
        known_failures=known_failures,
        ci_status=ci_status,
    )
    stamp = snapshot["captured_at"].replace(":", "").replace("-", "")
    snapshot_id = f"{stamp}-{slugify(repo or snapshot.get('branch') or 'repo')}-{secrets.token_hex(4)}"
    snapshot["id"] = snapshot_id
    target = snapshot_path(root, snapshot_id)
    if target.exists():
        raise FileExistsError(f"snapshot already exists: {snapshot_id}")
    snapshot["path"] = str(target)
    atomic_write_json(target, snapshot)
    return snapshot


def snapshot_repo_command(args: argparse.Namespace) -> int:
    snapshot = capture_repo_snapshot(
        args.root,
        cwd=args.cwd,
        repo=args.repo,
        active_pr=args.active_pr,
        known_failures=args.known_failure or [],
        ci_status=args.ci_status,
    )
    emit(snapshot)
    return 0


def prepare_handoff_command(args: argparse.Namespace) -> int:
    result = prepare_handoff(
        root=args.root,
        cwd=Path(args.cwd),
        handoff_id=args.id or generate_handoff_id(args.title),
        title=args.title,
        guardrail=args.guardrail,
        repo=args.repo,
        branch=args.branch,
        task_type=args.task_type,
        drivers=args.driver or [],
        summary=args.summary,
        ci_status=args.ci_status,
        next_actions=args.next_action or [],
    )
    emit(result)
    return 0


def persist_snapshot_record(root: Path, snapshot: dict[str, Any]) -> None:
    snapshot_id = snapshot.get("id")
    if not isinstance(snapshot_id, str):
        raise ValueError("snapshot id is required")
    target = snapshot_path(root, snapshot_id)
    if target.exists():
        existing = read_json(target)
        if checksum_json(existing) != checksum_json(snapshot):
            raise ValueError(f"snapshot record already exists with different content: {snapshot_id}")
        return
    atomic_write_json(target, snapshot)


def start_epoch_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "runtime")):
        brake = read_brake(args.root)
        if brake["status"] != "DRIVE":
            raise ValueError("start-epoch requires brake DRIVE")
        tasks = parse_json_file(args.tasks_file, [])
        snapshot_before = parse_json_file(args.snapshot_before_file, None)
        if not isinstance(tasks, list):
            raise ValueError("--tasks-file must contain a JSON list")
        if snapshot_before is not None and not isinstance(snapshot_before, dict):
            raise ValueError("--snapshot-before-file must contain a JSON object")
        epoch = start_epoch_record(args.root, args.repo, args.branch, tasks, snapshot_before)
    emit(epoch)
    return 0


def execution_start_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def executor_task_approval_token(task_packet: dict[str, Any]) -> str:
    return f"approve-executor-task:{checksum_json(task_packet)}"


def execution_start_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "handoff_to_executor"
    codes = {blocker.get("code") for blocker in blockers}
    if "ownership_record_missing" in codes:
        return "claim_work_ownership"
    if codes & {"ownership_closed", "ownership_stale", "duplicate_active_ownership"}:
        return "close_or_fail_active_ownership"
    if codes & {
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_field_type_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
        "ownership_required_field_missing",
        "ownership_record_unreadable",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_registry_state_invalid",
    }:
        return "repair_ownership_record"
    if codes & {
        "ownership_repo_mismatch",
        "ownership_branch_mismatch",
        "ownership_task_mismatch",
        "ownership_candidate_mismatch",
        "ownership_role_mismatch",
        "ownership_claimer_mismatch",
        "ownership_head_mismatch",
    }:
        return "refresh_ownership_evidence"
    if "ownership_record_write_failed" in codes or "ownership_rollback_failed" in codes:
        return "inspect_runtime_state"
    if "executor_task_invalid" in codes or "task_file_unreadable" in codes:
        return "fix_executor_task_packet"
    if "operator_approval_missing" in codes or "operator_approval_mismatch" in codes:
        return "approve_executor_task"
    if "brake_state_invalid" in codes:
        return "inspect_runtime_state"
    if "brake_not_drive" in codes:
        return "clear_brake"
    if "active_epoch_exists" in codes or "active_epoch_invalid" in codes:
        return "close_or_fail_active_epoch"
    if "audit_append_failed" in codes or "epoch_rollback_failed" in codes or "epoch_start_failed" in codes:
        return "inspect_runtime_state"
    return "recreate_executor_task"


def execution_start_reason(valid: bool, blockers: list[dict[str, Any]]) -> str:
    if valid:
        return "approved executor task started a governed epoch"
    if blockers:
        return blockers[0]["message"]
    return "execution start blocked"


def execution_start_ownership_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def validate_execution_start_ownership(
    *,
    root: Path,
    target: str,
    cwd: Path,
    repo_packet: dict[str, Any],
    task_packet: dict[str, Any],
    role: str | None,
    claimer: str | None,
    max_age_minutes: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, Path | None, dict[str, Any] | None]:
    """Validate active ownership evidence for a governed execution start."""
    task = task_packet["task"]
    task_id = task["id"]
    validation = validate_work_ownership(
        root=root,
        target=target,
        cwd=cwd,
        repo=repo_packet["name"],
        branch=repo_packet["branch"],
        task_id=task_id,
        require_active=True,
        max_age_minutes=max_age_minutes,
    )
    blockers = list(validation.get("blockers", []))
    summary = validation.get("record") if isinstance(validation.get("record"), dict) else None
    path = Path(summary["path"]) if isinstance(summary, dict) and isinstance(summary.get("path"), str) else None
    record: dict[str, Any] | None = None
    if path is not None and not blockers:
        try:
            loaded = read_json(path)
            if isinstance(loaded, dict):
                record = loaded
            else:
                blockers.append(
                    execution_start_ownership_blocker(
                        "ownership_record_invalid",
                        "work ownership record must be a JSON object",
                        path=str(path),
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                execution_start_ownership_blocker(
                    "ownership_record_unreadable",
                    f"work ownership record could not be read for execution start: {exc}",
                    path=str(path),
                )
            )
    if not role:
        blockers.append(
            execution_start_ownership_blocker(
                "ownership_required_field_missing",
                "ownership role is required when ownership evidence is supplied",
                field="ownership_role",
            )
        )
    if not claimer:
        blockers.append(
            execution_start_ownership_blocker(
                "ownership_required_field_missing",
                "ownership claimer is required when ownership evidence is supplied",
                field="ownership_claimer",
            )
        )
    if record is not None:
        if record.get("candidate_id") != task_id:
            blockers.append(
                execution_start_ownership_blocker(
                    "ownership_candidate_mismatch",
                    "work ownership candidate_id does not match executor task id",
                    expected_candidate_id=task_id,
                    actual_candidate_id=record.get("candidate_id"),
                    path=str(path) if path else None,
                )
            )
        if role and record.get("role") != role:
            blockers.append(
                execution_start_ownership_blocker(
                    "ownership_role_mismatch",
                    "work ownership role does not match requested execution-start role",
                    expected_role=role,
                    actual_role=record.get("role"),
                    path=str(path) if path else None,
                )
            )
        if claimer and record.get("claimer") != claimer:
            blockers.append(
                execution_start_ownership_blocker(
                    "ownership_claimer_mismatch",
                    "work ownership claimer does not match requested execution-start claimer",
                    expected_claimer=claimer,
                    actual_claimer=record.get("claimer"),
                    path=str(path) if path else None,
                )
            )
        if record.get("head") != repo_packet.get("head"):
            blockers.append(
                execution_start_ownership_blocker(
                    "ownership_head_mismatch",
                    "work ownership head does not match executor task repo.head",
                    expected_head=repo_packet.get("head"),
                    actual_head=record.get("head"),
                    path=str(path) if path else None,
                )
            )
    if record is not None:
        summary = ownership_record_summary(record, path, "active")
    return blockers, summary, path, record


def task_packet_to_epoch_task(task_packet: dict[str, Any]) -> dict[str, Any]:
    task = task_packet["task"]
    epoch_task = {
        "id": task["id"],
        "title": task.get("title"),
        "summary": task.get("summary"),
        "task_type": task["task_type"],
        "bucket": task.get("bucket"),
        "source": task.get("source"),
        "drivers": list(task.get("drivers", [])),
        "evidence": dict(task.get("evidence", {})),
        "executable": True,
        "executor_task_checksum": checksum_json(task_packet),
        "allowed_paths": list(task_packet.get("allowed_paths", [])),
        "required_checks": list(task_packet.get("required_checks", [])),
        "limits": dict(task_packet.get("limits", {})),
        "stop_conditions": list(task_packet.get("stop_conditions", [])),
        "command_policy": dict(task_packet.get("command_policy", {})),
        "branch_policy": dict(task_packet.get("branch_policy", {})),
        "expected_output": dict(task_packet.get("expected_output", {})),
        "permissions": dict(task_packet.get("permissions", {})),
    }
    for field in ("requires_user_allowance", "allowance", "allowance_reason"):
        if field in task:
            epoch_task[field] = task[field]
    return epoch_task


def build_execution_start_packet(
    *,
    task_file: Path,
    task_packet: Any,
    task_checksum: str | None,
    approval_state: str,
    blockers: list[dict[str, Any]],
    repo: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    epoch: dict[str, Any] | None = None,
    ownership: dict[str, Any] | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, Any]:
    valid = not blockers
    task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTION_START_SCHEMA_VERSION,
        "packet": "execution_start",
        "read_only": False,
        "valid": valid,
        "epoch_started": epoch is not None,
        "executor_started": False,
        "pr_action_started": False,
        "approval_state": approval_state,
        "task_file": str(task_file),
        "task_checksum": task_checksum,
        "task_id": task.get("id"),
        "repo": repo or {},
        "snapshot": snapshot,
        "epoch_id": epoch.get("id") if isinstance(epoch, dict) else None,
        "ownership": ownership,
        "blockers": blockers,
        "recommended_next_action": execution_start_recommendation(blockers),
        "reason": execution_start_reason(valid, blockers),
        "side_effects": list(side_effects) if side_effects else None,
        "limitations": [
            "executor_not_started",
            "executor_invocation_out_of_scope",
            "git_pr_writes_out_of_scope",
            "merge_release_publish_out_of_scope",
        ],
    }
    return {key: value for key, value in payload.items() if value is not None}


def start_governed_execution_command(args: argparse.Namespace) -> int:
    root = args.root
    task_file = Path(args.task_file)
    blockers: list[dict[str, Any]] = []
    task_packet: Any = None
    task_checksum: str | None = None
    approval_state = "missing"
    repo_packet: dict[str, Any] | None = None
    repo_path: Path | None = None
    current_snapshot: dict[str, Any] | None = None
    epoch: dict[str, Any] | None = None
    audit_record: dict[str, Any] | None = None
    ownership_requested = bool(getattr(args, "ownership_target", None))
    ownership_summary: dict[str, Any] | None = None
    ownership_path: Path | None = None
    ownership_record_before: dict[str, Any] | None = None
    ownership_bound = False
    side_effects: list[str] = []

    try:
        task_packet = read_json(task_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(execution_start_blocker("task_file_unreadable", f"task file could not be read: {exc}"))

    if task_packet is not None:
        task_checksum = checksum_json(task_packet)
        valid_task, task_reason = validate_executor_task_packet(task_packet)
        if not valid_task:
            blockers.append(execution_start_blocker("executor_task_invalid", task_reason))
        else:
            expected_approval = executor_task_approval_token(task_packet)
            if not args.approval_token:
                blockers.append(
                    execution_start_blocker(
                        "operator_approval_missing",
                        "approval token is required for governed execution start",
                    )
                )
            elif args.approval_token != expected_approval:
                approval_state = "mismatch"
                blockers.append(
                    execution_start_blocker(
                        "operator_approval_mismatch",
                        "approval token does not match executor task checksum",
                    )
                )
            else:
                approval_state = "approved"

    if not blockers and isinstance(task_packet, dict):
        repo_packet = task_packet["repo"]
        repo_path = Path(args.cwd or repo_packet["path"]).expanduser().resolve()
        expected_repo_path = Path(repo_packet["path"]).expanduser().resolve()
        if repo_path != expected_repo_path:
            blockers.append(
                execution_start_blocker(
                    "repo_path_mismatch",
                    "current repo path does not match executor task repo.path",
                    expected=str(expected_repo_path),
                    actual=str(repo_path),
                )
            )

    if not blockers and isinstance(task_packet, dict) and isinstance(repo_packet, dict) and repo_path is not None:
        with exclusive_lock(lock_path(root, "runtime")):
            try:
                brake = read_brake(root)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                blockers.append(
                    execution_start_blocker(
                        "brake_state_invalid",
                        f"cadence brake state could not be read: {exc}",
                    )
                )
            else:
                if brake["status"] != "DRIVE":
                    blockers.append(
                        execution_start_blocker(
                            "brake_not_drive",
                            f"cadence brake is {brake['status']}; governed execution requires DRIVE",
                            brake_status=brake["status"],
                        )
                    )
            try:
                active_epochs = read_active_epoch_records(root)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                active_epochs = []
                blockers.append(
                    execution_start_blocker(
                        "active_epoch_invalid",
                        str(exc),
                    )
                )
            if active_epochs:
                blockers.append(
                    execution_start_blocker(
                        "active_epoch_exists",
                        "an active epoch already exists",
                        active_epoch_id=active_epochs[0][1].get("id"),
                    )
                )
            try:
                current_snapshot = snapshot_repo(
                    repo_path,
                    repo=repo_packet["name"],
                    active_pr=None,
                    known_failures=[],
                    ci_status=task_packet.get("snapshot", {}).get("ci", "unknown"),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                blockers.append(
                    execution_start_blocker(
                        "repo_inspection_failed",
                        f"current repo could not be inspected: {exc}",
                        path=str(repo_path),
                    )
                )
            if current_snapshot is not None:
                stamp = current_snapshot["captured_at"].replace(":", "").replace("-", "")
                current_snapshot["id"] = f"{stamp}-{slugify(repo_packet['name'])}-{secrets.token_hex(4)}"
                if current_snapshot.get("branch") != repo_packet["branch"]:
                    blockers.append(
                        execution_start_blocker(
                            "repo_branch_mismatch",
                            "current branch does not match executor task repo.branch",
                            expected=repo_packet["branch"],
                            actual=current_snapshot.get("branch"),
                        )
                    )
                if current_snapshot.get("head") != repo_packet["head"]:
                    blockers.append(
                        execution_start_blocker(
                            "repo_head_mismatch",
                            "current HEAD does not match executor task repo.head",
                            expected=repo_packet["head"],
                            actual=current_snapshot.get("head"),
                        )
                    )
                if current_snapshot.get("dirty_worktree") is not False:
                    blockers.append(
                        execution_start_blocker(
                            "dirty_worktree",
                            "current worktree must be clean before governed execution start",
                        )
                    )
                if current_snapshot.get("repo_confidence") == "low":
                    blockers.append(
                        execution_start_blocker(
                            "repo_confidence_low",
                            "current repo confidence is low",
                        )
                    )
            ownership_lock = exclusive_lock(lock_path(root, "work-ownership")) if ownership_requested else nullcontext()
            with ownership_lock:
                if not blockers and ownership_requested:
                    (
                        ownership_blockers,
                        ownership_summary,
                        ownership_path,
                        ownership_record_before,
                    ) = validate_execution_start_ownership(
                        root=root,
                        target=getattr(args, "ownership_target"),
                        cwd=repo_path,
                        repo_packet=repo_packet,
                        task_packet=task_packet,
                        role=getattr(args, "ownership_role", None),
                        claimer=getattr(args, "ownership_claimer", None),
                        max_age_minutes=getattr(
                            args,
                            "ownership_max_age_minutes",
                            DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
                        ),
                    )
                    blockers.extend(ownership_blockers)

                if not blockers:
                    try:
                        epoch = start_epoch_record(
                            root,
                            repo_packet["name"],
                            repo_packet["branch"],
                            [task_packet_to_epoch_task(task_packet)],
                            current_snapshot,
                            policy={"max_tasks_per_epoch": 1},
                        )
                    except (RuntimeError, ValueError, FileExistsError) as exc:
                        blockers.append(execution_start_blocker("epoch_start_failed", str(exc)))

                if epoch is not None and not blockers and ownership_requested:
                    try:
                        if ownership_path is None or ownership_record_before is None:
                            raise ValueError("validated work ownership record is missing")
                        updated_ownership = dict(ownership_record_before)
                        updated_ownership["epoch_id"] = epoch["id"]
                        updated_ownership["updated_at"] = utc_now()
                        ownership_write_blockers = validate_work_ownership_record(
                            updated_ownership,
                            path=ownership_path,
                            state="active",
                            expected_id=ownership_path.stem,
                            expected_repo=repo_packet["name"],
                            expected_branch=repo_packet["branch"],
                            expected_task_id=task_packet["task"]["id"],
                            require_active=True,
                            max_age_minutes=None,
                        )
                        if ownership_write_blockers:
                            blockers.extend(ownership_write_blockers)
                        else:
                            atomic_write_json(ownership_path, updated_ownership)
                            ownership_bound = True
                            ownership_summary = ownership_record_summary(updated_ownership, ownership_path, "active")
                            side_effects.append("work_ownership_epoch_bound")
                    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                        blockers.append(
                            execution_start_blocker(
                                "ownership_record_write_failed",
                                f"work ownership record could not be bound to started epoch: {exc}",
                                path=str(ownership_path) if ownership_path else None,
                            )
                        )

                if epoch is not None and blockers:
                    try:
                        epoch_path(root, "active", epoch["id"]).unlink()
                        epoch = None
                    except FileNotFoundError:
                        epoch = None
                    except OSError as rollback_exc:
                        blockers.append(
                            execution_start_blocker(
                                "epoch_rollback_failed",
                                f"active epoch could not be rolled back after execution-start failure: {rollback_exc}",
                                epoch_id=epoch.get("id"),
                            )
                        )

                if epoch is not None and not blockers:
                    provisional_packet = build_execution_start_packet(
                        task_file=task_file,
                        task_packet=task_packet,
                        task_checksum=task_checksum,
                        approval_state=approval_state,
                        blockers=blockers,
                        repo={
                            "name": repo_packet.get("name"),
                            "path": repo_packet.get("path"),
                            "branch": current_snapshot.get("branch"),
                            "head": current_snapshot.get("head"),
                            "expected_branch": repo_packet.get("branch"),
                            "expected_head": repo_packet.get("head"),
                        },
                        snapshot=current_snapshot,
                        epoch=epoch,
                        ownership=ownership_summary,
                        side_effects=side_effects,
                    )
                    try:
                        audit_record = append_audit_record(root, execution_start_audit_record(provisional_packet))
                    except (OSError, RuntimeError, ValueError) as exc:
                        blockers.append(
                            execution_start_blocker(
                                "audit_append_failed",
                                f"execution-start audit record could not be written: {exc}",
                            )
                        )
                        try:
                            epoch_path(root, "active", epoch["id"]).unlink()
                            epoch = None
                        except FileNotFoundError:
                            epoch = None
                        except OSError as rollback_exc:
                            blockers.append(
                                execution_start_blocker(
                                    "epoch_rollback_failed",
                                    f"active epoch could not be rolled back after audit failure: {rollback_exc}",
                                    epoch_id=epoch.get("id"),
                                )
                            )
                        if ownership_bound and ownership_path is not None and ownership_record_before is not None:
                            try:
                                atomic_write_json(ownership_path, ownership_record_before)
                                ownership_summary = ownership_record_summary(
                                    ownership_record_before,
                                    ownership_path,
                                    "active",
                                )
                                side_effects.append("work_ownership_epoch_binding_rollback")
                                ownership_bound = False
                            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as rollback_exc:
                                blockers.append(
                                    execution_start_blocker(
                                        "ownership_rollback_failed",
                                        f"work ownership epoch binding could not be rolled back after audit failure: {rollback_exc}",
                                        path=str(ownership_path),
                                    )
                                )

    repo = {
        "name": repo_packet.get("name"),
        "path": repo_packet.get("path"),
        "branch": current_snapshot.get("branch") if isinstance(current_snapshot, dict) else repo_packet.get("branch"),
        "head": current_snapshot.get("head") if isinstance(current_snapshot, dict) else repo_packet.get("head"),
        "expected_branch": repo_packet.get("branch"),
        "expected_head": repo_packet.get("head"),
    } if isinstance(repo_packet, dict) else None
    packet = build_execution_start_packet(
        task_file=task_file,
        task_packet=task_packet,
        task_checksum=task_checksum,
        approval_state=approval_state,
        blockers=blockers,
        repo=repo,
        snapshot=current_snapshot,
        epoch=epoch,
        ownership=ownership_summary,
        side_effects=side_effects,
    )
    if audit_record is not None and packet["valid"]:
        packet["audit_record"] = audit_record
    emit(packet)
    return 0 if packet["valid"] else 2


def complete_epoch_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "runtime")):
        if args.decision == "CONTINUE":
            brake = read_brake(args.root)
            if brake["status"] != "DRIVE":
                raise ValueError("CONTINUE requires brake to remain DRIVE")
        epoch = complete_epoch_record(args.root, args.epoch_id, args.decision, args.summary)
    emit(epoch)
    return 0


def fail_epoch_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "runtime")):
        epoch = fail_epoch_record(args.root, args.epoch_id, args.reason)
    emit(epoch)
    return 0


def parse_candidates_file(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = parse_json_file(path, [])
    if isinstance(payload, list):
        return payload, {}
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
        run_signals = payload.get("run_signals", {})
        if run_signals is None:
            run_signals = {}
        if not isinstance(run_signals, dict):
            raise ValueError("discover-candidates run_signals must be a JSON object")
        return payload["candidates"], run_signals
    raise ValueError("--candidates-file must contain a JSON list or discover-candidates JSON object")


def validated_discovery_signal(run_signals: dict[str, Any], key: str, allowed_values: tuple[str, ...]) -> str | None:
    value = run_signals.get(key)
    if value is None:
        return None
    if value not in allowed_values:
        allowed = ", ".join(allowed_values)
        raise ValueError(f"discover-candidates run_signals.{key} must be one of: {allowed}")
    return value


def conservative_signal(values: list[str | None], allowed_values: tuple[str, ...], default: str) -> str:
    ranks = {value: index for index, value in enumerate(allowed_values)}
    present = [value for value in values if value is not None]
    if not present:
        return default
    return max(present, key=lambda value: ranks[value])


def candidate_uncertainty_signal(candidates: list[dict[str, Any]]) -> str | None:
    values = []
    ranks = {value: index for index, value in enumerate(UNCERTAINTY_VALUES)}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("uncertainty")
        if value is None:
            continue
        if value not in UNCERTAINTY_VALUES:
            raise ValueError(f"candidate {index} uncertainty must be one of low, medium, high")
        values.append(value)
    return max(values, key=lambda value: ranks[value]) if values else None


def self_check_command(args: argparse.Namespace) -> int:
    brake = read_brake(args.root)
    candidates, discovery_run_signals = parse_candidates_file(args.candidates_file)
    discovery_repo_confidence = validated_discovery_signal(
        discovery_run_signals, "repo_confidence", REPO_CONFIDENCE_VALUES
    )
    discovery_uncertainty = validated_discovery_signal(discovery_run_signals, "uncertainty", UNCERTAINTY_VALUES)
    candidate_growth = validated_discovery_signal(discovery_run_signals, "candidate_growth", UNCERTAINTY_VALUES) or "low"
    candidate_uncertainty = candidate_uncertainty_signal(candidates)
    snapshot_after = parse_json_file(args.snapshot_after_file, None)
    if snapshot_after is not None and not isinstance(snapshot_after, dict):
        raise ValueError("--snapshot-after-file must contain a JSON object")
    epoch = load_active_epoch(args.root, args.epoch_id) if args.epoch_id else None
    epoch_policy = epoch.get("policy", {}) if isinstance(epoch, dict) else {"allow_recursive_discovery": args.allow_recursive_discovery}
    if not isinstance(epoch_policy, dict):
        raise ValueError("active epoch policy must be a JSON object")
    snapshot_before = epoch.get("snapshot_before") if isinstance(epoch, dict) else None
    if snapshot_before is not None and not isinstance(snapshot_before, dict):
        raise ValueError("active epoch snapshot_before must be a JSON object")
    snapshot_before_valid = False
    if snapshot_before is not None:
        snapshot_before_valid, snapshot_error = validate_repo_snapshot(
            snapshot_before,
            expected_repo=epoch.get("repo"),
            expected_branch=epoch.get("branch"),
        )
        if not snapshot_before_valid:
            raise ValueError(f"active epoch snapshot_before is invalid: {snapshot_error}")
    snapshot_after_valid = False
    if snapshot_after is not None:
        snapshot_after_valid, snapshot_error = validate_repo_snapshot(
            snapshot_after,
            expected_repo=epoch.get("repo") if epoch else None,
            expected_branch=epoch.get("branch") if epoch else None,
        )
        if not snapshot_after_valid:
            raise ValueError(f"snapshot_after is invalid: {snapshot_error}")
        if epoch:
            validate_snapshot_after_epoch(epoch, snapshot_after)
        persist_snapshot_record(args.root, snapshot_after)
    snapshot_before_confidence = None
    if isinstance(snapshot_before, dict) and snapshot_before.get("repo_confidence") in {"high", "medium", "low"}:
        snapshot_before_confidence = snapshot_before["repo_confidence"]
    snapshot_after_confidence = None
    if isinstance(snapshot_after, dict) and snapshot_after.get("repo_confidence") in {"high", "medium", "low"}:
        snapshot_after_confidence = snapshot_after["repo_confidence"]
    snapshot_repo_confidence = snapshot_after_confidence or snapshot_before_confidence or args.repo_confidence or "high"
    repo_confidence = conservative_signal(
        [snapshot_repo_confidence, discovery_repo_confidence], REPO_CONFIDENCE_VALUES, "high"
    )
    uncertainty = conservative_signal(
        [args.uncertainty, discovery_uncertainty, candidate_growth, candidate_uncertainty],
        UNCERTAINTY_VALUES,
        "low",
    )
    current_snapshot_ci = snapshot_after.get("ci") if isinstance(snapshot_after, dict) else None
    elapsed_minutes = epoch_elapsed_minutes(epoch) if epoch else None
    epoch_grounded = bool(args.epoch_id and snapshot_before_valid and snapshot_before_confidence)
    current_snapshot_grounded = bool(args.epoch_id and snapshot_after_valid and snapshot_after_confidence)
    stored_max_tasks = policy_limit(epoch_policy, "max_tasks_per_epoch") if epoch else args.max_tasks
    requested_max_tasks = min(args.max_tasks, stored_max_tasks)
    effective_max_tasks = continuation_task_limit(requested_max_tasks, uncertainty, args.epoch_health)
    stored_max_discovery_tasks = policy_limit(epoch_policy, "max_discovery_tasks_per_epoch") if epoch else None
    completed_continue_epochs = completed_continue_count(args.root) if epoch else 0
    elected_next = elect_candidates(candidates, effective_max_tasks, stored_max_discovery_tasks)
    decision = self_check_decision(
        brake_status=brake["status"],
        repo_confidence=repo_confidence,
        uncertainty=uncertainty,
        epoch_health=args.epoch_health,
        elected_next=elected_next,
        policy=epoch_policy,
        epoch_grounded=epoch_grounded,
        current_snapshot_grounded=current_snapshot_grounded,
        current_snapshot_ci=current_snapshot_ci,
        epoch_elapsed_minutes=elapsed_minutes,
        completed_continue_count=completed_continue_epochs,
    )
    if epoch and elected_next and decision["decision"] == CONTINUE and not current_snapshot_grounded:
        decision = {"decision": ASK_APPROVAL, "reason": "current repo snapshot required for continuation"}
    payload = {
        "epoch_id": args.epoch_id,
        "elected_next": elected_next,
        "epoch_grounded": epoch_grounded,
        "current_snapshot_grounded": current_snapshot_grounded,
        "repo_confidence": repo_confidence,
        "uncertainty": uncertainty,
        "candidate_growth": candidate_growth,
        "epoch_health": args.epoch_health,
        "brake_status": brake["status"],
        "requested_max_tasks": requested_max_tasks,
        "effective_max_tasks": effective_max_tasks,
        "completed_continue_count": completed_continue_epochs,
        "current_snapshot_ci": current_snapshot_ci,
        "epoch_elapsed_minutes": elapsed_minutes,
        "epoch_policy_checksum": checksum_epoch_json(epoch_policy),
        "snapshot_before_id": snapshot_before.get("id") if isinstance(snapshot_before, dict) else None,
        "snapshot_before_checksum": checksum_json(snapshot_before) if isinstance(snapshot_before, dict) else None,
        "snapshot_after_id": snapshot_after.get("id") if isinstance(snapshot_after, dict) else None,
        "snapshot_after_checksum": checksum_json(snapshot_after) if isinstance(snapshot_after, dict) else None,
        "self_check_id": f"self-check-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
        "checked_at": utc_now(),
        **decision,
    }
    if epoch:
        record_self_check(args.root, args.epoch_id, payload)
    emit(payload)
    return 0


def find_handoff(root: Path, handoff_id: str) -> tuple[str, Path, dict[str, Any]]:
    ensure_layout(root)
    matches: list[tuple[str, Path, dict[str, Any]]] = []
    for state in HANDOFF_STATES:
        path = handoff_path(root, state, handoff_id)
        if path.exists():
            matches.append((state, path, read_json(path)))
    if not matches:
        raise FileNotFoundError(f"handoff not found: {handoff_id}")
    if len(matches) > 1:
        states = ", ".join(match[0] for match in matches)
        raise RuntimeError(f"handoff exists in multiple states: {handoff_id} ({states})")
    return matches[0]


def move_handoff(root: Path, handoff_id: str, source_state: str, target_state: str, updates: dict[str, Any]) -> dict[str, Any]:
    with exclusive_lock(record_lock_path(root, "handoff", handoff_id)):
        current_state, source, data = find_handoff(root, handoff_id)
        if current_state != source_state:
            raise RuntimeError(f"handoff {handoff_id} is {current_state}, expected {source_state}")
        target = handoff_path(root, target_state, handoff_id)
        if target.exists():
            raise FileExistsError(f"handoff already exists in {target_state}: {handoff_id}")
        data.update(updates)
        data["status"] = target_state.upper()
        data["updated_at"] = utc_now()
        atomic_write_json(target, data)
        try:
            source.unlink()
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise
        return data


def approval_is_valid(root: Path, handoff: dict[str, Any]) -> bool:
    handoff_id = handoff.get("id")
    if not isinstance(handoff_id, str):
        return False
    path = approval_path(root, handoff_id)
    if not path.exists():
        return False
    approval = read_json(path)
    if not isinstance(approval, dict):
        return False
    return (
        approval.get("status") == "APPROVED"
        and approval.get("handoff_id") == handoff_id
        and approval.get("handoff_checksum") == handoff.get("checksum")
        and approval.get("estimate_checksum") == handoff.get("estimate_checksum")
        and isinstance(approval.get("approved_by"), str)
        and bool(approval.get("approved_by"))
    )


def pickup_is_approved(root: Path, handoff: dict[str, Any]) -> bool:
    return approval_is_valid(root, handoff)


def approve_handoff(args: argparse.Namespace) -> int:
    state, _path, handoff = find_handoff(args.root, args.handoff_id)
    if state != "ready":
        raise RuntimeError(f"handoff {args.handoff_id} is {state}, expected ready")
    estimate = handoff.get("estimate")
    if estimate is not None:
        malformed_block = validate_estimate_for_pickup(handoff, estimate)
        if malformed_block:
            raise ValueError(f"cannot approve malformed estimate: {malformed_block['reason']}")
    data = {
        "status": "APPROVED",
        "handoff_id": args.handoff_id,
        "handoff_checksum": handoff.get("checksum"),
        "estimate_checksum": handoff.get("estimate_checksum"),
        "approved_by": args.approver,
        "approved_at": utc_now(),
    }
    atomic_write_json(approval_path(args.root, args.handoff_id), data)
    emit(data)
    return 0


def malformed_estimate_block(reason: str) -> dict[str, Any]:
    return {
        "action": "malformed_estimate",
        "pickup_requires_approval": True,
        "reason": reason,
    }


def validate_estimate_for_pickup(handoff: dict[str, Any], estimate: Any) -> dict[str, Any] | None:
    if not isinstance(estimate, dict):
        return malformed_estimate_block("estimate must be an object")

    source = handoff.get("estimate_input")
    if not isinstance(source, dict):
        return malformed_estimate_block("estimate input must be an object")

    task_type = source.get("task_type")
    if task_type not in TASK_TYPES:
        return malformed_estimate_block("estimate input task_type is invalid")

    drivers = source.get("drivers")
    if not isinstance(drivers, list) or any(not isinstance(driver, str) for driver in drivers):
        return malformed_estimate_block("estimate input drivers must be a list of strings")

    bucket = estimate.get("bucket")
    if bucket not in BUCKETS:
        return malformed_estimate_block("estimate bucket is invalid")

    uncertainty = estimate.get("uncertainty")
    if not isinstance(uncertainty, dict):
        return malformed_estimate_block("estimate uncertainty must be an object")
    if uncertainty.get("level") not in {"low", "medium", "high"}:
        return malformed_estimate_block("estimate uncertainty level is invalid")

    policy = estimate.get("policy")
    if not isinstance(policy, dict):
        return malformed_estimate_block("estimate policy must be an object")
    expected_policy = policy_for_bucket(bucket)
    if policy.get("pickup_requires_approval") != expected_policy["pickup_requires_approval"]:
        return malformed_estimate_block("estimate policy approval does not match bucket")
    if policy.get("action") != expected_policy["action"]:
        return malformed_estimate_block("estimate policy action does not match bucket")

    title = handoff.get("title")
    message = handoff.get("message")
    if not isinstance(title, str) or not isinstance(message, str):
        return malformed_estimate_block("handoff title and message must be strings")
    if handoff.get("checksum") != checksum_message(message):
        return malformed_estimate_block("handoff message checksum mismatch")
    try:
        canonical = estimate_task(title=title, message=message, task_type=task_type, drivers=drivers)
    except ValueError as exc:
        return malformed_estimate_block(f"estimate cannot be recomputed: {exc}")

    for field in ("task_type", "bucket", "confidence", "score", "expected_minutes", "drivers", "uncertainty", "policy"):
        if estimate.get(field) != canonical[field]:
            return malformed_estimate_block(f"estimate {field} does not match canonical estimate")
    expected_checksum = checksum_estimate_binding(title, message, source, estimate)
    if handoff.get("estimate_checksum") != expected_checksum:
        return malformed_estimate_block("estimate checksum mismatch")

    return None


def self_evolution_policy_block(estimate: dict[str, Any]) -> dict[str, Any] | None:
    drivers = estimate.get("drivers", [])
    if "self_evolution" not in drivers:
        return None
    permissions = governance_permissions()
    if estimate.get("task_type") == "execution" or not permissions["may_propose_protocol_changes"]:
        return {
            "action": "self_evolution_propose_only",
            "pickup_requires_approval": True,
            "reason": "self-evolution may propose protocol changes but cannot execute governance mutations",
        }
    return None


def pickup_policy_block(root: Path, handoff: dict[str, Any]) -> dict[str, Any] | None:
    if "estimate" not in handoff or handoff["estimate"] is None:
        return malformed_estimate_block("handoff estimate is required for pickup")
    estimate = handoff["estimate"]
    malformed_block = validate_estimate_for_pickup(handoff, estimate)
    if malformed_block:
        return malformed_block
    self_evolution_block = self_evolution_policy_block(estimate)
    if self_evolution_block:
        return self_evolution_block
    policy = estimate.get("policy", {})
    if policy.get("pickup_requires_approval") and not pickup_is_approved(root, handoff):
        return policy
    if (
        estimate.get("task_type") == "discovery"
        and estimate.get("uncertainty", {}).get("level") == "high"
        and not pickup_is_approved(root, handoff)
    ):
        return {"action": "discovery_requires_approval", "pickup_requires_approval": True}
    return None


def set_brake(args: argparse.Namespace) -> int:
    root = args.root
    ensure_layout(root)
    if args.status == "DRIVE" and not getattr(args, "allow_drive", False):
        raise ValueError("use clear-brake to return the emergency brake to DRIVE")
    with exclusive_lock(lock_path(root, "runtime")):
        data = {
            "status": args.status,
            "reason": args.reason,
            "scope": args.scope,
            "resume_requires": args.resume_requires,
            "updated_at": utc_now(),
        }
        atomic_write_json(brake_path(root), data)
    emit(data)
    return 0


def clear_brake(args: argparse.Namespace) -> int:
    args.status = "DRIVE"
    args.allow_drive = True
    args.reason = args.reason or "operator cleared brake"
    args.scope = args.scope or "global"
    args.resume_requires = None
    return set_brake(args)


def claim_handoff(args: argparse.Namespace) -> int:
    root = args.root
    with exclusive_lock(lock_path(root, "runtime")):
        brake = read_brake(root)
        if brake["status"] != "DRIVE":
            emit(
                {
                    "claimed": False,
                    "blocked_by_brake": brake,
                    "handoff_id": args.handoff_id,
                }
            )
            return 2
        state, _path, ready_handoff = find_handoff(root, args.handoff_id)
        if state != "ready":
            raise RuntimeError(f"handoff {args.handoff_id} is {state}, expected ready")
        blocked_policy = pickup_policy_block(root, ready_handoff)
        if blocked_policy:
            emit({"claimed": False, "handoff_id": args.handoff_id, "blocked_by_policy": blocked_policy})
            return 3
        data = move_handoff(
            root,
            args.handoff_id,
            "ready",
            "claimed",
            {
                "claimed_at": utc_now(),
                "claimed_by": args.claimer,
            },
        )
    emit(data)
    return 0


def complete_handoff(args: argparse.Namespace) -> int:
    data = move_handoff(
        args.root,
        args.handoff_id,
        "claimed",
        "completed",
        {
            "completed_at": utc_now(),
            "summary": args.summary,
        },
    )
    emit(data)
    return 0


def fail_handoff(args: argparse.Namespace) -> int:
    current_state, _source, _data = find_handoff(args.root, args.handoff_id)
    if current_state not in {"ready", "claimed"}:
        raise RuntimeError(f"handoff {args.handoff_id} is {current_state}; only ready or claimed handoffs can fail")
    data = move_handoff(
        args.root,
        args.handoff_id,
        current_state,
        "failed",
        {
            "failed_at": utc_now(),
            "failure_reason": args.reason,
        },
    )
    emit(data)
    return 0


def ready_handoffs(root: Path) -> list[dict[str, Any]]:
    ensure_layout(root)
    items = []
    for path in sorted(handoff_state_dir(root, "ready").glob("*.json")):
        items.append(read_json(path))
    return sorted(items, key=lambda item: (item.get("created_at", ""), item.get("id", "")))


def next_handoff(args: argparse.Namespace) -> int:
    brake = read_brake(args.root)
    items = ready_handoffs(args.root)
    payload: dict[str, Any] = {
        "brake": brake,
        "blocked": brake["status"] != "DRIVE",
        "ready_count": len(items),
    }
    if args.all:
        payload["handoffs"] = items
    else:
        payload["handoff"] = items[0] if items else None
    emit(payload)
    return 0


def status(args: argparse.Namespace) -> int:
    root = args.root
    brake = read_brake(root)
    counts = {
        state: len(list(handoff_state_dir(root, state).glob("*.json")))
        for state in HANDOFF_STATES
    }
    next_ready = ready_handoffs(root)
    emit(
        {
            "root": str(root),
            "brake": brake,
            "cadence": cadence_state(brake),
            "counts": counts,
            "next_ready": next_ready[0] if next_ready else None,
        }
    )
    return 0


def validate_handoff(args: argparse.Namespace) -> int:
    errors: list[str] = []
    if Path(args.target).exists():
        data = read_json(Path(args.target))
    else:
        _, _, data = find_handoff(args.root, args.target)

    required = ("protocol_version", "id", "status", "checksum", "signature", "message")
    for key in required:
        if key not in data:
            errors.append(f"missing {key}")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"unsupported protocol_version: {data.get('protocol_version')}")
    if "message" in data and "checksum" in data:
        actual = checksum_message(data["message"])
        if actual != data["checksum"]:
            errors.append("checksum mismatch")
    if "id" in data and "checksum" in data and "signature" in data:
        expected = create_signature(data["id"], data["checksum"], data.get("status", "READY"))
        ready_expected = create_signature(data["id"], data["checksum"], "READY")
        if data["signature"] not in {expected, ready_expected}:
            errors.append("signature mismatch")

    payload = {
        "valid": not errors,
        "errors": errors,
        "id": data.get("id"),
    }
    emit(payload)
    return 0 if not errors else 1


def clean_square(args: argparse.Namespace) -> int:
    _, _, handoff = find_handoff(args.root, args.handoff_id)
    now = utc_now()
    data = {
        "handoff_id": args.handoff_id,
        "handoff_status": handoff.get("status"),
        "summary": args.summary,
        "created_at": now,
        "checks": {
            "handoff_written": True,
            "signature_present": bool(handoff.get("signature")),
            "next_session_can_resume": handoff.get("status") in {"READY", "CLAIMED", "COMPLETED"},
        },
    }
    target = args.root / "logs" / "clean-square" / f"{args.handoff_id}.json"
    atomic_write_json(target, data)
    emit(data)
    return 0


def verify_resume_command(args: argparse.Namespace) -> int:
    packet = verify_resume(
        root=args.root,
        cwd=Path(args.cwd),
        handoff_id=args.handoff_id,
        claimer=args.claimer,
    )
    emit(packet)
    return 0 if packet["resumable"] else 2


def resume_continuation_command(args: argparse.Namespace) -> int:
    packet = resume_continuation(
        root=args.root,
        cwd=Path(args.cwd),
        resume_verification_file=Path(args.resume_verification_file),
        claimer=args.claimer,
        max_resume_age_minutes=args.max_resume_age_minutes,
        ownership_target=args.ownership_target,
        ownership_role=args.ownership_role,
        ownership_task_id=args.ownership_task_id,
        max_ownership_age_minutes=args.max_ownership_age_minutes,
    )
    emit(packet)
    return 0 if packet["valid"] else 2


def work_ownership_status_command(args: argparse.Namespace) -> int:
    packet = work_ownership_status(
        root=args.root,
        cwd=Path(args.cwd),
        repo=args.repo,
        branch=args.branch,
        task_id=args.task_id,
        max_age_minutes=args.max_age_minutes,
    )
    emit(packet)
    return 0 if packet["valid"] else 2


def validate_work_ownership_command(args: argparse.Namespace) -> int:
    packet = validate_work_ownership(
        root=args.root,
        target=args.target,
        cwd=Path(args.cwd),
        repo=args.repo,
        branch=args.branch,
        task_id=args.task_id,
        require_active=args.require_active,
        max_age_minutes=args.max_age_minutes,
    )
    emit(packet)
    return 0 if packet["valid"] else 2


def ownership_mutation_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def read_work_ownership_rollback_record(root: Path, target: str) -> dict[str, Any] | None:
    path, state, blockers = find_work_ownership_record(root, target)
    if blockers or path is None or state != "active":
        return None
    try:
        record = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def rollback_work_ownership_mutation(packet: dict[str, Any], rollback_record: dict[str, Any] | None = None) -> None:
    side_effects = packet.setdefault("side_effects", [])
    packet_type = packet.get("packet")
    record = packet.get("record") if isinstance(packet.get("record"), dict) else {}
    record_path = record.get("path")
    if packet_type == "work_ownership_claim" and isinstance(record_path, str):
        path = Path(record_path)
        try:
            if path.exists():
                path.unlink()
            packet["ownership_written"] = False
            side_effects.append("work_ownership_active_rollback")
        except OSError as exc:
            packet.setdefault("blockers", []).append(
                ownership_mutation_blocker(
                    "ownership_rollback_failed",
                    f"active work ownership record could not be removed after audit failure: {exc}",
                    path=str(path),
                )
            )
        return

    if packet_type == "work_ownership_closeout" and isinstance(record_path, str):
        destination = Path(record_path)
        source_value = packet.get("source_record")
        source = Path(source_value) if isinstance(source_value, str) else None
        try:
            if destination.exists() and source is not None:
                if rollback_record is not None:
                    active_record = dict(rollback_record)
                else:
                    terminal_record = read_json(destination)
                    if not isinstance(terminal_record, dict):
                        raise ValueError("terminal work ownership record is not an object")
                    active_record = dict(terminal_record)
                    active_record["status"] = "ACTIVE"
                    active_record.pop("closeout", None)
                    active_record.pop("closed_at", None)
                    active_record.pop("failed_at", None)
                atomic_write_json(source, active_record)
                destination.unlink()
            packet["ownership_moved"] = False
            side_effects.append("work_ownership_closeout_rollback")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            packet.setdefault("blockers", []).append(
                ownership_mutation_blocker(
                    "ownership_rollback_failed",
                    f"work ownership closeout could not be restored after audit failure: {exc}",
                    source=str(source) if source else None,
                    destination=str(destination),
                )
            )


def append_work_ownership_mutation_audit(
    root: Path,
    packet: dict[str, Any],
    rollback_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not packet.get("valid"):
        return packet
    packet.setdefault("side_effects", []).append("work_ownership_audit_appended")
    try:
        packet["audit_record"] = append_audit_record(root, work_ownership_mutation_audit_record(packet))
    except (OSError, RuntimeError, ValueError) as exc:
        packet["side_effects"] = [
            effect for effect in packet.get("side_effects", []) if effect != "work_ownership_audit_appended"
        ]
        rollback_work_ownership_mutation(packet, rollback_record=rollback_record)
        packet.setdefault("blockers", []).append(
            {
                "code": "audit_append_failed",
                "message": f"work ownership audit record could not be written: {exc}",
            }
        )
        packet["valid"] = False
        packet["recommended_next_action"] = "inspect_runtime_state"
        packet["reason"] = packet["blockers"][-1]["message"]
    return packet


def _ownership_closeout_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _closeout_context_path(context_file: Path, value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context_file.parent / path
    return path


def _closeout_bound_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _closeout_bound_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_closeout_bound_non_empty_string(item) for item in value)


def _closeout_bound_checksum(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _closeout_context_paths_match(context_file: Path, expected: Any, actual: Any) -> bool:
    if not _closeout_bound_non_empty_string(expected) or not _closeout_bound_non_empty_string(actual):
        return False
    return _closeout_context_path(context_file, expected).expanduser().resolve() == _closeout_context_path(
        context_file,
        actual,
    ).expanduser().resolve()


def _validate_closeout_bound_packet_shape(closeout: dict[str, Any], closeout_file: Path) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    def add(message: str, *, field: str | None = None, **extra: Any) -> None:
        if field is not None:
            extra["field"] = field
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_invalid",
                message,
                path=str(closeout_file),
                **extra,
            )
        )

    if closeout.get("protocol_version") != PROTOCOL_VERSION:
        add(
            "executor closeout evidence protocol_version is invalid",
            field="protocol_version",
            expected_protocol=PROTOCOL_VERSION,
            actual_protocol=closeout.get("protocol_version"),
        )
    for field in (
        "reason",
        "epoch_id",
        "epoch_status",
        "closeout_status",
        "task_file",
        "result_file",
        "snapshot_after_file",
        "executor_result_status",
    ):
        if not _closeout_bound_non_empty_string(closeout.get(field)):
            add(f"executor closeout evidence {field} is required", field=field)
    for field in ("task_checksum", "snapshot_after_checksum"):
        if not _closeout_bound_checksum(closeout.get(field)):
            add(f"executor closeout evidence {field} must be a sha256 checksum", field=field)
    for field in ("valid", "executor_started", "pr_action_started", "operator_confirmation_required"):
        if not isinstance(closeout.get(field), bool):
            add(f"executor closeout evidence {field} must be a boolean", field=field)
    if closeout.get("pr_action_started") is not False:
        add("executor closeout evidence must not have started PR actions", field="pr_action_started")

    blockers_value = closeout.get("blockers")
    if not isinstance(blockers_value, list):
        add("executor closeout evidence blockers must be a list", field="blockers")
    else:
        for index, blocker in enumerate(blockers_value):
            if (
                not isinstance(blocker, dict)
                or not _closeout_bound_non_empty_string(blocker.get("code"))
                or not _closeout_bound_non_empty_string(blocker.get("message"))
            ):
                add(
                    "executor closeout evidence blockers must contain code and message strings",
                    field=f"blockers[{index}]",
                )

    validation = closeout.get("validation")
    if not isinstance(validation, dict):
        add("executor closeout evidence validation must be a JSON object", field="validation")
    else:
        if validation.get("protocol_version") != PROTOCOL_VERSION:
            add("executor closeout validation protocol_version is invalid", field="validation.protocol_version")
        if validation.get("packet") != "executor_result_validation":
            add("executor closeout validation packet is invalid", field="validation.packet")
        if not isinstance(validation.get("valid"), bool):
            add("executor closeout validation valid must be a boolean", field="validation.valid")
        if not isinstance(validation.get("executor_started"), bool):
            add("executor closeout validation executor_started must be a boolean", field="validation.executor_started")
        for field in ("reason", "task_file", "result_file", "recommended_next_action"):
            if not _closeout_bound_non_empty_string(validation.get(field)):
                add(f"executor closeout validation {field} is required", field=f"validation.{field}")
        if not _closeout_context_paths_match(closeout_file, closeout.get("task_file"), validation.get("task_file")):
            add("executor closeout validation task_file does not match closeout task_file", field="validation.task_file")
        if not _closeout_context_paths_match(closeout_file, closeout.get("result_file"), validation.get("result_file")):
            add(
                "executor closeout validation result_file does not match closeout result_file",
                field="validation.result_file",
            )

    next_decision = closeout.get("next_decision")
    if not isinstance(next_decision, dict):
        add("executor closeout evidence next_decision must be a JSON object", field="next_decision")
    else:
        for field in ("decision", "recommended_next_action", "reason"):
            if not _closeout_bound_non_empty_string(next_decision.get(field)):
                add(f"executor closeout next_decision {field} is required", field=f"next_decision.{field}")

    git_pr_plan = closeout.get("git_pr_plan")
    if "git_pr_plan" not in closeout or (git_pr_plan is not None and not isinstance(git_pr_plan, dict)):
        add("executor closeout evidence git_pr_plan must be null or a JSON object", field="git_pr_plan")
    if not _closeout_bound_string_list(closeout.get("side_effects")):
        add("executor closeout evidence side_effects must be a list of strings", field="side_effects")
    if not _closeout_bound_string_list(closeout.get("limitations")):
        add("executor closeout evidence limitations must be a list of strings", field="limitations")
    else:
        expected_limitations = {
            "local_packets_only",
            "does_not_start_executor",
            "does_not_execute_git_commands",
            "does_not_call_github",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
        }
        missing_limitations = sorted(expected_limitations.difference(closeout["limitations"]))
        if missing_limitations:
            add(
                "executor closeout evidence limitations are incomplete",
                field="limitations",
                missing_limitations=missing_limitations,
            )

    audit_record = closeout.get("audit_record")
    if not isinstance(audit_record, dict):
        add("completed executor closeout evidence must include an audit_record reference", field="audit_record")
    else:
        if audit_record.get("event") != "executor_epoch_closeout":
            add("executor closeout audit_record event is invalid", field="audit_record.event")
        for field in ("path", "recorded_at", "audit_chain_version"):
            if not _closeout_bound_non_empty_string(audit_record.get(field)):
                add(f"executor closeout audit_record {field} is required", field=f"audit_record.{field}")
        if (
            isinstance(audit_record.get("chain_index"), bool)
            or not isinstance(audit_record.get("chain_index"), int)
            or audit_record.get("chain_index") < 1
        ):
            add(
                "executor closeout audit_record chain_index must be a positive integer",
                field="audit_record.chain_index",
            )
        for field in ("previous_event_hash", "event_hash"):
            if not _closeout_bound_checksum(audit_record.get(field)):
                add(f"executor closeout audit_record {field} must be a sha256 checksum", field=f"audit_record.{field}")

    if closeout.get("valid") is True and closeout.get("closeout_status") == "completed":
        if closeout.get("epoch_status") != "COMPLETED":
            add("completed executor closeout evidence must report COMPLETED epoch_status", field="epoch_status")
        if closeout.get("executor_result_status") != "succeeded":
            add("completed executor closeout evidence must report succeeded executor_result_status", field="executor_result_status")
        if blockers_value != []:
            add("completed executor closeout evidence must not contain blockers", field="blockers")
        if isinstance(validation, dict) and validation.get("valid") is not True:
            add("completed executor closeout evidence must include valid result validation", field="validation.valid")
        if isinstance(next_decision, dict) and next_decision.get("decision") != "generate_git_pr_plan":
            add("completed executor closeout evidence next_decision is invalid", field="next_decision.decision")
        side_effects = closeout.get("side_effects")
        if isinstance(side_effects, list):
            for expected_effect in ("epoch_completed", "audit_record_appended"):
                if expected_effect not in side_effects:
                    add(
                        "completed executor closeout evidence side_effects are incomplete",
                        field="side_effects",
                        missing_side_effect=expected_effect,
                    )
    return blockers


def _closeout_nested_blocker(message: str, *, path: str | None = None, nested_blocker: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    blocker = _ownership_closeout_blocker(
        "ownership_closeout_invalid",
        message,
        path=path,
        **extra,
    )
    if nested_blocker is not None:
        blocker["nested_blocker"] = nested_blocker
    return blocker


def _validate_closeout_bound_execution_reference(
    root: Path,
    closeout: dict[str, Any],
    closeout_file: Path,
    *,
    task_packet: dict[str, Any],
    result_evidence: dict[str, Any],
    validation: dict[str, Any],
    task_file: Path,
    result_file: Path,
    epoch_id: str | None,
    closeout_status: str | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    run_record_ref = closeout.get("run_record")
    real_invocation_ref = closeout.get("real_invocation")
    execution_refs = [
        ref
        for ref in (run_record_ref, real_invocation_ref)
        if isinstance(ref, dict) and _closeout_bound_non_empty_string(ref.get("path"))
    ]
    if len(execution_refs) != 1:
        return [
            _closeout_nested_blocker(
                "completed executor closeout evidence must reference exactly one execution record",
                path=str(closeout_file),
                execution_reference_count=len(execution_refs),
            )
        ]

    if isinstance(run_record_ref, dict) and _closeout_bound_non_empty_string(run_record_ref.get("path")):
        run_record_path = _closeout_context_path(closeout_file, run_record_ref["path"])
        run_record, run_blockers = load_closeout_run_record(root, run_record_path)
        blockers.extend(
            _closeout_nested_blocker(
                "executor closeout run record is invalid",
                path=str(run_record_path),
                nested_blocker=blocker,
            )
            for blocker in run_blockers
        )
        if isinstance(run_record, dict):
            blockers.extend(
                _closeout_nested_blocker(
                    "executor closeout run record is invalid",
                    path=str(run_record_path),
                    nested_blocker=blocker,
                )
                for blocker in validate_execution_run_record(
                    run_record,
                    task_packet=task_packet,
                    result_evidence=result_evidence,
                    validation_packet=validation,
                    task_file=task_file,
                    result_file=result_file,
                    expected_closeout_status=closeout_status or "",
                    epoch_id=epoch_id,
                )
            )
            for field in ("run_id", "invocation_id", "closeout_status", "epoch_closeout_checksum"):
                if run_record_ref.get(field) != run_record.get(field):
                    blockers.append(
                        _closeout_nested_blocker(
                            f"executor closeout run_record.{field} does not match bound run record",
                            path=str(run_record_path),
                            expected=run_record_ref.get(field),
                            actual=run_record.get(field),
                            field=f"run_record.{field}",
                        )
                    )
            if run_record_ref.get("after_checksum") != checksum_json(run_record):
                blockers.append(
                    _closeout_nested_blocker(
                        "executor closeout run_record after_checksum does not match bound run record",
                        path=str(run_record_path),
                        expected_checksum=run_record_ref.get("after_checksum"),
                        actual_checksum=checksum_json(run_record),
                        field="run_record.after_checksum",
                    )
                )
            if run_record.get("epoch_id") != epoch_id or run_record.get("epoch_status") != closeout.get("epoch_status"):
                blockers.append(
                    _closeout_nested_blocker(
                        "executor closeout run record epoch anchors do not match closeout evidence",
                        path=str(run_record_path),
                        expected_epoch_id=epoch_id,
                        actual_epoch_id=run_record.get("epoch_id"),
                        expected_epoch_status=closeout.get("epoch_status"),
                        actual_epoch_status=run_record.get("epoch_status"),
                    )
                )
        return blockers

    if isinstance(real_invocation_ref, dict) and _closeout_bound_non_empty_string(real_invocation_ref.get("path")):
        invocation_path = _closeout_context_path(closeout_file, real_invocation_ref["path"])
        invocation, invocation_blockers = load_closeout_real_invocation(root, invocation_path)
        blockers.extend(
            _closeout_nested_blocker(
                "executor closeout real invocation record is invalid",
                path=str(invocation_path),
                nested_blocker=blocker,
            )
            for blocker in invocation_blockers
        )
        if isinstance(invocation, dict):
            required_fields = {
                "protocol_version": PROTOCOL_VERSION,
                "schema_version": REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION,
                "packet": "real_executor_invocation",
                "invocation_id": real_invocation_ref.get("invocation_id"),
                "closeout_status": closeout_status,
                "epoch_id": epoch_id,
                "epoch_status": closeout.get("epoch_status"),
                "task_file": str(task_file),
                "result_evidence_checksum": checksum_json(result_evidence),
                "validation_packet_checksum": checksum_json(validation),
                "snapshot_after_checksum": closeout.get("snapshot_after_checksum"),
                "epoch_closeout_checksum": real_invocation_ref.get("epoch_closeout_checksum"),
            }
            for field, expected in required_fields.items():
                if invocation.get(field) != expected:
                    blockers.append(
                        _closeout_nested_blocker(
                            f"executor closeout real invocation {field} does not match closeout evidence",
                            path=str(invocation_path),
                            expected=expected,
                            actual=invocation.get(field),
                            field=f"real_invocation.{field}",
                        )
                    )
            if real_invocation_ref.get("after_checksum") != checksum_json(invocation):
                blockers.append(
                    _closeout_nested_blocker(
                        "executor closeout real_invocation after_checksum does not match bound invocation record",
                        path=str(invocation_path),
                        expected_checksum=real_invocation_ref.get("after_checksum"),
                        actual_checksum=checksum_json(invocation),
                        field="real_invocation.after_checksum",
                    )
                )
        return blockers

    return blockers


def _validate_closeout_bound_audit_reference(
    root: Path,
    closeout: dict[str, Any],
    closeout_file: Path,
    *,
    task_packet: dict[str, Any],
    result_evidence: dict[str, Any],
    snapshot_after: dict[str, Any],
) -> list[dict[str, Any]]:
    audit_ref = closeout.get("audit_record")
    if not isinstance(audit_ref, dict):
        return []
    audit_path_value = audit_ref.get("path")
    if not _closeout_bound_non_empty_string(audit_path_value):
        return []
    audit_path = _closeout_context_path(closeout_file, audit_path_value)
    expected_audit_path = audit_events_path(root)
    blockers: list[dict[str, Any]] = []
    if not _closeout_paths_match(audit_path, expected_audit_path):
        blockers.append(
            _closeout_nested_blocker(
                "executor closeout audit_record path does not match runtime audit log",
                path=str(audit_path),
                expected_path=str(expected_audit_path),
                actual_path=str(audit_path),
            )
        )
    chain_index = audit_ref.get("chain_index")
    if isinstance(chain_index, bool) or not isinstance(chain_index, int) or chain_index < 1:
        blockers.append(
            _closeout_nested_blocker(
                "executor closeout audit_record chain_index must be a positive integer",
                path=str(audit_path),
                field="audit_record.chain_index",
                actual_chain_index=chain_index,
            )
        )
        return blockers
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        raw_line = lines[chain_index - 1]
        record = json.loads(raw_line)
    except (IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(
            _closeout_nested_blocker(
                "executor closeout audit_record line could not be read",
                path=str(audit_path),
                line=chain_index,
                error=str(exc),
            )
        )
        return blockers
    if not isinstance(record, dict):
        blockers.append(
            _closeout_nested_blocker(
                "executor closeout audit_record line must be a JSON object",
                path=str(audit_path),
                line=chain_index,
            )
        )
        return blockers

    _event, audit_blockers = validate_audit_record(record, chain_index)
    blockers.extend(
        _closeout_nested_blocker(
            "executor closeout audit_record line is invalid",
            path=str(audit_path),
            nested_blocker=blocker,
            line=chain_index,
        )
        for blocker in audit_blockers
    )
    for field in ("event", "recorded_at", "audit_chain_version", "chain_index", "previous_event_hash", "event_hash"):
        if record.get(field) != audit_ref.get(field):
            blockers.append(
                _closeout_nested_blocker(
                    f"executor closeout audit_record {field} does not match referenced audit line",
                    path=str(audit_path),
                    line=chain_index,
                    expected=audit_ref.get(field),
                    actual=record.get(field),
                    field=f"audit_record.{field}",
                )
            )
    computed_event_hash = audit_event_hash(record)
    if record.get("event_hash") != computed_event_hash:
        blockers.append(
            _closeout_nested_blocker(
                "executor closeout audit_record event_hash does not match audit line payload",
                path=str(audit_path),
                line=chain_index,
                expected_checksum=record.get("event_hash"),
                actual_checksum=computed_event_hash,
            )
        )
    closeout_payload = dict(closeout)
    closeout_payload.pop("audit_record", None)
    expected_checksums = {
        "payload_checksum": checksum_json(closeout_payload),
        "task_packet_checksum": checksum_json(task_packet),
        "result_evidence_checksum": checksum_json(result_evidence),
        "snapshot_after_checksum": checksum_epoch_json(snapshot_after),
    }
    for field, expected in expected_checksums.items():
        if record.get(field) != expected:
            blockers.append(
                _closeout_nested_blocker(
                    f"executor closeout audit_record {field} does not match closeout evidence",
                    path=str(audit_path),
                    line=chain_index,
                    expected_checksum=expected,
                    actual_checksum=record.get(field),
                    field=f"audit_record.{field}",
                )
            )
    return blockers


def _load_closeout_bound_ownership_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closeout_file = Path(args.closeout_file)
    blockers: list[dict[str, Any]] = []
    closeout: Any = None
    task_packet: Any = None
    task_file: Path | None = None
    result_evidence: Any = None
    result_file: Path | None = None
    snapshot_after: Any = None
    snapshot_after_file: Path | None = None
    closeout_checksum = None
    try:
        closeout = read_json(closeout_file)
        closeout_checksum = checksum_json(closeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_unreadable",
                f"executor closeout evidence could not be read: {exc}",
                path=str(closeout_file),
            )
        )
    if closeout_checksum is not None and args.closeout_checksum != closeout_checksum:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_checksum_mismatch",
                "executor closeout checksum does not match supplied closeout evidence",
                expected_checksum=args.closeout_checksum,
                actual_checksum=closeout_checksum,
                path=str(closeout_file),
            )
        )
    if not isinstance(closeout, dict):
        if closeout is not None:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_invalid",
                    "executor closeout evidence must be a JSON object",
                    path=str(closeout_file),
                )
            )
        return (
            {
                "repo": "",
                "branch": "",
                "head": "",
                "task_id": "",
                "epoch_id": None,
                "executor_closeout_status": None,
                "executor_closeout_file": str(closeout_file),
                "executor_closeout_checksum": args.closeout_checksum,
                "summary": args.summary,
            },
            blockers,
        )

    if closeout.get("schema_version") != EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_schema_unsupported",
                "executor closeout evidence schema is unsupported",
                expected_schema=EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION,
                actual_schema=closeout.get("schema_version"),
                path=str(closeout_file),
            )
        )
    if closeout.get("packet") != "executor_epoch_closeout":
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_packet_invalid",
                "executor closeout evidence packet is invalid",
                expected_packet="executor_epoch_closeout",
                actual_packet=closeout.get("packet"),
                path=str(closeout_file),
            )
        )
    if closeout.get("valid") is not True:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_invalid",
                "executor closeout evidence must be a valid accepted packet",
                path=str(closeout_file),
            )
        )
    blockers.extend(_validate_closeout_bound_packet_shape(closeout, closeout_file))
    epoch_id = closeout.get("epoch_id") if isinstance(closeout.get("epoch_id"), str) else None
    if epoch_id is None:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_epoch_missing",
                "executor closeout evidence must include epoch_id",
                path=str(closeout_file),
            )
        )
    executor_closeout_status = closeout.get("closeout_status")
    if executor_closeout_status != "completed":
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_not_completed",
                "work ownership completion requires completed executor closeout evidence",
                expected_closeout_status="completed",
                actual_closeout_status=executor_closeout_status,
                path=str(closeout_file),
            )
        )

    task_file_value = closeout.get("task_file")
    if isinstance(task_file_value, str) and task_file_value.strip():
        task_file = _closeout_context_path(closeout_file, task_file_value)
        try:
            task_packet = read_json(task_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_task_unreadable",
                    f"executor closeout task file could not be read: {exc}",
                    path=str(task_file),
                )
            )
    else:
        blockers.append(
            _ownership_closeout_blocker(
                "ownership_closeout_task_missing",
                "executor closeout evidence must include task_file",
                path=str(closeout_file),
            )
        )

    result_file_value = closeout.get("result_file")
    if isinstance(result_file_value, str) and result_file_value.strip():
        result_file = _closeout_context_path(closeout_file, result_file_value)
        try:
            result_evidence = read_json(result_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_invalid",
                    f"executor closeout result file could not be read: {exc}",
                    path=str(result_file),
                )
            )

    snapshot_after_file_value = closeout.get("snapshot_after_file")
    if isinstance(snapshot_after_file_value, str) and snapshot_after_file_value.strip():
        snapshot_after_file = _closeout_context_path(closeout_file, snapshot_after_file_value)
        try:
            snapshot_after = read_json(snapshot_after_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_invalid",
                    f"executor closeout snapshot-after file could not be read: {exc}",
                    path=str(snapshot_after_file),
                )
            )

    expected_executor_started = False
    validation_invocation_id = None
    validation_packet_checksum = None
    allow_succeeded_dirty_worktree = False
    run_record_ref = closeout.get("run_record")
    real_invocation_ref = closeout.get("real_invocation")
    if isinstance(run_record_ref, dict) and _closeout_bound_non_empty_string(run_record_ref.get("path")):
        run_record_path = _closeout_context_path(closeout_file, run_record_ref["path"])
        try:
            run_record = read_json(run_record_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_invalid",
                    f"executor closeout run record could not be read: {exc}",
                    path=str(run_record_path),
                )
            )
        else:
            if isinstance(run_record, dict):
                expected_executor_started = run_record.get("executor_started") is True
                if _closeout_bound_non_empty_string(run_record.get("invocation_id")):
                    validation_invocation_id = run_record["invocation_id"]
                if _closeout_bound_checksum(run_record.get("validation_packet_checksum")):
                    validation_packet_checksum = run_record["validation_packet_checksum"]
            else:
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_closeout_invalid",
                        "executor closeout run record must be a JSON object",
                        path=str(run_record_path),
                    )
                )
    if (
        validation_invocation_id is None
        and isinstance(real_invocation_ref, dict)
        and _closeout_bound_non_empty_string(real_invocation_ref.get("path"))
    ):
        real_invocation_path = _closeout_context_path(closeout_file, real_invocation_ref["path"])
        try:
            real_invocation = read_json(real_invocation_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_invalid",
                    f"executor closeout real invocation record could not be read: {exc}",
                    path=str(real_invocation_path),
                )
            )
        else:
            if isinstance(real_invocation, dict):
                expected_executor_started = real_invocation.get("executor_started") is True
                allow_succeeded_dirty_worktree = real_invocation.get("side_effect_mode") == "materialized_changes"
                if _closeout_bound_non_empty_string(real_invocation.get("invocation_id")):
                    validation_invocation_id = real_invocation["invocation_id"]
                if _closeout_bound_checksum(real_invocation.get("validation_packet_checksum")):
                    validation_packet_checksum = real_invocation["validation_packet_checksum"]
            else:
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_closeout_invalid",
                        "executor closeout real invocation record must be a JSON object",
                        path=str(real_invocation_path),
                    )
                )

    task_id = ""
    repo_name = ""
    branch = ""
    head = ""
    if task_packet is not None:
        task_valid, task_reason = validate_executor_task_packet(task_packet)
        if not task_valid:
            blockers.append(
                _ownership_closeout_blocker(
                    "ownership_closeout_task_invalid",
                    task_reason,
                    path=str(task_file) if task_file is not None else None,
                )
            )
        elif isinstance(task_packet, dict):
            task_checksum = checksum_json(task_packet)
            closeout_task_checksum = closeout.get("task_checksum")
            if closeout_task_checksum != task_checksum:
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_closeout_task_checksum_mismatch",
                        "executor closeout task checksum does not match reread task file",
                        expected_checksum=closeout_task_checksum,
                        actual_checksum=task_checksum,
                        path=str(task_file) if task_file is not None else None,
                    )
                )
            task = task_packet["task"]
            repo = task_packet["repo"]
            task_id = task["id"]
            repo_name = repo["name"]
            branch = repo["branch"]
            head = repo["head"]
            if result_evidence is not None:
                result_valid, result_reason = validate_executor_result_evidence(
                    result_evidence,
                    task_packet,
                    allow_succeeded_dirty_worktree=allow_succeeded_dirty_worktree,
                )
                if not result_valid:
                    blockers.append(
                        _ownership_closeout_blocker(
                            "ownership_closeout_invalid",
                            f"executor closeout result evidence is invalid: {result_reason}",
                            path=str(result_file) if result_file is not None else None,
                        )
                    )
                elif closeout.get("executor_result_status") != result_evidence.get("status"):
                    blockers.append(
                        _ownership_closeout_blocker(
                            "ownership_closeout_invalid",
                            "executor closeout result status does not match result evidence",
                            expected_status=closeout.get("executor_result_status"),
                            actual_status=result_evidence.get("status"),
                            path=str(result_file) if result_file is not None else None,
                        )
                    )
                else:
                    expected_validation = {
                        "protocol_version": PROTOCOL_VERSION,
                        "packet": "executor_result_validation",
                        "valid": True,
                        "reason": result_reason,
                        "task_file": str(task_file) if task_file is not None else str(closeout.get("task_file")),
                        "result_file": str(result_file) if result_file is not None else str(closeout.get("result_file")),
                        "executor_started": expected_executor_started,
                        "recommended_next_action": "record_executor_result",
                    }
                    if validation_invocation_id is not None:
                        expected_validation["invocation_id"] = validation_invocation_id
                    saved_validation = closeout.get("validation")
                    if isinstance(saved_validation, dict) and saved_validation != expected_validation:
                        blockers.append(
                            _ownership_closeout_blocker(
                                "ownership_closeout_invalid",
                                "executor closeout validation does not match reread task and result evidence",
                                expected_checksum=checksum_json(expected_validation),
                                actual_checksum=checksum_json(saved_validation),
                                path=str(closeout_file),
                            )
                        )
                    if (
                        isinstance(saved_validation, dict)
                        and validation_packet_checksum is not None
                        and checksum_json(saved_validation) != validation_packet_checksum
                    ):
                        blockers.append(
                            _ownership_closeout_blocker(
                                "ownership_closeout_invalid",
                                "executor closeout validation checksum does not match bound run evidence",
                                expected_checksum=validation_packet_checksum,
                                actual_checksum=checksum_json(saved_validation),
                                path=str(closeout_file),
                            )
                        )
            validation = closeout.get("validation")
            if closeout.get("executor_started") is not expected_executor_started:
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_closeout_invalid",
                        "executor closeout executor_started does not match bound run evidence",
                        expected_executor_started=expected_executor_started,
                        actual_executor_started=closeout.get("executor_started"),
                        path=str(closeout_file),
                    )
                )
            if isinstance(validation, dict) and closeout.get("executor_started") != validation.get("executor_started"):
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_closeout_invalid",
                        "executor closeout executor_started does not match validation",
                        expected_executor_started=validation.get("executor_started"),
                        actual_executor_started=closeout.get("executor_started"),
                        path=str(closeout_file),
                    )
                )
            next_decision = closeout.get("next_decision")
            if isinstance(next_decision, dict):
                expected_confirmation = next_decision.get("decision") in {"generate_git_pr_plan", "handoff"}
                if closeout.get("operator_confirmation_required") is not expected_confirmation:
                    blockers.append(
                        _ownership_closeout_blocker(
                            "ownership_closeout_invalid",
                            "executor closeout operator_confirmation_required does not match next_decision",
                            expected_operator_confirmation_required=expected_confirmation,
                            actual_operator_confirmation_required=closeout.get("operator_confirmation_required"),
                            path=str(closeout_file),
                        )
                    )
            if snapshot_after is not None:
                snapshot_valid, snapshot_reason = validate_repo_snapshot(
                    snapshot_after,
                    expected_repo=repo_name,
                    expected_branch=branch,
                )
                if not snapshot_valid:
                    blockers.append(
                        _ownership_closeout_blocker(
                            "ownership_closeout_invalid",
                            f"executor closeout snapshot-after evidence is invalid: {snapshot_reason}",
                            path=str(snapshot_after_file) if snapshot_after_file is not None else None,
                        )
                    )
                elif closeout.get("snapshot_after_checksum") != checksum_epoch_json(snapshot_after):
                    blockers.append(
                        _ownership_closeout_blocker(
                            "ownership_closeout_invalid",
                            "executor closeout snapshot checksum does not match snapshot-after evidence",
                            expected_checksum=closeout.get("snapshot_after_checksum"),
                            actual_checksum=checksum_epoch_json(snapshot_after),
                            path=str(snapshot_after_file) if snapshot_after_file is not None else None,
                        )
                    )
            saved_validation = closeout.get("validation")
            if (
                isinstance(result_evidence, dict)
                and isinstance(snapshot_after, dict)
                and isinstance(saved_validation, dict)
                and task_file is not None
                and result_file is not None
            ):
                blockers.extend(
                    _validate_closeout_bound_execution_reference(
                        args.root,
                        closeout,
                        closeout_file,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                        validation=saved_validation,
                        task_file=task_file,
                        result_file=result_file,
                        epoch_id=epoch_id,
                        closeout_status=executor_closeout_status if isinstance(executor_closeout_status, str) else None,
                    )
                )
                blockers.extend(
                    _validate_closeout_bound_audit_reference(
                        args.root,
                        closeout,
                        closeout_file,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                        snapshot_after=snapshot_after,
                    )
                )
            if args.candidate_id != task_id:
                blockers.append(
                    _ownership_closeout_blocker(
                        "ownership_candidate_mismatch",
                        "work ownership candidate_id must match executor task id",
                        expected_candidate_id=task_id,
                        actual_candidate_id=args.candidate_id,
                        path=str(task_file) if task_file is not None else None,
                    )
                )

    summary = args.summary
    if summary is None and isinstance(closeout.get("reason"), str):
        summary = closeout["reason"]
    return (
        {
            "repo": repo_name,
            "branch": branch,
            "head": head,
            "task_id": task_id,
            "epoch_id": epoch_id,
            "executor_closeout_status": executor_closeout_status if isinstance(executor_closeout_status, str) else None,
            "executor_closeout_file": str(closeout_file),
            "executor_closeout_checksum": args.closeout_checksum,
            "summary": summary,
        },
        blockers,
    )


def complete_work_ownership_from_closeout_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "work-ownership")):
        rollback_record = read_work_ownership_rollback_record(args.root, args.target)
        closeout_inputs, closeout_blockers = _load_closeout_bound_ownership_inputs(args)
        packet = closeout_work_ownership(
            root=args.root,
            cwd=Path(args.cwd),
            target=args.target,
            closeout_status="CLOSED",
            repo=closeout_inputs["repo"],
            branch=closeout_inputs["branch"],
            head=closeout_inputs["head"],
            task_id=closeout_inputs["task_id"],
            claimer=args.claimer,
            summary=closeout_inputs["summary"],
            candidate_id=args.candidate_id,
            role=args.role,
            epoch_id=closeout_inputs["epoch_id"],
            executor_closeout_file=closeout_inputs["executor_closeout_file"],
            executor_closeout_checksum=closeout_inputs["executor_closeout_checksum"],
            executor_closeout_status=closeout_inputs["executor_closeout_status"],
            pre_blockers=closeout_blockers,
        )
        packet = append_work_ownership_mutation_audit(args.root, packet, rollback_record=rollback_record)
    emit(packet)
    return 0 if packet["valid"] else 2


def claim_work_ownership_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "work-ownership")):
        packet = claim_work_ownership(
            root=args.root,
            cwd=Path(args.cwd),
            repo=args.repo,
            branch=args.branch,
            head=args.head,
            task_id=args.task_id,
            candidate_id=args.candidate_id,
            role=args.role,
            claimer=args.claimer,
            ownership_id=args.id,
            pr_number=args.pr_number,
            epoch_id=args.epoch_id,
            handoff_id=args.handoff_id,
            max_age_minutes=args.max_age_minutes,
        )
        packet = append_work_ownership_mutation_audit(args.root, packet)
    emit(packet)
    return 0 if packet["valid"] else 2


def close_work_ownership_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "work-ownership")):
        rollback_record = read_work_ownership_rollback_record(args.root, args.target)
        packet = closeout_work_ownership(
            root=args.root,
            cwd=Path(args.cwd),
            target=args.target,
            closeout_status="CLOSED",
            repo=args.repo,
            branch=args.branch,
            head=args.head,
            task_id=args.task_id,
            claimer=args.claimer,
            summary=args.summary,
        )
        packet = append_work_ownership_mutation_audit(args.root, packet, rollback_record=rollback_record)
    emit(packet)
    return 0 if packet["valid"] else 2


def fail_work_ownership_command(args: argparse.Namespace) -> int:
    with exclusive_lock(lock_path(args.root, "work-ownership")):
        rollback_record = read_work_ownership_rollback_record(args.root, args.target)
        packet = closeout_work_ownership(
            root=args.root,
            cwd=Path(args.cwd),
            target=args.target,
            closeout_status="FAILED",
            repo=args.repo,
            branch=args.branch,
            head=args.head,
            task_id=args.task_id,
            claimer=args.claimer,
            summary=args.summary,
        )
        packet = append_work_ownership_mutation_audit(args.root, packet, rollback_record=rollback_record)
    emit(packet)
    return 0 if packet["valid"] else 2


def choose_interactive_intent() -> str:
    print("Choose discovery intent:", file=sys.stderr)
    for index, intent in enumerate(DISCOVERY_INTENTS, start=1):
        print(f"{index}. {intent}", file=sys.stderr)
    print("Intent: ", end="", file=sys.stderr)
    choice = input()
    try:
        selected = int(choice)
    except ValueError as exc:
        raise ValueError("intent selection must be a number") from exc
    if selected < 1 or selected > len(DISCOVERY_INTENTS):
        raise ValueError("intent selection is out of range")
    return DISCOVERY_INTENTS[selected - 1]


def discover_candidates_command(args: argparse.Namespace) -> int:
    intent = args.intent
    if args.discovery_mode == "expanded":
        raise ValueError("expanded discovery mode is reserved for v2")
    if args.interactive and not intent and args.discovery_mode != "off":
        intent = choose_interactive_intent()
    if args.discovery_mode != "off" and not intent:
        raise ValueError("intent is required unless --interactive or --discovery-mode off is used")
    budget = CandidateBudget(
        max_candidates=args.max_candidates,
        max_candidates_per_source=args.max_candidates_per_source,
        max_text_marker_candidates=args.max_text_marker_candidates,
        max_doc_marker_candidates=args.max_doc_marker_candidates,
        max_business_memory_candidates=args.max_business_memory_candidates,
        max_proposals=args.max_proposals,
        max_product_evolution_candidates_in_hybrid=args.max_product_evolution_candidates_in_hybrid,
    )
    payload = discover_candidates(
        cwd=Path(args.cwd),
        intent=intent,
        discovery_mode=args.discovery_mode,
        proposal_allowance=args.proposal_allowance,
        known_failures=args.known_failure or [],
        pr_json_file=Path(args.pr_json_file) if args.pr_json_file else None,
        review_findings_file=Path(args.review_findings_file) if args.review_findings_file else None,
        review_threads_file=Path(args.review_threads_file) if args.review_threads_file else None,
        elect=args.elect,
        max_tasks=args.max_tasks,
        budget=budget,
    )
    emit(payload)
    return 0


def loop_tick_recommendation(
    cadence: dict[str, Any],
    snapshot: dict[str, Any],
    elected_next: list[dict[str, Any]],
) -> tuple[str, str, bool, bool]:
    if not cadence["can_start_work"]:
        return "blocked", f"cadence state is {cadence['state']}", False, False
    if snapshot["repo_confidence"] == "low":
        return "approval_required", "repo confidence is low", True, False
    if not elected_next:
        return "no_candidates", "no elected candidate", False, False
    return "requires_executor_contract", "executor task packet has not been emitted", False, True


def loop_tick_command(args: argparse.Namespace) -> int:
    if args.discovery_mode == "expanded":
        raise ValueError("expanded discovery mode is reserved for v2")
    if args.discovery_mode != "off" and not args.intent:
        raise ValueError("intent is required unless --discovery-mode off is used")
    root = args.root
    policy = load_loop_policy(args.policy_file)
    brake = read_brake(root)
    cadence = cadence_state(brake)
    known_failures = args.known_failure or []
    snapshot = capture_repo_snapshot(
        root,
        cwd=args.cwd,
        repo=args.repo,
        active_pr=args.active_pr,
        known_failures=known_failures,
        ci_status=args.ci_status,
    )
    budget = CandidateBudget(
        max_candidates=args.max_candidates,
        max_candidates_per_source=args.max_candidates_per_source,
        max_text_marker_candidates=args.max_text_marker_candidates,
        max_doc_marker_candidates=args.max_doc_marker_candidates,
        max_business_memory_candidates=args.max_business_memory_candidates,
        max_proposals=args.max_proposals,
        max_product_evolution_candidates_in_hybrid=args.max_product_evolution_candidates_in_hybrid,
    )
    discovery = discover_candidates(
        cwd=Path(args.cwd),
        intent=args.intent,
        discovery_mode=args.discovery_mode,
        proposal_allowance=args.proposal_allowance,
        known_failures=known_failures,
        pr_json_file=Path(args.pr_json_file) if args.pr_json_file else None,
        review_findings_file=Path(args.review_findings_file) if args.review_findings_file else None,
        review_threads_file=Path(args.review_threads_file) if args.review_threads_file else None,
        elect=True,
        max_tasks=args.max_tasks,
        budget=budget,
    )
    elected_next = discovery["elected_next"]
    recommended_next_action, reason, operator_confirmation_required, executor_contract_required = loop_tick_recommendation(
        cadence,
        snapshot,
        elected_next,
    )
    tick_id = f"loop-tick-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    executor_task = None
    if args.emit_executor_task and recommended_next_action == "requires_executor_contract":
        policy_denial, policy = resolve_executor_policy(
            policy,
            requested_allowed_paths=args.allowed_path or [],
            requested_required_checks=args.required_check or [],
            requested_max_minutes=args.executor_time_limit_minutes,
            requested_stop_conditions=args.stop_condition or [],
        )
        if policy_denial:
            recommended_next_action = "policy_denied"
            reason = policy_denial["reason"]
            operator_confirmation_required = True
            executor_contract_required = False
        else:
            policy["effective_stop_conditions"] = list(
                dict.fromkeys([*DEFAULT_EXECUTOR_STOP_CONDITIONS, *policy["effective_stop_conditions"]])
            )
            evidence_path = args.executor_evidence_path or str(root / "executor-results" / f"{tick_id}.json")
            executor_task = build_executor_task_packet(
                task=elected_next[0],
                snapshot=snapshot,
                repo_path=args.cwd,
                allowed_paths=policy["effective_allowed_paths"],
                required_checks=policy["effective_required_checks"],
                max_minutes=policy["effective_max_minutes"],
                max_tasks=1,
                stop_conditions=policy["effective_stop_conditions"],
                evidence_path=evidence_path,
                allowed_commands=policy["effective_allowed_commands"],
                denied_commands=policy["effective_denied_commands"],
                branch_policy=policy["branch_policy"],
            )
            valid_task, invalid_reason = validate_executor_task_packet(executor_task)
            if not valid_task:
                raise ValueError(f"invalid executor task packet: {invalid_reason}")
            recommended_next_action = "approve_executor_task"
            reason = "executor task packet emitted for operator approval"
            operator_confirmation_required = True
            executor_contract_required = False
    limitations = ["phase1_read_only"]
    if recommended_next_action == "policy_denied":
        limitations.append("policy_denied")
    elif executor_task is None:
        limitations.append("executor_contract_not_implemented")
    limitations.extend(
        [
            "executor_not_started",
            "git_pr_automation_not_implemented",
            "live_pr_review_sync_not_implemented",
        ]
    )
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "packet": "loop_tick",
        "tick_id": tick_id,
        "mode": "single_tick",
        "read_only": True,
        "executor_started": False,
        "epoch_started": False,
        "pr_action_started": False,
        "operator_confirmation_required": operator_confirmation_required,
        "executor_contract_required": executor_contract_required,
        "recommended_next_action": recommended_next_action,
        "reason": reason,
        "brake": brake,
        "cadence": cadence,
        "snapshot": snapshot,
        "candidate_discovery": discovery,
        "elected_next": elected_next,
        "executor_task": executor_task,
        "policy": policy,
        "limitations": limitations,
    }
    payload["audit_record"] = append_audit_record(root, loop_tick_audit_record(payload))
    emit(payload)
    return 0


CONTROLLED_LOOP_TICK_SCHEMA_VERSION = "controlled-loop-tick.v1"
CONTROLLED_LOOP_TICK_TERMINAL_CLOSEOUT_STATUSES = {"completed", "failed"}


def controlled_loop_tick_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def controlled_tick_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def read_controlled_tick_packet(path: Path, *, code: str, label: str) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        packet = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [controlled_loop_tick_blocker(code, f"{label} could not be read as JSON", path=str(path), error=str(exc))]
    if not isinstance(packet, dict):
        return None, [controlled_loop_tick_blocker(code, f"{label} must be a JSON object", path=str(path))]
    return packet, []


def controlled_tick_step(name: str, path: Path, packet: Any, status: str, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "file": str(path),
        "checksum": checksum_json(packet) if isinstance(packet, dict) else None,
        "blocker_codes": [blocker["code"] for blocker in blockers],
    }


def controlled_tick_packet_type_blockers(packet: dict[str, Any], *, label: str, expected_packet: str, expected_schema: str | None = None) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if expected_schema is not None and packet.get("schema_version") != expected_schema:
        blockers.append(
            controlled_loop_tick_blocker(
                "controlled_tick_packet_mismatch",
                f"{label} schema_version is invalid",
                expected=expected_schema,
                actual=packet.get("schema_version"),
            )
        )
    if packet.get("packet") != expected_packet:
        blockers.append(
            controlled_loop_tick_blocker(
                "controlled_tick_packet_mismatch",
                f"{label} packet is invalid",
                expected=expected_packet,
                actual=packet.get("packet"),
            )
        )
    return blockers


def controlled_tick_paths_match(left: Any, right: Path) -> bool:
    if isinstance(left, str) and left.strip():
        return _closeout_paths_match(left, right)
    return False


def controlled_tick_context_paths_match(context_file: Path, left: Any, right: Path) -> bool:
    return _closeout_paths_match(controlled_tick_context_path(context_file, left), right)


def controlled_tick_context_path(context_file: Path, value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context_file.parent / path
    return path


def controlled_tick_invocation_path(invocation: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    return _invocation_context_path(invocation, value)


def controlled_loop_tick_command(args: argparse.Namespace) -> int:
    packet_inputs = {
        "loop_tick": Path(args.loop_tick_file),
        "task": Path(args.task_file),
        "execution_start": Path(args.execution_start_file),
        "readiness": Path(args.readiness_file),
        "invocation_plan": Path(args.invocation_plan_file),
        "real_invocation": Path(args.real_invocation_file),
        "result": Path(args.result_file),
        "snapshot_after": Path(args.snapshot_after_file),
        "closeout": Path(args.closeout_file),
    }
    optional_git_pr_plan = Path(args.git_pr_plan_file) if args.git_pr_plan_file else None
    packets: dict[str, Any] = {}
    blockers: list[dict[str, Any]] = []
    read_codes = {
        "loop_tick": "loop_tick_evidence_missing",
        "task": "task_evidence_missing",
        "execution_start": "execution_start_evidence_missing",
        "readiness": "readiness_evidence_missing",
        "invocation_plan": "invocation_plan_evidence_missing",
        "real_invocation": "real_invocation_evidence_missing",
        "result": "result_evidence_missing",
        "snapshot_after": "snapshot_after_evidence_missing",
        "closeout": "closeout_evidence_missing",
    }
    step_blockers: dict[str, list[dict[str, Any]]] = {}
    for name, path in packet_inputs.items():
        packet, packet_blockers = read_controlled_tick_packet(
            path,
            code=read_codes[name],
            label=name.replace("_", " "),
        )
        packets[name] = packet
        step_blockers[name] = list(packet_blockers)
        blockers.extend(packet_blockers)

    if optional_git_pr_plan is not None:
        packet, packet_blockers = read_controlled_tick_packet(
            optional_git_pr_plan,
            code="git_pr_plan_evidence_missing",
            label="git pr plan",
        )
        packets["git_pr_plan"] = packet
        step_blockers["git_pr_plan"] = list(packet_blockers)
        blockers.extend(packet_blockers)

    if not blockers:
        loop_tick = packets["loop_tick"]
        task_packet = packets["task"]
        execution_start = packets["execution_start"]
        readiness = packets["readiness"]
        invocation_plan = packets["invocation_plan"]
        real_invocation = packets["real_invocation"]
        result_evidence = packets["result"]
        snapshot_after = packets["snapshot_after"]
        closeout = packets["closeout"]
        git_pr_plan = packets.get("git_pr_plan")

        expected_types = {
            "loop_tick": ("loop_tick", None),
            "execution_start": ("execution_start", EXECUTION_START_SCHEMA_VERSION),
            "readiness": ("executor_invocation_readiness", "executor-invocation-readiness.v1"),
            "invocation_plan": ("executor_invocation_plan", "executor-invocation-plan.v1"),
            "real_invocation": ("real_executor_invocation", REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION),
            "result": ("executor_result", "generic-executor-result.v1"),
            "closeout": ("executor_epoch_closeout", EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION),
        }
        for name, (expected_packet, expected_schema) in expected_types.items():
            packet_blockers = controlled_tick_packet_type_blockers(
                packets[name],
                label=name.replace("_", " "),
                expected_packet=expected_packet,
                expected_schema=expected_schema,
            )
            step_blockers[name].extend(packet_blockers)
            blockers.extend(packet_blockers)
        if git_pr_plan is not None:
            packet_blockers = controlled_tick_packet_type_blockers(
                git_pr_plan,
                label="git pr plan",
                expected_packet="git_pr_plan",
                expected_schema="git-pr-plan.v1",
            )
            packet_blockers.extend(validate_git_pr_plan_dry_run_packet(git_pr_plan))
            step_blockers["git_pr_plan"].extend(packet_blockers)
            blockers.extend(packet_blockers)

        valid_task, invalid_task_reason = validate_executor_task_packet(task_packet)
        if not valid_task:
            blocker = controlled_loop_tick_blocker("executor_task_invalid", invalid_task_reason)
            step_blockers["task"].append(blocker)
            blockers.append(blocker)
        task_checksum = checksum_json(task_packet)
        task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
        repo = task_packet.get("repo") if isinstance(task_packet.get("repo"), dict) else {}
        task_id = task.get("id")
        epoch_id = execution_start.get("epoch_id")

        loop_task = loop_tick.get("executor_task") if isinstance(loop_tick.get("executor_task"), dict) else {}
        if not controlled_tick_non_empty_string(loop_tick.get("tick_id")):
            blocker = controlled_loop_tick_blocker("loop_tick_identity_missing", "loop tick tick_id is required")
            step_blockers["loop_tick"].append(blocker)
            blockers.append(blocker)
        if checksum_json(loop_task) != task_checksum:
            blocker = controlled_loop_tick_blocker(
                "loop_tick_task_mismatch",
                "loop tick executor_task does not match the supplied task packet",
                expected=task_checksum,
                actual=checksum_json(loop_task),
            )
            step_blockers["loop_tick"].append(blocker)
            blockers.append(blocker)
        if loop_tick.get("recommended_next_action") != "approve_executor_task":
            blocker = controlled_loop_tick_blocker(
                "loop_tick_not_ready",
                "loop tick must have emitted an executor task for approval",
                actual=loop_tick.get("recommended_next_action"),
            )
            step_blockers["loop_tick"].append(blocker)
            blockers.append(blocker)
        if execution_start.get("valid") is not True or execution_start.get("epoch_started") is not True:
            blocker = controlled_loop_tick_blocker("execution_start_invalid", "execution start evidence must be valid and epoch_started")
            step_blockers["execution_start"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_non_empty_string(epoch_id):
            blocker = controlled_loop_tick_blocker("execution_start_invalid", "execution start epoch_id is required")
            step_blockers["execution_start"].append(blocker)
            blockers.append(blocker)
        if execution_start.get("executor_started") is not False:
            blocker = controlled_loop_tick_blocker("execution_start_invalid", "execution start must not start an executor")
            step_blockers["execution_start"].append(blocker)
            blockers.append(blocker)
        if execution_start.get("task_checksum") != task_checksum or execution_start.get("task_id") != task_id:
            blocker = controlled_loop_tick_blocker("execution_start_task_mismatch", "execution start task anchor does not match task packet")
            step_blockers["execution_start"].append(blocker)
            blockers.append(blocker)
        if "task_file" in execution_start and not controlled_tick_context_paths_match(packet_inputs["execution_start"], execution_start.get("task_file"), packet_inputs["task"]):
            blocker = controlled_loop_tick_blocker("execution_start_task_mismatch", "execution start task_file does not match supplied task file")
            step_blockers["execution_start"].append(blocker)
            blockers.append(blocker)

        snapshot_valid, snapshot_reason = validate_repo_snapshot(
            snapshot_after,
            expected_repo=repo.get("name") if isinstance(repo.get("name"), str) else None,
            expected_branch=repo.get("branch") if isinstance(repo.get("branch"), str) else None,
        )
        if not snapshot_valid:
            blocker = controlled_loop_tick_blocker("snapshot_after_invalid", f"snapshot-after evidence is invalid: {snapshot_reason}")
            step_blockers["snapshot_after"].append(blocker)
            blockers.append(blocker)

        readiness_task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
        readiness_epoch = readiness.get("active_epoch") if isinstance(readiness.get("active_epoch"), dict) else {}
        if readiness.get("valid") is not True or readiness.get("executor_invocation_ready") is not True:
            blocker = controlled_loop_tick_blocker("readiness_not_invocable", "readiness evidence is not invocable")
            step_blockers["readiness"].append(blocker)
            blockers.append(blocker)
        if readiness_task.get("checksum") != task_checksum or readiness_task.get("id") != task_id:
            blocker = controlled_loop_tick_blocker("readiness_task_mismatch", "readiness task anchor does not match task packet")
            step_blockers["readiness"].append(blocker)
            blockers.append(blocker)
        if not _closeout_paths_match(controlled_tick_context_path(packet_inputs["readiness"], readiness_task.get("file")), packet_inputs["task"]):
            blocker = controlled_loop_tick_blocker("readiness_task_mismatch", "readiness task file does not match supplied task file")
            step_blockers["readiness"].append(blocker)
            blockers.append(blocker)
        if readiness_epoch.get("id") != epoch_id:
            blocker = controlled_loop_tick_blocker("readiness_epoch_mismatch", "readiness active epoch does not match execution start")
            step_blockers["readiness"].append(blocker)
            blockers.append(blocker)

        plan_readiness = invocation_plan.get("readiness") if isinstance(invocation_plan.get("readiness"), dict) else {}
        if invocation_plan.get("valid") is not True or invocation_plan.get("executor_invocation_planned") is not True:
            blocker = controlled_loop_tick_blocker("invocation_plan_not_invocable", "invocation plan evidence is not invocable")
            step_blockers["invocation_plan"].append(blocker)
            blockers.append(blocker)
        if plan_readiness.get("checksum") != checksum_json(readiness):
            blocker = controlled_loop_tick_blocker("invocation_plan_readiness_mismatch", "invocation plan readiness checksum does not match supplied readiness")
            step_blockers["invocation_plan"].append(blocker)
            blockers.append(blocker)
        if "file" in plan_readiness and not _closeout_paths_match(controlled_tick_context_path(packet_inputs["invocation_plan"], plan_readiness.get("file")), packet_inputs["readiness"]):
            blocker = controlled_loop_tick_blocker("invocation_plan_readiness_mismatch", "invocation plan readiness file does not match supplied readiness file")
            step_blockers["invocation_plan"].append(blocker)
            blockers.append(blocker)

        if real_invocation.get("valid") is not True or real_invocation.get("executor_started") is not True:
            blocker = controlled_loop_tick_blocker("real_invocation_invalid", "real invocation evidence must be valid and executor_started")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_non_empty_string(real_invocation.get("invocation_id")):
            blocker = controlled_loop_tick_blocker("real_invocation_identity_missing", "real invocation invocation_id is required")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("plan_checksum") != checksum_json(invocation_plan):
            blocker = controlled_loop_tick_blocker("real_invocation_plan_mismatch", "real invocation plan checksum does not match invocation plan")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("plan_target_checksum") != invocation_plan.get("target_checksum"):
            blocker = controlled_loop_tick_blocker("real_invocation_plan_mismatch", "real invocation target checksum does not match invocation plan")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if not _closeout_paths_match(controlled_tick_invocation_path(real_invocation, real_invocation.get("record_file")), packet_inputs["real_invocation"]):
            blocker = controlled_loop_tick_blocker("real_invocation_record_mismatch", "real invocation record_file does not match supplied real invocation file")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if not _closeout_paths_match(controlled_tick_invocation_path(real_invocation, real_invocation.get("plan_file")), packet_inputs["invocation_plan"]):
            blocker = controlled_loop_tick_blocker("real_invocation_plan_mismatch", "real invocation plan_file does not match supplied invocation plan file")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("result_evidence_checksum") != checksum_json(result_evidence):
            blocker = controlled_loop_tick_blocker("real_invocation_result_mismatch", "real invocation result checksum does not match supplied result")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if not _closeout_paths_match(controlled_tick_invocation_path(real_invocation, real_invocation.get("result_file")), packet_inputs["result"]):
            blocker = controlled_loop_tick_blocker("real_invocation_result_mismatch", "real invocation result_file does not match supplied result file")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)

        if result_evidence.get("task_id") != task_id:
            blocker = controlled_loop_tick_blocker("result_task_mismatch", "executor result task_id does not match task packet")
            step_blockers["result"].append(blocker)
            blockers.append(blocker)

        if closeout.get("valid") is not True:
            blocker = controlled_loop_tick_blocker("closeout_invalid", "closeout evidence must be valid")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if closeout.get("epoch_id") != epoch_id:
            blocker = controlled_loop_tick_blocker("closeout_epoch_mismatch", "closeout epoch does not match execution start")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_context_paths_match(packet_inputs["closeout"], closeout.get("task_file"), packet_inputs["task"]):
            blocker = controlled_loop_tick_blocker("closeout_task_mismatch", "closeout task_file does not match supplied task file")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_context_paths_match(packet_inputs["closeout"], closeout.get("result_file"), packet_inputs["result"]):
            blocker = controlled_loop_tick_blocker("closeout_result_mismatch", "closeout result_file does not match supplied result file")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_context_paths_match(packet_inputs["closeout"], closeout.get("snapshot_after_file"), packet_inputs["snapshot_after"]):
            blocker = controlled_loop_tick_blocker("closeout_snapshot_mismatch", "closeout snapshot_after_file does not match supplied snapshot-after file")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if closeout.get("snapshot_after_checksum") != checksum_epoch_json(snapshot_after):
            blocker = controlled_loop_tick_blocker("closeout_snapshot_mismatch", "closeout snapshot checksum does not match supplied snapshot-after evidence")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        closeout_invocation = closeout.get("real_invocation") if isinstance(closeout.get("real_invocation"), dict) else {}
        closeout_invocation_after_checksum = closeout_invocation.get("after_checksum")
        if not isinstance(closeout_invocation_after_checksum, str) or not closeout_invocation_after_checksum.strip():
            blocker = controlled_loop_tick_blocker(
                "closeout_invocation_mismatch",
                "closeout real invocation after_checksum is required for controlled tick composition",
            )
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        elif checksum_json(real_invocation) != closeout_invocation_after_checksum:
            blocker = controlled_loop_tick_blocker(
                "closeout_invocation_mismatch",
                "closeout real invocation after_checksum does not match supplied real invocation evidence",
            )
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if not controlled_tick_context_paths_match(packet_inputs["closeout"], closeout_invocation.get("path"), packet_inputs["real_invocation"]):
            blocker = controlled_loop_tick_blocker("closeout_invocation_mismatch", "closeout real invocation path does not match supplied real invocation file")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if closeout_invocation.get("invocation_id") != real_invocation.get("invocation_id"):
            blocker = controlled_loop_tick_blocker("closeout_invocation_mismatch", "closeout real invocation id does not match supplied real invocation evidence")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        closeout_validation = closeout.get("validation") if isinstance(closeout.get("validation"), dict) else {}
        if closeout_validation.get("valid") is not True:
            blocker = controlled_loop_tick_blocker("closeout_validation_mismatch", "closeout validation evidence is not valid")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        closeout_core_packet = {
            key: value
            for key, value in closeout.items()
            if key not in {"audit_record", "run_record", "real_invocation"}
        }
        epoch_closeout_checksum = checksum_json(closeout_core_packet)
        if closeout_invocation.get("epoch_closeout_checksum") != epoch_closeout_checksum:
            blocker = controlled_loop_tick_blocker("closeout_invocation_mismatch", "closeout real invocation epoch_closeout_checksum does not match closeout evidence")
            step_blockers["closeout"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("epoch_closeout_checksum") != epoch_closeout_checksum:
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation epoch_closeout_checksum does not match closeout evidence")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("closeout_status") != closeout.get("closeout_status"):
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation closeout_status does not match closeout evidence")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if (
            closeout.get("closeout_status") not in CONTROLLED_LOOP_TICK_TERMINAL_CLOSEOUT_STATUSES
            or real_invocation.get("closeout_status") not in CONTROLLED_LOOP_TICK_TERMINAL_CLOSEOUT_STATUSES
        ):
            blocker = controlled_loop_tick_blocker(
                "closeout_not_completed",
                "closeout and real invocation must be terminal completed or failed to emit a controlled tick",
            )
            step_blockers["closeout"].append(blocker)
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("epoch_id") != epoch_id:
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation epoch_id does not match execution start")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("validation_packet_checksum") != checksum_json(closeout_validation):
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation validation checksum does not match closeout validation evidence")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if real_invocation.get("snapshot_after_checksum") != checksum_epoch_json(snapshot_after):
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation snapshot-after checksum does not match supplied snapshot-after evidence")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)
        if not _closeout_paths_match(controlled_tick_invocation_path(real_invocation, real_invocation.get("task_file")), packet_inputs["task"]):
            blocker = controlled_loop_tick_blocker("real_invocation_closeout_mismatch", "real invocation task_file does not match supplied task file")
            step_blockers["real_invocation"].append(blocker)
            blockers.append(blocker)

        embedded_git_pr_plan = closeout.get("git_pr_plan") if isinstance(closeout.get("git_pr_plan"), dict) else None
        if git_pr_plan is not None:
            if embedded_git_pr_plan is None:
                blocker = controlled_loop_tick_blocker("git_pr_plan_unanchored", "supplied git-pr plan is not embedded in closeout evidence")
                step_blockers["git_pr_plan"].append(blocker)
                blockers.append(blocker)
            elif checksum_json(git_pr_plan) != checksum_json(embedded_git_pr_plan):
                blocker = controlled_loop_tick_blocker("git_pr_plan_mismatch", "supplied git-pr plan does not match closeout embedded git-pr plan")
                step_blockers["git_pr_plan"].append(blocker)
                blockers.append(blocker)

    valid = not blockers
    tick_id = f"controlled-loop-tick-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    loop_tick = packets.get("loop_tick") if isinstance(packets.get("loop_tick"), dict) else {}
    task_packet = packets.get("task") if isinstance(packets.get("task"), dict) else {}
    execution_start = packets.get("execution_start") if isinstance(packets.get("execution_start"), dict) else {}
    real_invocation = packets.get("real_invocation") if isinstance(packets.get("real_invocation"), dict) else {}
    closeout = packets.get("closeout") if isinstance(packets.get("closeout"), dict) else {}
    task = task_packet.get("task") if isinstance(task_packet.get("task"), dict) else {}
    next_decision = closeout.get("next_decision") if isinstance(closeout.get("next_decision"), dict) else {}
    checksums = {
        name: checksum_json(packet)
        for name, packet in packets.items()
        if isinstance(packet, dict)
    }
    files = {name: str(path) for name, path in packet_inputs.items()}
    if optional_git_pr_plan is not None:
        files["git_pr_plan"] = str(optional_git_pr_plan)
    steps = [
        controlled_tick_step(name, path, packets.get(name), "accepted" if not step_blockers.get(name) else "blocked", step_blockers.get(name, []))
        for name, path in packet_inputs.items()
    ]
    if optional_git_pr_plan is not None:
        steps.append(
            controlled_tick_step(
                "git_pr_plan",
                optional_git_pr_plan,
                packets.get("git_pr_plan"),
                "accepted" if not step_blockers.get("git_pr_plan") else "blocked",
                step_blockers.get("git_pr_plan", []),
            )
        )
    recommended_next_action = "controlled_tick_complete" if valid else "inspect_controlled_tick_blockers"
    reason = "controlled loop tick evidence is internally consistent" if valid else "controlled loop tick evidence is incomplete or mismatched"
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": CONTROLLED_LOOP_TICK_SCHEMA_VERSION,
        "packet": "controlled_loop_tick",
        "generated_at": utc_now(),
        "tick_id": tick_id,
        "source_tick_id": loop_tick.get("tick_id"),
        "valid": valid,
        "controlled_tick_status": "completed" if valid else "blocked",
        "reason": reason,
        "executor_started": real_invocation.get("executor_started") is True,
        "side_effects": [],
        "recommended_next_action": recommended_next_action,
        "operator_confirmation_required": not valid,
        "task": {
            "id": task.get("id"),
            "checksum": checksums.get("task"),
            "source": task.get("source"),
        },
        "epoch": {
            "id": execution_start.get("epoch_id"),
            "closeout_status": closeout.get("closeout_status"),
            "epoch_status": closeout.get("epoch_status"),
        },
        "real_invocation": {
            "invocation_id": real_invocation.get("invocation_id"),
            "side_effect_mode": real_invocation.get("side_effect_mode"),
            "closeout_status": real_invocation.get("closeout_status"),
        },
        "next_decision": next_decision,
        "files": files,
        "checksums": checksums,
        "steps": steps,
        "blockers": blockers,
        "limitations": [
            "composes_existing_local_evidence_only",
            "does_not_start_executor",
            "does_not_retry_executor",
            "does_not_rewrite_invocation_or_closeout_records",
            "does_not_execute_git_commands",
            "does_not_call_github",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
            "does_not_assign_roles_or_claim_distributed_locks",
        ],
    }
    if valid:
        payload["side_effects"].append("controlled_loop_tick_audit_appended")
        try:
            audit_record = controlled_loop_tick_audit_record(payload)
            audit_blockers = validate_controlled_loop_tick_audit_record(audit_record, 0)
            if audit_blockers:
                raise ValueError(f"controlled_loop_tick audit record is invalid: {audit_blockers[0]['code']}")
            payload["audit_record"] = append_audit_record(args.root, audit_record)
        except Exception as exc:
            payload["valid"] = False
            payload["controlled_tick_status"] = "blocked"
            payload["reason"] = "controlled loop tick audit record could not be written"
            payload["recommended_next_action"] = "recover_controlled_tick_audit"
            payload["operator_confirmation_required"] = True
            payload["side_effects"] = []
            payload["blockers"] = [
                {
                    "code": "controlled_loop_tick_audit_append_failed",
                    "message": "controlled loop tick audit record could not be written",
                    "error": str(exc),
                }
            ]
            emit(payload)
            return 2
    emit(payload)
    return 0 if payload["valid"] else 1


def build_executor_result_validation_payload(
    *,
    root: Path | None,
    task_file: Path,
    result_file: Path,
    task_packet: Any,
    result_evidence: Any,
    executor_started: bool,
    invocation_id: str | None = None,
    allow_succeeded_dirty_worktree: bool = False,
) -> dict[str, Any]:
    valid, reason = validate_executor_result_evidence(
        result_evidence,
        task_packet,
        allow_succeeded_dirty_worktree=allow_succeeded_dirty_worktree,
    )
    if valid:
        expected_output = task_packet.get("expected_output") if isinstance(task_packet, dict) else {}
        expected_path = expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
        if expected_path is not None and Path(expected_path).expanduser().resolve() != result_file.expanduser().resolve():
            valid = False
            reason = "executor result file does not match task expected_output.evidence_path"
    active_stop = None
    missing_runtime_root_for_stop = False
    stop_conditions = task_packet.get("stop_conditions") if isinstance(task_packet, dict) else []
    result_status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    needs_brake_check = (
        valid
        and isinstance(stop_conditions, list)
        and "brake_not_drive" in stop_conditions
        and result_status != "stopped"
    )
    if needs_brake_check and root is None:
        valid = False
        reason = "runtime root is required to validate brake_not_drive stop condition"
        missing_runtime_root_for_stop = True
    if root is not None:
        brake = read_brake(root)
        if (
            needs_brake_check
            and valid
            and brake["status"] != "DRIVE"
        ):
            valid = False
            reason = (
                f"cadence brake is {brake['status']}; "
                "executor result must report stopped before completion can be recorded"
            )
            active_stop = {
                "brake_status": brake["status"],
                "cadence": cadence_state(brake),
                "reason": brake.get("reason"),
                "required_result_status": "stopped",
            }
    recommended_next_action = "record_executor_result" if valid else "fix_executor_evidence"
    if active_stop is not None:
        recommended_next_action = "stop_active_loop"
    elif missing_runtime_root_for_stop:
        recommended_next_action = "provide_runtime_root"
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "packet": "executor_result_validation",
        "valid": valid,
        "reason": reason,
        "task_file": str(task_file),
        "result_file": str(result_file),
        "executor_started": executor_started,
        "recommended_next_action": recommended_next_action,
    }
    if invocation_id:
        payload["invocation_id"] = invocation_id
    if active_stop is not None:
        payload["active_stop"] = active_stop
    return payload


def load_closeout_run_record(root: Path, run_record_file: Path) -> tuple[Any | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    supplied_path = run_record_file.resolve(strict=False)
    run_dir = execution_run_dir(root).resolve(strict=False)
    supplied_inside_run_dir = path_is_relative_to(supplied_path, run_dir)
    if not supplied_inside_run_dir:
        blockers.append(
            execution_run_blocker(
                "run_record_path_mismatch",
                "execution run record file must be under the runtime execution-runs directory",
                run_record_file=str(run_record_file),
                expected_directory=str(execution_run_dir(root)),
            )
        )
    try:
        run_record = read_json(run_record_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(
            execution_run_blocker(
                "run_record_invalid",
                "execution run record file could not be read as JSON",
                run_record_file=str(run_record_file),
                error=str(exc),
            )
        )
        return None, blockers
    if supplied_inside_run_dir and isinstance(run_record, dict) and isinstance(run_record.get("run_id"), str):
        try:
            canonical_path = execution_run_path(root, run_record["run_id"]).resolve(strict=False)
        except ValueError as exc:
            blockers.append(
                execution_run_blocker(
                    "run_record_invalid",
                    "execution run record run_id is invalid",
                    field="run_id",
                    error=str(exc),
                )
            )
        else:
            if supplied_path != canonical_path:
                blockers.append(
                    execution_run_blocker(
                        "run_record_path_mismatch",
                        "execution run record file does not match the canonical run_id path",
                        run_record_file=str(run_record_file),
                        expected_run_record_file=str(execution_run_path(root, run_record["run_id"])),
                    )
                )
    return run_record, blockers


def real_invocation_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _closeout_path_value(value: Any) -> str | None:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return None
    try:
        return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))
    except (OSError, RuntimeError, ValueError):
        return None


def _closeout_paths_match(left: Any, right: str | Path) -> bool:
    normalized_left = _closeout_path_value(left)
    normalized_right = _closeout_path_value(str(right))
    if normalized_left is not None and normalized_right is not None:
        return normalized_left == normalized_right
    return left == str(right)


def _closeout_local_branch_refs(cwd: Path) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    try:
        return _local_branch_refs(cwd), None
    except (OSError, RuntimeError, ValueError) as exc:
        return None, real_invocation_blocker(
            "materialized_change_mismatch",
            "current local branch refs could not be inspected for real executor closeout",
            error=str(exc),
            path=str(cwd),
        )


def _closeout_dirty_files(cwd: Path) -> tuple[set[str] | None, dict[str, Any] | None]:
    try:
        dirty_files, blocker = _local_dirty_files(cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, real_invocation_blocker(
            "materialized_change_mismatch",
            "current dirty files could not be inspected for real executor closeout",
            error=str(exc),
            path=str(cwd),
        )
    if blocker is not None:
        return None, real_invocation_blocker(
            "materialized_change_mismatch",
            "current dirty files could not be inspected for real executor closeout",
            invocation_blocker=blocker,
            path=str(cwd),
        )
    return dirty_files, None


def _closeout_dirty_fingerprint(cwd: Path, dirty_files: set[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    fingerprint, blocker = _dirty_worktree_fingerprint(cwd, dirty_files)
    if blocker is not None:
        return None, real_invocation_blocker(
            "materialized_change_mismatch",
            "current dirty worktree could not be fingerprinted for real executor closeout",
            invocation_blocker=blocker,
            path=str(cwd),
        )
    return fingerprint, None


def _materialized_evidence_public_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in evidence.items()
        if key not in {"worktree_fingerprint_schema_version", "worktree_fingerprint_checksum"}
    }


def _invocation_context_path(invocation: dict[str, Any], value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    invocation_cwd = invocation.get("invocation_cwd")
    if isinstance(invocation_cwd, str) and invocation_cwd.strip():
        return (Path(invocation_cwd).expanduser() / path).resolve(strict=False)
    return path


def _readiness_context_path(readiness_path: Path | None, value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    path = Path(value).expanduser()
    if path.is_absolute() or readiness_path is None:
        return path
    return (readiness_path.parent / path).resolve(strict=False)


def load_closeout_real_invocation(
    root: Path,
    invocation_file: Path,
) -> tuple[Any | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    supplied_path = invocation_file.resolve(strict=False)
    invocation_dir = real_executor_invocation_dir(root).resolve(strict=False)
    supplied_inside_invocation_dir = path_is_relative_to(supplied_path, invocation_dir)
    if not supplied_inside_invocation_dir:
        blockers.append(
            real_invocation_blocker(
                "invocation_record_missing",
                "real executor invocation file must be under the runtime real-executor-invocations directory",
                invocation_file=str(invocation_file),
                expected_directory=str(real_executor_invocation_dir(root)),
            )
        )
    try:
        invocation = read_json(invocation_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(
            real_invocation_blocker(
                "invocation_record_missing",
                "real executor invocation file could not be read as JSON",
                invocation_file=str(invocation_file),
                error=str(exc),
            )
        )
        return None, blockers
    if supplied_inside_invocation_dir and isinstance(invocation, dict) and isinstance(invocation.get("invocation_id"), str):
        try:
            canonical_path = real_executor_invocation_path(root, invocation["invocation_id"]).resolve(strict=False)
        except ValueError as exc:
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    "real executor invocation id is invalid",
                    field="invocation_id",
                    error=str(exc),
                )
            )
        else:
            if supplied_path != canonical_path:
                blockers.append(
                    real_invocation_blocker(
                        "invocation_checksum_mismatch",
                        "real executor invocation file does not match the canonical invocation_id path",
                        invocation_file=str(invocation_file),
                        expected_invocation_file=str(real_executor_invocation_path(root, invocation["invocation_id"])),
                    )
                )
    return invocation, blockers


def _read_closeout_invocation_object(path: Path, *, code: str, label: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        packet = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [real_invocation_blocker(code, f"{label} could not be read as JSON", path=str(path), error=str(exc))]
    if not isinstance(packet, dict):
        return None, [real_invocation_blocker(code, f"{label} must be a JSON object", path=str(path))]
    return packet, []


def _closeout_real_invocation_audit_blockers(
    root: Path,
    invocation: dict[str, Any],
    invocation_file: Path,
) -> list[dict[str, Any]]:
    target = audit_events_path(root)
    invocation_id = invocation.get("invocation_id")
    matching_records: list[dict[str, Any]] = []
    if not target.exists():
        return [
            real_invocation_blocker(
                "audit_chain_mismatch",
                "real executor invocation audit record is missing",
                invocation_id=invocation_id,
            )
        ]
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("event") != "real_executor_invocation_record":
                    continue
                if record.get("action") != "record_real_executor_invocation":
                    continue
                if record.get("invocation_id") != invocation_id:
                    continue
                if not _closeout_paths_match(record.get("invocation_record_file"), invocation_file):
                    continue
                record["_line"] = line_number
                matching_records.append(record)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [
            real_invocation_blocker(
                "audit_chain_mismatch",
                "real executor invocation audit records could not be read",
                error=str(exc),
                path=str(target),
            )
        ]

    if not matching_records:
        return [
            real_invocation_blocker(
                "audit_chain_mismatch",
                "real executor invocation audit record is missing",
                invocation_id=invocation_id,
                invocation_file=str(invocation_file),
            )
        ]
    if len(matching_records) != 1:
        return [
            real_invocation_blocker(
                "audit_chain_mismatch",
                "real executor invocation audit record must be unique",
                invocation_id=invocation_id,
                count=len(matching_records),
            )
        ]

    record = matching_records[0]
    blockers: list[dict[str, Any]] = []
    audit_chain = invocation.get("audit_chain") if isinstance(invocation.get("audit_chain"), dict) else {}
    expected_previous_hash = audit_chain.get("chain_head")
    if record.get("previous_event_hash") != expected_previous_hash:
        blockers.append(
            real_invocation_blocker(
                "audit_chain_mismatch",
                "real executor invocation audit record is not anchored to invocation audit-chain evidence",
                expected_previous_event_hash=expected_previous_hash,
                actual_previous_event_hash=record.get("previous_event_hash"),
                line=record.get("_line"),
            )
        )
    actual_checksum = checksum_json(invocation)
    if record.get("invocation_record_checksum") != actual_checksum:
        blockers.append(
            real_invocation_blocker(
                "invocation_checksum_mismatch",
                "real executor invocation record checksum does not match audited invocation record",
                expected=record.get("invocation_record_checksum"),
                actual=actual_checksum,
                line=record.get("_line"),
            )
        )
    return blockers


def _invocation_plan_and_readiness(
    invocation: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    plan_file = invocation.get("plan_file")
    if not isinstance(plan_file, str) or not plan_file.strip():
        return None, None, None, [
            real_invocation_blocker(
                "invocation_checksum_mismatch",
                "real executor invocation plan_file is required",
            )
        ]
    plan_path = _invocation_context_path(invocation, plan_file)
    plan, plan_blockers = _read_closeout_invocation_object(
        plan_path,
        code="invocation_checksum_mismatch",
        label="real executor invocation plan",
    )
    blockers.extend(plan_blockers)
    readiness: dict[str, Any] | None = None
    readiness_path: Path | None = None
    if plan is not None:
        if invocation.get("plan_checksum") != checksum_json(plan):
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    "real executor invocation plan checksum does not match the plan file",
                    expected=invocation.get("plan_checksum"),
                    actual=checksum_json(plan),
                )
            )
        if invocation.get("plan_target_checksum") != plan.get("target_checksum"):
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    "real executor invocation plan target checksum does not match the plan file",
                    expected=invocation.get("plan_target_checksum"),
                    actual=plan.get("target_checksum"),
                )
            )
        readiness_summary = plan.get("readiness") if isinstance(plan.get("readiness"), dict) else {}
        readiness_file = readiness_summary.get("file")
        if not isinstance(readiness_file, str) or not readiness_file.strip():
            blockers.append(
                real_invocation_blocker(
                    "invocation_epoch_mismatch",
                    "real executor invocation plan readiness file is required",
                )
            )
        else:
            readiness_path = _invocation_context_path(invocation, readiness_file)
            readiness, readiness_blockers = _read_closeout_invocation_object(
                readiness_path,
                code="invocation_epoch_mismatch",
                label="real executor invocation readiness evidence",
            )
            blockers.extend(readiness_blockers)
            if readiness is not None and readiness_summary.get("checksum") != checksum_json(readiness):
                blockers.append(
                    real_invocation_blocker(
                        "invocation_checksum_mismatch",
                        "real executor invocation readiness checksum does not match the readiness file",
                        expected=readiness_summary.get("checksum"),
                        actual=checksum_json(readiness),
                    )
                )
    return plan, readiness, readiness_path, blockers


def validate_closeout_real_invocation(
    root: Path,
    invocation: Any,
    *,
    invocation_file: Path,
    epoch_id: str,
    task_file: Path,
    result_file: Path,
    task_packet: Any,
    result_evidence: Any,
    snapshot_after: Any,
    validation: dict[str, Any],
    cwd: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not isinstance(invocation, dict):
        return None, [real_invocation_blocker("invocation_record_missing", "real executor invocation must be a JSON object")]
    blockers: list[dict[str, Any]] = []
    if invocation.get("protocol_version") != PROTOCOL_VERSION:
        blockers.append(real_invocation_blocker("invocation_checksum_mismatch", "real executor invocation protocol_version is invalid"))
    if invocation.get("schema_version") != REAL_EXECUTOR_INVOCATION_SCHEMA_VERSION:
        blockers.append(real_invocation_blocker("invocation_checksum_mismatch", "real executor invocation schema_version is invalid"))
    if invocation.get("packet") != "real_executor_invocation":
        blockers.append(real_invocation_blocker("invocation_checksum_mismatch", "real executor invocation packet must be real_executor_invocation"))
    invocation_id = invocation.get("invocation_id")
    if not isinstance(invocation_id, str) or not invocation_id.strip():
        blockers.append(real_invocation_blocker("invocation_record_missing", "real executor invocation invocation_id is required"))
    if invocation.get("valid") is not True:
        blockers.append(
            real_invocation_blocker(
                "invocation_result_invalid",
                "real executor invocation was not valid",
                invocation_blockers=invocation.get("blockers") if isinstance(invocation.get("blockers"), list) else [],
            )
        )
    if invocation.get("executor_started") is not True:
        blockers.append(
            real_invocation_blocker(
                "invocation_result_invalid",
                "real executor invocation must have started the executor",
            )
        )
    if invocation.get("timed_out") is True:
        blockers.append(real_invocation_blocker("invocation_result_invalid", "real executor invocation timed out"))
    if isinstance(invocation.get("blockers"), list) and invocation["blockers"]:
        blockers.append(
            real_invocation_blocker(
                "invocation_result_invalid",
                "real executor invocation has unresolved blockers",
                invocation_blockers=invocation["blockers"],
            )
        )
    if invocation.get("closeout_status") not in (None, "pending"):
        blockers.append(
            real_invocation_blocker(
                "invocation_result_invalid",
                "real executor invocation has already been bound to closeout",
                closeout_status=invocation.get("closeout_status"),
            )
        )
    if not _closeout_paths_match(invocation.get("record_file"), invocation_file):
        blockers.append(
            real_invocation_blocker(
                "invocation_checksum_mismatch",
                "real executor invocation record_file does not match the supplied invocation file",
                invocation_record_file=invocation.get("record_file"),
                supplied_invocation_file=str(invocation_file),
            )
        )
    if invocation.get("result_present") is not True:
        blockers.append(real_invocation_blocker("invocation_result_missing", "real executor invocation result file was not present"))
    if not _closeout_paths_match(invocation.get("result_file"), result_file):
        blockers.append(
            real_invocation_blocker(
                "invocation_result_missing",
                "real executor invocation result_file does not match the supplied result file",
                invocation_result_file=invocation.get("result_file"),
                result_file=str(result_file),
            )
        )
    result_evidence_checksum = checksum_json(result_evidence) if isinstance(result_evidence, dict) else None
    if invocation.get("result_evidence_checksum") != result_evidence_checksum:
        blockers.append(
            real_invocation_blocker(
                "invocation_checksum_mismatch",
                "real executor invocation result evidence checksum does not match the supplied result file",
                expected=invocation.get("result_evidence_checksum"),
                actual=result_evidence_checksum,
            )
        )
    command = invocation.get("command") if isinstance(invocation.get("command"), dict) else {}
    if not _closeout_paths_match(command.get("expected_result_path"), result_file):
        blockers.append(
            real_invocation_blocker(
                "invocation_result_missing",
                "real executor invocation command expected_result_path does not match the supplied result file",
                expected_result_path=command.get("expected_result_path"),
                result_file=str(result_file),
            )
        )
    if validation.get("valid") is not True:
        blockers.append(
            real_invocation_blocker(
                "invocation_result_invalid",
                "real executor invocation result evidence is not valid for closeout",
                validation_reason=validation.get("reason"),
            )
        )

    _plan, readiness, readiness_path, plan_blockers = _invocation_plan_and_readiness(invocation)
    blockers.extend(plan_blockers)
    if readiness is not None:
        readiness_task = readiness.get("task") if isinstance(readiness.get("task"), dict) else {}
        readiness_task_file = _readiness_context_path(readiness_path, readiness_task.get("file"))
        if not _closeout_paths_match(readiness_task_file, task_file):
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    "real executor invocation readiness task file does not match the supplied task file",
                    readiness_task_file=readiness_task.get("file"),
                    task_file=str(task_file),
                )
            )
        if readiness_task.get("checksum") != checksum_json(task_packet):
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    "real executor invocation readiness task checksum does not match the supplied task packet",
                )
            )
        active_epoch = readiness.get("active_epoch") if isinstance(readiness.get("active_epoch"), dict) else {}
        if active_epoch.get("id") != epoch_id:
            blockers.append(
                real_invocation_blocker(
                    "invocation_epoch_mismatch",
                    "real executor invocation active epoch does not match the requested closeout epoch",
                    expected_epoch_id=epoch_id,
                    invocation_epoch_id=active_epoch.get("id"),
                )
            )
        repo_packet = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
        ownership = readiness.get("ownership") if isinstance(readiness.get("ownership"), dict) else {}
        ownership_target = ownership.get("id") or ownership.get("path")
        if not isinstance(ownership_target, str) or not ownership_target.strip():
            blockers.append(
                real_invocation_blocker(
                    "ownership_closeout_blocked",
                    "real executor invocation readiness ownership evidence is required",
                )
            )
        else:
            task = task_packet.get("task") if isinstance(task_packet, dict) and isinstance(task_packet.get("task"), dict) else {}
            ownership_validation = validate_work_ownership(
                root=root,
                target=ownership_target,
                cwd=cwd,
                repo=repo_packet.get("name"),
                branch=repo_packet.get("branch"),
                task_id=task.get("id"),
                require_active=True,
            )
            ownership_record = (
                ownership_validation.get("record") if isinstance(ownership_validation.get("record"), dict) else {}
            )
            if ownership_validation.get("valid") is not True or ownership_record.get("epoch_id") != epoch_id:
                blockers.append(
                    real_invocation_blocker(
                        "ownership_closeout_blocked",
                        "active work ownership no longer validates for real executor closeout",
                        ownership_target=ownership_target,
                        ownership_blockers=(
                            ownership_validation.get("blockers")
                            if isinstance(ownership_validation.get("blockers"), list)
                            else []
                        ),
                    )
                )
            else:
                for field in ("id", "task_id", "candidate_id", "role", "claimer", "repo", "branch", "head", "epoch_id"):
                    expected = ownership.get(field)
                    actual = ownership_record.get(field)
                    if expected is not None and actual != expected:
                        blockers.append(
                            real_invocation_blocker(
                                "ownership_closeout_blocked",
                                f"active work ownership {field} no longer matches readiness evidence",
                                field=field,
                                expected=expected,
                                actual=actual,
                            )
                        )
                        break
                if "path" in ownership and not _closeout_paths_match(ownership.get("path"), ownership_record.get("path")):
                    blockers.append(
                        real_invocation_blocker(
                            "ownership_closeout_blocked",
                            "active work ownership path no longer matches readiness evidence",
                            expected=ownership.get("path"),
                            actual=ownership_record.get("path"),
                        )
                    )

    repo_packet = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    repository_before = invocation.get("repository_before") if isinstance(invocation.get("repository_before"), dict) else {}
    for field, expected in (
        ("cwd", repo_packet.get("path")),
        ("branch", repo_packet.get("branch")),
        ("head", repo_packet.get("head")),
    ):
        actual = repository_before.get(field)
        matches = _closeout_paths_match(actual, expected) if field == "cwd" and isinstance(expected, str) else actual == expected
        if expected is not None and not matches:
            blockers.append(
                real_invocation_blocker(
                    "invocation_checksum_mismatch",
                    f"real executor invocation repository_before.{field} does not match task repo.{field}",
                    expected=expected,
                    actual=actual,
                )
            )
    if repository_before.get("dirty_worktree") is not False:
        blockers.append(
            real_invocation_blocker(
                "invocation_checksum_mismatch",
                "real executor invocation repository_before must be clean",
            )
        )

    repository_after = invocation.get("repository_after") if isinstance(invocation.get("repository_after"), dict) else {}
    if isinstance(snapshot_after, dict):
        for field in ("cwd", "branch", "head", "dirty_worktree"):
            expected = repository_after.get(field)
            actual = snapshot_after.get(field)
            matches = _closeout_paths_match(actual, expected) if field == "cwd" and isinstance(expected, str) else actual == expected
            if expected is not None and not matches:
                blockers.append(
                    real_invocation_blocker(
                        "materialized_change_mismatch",
                        f"snapshot_after {field} does not match real executor invocation repository_after.{field}",
                        expected=expected,
                        actual=actual,
                    )
                )
    before_branch_refs = (
        repository_before.get("local_branch_refs") if isinstance(repository_before.get("local_branch_refs"), dict) else {}
    )
    after_branch_refs = (
        repository_after.get("local_branch_refs") if isinstance(repository_after.get("local_branch_refs"), dict) else {}
    )
    branch_ref_changes = _branch_ref_changes(before_branch_refs, after_branch_refs)
    if (
        repository_after.get("branch") != repository_before.get("branch")
        or repository_after.get("head") != repository_before.get("head")
        or any(branch_ref_changes.values())
    ):
        blockers.append(
            real_invocation_blocker(
                "materialized_change_mismatch",
                "real executor invocation changed repository branch, HEAD, or local branch refs",
                before_branch=repository_before.get("branch"),
                after_branch=repository_after.get("branch"),
                before_head=repository_before.get("head"),
                after_head=repository_after.get("head"),
                local_branch_ref_changes=branch_ref_changes,
            )
        )
    try:
        current_repo = current_repo_evidence(cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        blockers.append(
            real_invocation_blocker(
                "materialized_change_mismatch",
                "current repo state could not be inspected for real executor closeout",
                error=str(exc),
            )
        )
    else:
        current_branch_refs, branch_refs_blocker = _closeout_local_branch_refs(cwd)
        if branch_refs_blocker is not None:
            blockers.append(branch_refs_blocker)
        else:
            current_repo["local_branch_refs"] = current_branch_refs
        for field in ("branch", "head", "dirty_worktree", "local_branch_refs"):
            if repository_after.get(field) != current_repo.get(field):
                blockers.append(
                    real_invocation_blocker(
                        "materialized_change_mismatch",
                        f"current repo {field} does not match real executor invocation repository_after.{field}",
                        expected=repository_after.get(field),
                        actual=current_repo.get(field),
                    )
                )
                break
    side_effect_mode = invocation.get("side_effect_mode")
    if side_effect_mode == "materialized_changes":
        invocation_materialized = (
            invocation.get("materialized_change_evidence")
            if isinstance(invocation.get("materialized_change_evidence"), dict)
            else {}
        )
        result_materialized = (
            result_evidence.get("materialized_change_evidence")
            if isinstance(result_evidence, dict) and isinstance(result_evidence.get("materialized_change_evidence"), dict)
            else {}
        )
        if (
            invocation_materialized.get("status") != "verified"
            or checksum_json(_materialized_evidence_public_fields(invocation_materialized)) != checksum_json(result_materialized)
        ):
            blockers.append(
                real_invocation_blocker(
                    "materialized_change_mismatch",
                    "real executor invocation materialized-change evidence does not match result evidence",
                )
            )
        materialized_files = invocation_materialized.get("files")
        if isinstance(materialized_files, list) and all(isinstance(path, str) and path.strip() for path in materialized_files):
            current_dirty_files, dirty_files_blocker = _closeout_dirty_files(cwd)
            if dirty_files_blocker is not None:
                blockers.append(dirty_files_blocker)
            elif current_dirty_files is not None:
                normalized_materialized_files = {str(path).replace("\\", "/") for path in materialized_files}
                if normalized_materialized_files != current_dirty_files:
                    blockers.append(
                        real_invocation_blocker(
                            "materialized_change_mismatch",
                            "materialized change evidence files must match the current dirty worktree",
                            expected_files=sorted(normalized_materialized_files),
                            actual_files=sorted(current_dirty_files),
                        )
                    )
                elif invocation_materialized.get("worktree_fingerprint_schema_version") != DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION:
                    blockers.append(
                        real_invocation_blocker(
                            "materialized_change_mismatch",
                            "real executor invocation materialized-change evidence is missing a dirty-worktree fingerprint",
                            expected_schema=DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
                            actual_schema=invocation_materialized.get("worktree_fingerprint_schema_version"),
                        )
                    )
                else:
                    fingerprint, fingerprint_blocker = _closeout_dirty_fingerprint(cwd, current_dirty_files)
                    if fingerprint_blocker is not None:
                        blockers.append(fingerprint_blocker)
                    elif invocation_materialized.get("worktree_fingerprint_checksum") != checksum_json(fingerprint):
                        blockers.append(
                            real_invocation_blocker(
                                "materialized_change_mismatch",
                                "current dirty worktree fingerprint does not match the real executor invocation",
                                expected=invocation_materialized.get("worktree_fingerprint_checksum"),
                                actual=checksum_json(fingerprint),
                            )
                        )

    audit_chain = invocation.get("audit_chain") if isinstance(invocation.get("audit_chain"), dict) else {}
    audit_replay = replay_audit_log(root)
    if audit_replay.get("valid") is not True:
        blockers.append(
            real_invocation_blocker(
                "audit_chain_mismatch",
                "current audit chain is not valid for real executor closeout",
                invocation_chain_head=audit_chain.get("chain_head"),
                current_chain_head=audit_replay.get("chain_head"),
            )
        )
    else:
        blockers.extend(_closeout_real_invocation_audit_blockers(root, invocation, invocation_file))

    return (invocation if isinstance(invocation_id, str) and invocation_id.strip() else None), blockers


def validate_executor_result_command(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    result_file = Path(args.result_file)
    task_packet = read_json(task_file)
    result_evidence = read_json(result_file)
    if getattr(args, "root", None) is not None:
        repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
        repo_path = repo.get("path")
        if isinstance(repo_path, str) and repo_path and not args.allow_repo_local_root:
            issue = runtime_root_safety_issue(args.root, repo_path)
            if issue:
                raise ValueError(issue)
    payload = build_executor_result_validation_payload(
        root=getattr(args, "root", None),
        task_file=task_file,
        result_file=result_file,
        task_packet=task_packet,
        result_evidence=result_evidence,
        executor_started=False,
    )
    if getattr(args, "root", None) is not None:
        payload["audit_record"] = append_audit_record(
            args.root,
            executor_result_validation_audit_record(payload, task_packet, result_evidence),
        )
    emit(payload)
    return 0 if payload["valid"] else 1


def closeout_executor_result_command(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    result_file = Path(args.result_file)
    snapshot_after_file = Path(args.snapshot_after_file)
    run_record_arg = getattr(args, "run_record_file", None)
    run_record_file = Path(run_record_arg) if run_record_arg else None
    real_invocation_arg = getattr(args, "real_invocation_file", None)
    real_invocation_file = Path(real_invocation_arg) if real_invocation_arg else None
    task_packet = read_json(task_file)
    result_evidence = read_json(result_file)
    snapshot_after = read_json(snapshot_after_file)
    run_record = None
    run_record_blockers: list[dict[str, Any]] = []
    if run_record_file is not None:
        run_record, run_record_blockers = load_closeout_run_record(args.root, run_record_file)
    repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
    repo_path = repo.get("path")
    if isinstance(repo_path, str) and repo_path and not args.allow_repo_local_root:
        issue = runtime_root_safety_issue(args.root, repo_path)
        if issue:
            raise ValueError(issue)

    real_invocation = None
    real_invocation_blockers: list[dict[str, Any]] = []
    if real_invocation_file is not None:
        real_invocation, real_invocation_blockers = load_closeout_real_invocation(args.root, real_invocation_file)

    run_record_started = isinstance(run_record, dict) and run_record.get("executor_started") is True
    real_invocation_started = isinstance(real_invocation, dict) and real_invocation.get("executor_started") is True
    validation_executor_started = run_record_started or real_invocation_started
    validation_invocation_id = None
    if isinstance(run_record, dict) and isinstance(run_record.get("invocation_id"), str):
        validation_invocation_id = run_record.get("invocation_id")
    elif isinstance(real_invocation, dict) and isinstance(real_invocation.get("invocation_id"), str):
        validation_invocation_id = real_invocation.get("invocation_id")
    allow_materialized_result = (
        isinstance(real_invocation, dict)
        and real_invocation.get("side_effect_mode") == "materialized_changes"
    )
    validation = build_executor_result_validation_payload(
        root=args.root,
        task_file=task_file,
        result_file=result_file,
        task_packet=task_packet,
        result_evidence=result_evidence,
        executor_started=validation_executor_started,
        invocation_id=validation_invocation_id if isinstance(validation_invocation_id, str) else None,
        allow_succeeded_dirty_worktree=allow_materialized_result,
    )
    valid_real_invocation = None
    if real_invocation_file is not None:
        valid_real_invocation, validation_real_invocation_blockers = validate_closeout_real_invocation(
            args.root,
            real_invocation,
            invocation_file=real_invocation_file,
            epoch_id=args.epoch_id,
            task_file=task_file,
            result_file=result_file,
            task_packet=task_packet,
            result_evidence=result_evidence,
            snapshot_after=snapshot_after,
            validation=validation,
            cwd=Path(repo_path) if isinstance(repo_path, str) and repo_path else Path(args.cwd),
        )
        real_invocation_blockers.extend(validation_real_invocation_blockers)
    result_status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    required_body_sections = list(args.required_body_section or [])
    template_sections: list[str] | None = None
    policy = None

    def validate_terminal_git_pr_plan_inputs() -> None:
        nonlocal template_sections, policy
        if args.pr_template_file and template_sections is None:
            template_sections = load_template_sections(Path(args.pr_template_file))
        policy_file = getattr(args, "policy_file", None)
        if policy_file and policy is None:
            policy = load_loop_policy(policy_file)

    closeout = closeout_executor_result_epoch(
        args.root,
        epoch_id_value=args.epoch_id,
        task_packet=task_packet,
        result_evidence=result_evidence,
        validation=validation,
        task_file=str(task_file),
        result_file=str(result_file),
        snapshot_after=snapshot_after,
        run_record=run_record,
        run_record_blockers=[*run_record_blockers, *real_invocation_blockers],
        before_terminal_complete=validate_terminal_git_pr_plan_inputs if args.emit_git_pr_plan else None,
    )
    git_pr_plan_packet = None
    next_decision = dict(closeout["next_decision"])
    if real_invocation_blockers and closeout["closeout_status"] == "blocked":
        next_decision = {
            "decision": "operator_review",
            "recommended_next_action": "inspect_real_run_blockers",
            "reason": "real executor invocation evidence cannot be bound to closeout",
        }
    if args.emit_git_pr_plan and closeout["closeout_status"] == "completed":
        if args.pr_template_file:
            required_body_sections.extend(
                template_sections if template_sections is not None else load_template_sections(Path(args.pr_template_file))
            )
        git_pr_plan_packet = evaluate_git_pr_plan(
            cwd=Path(args.cwd),
            task_packet=task_packet,
            result_evidence=result_evidence,
            task_file=task_file,
            result_file=result_file,
            base_branch=args.base_branch,
            branch_prefix=args.branch_prefix,
            branch_policy=policy["branch_policy"] if policy else None,
            required_body_sections=required_body_sections,
            runtime_root=args.root,
        )
        next_decision["recommended_next_action"] = git_pr_plan_packet["recommended_next_action"]
        next_decision["git_pr_plan_ready"] = git_pr_plan_packet["ready_to_review"]
    side_effects = list(closeout["side_effects"])
    append_closeout_audit = closeout["closeout_status"] != "already_closed"
    run_record_ref = None
    if run_record_file is not None and isinstance(run_record, dict):
        run_record_ref = {
            "path": str(run_record_file),
            "run_id": run_record.get("run_id"),
            "invocation_id": run_record.get("invocation_id"),
            "before_checksum": checksum_json(run_record),
            "closeout_status": run_record.get("closeout_status"),
        }
        if closeout["valid"]:
            side_effects.extend(["execution_run_record_updated", "execution_run_audit_appended"])
    real_invocation_ref = None
    if real_invocation_file is not None and isinstance(real_invocation, dict):
        real_invocation_ref = {
            "path": str(real_invocation_file),
            "invocation_id": real_invocation.get("invocation_id"),
            "before_checksum": checksum_json(real_invocation),
            "closeout_status": real_invocation.get("closeout_status"),
        }
        if closeout["valid"]:
            side_effects.extend(["real_executor_invocation_record_updated", "real_executor_invocation_audit_appended"])
    if append_closeout_audit:
        side_effects.append("audit_record_appended")
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EXECUTOR_EPOCH_CLOSEOUT_SCHEMA_VERSION,
        "packet": "executor_epoch_closeout",
        "valid": closeout["valid"],
        "reason": closeout["reason"],
        "epoch_id": args.epoch_id,
        "epoch_status": closeout["epoch_status"],
        "closeout_status": closeout["closeout_status"],
        "failure_reason": closeout["failure_reason"],
        "blockers": closeout["blockers"],
        "task_file": str(task_file),
        "task_checksum": checksum_json(task_packet),
        "result_file": str(result_file),
        "snapshot_after_file": str(snapshot_after_file),
        "snapshot_after_checksum": checksum_epoch_json(snapshot_after),
        "executor_result_status": result_status,
        "executor_started": validation_executor_started,
        "pr_action_started": False,
        "operator_confirmation_required": next_decision["decision"] in {"generate_git_pr_plan", "handoff"},
        "validation": validation,
        "next_decision": next_decision,
        "git_pr_plan": git_pr_plan_packet,
        "side_effects": side_effects,
        "limitations": [
            "local_packets_only",
            "does_not_start_executor",
            "does_not_execute_git_commands",
            "does_not_call_github",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
        ],
    }
    if run_record_ref is not None:
        payload["run_record"] = run_record_ref
    if real_invocation_ref is not None:
        payload["real_invocation"] = real_invocation_ref
    if payload["failure_reason"] is None:
        payload.pop("failure_reason")
    run_record_audit = None
    if run_record_file is not None and isinstance(run_record, dict) and closeout["valid"]:
        closeout_core_packet = {
            key: value
            for key, value in payload.items()
            if key not in {"audit_record", "run_record"}
        }
        epoch_closeout_checksum = checksum_json(closeout_core_packet)
        updated_run_record = dict(run_record)
        updated_run_record.update(
            {
                "closeout_status": closeout["closeout_status"],
                "epoch_id": args.epoch_id,
                "epoch_status": closeout["epoch_status"],
                "epoch_closeout_checksum": epoch_closeout_checksum,
                "updated_at": utc_now(),
            }
        )
        atomic_write_json(run_record_file, updated_run_record)
        try:
            run_record_audit = append_audit_record(
                args.root,
                execution_run_record_audit_record(
                    updated_run_record,
                    run_record_file=str(run_record_file),
                    action="update_execution_run_closeout",
                    reason="execution run closeout status updated",
                ),
            )
        except Exception as exc:
            rollback_restored = False
            rollback_error = None
            try:
                atomic_write_json(run_record_file, run_record)
                rollback_restored = True
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
            current_run_record = run_record if rollback_restored else updated_run_record
            payload["valid"] = False
            payload["reason"] = "execution run closeout audit record could not be written"
            payload["blockers"] = [
                *payload["blockers"],
                {
                    "code": "run_record_audit_append_failed",
                    "message": "execution run closeout audit record could not be written",
                    "error": str(exc),
                },
            ]
            payload["next_decision"] = {
                "decision": "operator_review",
                "recommended_next_action": "recover_closeout_audit",
                "reason": "execution run closeout audit append failed after epoch closeout",
            }
            payload["operator_confirmation_required"] = True
            payload["side_effects"] = [
                effect
                for effect in payload["side_effects"]
                if effect
                not in {
                    "execution_run_record_updated",
                    "execution_run_audit_appended",
                    "audit_record_appended",
                }
            ]
            payload["side_effects"].append("execution_run_audit_append_failed")
            rollback_side_effect = (
                "execution_run_record_update_rolled_back"
                if rollback_restored
                else "execution_run_record_update_unreconciled"
            )
            payload["side_effects"].append(rollback_side_effect)
            payload["run_record"].update(
                {
                    "after_checksum": checksum_json(current_run_record),
                    "closeout_status": current_run_record.get("closeout_status"),
                    "epoch_closeout_checksum": epoch_closeout_checksum,
                    "audit_record_error": str(exc),
                    "rollback_record_restored": rollback_restored,
                }
            )
            if rollback_error is not None:
                payload["run_record"]["rollback_error"] = rollback_error
            emit(payload)
            return 2
        payload["run_record"].update(
            {
                "after_checksum": checksum_json(updated_run_record),
                "closeout_status": updated_run_record.get("closeout_status"),
                "epoch_closeout_checksum": epoch_closeout_checksum,
                "audit_record": run_record_audit,
            }
        )
    if real_invocation_file is not None and valid_real_invocation is not None and closeout["valid"]:
        closeout_core_packet = {
            key: value
            for key, value in payload.items()
            if key not in {"audit_record", "run_record", "real_invocation"}
        }
        epoch_closeout_checksum = checksum_json(closeout_core_packet)
        updated_invocation = dict(valid_real_invocation)
        updated_invocation.update(
            {
                "closeout_status": closeout["closeout_status"],
                "epoch_id": args.epoch_id,
                "epoch_status": closeout["epoch_status"],
                "epoch_closeout_checksum": epoch_closeout_checksum,
                "task_file": str(task_file),
                "result_evidence_checksum": checksum_json(result_evidence),
                "validation_packet_checksum": checksum_json(validation),
                "snapshot_after_checksum": checksum_epoch_json(snapshot_after),
                "updated_at": utc_now(),
            }
        )
        atomic_write_json(real_invocation_file, updated_invocation)
        try:
            invocation_update_audit = append_audit_record(
                args.root,
                real_executor_invocation_audit_record(
                    updated_invocation,
                    invocation_record_file=str(real_invocation_file),
                    action="update_real_executor_invocation_closeout",
                    reason="real executor invocation closeout status updated",
                ),
            )
        except Exception as exc:
            rollback_restored = False
            rollback_error = None
            try:
                atomic_write_json(real_invocation_file, valid_real_invocation)
                rollback_restored = True
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
            current_invocation = valid_real_invocation if rollback_restored else updated_invocation
            payload["valid"] = False
            payload["reason"] = "real executor invocation closeout audit record could not be written"
            payload["blockers"] = [
                *payload["blockers"],
                {
                    "code": "real_invocation_audit_append_failed",
                    "message": "real executor invocation closeout audit record could not be written",
                    "error": str(exc),
                },
            ]
            payload["next_decision"] = {
                "decision": "operator_review",
                "recommended_next_action": "recover_closeout_audit",
                "reason": "real executor invocation closeout audit append failed after epoch closeout",
            }
            payload["operator_confirmation_required"] = True
            payload["side_effects"] = [
                effect
                for effect in payload["side_effects"]
                if effect
                not in {
                    "real_executor_invocation_record_updated",
                    "real_executor_invocation_audit_appended",
                    "audit_record_appended",
                }
            ]
            payload["side_effects"].append("real_executor_invocation_audit_append_failed")
            payload["side_effects"].append(
                "real_executor_invocation_record_update_rolled_back"
                if rollback_restored
                else "real_executor_invocation_record_update_unreconciled"
            )
            payload["real_invocation"].update(
                {
                    "after_checksum": checksum_json(current_invocation),
                    "closeout_status": current_invocation.get("closeout_status"),
                    "epoch_closeout_checksum": epoch_closeout_checksum,
                    "audit_record_error": str(exc),
                    "rollback_record_restored": rollback_restored,
                }
            )
            if rollback_error is not None:
                payload["real_invocation"]["rollback_error"] = rollback_error
            emit(payload)
            return 2
        payload["real_invocation"].update(
            {
                "after_checksum": checksum_json(updated_invocation),
                "closeout_status": updated_invocation.get("closeout_status"),
                "epoch_closeout_checksum": epoch_closeout_checksum,
                "audit_record": invocation_update_audit,
            }
        )
    if append_closeout_audit:
        try:
            payload["audit_record"] = append_audit_record(
                args.root,
                executor_epoch_closeout_audit_record(payload, task_packet, result_evidence),
            )
        except Exception as exc:
            payload["valid"] = False
            payload["reason"] = "executor epoch closeout audit record could not be written"
            payload["blockers"] = [
                *payload["blockers"],
                {
                    "code": "closeout_audit_append_failed",
                    "message": "executor epoch closeout audit record could not be written",
                    "error": str(exc),
                },
            ]
            payload["next_decision"] = {
                "decision": "operator_review",
                "recommended_next_action": "recover_closeout_audit",
                "reason": "executor epoch closeout audit append failed after local closeout state changed",
            }
            payload["operator_confirmation_required"] = True
            payload["side_effects"] = [
                effect for effect in payload["side_effects"] if effect != "audit_record_appended"
            ]
            payload["side_effects"].append("closeout_audit_append_failed")
            emit(payload)
            return 2
    emit(payload)
    return 0 if payload["valid"] else 1


def run_controlled_executor_fixture_command(args: argparse.Namespace) -> int:
    payload = run_controlled_executor_fixture(
        root=args.root,
        task_file=args.task_file,
        command_template=args.command_template,
        timeout_seconds=args.timeout_seconds,
        allow_repo_local_root=args.allow_repo_local_root,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def audit_replay_command(args: argparse.Namespace) -> int:
    """Emit a read-only audit replay packet for the runtime root."""
    payload = replay_audit_log(args.root)
    emit(payload)
    return 0 if payload["valid"] else 1


def pr_readiness_command(args: argparse.Namespace) -> int:
    pr_json_file = Path(args.pr_json_file)
    pr = load_pr_json(pr_json_file)
    review_threads = read_json(Path(args.review_threads_file)) if args.review_threads_file else None
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    evidence_captured_at = datetime.fromtimestamp(pr_json_file.stat().st_mtime, timezone.utc)
    payload = evaluate_pr_readiness(
        pr,
        required_checks=args.required_check or [],
        required_body_sections=required_body_sections,
        review_threads=review_threads,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0


def review_response_plan_command(args: argparse.Namespace) -> int:
    pr_json_file = Path(args.pr_json_file)
    pr = load_pr_json(pr_json_file)
    review_threads = read_json(Path(args.review_threads_file)) if args.review_threads_file else None
    candidate_discovery = read_json(Path(args.candidate_discovery_file)) if args.candidate_discovery_file else None
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    evidence_captured_at = datetime.fromtimestamp(pr_json_file.stat().st_mtime, timezone.utc)
    payload = evaluate_review_response_plan(
        pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        required_body_sections=required_body_sections,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def review_response_materialization_plan_command(args: argparse.Namespace) -> int:
    pr_json_file = Path(args.pr_json_file)
    pr = load_pr_json(pr_json_file)
    response_plan = read_json(Path(args.response_plan_file))
    review_threads = read_json(Path(args.review_threads_file)) if args.review_threads_file else None
    candidate_discovery = read_json(Path(args.candidate_discovery_file)) if args.candidate_discovery_file else None
    write_packet = read_json(Path(args.write_file))
    intended_writes = write_packet.get("writes") if isinstance(write_packet, dict) else write_packet
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    evidence_captured_at = datetime.fromtimestamp(pr_json_file.stat().st_mtime, timezone.utc)
    payload = evaluate_review_response_materialization_plan(
        response_plan,
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        intended_writes=intended_writes,
        required_body_sections=required_body_sections,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def review_response_materialize_command(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan_file)
    pr_json_file = Path(args.pr_json_file)
    plan_packet = read_json(plan_file)
    pr = load_pr_json(pr_json_file)
    review_threads = read_json(Path(args.review_threads_file)) if args.review_threads_file else None
    candidate_discovery = read_json(Path(args.candidate_discovery_file)) if args.candidate_discovery_file else None
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    evidence_captured_at = datetime.fromtimestamp(pr_json_file.stat().st_mtime, timezone.utc)
    payload = materialize_review_response_plan(
        cwd=Path(args.cwd),
        plan_packet=plan_packet,
        plan_file=plan_file,
        approval_token=args.approval_token,
        runtime_root=args.root,
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        required_body_sections=required_body_sections or None,
        pr_evidence_captured_at=evidence_captured_at,
        max_pr_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def review_thread_resolution_plan_command(args: argparse.Namespace) -> int:
    pr_json_file = Path(args.pr_json_file)
    pr = load_pr_json(pr_json_file)
    review_threads = read_json(Path(args.review_threads_file))
    response_materialization = read_json(Path(args.response_materialization_file))
    post_write_gate = read_json(Path(args.post_write_gate_file))
    gate_refresh = (
        post_write_gate.get("refresh")
        if isinstance(post_write_gate, dict) and isinstance(post_write_gate.get("refresh"), dict)
        else {}
    )
    evidence_captured_at = gate_refresh.get("captured_at") or datetime.fromtimestamp(
        pr_json_file.stat().st_mtime, timezone.utc
    )
    payload = evaluate_review_thread_resolution_plan(
        pr=pr,
        review_threads=review_threads,
        response_materialization=response_materialization,
        post_write_gate=post_write_gate,
        target_thread_ids=args.thread_id or [],
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def review_thread_resolution_materialize_command(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan_file)
    pr_json_file = Path(args.pr_json_file)
    plan_packet = read_json(plan_file)
    pr = load_pr_json(pr_json_file)
    review_threads = read_json(Path(args.review_threads_file))
    response_materialization = read_json(Path(args.response_materialization_file))
    post_write_gate = read_json(Path(args.post_write_gate_file))
    gate_refresh = (
        post_write_gate.get("refresh")
        if isinstance(post_write_gate, dict) and isinstance(post_write_gate.get("refresh"), dict)
        else {}
    )
    evidence_captured_at = gate_refresh.get("captured_at") or datetime.fromtimestamp(
        pr_json_file.stat().st_mtime, timezone.utc
    )
    payload = materialize_review_thread_resolution_plan(
        cwd=Path(args.cwd),
        plan_packet=plan_packet,
        plan_file=plan_file,
        approval_token=args.approval_token,
        runtime_root=args.root,
        pr=pr,
        review_threads=review_threads,
        response_materialization=response_materialization,
        post_write_gate=post_write_gate,
        pr_evidence_captured_at=evidence_captured_at,
        max_pr_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def role_readiness_command(args: argparse.Namespace) -> int:
    payload = evaluate_role_readiness(
        root=args.root,
        cwd=Path(args.cwd),
        role_policy_file=Path(args.role_policy_file) if args.role_policy_file else None,
        pr_json_file=Path(args.pr_json_file) if args.pr_json_file else None,
        review_threads_file=Path(args.review_threads_file) if args.review_threads_file else None,
        repo=args.repo,
        branch=args.branch,
        task_id=args.task_id,
        max_ownership_age_minutes=args.max_ownership_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 2


def executor_invocation_readiness_command(args: argparse.Namespace) -> int:
    payload = evaluate_executor_invocation_readiness(
        root=args.root,
        cwd=Path(args.cwd),
        task_file=Path(args.task_file),
        epoch_id=args.epoch_id,
        ownership_target=args.ownership_target,
        expected_result_path=args.expected_result_path,
        role_readiness_file=Path(args.role_readiness_file) if args.role_readiness_file else None,
        max_ownership_age_minutes=args.max_ownership_age_minutes,
    )
    emit(payload)
    return 0 if payload["valid"] else 2


def executor_invocation_plan_command(args: argparse.Namespace) -> int:
    payload = build_executor_invocation_plan(
        root=args.root,
        cwd=Path(args.cwd),
        readiness_file=Path(args.readiness_file),
        approval_file=Path(args.approval_file),
        approval_secret=operator_approval_secret_from_args(args),
        adapter_file=Path(args.adapter_file),
        rollback_file=Path(args.rollback_file),
        command=args.command,
        environment_allowlist=list(args.env_allow or []),
        timeout_seconds=args.timeout_seconds,
        expected_result_path=args.expected_result_path,
    )
    emit(payload)
    return 0 if payload["valid"] else 2


def invoke_real_executor_command(args: argparse.Namespace) -> int:
    payload = invoke_real_executor(
        root=args.root,
        plan_file=Path(args.plan_file),
        approval_secret=operator_approval_secret_from_args(args),
        side_effect_mode=args.side_effect_mode,
        allow_repo_local_root=args.allow_repo_local_root,
        max_plan_age_seconds=args.max_plan_age_minutes * 60,
    )
    record_file = payload.get("record_file") if isinstance(payload, dict) else None
    if isinstance(record_file, str) and record_file.strip():
        try:
            append_audit_record(
                args.root,
                real_executor_invocation_audit_record(
                    payload,
                    invocation_record_file=record_file,
                    action="record_real_executor_invocation",
                    reason="real executor invocation record written",
                ),
            )
        except Exception as exc:
            payload = dict(payload)
            payload["valid"] = False
            payload["closeout_status"] = "blocked"
            payload.setdefault("blockers", []).append(
                {
                    "code": "audit_append_failed",
                    "message": "real executor invocation audit record could not be written",
                    "error": str(exc),
                }
            )
            payload["recommended_next_action"] = "inspect_runtime_state"
            payload["reason"] = "real executor invocation blocked because audit record append failed"
            payload.setdefault("side_effects", []).append("real_executor_invocation_audit_append_failed")
            blocked_record_written = False
            try:
                atomic_write_json(Path(record_file), payload)
                blocked_record_written = True
            except Exception as write_exc:
                payload["blockers"].append(
                    {
                        "code": "invocation_record_write_failed",
                        "message": "blocked real executor invocation record could not be written",
                        "error": str(write_exc),
                    }
                )
            if blocked_record_written:
                try:
                    append_audit_record(
                        args.root,
                        real_executor_invocation_audit_record(
                            payload,
                            invocation_record_file=record_file,
                            action="record_real_executor_invocation_blocked",
                            reason="real executor invocation blocked due to audit write failure",
                        ),
                    )
                except Exception as blocked_audit_exc:
                    payload["audit_record_error"] = str(blocked_audit_exc)
            emit(payload)
            return 2
    emit(payload)
    return 0 if payload["valid"] else 2


def github_evidence_out_dir_safety_issue(out_dir: Path) -> str | None:
    target = out_dir.expanduser().resolve(strict=False)
    cwd_repo_root = git_repo_root(Path.cwd())
    if cwd_repo_root is not None and path_is_relative_to(target, cwd_repo_root):
        return "github evidence out-dir is inside a git worktree; choose a runtime-owned directory outside the repository"

    probe = target if target.exists() else target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    out_dir_repo_root = git_repo_root(probe)
    if out_dir_repo_root is not None and path_is_relative_to(target, out_dir_repo_root):
        return "github evidence out-dir is inside a git worktree; choose a runtime-owned directory outside the repository"
    return None


def github_evidence_sync_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    issue = github_evidence_out_dir_safety_issue(out_dir)
    if issue:
        raise ValueError(issue)
    payload = sync_github_evidence(
        repo=args.repo,
        pr_number=args.pr_number,
        out_dir=out_dir,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def post_write_pr_evidence_gate_command(args: argparse.Namespace) -> int:
    materialization_file = Path(args.materialization_file)
    github_evidence_file = Path(args.github_evidence_file)
    materialization_result = read_json(materialization_file)
    github_evidence_sync = read_json(github_evidence_file)
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    payload = evaluate_post_write_pr_evidence_gate(
        cwd=Path(args.cwd),
        materialization_result=materialization_result,
        github_evidence_sync=github_evidence_sync,
        github_evidence_file=github_evidence_file,
        required_checks=args.required_check or [],
        required_body_sections=required_body_sections,
        max_pr_evidence_age_minutes=args.max_pr_json_age_minutes,
        discovery_mode=args.discovery_mode,
        proposal_allowance=args.proposal_allowance,
        max_tasks=args.max_tasks,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def controlled_pr_cycle_command(args: argparse.Namespace) -> int:
    payload = compose_controlled_pr_cycle_from_files(
        root=args.root,
        controlled_loop_tick_file=Path(args.controlled_loop_tick_file),
        git_pr_materialization_file=Path(args.git_pr_materialization_file),
        initial_post_write_gate_file=Path(args.initial_post_write_gate_file),
        review_response_materialization_file=Path(args.review_response_materialization_file)
        if args.review_response_materialization_file
        else None,
        review_response_post_write_gate_file=Path(args.review_response_post_write_gate_file)
        if args.review_response_post_write_gate_file
        else None,
        review_thread_resolution_materialization_file=Path(args.review_thread_resolution_materialization_file)
        if args.review_thread_resolution_materialization_file
        else None,
        review_thread_resolution_post_write_gate_file=Path(args.review_thread_resolution_post_write_gate_file)
        if args.review_thread_resolution_post_write_gate_file
        else None,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def pr_body_preflight_command(args: argparse.Namespace) -> int:
    body = load_pr_body(Path(args.body_file))
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    payload = evaluate_pr_body_preflight(
        body,
        required_body_sections=required_body_sections,
    )
    emit(payload)
    return 0


def git_pr_plan_command(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    result_file = Path(args.result_file)
    task_packet = read_json(task_file)
    result_evidence = read_json(result_file)
    if getattr(args, "root", None) is not None:
        repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
        repo_path = repo.get("path")
        if isinstance(repo_path, str) and repo_path and not args.allow_repo_local_root:
            issue = runtime_root_safety_issue(args.root, repo_path)
            if issue:
                raise ValueError(issue)
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    policy = load_loop_policy(args.policy_file) if args.policy_file else None
    payload = evaluate_git_pr_plan(
        cwd=Path(args.cwd),
        task_packet=task_packet,
        result_evidence=result_evidence,
        task_file=task_file,
        result_file=result_file,
        base_branch=args.base_branch,
        branch_prefix=args.branch_prefix,
        branch_policy=policy["branch_policy"] if policy else None,
        required_body_sections=required_body_sections,
        runtime_root=args.root,
    )
    emit(payload)
    return 0


def git_pr_dirty_materialization_plan_command(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    result_file = Path(args.result_file)
    real_invocation_file = Path(args.real_invocation_file)
    closeout_file = Path(args.closeout_file)
    task_packet = read_json(task_file)
    result_evidence = read_json(result_file)
    real_invocation = read_json(real_invocation_file)
    closeout_packet = read_json(closeout_file)
    if getattr(args, "root", None) is not None:
        repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
        repo_path = repo.get("path")
        if isinstance(repo_path, str) and repo_path and not args.allow_repo_local_root:
            issue = runtime_root_safety_issue(args.root, repo_path)
            if issue:
                raise ValueError(issue)
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    policy = load_loop_policy(args.policy_file) if args.policy_file else None
    payload = evaluate_dirty_git_pr_materialization_plan(
        cwd=Path(args.cwd),
        task_packet=task_packet,
        result_evidence=result_evidence,
        real_invocation=real_invocation,
        closeout_packet=closeout_packet,
        task_file=task_file,
        result_file=result_file,
        real_invocation_file=real_invocation_file,
        closeout_file=closeout_file,
        base_branch=args.base_branch,
        branch_prefix=args.branch_prefix,
        branch_policy=policy["branch_policy"] if policy else None,
        required_body_sections=required_body_sections,
        remote=args.remote,
        pr_number=str(args.pr_number) if args.pr_number is not None else None,
        expected_base_head=args.expected_base_head,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def git_pr_materialize_command(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan_file)
    try:
        plan_packet = read_json(plan_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload = git_pr_materialization_load_error_packet(plan_file, exc)
        emit(payload)
        return 1
    pr_evidence = None
    pr_evidence_path = None
    pr_evidence_captured_at = None
    if args.pr_json_file:
        pr_evidence_path = Path(args.pr_json_file)
        try:
            pr_evidence = load_pr_json(pr_evidence_path)
            pr_evidence_captured_at = datetime.fromtimestamp(pr_evidence_path.stat().st_mtime, timezone.utc)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = git_pr_materialization_pr_evidence_load_error_packet(
                plan_packet=plan_packet,
                plan_file=plan_file,
                pr_json_file=pr_evidence_path,
                error=exc,
                remote=args.remote,
                pr_number=str(args.pr_number) if args.pr_number is not None else None,
            )
            emit(payload)
            return 1
    dirty_commit_materialization = None
    dirty_commit_materialization_path = None
    if args.dirty_commit_materialization_file:
        dirty_commit_materialization_path = Path(args.dirty_commit_materialization_file)
        try:
            dirty_commit_materialization = read_json(dirty_commit_materialization_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            payload = git_pr_materialization_dirty_commit_load_error_packet(
                plan_packet=plan_packet,
                plan_file=plan_file,
                dirty_commit_file=dirty_commit_materialization_path,
                error=exc,
                remote=args.remote,
                pr_number=str(args.pr_number) if args.pr_number is not None else None,
            )
            emit(payload)
            return 1
    payload = materialize_git_pr_plan(
        cwd=Path(args.cwd),
        plan_packet=plan_packet,
        plan_file=plan_file,
        approval_token=args.approval_token,
        runtime_root=args.root,
        remote=args.remote,
        pr_number=str(args.pr_number) if args.pr_number is not None else None,
        pr_evidence=pr_evidence,
        pr_evidence_captured_at=pr_evidence_captured_at,
        max_pr_evidence_age_minutes=args.max_pr_json_age_minutes,
        pr_evidence_path=pr_evidence_path,
        dirty_commit_materialization=dirty_commit_materialization,
        dirty_commit_materialization_path=dirty_commit_materialization_path,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


def git_pr_dirty_commit_materialize_command(args: argparse.Namespace) -> int:
    plan_file = Path(args.plan_file)
    try:
        plan_packet = read_json(plan_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload = git_pr_dirty_commit_materialization_load_error_packet(plan_file, exc)
        emit(payload)
        return 1
    payload = materialize_dirty_commit_plan(
        cwd=Path(args.cwd),
        plan_packet=plan_packet,
        plan_file=plan_file,
        approval_token=args.approval_token,
        runtime_root=args.root,
    )
    emit(payload)
    return 0 if payload["valid"] else 1


OPERATOR_APPROVAL_SECRET_ENV = "CADENCE_OPERATOR_APPROVAL_SECRET"


def operator_approval_secret_from_args(args: argparse.Namespace) -> str | None:
    if args.approval_secret is not None:
        return args.approval_secret
    return os.environ.get(args.approval_secret_env)


def verify_operator_approval_command(args: argparse.Namespace) -> int:
    approval_file = Path(args.approval_file)
    approval_secret = operator_approval_secret_from_args(args)
    try:
        approval_packet = read_json(approval_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        approval_packet = None
        payload = build_operator_approval_verification_packet(
            approval=approval_packet,
            approval_file=approval_file,
            expected_target_checksum=args.target_checksum,
            expected_purpose=args.purpose,
            approval_secret=approval_secret,
        )
        payload["blockers"].insert(
            0,
            {
                "code": "operator_approval_file_unreadable",
                "message": f"operator approval file could not be read: {exc}",
            },
        )
        payload["valid"] = False
        payload["approval_state"] = "blocked"
        payload["signature_verified"] = False
        payload["recommended_next_action"] = "fix_operator_approval"
        payload["reason"] = payload["blockers"][0]["message"]
        emit(payload)
        return 1

    payload = build_operator_approval_verification_packet(
        approval=approval_packet,
        approval_file=approval_file,
        expected_target_checksum=args.target_checksum,
        expected_purpose=args.purpose,
        approval_secret=approval_secret,
    )
    if payload["valid"]:
        payload["side_effects"] = ["operator_approval_audit_appended"]
        try:
            audit_record = append_audit_record(args.root, operator_approval_verification_audit_record(payload))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            payload["valid"] = False
            payload["approval_state"] = "blocked"
            payload["side_effects"] = []
            payload["blockers"].append(
                {
                    "code": "operator_approval_audit_append_failed",
                    "message": f"operator approval verification audit could not be appended: {exc}",
                }
            )
            payload["recommended_next_action"] = "inspect_runtime_state"
            payload["reason"] = payload["blockers"][-1]["message"]
        else:
            payload["audit_record"] = audit_record
    emit(payload)
    return 0 if payload["valid"] else 1


def release_dry_run_command(args: argparse.Namespace) -> int:
    payload = evaluate_release_dry_run(
        cwd=Path(args.cwd),
        version=args.version,
        tag=args.tag,
        target_branch=args.target_branch,
        target_ref=args.target_ref,
    )
    emit(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agentic Cadence")
    parser.add_argument("--root", type=Path, help="Agentic Cadence state root")
    parser.add_argument(
        "--allow-repo-local-root",
        action="store_true",
        help="Allow an unignored runtime root inside the target git repo",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create the runtime layout")
    init_parser.set_defaults(func=lambda args: (ensure_layout(args.root), status(args))[1])

    status_parser = subparsers.add_parser("status", help="Show brake and handoff counts")
    status_parser.set_defaults(func=status)

    set_brake_parser = subparsers.add_parser("set-brake", help="Set NEUTRAL or PARK")
    set_brake_parser.add_argument("status", choices=("NEUTRAL", "PARK"))
    set_brake_parser.add_argument("--reason")
    set_brake_parser.add_argument("--scope", default="global")
    set_brake_parser.add_argument("--resume-requires")
    set_brake_parser.set_defaults(func=set_brake)

    clear_brake_parser = subparsers.add_parser("clear-brake", help="Clear brake back to DRIVE")
    clear_brake_parser.add_argument("--reason")
    clear_brake_parser.add_argument("--scope", default="global")
    clear_brake_parser.set_defaults(func=clear_brake)

    create_parser = subparsers.add_parser("create-handoff", help="Write a ready handoff")
    create_parser.add_argument("--id")
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--guardrail", default="manual")
    create_parser.add_argument("--repo")
    create_parser.add_argument("--branch")
    create_parser.add_argument("--task-type", choices=("execution", "discovery"), required=True)
    create_parser.add_argument("--driver", action="append", default=[])
    create_parser.add_argument("--metadata", action="append", default=[])
    create_parser.add_argument("--message")
    create_parser.add_argument("--message-file")
    create_parser.add_argument("--signature-file")
    create_parser.set_defaults(func=create_handoff)

    plan_parser = subparsers.add_parser("plan-task", help="Estimate a task and return pickup policy")
    plan_parser.add_argument("--title", required=True)
    plan_parser.add_argument("--task-type", choices=("execution", "discovery"), required=True)
    plan_parser.add_argument("--driver", action="append", default=[])
    plan_parser.add_argument("--repo")
    plan_parser.add_argument("--branch")
    plan_parser.add_argument("--message")
    plan_parser.add_argument("--message-file")
    plan_parser.set_defaults(func=plan_task, requires_root=False)

    snapshot_parser = subparsers.add_parser("snapshot-repo", help="Capture repo state for epoch decisions")
    snapshot_parser.add_argument("--cwd", default=".")
    snapshot_parser.add_argument("--repo")
    snapshot_parser.add_argument("--active-pr", type=int)
    snapshot_parser.add_argument("--known-failure", action="append", default=[])
    snapshot_parser.add_argument("--ci-status", choices=("unknown", "green", "red", "pending"), default="unknown")
    snapshot_parser.set_defaults(func=snapshot_repo_command)

    prepare_parser = subparsers.add_parser("prepare-handoff", help="Prepare a validated context handoff and clean-square")
    prepare_parser.add_argument("--id")
    prepare_parser.add_argument("--title", required=True)
    prepare_parser.add_argument("--guardrail", required=True)
    prepare_parser.add_argument("--repo")
    prepare_parser.add_argument("--branch")
    prepare_parser.add_argument("--cwd", default=".")
    prepare_parser.add_argument("--task-type", choices=("execution", "discovery"), required=True)
    prepare_parser.add_argument("--driver", action="append", default=[])
    prepare_parser.add_argument("--summary", required=True)
    prepare_parser.add_argument("--ci-status", choices=("unknown", "green", "red", "pending"), default="unknown")
    prepare_parser.add_argument("--next-action", action="append", default=[])
    prepare_parser.set_defaults(func=prepare_handoff_command)

    discover_parser = subparsers.add_parser("discover-candidates", help="Discover governed repo candidate slices")
    discover_parser.add_argument("--cwd", default=".")
    discover_parser.add_argument("--intent", choices=DISCOVERY_INTENTS)
    discover_parser.add_argument("--interactive", action="store_true")
    discover_parser.add_argument("--discovery-mode", choices=DISCOVERY_MODES, default="local")
    discover_parser.add_argument("--proposal-allowance", choices=PROPOSAL_ALLOWANCES, default="none")
    discover_parser.add_argument("--known-failure", action="append", default=[])
    discover_parser.add_argument("--pr-json-file")
    discover_parser.add_argument("--review-findings-file")
    discover_parser.add_argument("--review-threads-file")
    discover_parser.add_argument("--elect", action="store_true")
    discover_parser.add_argument("--max-tasks", type=int, default=1)
    discover_parser.add_argument("--max-candidates", type=int, default=25)
    discover_parser.add_argument("--max-candidates-per-source", type=int, default=10)
    discover_parser.add_argument("--max-text-marker-candidates", type=int, default=10)
    discover_parser.add_argument("--max-doc-marker-candidates", type=int, default=5)
    discover_parser.add_argument("--max-business-memory-candidates", type=int, default=5)
    discover_parser.add_argument("--max-proposals", type=int, default=3)
    discover_parser.add_argument("--max-product-evolution-candidates-in-hybrid", type=int, default=1)
    discover_parser.set_defaults(func=discover_candidates_command, requires_root=False)

    loop_tick_parser = subparsers.add_parser("loop-tick", help="Run one read-only governed loop tick")
    loop_tick_parser.add_argument("--cwd", default=".")
    loop_tick_parser.add_argument("--repo")
    loop_tick_parser.add_argument("--active-pr", type=int)
    loop_tick_parser.add_argument("--ci-status", choices=("unknown", "green", "red", "pending"), default="unknown")
    loop_tick_parser.add_argument("--intent", choices=DISCOVERY_INTENTS)
    loop_tick_parser.add_argument("--discovery-mode", choices=DISCOVERY_MODES, default="local")
    loop_tick_parser.add_argument("--proposal-allowance", choices=PROPOSAL_ALLOWANCES, default="none")
    loop_tick_parser.add_argument("--known-failure", action="append", default=[])
    loop_tick_parser.add_argument("--pr-json-file")
    loop_tick_parser.add_argument("--review-findings-file")
    loop_tick_parser.add_argument("--review-threads-file")
    loop_tick_parser.add_argument("--max-tasks", type=int, default=1)
    loop_tick_parser.add_argument("--max-candidates", type=int, default=25)
    loop_tick_parser.add_argument("--max-candidates-per-source", type=int, default=10)
    loop_tick_parser.add_argument("--max-text-marker-candidates", type=int, default=10)
    loop_tick_parser.add_argument("--max-doc-marker-candidates", type=int, default=5)
    loop_tick_parser.add_argument("--max-business-memory-candidates", type=int, default=5)
    loop_tick_parser.add_argument("--max-proposals", type=int, default=3)
    loop_tick_parser.add_argument("--max-product-evolution-candidates-in-hybrid", type=int, default=1)
    loop_tick_parser.add_argument("--emit-executor-task", action="store_true")
    loop_tick_parser.add_argument("--allowed-path", action="append", default=[])
    loop_tick_parser.add_argument("--required-check", action="append", default=[])
    loop_tick_parser.add_argument("--executor-time-limit-minutes", type=positive_int)
    loop_tick_parser.add_argument("--executor-evidence-path")
    loop_tick_parser.add_argument("--stop-condition", action="append", default=[])
    loop_tick_parser.add_argument("--policy-file")
    loop_tick_parser.set_defaults(func=loop_tick_command)

    controlled_loop_tick_parser = subparsers.add_parser(
        "controlled-loop-tick",
        help="Compose existing local evidence into one controlled loop tick packet",
    )
    controlled_loop_tick_parser.add_argument("--loop-tick-file", required=True)
    controlled_loop_tick_parser.add_argument("--task-file", required=True)
    controlled_loop_tick_parser.add_argument("--execution-start-file", required=True)
    controlled_loop_tick_parser.add_argument("--readiness-file", required=True)
    controlled_loop_tick_parser.add_argument("--invocation-plan-file", required=True)
    controlled_loop_tick_parser.add_argument("--real-invocation-file", required=True)
    controlled_loop_tick_parser.add_argument("--result-file", required=True)
    controlled_loop_tick_parser.add_argument("--snapshot-after-file", required=True)
    controlled_loop_tick_parser.add_argument("--closeout-file", required=True)
    controlled_loop_tick_parser.add_argument("--git-pr-plan-file")
    controlled_loop_tick_parser.set_defaults(
        func=controlled_loop_tick_command,
        requires_root=True,
        guards_runtime_root_only=True,
    )

    controlled_pr_cycle_parser = subparsers.add_parser(
        "controlled-pr-cycle",
        help="Compose saved PR-cycle evidence into one read-only consistency packet",
    )
    controlled_pr_cycle_parser.add_argument("--controlled-loop-tick-file", required=True)
    controlled_pr_cycle_parser.add_argument("--git-pr-materialization-file", required=True)
    controlled_pr_cycle_parser.add_argument("--initial-post-write-gate-file", required=True)
    controlled_pr_cycle_parser.add_argument("--review-response-materialization-file")
    controlled_pr_cycle_parser.add_argument("--review-response-post-write-gate-file")
    controlled_pr_cycle_parser.add_argument("--review-thread-resolution-materialization-file")
    controlled_pr_cycle_parser.add_argument("--review-thread-resolution-post-write-gate-file")
    controlled_pr_cycle_parser.set_defaults(
        func=controlled_pr_cycle_command,
        requires_root=True,
        guards_runtime_root_only=True,
    )

    executor_result_parser = subparsers.add_parser(
        "validate-executor-result",
        help="Validate generic executor result evidence against a task packet",
    )
    executor_result_parser.add_argument("--task-file", required=True)
    executor_result_parser.add_argument("--result-file", required=True)
    executor_result_parser.set_defaults(
        func=validate_executor_result_command,
        requires_root=False,
        guards_optional_root=True,
    )

    closeout_parser = subparsers.add_parser(
        "closeout-executor-result",
        help="Close out the active epoch from validated local executor result evidence",
    )
    closeout_parser.add_argument("--epoch-id", required=True)
    closeout_parser.add_argument("--task-file", required=True)
    closeout_parser.add_argument("--result-file", required=True)
    closeout_parser.add_argument("--snapshot-after-file", required=True)
    closeout_evidence_group = closeout_parser.add_mutually_exclusive_group(required=True)
    closeout_evidence_group.add_argument("--run-record-file")
    closeout_evidence_group.add_argument("--real-invocation-file")
    closeout_parser.add_argument("--cwd", default=".")
    closeout_parser.add_argument("--emit-git-pr-plan", action="store_true")
    closeout_parser.add_argument("--base-branch", default="main")
    closeout_parser.add_argument("--branch-prefix", default="cadence")
    closeout_parser.add_argument("--policy-file")
    closeout_parser.add_argument("--pr-template-file")
    closeout_parser.add_argument("--required-body-section", action="append", default=[])
    closeout_parser.set_defaults(
        func=closeout_executor_result_command,
        requires_root=True,
    )

    execution_start_parser = subparsers.add_parser(
        "start-governed-execution",
        help="Start one governed epoch from an approved generic executor task packet",
    )
    execution_start_parser.add_argument("--task-file", required=True)
    execution_start_parser.add_argument("--approval-token")
    execution_start_parser.add_argument("--cwd")
    execution_start_parser.add_argument("--ownership-target")
    execution_start_parser.add_argument("--ownership-role")
    execution_start_parser.add_argument("--ownership-claimer")
    execution_start_parser.add_argument(
        "--ownership-max-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    execution_start_parser.set_defaults(
        func=start_governed_execution_command,
        requires_root=True,
        guards_runtime_root_only=True,
    )

    controlled_fixture_parser = subparsers.add_parser(
        "run-controlled-executor-fixture",
        help="Run a test/example-only controlled executor fixture command",
    )
    controlled_fixture_parser.add_argument("--task-file", required=True)
    controlled_fixture_parser.add_argument("--command-template", required=True)
    controlled_fixture_parser.add_argument("--timeout-seconds", type=positive_int, default=60)
    controlled_fixture_parser.set_defaults(
        func=run_controlled_executor_fixture_command,
        requires_root=True,
        guards_runtime_root_only=True,
    )

    audit_replay_parser = subparsers.add_parser(
        "audit-replay",
        help="Replay and validate the local Cadence audit log",
    )
    audit_replay_parser.set_defaults(
        func=audit_replay_command,
        requires_root=False,
        guards_runtime_root_only=True,
    )

    readiness_parser = subparsers.add_parser("pr-readiness", help="Evaluate a saved PR JSON readiness packet")
    readiness_parser.add_argument("--pr-json-file", required=True)
    readiness_parser.add_argument("--review-threads-file")
    readiness_parser.add_argument("--required-check", action="append", default=[])
    readiness_parser.add_argument("--required-body-section", action="append", default=[])
    readiness_parser.add_argument("--pr-template-file")
    readiness_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    readiness_parser.set_defaults(func=pr_readiness_command, requires_root=False)

    review_response_parser = subparsers.add_parser(
        "review-response-plan",
        help="Plan a bounded response from saved PR checks and review feedback",
    )
    review_response_parser.add_argument("--pr-json-file", required=True)
    review_response_parser.add_argument("--review-threads-file")
    review_response_parser.add_argument("--candidate-discovery-file")
    review_response_parser.add_argument("--required-body-section", action="append", default=[])
    review_response_parser.add_argument("--pr-template-file")
    review_response_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    review_response_parser.set_defaults(func=review_response_plan_command, requires_root=False)

    review_response_materialization_parser = subparsers.add_parser(
        "review-response-materialization-plan",
        help="Plan exact operator-approved review response writes from saved evidence",
    )
    review_response_materialization_parser.add_argument("--response-plan-file", required=True)
    review_response_materialization_parser.add_argument("--pr-json-file", required=True)
    review_response_materialization_parser.add_argument("--review-threads-file")
    review_response_materialization_parser.add_argument("--candidate-discovery-file")
    review_response_materialization_parser.add_argument("--write-file", required=True)
    review_response_materialization_parser.add_argument("--required-body-section", action="append", default=[])
    review_response_materialization_parser.add_argument("--pr-template-file")
    review_response_materialization_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    review_response_materialization_parser.set_defaults(
        func=review_response_materialization_plan_command,
        requires_root=False,
    )

    review_response_materialize_parser = subparsers.add_parser(
        "review-response-materialize",
        help="Materialize approved PR body/comment review responses after freshness rechecks",
    )
    review_response_materialize_parser.add_argument("--cwd", default=".")
    review_response_materialize_parser.add_argument("--plan-file", required=True)
    review_response_materialize_parser.add_argument("--pr-json-file", required=True)
    review_response_materialize_parser.add_argument("--review-threads-file")
    review_response_materialize_parser.add_argument("--candidate-discovery-file")
    review_response_materialize_parser.add_argument("--approval-token")
    review_response_materialize_parser.add_argument("--required-body-section", action="append", default=[])
    review_response_materialize_parser.add_argument("--pr-template-file")
    review_response_materialize_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    review_response_materialize_parser.set_defaults(
        func=review_response_materialize_command,
        requires_root=True,
    )

    review_thread_resolution_parser = subparsers.add_parser(
        "review-thread-resolution-plan",
        help="Plan exact operator-approved review thread resolutions from refreshed saved evidence",
    )
    review_thread_resolution_parser.add_argument("--pr-json-file", required=True)
    review_thread_resolution_parser.add_argument("--review-threads-file", required=True)
    review_thread_resolution_parser.add_argument("--response-materialization-file", required=True)
    review_thread_resolution_parser.add_argument("--post-write-gate-file", required=True)
    review_thread_resolution_parser.add_argument("--thread-id", action="append", default=[])
    review_thread_resolution_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    review_thread_resolution_parser.set_defaults(
        func=review_thread_resolution_plan_command,
        requires_root=False,
    )

    review_thread_resolution_materialize_parser = subparsers.add_parser(
        "review-thread-resolution-materialize",
        help="Resolve approved review threads after exact target and freshness rechecks",
    )
    review_thread_resolution_materialize_parser.add_argument("--cwd", default=".")
    review_thread_resolution_materialize_parser.add_argument("--plan-file", required=True)
    review_thread_resolution_materialize_parser.add_argument("--pr-json-file", required=True)
    review_thread_resolution_materialize_parser.add_argument("--review-threads-file", required=True)
    review_thread_resolution_materialize_parser.add_argument("--response-materialization-file", required=True)
    review_thread_resolution_materialize_parser.add_argument("--post-write-gate-file", required=True)
    review_thread_resolution_materialize_parser.add_argument("--approval-token")
    review_thread_resolution_materialize_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    review_thread_resolution_materialize_parser.set_defaults(
        func=review_thread_resolution_materialize_command,
        requires_root=True,
    )

    role_readiness_parser = subparsers.add_parser(
        "role-readiness",
        help="Evaluate local role policy and builder/reviewer separation evidence",
    )
    role_readiness_parser.add_argument("--cwd", default=".")
    role_readiness_parser.add_argument("--repo")
    role_readiness_parser.add_argument("--branch")
    role_readiness_parser.add_argument("--task-id")
    role_readiness_parser.add_argument("--role-policy-file")
    role_readiness_parser.add_argument("--pr-json-file")
    role_readiness_parser.add_argument("--review-threads-file")
    role_readiness_parser.add_argument(
        "--max-ownership-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    role_readiness_parser.set_defaults(func=role_readiness_command, requires_root=True)

    executor_readiness_parser = subparsers.add_parser(
        "executor-invocation-readiness",
        help="Read-only preflight for future real executor invocation",
    )
    executor_readiness_parser.add_argument("--cwd", default=".")
    executor_readiness_parser.add_argument("--task-file", required=True)
    executor_readiness_parser.add_argument("--epoch-id", required=True)
    executor_readiness_parser.add_argument("--ownership-target")
    executor_readiness_parser.add_argument("--expected-result-path", required=True)
    executor_readiness_parser.add_argument("--role-readiness-file")
    executor_readiness_parser.add_argument(
        "--max-ownership-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    executor_readiness_parser.set_defaults(func=executor_invocation_readiness_command, requires_root=True)

    executor_plan_parser = subparsers.add_parser(
        "executor-invocation-plan",
        help="Read-only real executor invocation plan with approval binding",
    )
    executor_plan_parser.add_argument("--cwd", default=".")
    executor_plan_parser.add_argument("--readiness-file", required=True)
    executor_plan_parser.add_argument("--approval-file", required=True)
    executor_plan_parser.add_argument("--approval-secret")
    executor_plan_parser.add_argument("--approval-secret-env", default=OPERATOR_APPROVAL_SECRET_ENV)
    executor_plan_parser.add_argument("--adapter-file", required=True)
    executor_plan_parser.add_argument("--rollback-file", required=True)
    executor_plan_parser.add_argument("--command", required=True)
    executor_plan_parser.add_argument("--env-allow", action="append", default=[])
    executor_plan_parser.add_argument("--timeout-seconds", type=positive_int, required=True)
    executor_plan_parser.add_argument("--expected-result-path", required=True)
    executor_plan_parser.set_defaults(func=executor_invocation_plan_command, requires_root=True)

    invoke_executor_parser = subparsers.add_parser(
        "invoke-real-executor",
        help="Start one approved real executor command from a fresh invocation plan",
    )
    invoke_executor_parser.add_argument("--plan-file", required=True)
    invoke_executor_parser.add_argument("--approval-secret")
    invoke_executor_parser.add_argument("--approval-secret-env", default=OPERATOR_APPROVAL_SECRET_ENV)
    invoke_executor_parser.add_argument("--side-effect-mode", choices=REAL_EXECUTOR_SIDE_EFFECT_MODES, required=True)
    invoke_executor_parser.add_argument("--max-plan-age-minutes", type=positive_int, default=15)
    invoke_executor_parser.set_defaults(
        func=invoke_real_executor_command,
        requires_root=True,
        skip_runtime_root_safety=True,
    )

    github_evidence_parser = subparsers.add_parser(
        "github-evidence-sync",
        help="Fetch read-only GitHub PR evidence into local JSON files",
    )
    github_evidence_parser.add_argument("--repo", required=True)
    github_evidence_parser.add_argument("--pr-number", type=positive_int, required=True)
    github_evidence_parser.add_argument("--out-dir", required=True)
    github_evidence_parser.set_defaults(func=github_evidence_sync_command, requires_root=False)

    post_write_gate_parser = subparsers.add_parser(
        "post-write-pr-evidence-gate",
        help="Evaluate fresh saved GitHub evidence after approved PR writes",
    )
    post_write_gate_parser.add_argument("--cwd", default=".")
    post_write_gate_parser.add_argument("--materialization-file", required=True)
    post_write_gate_parser.add_argument("--github-evidence-file", required=True)
    post_write_gate_parser.add_argument("--required-check", action="append", default=[])
    post_write_gate_parser.add_argument("--required-body-section", action="append", default=[])
    post_write_gate_parser.add_argument("--pr-template-file")
    post_write_gate_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    post_write_gate_parser.add_argument("--discovery-mode", choices=DISCOVERY_MODES, default="local")
    post_write_gate_parser.add_argument("--proposal-allowance", choices=PROPOSAL_ALLOWANCES, default="none")
    post_write_gate_parser.add_argument("--max-tasks", type=positive_int, default=1)
    post_write_gate_parser.set_defaults(func=post_write_pr_evidence_gate_command, requires_root=False)

    body_preflight_parser = subparsers.add_parser("pr-body-preflight", help="Evaluate a draft PR body before publishing")
    body_preflight_parser.add_argument("--body-file", required=True)
    body_preflight_parser.add_argument("--required-body-section", action="append", default=[])
    body_preflight_parser.add_argument("--pr-template-file")
    body_preflight_parser.set_defaults(func=pr_body_preflight_command, requires_root=False)

    git_pr_plan_parser = subparsers.add_parser(
        "git-pr-plan",
        help="Generate a dry-run Git/PR transition plan from executor evidence",
    )
    git_pr_plan_parser.add_argument("--cwd", default=".")
    git_pr_plan_parser.add_argument("--task-file", required=True)
    git_pr_plan_parser.add_argument("--result-file", required=True)
    git_pr_plan_parser.add_argument("--base-branch", default="main")
    git_pr_plan_parser.add_argument("--branch-prefix", default="cadence")
    git_pr_plan_parser.add_argument("--policy-file")
    git_pr_plan_parser.add_argument("--pr-template-file")
    git_pr_plan_parser.add_argument("--required-body-section", action="append", default=[])
    git_pr_plan_parser.set_defaults(
        func=git_pr_plan_command,
        requires_root=False,
        guards_optional_root=True,
    )

    dirty_materialization_plan_parser = subparsers.add_parser(
        "git-pr-dirty-materialization-plan",
        help="Generate a dry-run Git/PR materialization input for closeout-approved dirty worktrees",
    )
    dirty_materialization_plan_parser.add_argument("--cwd", default=".")
    dirty_materialization_plan_parser.add_argument("--task-file", required=True)
    dirty_materialization_plan_parser.add_argument("--result-file", required=True)
    dirty_materialization_plan_parser.add_argument("--real-invocation-file", required=True)
    dirty_materialization_plan_parser.add_argument("--closeout-file", required=True)
    dirty_materialization_plan_parser.add_argument("--base-branch", default="main")
    dirty_materialization_plan_parser.add_argument("--expected-base-head")
    dirty_materialization_plan_parser.add_argument("--branch-prefix", default="cadence")
    dirty_materialization_plan_parser.add_argument("--policy-file")
    dirty_materialization_plan_parser.add_argument("--pr-template-file")
    dirty_materialization_plan_parser.add_argument("--required-body-section", action="append", default=[])
    dirty_materialization_plan_parser.add_argument("--remote", default="origin")
    dirty_materialization_plan_parser.add_argument("--pr-number", type=positive_int)
    dirty_materialization_plan_parser.set_defaults(
        func=git_pr_dirty_materialization_plan_command,
        requires_root=False,
        guards_optional_root=True,
    )

    git_pr_materialize_parser = subparsers.add_parser(
        "git-pr-materialize",
        help="Materialize an operator-approved Git/PR plan after local preflight rechecks",
    )
    git_pr_materialize_parser.add_argument("--cwd", default=".")
    git_pr_materialize_parser.add_argument("--plan-file", required=True)
    git_pr_materialize_parser.add_argument("--approval-token")
    git_pr_materialize_parser.add_argument("--remote", default="origin")
    git_pr_materialize_parser.add_argument("--pr-number", type=positive_int)
    git_pr_materialize_parser.add_argument("--pr-json-file")
    git_pr_materialize_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    git_pr_materialize_parser.add_argument("--dirty-commit-materialization-file")
    git_pr_materialize_parser.set_defaults(func=git_pr_materialize_command, requires_root=True)

    dirty_commit_materialize_parser = subparsers.add_parser(
        "git-pr-dirty-commit-materialize",
        help="Materialize an operator-approved dirty-worktree plan into one local commit",
    )
    dirty_commit_materialize_parser.add_argument("--cwd", default=".")
    dirty_commit_materialize_parser.add_argument("--plan-file", required=True)
    dirty_commit_materialize_parser.add_argument("--approval-token")
    dirty_commit_materialize_parser.set_defaults(func=git_pr_dirty_commit_materialize_command, requires_root=True)

    operator_approval_parser = subparsers.add_parser(
        "verify-operator-approval",
        help="Verify reusable operator approval identity evidence without starting governed actions",
    )
    operator_approval_parser.add_argument("--approval-file", required=True)
    operator_approval_parser.add_argument("--target-checksum", required=True)
    operator_approval_parser.add_argument("--purpose", choices=sorted(OPERATOR_APPROVAL_PURPOSES), required=True)
    operator_approval_parser.add_argument("--approval-secret")
    operator_approval_parser.add_argument("--approval-secret-env", default=OPERATOR_APPROVAL_SECRET_ENV)
    operator_approval_parser.set_defaults(func=verify_operator_approval_command, requires_root=True)

    release_parser = subparsers.add_parser(
        "release-dry-run",
        help="Evaluate release metadata, notes, and tag status without publishing",
    )
    release_parser.add_argument("--cwd", default=".")
    release_parser.add_argument("--version")
    release_parser.add_argument("--tag")
    release_parser.add_argument("--target-branch", default="main")
    release_parser.add_argument("--target-ref", default="HEAD")
    release_parser.set_defaults(func=release_dry_run_command, requires_root=False)

    start_epoch_parser = subparsers.add_parser("start-epoch", help="Create an active epoch")
    start_epoch_parser.add_argument("--repo", required=True)
    start_epoch_parser.add_argument("--branch", required=True)
    start_epoch_parser.add_argument("--tasks-file")
    start_epoch_parser.add_argument("--snapshot-before-file", required=True)
    start_epoch_parser.set_defaults(func=start_epoch_command)

    complete_epoch_parser = subparsers.add_parser("complete-epoch", help="Complete an active epoch")
    complete_epoch_parser.add_argument("epoch_id")
    complete_epoch_parser.add_argument("--decision", choices=("STOP", "CONTINUE", "HANDOFF", "ASK_APPROVAL"), required=True)
    complete_epoch_parser.add_argument("--summary")
    complete_epoch_parser.set_defaults(func=complete_epoch_command)

    fail_epoch_parser = subparsers.add_parser("fail-epoch", help="Mark an active epoch failed")
    fail_epoch_parser.add_argument("epoch_id")
    fail_epoch_parser.add_argument("--reason", required=True)
    fail_epoch_parser.set_defaults(func=fail_epoch_command)

    self_check_parser = subparsers.add_parser("self-check", help="Evaluate epoch continuation rights")
    self_check_parser.add_argument("--epoch-id")
    self_check_parser.add_argument("--candidates-file")
    self_check_parser.add_argument("--max-tasks", type=int, default=1)
    self_check_parser.add_argument("--repo-confidence", choices=("high", "medium", "low"))
    self_check_parser.add_argument("--uncertainty", choices=("low", "medium", "high"), default="low")
    self_check_parser.add_argument("--epoch-health", choices=("good", "watch", "degraded"), default="good")
    self_check_parser.add_argument("--snapshot-after-file")
    self_check_parser.add_argument("--allow-recursive-discovery", action="store_true")
    self_check_parser.set_defaults(func=self_check_command)

    next_parser = subparsers.add_parser("next-handoff", help="Show the next ready handoff")
    next_parser.add_argument("--all", action="store_true")
    next_parser.set_defaults(func=next_handoff)

    claim_parser = subparsers.add_parser("claim-handoff", help="Claim a ready handoff")
    claim_parser.add_argument("handoff_id")
    claim_parser.add_argument("--claimer", required=True)
    claim_parser.set_defaults(func=claim_handoff)

    approve_parser = subparsers.add_parser("approve-handoff", help="Approve an approval-gated ready handoff")
    approve_parser.add_argument("handoff_id")
    approve_parser.add_argument("--approver", required=True)
    approve_parser.set_defaults(func=approve_handoff)

    complete_parser = subparsers.add_parser("complete-handoff", help="Mark a claimed handoff completed")
    complete_parser.add_argument("handoff_id")
    complete_parser.add_argument("--summary")
    complete_parser.set_defaults(func=complete_handoff)

    fail_parser = subparsers.add_parser("fail-handoff", help="Mark a ready or claimed handoff failed")
    fail_parser.add_argument("handoff_id")
    fail_parser.add_argument("--reason", required=True)
    fail_parser.set_defaults(func=fail_handoff)

    validate_parser = subparsers.add_parser("validate-handoff", help="Validate a handoff id or JSON path")
    validate_parser.add_argument("target")
    validate_parser.set_defaults(func=validate_handoff)

    verify_resume_parser = subparsers.add_parser(
        "verify-resume",
        help="Read-only gate that verifies a claimed handoff can resume in the current repo",
    )
    verify_resume_parser.add_argument("handoff_id")
    verify_resume_parser.add_argument("--cwd", default=".")
    verify_resume_parser.add_argument("--claimer")
    verify_resume_parser.set_defaults(func=verify_resume_command)

    resume_continuation_parser = subparsers.add_parser(
        "resume-continuation",
        help="Read-only gate that binds a saved resume verification to governed execution start",
    )
    resume_continuation_parser.add_argument("--resume-verification-file", required=True)
    resume_continuation_parser.add_argument("--cwd", default=".")
    resume_continuation_parser.add_argument("--claimer")
    resume_continuation_parser.add_argument("--ownership-target")
    resume_continuation_parser.add_argument("--ownership-role")
    resume_continuation_parser.add_argument("--ownership-task-id")
    resume_continuation_parser.add_argument(
        "--max-ownership-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    resume_continuation_parser.add_argument(
        "--max-resume-age-minutes",
        type=non_negative_int,
        default=DEFAULT_RESUME_CONTINUATION_MAX_AGE_MINUTES,
    )
    resume_continuation_parser.set_defaults(func=resume_continuation_command)

    ownership_status_parser = subparsers.add_parser(
        "work-ownership-status",
        help="Read-only status for local work ownership records",
    )
    ownership_status_parser.add_argument("--cwd", default=".")
    ownership_status_parser.add_argument("--repo")
    ownership_status_parser.add_argument("--branch")
    ownership_status_parser.add_argument("--task-id")
    ownership_status_parser.add_argument(
        "--max-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    ownership_status_parser.set_defaults(func=work_ownership_status_command)

    validate_ownership_parser = subparsers.add_parser(
        "validate-work-ownership",
        help="Validate one local work ownership record without mutating runtime state",
    )
    validate_ownership_parser.add_argument("target")
    validate_ownership_parser.add_argument("--cwd", default=".")
    validate_ownership_parser.add_argument("--repo")
    validate_ownership_parser.add_argument("--branch")
    validate_ownership_parser.add_argument("--task-id")
    validate_ownership_parser.add_argument("--require-active", action="store_true")
    validate_ownership_parser.add_argument(
        "--max-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    validate_ownership_parser.set_defaults(func=validate_work_ownership_command)

    claim_ownership_parser = subparsers.add_parser(
        "claim-work-ownership",
        help="Create one governed local active work ownership record",
    )
    claim_ownership_parser.add_argument("--id")
    claim_ownership_parser.add_argument("--cwd", default=".")
    claim_ownership_parser.add_argument("--repo", required=True)
    claim_ownership_parser.add_argument("--branch", required=True)
    claim_ownership_parser.add_argument("--head", required=True)
    claim_ownership_parser.add_argument("--task-id", required=True)
    claim_ownership_parser.add_argument("--candidate-id", required=True)
    claim_ownership_parser.add_argument("--role", required=True)
    claim_ownership_parser.add_argument("--claimer", required=True)
    claim_ownership_parser.add_argument("--pr-number", type=positive_int)
    claim_ownership_parser.add_argument("--epoch-id")
    claim_ownership_parser.add_argument("--handoff-id")
    claim_ownership_parser.add_argument(
        "--max-age-minutes",
        type=non_negative_int,
        default=DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
    )
    claim_ownership_parser.set_defaults(func=claim_work_ownership_command)

    close_ownership_parser = subparsers.add_parser(
        "close-work-ownership",
        help="Move one active work ownership record to closed local evidence",
    )
    close_ownership_parser.add_argument("target")
    close_ownership_parser.add_argument("--cwd", default=".")
    close_ownership_parser.add_argument("--repo", required=True)
    close_ownership_parser.add_argument("--branch", required=True)
    close_ownership_parser.add_argument("--head", required=True)
    close_ownership_parser.add_argument("--task-id", required=True)
    close_ownership_parser.add_argument("--claimer", required=True)
    close_ownership_parser.add_argument("--summary", required=True)
    close_ownership_parser.set_defaults(func=close_work_ownership_command)

    complete_ownership_from_closeout_parser = subparsers.add_parser(
        "complete-work-ownership-from-closeout",
        help="Move one active work ownership record to closed after completed executor closeout evidence matches",
    )
    complete_ownership_from_closeout_parser.add_argument("target")
    complete_ownership_from_closeout_parser.add_argument("--cwd", default=".")
    complete_ownership_from_closeout_parser.add_argument("--closeout-file", required=True)
    complete_ownership_from_closeout_parser.add_argument("--closeout-checksum", required=True)
    complete_ownership_from_closeout_parser.add_argument("--candidate-id", required=True)
    complete_ownership_from_closeout_parser.add_argument("--role", required=True)
    complete_ownership_from_closeout_parser.add_argument("--claimer", required=True)
    complete_ownership_from_closeout_parser.add_argument("--summary")
    complete_ownership_from_closeout_parser.set_defaults(func=complete_work_ownership_from_closeout_command)

    fail_ownership_parser = subparsers.add_parser(
        "fail-work-ownership",
        help="Move one active work ownership record to failed local evidence",
    )
    fail_ownership_parser.add_argument("target")
    fail_ownership_parser.add_argument("--cwd", default=".")
    fail_ownership_parser.add_argument("--repo", required=True)
    fail_ownership_parser.add_argument("--branch", required=True)
    fail_ownership_parser.add_argument("--head", required=True)
    fail_ownership_parser.add_argument("--task-id", required=True)
    fail_ownership_parser.add_argument("--claimer", required=True)
    fail_ownership_parser.add_argument("--summary", required=True)
    fail_ownership_parser.set_defaults(func=fail_work_ownership_command)

    clean_parser = subparsers.add_parser("clean-square", help="Record old-session shutdown after handoff")
    clean_parser.add_argument("handoff_id")
    clean_parser.add_argument("--summary")
    clean_parser.set_defaults(func=clean_square)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        requires_root = getattr(args, "requires_root", True)
        guards_runtime_root_only = getattr(args, "guards_runtime_root_only", False)
        if requires_root or guards_runtime_root_only or args.root is not None:
            args.root = (args.root if args.root is not None else default_root()).expanduser().resolve()
            if (
                requires_root
                or guards_runtime_root_only
                or getattr(args, "guards_optional_root", False)
            ) and not args.allow_repo_local_root and not getattr(args, "skip_runtime_root_safety", False):
                if requires_root and not guards_runtime_root_only:
                    target_cwd = Path(getattr(args, "cwd", Path.cwd()))
                    issue = runtime_root_safety_issue(args.root, target_cwd)
                    if issue:
                        raise ValueError(issue)
                if guards_runtime_root_only or getattr(args, "guards_optional_root", False):
                    issue = runtime_root_location_safety_issue(args.root)
                    if issue:
                        raise ValueError(issue)
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
