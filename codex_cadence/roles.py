from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.github_evidence import review_thread_findings_from_payload
from codex_cadence.ownership import DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES, work_ownership_status
from codex_cadence.policy_audit import checksum_json
from codex_cadence.store import read_json, validate_record_id

ROLE_POLICY_SCHEMA_VERSION = "role-policy.v1"
ROLE_READINESS_SCHEMA_VERSION = "role-readiness.v1"


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def _read_json_object(path: Path, *, code: str, label: str) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [_issue(code, f"{label} could not be read: {exc}", path=str(path))]
    if not isinstance(payload, dict):
        invalid_code = code.replace("_unreadable", "_invalid") if code.endswith("_unreadable") else code
        return None, [_issue(invalid_code, f"{label} must be a JSON object", path=str(path))]
    return payload, []


def _valid_label(value: Any, *, field: str, code: str, path: Path | None) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return [_issue(code, f"{field} is required", field=field, path=str(path) if path else None)]
    try:
        validate_record_id(value, field)
    except ValueError as exc:
        return [_issue(code, str(exc), field=field, path=str(path) if path else None)]
    return []


def _string_list(value: Any, *, field: str, code: str, path: Path | None) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(value, list):
        return [], [_issue(code, f"{field} must be a list", field=field, path=str(path) if path else None)]
    values: list[str] = []
    blockers: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            blockers.append(
                _issue(
                    code,
                    f"{field} entries must be non-empty strings",
                    field=field,
                    index=index,
                    path=str(path) if path else None,
                )
            )
            continue
        label_blockers = _valid_label(item, field=field, code=code, path=path)
        blockers.extend(label_blockers)
        if label_blockers:
            continue
        values.append(item)
    return values, blockers


