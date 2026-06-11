from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.repo_state import current_repo_evidence
from codex_cadence.store import (
    WORK_OWNERSHIP_STATES,
    atomic_write_json,
    read_json,
    utc_now,
    validate_record_id,
    work_ownership_path,
    work_ownership_state_dir,
)

WORK_OWNERSHIP_SCHEMA_VERSION = "work-ownership.v1"
WORK_OWNERSHIP_STATUS_SCHEMA_VERSION = "work-ownership-status.v1"
WORK_OWNERSHIP_VALIDATION_SCHEMA_VERSION = "work-ownership-validation.v1"
WORK_OWNERSHIP_CLAIM_SCHEMA_VERSION = "work-ownership-claim.v1"
WORK_OWNERSHIP_CLOSEOUT_SCHEMA_VERSION = "work-ownership-closeout.v1"
WORK_OWNERSHIP_STATUSES = ("ACTIVE", "CLOSED", "FAILED")
ACTIVE_WORK_OWNERSHIP_STATUS = "ACTIVE"
DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES = 24 * 60


def ownership_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _parse_utc_timestamp(value: Any, field: str, path: Path | None, blockers: list[dict[str, Any]]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        blockers.append(
            ownership_blocker(
                "ownership_timestamp_invalid",
                f"{field} must be an ISO-8601 UTC timestamp",
                field=field,
                path=str(path) if path else None,
            )
        )
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        blockers.append(
            ownership_blocker(
                "ownership_timestamp_invalid",
                f"{field} must be an ISO-8601 UTC timestamp",
                field=field,
                path=str(path) if path else None,
            )
        )
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        blockers.append(
            ownership_blocker(
                "ownership_timestamp_invalid",
                f"{field} must be an ISO-8601 UTC timestamp",
                field=field,
                path=str(path) if path else None,
            )
        )
        return None
    return parsed.astimezone(timezone.utc)


def _required_string(record: dict[str, Any], field: str, path: Path | None) -> list[dict[str, Any]]:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        return [
            ownership_blocker(
                "ownership_required_field_missing",
                f"{field} is required",
                field=field,
                path=str(path) if path else None,
            )
        ]
    return []


def _validate_optional_id(record: dict[str, Any], field: str, kind: str, path: Path | None) -> list[dict[str, Any]]:
    value = record.get(field)
    if value in (None, ""):
        return []
    if not isinstance(value, str):
        return [
            ownership_blocker(
                "ownership_field_type_invalid",
                f"{field} must be a string when present",
                field=field,
                path=str(path) if path else None,
            )
        ]
    try:
        validate_record_id(value, kind)
    except ValueError as exc:
        return [
            ownership_blocker(
                "ownership_id_invalid",
                str(exc),
                field=field,
                path=str(path) if path else None,
            )
        ]
    return []


def ownership_record_summary(record: Any, path: Path | None = None, state: str | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path) if path else None,
        "state": state,
        "schema_version": record.get("schema_version") if isinstance(record, dict) else None,
        "id": record.get("id") if isinstance(record, dict) else None,
        "task_id": record.get("task_id") if isinstance(record, dict) else None,
        "candidate_id": record.get("candidate_id") if isinstance(record, dict) else None,
        "role": record.get("role") if isinstance(record, dict) else None,
        "claimer": record.get("claimer") if isinstance(record, dict) else None,
        "repo": record.get("repo") if isinstance(record, dict) else None,
        "branch": record.get("branch") if isinstance(record, dict) else None,
        "head": record.get("head") if isinstance(record, dict) else None,
        "pr_number": record.get("pr_number") if isinstance(record, dict) else None,
        "epoch_id": record.get("epoch_id") if isinstance(record, dict) else None,
        "handoff_id": record.get("handoff_id") if isinstance(record, dict) else None,
        "status": record.get("status") if isinstance(record, dict) else None,
        "created_at": record.get("created_at") if isinstance(record, dict) else None,
        "updated_at": record.get("updated_at") if isinstance(record, dict) else None,
    }
    closeout = record.get("closeout") if isinstance(record, dict) and isinstance(record.get("closeout"), dict) else {}
    for field in ("executor_closeout_file", "executor_closeout_checksum", "executor_closeout_status"):
        if closeout.get(field) is not None:
            summary[field] = closeout.get(field)
    return summary


