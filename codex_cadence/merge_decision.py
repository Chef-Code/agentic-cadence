from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.github_evidence import review_thread_findings_from_payload
from codex_cadence.policy_audit import checksum_json
from codex_cadence.store import read_json

MERGE_DECISION_PLAN_SCHEMA_VERSION = "merge-decision-plan.v1"
AUDIT_REPLAY_SCHEMA_VERSION = "audit-replay.v1"
CONTROLLED_PR_CYCLE_SCHEMA_VERSION = "controlled-pr-cycle.v1"
ROLE_READINESS_SCHEMA_VERSION = "role-readiness.v1"


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _target_from_pr_json(pr: dict[str, Any]) -> dict[str, Any]:
    number = pr.get("number")
    return {
        "number": str(number) if number is not None else None,
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
    }


def _target_from_review_threads(review_threads: dict[str, Any]) -> dict[str, Any]:
    current: Any = review_threads
    for key in ("data", "repository", "pullRequest"):
        if not isinstance(current, dict) or key not in current:
            return {"number": None}
        current = current[key]
    number = current.get("number") if isinstance(current, dict) else None
    return {"number": str(number) if number is not None else None}


def _target_from_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    pr = readiness.get("pr") if isinstance(readiness.get("pr"), dict) else {}
    number = pr.get("number")
    return {
        "number": str(number) if number is not None else None,
        "head_ref": pr.get("head_ref"),
        "base_ref": pr.get("base_ref"),
        "head_sha": pr.get("head_sha"),
    }


def _target_from_controlled_cycle(packet: dict[str, Any]) -> dict[str, Any]:
    pr = packet.get("pr") if isinstance(packet.get("pr"), dict) else {}
    number = pr.get("number")
    return {
        "number": str(number) if number is not None else None,
        "head_ref": pr.get("head_ref"),
        "base_ref": pr.get("base_ref"),
        "head_sha": pr.get("head_sha"),
    }


def _target_from_role_readiness(packet: dict[str, Any]) -> dict[str, Any]:
    pr = packet.get("pr") if isinstance(packet.get("pr"), dict) else {}
    number = pr.get("number")
    return {
        "number": str(number) if number is not None else None,
        "head_ref": pr.get("head_ref"),
        "base_ref": pr.get("base_ref"),
        "head_sha": pr.get("head_sha"),
    }