def _role_policy_summary(
    policy: Any,
    path: Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str], set[str], set[str], bool]:
    summary: dict[str, Any] = {
        "path": str(path) if path else None,
        "present": policy is not None,
        "schema_version": policy.get("schema_version") if isinstance(policy, dict) else None,
        "checksum": checksum_json(policy) if isinstance(policy, dict) else None,
        "allowed_roles": [],
        "capabilities": {},
        "review_separation": {
            "required": False,
            "builder_roles": [],
            "reviewer_roles": [],
        },
    }
    blockers: list[dict[str, Any]] = []
    allowed_roles: set[str] = set()
    builder_roles: set[str] = set()
    reviewer_roles: set[str] = set()
    separation_required = False

    if policy is None:
        return summary, blockers, allowed_roles, builder_roles, reviewer_roles, separation_required
    if not isinstance(policy, dict):
        blockers.append(
            _issue("role_policy_invalid", "role policy must be a JSON object", path=str(path) if path else None)
        )
        return summary, blockers, allowed_roles, builder_roles, reviewer_roles, separation_required
    if policy.get("schema_version") != ROLE_POLICY_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "role_policy_schema_unsupported",
                "role policy schema is unsupported",
                expected_schema=ROLE_POLICY_SCHEMA_VERSION,
                actual_schema=policy.get("schema_version"),
                path=str(path) if path else None,
            )
        )

    roles = policy.get("roles")
    if not isinstance(roles, list) or not roles:
        blockers.append(
            _issue(
                "role_policy_invalid",
                "role policy roles must be a non-empty list",
                field="roles",
                path=str(path) if path else None,
            )
        )
        roles = []
    capabilities: dict[str, list[str]] = {}
    for index, entry in enumerate(roles):
        if not isinstance(entry, dict):
            blockers.append(
                _issue(
                    "role_policy_invalid",
                    "role policy role entries must be objects",
                    field="roles",
                    index=index,
                    path=str(path) if path else None,
                )
            )
            continue
        role = entry.get("role")
        role_blockers = _valid_label(role, field="role", code="role_policy_invalid", path=path)
        blockers.extend(role_blockers)
        capabilities_value, capability_blockers = _string_list(
            entry.get("capabilities", []),
            field="capabilities",
            code="role_policy_invalid",
            path=path,
        )
        blockers.extend(capability_blockers)
        if role_blockers or not isinstance(role, str):
            continue
        allowed_roles.add(role)
        capabilities[role] = sorted(capabilities_value)

    review_separation = policy.get("review_separation", {})
    if review_separation in (None, ""):
        review_separation = {}
    if not isinstance(review_separation, dict):
        blockers.append(
            _issue(
                "role_policy_invalid",
                "review_separation must be an object",
                field="review_separation",
                path=str(path) if path else None,
            )
        )
        review_separation = {}
    required_value = review_separation.get("required", False)
    if not isinstance(required_value, bool):
        blockers.append(
            _issue(
                "role_policy_invalid",
                "review_separation.required must be a boolean",
                field="review_separation.required",
                path=str(path) if path else None,
            )
        )
    separation_required = required_value is True
    builder_values, builder_blockers = _string_list(
        review_separation.get("builder_roles", []),
        field="review_separation.builder_roles",
        code="role_policy_invalid",
        path=path,
    )
    reviewer_values, reviewer_blockers = _string_list(
        review_separation.get("reviewer_roles", []),
        field="review_separation.reviewer_roles",
        code="role_policy_invalid",
        path=path,
    )
    if separation_required:
        blockers.extend(builder_blockers)
        blockers.extend(reviewer_blockers)
        builder_roles = set(builder_values)
        reviewer_roles = set(reviewer_values)
        unknown_builder_roles = sorted(builder_roles - allowed_roles) if allowed_roles else []
        unknown_reviewer_roles = sorted(reviewer_roles - allowed_roles) if allowed_roles else []
        if not builder_roles:
            blockers.append(
                _issue(
                    "role_policy_invalid",
                    "review separation requires at least one builder role",
                    field="review_separation.builder_roles",
                    path=str(path) if path else None,
                )
            )
        if not reviewer_roles:
            blockers.append(
                _issue(
                    "role_policy_invalid",
                    "review separation requires at least one reviewer role",
                    field="review_separation.reviewer_roles",
                    path=str(path) if path else None,
                )
            )
        if unknown_builder_roles:
            blockers.append(
                _issue(
                    "role_policy_invalid",
                    "review separation builder roles must be declared policy roles",
                    field="review_separation.builder_roles",
                    roles=unknown_builder_roles,
                    path=str(path) if path else None,
                )
            )
        if unknown_reviewer_roles:
            blockers.append(
                _issue(
                    "role_policy_invalid",
                    "review separation reviewer roles must be declared policy roles",
                    field="review_separation.reviewer_roles",
                    roles=unknown_reviewer_roles,
                    path=str(path) if path else None,
                )
            )

    summary["allowed_roles"] = sorted(allowed_roles)
    summary["capabilities"] = capabilities
    summary["review_separation"] = {
        "required": separation_required,
        "builder_roles": sorted(builder_roles),
        "reviewer_roles": sorted(reviewer_roles),
    }
    return summary, blockers, allowed_roles, builder_roles, reviewer_roles, separation_required