def validate_work_ownership_record(
    record: Any,
    *,
    path: Path | None = None,
    state: str | None = None,
    expected_id: str | None = None,
    expected_repo: str | None = None,
    expected_branch: str | None = None,
    expected_task_id: str | None = None,
    require_active: bool = False,
    max_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(record, dict):
        return [
            ownership_blocker(
                "ownership_record_invalid",
                "work ownership record must be a JSON object",
                path=str(path) if path else None,
            )
        ]

    if record.get("schema_version") != WORK_OWNERSHIP_SCHEMA_VERSION:
        blockers.append(
            ownership_blocker(
                "ownership_schema_unsupported",
                "work ownership record schema is unsupported",
                expected_schema=WORK_OWNERSHIP_SCHEMA_VERSION,
                actual_schema=record.get("schema_version"),
                path=str(path) if path else None,
            )
        )

    for field in ("id", "task_id", "candidate_id", "role", "claimer", "repo", "branch", "status", "created_at", "updated_at"):
        blockers.extend(_required_string(record, field, path))

    if "head" in record and (not isinstance(record.get("head"), str) or not record.get("head", "").strip()):
        blockers.append(
            ownership_blocker(
                "ownership_field_type_invalid",
                "head must be a non-empty string when present",
                field="head",
                path=str(path) if path else None,
            )
        )

    for field, kind in (
        ("id", "work ownership"),
        ("task_id", "task"),
        ("candidate_id", "candidate"),
        ("epoch_id", "epoch"),
        ("handoff_id", "handoff"),
    ):
        blockers.extend(_validate_optional_id(record, field, kind, path))

    if expected_id is not None and record.get("id") != expected_id:
        blockers.append(
            ownership_blocker(
                "ownership_id_mismatch",
                "work ownership record id does not match registry path",
                expected_id=expected_id,
                actual_id=record.get("id"),
                path=str(path) if path else None,
            )
        )

    pr_number = record.get("pr_number")
    if pr_number is not None and (isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0):
        blockers.append(
            ownership_blocker(
                "ownership_field_type_invalid",
                "pr_number must be a positive integer when present",
                field="pr_number",
                path=str(path) if path else None,
            )
        )

    status = record.get("status")
    if status not in WORK_OWNERSHIP_STATUSES:
        blockers.append(
            ownership_blocker(
                "ownership_status_invalid",
                "work ownership status is invalid",
                allowed_statuses=list(WORK_OWNERSHIP_STATUSES),
                actual_status=status,
                path=str(path) if path else None,
            )
        )
    elif state is not None:
        expected_state = status.lower() if status != "CLOSED" else "closed"
        if state != expected_state:
            blockers.append(
                ownership_blocker(
                    "ownership_state_mismatch",
                    "work ownership record path state does not match record status",
                    expected_state=expected_state,
                    actual_state=state,
                    path=str(path) if path else None,
                )
            )

    created_at = _parse_utc_timestamp(record.get("created_at"), "created_at", path, blockers)
    updated_at = _parse_utc_timestamp(record.get("updated_at"), "updated_at", path, blockers)
    if created_at is not None and updated_at is not None and updated_at < created_at:
        blockers.append(
            ownership_blocker(
                "ownership_timestamp_invalid",
                "updated_at must be greater than or equal to created_at",
                path=str(path) if path else None,
            )
        )
    now = datetime.now(timezone.utc)
    for field, timestamp in (("created_at", created_at), ("updated_at", updated_at)):
        if timestamp is not None and timestamp > now:
            blockers.append(
                ownership_blocker(
                    "ownership_timestamp_invalid",
                    f"{field} must not be in the future",
                    field=field,
                    path=str(path) if path else None,
                )
            )
    if updated_at is not None and max_age_minutes is not None and status == ACTIVE_WORK_OWNERSHIP_STATUS:
        age_seconds = (now - updated_at).total_seconds()
        if age_seconds > max_age_minutes * 60:
            blockers.append(
                ownership_blocker(
                    "ownership_stale",
                    "active work ownership evidence is older than the allowed freshness window",
                    path=str(path) if path else None,
                    max_age_minutes=max_age_minutes,
                )
            )

    if require_active and status != ACTIVE_WORK_OWNERSHIP_STATUS:
        blockers.append(
            ownership_blocker(
                "ownership_closed",
                "work ownership record is not active",
                actual_status=status,
                path=str(path) if path else None,
            )
        )

    if expected_repo is not None and record.get("repo") != expected_repo:
        blockers.append(
            ownership_blocker(
                "ownership_repo_mismatch",
                "work ownership repo does not match expected repo",
                expected_repo=expected_repo,
                actual_repo=record.get("repo"),
                path=str(path) if path else None,
            )
        )
    if expected_branch is not None and record.get("branch") != expected_branch:
        blockers.append(
            ownership_blocker(
                "ownership_branch_mismatch",
                "work ownership branch does not match expected branch",
                expected_branch=expected_branch,
                actual_branch=record.get("branch"),
                path=str(path) if path else None,
            )
        )
    if expected_task_id is not None and record.get("task_id") != expected_task_id:
        blockers.append(
            ownership_blocker(
                "ownership_task_mismatch",
                "work ownership task_id does not match expected task_id",
                expected_task_id=expected_task_id,
                actual_task_id=record.get("task_id"),
                path=str(path) if path else None,
            )
        )

    return blockers


def _repo_evidence(cwd: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return current_repo_evidence(cwd), []
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "cwd": str(cwd.expanduser().resolve(strict=False)),
            "branch": None,
            "head": None,
            "dirty_worktree": None,
        }, [
            ownership_blocker(
                "repo_inspection_failed",
                f"could not inspect repo state: {exc}",
            )
        ]


def _repo_anchor_blockers(repository: dict[str, Any], *, branch: str, head: str) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if repository.get("branch") != branch:
        blockers.append(
            ownership_blocker(
                "repo_branch_mismatch",
                "current branch does not match requested work ownership branch",
                expected=branch,
                actual=repository.get("branch"),
            )
        )
    if repository.get("head") != head:
        blockers.append(
            ownership_blocker(
                "repo_head_mismatch",
                "current HEAD does not match requested work ownership head",
                expected=head,
                actual=repository.get("head"),
            )
        )
    if repository.get("dirty_worktree") is not False:
        blockers.append(
            ownership_blocker(
                "dirty_worktree",
                "current worktree must be clean before work ownership mutation",
            )
        )
    return blockers


def _registry_state_directory_blockers(root: Path) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    registry_dir = root / "work-ownership"
    try:
        if registry_dir.is_symlink():
            return [
                ownership_blocker(
                    "ownership_registry_state_invalid",
                    "work ownership registry directory must not be a symlink",
                    state="registry",
                    path=str(registry_dir),
                )
            ]
        if registry_dir.exists() and not registry_dir.is_dir():
            return [
                ownership_blocker(
                    "ownership_registry_state_invalid",
                    "work ownership registry path must be a directory",
                    state="registry",
                    path=str(registry_dir),
                )
            ]
    except OSError as exc:
        return [
            ownership_blocker(
                "ownership_registry_state_invalid",
                f"work ownership registry directory could not be inspected: {exc}",
                state="registry",
                path=str(registry_dir),
            )
        ]

    for state in WORK_OWNERSHIP_STATES:
        directory = work_ownership_state_dir(root, state)
        try:
            if directory.is_symlink():
                blockers.append(
                    ownership_blocker(
                        "ownership_registry_state_invalid",
                        "work ownership state directory must not be a symlink",
                        state=state,
                        path=str(directory),
                    )
                )
            elif directory.exists() and not directory.is_dir():
                blockers.append(
                    ownership_blocker(
                        "ownership_registry_state_invalid",
                        "work ownership state path must be a directory",
                        state=state,
                        path=str(directory),
                    )
                )
        except OSError as exc:
            blockers.append(
                ownership_blocker(
                    "ownership_registry_state_invalid",
                    f"work ownership state directory could not be inspected: {exc}",
                    state=state,
                    path=str(directory),
                )
            )
    return blockers


def _ensure_ownership_layout_blockers(root: Path) -> list[dict[str, Any]]:
    blockers = _registry_state_directory_blockers(root)
    if blockers:
        return blockers
    for state in WORK_OWNERSHIP_STATES:
        directory = work_ownership_state_dir(root, state)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            blockers.append(
                ownership_blocker(
                    "ownership_registry_state_invalid",
                    f"work ownership state directory could not be created: {exc}",
                    state=state,
                    path=str(directory),
                )
            )
    return blockers


def _valid_registry_state_directory(root: Path, state: str) -> Path | None:
    if _registry_state_directory_blockers(root):
        return None
    directory = work_ownership_state_dir(root, state)
    try:
        if directory.is_symlink() or not directory.exists() or not directory.is_dir():
            return None
    except OSError:
        return None
    return directory


def _registry_state_for_candidate_path(root: Path, path: Path) -> str | None:
    if path.suffix != ".json":
        return None
    try:
        candidate_parent = path.expanduser().resolve(strict=False).parent
    except OSError:
        return None
    for state in WORK_OWNERSHIP_STATES:
        try:
            expected_parent = work_ownership_state_dir(root, state).resolve(strict=False)
        except OSError:
            continue
        if candidate_parent == expected_parent:
            return state
    return None


def _record_path_blockers(root: Path, path: Path, state: str) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    try:
        if path.is_symlink():
            return [
                ownership_blocker(
                    "ownership_record_path_invalid",
                    "work ownership record file must not be a symlink",
                    path=str(path),
                )
            ]
        if path.exists() and not path.is_file():
            blockers.append(
                ownership_blocker(
                    "ownership_record_path_invalid",
                    "work ownership record path must be a file",
                    path=str(path),
                )
            )
    except OSError as exc:
        return [
            ownership_blocker(
                "ownership_record_path_invalid",
                f"work ownership record path could not be inspected: {exc}",
                path=str(path),
            )
        ]

    state_dir = _valid_registry_state_directory(root, state)
    if state_dir is None:
        return blockers
    try:
        resolved_parent = path.expanduser().resolve(strict=False).parent
        resolved_state_dir = state_dir.resolve(strict=True)
    except OSError as exc:
        blockers.append(
            ownership_blocker(
                "ownership_record_path_invalid",
                f"work ownership record path could not be resolved: {exc}",
                path=str(path),
            )
        )
        return blockers
    if resolved_parent != resolved_state_dir:
        blockers.append(
            ownership_blocker(
                "ownership_record_outside_registry",
                "work ownership record path must be under the runtime work-ownership registry",
                path=str(path),
                registry=str(root / "work-ownership"),
            )
        )
    return blockers


def _iter_ownership_files(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for state in WORK_OWNERSHIP_STATES:
        directory = _valid_registry_state_directory(root, state)
        if directory is not None:
            files.extend((state, path) for path in sorted(directory.glob("*.json")))
    return files


def _read_record(path: Path) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        return read_json(path), []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [
            ownership_blocker(
                "ownership_record_unreadable",
                f"work ownership record could not be read: {exc}",
                path=str(path),
            )
        ]


def _status_recommendation(blockers: list[dict[str, Any]]) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if "duplicate_active_ownership" in codes:
        return "resolve_duplicate_ownership"
    if blockers:
        return "inspect_ownership_evidence"
    return "use_work_ownership_status"


def _validation_recommendation(blockers: list[dict[str, Any]]) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if "duplicate_active_ownership" in codes:
        return "resolve_duplicate_ownership"
    if "ownership_record_missing" in codes:
        return "provide_ownership_record"
    if "ownership_closed" in codes or "ownership_stale" in codes:
        return "refresh_ownership_evidence"
    if blockers:
        return "repair_ownership_record"
    return "use_work_ownership_record"


def _matches_scope(record: Any, *, repo: str | None, branch: str | None, task_id: str | None) -> bool:
    if not isinstance(record, dict):
        return False
    for field, expected in (("repo", repo), ("branch", branch), ("task_id", task_id)):
        if expected is None:
            continue
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
        if value != expected:
            return False
    return True


def _duplicate_active_blockers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        if record.get("status") != ACTIVE_WORK_OWNERSHIP_STATUS:
            continue
        key_values = (record.get("repo"), record.get("branch"), record.get("task_id"))
        if not all(isinstance(value, str) and value for value in key_values):
            continue
        groups.setdefault(key_values, []).append(record)

    blockers: list[dict[str, Any]] = []
    for (repo, branch, task_id), duplicates in sorted(groups.items()):
        if len(duplicates) <= 1:
            continue
        blockers.append(
            ownership_blocker(
                "duplicate_active_ownership",
                "more than one active work ownership record exists for the same repo, branch, and task",
                repo=repo,
                branch=branch,
                task_id=task_id,
                ownership_ids=[record.get("id") for record in duplicates],
                paths=[record.get("path") for record in duplicates],
            )
        )
    return blockers


def _active_claim_blocker(record: dict[str, Any]) -> dict[str, Any]:
    return ownership_blocker(
        "duplicate_active_ownership",
        "an active work ownership record already exists for this repo, branch, and task",
        repo=record.get("repo"),
        branch=record.get("branch"),
        task_id=record.get("task_id"),
        ownership_ids=[record.get("id")],
        paths=[record.get("path")],
    )


def _active_records_for_duplicate_scan(
    root: Path,
    *,
    repo: Any,
    branch: Any,
    task_id: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not all(isinstance(value, str) and value.strip() for value in (repo, branch, task_id)):
        return [], []
    records: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for state, path in _iter_ownership_files(root):
        if state != "active":
            continue
        path_blockers = _record_path_blockers(root, path, state)
        if path_blockers:
            blockers.extend(path_blockers)
            continue
        record, read_blockers = _read_record(path)
        if read_blockers:
            blockers.extend(read_blockers)
            continue
        if not isinstance(record, dict):
            blockers.extend(validate_work_ownership_record(record, path=path, state=state, expected_id=path.stem, max_age_minutes=None))
            continue
        if _matches_scope(record, repo=repo, branch=branch, task_id=task_id):
            blockers.extend(validate_work_ownership_record(record, path=path, state=state, expected_id=path.stem, max_age_minutes=None))
        if (
            record.get("status") == ACTIVE_WORK_OWNERSHIP_STATUS
            and record.get("repo") == repo
            and record.get("branch") == branch
            and record.get("task_id") == task_id
        ):
            records.append(ownership_record_summary(record, path, state))
    return records, blockers


def _validate_record_id_field(value: Any, field: str, kind: str) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return [ownership_blocker("ownership_required_field_missing", f"{field} is required", field=field)]
    try:
        validate_record_id(value, kind)
    except ValueError as exc:
        return [ownership_blocker("ownership_id_invalid", str(exc), field=field)]
    return []


def _validate_label_field(value: Any, field: str, code: str) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return [ownership_blocker("ownership_required_field_missing", f"{field} is required", field=field)]
    try:
        validate_record_id(value, field)
    except ValueError as exc:
        return [ownership_blocker(code, str(exc), field=field)]
    return []


def _validate_required_text_field(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return [ownership_blocker("ownership_required_field_missing", f"{field} is required", field=field)]
    return []


def _ownership_mutation_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "use_work_ownership_record"
    codes = {blocker.get("code") for blocker in blockers}
    if "dirty_worktree" in codes:
        return "clean_worktree"
    if codes & {"repo_inspection_failed", "repo_branch_mismatch", "repo_head_mismatch"}:
        return "inspect_repo_state"
    if codes & {
        "ownership_id_invalid",
        "ownership_role_invalid",
        "ownership_claimer_invalid",
        "ownership_required_field_missing",
    }:
        return "fix_ownership_request"
    if codes & {"duplicate_active_ownership", "ownership_stale"}:
        return "close_or_fail_active_ownership"
    if "ownership_record_missing" in codes:
        return "provide_ownership_record"
    if codes & {
        "ownership_registry_state_invalid",
        "ownership_record_unreadable",
        "ownership_record_path_invalid",
        "ownership_record_outside_registry",
        "ownership_record_ambiguous",
        "ownership_record_invalid",
        "ownership_schema_unsupported",
        "ownership_field_type_invalid",
        "ownership_id_mismatch",
        "ownership_status_invalid",
        "ownership_state_mismatch",
        "ownership_timestamp_invalid",
    }:
        return "repair_ownership_record"
    if "audit_append_failed" in codes:
        return "inspect_runtime_state"
    return "inspect_ownership_evidence"


def _ownership_reason(valid: bool, blockers: list[dict[str, Any]], success: str) -> str:
    if valid:
        return success
    if blockers:
        return blockers[0]["message"]
    return "work ownership mutation blocked"


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return (safe or "work")[:48].strip("-") or "work"


def generate_work_ownership_id(task_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(task_id)}-{secrets.token_hex(4)}"


def _claim_packet(
    *,
    root: Path,
    cwd: Path,
    ownership_id: str | None,
    repo: str,
    branch: str,
    head: str,
    task_id: str,
    candidate_id: str,
    role: str,
    claimer: str,
    repository: dict[str, Any],
    blockers: list[dict[str, Any]],
    record: Any | None = None,
    record_path: Path | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, Any]:
    valid = not blockers
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": WORK_OWNERSHIP_CLAIM_SCHEMA_VERSION,
        "packet": "work_ownership_claim",
        "read_only": False,
        "valid": valid,
        "ownership_written": record_path is not None and valid,
        "ownership_id": ownership_id,
        "root": str(root),
        "repository": {
            **repository,
            "expected_repo": repo,
            "expected_branch": branch,
            "expected_head": head,
            "cwd": str(Path(cwd).expanduser().resolve(strict=False)),
        },
        "request": {
            "repo": repo,
            "branch": branch,
            "head": head,
            "task_id": task_id,
            "candidate_id": candidate_id,
            "role": role,
            "claimer": claimer,
        },
        "record": ownership_record_summary(record, record_path, "active") if record is not None else None,
        "blockers": blockers,
        "side_effects": list(side_effects or []),
        "recommended_next_action": _ownership_mutation_recommendation(blockers),
        "reason": _ownership_reason(valid, blockers, "work ownership claim written"),
        "limitations": [
            "local_evidence_only",
            "not_a_distributed_lock",
            "role_assignment_out_of_scope",
            "execution_start_enforcement_out_of_scope",
            "resume_continuation_enforcement_out_of_scope",
            "git_github_writes_out_of_scope",
        ],
    }


def claim_work_ownership(
    *,
    root: Path,
    cwd: Path,
    repo: str,
    branch: str,
    head: str,
    task_id: str,
    candidate_id: str,
    role: str,
    claimer: str,
    ownership_id: str | None = None,
    pr_number: int | None = None,
    epoch_id: str | None = None,
    handoff_id: str | None = None,
    max_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    requested_id = ownership_id or generate_work_ownership_id(task_id)
    repository, repo_blockers = _repo_evidence(cwd)
    blockers: list[dict[str, Any]] = []
    blockers.extend(repo_blockers)
    if not repo_blockers:
        blockers.extend(_repo_anchor_blockers(repository, branch=branch, head=head))

    for field, value in (("repo", repo), ("branch", branch), ("head", head)):
        blockers.extend(_validate_required_text_field(value, field))
    blockers.extend(_validate_record_id_field(requested_id, "id", "work ownership"))
    blockers.extend(_validate_record_id_field(task_id, "task_id", "task"))
    blockers.extend(_validate_record_id_field(candidate_id, "candidate_id", "candidate"))
    blockers.extend(_validate_label_field(role, "role", "ownership_role_invalid"))
    blockers.extend(_validate_label_field(claimer, "claimer", "ownership_claimer_invalid"))
    if epoch_id:
        blockers.extend(_validate_record_id_field(epoch_id, "epoch_id", "epoch"))
    if handoff_id:
        blockers.extend(_validate_record_id_field(handoff_id, "handoff_id", "handoff"))
    if pr_number is not None and (isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0):
        blockers.append(ownership_blocker("ownership_field_type_invalid", "pr_number must be a positive integer", field="pr_number"))

    status_packet: dict[str, Any] | None = None
    if not repo_blockers:
        status_packet = work_ownership_status(
            root=root,
            cwd=cwd,
            repo=repo,
            branch=branch,
            task_id=task_id,
            max_age_minutes=max_age_minutes,
        )
        blockers.extend(status_packet["blockers"])
        codes = {blocker.get("code") for blocker in blockers}
        active_records = [
            record
            for record in status_packet.get("records", [])
            if record.get("status") == ACTIVE_WORK_OWNERSHIP_STATUS
            and record.get("repo") == repo
            and record.get("branch") == branch
            and record.get("task_id") == task_id
        ]
        if active_records and not (codes & {"duplicate_active_ownership", "ownership_stale"}):
            blockers.append(_active_claim_blocker(active_records[0]))

    if not blockers:
        blockers.extend(_ensure_ownership_layout_blockers(root))

    if not blockers:
        for state in WORK_OWNERSHIP_STATES:
            existing_path = work_ownership_path(root, state, requested_id)
            if existing_path.exists():
                blockers.append(
                    ownership_blocker(
                        "ownership_record_exists",
                        "work ownership id already exists",
                        ownership_id=requested_id,
                        path=str(existing_path),
                    )
                )
                break

    record: dict[str, Any] | None = None
    target: Path | None = None
    side_effects: list[str] = []
    if not blockers:
        now = utc_now()
        record = {
            "schema_version": WORK_OWNERSHIP_SCHEMA_VERSION,
            "id": requested_id,
            "task_id": task_id,
            "candidate_id": candidate_id,
            "role": role,
            "claimer": claimer,
            "repo": repo,
            "branch": branch,
            "head": head,
            "status": ACTIVE_WORK_OWNERSHIP_STATUS,
            "created_at": now,
            "updated_at": now,
        }
        if pr_number is not None:
            record["pr_number"] = pr_number
        if epoch_id:
            record["epoch_id"] = epoch_id
        if handoff_id:
            record["handoff_id"] = handoff_id
        target = work_ownership_path(root, "active", requested_id)
        blockers.extend(_record_path_blockers(root, target, "active"))
        blockers.extend(validate_work_ownership_record(record, path=target, state="active", expected_id=requested_id, max_age_minutes=None))

    if not blockers and record is not None and target is not None:
        try:
            atomic_write_json(target, record)
            side_effects.append("work_ownership_active_written")
        except (OSError, ValueError) as exc:
            blockers.append(ownership_blocker("ownership_record_write_failed", f"work ownership record could not be written: {exc}"))
            record = None
            target = None

    return _claim_packet(
        root=root,
        cwd=cwd,
        ownership_id=requested_id,
        repo=repo,
        branch=branch,
        head=head,
        task_id=task_id,
        candidate_id=candidate_id,
        role=role,
        claimer=claimer,
        repository=repository,
        blockers=blockers,
        record=record,
        record_path=target,
        side_effects=side_effects,
    )


def _closeout_packet(
    *,
    root: Path,
    cwd: Path,
    target: str,
    closeout_status: str,
    repo: str,
    branch: str,
    head: str,
    task_id: str,
    claimer: str,
    candidate_id: str | None = None,
    role: str | None = None,
    epoch_id: str | None = None,
    executor_closeout_file: str | None = None,
    executor_closeout_checksum: str | None = None,
    executor_closeout_status: str | None = None,
    repository: dict[str, Any],
    blockers: list[dict[str, Any]],
    record: Any | None = None,
    record_path: Path | None = None,
    source_path: Path | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, Any]:
    valid = not blockers
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": WORK_OWNERSHIP_CLOSEOUT_SCHEMA_VERSION,
        "packet": "work_ownership_closeout",
        "read_only": False,
        "valid": valid,
        "ownership_moved": record_path is not None and valid,
        "ownership_id": record.get("id") if isinstance(record, dict) else None,
        "target": target,
        "closeout_status": closeout_status,
        "root": str(root),
        "repository": {
            **repository,
            "expected_repo": repo,
            "expected_branch": branch,
            "expected_head": head,
            "cwd": str(Path(cwd).expanduser().resolve(strict=False)),
        },
        "request": {
            "repo": repo,
            "branch": branch,
            "head": head,
            "task_id": task_id,
            "claimer": claimer,
            **({"candidate_id": candidate_id} if candidate_id is not None else {}),
            **({"role": role} if role is not None else {}),
            **({"epoch_id": epoch_id} if epoch_id is not None else {}),
            **({"executor_closeout_file": executor_closeout_file} if executor_closeout_file is not None else {}),
            **({"executor_closeout_checksum": executor_closeout_checksum} if executor_closeout_checksum is not None else {}),
            **({"executor_closeout_status": executor_closeout_status} if executor_closeout_status is not None else {}),
        },
        "source_record": str(source_path) if source_path else None,
        "record": ownership_record_summary(record, record_path, closeout_status.lower()) if record is not None else None,
        "blockers": blockers,
        "side_effects": list(side_effects or []),
        "recommended_next_action": _ownership_mutation_recommendation(blockers),
        "reason": _ownership_reason(valid, blockers, f"work ownership marked {closeout_status.lower()}"),
        "limitations": [
            "local_evidence_only",
            "not_a_distributed_lock",
            "role_assignment_out_of_scope",
            "execution_start_enforcement_out_of_scope",
            "resume_continuation_enforcement_out_of_scope",
            "git_github_writes_out_of_scope",
        ],
    }


def closeout_work_ownership(
    *,
    root: Path,
    cwd: Path,
    target: str,
    closeout_status: str,
    repo: str,
    branch: str,
    head: str,
    task_id: str,
    claimer: str,
    summary: str | None = None,
    candidate_id: str | None = None,
    role: str | None = None,
    epoch_id: str | None = None,
    executor_closeout_file: str | None = None,
    executor_closeout_checksum: str | None = None,
    executor_closeout_status: str | None = None,
    pre_blockers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if closeout_status not in ("CLOSED", "FAILED"):
        raise ValueError("closeout_status must be CLOSED or FAILED")
    destination_state = "closed" if closeout_status == "CLOSED" else "failed"
    repository, repo_blockers = _repo_evidence(cwd)
    blockers: list[dict[str, Any]] = list(pre_blockers or [])
    blockers.extend(repo_blockers)
    if not repo_blockers:
        blockers.extend(_repo_anchor_blockers(repository, branch=branch, head=head))
    for field, value in (("repo", repo), ("branch", branch), ("head", head), ("summary", summary or "")):
        blockers.extend(_validate_required_text_field(value, field))
    blockers.extend(_validate_record_id_field(task_id, "task_id", "task"))
    blockers.extend(_validate_label_field(claimer, "claimer", "ownership_claimer_invalid"))
    if candidate_id is not None:
        blockers.extend(_validate_record_id_field(candidate_id, "candidate_id", "candidate"))
    if role is not None:
        blockers.extend(_validate_label_field(role, "role", "ownership_role_invalid"))
    if epoch_id is not None:
        blockers.extend(_validate_record_id_field(epoch_id, "epoch_id", "epoch"))
    closeout_anchor_values = {
        "executor_closeout_file": executor_closeout_file,
        "executor_closeout_checksum": executor_closeout_checksum,
        "executor_closeout_status": executor_closeout_status,
    }
    if any(value is not None for value in closeout_anchor_values.values()):
        for field, value in closeout_anchor_values.items():
            blockers.extend(_validate_required_text_field(value, field))

    path: Path | None = None
    state: str | None = None
    record: Any | None = None
    if not repo_blockers:
        path, state, find_blockers = find_work_ownership_record(root, target)
        blockers.extend(find_blockers)
        if path is not None:
            path_blockers = _record_path_blockers(root, path, state or "active")
            blockers.extend(path_blockers)
            if not path_blockers:
                record, read_blockers = _read_record(path)
                blockers.extend(read_blockers)
                if not read_blockers:
                    blockers.extend(
                        validate_work_ownership_record(
                            record,
                            path=path,
                            state=state,
                            expected_id=path.stem,
                            expected_repo=repo,
                            expected_branch=branch,
                            expected_task_id=task_id,
                            require_active=True,
                            max_age_minutes=None,
                        )
                    )
                    if state != "active":
                        blockers.append(
                            ownership_blocker(
                                "ownership_closed",
                                "work ownership record is not active",
                                actual_state=state,
                                path=str(path),
                            )
                        )
                    if isinstance(record, dict):
                        if record.get("claimer") != claimer:
                            blockers.append(
                                ownership_blocker(
                                    "ownership_claimer_mismatch",
                                    "work ownership claimer does not match closeout claimer",
                                    expected_claimer=record.get("claimer"),
                                    actual_claimer=claimer,
                                    path=str(path),
                                )
                            )
                        require_closeout_bound_head = executor_closeout_checksum is not None
                        if (
                            (require_closeout_bound_head and record.get("head") != head)
                            or (not require_closeout_bound_head and record.get("head") is not None and record.get("head") != head)
                        ):
                            blockers.append(
                                ownership_blocker(
                                    "ownership_head_mismatch",
                                    "work ownership head does not match requested head",
                                    expected_head=record.get("head"),
                                    actual_head=head,
                                    path=str(path),
                                )
                            )
                        if candidate_id is not None and record.get("candidate_id") != candidate_id:
                            blockers.append(
                                ownership_blocker(
                                    "ownership_candidate_mismatch",
                                    "work ownership candidate does not match closeout candidate",
                                    expected_candidate_id=record.get("candidate_id"),
                                    actual_candidate_id=candidate_id,
                                    path=str(path),
                                )
                            )
                        if role is not None and record.get("role") != role:
                            blockers.append(
                                ownership_blocker(
                                    "ownership_role_mismatch",
                                    "work ownership role does not match closeout role",
                                    expected_role=record.get("role"),
                                    actual_role=role,
                                    path=str(path),
                                )
                            )
                        if epoch_id is not None and record.get("epoch_id") != epoch_id:
                            blockers.append(
                                ownership_blocker(
                                    "ownership_epoch_mismatch",
                                    "work ownership epoch does not match executor closeout epoch",
                                    expected_epoch_id=record.get("epoch_id"),
                                    actual_epoch_id=epoch_id,
                                    path=str(path),
                                )
                            )

    if not blockers:
        blockers.extend(_ensure_ownership_layout_blockers(root))

    destination: Path | None = None
    updated_record: dict[str, Any] | None = None
    side_effects: list[str] = []
    if not blockers and isinstance(record, dict) and path is not None:
        destination = work_ownership_path(root, destination_state, record["id"])
        blockers.extend(_record_path_blockers(root, destination, destination_state))
        if destination.exists():
            blockers.append(
                ownership_blocker(
                    "ownership_record_exists",
                    "destination work ownership record already exists",
                    ownership_id=record.get("id"),
                    path=str(destination),
                )
            )
        now = utc_now()
        updated_record = dict(record)
        updated_record["status"] = closeout_status
        updated_record["updated_at"] = now
        updated_record["closeout"] = {
            "status": closeout_status,
            "claimer": claimer,
            "summary": summary,
            "repo": repo,
            "branch": branch,
            "head": head,
            "recorded_at": now,
        }
        if epoch_id is not None:
            updated_record["closeout"]["epoch_id"] = epoch_id
        if executor_closeout_file is not None:
            updated_record["closeout"]["executor_closeout_file"] = executor_closeout_file
        if executor_closeout_checksum is not None:
            updated_record["closeout"]["executor_closeout_checksum"] = executor_closeout_checksum
        if executor_closeout_status is not None:
            updated_record["closeout"]["executor_closeout_status"] = executor_closeout_status
        if closeout_status == "CLOSED":
            updated_record["closed_at"] = now
        else:
            updated_record["failed_at"] = now
        blockers.extend(
            validate_work_ownership_record(
                updated_record,
                path=destination,
                state=destination_state,
                expected_id=record["id"],
                expected_repo=repo,
                expected_branch=branch,
                expected_task_id=task_id,
                require_active=False,
                max_age_minutes=None,
            )
        )

    if not blockers and updated_record is not None and destination is not None and path is not None:
        destination_written = False
        try:
            atomic_write_json(destination, updated_record)
            destination_written = True
            path.unlink()
            side_effects.append("work_ownership_active_moved")
        except (OSError, ValueError) as exc:
            blockers.append(ownership_blocker("ownership_record_write_failed", f"work ownership record could not be moved: {exc}"))
            if destination_written:
                try:
                    destination.unlink()
                    side_effects.append("work_ownership_destination_rollback")
                except OSError as rollback_exc:
                    blockers.append(
                        ownership_blocker(
                            "ownership_rollback_failed",
                            f"partial closeout destination could not be removed: {rollback_exc}",
                            path=str(destination),
                        )
                    )
            updated_record = None
            destination = None

    return _closeout_packet(
        root=root,
        cwd=cwd,
        target=target,
        closeout_status=closeout_status,
        repo=repo,
        branch=branch,
        head=head,
        task_id=task_id,
        claimer=claimer,
        candidate_id=candidate_id,
        role=role,
        epoch_id=epoch_id,
        executor_closeout_file=executor_closeout_file,
        executor_closeout_checksum=executor_closeout_checksum,
        executor_closeout_status=executor_closeout_status,
        repository=repository,
        blockers=blockers,
        record=updated_record,
        record_path=destination,
        source_path=path,
        side_effects=side_effects,
    )


def work_ownership_status(
    *,
    root: Path,
    cwd: Path,
    repo: str | None = None,
    branch: str | None = None,
    task_id: str | None = None,
    max_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    repository, repo_blockers = _repo_evidence(cwd)
    expected_branch = branch if branch is not None else repository.get("branch")
    blockers: list[dict[str, Any]] = []
    blockers.extend(repo_blockers)
    blockers.extend(_registry_state_directory_blockers(root))
    records: list[dict[str, Any]] = []
    active_records_for_duplicates: list[dict[str, Any]] = []

    for state, path in _iter_ownership_files(root):
        path_blockers = _record_path_blockers(root, path, state)
        if path_blockers:
            blockers.extend(path_blockers)
            records.append(ownership_record_summary(None, path, state))
            continue
        record, read_blockers = _read_record(path)
        blockers.extend(read_blockers)
        if read_blockers:
            records.append(ownership_record_summary(record, path, state))
            continue
        summary = ownership_record_summary(record, path, state)
        in_scope = _matches_scope(record, repo=repo, branch=expected_branch, task_id=task_id)
        if in_scope:
            active_records_for_duplicates.append(summary)
        structural_blockers = validate_work_ownership_record(
            record,
            path=path,
            state=state,
            expected_id=path.stem,
            max_age_minutes=None,
        )
        if structural_blockers:
            blockers.extend(structural_blockers)
            records.append(summary)
            continue
        if not in_scope:
            continue
        record_blockers = validate_work_ownership_record(
            record,
            path=path,
            state=state,
            expected_id=path.stem,
            max_age_minutes=max_age_minutes,
        )
        blockers.extend(record_blockers)
        records.append(summary)

    blockers.extend(_duplicate_active_blockers(active_records_for_duplicates))
    counts = {
        "total": len(records),
        "active": sum(1 for record in records if record.get("status") == "ACTIVE"),
        "closed": sum(1 for record in records if record.get("status") == "CLOSED"),
        "failed": sum(1 for record in records if record.get("status") == "FAILED"),
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": WORK_OWNERSHIP_STATUS_SCHEMA_VERSION,
        "packet": "work_ownership_status",
        "read_only": True,
        "side_effects": [],
        "root": str(root),
        "repository": {
            **repository,
            "expected_repo": repo,
            "expected_branch": expected_branch,
        },
        "scope": {
            "repo": repo,
            "branch": expected_branch,
            "task_id": task_id,
            "max_age_minutes": max_age_minutes,
        },
        "valid": not blockers,
        "counts": counts,
        "records": records,
        "blockers": blockers,
        "recommended_next_action": _status_recommendation(blockers),
    }


def _registry_state_for_path(root: Path, path: Path) -> str | None:
    try:
        resolved_path = path.expanduser().resolve(strict=True)
    except OSError:
        return None
    for state in WORK_OWNERSHIP_STATES:
        state_dir = _valid_registry_state_directory(root, state)
        if state_dir is None:
            continue
        state_dir = state_dir.resolve(strict=True)
        if resolved_path.parent == state_dir and resolved_path.suffix == ".json":
            return state
    return None


def find_work_ownership_record(root: Path, target: str) -> tuple[Path | None, str | None, list[dict[str, Any]]]:
    registry_blockers = _registry_state_directory_blockers(root)
    try:
        ownership_id = validate_record_id(target, "work ownership")
    except ValueError:
        ownership_id = None
    if ownership_id is not None:
        invalid_path_blockers: list[dict[str, Any]] = []
        matches = [
            (state, work_ownership_path(root, state, ownership_id))
            for state in WORK_OWNERSHIP_STATES
            if _valid_registry_state_directory(root, state) is not None
        ]
        existing = []
        for state, path in matches:
            if not path.exists():
                continue
            path_blockers = _record_path_blockers(root, path, state)
            if path_blockers:
                invalid_path_blockers.extend(path_blockers)
                continue
            existing.append((state, path))
        if len(existing) > 1:
            return None, None, [
                *registry_blockers,
                *invalid_path_blockers,
                ownership_blocker(
                    "ownership_record_ambiguous",
                    "work ownership id exists in more than one state directory",
                    ownership_id=ownership_id,
                    paths=[str(path) for _state, path in existing],
                ),
            ]
        if len(existing) == 1:
            state, path = existing[0]
            return path, state, [*registry_blockers, *invalid_path_blockers]
        if invalid_path_blockers:
            return None, None, [*registry_blockers, *invalid_path_blockers]
        return None, None, [
            *registry_blockers,
            ownership_blocker(
                "ownership_record_missing",
                "work ownership record was not found",
                ownership_id=ownership_id,
            ),
        ]

    supplied = Path(target)
    if supplied.exists():
        state = _registry_state_for_path(root, supplied)
        if state is None:
            return None, None, [
                *registry_blockers,
                ownership_blocker(
                    "ownership_record_outside_registry",
                    "work ownership record path must be under the runtime work-ownership registry",
                    path=str(supplied),
                    registry=str(root / "work-ownership"),
                )
            ]
        path_blockers = _record_path_blockers(root, supplied, state)
        if path_blockers:
            return None, None, [*registry_blockers, *path_blockers]
        return supplied, state, registry_blockers
    if supplied.is_absolute() or supplied.suffix == ".json" or len(supplied.parts) > 1:
        state = _registry_state_for_candidate_path(root, supplied)
        if state is None:
            return None, None, [
                *registry_blockers,
                ownership_blocker(
                    "ownership_record_outside_registry",
                    "work ownership record path must be under the runtime work-ownership registry",
                    path=str(supplied),
                    registry=str(root / "work-ownership"),
                ),
            ]
        return None, None, [
            *registry_blockers,
            ownership_blocker(
                "ownership_record_missing",
                "work ownership record was not found",
                path=str(supplied),
            ),
        ]
    try:
        ownership_id = validate_record_id(target, "work ownership")
    except ValueError as exc:
        return None, None, [*registry_blockers, ownership_blocker("ownership_id_invalid", str(exc), target=target)]
    return None, None, [
        *registry_blockers,
        ownership_blocker(
            "ownership_record_missing",
            "work ownership record was not found",
            ownership_id=ownership_id,
        ),
    ]


def validate_work_ownership(
    *,
    root: Path,
    target: str,
    cwd: Path,
    repo: str | None = None,
    branch: str | None = None,
    task_id: str | None = None,
    require_active: bool = False,
    max_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    repository, repo_blockers = _repo_evidence(cwd)
    expected_branch = branch if branch is not None else repository.get("branch")
    blockers: list[dict[str, Any]] = []
    blockers.extend(repo_blockers)
    path, state, find_blockers = find_work_ownership_record(root, target)
    blockers.extend(find_blockers)
    record: Any | None = None
    if path is not None:
        record, read_blockers = _read_record(path)
        blockers.extend(read_blockers)
        if not read_blockers:
            blockers.extend(
                validate_work_ownership_record(
                    record,
                    path=path,
                    state=state,
                    expected_id=path.stem,
                    expected_repo=repo,
                    expected_branch=expected_branch,
                    expected_task_id=task_id,
                    require_active=require_active,
                    max_age_minutes=max_age_minutes,
                )
            )
            if isinstance(record, dict) and record.get("status") == ACTIVE_WORK_OWNERSHIP_STATUS:
                duplicate_records, duplicate_scan_blockers = _active_records_for_duplicate_scan(
                    root,
                    repo=record.get("repo"),
                    branch=record.get("branch"),
                    task_id=record.get("task_id"),
                )
                blockers.extend(duplicate_scan_blockers)
                blockers.extend(
                    _duplicate_active_blockers(
                        duplicate_records
                    )
                )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": WORK_OWNERSHIP_VALIDATION_SCHEMA_VERSION,
        "packet": "work_ownership_validation",
        "read_only": True,
        "side_effects": [],
        "root": str(root),
        "target": target,
        "repository": {
            **repository,
            "expected_repo": repo,
            "expected_branch": expected_branch,
        },
        "scope": {
            "repo": repo,
            "branch": expected_branch,
            "task_id": task_id,
            "require_active": require_active,
            "max_age_minutes": max_age_minutes,
        },
        "valid": not blockers,
        "record": ownership_record_summary(record, path, state) if record is not None else None,
        "blockers": blockers,
        "recommended_next_action": _validation_recommendation(blockers),
    }
