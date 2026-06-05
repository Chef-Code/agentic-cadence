from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.epochs import read_active_epoch_records
from codex_cadence.model import BUCKETS, TASK_TYPES, estimate_task, governance_permissions, policy_for_bucket
from codex_cadence.repo_state import current_repo_evidence, snapshot_repo
from codex_cadence.store import (
    HANDOFF_STATES,
    approval_path,
    atomic_write_json,
    brake_path,
    ensure_layout,
    exclusive_lock,
    handoff_path,
    handoff_state_dir,
    lock_path,
    read_brake,
    read_json,
    snapshot_path,
    utc_now,
    validate_record_id,
)

RESUME_VERIFICATION_SCHEMA_VERSION = "resume-verification.v1"
RESUME_CONTINUATION_SCHEMA_VERSION = "resume-continuation.v1"
DEFAULT_RESUME_CONTINUATION_MAX_AGE_MINUTES = 60

RESUME_CONTINUATION_ACTIONS = {
    "start_governed_execution",
    "claim_handoff",
    "approve_handoff",
    "recreate_handoff",
    "close_or_fail_active_epoch",
    "inspect_resume_blockers",
}

_RESUME_CONTINUATION_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("handoff_id", ("handoff_id",)),
    ("handoff.state", ("handoff", "state")),
    ("handoff.status", ("handoff", "status")),
    ("handoff.claimed_by", ("handoff", "claimed_by")),
    ("handoff.signature_valid", ("handoff", "signature_valid")),
    ("clean_square.present", ("clean_square", "present")),
    ("clean_square.valid", ("clean_square", "valid")),
    ("clean_square.created_at", ("clean_square", "created_at")),
    ("repository.expected_repo", ("repository", "expected_repo")),
    ("repository.expected_branch", ("repository", "expected_branch")),
    ("repository.expected_head", ("repository", "expected_head")),
    ("repository.current_branch", ("repository", "current_branch")),
    ("repository.current_head", ("repository", "current_head")),
    ("repository.dirty_worktree", ("repository", "dirty_worktree")),
    ("repository.snapshot_id", ("repository", "snapshot_id")),
    ("repository.snapshot_checksum", ("repository", "snapshot_checksum")),
    ("cadence.brake_status", ("cadence", "brake_status")),
    ("cadence.state", ("cadence", "state")),
    ("active_epoch.count", ("active_epoch", "count")),
    ("active_epoch.epochs", ("active_epoch", "epochs")),
    ("policy_evidence.status", ("policy_evidence", "status")),
    ("policy_evidence.approval_required", ("policy_evidence", "approval_required")),
    ("policy_evidence.approval_present", ("policy_evidence", "approval_present")),
    ("policy_evidence.estimate_checksum", ("policy_evidence", "estimate_checksum")),
)


def _format_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "unknown" if value is None else str(value)


def _join_drivers(drivers: Any) -> str:
    if not isinstance(drivers, list) or not drivers:
        return "none"
    return ", ".join(str(driver) for driver in drivers)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "handoff")[:48].strip("-") or "handoff"


def _checksum_message(message: str) -> str:
    return "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()


def _checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_signature(handoff_id: str, checksum: str, status: str = "READY") -> str:
    return f"<!-- codex-handoff:{PROTOCOL_VERSION} id={handoff_id} status={status} sha={checksum.removeprefix('sha256:')} -->"


def _checksum_estimate_binding(
    *,
    title: str,
    message: str,
    source: dict[str, Any],
    estimate: dict[str, Any],
) -> str:
    return _checksum_json(
        {
            "title": title,
            "message_checksum": _checksum_message(message),
            "estimate_input": source,
            "estimate": estimate,
        }
    )


def _resume_snapshot_binding(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": snapshot.get("id"),
        "path": snapshot.get("path"),
        "repo": snapshot.get("repo"),
        "branch": snapshot.get("branch"),
        "head": snapshot.get("head"),
        "checksum": _checksum_json(snapshot),
    }


def _cadence_state(brake: dict[str, Any]) -> dict[str, Any]:
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


def _status_payload(root: Path, brake: dict[str, Any]) -> dict[str, Any]:
    counts = {
        state: len(list(handoff_state_dir(root, state).glob("*.json")))
        for state in HANDOFF_STATES
    }
    return {
        "root": str(root),
        "brake": brake,
        "cadence": _cadence_state(brake),
        "counts": counts,
    }