def _target_blockers(step: str, target: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for field in ("number", "head_ref", "base_ref", "head_sha"):
        actual = target.get(field)
        expected_value = expected.get(field)
        if not _non_empty_string(actual) or not _non_empty_string(expected_value):
            blockers.append(
                _issue(
                    "merge_decision_pr_target_anchor_missing",
                    "merge decision evidence must identify PR number, branch, base, and head SHA",
                    step=step,
                    field=field,
                    expected=expected_value,
                    actual=actual,
                )
            )
        elif str(actual) != str(expected_value):
            blockers.append(
                _issue(
                    "merge_decision_pr_target_mismatch",
                    "merge decision evidence PR target does not match the saved PR target",
                    step=step,
                    field=field,
                    expected=expected_value,
                    actual=actual,
                )
            )
    return blockers


def _review_thread_target_blockers(target: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    actual = target.get("number")
    expected_value = expected.get("number")
    if not _non_empty_string(actual):
        return [
            _issue(
                "merge_decision_review_threads_pr_anchor_missing",
                "review-thread evidence must identify the source PR number",
                expected=expected_value,
                actual=actual,
            )
        ]
    if _non_empty_string(expected_value) and str(actual) != str(expected_value):
        return [
            _issue(
                "merge_decision_review_threads_pr_mismatch",
                "review-thread evidence PR number does not match the saved PR target",
                expected=expected_value,
                actual=actual,
            )
        ]
    return []


def _pr_review_decision_blockers(pr: dict[str, Any]) -> list[dict[str, Any]]:
    decision = pr.get("reviewDecision")
    normalized = str(decision or "").strip().upper()
    if normalized == "CHANGES_REQUESTED":
        return [_issue("review_changes_requested", "review changes are requested")]
    if normalized == "REVIEW_REQUIRED":
        return [_issue("review_required", "required review is still missing")]
    return []


def _is_empty_list(value: Any) -> bool:
    return isinstance(value, list) and not value


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_sha256_checksum(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    return value.startswith(prefix) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _controlled_pr_cycle_audit_reference_blockers(controlled_pr_cycle: dict[str, Any]) -> list[dict[str, Any]]:
    audit_record = controlled_pr_cycle.get("audit_record")
    if not isinstance(audit_record, dict):
        return [
            _issue(
                "controlled_pr_cycle_audit_reference_invalid",
                "controlled-pr-cycle evidence must include its audit_record reference",
            )
        ]

    blockers: list[dict[str, Any]] = []
    if audit_record.get("event") != "controlled_pr_cycle":
        blockers.append(
            _issue(
                "controlled_pr_cycle_audit_reference_invalid",
                "controlled-pr-cycle audit_record event is invalid",
                actual=audit_record.get("event"),
            )
        )
    for field in ("path", "recorded_at", "audit_chain_version"):
        if not _non_empty_string(audit_record.get(field)):
            blockers.append(
                _issue(
                    "controlled_pr_cycle_audit_reference_invalid",
                    f"controlled-pr-cycle audit_record {field} is required",
                    field=f"audit_record.{field}",
                )
            )
    chain_index = audit_record.get("chain_index")
    if not isinstance(chain_index, int) or isinstance(chain_index, bool) or chain_index < 1:
        blockers.append(
            _issue(
                "controlled_pr_cycle_audit_reference_invalid",
                "controlled-pr-cycle audit_record chain_index must be a positive integer",
                field="audit_record.chain_index",
            )
        )
    for field in ("previous_event_hash", "event_hash"):
        if not _is_sha256_checksum(audit_record.get(field)):
            blockers.append(
                _issue(
                    "controlled_pr_cycle_audit_reference_invalid",
                    f"controlled-pr-cycle audit_record {field} must be a sha256 checksum",
                    field=f"audit_record.{field}",
                )
            )

    expected_payload_checksum = checksum_json({key: value for key, value in controlled_pr_cycle.items() if key != "audit_record"})
    payload_checksum = audit_record.get("payload_checksum")
    if not _is_sha256_checksum(payload_checksum):
        blockers.append(
            _issue(
                "controlled_pr_cycle_audit_reference_invalid",
                "controlled-pr-cycle audit_record payload_checksum must be a sha256 checksum",
                field="audit_record.payload_checksum",
            )
        )
    elif payload_checksum != expected_payload_checksum:
        blockers.append(
            _issue(
                "controlled_pr_cycle_audit_checksum_mismatch",
                "controlled-pr-cycle audit_record payload_checksum does not match supplied packet",
                expected=expected_payload_checksum,
                actual=payload_checksum,
            )
        )
    return blockers


def _pr_readiness_summary_blockers(
    readiness: dict[str, Any],
    *,
    review_findings_count: int | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if readiness.get("decision") != "ready":
        blockers.append(
            _issue(
                "pr_readiness_decision_not_ready",
                "PR readiness decision must be ready before merge decision planning",
                decision=readiness.get("decision"),
            )
        )
    if readiness.get("recommended_next_action") != "merge_after_operator_confirmation":
        blockers.append(
            _issue(
                "pr_readiness_action_not_merge_ready",
                "PR readiness must recommend merge after operator confirmation",
                recommended_next_action=readiness.get("recommended_next_action"),
            )
        )

    for field in ("blockers", "waiting"):
        value = readiness.get(field)
        if not isinstance(value, list):
            blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness evidence field must be a list", field=field))
        elif value:
            for item in value:
                if isinstance(item, dict):
                    blockers.append(item)
            blockers.append(
                _issue(
                    "pr_readiness_not_ready",
                    "PR readiness evidence contains blockers or waiting items",
                    field=field,
                    count=len(value),
                )
            )

    check_summary = readiness.get("check_summary")
    if not isinstance(check_summary, dict):
        blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness evidence is missing check_summary"))
    else:
        failed = _int_value(check_summary.get("failed"))
        pending = _int_value(check_summary.get("pending"))
        required_checks = check_summary.get("required")
        if failed is None or pending is None:
            blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness check_summary is missing counts"))
        elif failed > 0 or pending > 0:
            blockers.append(
                _issue(
                    "pr_readiness_checks_not_clear",
                    "PR readiness evidence reports failed or pending checks",
                    failed=failed,
                    pending=pending,
                )
            )
        if not isinstance(required_checks, list) or not required_checks:
            blockers.append(
                _issue(
                    "pr_readiness_required_checks_missing",
                    "PR readiness evidence must include the required check contract",
                )
            )

    review_summary = readiness.get("review_summary")
    if not isinstance(review_summary, dict):
        blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness evidence is missing review_summary"))
    else:
        if review_summary.get("changes_requested") is True:
            blockers.append(_issue("review_changes_requested", "review changes are requested"))
        if review_summary.get("review_required") is True:
            blockers.append(_issue("review_required", "required review is still missing"))
        if not isinstance(review_summary.get("changes_requested"), bool) or not isinstance(
            review_summary.get("review_required"),
            bool,
        ):
            blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness review_summary is missing booleans"))

    template_summary = readiness.get("template_summary")
    if not isinstance(template_summary, dict):
        blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness evidence is missing template_summary"))
    else:
        missing_sections = template_summary.get("missing_sections")
        required_sections = template_summary.get("required_sections")
        if not _is_empty_list(missing_sections):
            blockers.append(
                _issue(
                    "pr_readiness_template_sections_missing",
                    "PR readiness evidence reports missing PR body sections",
                    missing_sections=missing_sections,
                )
            )
        if not isinstance(required_sections, list) or not required_sections:
            blockers.append(
                _issue(
                    "pr_readiness_template_contract_missing",
                    "PR readiness evidence must include the PR body/template contract",
                )
            )

    readiness_evidence = readiness.get("readiness_evidence")
    if not isinstance(readiness_evidence, dict):
        blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness evidence is missing readiness_evidence"))
    elif readiness_evidence.get("stale") is True or readiness_evidence.get("freshness") == "stale":
        blockers.append(_issue("pr_readiness_evidence_stale", "PR readiness evidence is stale"))
    elif not isinstance(readiness_evidence.get("stale"), bool):
        blockers.append(_issue("pr_readiness_evidence_incomplete", "PR readiness stale marker is missing or invalid"))

    if review_findings_count is not None:
        review_feedback_summary = readiness.get("review_feedback_summary")
        if not isinstance(review_feedback_summary, dict):
            blockers.append(
                _issue(
                    "pr_readiness_review_feedback_missing",
                    "PR readiness evidence must include review feedback summary when review threads are supplied",
                )
            )
        else:
            readiness_count = _int_value(review_feedback_summary.get("unresolved_actionable_comments"))
            if readiness_count is None:
                blockers.append(
                    _issue(
                        "pr_readiness_evidence_incomplete",
                        "PR readiness review feedback summary is missing unresolved-actionable count",
                    )
                )
            elif readiness_count != review_findings_count:
                blockers.append(
                    _issue(
                        "pr_readiness_review_feedback_mismatch",
                        "PR readiness review feedback summary does not match supplied review-thread evidence",
                        expected=review_findings_count,
                        actual=readiness_count,
                    )
                )
    return blockers


def _read_packet(path: Path, *, code: str, label: str) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        packet = read_json(path)
    except (OSError, ValueError) as exc:
        return None, [_issue(code, f"{label} could not be read as JSON", path=str(path), error=str(exc))]
    if not isinstance(packet, dict):
        return None, [_issue(code, f"{label} must be a JSON object", path=str(path))]
    return packet, []


def _recommendation(blockers: list[dict[str, Any]], pr_readiness: dict[str, Any], role_readiness: dict[str, Any] | None) -> str:
    if not blockers:
        return "merge_after_operator_confirmation"
    codes = {blocker.get("code") for blocker in blockers}
    if "unresolved_review_comment" in codes or "review_changes_requested" in codes:
        return "respond_to_review"
    if "review_thread_evidence_invalid" in codes or "pr_readiness_evidence_stale" in codes:
        return "refresh_pr_evidence"
    if "role_readiness_blocked" in codes and isinstance(role_readiness, dict):
        action = role_readiness.get("recommended_next_action")
        if _non_empty_string(action):
            return str(action)
    action = pr_readiness.get("recommended_next_action") if isinstance(pr_readiness, dict) else None
    if _non_empty_string(action) and action != "merge_after_operator_confirmation":
        return str(action)
    return "address_blockers"


def plan_merge_decision(
    *,
    root: Path,
    pr: Any,
    review_threads: Any,
    pr_readiness: Any,
    audit_replay: Any,
    controlled_pr_cycle: Any,
    role_readiness: Any | None = None,
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(strict=False)
    files = dict(files or {})
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    expected_target: dict[str, Any] = {}

    if not isinstance(pr, dict):
        blockers.append(_issue("merge_decision_pr_json_invalid", "PR JSON evidence must be a JSON object"))
    else:
        expected_target = _target_from_pr_json(pr)
        blockers.extend(_pr_review_decision_blockers(pr))

    review_findings_count: int | None = None
    if review_threads is None:
        blockers.append(_issue("merge_decision_review_threads_missing", "review-thread evidence is required"))
    elif not isinstance(review_threads, dict):
        blockers.append(_issue("merge_decision_review_threads_invalid", "review-thread evidence must be a JSON object"))
    else:
        review_findings, review_warnings = review_thread_findings_from_payload(review_threads)
        review_findings_count = len(review_findings)
        if expected_target:
            blockers.extend(_review_thread_target_blockers(_target_from_review_threads(review_threads), expected_target))
        for warning in review_warnings:
            blockers.append(_issue("review_thread_evidence_invalid", warning))
        for finding in review_findings:
            blockers.append(
                _issue(
                    "unresolved_review_comment",
                    "unresolved actionable review comment blocks merge decision planning",
                    id=finding.get("id"),
                    thread_id=finding.get("thread_id"),
                    file=finding.get("file"),
                    line=finding.get("line"),
                )
            )

    if not isinstance(pr_readiness, dict):
        blockers.append(_issue("merge_decision_pr_readiness_invalid", "PR readiness evidence must be a JSON object"))
    else:
        if pr_readiness.get("ready_to_merge") is not True:
            readiness_blocker_count = len(blockers)
            for blocker in pr_readiness.get("blockers", []):
                if isinstance(blocker, dict):
                    blockers.append(blocker)
            for blocker in pr_readiness.get("waiting", []):
                if isinstance(blocker, dict):
                    blockers.append(blocker)
            if len(blockers) == readiness_blocker_count:
                blockers.append(_issue("pr_readiness_not_ready", "PR readiness evidence is not ready to merge"))
        else:
            blockers.extend(_pr_readiness_summary_blockers(pr_readiness, review_findings_count=review_findings_count))
        if expected_target:
            blockers.extend(_target_blockers("pr_readiness", _target_from_readiness(pr_readiness), expected_target))

    if not isinstance(audit_replay, dict):
        blockers.append(_issue("merge_decision_audit_replay_invalid", "audit replay evidence must be a JSON object"))
    else:
        if audit_replay.get("schema_version") != AUDIT_REPLAY_SCHEMA_VERSION:
            blockers.append(
                _issue(
                    "merge_decision_audit_replay_schema_invalid",
                    "audit replay schema_version is invalid",
                    expected=AUDIT_REPLAY_SCHEMA_VERSION,
                    actual=audit_replay.get("schema_version"),
                )
            )
        if audit_replay.get("packet") != "audit_replay":
            blockers.append(_issue("merge_decision_audit_replay_invalid", "audit replay packet type is invalid"))
        if audit_replay.get("valid") is not True:
            blockers.append(_issue("audit_replay_blocked", "audit replay evidence must be valid"))
        events_by_type = audit_replay.get("events_by_type") if isinstance(audit_replay.get("events_by_type"), dict) else {}
        controlled_cycle_events = events_by_type.get("controlled_pr_cycle")
        if not isinstance(controlled_cycle_events, int) or controlled_cycle_events < 1:
            blockers.append(
                _issue(
                    "audit_replay_controlled_pr_cycle_missing",
                    "audit replay evidence must include controlled_pr_cycle audit evidence",
                )
            )

    if not isinstance(controlled_pr_cycle, dict):
        blockers.append(
            _issue(
                "merge_decision_controlled_pr_cycle_missing",
                "controlled-pr-cycle evidence is required before merge decision planning",
            )
        )
    else:
        if controlled_pr_cycle.get("schema_version") != CONTROLLED_PR_CYCLE_SCHEMA_VERSION:
            blockers.append(
                _issue(
                    "merge_decision_controlled_pr_cycle_schema_invalid",
                    "controlled-pr-cycle schema_version is invalid",
                    expected=CONTROLLED_PR_CYCLE_SCHEMA_VERSION,
                    actual=controlled_pr_cycle.get("schema_version"),
                )
            )
        if controlled_pr_cycle.get("packet") != "controlled_pr_cycle":
            blockers.append(_issue("merge_decision_controlled_pr_cycle_invalid", "controlled-pr-cycle packet type is invalid"))
        if controlled_pr_cycle.get("valid") is not True or controlled_pr_cycle.get("controlled_pr_cycle_status") != "completed":
            blockers.append(_issue("controlled_pr_cycle_not_completed", "controlled-pr-cycle evidence must be completed"))
        blockers.extend(_controlled_pr_cycle_audit_reference_blockers(controlled_pr_cycle))
        if expected_target:
            blockers.extend(
                _target_blockers("controlled_pr_cycle", _target_from_controlled_cycle(controlled_pr_cycle), expected_target)
            )

    role_packet = role_readiness if isinstance(role_readiness, dict) else None
    if role_readiness is not None and not isinstance(role_readiness, dict):
        blockers.append(_issue("role_readiness_invalid", "role-readiness evidence must be a JSON object"))
    elif role_packet is not None:
        if role_packet.get("schema_version") != ROLE_READINESS_SCHEMA_VERSION:
            blockers.append(
                _issue(
                    "role_readiness_schema_invalid",
                    "role-readiness schema_version is invalid",
                    expected=ROLE_READINESS_SCHEMA_VERSION,
                    actual=role_packet.get("schema_version"),
                )
            )
        if role_packet.get("packet") != "role_readiness":
            blockers.append(_issue("role_readiness_invalid", "role-readiness packet type is invalid"))
        if role_packet.get("valid") is not True or role_packet.get("role_ready") is not True:
            blockers.append(_issue("role_readiness_blocked", "role-readiness evidence is not ready"))
            for blocker in role_packet.get("blockers", []):
                if isinstance(blocker, dict):
                    blockers.append(blocker)
        if expected_target:
            blockers.extend(_target_blockers("role_readiness", _target_from_role_readiness(role_packet), expected_target))

    valid = not blockers
    recommended_next_action = _recommendation(blockers, pr_readiness if isinstance(pr_readiness, dict) else {}, role_packet)
    checksums = {
        "pr": checksum_json(pr) if isinstance(pr, dict) else None,
        "review_threads": checksum_json(review_threads) if isinstance(review_threads, dict) else None,
        "pr_readiness": checksum_json(pr_readiness) if isinstance(pr_readiness, dict) else None,
        "audit_replay": checksum_json(audit_replay) if isinstance(audit_replay, dict) else None,
        "controlled_pr_cycle": checksum_json(controlled_pr_cycle) if isinstance(controlled_pr_cycle, dict) else None,
        "role_readiness": checksum_json(role_packet) if role_packet is not None else None,
    }
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": MERGE_DECISION_PLAN_SCHEMA_VERSION,
        "packet": "merge_decision_plan",
        "plan_id": f"merge-decision-plan-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
        "read_only": True,
        "valid": valid,
        "merge_decision_status": "ready" if valid else "blocked",
        "recommended_next_action": recommended_next_action,
        "operator_confirmation_required": True,
        "merge_started": False,
        "github_write_started": False,
        "side_effects": [],
        "command_trace": [],
        "root": str(root),
        "pr": expected_target,
        "controlled_pr_cycle": {
            "present": isinstance(controlled_pr_cycle, dict),
            "status": controlled_pr_cycle.get("controlled_pr_cycle_status") if isinstance(controlled_pr_cycle, dict) else None,
            "checksum": checksums["controlled_pr_cycle"],
        },
        "pr_readiness": {
            "ready_to_merge": pr_readiness.get("ready_to_merge") if isinstance(pr_readiness, dict) else None,
            "decision": pr_readiness.get("decision") if isinstance(pr_readiness, dict) else None,
            "recommended_next_action": pr_readiness.get("recommended_next_action") if isinstance(pr_readiness, dict) else None,
            "checksum": checksums["pr_readiness"],
        },
        "role_readiness": {
            "present": role_packet is not None,
            "role_ready": role_packet.get("role_ready") if role_packet is not None else None,
            "recommended_next_action": role_packet.get("recommended_next_action") if role_packet is not None else None,
            "checksum": checksums["role_readiness"],
        },
        "files": {key: str(value) for key, value in files.items()},
        "checksums": {key: value for key, value in checksums.items() if value is not None},
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "local_saved_evidence_only",
            "does_not_call_github",
            "does_not_write_github",
            "does_not_execute_git_commands",
            "does_not_merge",
            "does_not_delete_branches",
            "does_not_create_tags",
            "does_not_release",
            "does_not_publish_packages",
            "does_not_assign_roles",
            "does_not_schedule_agents",
            "does_not_continue_loop",
        ],
    }


def plan_merge_decision_from_files(
    *,
    root: Path,
    pr_json_file: Path,
    review_threads_file: Path,
    pr_readiness_file: Path,
    audit_replay_file: Path,
    controlled_pr_cycle_file: Path,
    role_readiness_file: Path | None = None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    blockers: list[dict[str, Any]] = []
    file_inputs: dict[str, Path | None] = {
        "pr": pr_json_file,
        "review_threads": review_threads_file,
        "pr_readiness": pr_readiness_file,
        "audit_replay": audit_replay_file,
        "controlled_pr_cycle": controlled_pr_cycle_file,
        "role_readiness": role_readiness_file,
    }
    for key, path in file_inputs.items():
        if path is None:
            continue
        packet, read_blockers = _read_packet(path, code=f"merge_decision_{key}_unreadable", label=key.replace("_", " "))
        inputs[key] = packet
        blockers.extend(read_blockers)
    payload = plan_merge_decision(
        root=root,
        pr=inputs.get("pr"),
        review_threads=inputs.get("review_threads"),
        pr_readiness=inputs.get("pr_readiness"),
        audit_replay=inputs.get("audit_replay"),
        controlled_pr_cycle=inputs.get("controlled_pr_cycle"),
        role_readiness=inputs.get("role_readiness"),
        files={key: str(path) for key, path in file_inputs.items() if path is not None},
    )
    if blockers:
        payload["valid"] = False
        payload["merge_decision_status"] = "blocked"
        payload["recommended_next_action"] = "inspect_merge_decision_inputs"
        payload["blockers"] = [*blockers, *payload["blockers"]]
    return payload