def _current_actionable_review_findings(
    review_threads: Any | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if review_threads is None:
        return [], []
    findings, warnings = review_thread_findings_from_payload(review_threads)
    blockers = [_issue("review_thread_evidence_invalid", warning) for warning in warnings]
    return findings, blockers


def _role_readiness_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "use_role_readiness"
    codes = {blocker.get("code") for blocker in blockers}
    if codes & {
        "role_policy_missing",
        "role_policy_unreadable",
        "role_policy_invalid",
        "role_policy_schema_unsupported",
    }:
        return "provide_role_policy"
    if codes & {
        "pr_branch_mismatch",
        "pr_evidence_invalid",
        "pr_evidence_missing",
        "pr_evidence_unreadable",
        "pr_head_mismatch",
        "pr_number_mismatch",
        "review_thread_evidence_invalid",
    }:
        return "refresh_pr_evidence"
    if codes & {"repo_inspection_failed", "repo_branch_mismatch", "repo_head_mismatch"}:
        return "inspect_repo_state"
    if codes & {"ownership_head_mismatch", "ownership_stale", "duplicate_active_ownership"}:
        return "refresh_ownership_evidence"
    if "ownership_role_unknown" in codes:
        return "fix_role_policy_or_ownership"
    if "builder_ownership_missing" in codes:
        return "claim_work_ownership"
    if "review_separation_conflict" in codes:
        return "assign_independent_reviewer"
    if "reviewer_evidence_missing" in codes:
        return "provide_reviewer_evidence"
    return "inspect_role_readiness_blockers"


def _pr_summary(pr: Any | None, path: Path | None) -> dict[str, Any]:
    if not isinstance(pr, dict):
        return {
            "path": str(path) if path else None,
            "present": pr is not None,
            "checksum": None,
            "number": None,
            "title": None,
            "state": None,
            "review_decision": None,
            "head_ref": None,
            "base_ref": None,
            "head_sha": None,
        }
    return {
        "path": str(path) if path else None,
        "present": True,
        "checksum": checksum_json(pr),
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "review_decision": pr.get("reviewDecision"),
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
    }


def _pr_evidence_blockers(
    pr: Any | None,
    path: Path | None,
    *,
    ownership_records: list[dict[str, Any]],
    repository: Any,
    expected_branch: str | None,
) -> list[dict[str, Any]]:
    if pr is None:
        return []
    blockers: list[dict[str, Any]] = []
    if not isinstance(pr, dict):
        return [_issue("pr_evidence_invalid", "PR evidence must be a JSON object", path=str(path) if path else None)]

    pr_number = pr.get("number")
    head_ref = pr.get("headRefName")
    head_sha = pr.get("headRefOid")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        blockers.append(
            _issue(
                "pr_evidence_invalid",
                "PR evidence number must be a positive integer",
                field="number",
                path=str(path) if path else None,
            )
        )
    if not isinstance(head_ref, str) or not head_ref.strip():
        blockers.append(
            _issue(
                "pr_evidence_invalid",
                "PR evidence headRefName must be a non-empty string",
                field="headRefName",
                path=str(path) if path else None,
            )
        )
    if not isinstance(head_sha, str) or not head_sha.strip():
        blockers.append(
            _issue(
                "pr_evidence_invalid",
                "PR evidence headRefOid must be a non-empty string",
                field="headRefOid",
                path=str(path) if path else None,
            )
        )

    repository = repository if isinstance(repository, dict) else {}
    current_branch = repository.get("branch")
    current_head = repository.get("head")
    if expected_branch is not None and current_branch is not None and current_branch != expected_branch:
        blockers.append(
            _issue(
                "repo_branch_mismatch",
                "current branch does not match requested role-readiness branch",
                expected=expected_branch,
                actual=current_branch,
            )
        )

    if isinstance(head_ref, str) and head_ref.strip() and isinstance(current_branch, str) and current_branch:
        if head_ref != current_branch:
            blockers.append(
                _issue(
                    "pr_branch_mismatch",
                    "saved PR head branch does not match current branch",
                    expected=current_branch,
                    actual=head_ref,
                    path=str(path) if path else None,
                )
            )
    if isinstance(head_sha, str) and head_sha.strip() and isinstance(current_head, str) and current_head:
        if head_sha != current_head:
            blockers.append(
                _issue(
                    "pr_head_mismatch",
                    "saved PR head SHA does not match current HEAD",
                    expected=current_head,
                    actual=head_sha,
                    path=str(path) if path else None,
                )
            )

    ownership_pr_numbers = sorted(
        {
            record["pr_number"]
            for record in ownership_records
            if isinstance(record.get("pr_number"), int) and not isinstance(record.get("pr_number"), bool)
        }
    )
    if isinstance(pr_number, int) and not isinstance(pr_number, bool):
        mismatched_pr_numbers = [number for number in ownership_pr_numbers if number != pr_number]
        if mismatched_pr_numbers:
            blockers.append(
                _issue(
                    "pr_number_mismatch",
                    "saved PR number does not match active ownership PR evidence",
                    actual_pr_number=pr_number,
                    expected_pr_numbers=mismatched_pr_numbers,
                    ownership_ids=[
                        record.get("id")
                        for record in ownership_records
                        if record.get("pr_number") in mismatched_pr_numbers
                    ],
                    path=str(path) if path else None,
                )
            )

    if isinstance(current_head, str) and current_head:
        for record in ownership_records:
            ownership_head = record.get("head")
            if isinstance(ownership_head, str) and ownership_head and ownership_head != current_head:
                blockers.append(
                    _issue(
                        "ownership_head_mismatch",
                        "active ownership head does not match current HEAD",
                        expected_head=ownership_head,
                        actual_head=current_head,
                        ownership_id=record.get("id"),
                        path=record.get("path"),
                    )
                )
    return blockers


def evaluate_role_readiness(
    *,
    root: Path,
    cwd: Path,
    role_policy_file: Path | None = None,
    pr_json_file: Path | None = None,
    review_threads_file: Path | None = None,
    repo: str | None = None,
    branch: str | None = None,
    task_id: str | None = None,
    max_ownership_age_minutes: int | None = DEFAULT_WORK_OWNERSHIP_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    blockers: list[dict[str, Any]] = []

    policy: Any | None = None
    if role_policy_file is None:
        blockers.append(_issue("role_policy_missing", "role policy file is required"))
    else:
        role_policy_file = Path(role_policy_file)
        policy, policy_blockers = _read_json_object(
            role_policy_file,
            code="role_policy_unreadable",
            label="role policy",
        )
        blockers.extend(policy_blockers)

    pr: Any | None = None
    if pr_json_file is None:
        blockers.append(_issue("pr_evidence_missing", "saved PR JSON evidence is required"))
    else:
        pr_json_file = Path(pr_json_file)
        pr, pr_blockers = _read_json_object(
            pr_json_file,
            code="pr_evidence_unreadable",
            label="PR evidence",
        )
        blockers.extend(pr_blockers)

    review_threads: Any | None = None
    if review_threads_file is not None:
        review_threads_file = Path(review_threads_file)
        review_threads, review_read_blockers = _read_json_object(
            review_threads_file,
            code="review_thread_evidence_invalid",
            label="review thread evidence",
        )
        blockers.extend(review_read_blockers)

    (
        policy_summary,
        policy_blockers,
        allowed_roles,
        builder_roles,
        reviewer_roles,
        separation_required,
    ) = _role_policy_summary(policy, role_policy_file)
    blockers.extend(policy_blockers)

    ownership_status = work_ownership_status(
        root=root,
        cwd=cwd,
        repo=repo,
        branch=branch,
        task_id=task_id,
        max_age_minutes=max_ownership_age_minutes,
    )
    ownership_records = [
        record
        for record in ownership_status.get("records", [])
        if isinstance(record, dict) and record.get("status") == "ACTIVE"
    ]
    blockers.extend(
        blocker
        for blocker in ownership_status.get("blockers", [])
        if isinstance(blocker, dict)
    )
    ownership_scope = ownership_status.get("scope") if isinstance(ownership_status.get("scope"), dict) else {}
    blockers.extend(
        _pr_evidence_blockers(
            pr,
            pr_json_file,
            ownership_records=ownership_records,
            repository=ownership_status.get("repository"),
            expected_branch=ownership_scope.get("branch"),
        )
    )

    for record in ownership_records:
        role = record.get("role")
        if isinstance(role, str) and allowed_roles and role not in allowed_roles:
            blockers.append(
                _issue(
                    "ownership_role_unknown",
                    "ownership role is not allowed by role policy",
                    role=role,
                    ownership_id=record.get("id"),
                    path=record.get("path"),
                )
            )

    builder_records = [
        record
        for record in ownership_records
        if isinstance(record.get("role"), str) and record.get("role") in builder_roles
    ]
    builder_claimers = sorted(
        {
            record["claimer"]
            for record in builder_records
            if isinstance(record.get("claimer"), str) and record.get("claimer")
        }
    )
    review_findings, review_blockers = _current_actionable_review_findings(review_threads)
    blockers.extend(review_blockers)
    all_review_authors = sorted(
        {
            finding["author"]
            for finding in review_findings
            if isinstance(finding.get("author"), str) and finding.get("author")
        }
    )
    builder_review_authors = sorted(set(builder_claimers) & set(all_review_authors))
    review_authors = sorted(author for author in all_review_authors if author not in builder_claimers)
    if separation_required and policy is not None:
        if not builder_records:
            blockers.append(
                _issue(
                    "builder_ownership_missing",
                    "builder ownership evidence is missing",
                    builder_roles=sorted(builder_roles),
                )
            )
        if not review_authors:
            if builder_review_authors:
                blockers.append(
                    _issue(
                        "review_separation_conflict",
                        "builder and reviewer evidence must come from different claimers",
                        claimer=builder_review_authors[0],
                    )
                )
            else:
                blockers.append(
                    _issue(
                        "reviewer_evidence_missing",
                        "current reviewer evidence is missing",
                        reviewer_roles=sorted(reviewer_roles),
                    )
                )

    valid = not blockers
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": ROLE_READINESS_SCHEMA_VERSION,
        "packet": "role_readiness",
        "read_only": True,
        "side_effects": [],
        "root": str(root),
        "checked_at": now,
        "valid": valid,
        "role_ready": valid,
        "recommended_next_action": _role_readiness_recommendation(blockers),
        "blockers": blockers,
        "role_policy": policy_summary,
        "ownership": {
            "status_checksum": checksum_json(ownership_status),
            "valid": ownership_status.get("valid"),
            "counts": ownership_status.get("counts"),
            "records": ownership_records,
        },
        "repository": ownership_status.get("repository"),
        "scope": {
            "repo": repo,
            "branch": (
                ownership_status.get("scope", {}).get("branch")
                if isinstance(ownership_status.get("scope"), dict)
                else branch
            ),
            "task_id": task_id,
            "max_ownership_age_minutes": max_ownership_age_minutes,
        },
        "pr": _pr_summary(pr, pr_json_file),
        "review_evidence": {
            "path": str(review_threads_file) if review_threads_file else None,
            "present": review_threads is not None,
            "checksum": checksum_json(review_threads) if review_threads is not None else None,
            "actionable_review_comments": len(review_findings),
            "actionable_review_authors": review_authors,
            "ignored_builder_review_authors": builder_review_authors,
            "findings": [
                {
                    "id": finding.get("id"),
                    "thread_id": finding.get("thread_id"),
                    "file": finding.get("file"),
                    "line": finding.get("line"),
                    "author": finding.get("author"),
                }
                for finding in review_findings
            ],
        },
        "role_summary": {
            "allowed_roles": sorted(allowed_roles),
            "builder_roles": sorted(builder_roles),
            "reviewer_roles": sorted(reviewer_roles),
            "builder_claimers": builder_claimers,
            "reviewer_claimers": review_authors,
            "review_separation_required": separation_required,
        },
        "limitations": [
            "local_saved_evidence_only",
            "does_not_call_github",
            "does_not_assign_roles",
            "does_not_start_agent_pool",
            "does_not_invoke_review_agents_or_paid_review",
            "does_not_write_github",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
        ],
    }
