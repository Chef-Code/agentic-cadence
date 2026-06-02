from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.store import atomic_write_json


GITHUB_EVIDENCE_SYNC_SCHEMA_VERSION = "github-evidence-sync.v1"
PR_VIEW_FIELDS = (
    "number",
    "title",
    "state",
    "isDraft",
    "mergeable",
    "mergeStateStatus",
    "reviewDecision",
    "body",
    "headRefName",
    "baseRefName",
    "headRefOid",
    "statusCheckRollup",
)
FAILED_STATES = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
REVIEW_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){"
    "reviewThreads(first:100){nodes{"
    "id isResolved isOutdated path line originalLine "
    "comments(first:50){nodes{id body path line originalLine outdated author{login}}}"
    "}}}}}"
)

NON_ACTIONABLE_REVIEW_MARKERS = (
    "<!-- walkthrough_start -->",
    "<!-- tips_start -->",
    "<!-- internal state start -->",
)
NON_ACTIONABLE_REVIEW_HEADINGS = (
    "## walkthrough",
    "## tips",
)
NON_ACTIONABLE_REVIEW_BODIES = {
    "approved",
    "lgtm",
    "looks good",
    "no actionable",
    "no actionable comments",
    "no actionable findings",
    "no changes requested",
    "review completed",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def parse_repo_slug(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in value.strip().split("/")]
    if len(parts) != 2 or not all(parts):
        raise ValueError("--repo must be owner/name")
    return parts[0], parts[1]


def _metadata(source: str, captured_at: str) -> dict[str, Any]:
    return {
        "source": source,
        "freshness": "live",
        "live": True,
        "stale": False,
        "captured_at": captured_at,
        "limitations": [
            "read_only_gh",
            "does_not_write_github",
            "does_not_create_branch",
            "does_not_commit",
            "does_not_push",
            "does_not_open_pr",
            "does_not_merge",
            "does_not_release",
            "does_not_publish_packages",
        ],
    }


def _read_json_stdout(result: subprocess.CompletedProcess[str], *, command_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if result.returncode != 0:
        return None, classify_gh_failure(result, command_name=command_name)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, _issue(
            "gh_json_invalid",
            f"{command_name} returned invalid JSON",
            detail=str(exc),
        )
    if not isinstance(payload, dict):
        return None, _issue("gh_json_not_object", f"{command_name} must return a JSON object")
    return payload, None


def classify_gh_failure(result: subprocess.CompletedProcess[str], *, command_name: str) -> dict[str, Any]:
    detail = (result.stderr.strip() or result.stdout.strip() or f"gh exited {result.returncode}")[:1000]
    lowered = detail.lower()
    if any(term in lowered for term in ("not logged", "authentication", "authenticate", "authorization", "bad credentials")):
        code = "gh_auth_failed"
        message = f"{command_name} could not authenticate with GitHub"
    elif "rate limit" in lowered or "api rate limit" in lowered:
        code = "github_rate_limited"
        message = f"{command_name} was rate limited"
    elif any(term in lowered for term in ("could not resolve", "failed to connect", "network", "connection refused", "connection timed out")):
        code = "github_network_failed"
        message = f"{command_name} could not reach GitHub"
    else:
        code = "gh_command_failed"
        message = f"{command_name} failed"
    return _issue(code, message, detail=detail, exit_code=result.returncode)


def _blocked_packet(
    *,
    repo: str,
    pr_number: int,
    out_dir: Path,
    blocker: dict[str, Any],
    command_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    code = blocker["code"]
    if code in {"gh_missing", "gh_auth_failed"}:
        action = "install_or_authenticate_gh"
    elif code in {"github_rate_limited", "github_network_failed"}:
        action = "retry_github_evidence_sync"
    else:
        action = "inspect_github_evidence_sync"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GITHUB_EVIDENCE_SYNC_SCHEMA_VERSION,
        "packet": "github_evidence_sync",
        "valid": False,
        "decision": "blocked",
        "recommended_next_action": action,
        "repo": repo,
        "pr_number": pr_number,
        "out_dir": str(out_dir),
        "captured_at": None,
        "evidence": {
            "source": "github_live_readonly",
            "freshness": "unavailable",
            "live": False,
            "stale": True,
            "limitations": ["no_partial_evidence_written"],
        },
        "files": {},
        "blockers": [blocker],
        "warnings": [],
        "side_effects": [],
        "github_write_started": False,
        "command_trace": command_trace or [],
    }


def sync_github_evidence(
    *,
    repo: str,
    pr_number: int,
    out_dir: Path,
    gh_bin: str | None = None,
) -> dict[str, Any]:
    try:
        owner, name = parse_repo_slug(repo)
    except ValueError as exc:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=_issue("repo_slug_invalid", str(exc)),
        )
    gh_path = gh_bin or shutil.which("gh")
    if not gh_path:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=_issue("gh_missing", "gh executable was not found on PATH"),
        )

    pr_args = [
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        ",".join(PR_VIEW_FIELDS),
    ]
    threads_args = [
        "api",
        "graphql",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={pr_number}",
        "-f",
        f"query={REVIEW_THREADS_QUERY}",
    ]
    command_trace = [
        {"argv": ["gh", *pr_args], "read_only": True},
        {"argv": ["gh", *threads_args], "read_only": True},
    ]
    pr_result = subprocess.run([gh_path, *pr_args], text=True, capture_output=True, check=False)
    pr_payload, blocker = _read_json_stdout(pr_result, command_name="gh pr view")
    if blocker is not None:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=blocker,
            command_trace=command_trace[:1],
        )
    threads_result = subprocess.run([gh_path, *threads_args], text=True, capture_output=True, check=False)
    threads_payload, blocker = _read_json_stdout(threads_result, command_name="gh api graphql")
    if blocker is not None:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=blocker,
            command_trace=command_trace,
        )

    captured_at = _utc_now()
    pr_payload = dict(pr_payload or {})
    threads_payload = dict(threads_payload or {})
    pr_payload["github_evidence"] = _metadata("gh_pr_view", captured_at)
    threads_payload["github_evidence"] = _metadata("gh_graphql_review_threads", captured_at)
    pr_file = out_dir / f"pr-{pr_number}.json"
    threads_file = out_dir / f"pr-{pr_number}-review-threads.json"
    summary_file = out_dir / f"pr-{pr_number}-github-evidence.json"
    packet = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": GITHUB_EVIDENCE_SYNC_SCHEMA_VERSION,
        "packet": "github_evidence_sync",
        "valid": True,
        "decision": "saved",
        "recommended_next_action": "use_saved_github_evidence",
        "repo": repo,
        "pr_number": pr_number,
        "out_dir": str(out_dir),
        "captured_at": captured_at,
        "evidence": _metadata("github_live_readonly", captured_at),
        "files": {
            "pr_json": str(pr_file),
            "review_threads_json": str(threads_file),
            "summary_json": str(summary_file),
        },
        "pr": {
            "number": pr_payload.get("number"),
            "title": pr_payload.get("title"),
            "state": pr_payload.get("state"),
            "head_ref": pr_payload.get("headRefName"),
            "base_ref": pr_payload.get("baseRefName"),
            "head_sha": pr_payload.get("headRefOid"),
        },
        "blockers": [],
        "warnings": [],
        "side_effects": ["wrote_pr_json", "wrote_review_threads_json", "wrote_evidence_summary"],
        "github_write_started": False,
        "command_trace": command_trace,
    }
    atomic_write_json(pr_file, pr_payload)
    atomic_write_json(threads_file, threads_payload)
    atomic_write_json(summary_file, packet)
    return packet