def discover_remote_url(cwd: str | Path, remote: str = "origin") -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=Path(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _snapshot_id(snapshot: dict[str, Any], repo: str | None) -> str:
    stamp_source = snapshot.get("captured_at")
    if not isinstance(stamp_source, str) or not stamp_source:
        stamp_source = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = stamp_source.replace(":", "").replace("-", "")
    basis = repo or snapshot.get("branch") or "repo"
    return f"{stamp}-{_slugify(str(basis))}-{secrets.token_hex(4)}"


def _validate_handoff_record(data: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return {
            "valid": False,
            "errors": ["handoff record must be a JSON object"],
            "id": None,
        }
    required = ("protocol_version", "id", "status", "checksum", "signature", "message")
    for key in required:
        if key not in data:
            errors.append(f"missing {key}")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"unsupported protocol_version: {data.get('protocol_version')}")
    if "message" in data and not isinstance(data.get("message"), str):
        errors.append("message must be a string")
    if "checksum" in data and not isinstance(data.get("checksum"), str):
        errors.append("checksum must be a string")
    if "signature" in data and not isinstance(data.get("signature"), str):
        errors.append("signature must be a string")
    if "id" in data and not isinstance(data.get("id"), str):
        errors.append("id must be a string")
    if "status" in data and not isinstance(data.get("status"), str):
        errors.append("status must be a string")
    if isinstance(data.get("message"), str) and isinstance(data.get("checksum"), str):
        actual = _checksum_message(data["message"])
        if actual != data["checksum"]:
            errors.append("checksum mismatch")
    if isinstance(data.get("id"), str) and isinstance(data.get("checksum"), str) and isinstance(data.get("signature"), str):
        expected = _create_signature(data["id"], data["checksum"], data.get("status", "READY"))
        ready_expected = _create_signature(data["id"], data["checksum"], "READY")
        if data["signature"] not in {expected, ready_expected}:
            errors.append("signature mismatch")
    return {
        "valid": not errors,
        "errors": errors,
        "id": data.get("id"),
    }


def _resume_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker: dict[str, Any] = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _find_handoff_read_only(root: Path, handoff_id: str) -> dict[str, Any]:
    validate_record_id(handoff_id, "handoff")
    matches: list[dict[str, Any]] = []
    for state in HANDOFF_STATES:
        path = handoff_path(root, state, handoff_id)
        if path.exists():
            try:
                handoff = read_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                return {
                    "state": state,
                    "path": str(path),
                    "handoff": None,
                    "blocker": _resume_blocker("handoff_unreadable", f"handoff record is unreadable: {exc}", path=str(path)),
                }
            matches.append({"state": state, "path": str(path), "handoff": handoff})
    if not matches:
        return {
            "state": None,
            "path": None,
            "handoff": None,
            "blocker": _resume_blocker("handoff_not_found", f"handoff not found: {handoff_id}"),
        }
    if len(matches) > 1:
        states = [match["state"] for match in matches]
        return {
            "state": "conflict",
            "path": None,
            "handoff": None,
            "matches": matches,
            "blocker": _resume_blocker(
                "handoff_state_conflict",
                f"handoff exists in multiple states: {handoff_id}",
                states=states,
            ),
        }
    return matches[0]


def _read_brake_read_only(root: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = brake_path(root)
    if not path.exists():
        return None, _resume_blocker("runtime_brake_missing", "runtime brake file is missing", path=str(path))
    try:
        brake = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, _resume_blocker("runtime_brake_invalid", f"runtime brake file is unreadable: {exc}", path=str(path))
    status = brake.get("status")
    if status not in {"DRIVE", "NEUTRAL", "PARK"}:
        return brake, _resume_blocker("runtime_brake_invalid", f"runtime brake status is invalid: {status}", path=str(path))
    return brake, None


def _clean_square_evidence(root: Path, handoff_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = root / "logs" / "clean-square" / f"{validate_record_id(handoff_id, 'handoff')}.json"
    evidence: dict[str, Any] = {"path": str(path), "present": path.exists(), "valid": False}
    blockers: list[dict[str, Any]] = []
    if not path.exists():
        blockers.append(_resume_blocker("clean_square_missing", "clean-square evidence is missing", path=str(path)))
        return evidence, blockers
    try:
        clean_square = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        blockers.append(_resume_blocker("clean_square_invalid", f"clean-square evidence is unreadable: {exc}", path=str(path)))
        return evidence, blockers
    checks = clean_square.get("checks") if isinstance(clean_square, dict) else None
    valid = (
        isinstance(clean_square, dict)
        and clean_square.get("handoff_id") == handoff_id
        and isinstance(checks, dict)
        and checks.get("handoff_written") is True
        and checks.get("signature_present") is True
        and checks.get("next_session_can_resume") is True
    )
    evidence.update(
        {
            "created_at": clean_square.get("created_at") if isinstance(clean_square, dict) else None,
            "handoff_status": clean_square.get("handoff_status") if isinstance(clean_square, dict) else None,
            "valid": valid,
        }
    )
    if not valid:
        blockers.append(_resume_blocker("clean_square_invalid", "clean-square evidence does not prove resumable handoff shutdown", path=str(path)))
    return evidence, blockers


def _approval_validity(root: Path, handoff: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    handoff_id = handoff.get("id")
    if not isinstance(handoff_id, str):
        return False, None
    path = approval_path(root, handoff_id)
    if not path.exists():
        return False, None
    try:
        approval = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return False, _resume_blocker("policy_evidence_invalid", f"approval record is unreadable: {exc}", path=str(path))
    if not isinstance(approval, dict):
        return False, _resume_blocker("policy_evidence_invalid", "approval record must be a JSON object", path=str(path))
    return (
        approval.get("status") == "APPROVED"
        and approval.get("handoff_id") == handoff_id
        and approval.get("handoff_checksum") == handoff.get("checksum")
        and approval.get("estimate_checksum") == handoff.get("estimate_checksum")
        and isinstance(approval.get("approved_by"), str)
        and bool(approval.get("approved_by"))
    ), None


def _malformed_policy_block(reason: str) -> dict[str, Any]:
    return _resume_blocker("policy_evidence_invalid", reason)


def _validate_resume_estimate(handoff: dict[str, Any], estimate: Any) -> dict[str, Any] | None:
    if not isinstance(estimate, dict):
        return _malformed_policy_block("handoff estimate must be an object")
    source = handoff.get("estimate_input")
    if not isinstance(source, dict):
        return _malformed_policy_block("handoff estimate input must be an object")
    task_type = source.get("task_type")
    if task_type not in TASK_TYPES:
        return _malformed_policy_block("handoff estimate input task_type is invalid")
    drivers = source.get("drivers")
    if not isinstance(drivers, list) or any(not isinstance(driver, str) for driver in drivers):
        return _malformed_policy_block("handoff estimate input drivers must be a list of strings")
    bucket = estimate.get("bucket")
    if bucket not in BUCKETS:
        return _malformed_policy_block("handoff estimate bucket is invalid")
    policy = estimate.get("policy")
    if not isinstance(policy, dict):
        return _malformed_policy_block("handoff estimate policy must be an object")
    expected_policy = policy_for_bucket(bucket)
    if policy.get("pickup_requires_approval") != expected_policy["pickup_requires_approval"]:
        return _malformed_policy_block("handoff estimate approval policy does not match bucket")
    if policy.get("action") != expected_policy["action"]:
        return _malformed_policy_block("handoff estimate action does not match bucket")
    title = handoff.get("title")
    message = handoff.get("message")
    if not isinstance(title, str) or not isinstance(message, str):
        return _malformed_policy_block("handoff title and message must be strings")
    if handoff.get("checksum") != _checksum_message(message):
        return _malformed_policy_block("handoff message checksum mismatch")
    try:
        canonical = estimate_task(title=title, message=message, task_type=task_type, drivers=drivers)
    except ValueError as exc:
        return _malformed_policy_block(f"handoff estimate cannot be recomputed: {exc}")
    for field in ("task_type", "bucket", "confidence", "score", "expected_minutes", "drivers", "uncertainty", "policy"):
        if estimate.get(field) != canonical[field]:
            return _malformed_policy_block(f"handoff estimate {field} does not match canonical estimate")
    expected_checksum = _checksum_estimate_binding(title=title, message=message, source=source, estimate=estimate)
    if handoff.get("estimate_checksum") != expected_checksum:
        return _malformed_policy_block("handoff estimate checksum mismatch")
    return None


def _policy_evidence(root: Path, handoff: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    evidence: dict[str, Any] = {
        "status": "missing",
        "approval_required": None,
        "approval_present": False,
    }
    if not isinstance(handoff, dict):
        return evidence, []
    if "estimate" not in handoff or handoff["estimate"] is None:
        blocker = _resume_blocker("policy_evidence_missing", "handoff estimate is required before resume")
        return evidence | {"status": "blocked"}, [blocker]
    estimate = handoff["estimate"]
    malformed = _validate_resume_estimate(handoff, estimate)
    if malformed:
        return evidence | {"status": "blocked"}, [malformed]
    policy = estimate.get("policy", {})
    approval_required = bool(policy.get("pickup_requires_approval"))
    if "self_evolution" in estimate.get("drivers", []):
        permissions = governance_permissions()
        if estimate.get("task_type") == "execution" or not permissions["may_propose_protocol_changes"]:
            evidence.update(
                {
                    "status": "blocked",
                    "bucket": estimate.get("bucket"),
                    "action": "self_evolution_propose_only",
                    "approval_required": True,
                    "approval_present": _approval_validity(root, handoff)[0],
                    "estimate_checksum": handoff.get("estimate_checksum"),
                }
            )
            return evidence, [
                _resume_blocker(
                    "policy_self_evolution_propose_only",
                    "self-evolution may propose protocol changes but cannot execute governance mutations",
                )
            ]
    if (
        estimate.get("task_type") == "discovery"
        and estimate.get("uncertainty", {}).get("level") == "high"
    ):
        approval_required = True
    approval_present, approval_blocker = _approval_validity(root, handoff)
    evidence.update(
        {
            "status": "verified",
            "bucket": estimate.get("bucket"),
            "action": policy.get("action"),
            "approval_required": approval_required,
            "approval_present": approval_present,
            "estimate_checksum": handoff.get("estimate_checksum"),
        }
    )
    if approval_blocker:
        return evidence | {"status": "blocked"}, [approval_blocker]
    if approval_required and not approval_present:
        return evidence | {"status": "blocked"}, [
            _resume_blocker("policy_approval_missing", "handoff pickup policy requires operator approval")
        ]
    return evidence, []


def _message_repo_field(handoff: dict[str, Any], label: str) -> str | None:
    message = handoff.get("message")
    if not isinstance(message, str):
        return None
    prefix = f"- {label}: "
    for line in message.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if value and value.lower() not in {"none", "unknown"}:
                return value
    return None


def _expected_repo_binding(root: Path, handoff: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(handoff, dict):
        return {}, []
    metadata = handoff.get("metadata") if isinstance(handoff.get("metadata"), dict) else {}
    binding = metadata.get("resume_snapshot") if isinstance(metadata.get("resume_snapshot"), dict) else {}
    signed_branch = _message_repo_field(handoff, "Branch")
    signed_head = _message_repo_field(handoff, "Head")
    blockers: list[dict[str, Any]] = []
    expected = {
        "repo": binding.get("repo", handoff.get("repo")),
        "branch": binding.get("branch", handoff.get("branch")),
        "head": binding.get("head"),
        "snapshot_id": binding.get("id"),
        "snapshot_path": binding.get("path"),
        "snapshot_checksum": binding.get("checksum"),
    }
    snapshot_id = expected.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        blockers.append(_resume_blocker("resume_snapshot_invalid", "resume snapshot id is missing"))
        return expected, blockers
    try:
        target = snapshot_path(root, snapshot_id)
    except ValueError as exc:
        blockers.append(_resume_blocker("resume_snapshot_invalid", f"resume snapshot id is invalid: {exc}"))
        return expected, blockers
    if expected.get("snapshot_path") != str(target):
        blockers.append(
            _resume_blocker(
                "resume_snapshot_invalid",
                "resume snapshot path does not match runtime snapshot path",
                expected=str(target),
                actual=expected.get("snapshot_path"),
            )
        )
    if not target.exists():
        blockers.append(_resume_blocker("resume_snapshot_invalid", "resume snapshot record is missing", path=str(target)))
        return expected, blockers
    try:
        snapshot = read_json(target)
    except (OSError, json.JSONDecodeError) as exc:
        blockers.append(_resume_blocker("resume_snapshot_invalid", f"resume snapshot record is unreadable: {exc}", path=str(target)))
        return expected, blockers
    if not isinstance(snapshot, dict):
        blockers.append(_resume_blocker("resume_snapshot_invalid", "resume snapshot record must be a JSON object", path=str(target)))
        return expected, blockers
    actual_checksum = _checksum_json(snapshot)
    if expected.get("snapshot_checksum") != actual_checksum:
        blockers.append(
            _resume_blocker(
                "resume_snapshot_invalid",
                "resume snapshot checksum does not match persisted snapshot",
                expected=expected.get("snapshot_checksum"),
                actual=actual_checksum,
            )
        )
    for field in ("repo", "branch", "head"):
        if expected.get(field) != snapshot.get(field):
            blockers.append(
                _resume_blocker(
                    "resume_snapshot_invalid",
                    f"resume snapshot {field} does not match persisted snapshot",
                    expected=snapshot.get(field),
                    actual=expected.get(field),
                )
            )
    if signed_branch is not None and snapshot.get("branch") != signed_branch:
        blockers.append(
            _resume_blocker(
                "resume_snapshot_invalid",
                "resume snapshot branch does not match signed handoff message",
                expected=signed_branch,
                actual=snapshot.get("branch"),
            )
        )
    if signed_head is not None and snapshot.get("head") != signed_head:
        blockers.append(
            _resume_blocker(
                "resume_snapshot_invalid",
                "resume snapshot head does not match signed handoff message",
                expected=signed_head,
                actual=snapshot.get("head"),
            )
        )
    return {
        "repo": snapshot.get("repo"),
        "branch": snapshot.get("branch"),
        "head": snapshot.get("head"),
        "snapshot_id": snapshot.get("id"),
        "snapshot_path": str(target),
        "snapshot_checksum": actual_checksum,
    }, blockers


def _repo_resume_evidence(cwd: Path, expected: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if not expected.get("branch") or not expected.get("head"):
        blockers.append(_resume_blocker("handoff_repo_evidence_missing", "handoff is missing resume branch or head evidence"))
    try:
        current = current_repo_evidence(cwd)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "cwd": str(Path(cwd).expanduser().resolve(strict=False)),
            "expected_branch": expected.get("branch"),
            "expected_head": expected.get("head"),
            "inspection_error": str(exc),
        }, [_resume_blocker("repo_inspection_failed", f"could not inspect repo state: {exc}")]
    evidence = {
        "cwd": current["cwd"],
        "expected_repo": expected.get("repo"),
        "expected_branch": expected.get("branch"),
        "expected_head": expected.get("head"),
        "current_branch": current["branch"],
        "current_head": current["head"],
        "dirty_worktree": current["dirty_worktree"],
        "snapshot_id": expected.get("snapshot_id"),
        "snapshot_path": expected.get("snapshot_path"),
        "snapshot_checksum": expected.get("snapshot_checksum"),
    }
    if expected.get("branch") and current["branch"] != expected["branch"]:
        blockers.append(
            _resume_blocker(
                "repo_branch_mismatch",
                "current repo branch does not match handoff branch",
                expected=expected.get("branch"),
                actual=current["branch"],
            )
        )
    if expected.get("head") and current["head"] != expected["head"]:
        blockers.append(
            _resume_blocker(
                "repo_head_mismatch",
                "current repo head does not match handoff head",
                expected=expected.get("head"),
                actual=current["head"],
            )
        )
    if current["dirty_worktree"]:
        blockers.append(_resume_blocker("dirty_worktree", "current repo worktree is dirty"))
    return evidence, blockers


def _active_epoch_evidence(root: Path, expected: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    try:
        records = read_active_epoch_records(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"count": None, "epochs": []}, [
            _resume_blocker("active_epoch_invalid", f"active epoch records are invalid or unreadable: {exc}")
        ]
    evidence: dict[str, Any] = {"count": len(records), "epochs": []}
    if len(records) > 1:
        blockers.append(
            _resume_blocker(
                "active_epoch_conflict",
                f"expected at most one active epoch, found {len(records)}",
                paths=[str(path) for path, _epoch in records],
            )
        )
        return evidence, blockers
    if not records:
        return evidence, blockers
    path, epoch = records[0]
    if not isinstance(epoch, dict):
        evidence["epochs"].append({"path": str(path), "status": None})
        blockers.append(_resume_blocker("active_epoch_invalid", "active epoch record must be a JSON object", path=str(path)))
        return evidence, blockers
    snapshot_before_value = epoch.get("snapshot_before")
    snapshot_before = snapshot_before_value if isinstance(snapshot_before_value, dict) else {}
    item = {
        "id": epoch.get("id"),
        "path": str(path),
        "status": epoch.get("status"),
        "repo": epoch.get("repo"),
        "branch": epoch.get("branch"),
        "snapshot_before_id": snapshot_before.get("id"),
        "snapshot_before_head": snapshot_before.get("head"),
    }
    evidence["epochs"].append(item)
    if (
        epoch.get("status") != "ACTIVE"
        or not isinstance(epoch.get("id"), str)
        or epoch.get("id") != path.stem
        or not isinstance(snapshot_before_value, dict)
        or (expected.get("head") and not isinstance(snapshot_before.get("head"), str))
    ):
        blockers.append(_resume_blocker("active_epoch_invalid", "active epoch record is malformed", path=str(path)))
    if expected.get("repo") and epoch.get("repo") != expected.get("repo"):
        blockers.append(
            _resume_blocker(
                "active_epoch_repo_mismatch",
                "active epoch repo does not match handoff repo",
                expected=expected.get("repo"),
                actual=epoch.get("repo"),
            )
        )
    if expected.get("branch") and epoch.get("branch") != expected.get("branch"):
        blockers.append(
            _resume_blocker(
                "active_epoch_branch_mismatch",
                "active epoch branch does not match handoff branch",
                expected=expected.get("branch"),
                actual=epoch.get("branch"),
            )
        )
    if expected.get("head") and isinstance(snapshot_before.get("head"), str) and snapshot_before.get("head") != expected.get("head"):
        blockers.append(
            _resume_blocker(
                "active_epoch_head_mismatch",
                "active epoch baseline head does not match handoff head",
                expected=expected.get("head"),
                actual=snapshot_before.get("head"),
            )
        )
    return evidence, blockers


def _resume_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "resume_work"
    codes = [blocker.get("code") for blocker in blockers]
    if "runtime_brake_missing" in codes or "runtime_brake_invalid" in codes:
        return "inspect_runtime_state"
    if "active_brake_stop" in codes:
        return "clear_brake"
    if "dirty_worktree" in codes:
        return "clean_worktree"
    if "handoff_claimed_by_other" in codes or "handoff_state_conflict" in codes:
        return "resolve_claim_conflict"
    if "policy_approval_missing" in codes:
        return "approve_handoff"
    if "handoff_not_claimed" in codes:
        return "claim_handoff"
    if any(code in codes for code in ("active_epoch_conflict", "active_epoch_invalid", "active_epoch_repo_mismatch", "active_epoch_branch_mismatch", "active_epoch_head_mismatch")):
        return "close_or_fail_active_epoch"
    if any(code in codes for code in ("repo_branch_mismatch", "repo_head_mismatch", "resume_snapshot_invalid", "handoff_unreadable", "handoff_signature_invalid", "handoff_checksum_mismatch", "handoff_protocol_unsupported", "handoff_repo_evidence_missing", "clean_square_missing", "clean_square_invalid", "policy_evidence_invalid", "policy_evidence_missing", "policy_self_evolution_propose_only")):
        return "recreate_handoff"
    return "inspect_resume_blockers"


def verify_resume(
    *,
    root: Path,
    cwd: Path,
    handoff_id: str,
    claimer: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    blockers: list[dict[str, Any]] = []

    found = _find_handoff_read_only(root, handoff_id)
    handoff = found.get("handoff") if isinstance(found.get("handoff"), dict) else None
    handoff_summary = {
        "id": handoff_id,
        "state": found.get("state"),
        "path": found.get("path"),
        "status": handoff.get("status") if isinstance(handoff, dict) else None,
        "claimed_by": handoff.get("claimed_by") if isinstance(handoff, dict) else None,
    }
    if found.get("blocker"):
        blockers.append(found["blocker"])
    if isinstance(handoff, dict):
        validation = _validate_handoff_record(handoff)
        handoff_summary["signature_valid"] = validation["valid"]
        handoff_summary["validation_errors"] = validation["errors"]
        for error in validation["errors"]:
            code = "handoff_signature_invalid"
            if error == "checksum mismatch":
                code = "handoff_checksum_mismatch"
            elif error.startswith("unsupported protocol_version"):
                code = "handoff_protocol_unsupported"
            blockers.append(_resume_blocker(code, error))
        if found.get("state") != "claimed":
            blockers.append(
                _resume_blocker(
                    "handoff_not_claimed",
                    f"handoff must be claimed before resume; current state is {found.get('state')}",
                    state=found.get("state"),
                )
            )
        if found.get("state") == "claimed" and handoff.get("status") != "CLAIMED":
            blockers.append(
                _resume_blocker(
                    "handoff_not_claimed",
                    "claimed handoff record status must be CLAIMED",
                    status=handoff.get("status"),
                )
            )
        claimed_by = handoff.get("claimed_by")
        if found.get("state") == "claimed" and (not isinstance(claimed_by, str) or not claimed_by.strip()):
            blockers.append(
                _resume_blocker(
                    "handoff_not_claimed",
                    "claimed handoff record must include claimed_by",
                )
            )
        if claimer is not None and handoff.get("claimed_by") != claimer:
            blockers.append(
                _resume_blocker(
                    "handoff_claimed_by_other",
                    "handoff is claimed by a different claimer",
                    expected=claimer,
                    actual=handoff.get("claimed_by"),
                )
            )
    else:
        handoff_summary["signature_valid"] = False
        handoff_summary["validation_errors"] = []

    clean_square, clean_blockers = _clean_square_evidence(root, handoff_id)
    blockers.extend(clean_blockers)

    brake, brake_blocker = _read_brake_read_only(root)
    cadence = {
        "brake_status": brake.get("status") if isinstance(brake, dict) else None,
        "state": _cadence_state(brake)["state"] if isinstance(brake, dict) and brake.get("status") in {"DRIVE", "NEUTRAL", "PARK"} else None,
        "can_start_work": brake.get("status") == "DRIVE" if isinstance(brake, dict) else False,
        "requires_operator_resume": brake.get("status") == "PARK" if isinstance(brake, dict) else False,
        "brake": brake,
    }
    if brake_blocker:
        blockers.append(brake_blocker)
    elif brake and brake.get("status") != "DRIVE":
        blockers.append(
            _resume_blocker(
                "active_brake_stop",
                f"Cadence state blocks resume because brake is {brake.get('status')}",
                brake_status=brake.get("status"),
            )
        )

    expected_repo, resume_snapshot_blockers = _expected_repo_binding(root, handoff)
    blockers.extend(resume_snapshot_blockers)
    repository, repo_blockers = _repo_resume_evidence(cwd, expected_repo)
    blockers.extend(repo_blockers)

    active_epoch, epoch_blockers = _active_epoch_evidence(root, expected_repo)
    blockers.extend(epoch_blockers)

    policy_evidence, policy_blockers = _policy_evidence(root, handoff)
    blockers.extend(policy_blockers)

    recommended_next_action = _resume_recommendation(blockers)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": RESUME_VERIFICATION_SCHEMA_VERSION,
        "packet": "resume_verification",
        "handoff_id": handoff_id,
        "resumable": not blockers,
        "read_only": True,
        "handoff": handoff_summary,
        "clean_square": clean_square,
        "repository": repository,
        "cadence": cadence,
        "active_epoch": active_epoch,
        "policy_evidence": policy_evidence,
        "blockers": blockers,
        "recommended_next_action": recommended_next_action,
    }


def _nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _resume_verification_freshness(
    path: Path,
    *,
    max_age_minutes: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "path": str(path),
        "max_age_minutes": max_age_minutes,
        "mtime": None,
        "age_seconds": None,
        "fresh": False,
    }
    try:
        stat = path.stat()
    except OSError as exc:
        blockers.append(
            _resume_blocker(
                "resume_verification_file_unreadable",
                f"resume verification file could not be statted: {exc}",
                path=str(path),
            )
        )
        return evidence, blockers

    now = datetime.now(timezone.utc)
    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    age_seconds = (now - mtime).total_seconds()
    evidence.update(
        {
            "mtime": mtime.isoformat().replace("+00:00", "Z"),
            "age_seconds": round(age_seconds, 3),
            "fresh": True,
        }
    )
    if age_seconds < -60:
        evidence["fresh"] = False
        blockers.append(
            _resume_blocker(
                "resume_verification_from_future",
                "resume verification file mtime is in the future",
                path=str(path),
                age_seconds=round(age_seconds, 3),
            )
        )
    if max_age_minutes is not None and age_seconds > max_age_minutes * 60:
        evidence["fresh"] = False
        blockers.append(
            _resume_blocker(
                "resume_verification_stale",
                "resume verification packet is older than the allowed freshness window",
                path=str(path),
                age_seconds=round(age_seconds, 3),
                max_age_minutes=max_age_minutes,
            )
        )
    return evidence, blockers


def _resume_verification_load_error_packet(
    *,
    root: Path,
    cwd: Path,
    resume_verification_file: Path,
    claimer: str | None,
    max_resume_age_minutes: int | None,
    blockers: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": RESUME_CONTINUATION_SCHEMA_VERSION,
        "packet": "resume_continuation",
        "handoff_id": None,
        "claimer": claimer,
        "valid": False,
        "continuable": False,
        "read_only": True,
        "executor_started": False,
        "epoch_started": False,
        "pr_action_started": False,
        "resume_verification": {
            "path": str(resume_verification_file),
            "checksum": None,
            "schema_version": None,
            "packet": None,
            "resumable": None,
            "recommended_next_action": None,
            "freshness": freshness
            or {
                "path": str(resume_verification_file),
                "max_age_minutes": max_resume_age_minutes,
                "mtime": None,
                "age_seconds": None,
                "fresh": False,
            },
        },
        "fresh_resume_verification": None,
        "checks": {
            "saved_packet_shape_valid": False,
            "saved_packet_resumable": False,
            "saved_packet_fresh": False,
            "fresh_packet_resumable": False,
            "anchors_match": False,
        },
        "blockers": blockers,
        "recommended_next_action": "inspect_resume_blockers",
        "side_effects": [],
        "limitations": [
            "new_session_launch_out_of_scope",
            "handoff_claim_out_of_scope",
            "executor_invocation_out_of_scope",
            "git_pr_writes_out_of_scope",
            "merge_release_publish_out_of_scope",
        ],
        "repo": {"cwd": str(Path(cwd).expanduser().resolve(strict=False))},
        "root": str(Path(root).expanduser().resolve(strict=False)),
    }


def _resume_continuation_recommendation(
    blockers: list[dict[str, Any]],
    *,
    saved_recommendation: str | None,
    fresh_recommendation: str | None,
) -> str:
    if not blockers:
        return "start_governed_execution"
    codes = [blocker.get("code") for blocker in blockers]
    if "resume_verification_not_resumable" in codes:
        recovery_actions = RESUME_CONTINUATION_ACTIONS - {"start_governed_execution"}
        for action in (saved_recommendation, fresh_recommendation):
            if action in recovery_actions:
                return action
    if "policy_approval_missing" in codes:
        return "approve_handoff"
    if "handoff_not_claimed" in codes:
        return "claim_handoff"
    if any(
        code in codes
        for code in (
            "active_epoch_exists",
            "active_epoch_conflict",
            "active_epoch_invalid",
            "active_epoch_repo_mismatch",
            "active_epoch_branch_mismatch",
            "active_epoch_head_mismatch",
        )
    ):
        return "close_or_fail_active_epoch"
    if any(
        code in codes
        for code in (
            "repo_branch_mismatch",
            "repo_head_mismatch",
            "resume_snapshot_invalid",
            "handoff_unreadable",
            "handoff_signature_invalid",
            "handoff_checksum_mismatch",
            "handoff_protocol_unsupported",
            "handoff_repo_evidence_missing",
            "clean_square_missing",
            "clean_square_invalid",
            "policy_evidence_invalid",
            "policy_evidence_missing",
            "policy_self_evolution_propose_only",
        )
    ):
        return "recreate_handoff"
    return "inspect_resume_blockers"


def _resume_anchor_mismatches(saved_packet: dict[str, Any], fresh_packet: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for label, path in _RESUME_CONTINUATION_ANCHORS:
        saved_value = _nested_value(saved_packet, path)
        fresh_value = _nested_value(fresh_packet, path)
        if saved_value != fresh_value:
            mismatches.append(
                {
                    "field": label,
                    "expected": saved_value,
                    "actual": fresh_value,
                }
            )
    return mismatches


def _saved_resume_claimer(packet: dict[str, Any]) -> str | None:
    handoff = packet.get("handoff") if isinstance(packet.get("handoff"), dict) else {}
    claimed_by = handoff.get("claimed_by")
    return claimed_by if isinstance(claimed_by, str) and claimed_by.strip() else None


def _fresh_resume_summary(packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(packet, dict):
        return None
    return {
        "checksum": _checksum_json(packet),
        "schema_version": packet.get("schema_version"),
        "packet": packet.get("packet"),
        "resumable": packet.get("resumable"),
        "recommended_next_action": packet.get("recommended_next_action"),
        "blocker_codes": [
            blocker.get("code")
            for blocker in packet.get("blockers", [])
            if isinstance(blocker, dict)
        ],
    }


def resume_continuation(
    *,
    root: Path,
    cwd: Path,
    resume_verification_file: Path,
    claimer: str | None = None,
    max_resume_age_minutes: int | None = DEFAULT_RESUME_CONTINUATION_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    resume_verification_file = Path(resume_verification_file)
    blockers: list[dict[str, Any]] = []
    freshness, freshness_blockers = _resume_verification_freshness(
        resume_verification_file,
        max_age_minutes=max_resume_age_minutes,
    )
    blockers.extend(freshness_blockers)
    if any(blocker.get("code") == "resume_verification_file_unreadable" for blocker in freshness_blockers):
        return _resume_verification_load_error_packet(
            root=root,
            cwd=cwd,
            resume_verification_file=resume_verification_file,
            claimer=claimer,
            max_resume_age_minutes=max_resume_age_minutes,
            freshness=freshness,
            blockers=blockers,
        )

    try:
        saved_packet = read_json(resume_verification_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append(
            _resume_blocker(
                "resume_verification_file_unreadable",
                f"resume verification file could not be read: {exc}",
                path=str(resume_verification_file),
            )
        )
        return _resume_verification_load_error_packet(
            root=root,
            cwd=cwd,
            resume_verification_file=resume_verification_file,
            claimer=claimer,
            max_resume_age_minutes=max_resume_age_minutes,
            freshness=freshness,
            blockers=blockers,
        )

    if not isinstance(saved_packet, dict):
        blockers.append(
            _resume_blocker(
                "resume_verification_invalid",
                "resume verification packet must be a JSON object",
                path=str(resume_verification_file),
            )
        )
        return _resume_verification_load_error_packet(
            root=root,
            cwd=cwd,
            resume_verification_file=resume_verification_file,
            claimer=claimer,
            max_resume_age_minutes=max_resume_age_minutes,
            freshness=freshness,
            blockers=blockers,
        )

    saved_checksum = _checksum_json(saved_packet)
    saved_schema = saved_packet.get("schema_version")
    saved_packet_kind = saved_packet.get("packet")
    saved_resumable = saved_packet.get("resumable")
    saved_recommendation = saved_packet.get("recommended_next_action")
    saved_handoff_id = saved_packet.get("handoff_id")
    saved_claimed_by = _saved_resume_claimer(saved_packet)
    expected_claimer = claimer or saved_claimed_by

    if saved_schema != RESUME_VERIFICATION_SCHEMA_VERSION or saved_packet_kind != "resume_verification":
        blockers.append(
            _resume_blocker(
                "resume_verification_schema_unsupported",
                "resume continuation requires a saved resume-verification.v1 packet",
                expected_schema=RESUME_VERIFICATION_SCHEMA_VERSION,
                actual_schema=saved_schema,
                actual_packet=saved_packet_kind,
            )
        )
    if not isinstance(saved_handoff_id, str) or not saved_handoff_id.strip():
        blockers.append(_resume_blocker("resume_handoff_id_missing", "resume verification packet is missing handoff_id"))
        saved_handoff_id = None
    else:
        try:
            validate_record_id(saved_handoff_id, "handoff")
        except ValueError as exc:
            blockers.append(_resume_blocker("resume_handoff_id_invalid", f"resume verification handoff_id is invalid: {exc}", handoff_id=saved_handoff_id))
            saved_handoff_id = None
    handoff_summary = saved_packet.get("handoff") if isinstance(saved_packet.get("handoff"), dict) else {}
    handoff_section_id = handoff_summary.get("id")
    if isinstance(saved_handoff_id, str) and isinstance(handoff_section_id, str) and handoff_section_id != saved_handoff_id:
        blockers.append(
            _resume_blocker(
                "resume_handoff_id_mismatch",
                "resume verification handoff section id does not match packet handoff_id",
                expected=saved_handoff_id,
                actual=handoff_section_id,
            )
        )
    if saved_resumable is not True:
        blockers.append(
            _resume_blocker(
                "resume_verification_not_resumable",
                "saved resume verification packet is not resumable",
                recommended_next_action=saved_recommendation,
            )
        )
    if saved_resumable is True and expected_claimer is None:
        blockers.append(
            _resume_blocker(
                "resume_claimer_missing",
                "resumable packet must include a claimed_by value or a --claimer",
            )
        )
    if claimer is not None and saved_claimed_by is not None and claimer != saved_claimed_by:
        blockers.append(
            _resume_blocker(
                "resume_claimer_mismatch",
                "requested claimer does not match saved resume verification claimer",
                expected=saved_claimed_by,
                actual=claimer,
            )
        )

    fresh_packet: dict[str, Any] | None = None
    if isinstance(saved_handoff_id, str):
        try:
            fresh_packet = verify_resume(
                root=root,
                cwd=cwd,
                handoff_id=saved_handoff_id,
                claimer=expected_claimer,
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(
                _resume_blocker(
                    "resume_recheck_failed",
                    f"fresh resume verification could not be computed: {exc}",
                )
            )
    if isinstance(fresh_packet, dict):
        blockers.extend(
            blocker
            for blocker in fresh_packet.get("blockers", [])
            if isinstance(blocker, dict)
        )
        fresh_codes = {
            blocker.get("code")
            for blocker in fresh_packet.get("blockers", [])
            if isinstance(blocker, dict)
        }
        fresh_active_epoch = fresh_packet.get("active_epoch") if isinstance(fresh_packet.get("active_epoch"), dict) else {}
        active_epoch_count = fresh_active_epoch.get("count")
        if (
            isinstance(active_epoch_count, int)
            and active_epoch_count > 0
            and not any(
                code in fresh_codes
                for code in (
                    "active_epoch_conflict",
                    "active_epoch_invalid",
                    "active_epoch_repo_mismatch",
                    "active_epoch_branch_mismatch",
                    "active_epoch_head_mismatch",
                )
            )
        ):
            blockers.append(
                _resume_blocker(
                    "active_epoch_exists",
                    "an active epoch already exists before governed execution start",
                    count=active_epoch_count,
                    epochs=fresh_active_epoch.get("epochs", []),
                )
            )
        mismatches = _resume_anchor_mismatches(saved_packet, fresh_packet)
        if mismatches:
            blockers.append(
                _resume_blocker(
                    "resume_verification_anchor_mismatch",
                    "saved resume verification anchors no longer match fresh runtime evidence",
                    mismatches=mismatches,
                )
            )

    fresh_recommendation = fresh_packet.get("recommended_next_action") if isinstance(fresh_packet, dict) else None
    recommended_next_action = _resume_continuation_recommendation(
        blockers,
        saved_recommendation=saved_recommendation if isinstance(saved_recommendation, str) else None,
        fresh_recommendation=fresh_recommendation if isinstance(fresh_recommendation, str) else None,
    )
    valid = not blockers
    checks = {
        "saved_packet_shape_valid": (
            saved_schema == RESUME_VERIFICATION_SCHEMA_VERSION
            and saved_packet_kind == "resume_verification"
            and isinstance(saved_packet.get("handoff_id"), str)
        ),
        "saved_packet_resumable": saved_resumable is True,
        "saved_packet_fresh": freshness.get("fresh") is True,
        "fresh_packet_resumable": fresh_packet.get("resumable") is True if isinstance(fresh_packet, dict) else False,
        "anchors_match": valid or (
            isinstance(fresh_packet, dict)
            and not any(blocker.get("code") == "resume_verification_anchor_mismatch" for blocker in blockers)
        ),
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": RESUME_CONTINUATION_SCHEMA_VERSION,
        "packet": "resume_continuation",
        "handoff_id": saved_packet.get("handoff_id"),
        "claimer": expected_claimer,
        "valid": valid,
        "continuable": valid,
        "read_only": True,
        "executor_started": False,
        "epoch_started": False,
        "pr_action_started": False,
        "resume_verification": {
            "path": str(resume_verification_file),
            "checksum": saved_checksum,
            "schema_version": saved_schema,
            "packet": saved_packet_kind,
            "resumable": saved_resumable,
            "recommended_next_action": saved_recommendation,
            "freshness": freshness,
        },
        "fresh_resume_verification": _fresh_resume_summary(fresh_packet),
        "checks": checks,
        "blockers": blockers,
        "recommended_next_action": recommended_next_action,
        "side_effects": [],
        "limitations": [
            "new_session_launch_out_of_scope",
            "handoff_claim_out_of_scope",
            "executor_invocation_out_of_scope",
            "git_pr_writes_out_of_scope",
            "merge_release_publish_out_of_scope",
        ],
        "repo": {"cwd": str(cwd.expanduser().resolve(strict=False))},
        "root": str(root.expanduser().resolve(strict=False)),
    }


def _create_clean_square_record(root: Path, handoff: dict[str, Any], summary: str) -> dict[str, Any]:
    handoff_id = validate_record_id(str(handoff.get("id")), "handoff")
    now = utc_now()
    target = root / "logs" / "clean-square" / f"{handoff_id}.json"
    data = {
        "handoff_id": handoff_id,
        "handoff_status": handoff.get("status"),
        "summary": summary,
        "created_at": now,
        "path": str(target),
        "checks": {
            "handoff_written": True,
            "signature_present": bool(handoff.get("signature")),
            "next_session_can_resume": handoff.get("status") in {"READY", "CLAIMED", "COMPLETED"},
        },
    }
    _write_json_once(target, data)
    return data


def _write_json_once(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        if fd is not None:
            os.close(fd)
        if created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def _handoff_conflicts(root: Path, handoff_id: str) -> list[str]:
    conflicts = []
    for state in HANDOFF_STATES:
        if handoff_path(root, state, handoff_id).exists():
            conflicts.append(state)
    return conflicts


def _ensure_handoff_id_available(root: Path, handoff_id: str) -> None:
    conflicts = _handoff_conflicts(root, handoff_id)
    if conflicts:
        states = ", ".join(conflicts)
        raise FileExistsError(f"handoff already exists: {handoff_id} ({states})")


def build_seed_message(
    *,
    title: str,
    summary: str,
    guardrail: str,
    snapshot: dict[str, Any],
    status_payload: dict[str, Any],
    remote_url: str | None,
    next_actions: list[str],
) -> str:
    cadence = status_payload.get("cadence", {})
    counts = status_payload.get("counts", {})
    actions = next_actions or [
        "Run python scripts\\cadence.py status and confirm Cadence is PLAY_ON.",
        "Run python scripts\\cadence.py next-handoff and inspect this seed.",
        "Claim the handoff only if pickup policy allows it.",
    ]

    lines = [
        "Seed for new Codex context window:",
        "",
        f"Task: {title}",
        f"Guardrail: {guardrail}",
        f"Summary: {summary}",
        "",
        "Repository state:",
        f"- Path: {snapshot.get('cwd')}",
        f"- Repo: {snapshot.get('repo')}",
        f"- Remote: {remote_url or 'unknown'}",
        f"- Branch: {snapshot.get('branch')}",
        f"- Head: {snapshot.get('head')}",
        f"- Dirty worktree: {_format_bool(snapshot.get('dirty_worktree'))}",
        f"- Repo confidence: {snapshot.get('repo_confidence')}",
        f"- Repo confidence drivers: {_join_drivers(snapshot.get('repo_confidence_drivers'))}",
        f"- Snapshot: {snapshot.get('path')}",
        "",
        "Cadence state:",
        f"- Runtime root: {status_payload.get('root')}",
        f"- State: {cadence.get('state')}",
        f"- Legacy brake: {cadence.get('legacy_brake')}",
        f"- Ready handoffs: {counts.get('ready')}",
        f"- Claimed handoffs: {counts.get('claimed')}",
        f"- Completed handoffs: {counts.get('completed')}",
        f"- Failed handoffs: {counts.get('failed')}",
        "",
        "New session first actions:",
    ]
    lines.extend(f"{index}. {action}" for index, action in enumerate(actions, start=1))
    lines.extend(
        [
            "",
            "Operating rules:",
            "- Do not auto-merge without explicit operator instruction.",
            "- Do not spend elected review unless the current guardrail allows it.",
            "- Keep the continuation PR-sized and bounded to the handoff objective.",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare_handoff(
    *,
    root: Path,
    cwd: Path,
    handoff_id: str,
    title: str,
    guardrail: str,
    repo: str | None,
    branch: str | None,
    task_type: str,
    drivers: list[str],
    summary: str,
    ci_status: str,
    next_actions: list[str],
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    ensure_layout(root)
    target = handoff_path(root, "ready", handoff_id)
    _ensure_handoff_id_available(root, handoff_id)

    with exclusive_lock(lock_path(root, "runtime")):
        _ensure_handoff_id_available(root, handoff_id)
        brake = read_brake(root)
        status_payload = _status_payload(root, brake)
        if not status_payload["cadence"]["can_start_work"]:
            raise ValueError(f"prepare-handoff requires Cadence PLAY_ON; current state is {status_payload['cadence']['state']}")

        snapshot = snapshot_repo(cwd, repo=repo, ci_status=ci_status)
        snapshot["id"] = _snapshot_id(snapshot, repo)
        snapshot_target = snapshot_path(root, snapshot["id"])
        if snapshot_target.exists():
            raise FileExistsError(f"snapshot already exists: {snapshot['id']}")
        snapshot["path"] = str(snapshot_target)
        atomic_write_json(snapshot_target, snapshot)

        remote_url = discover_remote_url(cwd)
        message = build_seed_message(
            title=title,
            summary=summary,
            guardrail=guardrail,
            snapshot=snapshot,
            status_payload=status_payload,
            remote_url=remote_url,
            next_actions=next_actions,
        )
        source = {"task_type": task_type, "drivers": list(drivers or [])}
        estimate = estimate_task(
            title=title,
            message=message,
            task_type=task_type,
            drivers=drivers or [],
        )
        now = utc_now()
        checksum = _checksum_message(message)
        handoff = {
            "protocol_version": PROTOCOL_VERSION,
            "id": handoff_id,
            "title": title,
            "status": "READY",
            "guardrail": guardrail,
            "repo": repo,
            "branch": branch or snapshot.get("branch"),
            "created_at": now,
            "updated_at": now,
            "metadata": {"resume_snapshot": _resume_snapshot_binding(snapshot)},
            "checksum": checksum,
            "signature": _create_signature(handoff_id, checksum),
            "message": message,
            "estimate": estimate,
            "estimate_input": source,
            "estimate_checksum": _checksum_estimate_binding(
                title=title,
                message=message,
                source=source,
                estimate=estimate,
            ),
        }

        validation = _validate_handoff_record(handoff)
        if not validation["valid"]:
            raise ValueError(f"generated handoff failed validation: {validation['errors']}")
        clean_square: dict[str, Any] | None = None
        published = False
        try:
            clean_square = _create_clean_square_record(root, handoff, summary)
            _ensure_handoff_id_available(root, handoff_id)
            _write_json_once(target, handoff)
            published = True
            persisted = read_json(target)
            validation = _validate_handoff_record(persisted)
            if not validation["valid"]:
                raise ValueError(f"generated handoff failed validation: {validation['errors']}")
        except Exception:
            if published:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            if clean_square and clean_square.get("path"):
                try:
                    Path(str(clean_square["path"])).unlink()
                except FileNotFoundError:
                    pass
            raise

    return {
        "handoff": persisted,
        "snapshot": snapshot,
        "validation": validation,
        "clean_square": clean_square,
        "stop_current_session": True,
    }
