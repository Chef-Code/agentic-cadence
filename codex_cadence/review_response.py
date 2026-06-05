from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.github_evidence import pr_check_failure_findings, review_thread_findings_from_payload
from codex_cadence.pr_readiness import PENDING_STATES, evaluate_pr_body_preflight

REVIEW_RESPONSE_PLAN_SCHEMA_VERSION = "review-response-plan.v1"


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
    return {
        "source": "saved_pr_json",
        "captured_at": _format_utc(captured),
        "checked_at": _format_utc(checked),
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "max_age_minutes": max_evidence_age_minutes,
        "stale": stale,
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
