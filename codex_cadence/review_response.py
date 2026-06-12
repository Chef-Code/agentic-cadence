from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.github_evidence import (
    POST_WRITE_PR_EVIDENCE_GATE_SCHEMA_VERSION,
    actionable_review_body,
    pr_check_failure_findings,
    review_thread_comment_nodes,
    review_thread_findings_from_payload,
    review_threads_nodes,
)
from codex_cadence.policy_audit import (
    append_audit_record,
    review_response_materialization_intent_audit_record,
    review_response_materialization_result_audit_record,
)
from codex_cadence.pr_readiness import PENDING_STATES, evaluate_pr_body_preflight
from codex_cadence.store import utc_now

REVIEW_RESPONSE_PLAN_SCHEMA_VERSION = "review-response-plan.v1"
REVIEW_RESPONSE_MATERIALIZATION_PLAN_SCHEMA_VERSION = "review-response-materialization-plan.v1"
REVIEW_RESPONSE_MATERIALIZATION_TARGET_SCHEMA_VERSION = "review-response-materialization-target.v1"
REVIEW_RESPONSE_MATERIALIZATION_SCHEMA_VERSION = "review-response-materialization.v1"
REVIEW_THREAD_RESOLUTION_PLAN_SCHEMA_VERSION = "review-thread-resolution-plan.v1"
REVIEW_THREAD_RESOLUTION_TARGET_SCHEMA_VERSION = "review-thread-resolution-target.v1"
REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_PREFIX = "approve-review-response:"
REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET_ENV = "CADENCE_REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET"
ALLOWED_REVIEW_RESPONSE_WRITE_KINDS = {"update_pr_body", "post_review_comment"}
REVIEW_THREAD_REPLY_MUTATION = (
    "mutation($threadId:ID!,$body:String!){"
    "addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId,body:$body}){"
    "comment{id url}}}"
)


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


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _run_process(cwd: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(argv[0])
    command = [executable or argv[0], *argv[1:]]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def _command_display(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)


def _materialization_command_trace(
    *,
    label: str,
    argv: list[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    resolved_argv = list(result.args) if isinstance(result.args, list) else []
    return {
        "label": label,
        "argv": argv,
        "resolved_executable": resolved_argv[0] if resolved_argv else None,
        "command": _command_display(argv),
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _materialization_error_trace(*, label: str, message: str) -> dict[str, Any]:
    return {
        "label": label,
        "argv": [],
        "resolved_executable": None,
        "command": label,
        "returncode": 1,
        "stdout": "",
        "stderr": message,
    }


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


POST_WRITE_FRESHNESS_BLOCKER_CODES = {
    "materialization_result_invalid",
    "materialization_result_not_materialized",
    "materialization_result_not_approved",
    "materialization_result_schema_invalid",
    "materialization_result_write_missing",
    "materialization_result_timestamp_invalid",
    "materialization_result_timestamp_missing",
    "materialization_result_packet_unsupported",
    "materialization_target_anchor_missing",
    "github_evidence_sync_missing",
    "github_evidence_sync_schema_invalid",
    "github_evidence_sync_packet_invalid",
    "github_evidence_sync_not_saved",
    "github_evidence_sync_timestamp_invalid",
    "github_evidence_sync_timestamp_missing",
    "github_evidence_sync_from_future",
    "github_evidence_sync_stale",
    "github_evidence_sync_before_materialization",
    "post_write_pr_target_anchor_missing",
    "post_write_pr_target_mismatch",
    "refreshed_pr_evidence_unreadable",
    "refreshed_pr_evidence_invalid_json",
    "refreshed_pr_evidence_invalid",
    "refreshed_review_threads_unreadable",
    "refreshed_review_threads_invalid_json",
    "refreshed_review_threads_invalid",
}


def _dedupe_target_thread_ids(target_thread_ids: Any) -> tuple[list[str], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(target_thread_ids, list) or not target_thread_ids:
        return [], [_issue("review_thread_resolution_targets_missing", "at least one target review thread id is required")]
    thread_ids: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(target_thread_ids, start=1):
        if not isinstance(value, str) or not value.strip():
            blockers.append(
                _issue(
                    "review_thread_resolution_target_invalid",
                    "target review thread ids must be non-empty strings",
                    index=index,
                    value=value,
                )
            )
            continue
        thread_id = value.strip()
        if thread_id in seen:
            continue
        seen.add(thread_id)
        thread_ids.append(thread_id)
    if not thread_ids and not blockers:
        blockers.append(_issue("review_thread_resolution_targets_missing", "at least one target review thread id is required"))
    return thread_ids, blockers


def _pr_resolution_anchors(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "pr_number": str(pr.get("number")) if pr.get("number") is not None else None,
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
    }


def _review_response_materialization_resolution_summary(
    response_materialization: Any,
) -> tuple[dict[str, Any], set[str], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    approved_thread_ids: set[str] = set()
    if not isinstance(response_materialization, dict):
        return (
            {},
            approved_thread_ids,
            [_issue("review_response_materialization_missing", "review-response materialization evidence is required")],
        )
    if response_materialization.get("schema_version") != REVIEW_RESPONSE_MATERIALIZATION_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "review_response_materialization_schema_invalid",
                "review-response materialization schema_version is unsupported",
                expected=REVIEW_RESPONSE_MATERIALIZATION_SCHEMA_VERSION,
                actual=response_materialization.get("schema_version"),
            )
        )
    if response_materialization.get("packet") != "review_response_materialization":
        blockers.append(
            _issue(
                "review_response_materialization_packet_invalid",
                "review-response materialization packet must be review_response_materialization",
            )
        )
    if response_materialization.get("valid") is not True or response_materialization.get("decision") != "materialized":
        blockers.append(
            _issue(
                "review_response_materialization_not_materialized",
                "thread resolution planning requires a successful review-response materialization result",
                valid=response_materialization.get("valid"),
                decision=response_materialization.get("decision"),
            )
        )
    if response_materialization.get("approval_state") != "approved":
        blockers.append(
            _issue(
                "review_response_materialization_not_approved",
                "thread resolution planning requires an operator-approved review-response materialization result",
                approval_state=response_materialization.get("approval_state"),
            )
        )
    if response_materialization.get("github_write_started") is not True:
        blockers.append(
            _issue(
                "review_response_materialization_write_missing",
                "review-response materialization must show a GitHub write before resolution can be planned",
            )
        )
    for write in response_materialization.get("github_writes", []):
        if not isinstance(write, dict) or write.get("kind") != "post_review_comment":
            continue
        thread_id = write.get("thread_id")
        if isinstance(thread_id, str) and thread_id.strip():
            approved_thread_ids.add(thread_id.strip())
    pr = response_materialization.get("pr") if isinstance(response_materialization.get("pr"), dict) else {}
    summary = {
        "generated_at": response_materialization.get("generated_at"),
        "plan_checksum": response_materialization.get("plan_checksum"),
        "target_checksum": response_materialization.get("target_checksum"),
        "pr_number": str(pr.get("number")) if pr.get("number") is not None else None,
        "head_ref": pr.get("head_ref"),
        "base_ref": pr.get("base_ref"),
        "head_sha": pr.get("head_sha"),
        "approved_thread_ids": sorted(approved_thread_ids),
    }
    return summary, approved_thread_ids, blockers


def _post_write_gate_resolution_blockers(
    *,
    post_write_gate: Any,
    pr: dict[str, Any],
    review_threads: Any,
    response_materialization: Any,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(post_write_gate, dict):
        return [_issue("post_write_gate_missing", "post-write PR evidence gate packet is required")]
    if post_write_gate.get("schema_version") != POST_WRITE_PR_EVIDENCE_GATE_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "post_write_gate_schema_invalid",
                "post-write gate schema_version is unsupported",
                expected=POST_WRITE_PR_EVIDENCE_GATE_SCHEMA_VERSION,
                actual=post_write_gate.get("schema_version"),
            )
        )
    if post_write_gate.get("packet") != "post_write_pr_evidence_gate":
        blockers.append(_issue("post_write_gate_packet_invalid", "post-write gate packet must be post_write_pr_evidence_gate"))
    if post_write_gate.get("side_effects") != [] or post_write_gate.get("github_write_started") is not False:
        blockers.append(_issue("post_write_gate_not_read_only", "post-write gate evidence must be read-only"))
    if post_write_gate.get("refresh_required") is True:
        blockers.append(_issue("post_write_gate_refresh_required", "post-write gate requires refreshed PR evidence"))

    for blocker in post_write_gate.get("blockers", []):
        if isinstance(blocker, dict) and blocker.get("code") in POST_WRITE_FRESHNESS_BLOCKER_CODES:
            blockers.append(blocker)

    anchors = _pr_resolution_anchors(pr)
    materialization = post_write_gate.get("materialization") if isinstance(post_write_gate.get("materialization"), dict) else {}
    response_pr = response_materialization.get("pr") if isinstance(response_materialization, dict) and isinstance(response_materialization.get("pr"), dict) else {}
    expected_materialization = {
        "generated_at": response_materialization.get("generated_at") if isinstance(response_materialization, dict) else None,
        "plan_checksum": response_materialization.get("plan_checksum") if isinstance(response_materialization, dict) else None,
        "target_checksum": response_materialization.get("target_checksum") if isinstance(response_materialization, dict) else None,
        "pr_number": str(response_pr.get("number")) if response_pr.get("number") is not None else None,
        "head_ref": response_pr.get("head_ref"),
        "base_ref": response_pr.get("base_ref"),
        "head_sha": response_pr.get("head_sha"),
    }
    for field, expected in expected_materialization.items():
        actual = materialization.get(field)
        if not _anchor_present(expected) or not _anchor_present(actual):
            blockers.append(
                _issue(
                    "post_write_gate_materialization_anchor_missing",
                    "post-write gate must bind the response materialization anchors",
                    field=field,
                    expected=expected,
                    actual=actual,
                )
            )
            continue
        if str(expected) != str(actual):
            blockers.append(
                _issue(
                    "post_write_gate_materialization_mismatch",
                    "post-write gate materialization summary does not match the supplied response materialization",
                    field=field,
                    expected=expected,
                    actual=actual,
                )
            )

    refresh = post_write_gate.get("refresh") if isinstance(post_write_gate.get("refresh"), dict) else {}
    expected_refresh = {
        **anchors,
        "pr_json_checksum": _checksum_json(pr),
        "review_threads_checksum": _checksum_json(review_threads),
    }
    for field, expected in expected_refresh.items():
        actual = refresh.get(field)
        if not _anchor_present(expected) or not _anchor_present(actual):
            blockers.append(
                _issue(
                    "post_write_gate_refresh_anchor_missing",
                    "post-write gate refresh summary must bind the saved PR and review-thread evidence",
                    field=field,
                    expected=expected,
                    actual=actual,
                )
            )
            continue
        if str(expected) != str(actual):
            blockers.append(
                _issue(
                    "post_write_gate_refresh_mismatch",
                    "post-write gate refresh summary does not match the supplied saved evidence",
                    field=field,
                    expected=expected,
                    actual=actual,
                )
            )
    try:
        captured = _parse_utc(refresh.get("captured_at"))
        materialized_at = _parse_utc(expected_materialization["generated_at"])
    except (TypeError, ValueError) as exc:
        blockers.append(_issue("post_write_gate_timestamp_invalid", f"post-write gate timestamp is invalid: {exc}"))
    else:
        if captured is None:
            blockers.append(_issue("post_write_gate_timestamp_missing", "post-write gate captured_at is required"))
        elif materialized_at is not None and captured < materialized_at:
            blockers.append(
                _issue(
                    "github_evidence_sync_before_materialization",
                    "post-write gate evidence was captured before the response materialization",
                    captured_at=_format_utc(captured),
                    materialization_generated_at=_format_utc(materialized_at),
                )
            )
    return blockers


def _review_thread_resolution_actions(
    *,
    review_threads: Any,
    target_thread_ids: list[str],
    approved_thread_ids: set[str],
    anchors: dict[str, Any],
    review_threads_checksum: str,
    response_materialization_checksum: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    nodes = review_threads_nodes(review_threads)
    if nodes is None:
        return actions, [_issue("review_thread_evidence_invalid", "review threads payload must contain reviewThreads.nodes")]
    thread_by_id = {str(thread.get("id")): thread for thread in nodes if isinstance(thread, dict) and thread.get("id")}
    findings, warnings = review_thread_findings_from_payload(review_threads)
    blockers.extend(_issue("review_thread_evidence_invalid", warning) for warning in warnings)
    findings_by_thread: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        thread_id = finding.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            findings_by_thread.setdefault(thread_id, []).append(finding)

    for thread_id in target_thread_ids:
        thread = thread_by_id.get(thread_id)
        if thread is None:
            blockers.append(
                _issue(
                    "review_thread_resolution_target_missing",
                    "target review thread was not present in saved review-thread evidence",
                    thread_id=thread_id,
                )
            )
            continue
        if thread.get("isResolved") is True:
            blockers.append(
                _issue(
                    "review_thread_resolution_target_already_resolved",
                    "target review thread is already resolved",
                    thread_id=thread_id,
                )
            )
            continue
        if thread.get("isOutdated") is True:
            blockers.append(
                _issue(
                    "review_thread_resolution_target_outdated",
                    "target review thread is outdated",
                    thread_id=thread_id,
                )
            )
            continue
        actionable_findings = findings_by_thread.get(thread_id, [])
        if not actionable_findings:
            current_actionable_comments = []
            for comment in review_thread_comment_nodes(thread):
                if not isinstance(comment, dict) or comment.get("outdated") is not False:
                    continue
                if actionable_review_body(comment.get("body")) is not None:
                    current_actionable_comments.append(comment)
            if not current_actionable_comments:
                blockers.append(
                    _issue(
                        "review_thread_resolution_target_not_actionable",
                        "target review thread has no current actionable review comments",
                        thread_id=thread_id,
                    )
                )
                continue
        if thread_id not in approved_thread_ids:
            blockers.append(
                _issue(
                    "review_thread_resolution_target_unresponded",
                    "target review thread was not part of approved review-response materialization evidence",
                    thread_id=thread_id,
                )
            )
            continue
        comment_ids = sorted({str(finding["id"]) for finding in actionable_findings if finding.get("id")})
        first = actionable_findings[0] if actionable_findings else {}
        action = {
            "kind": "resolve_review_thread",
            "thread_id": thread_id,
            "comment_ids": comment_ids,
            "pr_number": anchors["pr_number"],
            "head_ref": anchors["head_ref"],
            "base_ref": anchors["base_ref"],
            "head_sha": anchors["head_sha"],
            "review_threads_checksum": review_threads_checksum,
            "response_materialization_checksum": response_materialization_checksum,
        }
        for key, value in {"file": first.get("file"), "line": first.get("line")}.items():
            if value is not None:
                action[key] = value
        actions.append(action)
    return actions, blockers


def evaluate_review_thread_resolution_plan(
    *,
    pr: dict[str, Any],
    review_threads: Any,
    response_materialization: Any,
    post_write_gate: Any,
    target_thread_ids: Any,
    evidence_captured_at: datetime | str | None = None,
    max_evidence_age_minutes: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a read-only, operator-approval target for resolving review threads."""
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    anchors = _pr_resolution_anchors(pr)
    thread_ids, target_blockers = _dedupe_target_thread_ids(target_thread_ids)
    blockers.extend(target_blockers)
    evidence, freshness_blockers = _evidence_summary(
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=None,
        evidence_captured_at=evidence_captured_at,
        max_evidence_age_minutes=max_evidence_age_minutes,
        now=now,
    )
    blockers.extend(freshness_blockers)
    response_summary, approved_thread_ids, materialization_blockers = _review_response_materialization_resolution_summary(
        response_materialization
    )
    blockers.extend(materialization_blockers)
    for field, current in anchors.items():
        if not _anchor_present(current):
            blockers.append(
                _issue(
                    "review_thread_resolution_pr_target_anchor_missing",
                    "thread resolution planning requires PR number, branch, base, and head anchors",
                    field=field,
                )
            )
            continue
        expected = response_summary.get(field)
        if _anchor_present(expected) and str(expected) != str(current):
            blockers.append(
                _issue(
                    "review_thread_resolution_pr_target_mismatch",
                    "saved PR evidence no longer matches the response materialization target",
                    field=field,
                    expected=expected,
                    actual=current,
                )
            )
    blockers.extend(
        _post_write_gate_resolution_blockers(
            post_write_gate=post_write_gate,
            pr=pr,
            review_threads=review_threads,
            response_materialization=response_materialization,
        )
    )

    review_threads_checksum = _checksum_json(review_threads)
    response_materialization_checksum = _checksum_json(response_materialization)
    post_write_gate_checksum = _checksum_json(post_write_gate)
    actions, action_blockers = _review_thread_resolution_actions(
        review_threads=review_threads,
        target_thread_ids=thread_ids,
        approved_thread_ids=approved_thread_ids,
        anchors=anchors,
        review_threads_checksum=review_threads_checksum,
        response_materialization_checksum=response_materialization_checksum,
    )
    blockers.extend(action_blockers)

    target_actions = [
        {
            "kind": action["kind"],
            "thread_id": action["thread_id"],
            "comment_ids": action["comment_ids"],
            "pr_number": action["pr_number"],
            "head_ref": action["head_ref"],
            "base_ref": action["base_ref"],
            "head_sha": action["head_sha"],
            "review_threads_checksum": action["review_threads_checksum"],
            "response_materialization_checksum": action["response_materialization_checksum"],
        }
        for action in actions
    ]
    target = {
        "schema_version": REVIEW_THREAD_RESOLUTION_TARGET_SCHEMA_VERSION,
        "packet": "review_thread_resolution_target",
        "operation": "review_thread_resolution",
        "pr_number": anchors["pr_number"],
        "head_ref": anchors["head_ref"],
        "base_ref": anchors["base_ref"],
        "head_sha": anchors["head_sha"],
        "review_threads_checksum": review_threads_checksum,
        "response_materialization_checksum": response_materialization_checksum,
        "response_materialization_plan_checksum": response_summary.get("plan_checksum"),
        "response_materialization_target_checksum": response_summary.get("target_checksum"),
        "post_write_gate_checksum": post_write_gate_checksum,
        "thread_ids": [action["thread_id"] for action in actions],
        "actions": target_actions,
    }
    valid = not blockers
    blocker_codes = {blocker.get("code") for blocker in blockers}
    if valid and actions:
        recommended_next_action = "approve_review_thread_resolution"
    elif blocker_codes & {
        "pr_evidence_stale",
        "pr_evidence_from_future",
        "review_thread_evidence_invalid",
        "post_write_gate_refresh_required",
        "post_write_gate_refresh_mismatch",
        "post_write_gate_refresh_anchor_missing",
        "github_evidence_sync_before_materialization",
    }:
        recommended_next_action = "refresh_pr_evidence"
    elif blocker_codes & {
        "review_response_materialization_missing",
        "review_response_materialization_not_materialized",
        "review_response_materialization_not_approved",
    }:
        recommended_next_action = "provide_review_response_materialization"
    else:
        recommended_next_action = "address_blockers"

    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REVIEW_THREAD_RESOLUTION_PLAN_SCHEMA_VERSION,
        "packet": "review_thread_resolution_plan",
        "valid": valid,
        "plan_ready": valid and bool(actions),
        "decision": "ready" if valid and actions else "blocked",
        "recommended_next_action": recommended_next_action,
        "operator_confirmation_required": True,
        "approval_state": "not_approved",
        "execution_authority": "none",
        "github_write_started": False,
        "target": target,
        "target_checksum": _checksum_json(target),
        "evidence": {key: value for key, value in evidence.items() if value is not None},
        "response_materialization": response_summary,
        "post_write_gate_checksum": post_write_gate_checksum,
        "resolution_plan": actions,
        "blockers": blockers,
        "warnings": warnings,
        "side_effects": [],
        "command_trace": [],
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


def review_response_materialization_approval_payload(plan_packet: Any) -> dict[str, Any]:
    target = plan_packet.get("target") if isinstance(plan_packet, dict) and isinstance(plan_packet.get("target"), dict) else {}
    return {
        "schema_version": "review-response-materialization-approval.v1",
        "packet": "review_response_materialization_approval",
        "plan_checksum": _checksum_json(plan_packet),
        "target_checksum": plan_packet.get("target_checksum") if isinstance(plan_packet, dict) else None,
        "pr_number": target.get("pr_number"),
        "write_kinds": target.get("write_kinds") if isinstance(target.get("write_kinds"), list) else [],
        "operation": "review_response_materialization",
    }


def _review_response_materialization_approval_secret(approval_secret: str | bytes | None = None) -> bytes | None:
    secret = (
        approval_secret
        if approval_secret is not None
        else os.environ.get(REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET_ENV)
    )
    if isinstance(secret, bytes):
        return secret if secret else None
    if isinstance(secret, str) and secret:
        return secret.encode("utf-8")
    return None


def review_response_materialization_approval_token(
    plan_packet: Any,
    *,
    approval_secret: str | bytes | None = None,
) -> str:
    secret = _review_response_materialization_approval_secret(approval_secret)
    if secret is None:
        raise ValueError(f"{REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET_ENV} is required for approval tokens")
    payload = json.dumps(
        review_response_materialization_approval_payload(plan_packet),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_PREFIX + "hmac-sha256:" + digest


def _required_sections_from_materialization_plan(plan_packet: Any) -> list[str]:
    sections: list[str] = []
    if not isinstance(plan_packet, dict):
        return sections
    for write in plan_packet.get("write_plan", []):
        if not isinstance(write, dict) or write.get("kind") != "update_pr_body":
            continue
        preflight = write.get("pr_body_preflight") if isinstance(write.get("pr_body_preflight"), dict) else {}
        template = preflight.get("template_summary") if isinstance(preflight.get("template_summary"), dict) else {}
        for section in template.get("required_sections", []):
            if isinstance(section, str) and section.strip() and section not in sections:
                sections.append(section)
            elif isinstance(section, dict) and _non_empty_string(section.get("section")) and section["section"] not in sections:
                sections.append(section["section"])
    return sections


def _review_response_materialization_plan_structural_blockers(plan_packet: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(plan_packet, dict):
        return [_issue("review_response_materialization_plan_invalid", "materialization plan must be a JSON object")]
    if plan_packet.get("schema_version") != REVIEW_RESPONSE_MATERIALIZATION_PLAN_SCHEMA_VERSION:
        blockers.append(
            _issue(
                "review_response_materialization_plan_schema_invalid",
                "materialization plan schema_version must be review-response-materialization-plan.v1",
            )
        )
    if plan_packet.get("packet") != "review_response_materialization_plan":
        blockers.append(
            _issue(
                "review_response_materialization_plan_packet_invalid",
                "materialization plan packet must be review_response_materialization_plan",
            )
        )
    if plan_packet.get("valid") is not True or plan_packet.get("plan_ready") is not True or plan_packet.get("decision") != "ready":
        blockers.append(_issue("review_response_materialization_plan_not_ready", "materialization plan must be valid and ready"))
    if plan_packet.get("operator_confirmation_required") is not True:
        blockers.append(
            _issue(
                "review_response_materialization_plan_operator_confirmation_missing",
                "materialization plan must require operator confirmation",
            )
        )
    if plan_packet.get("approval_state") != "not_approved":
        blockers.append(
            _issue(
                "review_response_materialization_plan_approval_state_invalid",
                "materialization plan must start as not_approved",
            )
        )
    if plan_packet.get("execution_authority") != "none":
        blockers.append(
            _issue(
                "review_response_materialization_plan_execution_authority_invalid",
                "materialization plan must not grant execution authority",
            )
        )
    if plan_packet.get("github_write_started") is not False:
        blockers.append(
            _issue(
                "review_response_materialization_plan_github_write_started",
                "materialization plan must not contain prior GitHub writes",
            )
        )
    if plan_packet.get("side_effects") != []:
        blockers.append(
            _issue(
                "review_response_materialization_plan_side_effects_present",
                "materialization plan must be side-effect free",
            )
        )
    target = plan_packet.get("target") if isinstance(plan_packet.get("target"), dict) else None
    if target is None:
        blockers.append(_issue("review_response_materialization_target_missing", "materialization plan target is required"))
    else:
        if target.get("schema_version") != REVIEW_RESPONSE_MATERIALIZATION_TARGET_SCHEMA_VERSION:
            blockers.append(
                _issue(
                    "review_response_materialization_target_schema_invalid",
                    "materialization target schema_version must be review-response-materialization-target.v1",
                )
            )
        if target.get("operation") != "review_response_materialization":
            blockers.append(
                _issue(
                    "review_response_materialization_target_operation_invalid",
                    "materialization target operation must be review_response_materialization",
                )
            )
        if plan_packet.get("target_checksum") != _checksum_json(target):
            blockers.append(
                _issue(
                    "review_response_materialization_target_checksum_mismatch",
                    "materialization target checksum does not match target payload",
                )
            )
        if target.get("response_plan_checksum") != plan_packet.get("response_plan_checksum"):
            blockers.append(
                _issue(
                    "review_response_materialization_response_plan_checksum_mismatch",
                    "materialization target response plan checksum must match the plan packet",
                )
            )
    if not isinstance(plan_packet.get("write_plan"), list) or not plan_packet.get("write_plan"):
        blockers.append(_issue("review_response_write_plan_missing", "materialization plan write_plan is required"))
    return blockers


def _comment_thread_targets(review_threads: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    findings, warnings = review_thread_findings_from_payload(review_threads)
    blockers = [_issue("review_thread_evidence_invalid", warning) for warning in warnings]
    targets = {
        str(finding["id"]): {
            "comment_id": str(finding["id"]),
            "thread_id": str(finding.get("thread_id")),
            "file": finding.get("file"),
            "line": finding.get("line"),
        }
        for finding in findings
        if finding.get("id") and finding.get("thread_id")
    }
    return targets, blockers


def _review_response_materialization_recheck(
    *,
    plan_packet: dict[str, Any],
    pr: dict[str, Any],
    review_threads: Any | None,
    candidate_discovery: Any | None,
    required_body_sections: list[str],
    pr_evidence_captured_at: datetime | str | None,
    max_pr_evidence_age_minutes: int | None,
    now: datetime | str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    target = plan_packet.get("target") if isinstance(plan_packet.get("target"), dict) else {}
    evidence, freshness_blockers = _evidence_summary(
        pr=pr,
        review_threads=review_threads,
        candidate_discovery=candidate_discovery,
        evidence_captured_at=pr_evidence_captured_at,
        max_evidence_age_minutes=max_pr_evidence_age_minutes,
        now=now,
    )
    blockers.extend(freshness_blockers)
    compared_pr_fields = {
        "pr_number": str(pr.get("number")) if pr.get("number") is not None else None,
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
    }
    for field, current in compared_pr_fields.items():
        planned = target.get(field)
        if not _anchor_present(planned) or not _anchor_present(current):
            blockers.append(
                _issue(
                    "review_response_pr_target_anchor_missing",
                    "approved review response writes require PR number, branch, base, and head anchors",
                    field=field,
                    expected=planned,
                    actual=current,
                )
            )
            continue
        if str(planned) != str(current):
            blockers.append(
                _issue(
                    "review_response_pr_target_mismatch",
                    "current PR target no longer matches the materialization target",
                    field=field,
                    expected=planned,
                    actual=current,
                )
            )

    planned_evidence = plan_packet.get("evidence") if isinstance(plan_packet.get("evidence"), dict) else {}
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
        planned_checksum = planned_evidence.get(field)
        if planned_checksum is None and current_checksum is None:
            continue
        if planned_checksum != current_checksum:
            blockers.append(
                _issue(
                    code,
                    "saved response evidence no longer matches the materialization plan",
                    field=field,
                    expected=planned_checksum,
                    actual=current_checksum,
                )
            )

    comment_targets: dict[str, dict[str, Any]] = {}
    if review_threads is not None:
        comment_targets, thread_blockers = _comment_thread_targets(review_threads)
        blockers.extend(thread_blockers)

    current_writes: list[dict[str, Any]] = []
    write_plan = plan_packet.get("write_plan")
    if isinstance(write_plan, list):
        for index, write in enumerate(write_plan, start=1):
            if not isinstance(write, dict):
                blockers.append(_issue("review_response_write_invalid", "materialization write must be a JSON object", index=index))
                continue
            kind = write.get("kind")
            body = write.get("body")
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
            if not isinstance(body, str) or not body.strip():
                blockers.append(_issue("review_response_write_body_missing", "materialization write body text is required", index=index, kind=kind))
                continue
            expected_checksum = _checksum_json(body)
            if write.get("body_checksum") != expected_checksum:
                blockers.append(
                    _issue(
                        "review_response_write_body_checksum_mismatch",
                        "materialization write body checksum does not match body text",
                        index=index,
                        kind=kind,
                        expected=expected_checksum,
                        actual=write.get("body_checksum"),
                    )
                )
            current_write = {
                "kind": kind,
                "body": body,
                "body_checksum": expected_checksum,
            }
            if kind == "update_pr_body":
                body_preflight = evaluate_pr_body_preflight(body, required_body_sections=required_body_sections)
                current_write["pr_body_preflight"] = body_preflight
                if body_preflight.get("ready_to_publish") is not True:
                    blockers.append(
                        _issue(
                            "review_response_pr_body_preflight_failed",
                            "approved PR body update no longer satisfies PR body preflight",
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
                elif not set(comment_ids).issubset(comment_targets):
                    blockers.append(
                        _issue(
                            "review_response_comment_target_not_actionable",
                            "post_review_comment target must still be an unresolved actionable review comment",
                            index=index,
                            comment_ids=comment_ids,
                        )
                    )
                current_write["comment_ids"] = comment_ids
            current_writes.append(current_write)

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
        for write in current_writes
    ]
    current_target = {
        "schema_version": REVIEW_RESPONSE_MATERIALIZATION_TARGET_SCHEMA_VERSION,
        "packet": "review_response_materialization_target",
        "operation": "review_response_materialization",
        "response_plan_checksum": plan_packet.get("response_plan_checksum"),
        "pr_number": compared_pr_fields["pr_number"],
        "head_ref": pr.get("headRefName"),
        "base_ref": pr.get("baseRefName"),
        "head_sha": pr.get("headRefOid"),
        "write_kinds": sorted({write["kind"] for write in current_writes}),
        "writes": target_writes,
    }
    if target != current_target:
        blockers.append(
            _issue(
                "review_response_materialization_target_checksum_mismatch",
                "current review response write target no longer matches the approved materialization target",
                expected=plan_packet.get("target_checksum"),
                actual=_checksum_json(current_target),
            )
        )
    elif plan_packet.get("target_checksum") != _checksum_json(current_target):
        blockers.append(
            _issue(
                "review_response_materialization_target_checksum_mismatch",
                "approved materialization target checksum does not match the current target payload",
                expected=plan_packet.get("target_checksum"),
                actual=_checksum_json(current_target),
            )
        )
    return blockers, warnings, current_writes, comment_targets, {key: value for key, value in evidence.items() if value is not None}


def _review_response_materialization_recommendation(
    blockers: list[dict[str, Any]],
    *,
    github_write_started: bool,
) -> str:
    codes = {blocker.get("code") for blocker in blockers}
    if not blockers:
        return "inspect_pull_request"
    if "audit_write_failed" in codes:
        return "repair_audit_materialization"
    if "operator_approval_missing" in codes or "operator_approval_mismatch" in codes:
        return "provide_operator_approval"
    if "operator_approval_secret_missing" in codes or "operator_approval_target_unresolved" in codes:
        return "provide_operator_approval"
    if "pr_evidence_stale" in codes or "pr_evidence_from_future" in codes:
        return "refresh_pr_evidence"
    if github_write_started:
        return "inspect_review_response_materialization"
    if any(
        code in codes
        for code in (
            "review_response_pr_target_mismatch",
            "review_response_plan_pr_checksum_mismatch",
            "review_response_plan_review_threads_checksum_mismatch",
            "review_response_plan_candidate_checksum_mismatch",
            "review_response_materialization_target_checksum_mismatch",
        )
    ):
        return "refresh_review_response_materialization_plan"
    return "address_blockers"


def _review_response_materialization_packet(
    *,
    valid: bool,
    decision: str,
    approval_state: str,
    plan_file: Path,
    plan_checksum: str | None,
    target_checksum: str | None,
    pr: dict[str, Any],
    evidence: dict[str, Any] | None,
    intended_side_effects: list[str],
    side_effects: list[str],
    command_trace: list[dict[str, Any]],
    github_writes: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    github_write_started = bool(github_writes)
    packet = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": REVIEW_RESPONSE_MATERIALIZATION_SCHEMA_VERSION,
        "packet": "review_response_materialization",
        "generated_at": utc_now(),
        "valid": valid,
        "decision": decision,
        "recommended_next_action": _review_response_materialization_recommendation(
            blockers,
            github_write_started=github_write_started,
        ),
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": approval_state,
        "execution_authority": (
            "operator_approved_review_response_materialization" if approval_state == "approved" else "none"
        ),
        "github_write_started": github_write_started,
        "review_resolution": "not_claimed",
        "merge_readiness": "not_evaluated",
        "plan_file": str(plan_file),
        "plan_checksum": plan_checksum,
        "target_checksum": target_checksum,
        "pr": {
            "number": str(pr.get("number")) if pr.get("number") is not None else None,
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
            "url": pr.get("url"),
        },
        "evidence": evidence or {},
        "intended_side_effects": intended_side_effects,
        "side_effects": side_effects,
        "command_trace": command_trace,
        "github_writes": github_writes,
        "blockers": blockers,
        "warnings": warnings,
        "limitations": [
            "operator_approved_review_response_materialization_only",
            "does_not_resolve_review_threads",
            "does_not_claim_review_resolved",
            "does_not_invoke_review_agents_or_paid_review",
            "does_not_edit_labels",
            "does_not_merge",
            "does_not_release",
            "does_not_publish_packages",
            "does_not_assign_roles",
            "does_not_schedule_agents",
            "does_not_continue_loop",
        ],
    }
    return packet


def _append_review_response_materialization_audit(root: Path, record: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        return append_audit_record(root, record), None
    except OSError as exc:
        return None, _issue("audit_write_failed", f"could not write review response materialization audit record: {exc}")


def _write_temp_body(body: str) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
            handle.write(body)
            return Path(handle.name), None
    except OSError as exc:
        return None, _issue(
            "temporary_review_response_body_creation_failed",
            "could not create temporary body file during approved review response materialization",
            detail=str(exc),
        )


def _unlink_temp_body(path: Path | None, warnings: list[dict[str, Any]]) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        warnings.append(_issue("temporary_review_response_body_cleanup_failed", "could not remove temporary body file"))


def _parse_review_comment_response(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reply = (
        data.get("addPullRequestReviewThreadReply")
        if isinstance(data.get("addPullRequestReviewThreadReply"), dict)
        else {}
    )
    comment = reply.get("comment") if isinstance(reply.get("comment"), dict) else {}
    comment_id = comment.get("id") if isinstance(comment.get("id"), str) else None
    url = comment.get("url") if isinstance(comment.get("url"), str) else None
    return comment_id, url


def _failed_review_response_materialization(
    *,
    runtime_path: Path,
    approval_state: str,
    plan_path: Path,
    plan_checksum: str | None,
    target_checksum: str | None,
    pr: dict[str, Any],
    evidence: dict[str, Any],
    intended_side_effects: list[str],
    side_effects: list[str],
    command_trace: list[dict[str, Any]],
    github_writes: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    failed_packet = _review_response_materialization_packet(
        valid=False,
        decision="blocked",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum,
        pr=pr,
        evidence=evidence,
        intended_side_effects=intended_side_effects,
        side_effects=[*side_effects, "audit_result_record_appended"],
        command_trace=command_trace,
        github_writes=github_writes,
        blockers=blockers,
        warnings=warnings,
    )
    _record, audit_blocker = _append_review_response_materialization_audit(
        runtime_path,
        review_response_materialization_result_audit_record(failed_packet),
    )
    if audit_blocker is None:
        return failed_packet
    return _review_response_materialization_packet(
        valid=False,
        decision="blocked",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum,
        pr=pr,
        evidence=evidence,
        intended_side_effects=intended_side_effects,
        side_effects=side_effects,
        command_trace=command_trace,
        github_writes=github_writes,
        blockers=[*blockers, audit_blocker],
        warnings=warnings,
    )


def materialize_review_response_plan(
    *,
    cwd: str | Path,
    plan_packet: Any,
    plan_file: str | Path,
    approval_token: str | None,
    runtime_root: str | Path,
    pr: dict[str, Any],
    review_threads: Any | None = None,
    candidate_discovery: Any | None = None,
    required_body_sections: list[str] | None = None,
    pr_evidence_captured_at: datetime | str | None = None,
    max_pr_evidence_age_minutes: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    repo_cwd = Path(cwd).expanduser().resolve(strict=False)
    plan_path = Path(plan_file).expanduser().resolve(strict=False)
    runtime_path = Path(runtime_root).expanduser().resolve()
    plan_checksum = _checksum_json(plan_packet)
    target_checksum = plan_packet.get("target_checksum") if isinstance(plan_packet, dict) else None
    target = plan_packet.get("target") if isinstance(plan_packet, dict) and isinstance(plan_packet.get("target"), dict) else {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    command_trace: list[dict[str, Any]] = []
    side_effects: list[str] = []
    github_writes: list[dict[str, Any]] = []
    current_writes: list[dict[str, Any]] = []
    comment_targets: dict[str, dict[str, Any]] = {}
    evidence: dict[str, Any] = {}
    intended_side_effects: list[str] = []
    if isinstance(plan_packet, dict):
        intended_side_effects = [
            "update_pr_body" if write.get("kind") == "update_pr_body" else "post_review_comment"
            for write in plan_packet.get("write_plan", [])
            if isinstance(write, dict) and write.get("kind") in ALLOWED_REVIEW_RESPONSE_WRITE_KINDS
        ]

    blockers.extend(_review_response_materialization_plan_structural_blockers(plan_packet))

    approval_secret = _review_response_materialization_approval_secret()
    expected_token = (
        review_response_materialization_approval_token(plan_packet, approval_secret=approval_secret)
        if approval_secret is not None
        else None
    )
    if not approval_token:
        approval_state = "not_approved"
        blockers.append(
            _issue(
                "operator_approval_missing",
                "operator approval token is required before review response materialization",
            )
        )
    elif approval_secret is None:
        approval_state = "approval_unresolved"
        blockers.append(
            _issue(
                "operator_approval_secret_missing",
                f"{REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET_ENV} is required to verify operator approval",
            )
        )
    elif expected_token is None or not hmac.compare_digest(approval_token, expected_token):
        approval_state = "approval_mismatch"
        blockers.append(
            _issue(
                "operator_approval_mismatch",
                "operator approval token does not match the approved review response materialization plan",
            )
        )
    else:
        approval_state = "approved"

    if isinstance(plan_packet, dict):
        required_sections = list(required_body_sections or [])
        if not required_sections:
            required_sections = _required_sections_from_materialization_plan(plan_packet)
        recheck_blockers, recheck_warnings, current_writes, comment_targets, evidence = _review_response_materialization_recheck(
            plan_packet=plan_packet,
            pr=pr,
            review_threads=review_threads,
            candidate_discovery=candidate_discovery,
            required_body_sections=required_sections,
            pr_evidence_captured_at=pr_evidence_captured_at,
            max_pr_evidence_age_minutes=max_pr_evidence_age_minutes,
            now=now,
        )
        blockers.extend(recheck_blockers)
        warnings.extend(recheck_warnings)

    if blockers:
        return _review_response_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
            pr=pr,
            evidence=evidence,
            intended_side_effects=intended_side_effects,
            side_effects=side_effects,
            command_trace=command_trace,
            github_writes=github_writes,
            blockers=blockers,
            warnings=warnings,
        )

    intent_packet = _review_response_materialization_packet(
        valid=True,
        decision="approved",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum if isinstance(target_checksum, str) else None,
        pr=pr,
        evidence=evidence,
        intended_side_effects=intended_side_effects,
        side_effects=[],
        command_trace=[],
        github_writes=[],
        blockers=[],
        warnings=warnings,
    )
    _record, audit_blocker = _append_review_response_materialization_audit(
        runtime_path,
        review_response_materialization_intent_audit_record(intent_packet),
    )
    if audit_blocker is not None:
        return _review_response_materialization_packet(
            valid=False,
            decision="blocked",
            approval_state=approval_state,
            plan_file=plan_path,
            plan_checksum=plan_checksum,
            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
            pr=pr,
            evidence=evidence,
            intended_side_effects=intended_side_effects,
            side_effects=[],
            command_trace=[],
            github_writes=[],
            blockers=[audit_blocker],
            warnings=warnings,
        )
    side_effects.append("audit_intent_record_appended")

    pr_number = str(target.get("pr_number") or pr.get("number"))
    execution_writes = sorted(current_writes, key=lambda item: 0 if item.get("kind") == "update_pr_body" else 1)
    for write in execution_writes:
        body_file, body_error = _write_temp_body(write["body"])
        if body_error is not None:
            blockers.append(body_error)
            command_trace.append(_materialization_error_trace(label=body_error["code"], message=body_error["message"]))
            return _failed_review_response_materialization(
                runtime_path=runtime_path,
                approval_state=approval_state,
                plan_path=plan_path,
                plan_checksum=plan_checksum,
                target_checksum=target_checksum if isinstance(target_checksum, str) else None,
                pr=pr,
                evidence=evidence,
                intended_side_effects=intended_side_effects,
                side_effects=side_effects,
                command_trace=command_trace,
                github_writes=github_writes,
                blockers=blockers,
                warnings=warnings,
            )
        try:
            if write["kind"] == "update_pr_body":
                argv = ["gh", "pr", "edit", pr_number, "--body-file", str(body_file)]
                result = _run_process(repo_cwd, argv)
                command_trace.append(_materialization_command_trace(label="update_pr_body", argv=argv, result=result))
                if result.returncode != 0:
                    blockers.append(
                        _issue(
                            "review_response_materialization_command_failed",
                            "update_pr_body failed during approved review response materialization",
                            command_label="update_pr_body",
                            returncode=result.returncode,
                            stderr=result.stderr.strip(),
                        )
                    )
                    return _failed_review_response_materialization(
                        runtime_path=runtime_path,
                        approval_state=approval_state,
                        plan_path=plan_path,
                        plan_checksum=plan_checksum,
                        target_checksum=target_checksum if isinstance(target_checksum, str) else None,
                        pr=pr,
                        evidence=evidence,
                        intended_side_effects=intended_side_effects,
                        side_effects=side_effects,
                        command_trace=command_trace,
                        github_writes=github_writes,
                        blockers=blockers,
                        warnings=warnings,
                    )
                url = result.stdout.strip().splitlines()[0] if result.stdout.strip() else pr.get("url")
                github_writes.append(
                    {
                        "kind": "update_pr_body",
                        "pr_number": pr_number,
                        "body_checksum": write["body_checksum"],
                        "url": url,
                    }
                )
                side_effects.append("updated_pr_body")
            elif write["kind"] == "post_review_comment":
                thread_ids: list[str] = []
                for comment_id in write.get("comment_ids", []):
                    thread_id = comment_targets.get(comment_id, {}).get("thread_id")
                    if isinstance(thread_id, str) and thread_id and thread_id not in thread_ids:
                        thread_ids.append(thread_id)
                for thread_id in thread_ids:
                    argv = [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        f"query={REVIEW_THREAD_REPLY_MUTATION}",
                        "-F",
                        f"threadId={thread_id}",
                        "-F",
                        f"body=@{body_file}",
                    ]
                    result = _run_process(repo_cwd, argv)
                    command_trace.append(_materialization_command_trace(label="post_review_comment", argv=argv, result=result))
                    if result.returncode != 0:
                        blockers.append(
                            _issue(
                                "review_response_materialization_command_failed",
                                "post_review_comment failed during approved review response materialization",
                                command_label="post_review_comment",
                                returncode=result.returncode,
                                stderr=result.stderr.strip(),
                            )
                        )
                        return _failed_review_response_materialization(
                            runtime_path=runtime_path,
                            approval_state=approval_state,
                            plan_path=plan_path,
                            plan_checksum=plan_checksum,
                            target_checksum=target_checksum if isinstance(target_checksum, str) else None,
                            pr=pr,
                            evidence=evidence,
                            intended_side_effects=intended_side_effects,
                            side_effects=side_effects,
                            command_trace=command_trace,
                            github_writes=github_writes,
                            blockers=blockers,
                            warnings=warnings,
                        )
                    github_comment_id, url = _parse_review_comment_response(result)
                    github_writes.append(
                        {
                            "kind": "post_review_comment",
                            "pr_number": pr_number,
                            "thread_id": thread_id,
                            "comment_ids": write.get("comment_ids", []),
                            "body_checksum": write["body_checksum"],
                            "github_comment_id": github_comment_id,
                            "url": url,
                        }
                    )
                    side_effects.append("posted_review_comment")
        finally:
            _unlink_temp_body(body_file, warnings)

    success_packet = _review_response_materialization_packet(
        valid=True,
        decision="materialized",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum if isinstance(target_checksum, str) else None,
        pr=pr,
        evidence=evidence,
        intended_side_effects=intended_side_effects,
        side_effects=[*side_effects, "audit_result_record_appended"],
        command_trace=command_trace,
        github_writes=github_writes,
        blockers=[],
        warnings=warnings,
    )
    _record, result_audit_blocker = _append_review_response_materialization_audit(
        runtime_path,
        review_response_materialization_result_audit_record(success_packet),
    )
    if result_audit_blocker is None:
        return success_packet
    return _review_response_materialization_packet(
        valid=False,
        decision="blocked",
        approval_state=approval_state,
        plan_file=plan_path,
        plan_checksum=plan_checksum,
        target_checksum=target_checksum if isinstance(target_checksum, str) else None,
        pr=pr,
        evidence=evidence,
        intended_side_effects=intended_side_effects,
        side_effects=side_effects,
        command_trace=command_trace,
        github_writes=github_writes,
        blockers=[result_audit_blocker],
        warnings=warnings,
    )