def _check_name(item: dict[str, Any]) -> str:
    if item.get("__typename") == "StatusContext":
        return str(item.get("context") or "").strip()
    return str(item.get("name") or "").strip()


def _check_state(item: dict[str, Any]) -> str:
    if item.get("__typename") == "StatusContext":
        return str(item.get("state") or "").strip().upper() or "UNKNOWN"
    status = str(item.get("status") or "").strip().upper()
    conclusion = str(item.get("conclusion") or "").strip().upper()
    if status == "COMPLETED":
        return conclusion or "UNKNOWN"
    return status or conclusion or "UNKNOWN"


def _check_workflow(item: dict[str, Any]) -> str:
    workflow = item.get("workflowName") or item.get("workflow")
    if isinstance(workflow, dict):
        workflow = workflow.get("name")
    return str(workflow or "").strip()


def pr_check_failure_findings(pr: dict[str, Any]) -> list[dict[str, Any]]:
    checks = pr.get("statusCheckRollup")
    if not isinstance(checks, list):
        return []
    findings = []
    pr_number = pr.get("number")
    for index, item in enumerate(checks, start=1):
        if not isinstance(item, dict):
            continue
        name = _check_name(item)
        if not name:
            continue
        state = _check_state(item)
        if state not in FAILED_STATES:
            continue
        finding: dict[str, Any] = {
            "id": f"pr-{pr_number or 'unknown'}-check-{index}",
            "check": name,
            "state": state,
            "workflow": _check_workflow(item),
            "source": "status_check_rollup",
        }
        url = item.get("detailsUrl") or item.get("targetUrl")
        if isinstance(url, str) and url.strip():
            finding["url"] = url.strip()
        findings.append(finding)
    return findings


