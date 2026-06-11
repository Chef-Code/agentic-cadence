from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.github_evidence import pr_check_failure_findings, review_thread_findings_from_payload
from codex_cadence.pr_readiness import PENDING_STATES, evaluate_pr_body_preflight

REVIEW_RESPONSE_PLAN_SCHEMA_VERSION = "review-response-plan.v1"
REVIEW_RESPONSE_MATERIALIZATION_PLAN_SCHEMA_VERSION = "review-response-materialization-plan.v1"
REVIEW_RESPONSE_MATERIALIZATION_TARGET_SCHEMA_VERSION = "review-response-materialization-target.v1"
ALLOWED_REVIEW_RESPONSE_WRITE_KINDS = {"update_pr_body", "post_review_comment"}


def _checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def _anchor_present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _parse_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _check_name(item: dict[str, Any]) -> str:
    if item.get("__typename") == "StatusContext":
        return str(item.get("context") or "").strip()
    return str(item.get("name") or "").strip()


def _check_workflow(item: dict[str, Any]) -> str:
    workflow = item.get("workflowName") or item.get("workflow")
    if isinstance(workflow, dict):
        workflow = workflow.get("name")
    if not workflow:
        suite = item.get("checkSuite")
        if isinstance(suite, dict):
            workflow_run = suite.get("workflowRun")
            if isinstance(workflow_run, dict):
                nested_workflow = workflow_run.get("workflow")
                if isinstance(nested_workflow, dict):
                    workflow = nested_workflow.get("name")
    return str(workflow or "").strip()


def _check_url(item: dict[str, Any]) -> str:
    url = item.get("detailsUrl") or item.get("targetUrl")
    return url.strip() if isinstance(url, str) else ""


def _check_state(item: dict[str, Any]) -> str:
    if item.get("__typename") == "StatusContext":
        return str(item.get("state") or "").strip().upper() or "UNKNOWN"
    status = str(item.get("status") or "").strip().upper()
    conclusion = str(item.get("conclusion") or "").strip().upper()
    if status == "COMPLETED":
        return conclusion or "UNKNOWN"
    return status or conclusion or "UNKNOWN"


def _pending_check_findings(pr: dict[str, Any]) -> list[dict[str, Any]]:
    checks = pr.get("statusCheckRollup")
    if not isinstance(checks, list):
        return []
    findings = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        name = _check_name(item)
        if not name:
            continue
        state = _check_state(item)
        if state not in PENDING_STATES and state != "UNKNOWN":
            continue
        finding: dict[str, Any] = {
            "id": _stable_id("pending-check", {"check": name, "state": state, "workflow": _check_workflow(item)}),
            "check": name,
            "state": state,
            "workflow": _check_workflow(item),
        }
        url = _check_url(item)
        if url:
            finding["url"] = url
        findings.append(finding)
    return findings


