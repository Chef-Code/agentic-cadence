from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASSING_STATES = {"SUCCESS", "NEUTRAL"}
SKIPPED_STATES = {"SKIPPED"}
FAILED_STATES = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
PENDING_STATES = {"EXPECTED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}
CODEX_REVIEW_SKIPPED_JOBS = {"codex", "post_feedback", "fork_notice"}
BLOCKING_MERGE_STATE_STATUSES = {"BEHIND", "BLOCKED", "DIRTY", "DRAFT"}
WAITING_MERGE_STATE_STATUSES = {"UNKNOWN", "UNSTABLE"}
WARNING_MERGE_STATE_STATUSES = {"HAS_HOOKS"}
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.*?)(?:\s+#+)?\s*$")
SETEXT_HEADING_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(?P<marker>`{3,}|~{3,})")
PR_EVIDENCE_SOURCES = {"saved_pr_json", "live_pr_json"}


def load_pr_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"could not read PR JSON file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse PR JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("PR JSON file must contain a JSON object")
    return payload


def _section_label(value: str) -> str:
    match = MARKDOWN_HEADING_RE.match(value.strip())
    if match:
        return match.group("title").strip()
    return value.strip()


def _normalized_section_label(value: str) -> str:
    label = _section_label(value)
    label = re.sub(r"\s+", " ", label).strip().lower()
    return label


def _append_markdown_barrier(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")


def _iter_markdown_content_lines(text: str) -> list[str]:
    lines = []
    fence_marker: str | None = None
    in_html_comment = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if in_html_comment:
            if "-->" in stripped:
                in_html_comment = False
            continue
        if stripped.startswith("<!--"):
            _append_markdown_barrier(lines)
            if "-->" not in stripped:
                in_html_comment = True
            continue
        fence_match = FENCE_RE.match(raw_line)
        if fence_marker is not None:
            closing_marker = fence_match.group("marker") if fence_match else ""
            if closing_marker.startswith(fence_marker[0]) and len(closing_marker) >= len(fence_marker):
                fence_marker = None
            continue
        if fence_match:
            _append_markdown_barrier(lines)
            fence_marker = fence_match.group("marker")
            continue
        lines.append(raw_line)
    return lines


def markdown_heading_labels(text: str) -> list[str]:
    headings = []
    lines = _iter_markdown_content_lines(text)
    index = 0
    while index < len(lines):
        line = lines[index]
        match = MARKDOWN_HEADING_RE.match(line)
        if match:
            headings.append(match.group("title").strip())
            index += 1
            continue
        if index + 1 < len(lines) and line.strip() and SETEXT_HEADING_RE.match(lines[index + 1]):
            headings.append(line.strip())
            index += 2
            continue
        index += 1
    return headings


def required_sections_from_template(text: str) -> list[str]:
    sections = []
    seen = set()
    for section in markdown_heading_labels(text):
        normalized = _normalized_section_label(section)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        sections.append(section)
    return sections


def load_template_sections(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"could not read PR template file {path}: {exc}") from exc
    return required_sections_from_template(text)


def load_pr_body(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"could not read PR body file {path}: {exc}") from exc


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


def _is_codex_review_job(entry: dict[str, Any]) -> bool:
    return entry["workflow"] == "Codex Review" or entry["name"] in CODEX_REVIEW_SKIPPED_JOBS


def _normalized_check_state(item: dict[str, Any]) -> str:
    if item.get("__typename") == "StatusContext":
        return str(item.get("state") or "").strip().upper() or "UNKNOWN"
    status = str(item.get("status") or "").strip().upper()
    conclusion = str(item.get("conclusion") or "").strip().upper()
    if status == "COMPLETED":
        return conclusion or "UNKNOWN"
    return status or conclusion or "UNKNOWN"


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


def _pr_readiness_evidence(
    *,
    source: str,
    captured_at: datetime | str | None,
    max_age_minutes: int | None,
    now: datetime | str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if source not in PR_EVIDENCE_SOURCES:
        allowed_sources = ", ".join(sorted(PR_EVIDENCE_SOURCES))
        raise ValueError(f"PR evidence source must be one of {allowed_sources}")
    live = source == "live_pr_json"
    captured = _parse_utc(captured_at)
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
                    "PR evidence timestamp is in the future; refresh evidence before acting on readiness",
                    captured_at=_format_utc(captured),
                    checked_at=_format_utc(checked),
                    age_minutes=round(age_minutes, 2),
                )
            )
        elif max_age_minutes is not None and age_minutes > max_age_minutes:
            stale = True
            waiting.append(
                _issue(
                    "pr_evidence_stale",
                    "saved PR evidence is stale; refresh PR JSON before acting on readiness",
                    captured_at=_format_utc(captured),
                    age_minutes=round(age_minutes, 2),
                    max_age_minutes=max_age_minutes,
                )
            )
    if stale:
        freshness = "stale"
        limitations = ["does_not_call_github", "refresh_saved_pr_json_before_merge"]
    elif live:
        freshness = "live_like"
        limitations = ["caller_asserted_live_source", "does_not_verify_source_freshness"]
    else:
        freshness = "saved_input"
        limitations = [
            "does_not_call_github",
            "depends_on_saved_status_check_rollup",
            "depends_on_saved_review_decision",
        ]
    return {
        "source": source,
        "freshness": freshness,
        "live": live,
        "stale": stale,
        "captured_at": _format_utc(captured),
        "checked_at": _format_utc(checked),
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "max_age_minutes": max_age_minutes,
        "limitations": limitations,
    }, waiting


def _summarize_checks(
    checks: list[dict[str, Any]],
    required_checks: list[str],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    blockers: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_required: set[str] = set()
    by_name: dict[str, list[dict[str, Any]]] = {}
    summary = {
        "total": 0,
        "passed": 0,
        "pending": 0,
        "failed": 0,
        "skipped": 0,
        "required": list(required_checks),
    }

    for raw_check in checks:
        if not isinstance(raw_check, dict):
            warnings.append(_issue("malformed_check", "ignored non-object check entry"))
            continue
        name = _check_name(raw_check)
        if not name:
            warnings.append(_issue("missing_check_name", "ignored check entry with no name"))
            continue
        state = _normalized_check_state(raw_check)
        workflow = _check_workflow(raw_check)
        normalized = {"name": name, "state": state, "workflow": workflow}
        by_name.setdefault(name, []).append(normalized)
        summary["total"] += 1
        if state in PASSING_STATES:
            summary["passed"] += 1
        elif state in SKIPPED_STATES:
            summary["skipped"] += 1
            if _is_codex_review_job(normalized):
                warnings.append(
                    _issue(
                        "codex_review_skipped",
                        f"Codex Review job skipped and is not a blocker in v1: {name}",
                        check=name,
                    )
                )
        elif state in PENDING_STATES:
            summary["pending"] += 1
        else:
            summary["failed"] += 1

    duplicate_groups = []
    for name, entries in sorted(by_name.items()):
        if len(entries) <= 1:
            continue
        states = sorted({entry["state"] for entry in entries})
        blocking = any(state in FAILED_STATES for state in states)
        duplicate_groups.append({
            "name": name,
            "count": len(entries),
            "states": states,
            "blocking": blocking,
        })
        if blocking:
            code = "duplicate_failed_checks"
        elif all(state in PASSING_STATES for state in states):
            code = "duplicate_successful_checks"
        else:
            code = "duplicate_nonblocking_checks"
        warnings.append(
            _issue(
                code,
                f"duplicate check group observed for {name}",
                check=name,
                count=len(entries),
                states=states,
            )
        )

    for required in required_checks:
        entries = by_name.get(required, [])
        if not entries:
            blockers.append(
                _issue(
                    "required_check_missing",
                    f"required check is missing: {required}",
                    check=required,
                )
            )
            continue
        seen_required.add(required)
        states = {entry["state"] for entry in entries}
        if any(state in FAILED_STATES for state in states):
            blockers.append(
                _issue(
                    "check_failed",
                    f"required check failed: {required}",
                    check=required,
                )
            )
        elif any(state in PENDING_STATES or state == "UNKNOWN" for state in states):
            waiting.append(
                _issue(
                    "check_pending",
                    f"required check is still pending: {required}",
                    check=required,
                )
            )
        elif any(entry["state"] in SKIPPED_STATES and _is_codex_review_job(entry) for entry in entries):
            pass
        elif any(state in SKIPPED_STATES for state in states):
            warnings.append(
                _issue(
                    "required_check_skipped",
                    f"required check was skipped and treated as non-blocking: {required}",
                    check=required,
                )
            )
        elif not any(state in PASSING_STATES for state in states):
            blockers.append(
                _issue(
                    "required_check_not_successful",
                    f"required check is not successful: {required}",
                    check=required,
                )
            )

    summary["required_present"] = sorted(seen_required)
    if not required_checks:
        failed_names = sorted(
            name
            for name, entries in by_name.items()
            if any(entry["state"] in FAILED_STATES for entry in entries)
        )
        for name in failed_names:
            blockers.append(
                _issue(
                    "check_failed",
                    f"check failed and no required-check allowlist was supplied: {name}",
                    check=name,
                )
            )
    return summary, duplicate_groups, blockers, waiting, warnings


def _summarize_template(body: str, required_sections: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    body_heading_labels = {_normalized_section_label(heading) for heading in markdown_heading_labels(body)}
    required = []
    missing = []
    blockers = []
    for section in required_sections:
        normalized_section = _normalized_section_label(section)
        present = normalized_section in body_heading_labels
        required.append({"section": section, "present": present})
        if not present:
            missing.append(section)
            blockers.append(
                _issue(
                    "required_body_section_missing",
                    f"required PR body section is missing: {section}",
                    section=section,
                )
            )
    return {"required_sections": required, "missing_sections": missing}, blockers


def evaluate_pr_body_preflight(
    body: str,
    *,
    required_body_sections: list[str] | None = None,
) -> dict[str, Any]:
    required_sections = [section for section in (required_body_sections or []) if section.strip()]
    template, blockers = _summarize_template(body, required_sections)
    if not required_sections:
        blockers.append(
            _issue(
                "required_body_section_contract_missing",
                "PR body preflight requires a PR template file or at least one required body section",
            )
        )
    if blockers:
        decision = "blocked"
        action = "provide_template_or_sections" if not required_sections else "update_pr_body"
    else:
        decision = "ready"
        action = "publish_pr_body"
    return {
        "ready_to_publish": decision == "ready",
        "decision": decision,
        "recommended_next_action": action,
        "blockers": blockers,
        "warnings": [],
        "template_summary": template,
    }


def _review_summary(decision: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = str(decision or "").strip().upper()
    blockers = []
    if normalized == "CHANGES_REQUESTED":
        blockers.append(_issue("review_changes_requested", "review changes are requested"))
    elif normalized == "REVIEW_REQUIRED":
        blockers.append(_issue("review_required", "required review is still missing"))
    return {
        "decision": normalized if normalized else "",
        "changes_requested": normalized == "CHANGES_REQUESTED",
        "review_required": normalized == "REVIEW_REQUIRED",
    }, blockers


def evaluate_pr_readiness(
    pr: dict[str, Any],
    *,
    required_checks: list[str] | None = None,
    required_body_sections: list[str] | None = None,
    evidence_source: str = "saved_pr_json",
    evidence_captured_at: datetime | str | None = None,
    max_evidence_age_minutes: int | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    required_check_names = [name for name in (required_checks or []) if name.strip()]
    required_sections = [section for section in (required_body_sections or []) if section.strip()]
    readiness_evidence, evidence_waiting = _pr_readiness_evidence(
        source=evidence_source,
        captured_at=evidence_captured_at,
        max_age_minutes=max_evidence_age_minutes,
        now=now,
    )

    blockers: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = list(evidence_waiting)
    warnings: list[dict[str, Any]] = []
    checks_value = pr.get("statusCheckRollup")
    if "statusCheckRollup" not in pr:
        checks = []
        waiting.append(_issue("status_check_rollup_missing", "statusCheckRollup is missing from PR JSON"))
    elif not isinstance(checks_value, list):
        checks = []
        waiting.append(_issue("status_check_rollup_malformed", "statusCheckRollup must be a list"))
    else:
        checks = checks_value

    state = str(pr.get("state") or "").strip().upper()
    mergeable = str(pr.get("mergeable") or "").strip().upper()
    merge_state_status = str(pr.get("mergeStateStatus") or "").strip().upper()
    is_draft = bool(pr.get("isDraft"))
    if state != "OPEN":
        blockers.append(_issue("pr_not_open", "pull request is not open", state=state))
    if is_draft:
        blockers.append(_issue("pr_draft", "pull request is still a draft"))
    if mergeable == "CONFLICTING":
        blockers.append(_issue("merge_conflict", "pull request has merge conflicts"))
    elif mergeable in {"UNKNOWN", ""}:
        waiting.append(_issue("mergeability_unknown", "mergeability is not known yet"))
    if merge_state_status in BLOCKING_MERGE_STATE_STATUSES:
        code = "merge_conflict" if merge_state_status == "DIRTY" else "merge_state_blocked"
        blockers.append(
            _issue(
                code,
                f"pull request merge state is not clean: {merge_state_status}",
                merge_state_status=merge_state_status,
            )
        )
    elif merge_state_status in WAITING_MERGE_STATE_STATUSES:
        code = "merge_state_unknown" if merge_state_status == "UNKNOWN" else "merge_state_waiting"
        message = (
            "pull request merge state is not known yet"
            if merge_state_status == "UNKNOWN"
            else f"pull request merge state is waiting on checks: {merge_state_status}"
        )
        waiting.append(
            _issue(
                code,
                message,
                merge_state_status=merge_state_status,
            )
        )
    elif merge_state_status in WARNING_MERGE_STATE_STATUSES:
        warnings.append(
            _issue(
                "merge_state_warning",
                f"pull request merge state needs operator attention: {merge_state_status}",
                merge_state_status=merge_state_status,
            )
        )

    check_summary, duplicate_check_groups, check_blockers, check_waiting, check_warnings = _summarize_checks(
        checks,
        required_check_names,
    )
    review, review_blockers = _review_summary(pr.get("reviewDecision"))
    template, template_blockers = _summarize_template(str(pr.get("body") or ""), required_sections)
    blockers.extend(check_blockers)
    blockers.extend(review_blockers)
    blockers.extend(template_blockers)
    waiting.extend(check_waiting)
    warnings.extend(check_warnings)

    has_stale_evidence = any(
        item["code"] in {"pr_evidence_from_future", "pr_evidence_stale"}
        for item in waiting
    )
    if has_stale_evidence:
        decision = "waiting"
        action = "refresh_pr_evidence"
    elif blockers:
        decision = "blocked"
        action = "address_blockers"
    elif waiting:
        decision = "waiting"
        action = "wait_for_checks"
    else:
        decision = "ready"
        action = "merge_after_operator_confirmation"

    return {
        "ready_to_merge": decision == "ready",
        "decision": decision,
        "recommended_next_action": action,
        "blockers": blockers,
        "waiting": waiting,
        "warnings": warnings,
        "check_summary": check_summary,
        "duplicate_check_groups": duplicate_check_groups,
        "review_summary": review,
        "template_summary": template,
        "readiness_evidence": readiness_evidence,
        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": state,
            "is_draft": is_draft,
            "mergeable": mergeable,
            "merge_state_status": merge_state_status,
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
        },
    }