def _review_body_label(body: str) -> str:
    return " ".join(body.strip().lower().split()).strip(" .!")


def actionable_review_body(body: Any) -> str | None:
    if not isinstance(body, str):
        return None
    stripped = body.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    if _review_body_label(stripped) in NON_ACTIONABLE_REVIEW_BODIES:
        return None
    if any(marker in lowered for marker in NON_ACTIONABLE_REVIEW_MARKERS):
        return None
    first_line = next((line.strip().lower() for line in stripped.splitlines() if line.strip()), "")
    if any(first_line.startswith(heading) for heading in NON_ACTIONABLE_REVIEW_HEADINGS):
        return None
    return stripped


def review_threads_nodes(payload: Any) -> list[Any] | None:
    if not isinstance(payload, dict):
        return None
    current: Any = payload
    for key in ("data", "repository", "pullRequest", "reviewThreads", "nodes"):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, list) else None


def review_thread_comment_nodes(thread: dict[str, Any]) -> list[Any]:
    comments = thread.get("comments")
    if isinstance(comments, dict) and isinstance(comments.get("nodes"), list):
        return comments["nodes"]
    if isinstance(comments, list):
        return comments
    return []


def review_thread_author(comment: dict[str, Any]) -> str | None:
    author = comment.get("author") or comment.get("user")
    if isinstance(author, dict) and author.get("login"):
        return str(author["login"])
    return None


def review_thread_findings_from_payload(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = review_threads_nodes(payload)
    if nodes is None:
        return [], ["review threads payload must contain a GitHub reviewThreads JSON object"]

    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    for index, thread in enumerate(nodes, start=1):
        if not isinstance(thread, dict):
            warnings.append(f"review thread {index} is not an object")
            continue
        is_resolved = thread.get("isResolved")
        is_outdated = thread.get("isOutdated")
        if not isinstance(is_resolved, bool) or not isinstance(is_outdated, bool):
            warnings.append(f"review thread {index} missing isResolved or isOutdated status")
            continue
        if is_resolved or is_outdated:
            continue
        thread_id = str(thread.get("id") or f"thread-{index}")
        thread_file = thread.get("path")
        thread_line = thread.get("line") or thread.get("originalLine")
        for comment_index, comment in enumerate(review_thread_comment_nodes(thread), start=1):
            if not isinstance(comment, dict):
                continue
            comment_outdated = comment.get("outdated")
            if not isinstance(comment_outdated, bool):
                warnings.append(f"review thread {index} comment {comment_index} missing outdated status")
                continue
            if comment_outdated:
                continue
            body = actionable_review_body(comment.get("body"))
            if body is None:
                continue
            finding_file = comment.get("path") or thread_file
            finding_id = comment.get("id") or thread_id
            finding: dict[str, Any] = {
                "id": str(finding_id),
                "file": finding_file,
                "body": body,
                "thread_id": thread_id,
                "source": "review_thread",
            }
            finding_line = comment.get("line") or comment.get("originalLine") or thread_line
            if finding_line is not None:
                finding["line"] = finding_line
            author = review_thread_author(comment)
            if author is not None:
                finding["author"] = author
            findings.append(finding)
    return findings, warnings