def _candidate_items(candidate_discovery: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if candidate_discovery is None:
        return [], []
    if not isinstance(candidate_discovery, dict):
        return [], [_issue("candidate_discovery_invalid", "candidate discovery packet must be a JSON object")]
    candidates = candidate_discovery.get("candidates")
    if not isinstance(candidates, list):
        return [], [_issue("candidate_discovery_invalid", "candidate discovery packet candidates must be a list")]
    return [candidate for candidate in candidates if isinstance(candidate, dict)], []


def _candidate_summary(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    summary = {
        "candidate_id": candidate.get("id"),
        "title": candidate.get("title"),
        "task_type": candidate.get("task_type"),
        "bucket": candidate.get("bucket"),
        "source": candidate.get("source"),
        "fingerprint": candidate.get("fingerprint"),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _match_check_candidate(candidates: list[dict[str, Any]], finding: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("source") != "pr_check_failure":
            continue
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        if evidence.get("id") and evidence.get("id") == finding.get("id"):
            return candidate
        if (
            evidence.get("check") == finding.get("check")
            and str(evidence.get("state") or "") == str(finding.get("state") or "")
            and str(evidence.get("workflow") or "") == str(finding.get("workflow") or "")
        ):
            return candidate
    return None


def _match_review_candidate(candidates: list[dict[str, Any]], finding: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("source") != "review_finding":
            continue
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        if evidence.get("id") and evidence.get("id") == finding.get("id"):
            return candidate
        if (
            evidence.get("thread_id") == finding.get("thread_id")
            and evidence.get("file") == finding.get("file")
            and str(evidence.get("line")) == str(finding.get("line"))
        ):
            return candidate
    return None


def _follow_up_task(
    *,
    title: str,
    recommended_next_action: str,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    task = {
        "title": title,
        "task_type": "execution",
        "bucket": "S",
        "recommended_next_action": recommended_next_action,
    }
    if candidate is not None:
        task.update(_candidate_summary(candidate))
    return task


def _failed_check_items(findings: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    seen_keys: set[tuple[str, str, str]] = set()
    for finding in sorted(findings, key=lambda item: (str(item.get("check")), str(item.get("state")), str(item.get("workflow")))):
        check = str(finding.get("check") or "")
        key = (check, str(finding.get("state") or ""), str(finding.get("workflow") or ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidate = _match_check_candidate(candidates, finding)
        item = {
            "id": _stable_id("failed-check-plan", {"check": check, "state": finding.get("state"), "workflow": finding.get("workflow")}),
            "kind": "failed_check",
            "recommended_next_action": "emit_executor_task",
            "group": {
                "type": "check",
                "check": check,
                "state": finding.get("state"),
                "workflow": finding.get("workflow", ""),
            },
            "evidence": {
                "id": finding.get("id"),
                "check": check,
                "state": finding.get("state"),
                "workflow": finding.get("workflow", ""),
                "url": finding.get("url"),
            },
            "follow_up_task": _follow_up_task(
                title=f"Resolve failing PR check: {check}",
                recommended_next_action="emit_executor_task",
                candidate=candidate,
            ),
        }
        item["evidence"] = {key: value for key, value in item["evidence"].items() if value not in (None, "")}
        items.append(item)
    return items


def _review_thread_items(findings: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for finding in findings:
        thread_id = str(finding.get("thread_id") or finding.get("id") or "unknown-thread")
        file_path = str(finding.get("file") or "unknown-file")
        grouped.setdefault((thread_id, file_path), []).append(finding)

    items = []
    for (thread_id, file_path), group_findings in sorted(grouped.items()):
        first_finding = group_findings[0]
        candidate = next(
            (match for finding in group_findings if (match := _match_review_candidate(candidates, finding)) is not None),
            None,
        )
        comments = [
            {
                key: value
                for key, value in {
                    "id": finding.get("id"),
                    "line": finding.get("line"),
                    "author": finding.get("author"),
                    "body": finding.get("body"),
                }.items()
                if value is not None
            }
            for finding in group_findings
        ]
        items.append(
            {
                "id": _stable_id("review-thread-plan", {"thread_id": thread_id, "file": file_path}),
                "kind": "review_thread",
                "recommended_next_action": "emit_executor_task",
                "group": {
                    "type": "review_thread",
                    "thread_id": thread_id,
                    "file": file_path,
                },
                "evidence": {
                    "thread_id": thread_id,
                    "file": file_path,
                    "comments": comments,
                },
                "follow_up_task": _follow_up_task(
                    title=f"Address review feedback in {file_path}",
                    recommended_next_action="emit_executor_task",
                    candidate=candidate,
                ),
            }
        )
        if first_finding.get("line") is not None:
            items[-1]["group"]["line"] = first_finding.get("line")
    return items


def _body_plan_item(missing_sections: list[str]) -> dict[str, Any]:
    return {
        "id": _stable_id("pr-body-plan", {"missing_sections": missing_sections}),
        "kind": "pr_body",
        "recommended_next_action": "update_pr_body",
        "missing_sections": missing_sections,
        "group": {
            "type": "pr_body",
            "missing_sections": missing_sections,
        },
        "follow_up_task": {
            "title": "Update PR body to satisfy required sections",
            "task_type": "documentation",
            "bucket": "XS",
            "recommended_next_action": "update_pr_body",
        },
    }


def _evidence_summary(
    *,
    pr: dict[str, Any],
    review_threads: Any,
    candidate_discovery: Any,
    evidence_captured_at: datetime | str | None,
    max_evidence_age_minutes: int | None,
    now: datetime | str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    captured = _parse_utc(evidence_captured_at)
    checked = _parse_utc(now) or datetime.now(timezone.utc)
    age_minutes = None
    stale = False
    waiting: list[dict[str, Any]] = []
    if captured is not None:
        age_minutes = (checked - captured).total_seconds() / 60
        if age_minutes < 0:
            stale = True
            waiting.append(
                _issue(
                    "pr_evidence_from_future",
                    "PR evidence timestamp is in the future; refresh evidence before planning a response",
                    captured_at=_format_utc(captured),
                    checked_at=_format_utc(checked),
                    age_minutes=round(age_minutes, 2),
                )
            )
        elif max_evidence_age_minutes is not None and age_minutes > max_evidence_age_minutes:
            stale = True
            waiting.append(
                _issue(
                    "pr_evidence_stale",
                    "saved PR evidence is stale; refresh evidence before planning a response",
                    captured_at=_format_utc(captured),
                    checked_at=_format_utc(checked),
                    age_minutes=round(age_minutes, 2),
                    max_age_minutes=max_evidence_age_minutes,
                )
            )
    freshness = "stale" if stale else "saved_input"
    limitations = (
        ["does_not_call_github", "refresh_saved_pr_json_before_merge"]
        if stale
        else [
            "does_not_call_github",
            "depends_on_saved_status_check_rollup",
            "depends_on_saved_review_threads",
        ]
    )
    return {
        "source": "saved_pr_json",
        "freshness": freshness,
        "live": False,
        "captured_at": _format_utc(captured),
        "checked_at": _format_utc(checked),
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "max_age_minutes": max_evidence_age_minutes,
        "stale": stale,
        "limitations": limitations,
        "pr_json_checksum": _checksum_json(pr),
        "review_threads_checksum": _checksum_json(review_threads) if review_threads is not None else None,
        "candidate_discovery_checksum": _checksum_json(candidate_discovery) if candidate_discovery is not None else None,
    }, waiting


def evaluate_review_response_plan(
    pr: dict[str, Any],
    *,
    review_threads: Any | None = None,
    candidate_discovery: Any | None = None,
    required_body_sections: list[str] | None = None,
    evidence_captured_at: datetime | str | None = None,
    max_evidence_age_minutes: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    plan_items: list[dict[str, Any]] = []
    candidates, candidate_blockers = _candidate_items(candidate_discovery)
    blockers.extend(candidate_blockers)
    evidence, evidence_waiting = _evidence_summary(
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=max_evidence_age_minutes,
        now=now,
    )
    waiting.extend(evidence_waiting)

    failed_checks = pr_check_failure_findings(pr)
    pending_checks = _pending_check_findings(pr)
    review_findings: list[dict[str, Any]] = []
    if review_threads is not None:
        review_findings, review_warnings = review_thread_findings_from_payload(review_threads)
        for warning in review_warnings:
            blockers.append(_issue("review_thread_evidence_invalid", warning))

    body_packet = evaluate_pr_body_preflight(str(pr.get("body") or ""), required_body_sections=required_body_sections or [])
    missing_sections = body_packet["template_summary"]["missing_sections"]

    if not evidence["stale"] and not blockers:
        plan_items.extend(_failed_check_items(failed_checks, candidates))
        plan_items.extend(_review_thread_items(review_findings, candidates))
        if missing_sections:
            plan_items.append(_body_plan_item(missing_sections))
    if pending_checks and not plan_items:
        waiting.append(
            _issue(
                "check_pending",
                "checks are still pending; wait before planning a response",
                checks=[finding["check"] for finding in pending_checks],
            )
        )

    if blockers:
        recommended_next_action = (
            "refresh_pr_evidence"
            if any(blocker["code"] == "review_thread_evidence_invalid" for blocker in blockers)
            else "operator_review"
        )
    elif evidence["stale"]:
        recommended_next_action = "refresh_pr_evidence"
    elif any(item["recommended_next_action"] == "update_pr_body" for item in plan_items):
        recommended_next_action = "update_pr_body"
    elif plan_items:
        recommended_next_action = "emit_executor_task"
    elif pending_checks:
        recommended_next_action = "wait_for_checks"
    elif str(pr.get("reviewDecision") or "").strip().upper() in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        recommended_next_action = "operator_review"
    else:
        recommended_next_action = "operator_review"

    files = sorted(
        {
            str(item["group"]["file"])
            for item in plan_items
            if item.get("group", {}).get("type") == "review_thread" and item.get("group", {}).get("file")
        }
    )
    packet = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REVIEW_RESPONSE_PLAN_SCHEMA_VERSION,
        "packet": "review_response_plan",
        "valid": not blockers,
        "plan_ready": bool(plan_items) and not blockers and not evidence["stale"],
        "recommended_next_action": recommended_next_action,
        "blockers": blockers,
        "waiting": waiting,
        "warnings": warnings,
        "summary": {
            "failed_checks": len(failed_checks),
            "pending_checks": len(pending_checks),
            "review_threads": len({item.get("thread_id") for item in review_findings}),
            "review_comments": len(review_findings),
            "files": files,
            "plan_items": len(plan_items),
        },
        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": pr.get("state"),
            "is_draft": pr.get("isDraft"),
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
            "review_decision": pr.get("reviewDecision"),
        },
        "evidence": {key: value for key, value in evidence.items() if value is not None},
        "plan_items": plan_items,
        "side_effects": [],
        "limitations": [
            "local_saved_evidence_only",
            "does_not_call_github",
            "does_not_resolve_review_threads",
            "does_not_post_comments",
            "does_not_update_pr_body",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
            "does_not_invoke_review_agents_or_paid_review",
        ],
    }
    return packet


def _response_plan_structural_blockers(response_plan: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(response_plan, dict):
        return [_issue("review_response_plan_invalid", "response plan must be a JSON object")]
    if response_plan.get("schema_version") != REVIEW_RESPONSE_PLAN_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "review_response_plan_schema_invalid",
                "response plan schema_version must be review-response-plan.v1",
            )
        )
    if response_plan.get("packet") != "review_response_plan":
        blockers.append(_issue("review_response_plan_packet_invalid", "response plan packet must be review_response_plan"))
    if response_plan.get("valid") is not True or response_plan.get("plan_ready") is not True:
        blockers.append(_issue("review_response_plan_not_ready", "response plan must be valid and plan_ready"))
    if response_plan.get("side_effects") != []:
        blockers.append(_issue("review_response_plan_side_effects_present", "response plan must be read-only"))
    return blockers


def _response_plan_recheck_blockers(
    *,
    response_plan: Any,
    pr: dict[str, Any],
    review_threads: Any,
    candidate_discovery: Any,
) -> list[dict[str, Any]]:
    if not isinstance(response_plan, dict):
        return []
    blockers: list[dict[str, Any]] = []
    plan_pr = response_plan.get("pr") if isinstance(response_plan.get("pr"), dict) else {}
    compared_pr_fields = {
        "number": pr.get("number"),
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
    }
    for field, current in compared_pr_fields.items():
        planned = plan_pr.get(field)
        if not _anchor_present(planned) or not _anchor_present(current):
            blockers.append(
                _issue(
                    "review_response_pr_target_anchor_missing",
                    "response materialization requires PR number, branch, base, and head anchors",
                    field=field,
                    expected=planned,
                    actual=current,
                )
            )
            continue
        if str(plan_pr.get(field)) != str(current):
            blockers.append(
                _issue(
                    "review_response_pr_target_mismatch",
                    "current PR target no longer matches the response plan",
                    field=field,
                    expected=plan_pr.get(field),
                    actual=current,
                )
            )
    evidence = response_plan.get("evidence") if isinstance(response_plan.get("evidence"), dict) else {}
    checksum_fields = (
        ("pr_json_checksum", _checksum_json(pr), "review_response_plan_pr_checksum_mismatch"),
        (
            "review_threads_checksum",
            _checksum_json(review_threads) if review_threads is not None else None,
            "review_response_plan_review_threads_checksum_mismatch",
        ),
        (
            "candidate_discovery_checksum",
            _checksum_json(candidate_discovery) if candidate_discovery is not None else None,
            "review_response_plan_candidate_checksum_mismatch",
        ),
    )
    for field, current_checksum, code in checksum_fields:
        planned_checksum = evidence.get(field)
        if planned_checksum is None and current_checksum is None:
            continue
        if planned_checksum != current_checksum:
            blockers.append(
                _issue(
                    code,
                    "saved response evidence no longer matches the response plan",
                    field=field,
                    expected=planned_checksum,
                    actual=current_checksum,
                )
            )
    return blockers


def _comment_ids_from_write(write: dict[str, Any]) -> list[str]:
    raw_ids = write.get("comment_ids")
    if raw_ids is None:
        raw_ids = [write.get("comment_id")]
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        return []
    ids = []
    seen = set()
    for value in raw_ids:
        if isinstance(value, str) and value.strip() and value not in seen:
            ids.append(value)
            seen.add(value)
    return sorted(ids)


def _normalize_review_response_writes(
    *,
    intended_writes: Any,
    actionable_comment_ids: set[str],
    required_body_sections: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    if not isinstance(intended_writes, list) or not intended_writes:
        return [], [_issue("review_response_write_plan_missing", "at least one intended review response write is required")]
    for index, write in enumerate(intended_writes, start=1):
        if not isinstance(write, dict):
            blockers.append(_issue("review_response_write_invalid", "intended write must be a JSON object", index=index))
            continue
        kind = write.get("kind")
        if kind not in ALLOWED_REVIEW_RESPONSE_WRITE_KINDS:
            blockers.append(
                _issue(
                    "review_response_write_kind_invalid",
                    "review response write kind is not allowed in this slice",
                    index=index,
                    kind=kind,
                    allowed_write_kinds=sorted(ALLOWED_REVIEW_RESPONSE_WRITE_KINDS),
                )
            )
            continue
        body = write.get("body")
        if not isinstance(body, str) or not body.strip():
            blockers.append(_issue("review_response_write_body_missing", "intended write body text is required", index=index, kind=kind))
            continue
        body_checksum = write.get("body_checksum")
        expected_checksum = _checksum_json(body)
        if body_checksum != expected_checksum:
            blockers.append(
                _issue(
                    "review_response_write_body_checksum_mismatch",
                    "intended write body checksum does not match body text",
                    index=index,
                    kind=kind,
                    expected=expected_checksum,
                    actual=body_checksum,
                )
            )
        item = {
            "kind": kind,
            "body": body,
            "body_checksum": expected_checksum,
        }
        if kind == "update_pr_body":
            body_preflight = evaluate_pr_body_preflight(body, required_body_sections=required_body_sections)
            item["pr_body_preflight"] = body_preflight
            if body_preflight.get("ready_to_publish") is not True:
                blockers.append(
                    _issue(
                        "review_response_pr_body_preflight_failed",
                        "intended PR body update does not satisfy PR body preflight",
                        index=index,
                        preflight_blockers=body_preflight.get("blockers", []),
                    )
                )
        if kind == "post_review_comment":
            comment_ids = _comment_ids_from_write(write)
            if not comment_ids:
                blockers.append(
                    _issue(
                        "review_response_comment_target_missing",
                        "post_review_comment writes require at least one review comment target",
                        index=index,
                    )
                )
            elif not set(comment_ids).issubset(actionable_comment_ids):
                blockers.append(
                    _issue(
                        "review_response_comment_target_not_actionable",
                        "post_review_comment target must still be an unresolved actionable review comment",
                        index=index,
                        comment_ids=comment_ids,
                    )
                )
            item["comment_ids"] = comment_ids
        normalized.append(item)

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in normalized:
        key = (item["kind"], tuple(item.get("comment_ids", [])), item["body_checksum"])
        deduped.setdefault(key, item)
    return sorted(deduped.values(), key=lambda item: (item["kind"], item.get("comment_ids", []), item["body_checksum"])), blockers


def evaluate_review_response_materialization_plan(
    response_plan: Any,
    *,
    pr: dict[str, Any],
    review_threads: Any | None = None,
    candidate_discovery: Any | None = None,
    intended_writes: Any,
    required_body_sections: list[str] | None = None,
    evidence_captured_at: datetime | str | None = None,
    max_evidence_age_minutes: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a read-only, operator-approval target for review response writes."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_sections = [section for section in (required_body_sections or []) if section.strip()]
    evidence, freshness_blockers = _evidence_summary(
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=max_evidence_age_minutes,
        now=now,
    )
    blockers.extend(freshness_blockers)
    blockers.extend(_response_plan_structural_blockers(response_plan))
    blockers.extend(
        _response_plan_recheck_blockers(
            response_plan=response_plan,
            pr=pr,
            review_threads=review_threads,
            candidate_discovery=candidate_discovery,
        )
    )

    actionable_comment_ids: set[str] = set()
    if review_threads is not None:
        review_findings, review_warnings = review_thread_findings_from_payload(review_threads)
        for warning in review_warnings:
            blockers.append(_issue("review_thread_evidence_invalid", warning))
        actionable_comment_ids = {str(finding.get("id")) for finding in review_findings if finding.get("id")}
    write_plan, write_blockers = _normalize_review_response_writes(
        intended_writes=intended_writes,
        actionable_comment_ids=actionable_comment_ids,
        required_body_sections=required_sections,
    )
    blockers.extend(write_blockers)
    pr_number = str(pr.get("number")) if pr.get("number") is not None else None
    target_writes = [
        {
            key: value
            for key, value in {
                "kind": write.get("kind"),
                "comment_ids": write.get("comment_ids"),
                "body_checksum": write.get("body_checksum"),
            }.items()
            if value is not None
        }
        for write in write_plan
    ]
    target = {
        "schema_version": REVIEW_RESPONSE_MATERIALIZATION_TARGET_SCHEMA_VERSION,
        "packet": "review_response_materialization_target",
        "operation": "review_response_materialization",
        "response_plan_checksum": _checksum_json(response_plan),
        "pr_number": pr_number,
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
        "write_kinds": sorted({write["kind"] for write in write_plan}),
        "writes": target_writes,
    }
    valid = not blockers
    blocker_codes = {blocker.get("code") for blocker in blockers}
    if valid and write_plan:
        recommended_next_action = "approve_review_response_materialization"
    elif blocker_codes & {"pr_evidence_stale", "pr_evidence_from_future", "review_thread_evidence_invalid"}:
        recommended_next_action = "refresh_pr_evidence"
    else:
        recommended_next_action = "address_blockers"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REVIEW_RESPONSE_MATERIALIZATION_PLAN_SCHEMA_VERSION,
        "packet": "review_response_materialization_plan",
        "valid": valid,
        "plan_ready": valid and bool(write_plan),
        "decision": "ready" if valid and write_plan else "blocked",
        "recommended_next_action": recommended_next_action,
        "operator_confirmation_required": True,
        "approval_state": "not_approved",
        "execution_authority": "none",
        "github_write_started": False,
        "target": target,
        "target_checksum": _checksum_json(target),
        "evidence": {key: value for key, value in evidence.items() if value is not None},
        "response_plan_checksum": _checksum_json(response_plan),
        "write_plan": write_plan,
        "blockers": blockers,
        "warnings": warnings,
        "side_effects": [],
        "limitations": [
            "local_saved_evidence_only",
            "does_not_call_github",
            "does_not_resolve_review_threads",
            "does_not_post_comments",
            "does_not_update_pr_body",
            "does_not_create_branch_commit_push_or_pr",
            "does_not_merge_release_or_publish_packages",
            "does_not_invoke_review_agents_or_paid_review",
        ],
    }
