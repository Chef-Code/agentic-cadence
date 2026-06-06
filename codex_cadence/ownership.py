from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.repo_state import current_repo_evidence
from codex_cadence.store import (
    WORK_OWNERSHIP_STATES,
    read_json,
    validate_record_id,
    work_ownership_path,
    work_ownership_state_dir,
)

WORK_OWNERSHIP_SCHEMA_VERSION = "work-ownership.v1"
WORK_OWNERSHIP_STATUS_SCHEMA_VERSION = "work-ownership-status.v1"
WORK_OWNERSHIP_VALIDATION_SCHEMA_VERSION = "work-ownership-validation.v1"
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
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
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
        "pr_number": record.get("pr_number") if isinstance(record, dict) else None,
        "epoch_id": record.get("epoch_id") if isinstance(record, dict) else None,
        "handoff_id": record.get("handoff_id") if isinstance(record, dict) else None,
        "status": record.get("status") if isinstance(record, dict) else None,
        "created_at": record.get("created_at") if isinstance(record, dict) else None,
        "updated_at": record.get("updated_at") if isinstance(record, dict) else None,
    }
    return summary


def validate_work_ownership_record(
    record: Any,
    *,
    path: Path | None = None,
    state: str | None = None,
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

    for field, kind in (
        ("id", "work ownership"),
        ("task_id", "task"),
        ("candidate_id", "candidate"),
        ("epoch_id", "epoch"),
        ("handoff_id", "handoff"),
    ):
        blockers.extend(_validate_optional_id(record, field, kind, path))

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
    if updated_at is not None and max_age_minutes is not None and status == ACTIVE_WORK_OWNERSHIP_STATUS:
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age_seconds > max_age_minutes * 60:
            blockers.append(
                ownership_blocker(
                    "ownership_stale",
                    "active work ownership evidence is older than the allowed freshness window",
                    path=str(path) if path else None,
                    age_seconds=round(age_seconds, 3),
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


def _iter_ownership_files(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for state in WORK_OWNERSHIP_STATES:
        directory = work_ownership_state_dir(root, state)
        if directory.exists():
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
        return True
    for field, expected in (("repo", repo), ("branch", branch), ("task_id", task_id)):
        if expected is None:
            continue
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            return True
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


def _active_records_for_duplicate_scan(
    root: Path,
    *,
    repo: Any,
    branch: Any,
    task_id: Any,
) -> list[dict[str, Any]]:
    if not all(isinstance(value, str) and value.strip() for value in (repo, branch, task_id)):
        return []
    records: list[dict[str, Any]] = []
    for state, path in _iter_ownership_files(root):
        if state != "active":
            continue
        record, read_blockers = _read_record(path)
        if read_blockers or not isinstance(record, dict):
            continue
        if (
            record.get("status") == ACTIVE_WORK_OWNERSHIP_STATUS
            and record.get("repo") == repo
            and record.get("branch") == branch
            and record.get("task_id") == task_id
        ):
            records.append(ownership_record_summary(record, path, state))
    return records


def work_ownership_status(
    *,
    root: Path,
    cwd: Path,
    repo: str | None = None,
    branch: str | None = None,
    task_id: str | None = None,
    max_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    repository = current_repo_evidence(cwd)
    expected_branch = branch if branch is not None else repository.get("branch")
    blockers: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    active_records_for_duplicates: list[dict[str, Any]] = []

    for state, path in _iter_ownership_files(root):
        record, read_blockers = _read_record(path)
        blockers.extend(read_blockers)
        if read_blockers:
            records.append(ownership_record_summary(record, path, state))
            continue
        structural_blockers = validate_work_ownership_record(
            record,
            path=path,
            state=state,
            max_age_minutes=None,
        )
        if structural_blockers:
            blockers.extend(structural_blockers)
            records.append(ownership_record_summary(record, path, state))
            continue
        if not _matches_scope(record, repo=repo, branch=expected_branch, task_id=task_id):
            continue
        record_blockers = validate_work_ownership_record(
            record,
            path=path,
            state=state,
            max_age_minutes=max_age_minutes,
        )
        blockers.extend(record_blockers)
        summary = ownership_record_summary(record, path, state)
        records.append(summary)
        active_records_for_duplicates.append(summary)

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
        state_dir = work_ownership_state_dir(root, state).resolve(strict=False)
        if resolved_path.parent == state_dir and resolved_path.suffix == ".json":
            return state
    return None


def find_work_ownership_record(root: Path, target: str) -> tuple[Path | None, str | None, list[dict[str, Any]]]:
    supplied = Path(target)
    if supplied.exists():
        state = _registry_state_for_path(root, supplied)
        if state is None:
            return None, None, [
                ownership_blocker(
                    "ownership_record_outside_registry",
                    "work ownership record path must be under the runtime work-ownership registry",
                    path=str(supplied),
                    registry=str(root / "work-ownership"),
                )
            ]
        return supplied, state, []
    try:
        ownership_id = validate_record_id(target, "work ownership")
    except ValueError as exc:
        return None, None, [ownership_blocker("ownership_id_invalid", str(exc), target=target)]
    matches = [(state, work_ownership_path(root, state, ownership_id)) for state in WORK_OWNERSHIP_STATES]
    existing = [(state, path) for state, path in matches if path.exists()]
    if not existing:
        return None, None, [
            ownership_blocker(
                "ownership_record_missing",
                "work ownership record was not found",
                ownership_id=ownership_id,
            )
        ]
    if len(existing) > 1:
        return None, None, [
            ownership_blocker(
                "ownership_record_ambiguous",
                "work ownership id exists in more than one state directory",
                ownership_id=ownership_id,
                paths=[str(path) for _state, path in existing],
            )
        ]
    state, path = existing[0]
    return path, state, []


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
    repository = current_repo_evidence(cwd)
    expected_branch = branch if branch is not None else repository.get("branch")
    blockers: list[dict[str, Any]] = []
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
                    expected_repo=repo,
                    expected_branch=expected_branch,
                    expected_task_id=task_id,
                    require_active=require_active,
                    max_age_minutes=max_age_minutes,
                )
            )
            if isinstance(record, dict) and record.get("status") == ACTIVE_WORK_OWNERSHIP_STATUS:
                blockers.extend(
                    _duplicate_active_blockers(
                        _active_records_for_duplicate_scan(
                            root,
                            repo=record.get("repo"),
                            branch=record.get("branch"),
                            task_id=record.get("task_id"),
                        )
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
