#!/usr/bin/env python3
"""Agentic Cadence command line tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.candidates import DISCOVERY_INTENTS, DISCOVERY_MODES, PROPOSAL_ALLOWANCES, CandidateBudget
from codex_cadence.candidates import discover_candidates
from codex_cadence.executor_contract import (
    DEFAULT_EXECUTOR_STOP_CONDITIONS,
    build_executor_task_packet,
    validate_executor_result_evidence,
    validate_executor_task_packet,
)
from codex_cadence.git_pr_plan import evaluate_git_pr_plan
from codex_cadence.epochs import complete_epoch as complete_epoch_record
from codex_cadence.epochs import CONTINUE, ASK_APPROVAL
from codex_cadence.epochs import completed_continue_count
from codex_cadence.epochs import continuation_task_limit
from codex_cadence.epochs import epoch_elapsed_minutes
from codex_cadence.epochs import fail_epoch as fail_epoch_record
from codex_cadence.epochs import checksum_json as checksum_epoch_json
from codex_cadence.epochs import elect_candidates, load_active_epoch, record_self_check, self_check_decision
from codex_cadence.epochs import policy_limit
from codex_cadence.epochs import REPO_CONFIDENCE_VALUES, UNCERTAINTY_VALUES
from codex_cadence.epochs import start_epoch as start_epoch_record
from codex_cadence.epochs import validate_snapshot_after_epoch
from codex_cadence.handoff_loop import prepare_handoff
from codex_cadence.model import BUCKETS, TASK_TYPES, estimate_task, governance_permissions, policy_for_bucket
from codex_cadence.policy_audit import (
    append_audit_record,
    executor_result_validation_audit_record,
    load_loop_policy,
    loop_tick_audit_record,
    replay_audit_log,
    resolve_executor_policy,
)
from codex_cadence.pr_readiness import (
    evaluate_pr_body_preflight,
    evaluate_pr_readiness,
    load_pr_body,
    load_pr_json,
    load_template_sections,
)
from codex_cadence.release import evaluate_release_dry_run
from codex_cadence.repo_state import (
    runtime_root_location_safety_issue,
    runtime_root_safety_issue,
    snapshot_repo,
    validate_repo_snapshot,
)
from codex_cadence.store import (
    BRAKE_STATUSES,
    HANDOFF_STATES,
    approval_path,
    atomic_write_json,
    brake_path,
    default_root,
    ensure_layout,
    exclusive_lock,
    handoff_path,
    handoff_state_dir,
    lock_path,
    read_brake,
    read_json,
    record_lock_path,
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


def validate_executor_result_command(args: argparse.Namespace) -> int:
    task_file = Path(args.task_file)
    result_file = Path(args.result_file)
    task_packet = read_json(task_file)
    result_evidence = read_json(result_file)
    valid, reason = validate_executor_result_evidence(result_evidence, task_packet)
    if valid:
        expected_output = task_packet.get("expected_output") if isinstance(task_packet, dict) else {}
        expected_path = expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
        if expected_path is not None and Path(expected_path).expanduser().resolve() != result_file.expanduser().resolve():
            valid = False
            reason = "executor result file does not match task expected_output.evidence_path"
    repo = {}
    if getattr(args, "root", None) is not None:
        repo = task_packet.get("repo") if isinstance(task_packet, dict) and isinstance(task_packet.get("repo"), dict) else {}
        repo_path = repo.get("path")
        if isinstance(repo_path, str) and repo_path and not args.allow_repo_local_root:
            issue = runtime_root_safety_issue(args.root, repo_path)
            if issue:
                raise ValueError(issue)
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
    if needs_brake_check and getattr(args, "root", None) is None:
        valid = False
        reason = "runtime root is required to validate brake_not_drive stop condition"
        missing_runtime_root_for_stop = True
    if getattr(args, "root", None) is not None:
        brake = read_brake(args.root)
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
        "executor_started": False,
        "recommended_next_action": recommended_next_action,
    }
    if active_stop is not None:
        payload["active_stop"] = active_stop
    if getattr(args, "root", None) is not None:
        payload["audit_record"] = append_audit_record(
            args.root,
            executor_result_validation_audit_record(payload, task_packet, result_evidence),
        )
    emit(payload)
    return 0 if valid else 1


def audit_replay_command(args: argparse.Namespace) -> int:
    """Emit a read-only audit replay packet for the runtime root."""
    payload = replay_audit_log(args.root)
    emit(payload)
    return 0 if payload["valid"] else 1


def pr_readiness_command(args: argparse.Namespace) -> int:
    pr_json_file = Path(args.pr_json_file)
    pr = load_pr_json(pr_json_file)
    required_body_sections = list(args.required_body_section or [])
    if args.pr_template_file:
        required_body_sections.extend(load_template_sections(Path(args.pr_template_file)))
    evidence_captured_at = datetime.fromtimestamp(pr_json_file.stat().st_mtime, timezone.utc)
    payload = evaluate_pr_readiness(
        pr,
        required_checks=args.required_check or [],
        required_body_sections=required_body_sections,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=args.max_pr_json_age_minutes,
    )
    emit(payload)
    return 0


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
    payload = evaluate_git_pr_plan(
        cwd=Path(args.cwd),
        task_packet=task_packet,
        result_evidence=result_evidence,
        task_file=task_file,
        result_file=result_file,
        base_branch=args.base_branch,
        branch_prefix=args.branch_prefix,
        required_body_sections=required_body_sections,
        runtime_root=args.root,
    )
    emit(payload)
    return 0


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
    readiness_parser.add_argument("--required-check", action="append", default=[])
    readiness_parser.add_argument("--required-body-section", action="append", default=[])
    readiness_parser.add_argument("--pr-template-file")
    readiness_parser.add_argument("--max-pr-json-age-minutes", type=non_negative_int)
    readiness_parser.set_defaults(func=pr_readiness_command, requires_root=False)

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
    git_pr_plan_parser.add_argument("--pr-template-file")
    git_pr_plan_parser.add_argument("--required-body-section", action="append", default=[])
    git_pr_plan_parser.set_defaults(
        func=git_pr_plan_command,
        requires_root=False,
        guards_optional_root=True,
    )

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
            ) and not args.allow_repo_local_root:
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
