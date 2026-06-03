from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
GITHUB_EVIDENCE_SYNC_SCHEMA_VERSION = "github-evidence-sync.v1"
DEFAULT_GH_TIMEOUT_SECONDS = 60
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
    "query($owner:String!,$name:String!,$number:Int!,$threadsCursor:String){"
    "repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){"
    "reviewThreads(first:100,after:$threadsCursor){pageInfo{hasNextPage endCursor} nodes{"
    "id isResolved isOutdated path line originalLine "
    "comments(first:50){pageInfo{hasNextPage endCursor} nodes{id body path line originalLine outdated author{login}}}"
    "}}}}}"
)
REVIEW_THREAD_COMMENTS_QUERY = (
    "query($threadId:ID!,$commentsCursor:String){"
    "node(id:$threadId){... on PullRequestReviewThread{"
    "comments(first:50,after:$commentsCursor){pageInfo{hasNextPage endCursor} "
    "nodes{id body path line originalLine outdated author{login}}}"
    "}}}"
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


def _run_gh_json(
    gh_path: str,
    args: list[str],
    *,
    command_name: str,
    command_trace: list[dict[str, Any]],
    timeout_seconds: int = DEFAULT_GH_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    command_trace.append({"argv": ["gh", *args], "read_only": True})
    try:
        result = subprocess.run(
            [gh_path, *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return None, _issue(
            "gh_command_timeout",
            f"{command_name} timed out",
            detail=f"{command_name} timed out after {exc.timeout} seconds",
            timeout_seconds=exc.timeout,
        )
    except OSError as exc:
        return None, _issue(
            "gh_spawn_failed",
            f"{command_name} could not start GitHub CLI",
            detail=str(exc),
        )
    return _read_json_stdout(result, command_name=command_name)


def _write_json_temp(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        return Path(handle.name)


def _evidence_write_issue(message: str, *, detail: str | None = None) -> dict[str, Any]:
    extra = {"detail": detail} if detail else {}
    return _issue("evidence_write_failed", message, **extra)


def write_evidence_files(files: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any] | None:
    temp_files: list[tuple[Path, Path]] = []
    backups: dict[Path, Path] = {}
    written: list[Path] = []
    try:
        for path, _data in files:
            if path.exists() and not path.is_file():
                return _issue(
                    "evidence_target_not_file",
                    f"evidence target is not a regular file: {path}",
                    path=str(path),
                )

        for path, data in files:
            temp_files.append((_write_json_temp(path, data), path))

        for temp_path, final_path in temp_files:
            if final_path.exists():
                backup_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.bak")
                os.replace(final_path, backup_path)
                backups[final_path] = backup_path
            os.replace(temp_path, final_path)
            written.append(final_path)
    except OSError as exc:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        for final_path, backup_path in backups.items():
            try:
                if backup_path.exists():
                    os.replace(backup_path, final_path)
            except OSError:
                pass
        for temp_path, _final_path in temp_files:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
        return _evidence_write_issue("could not write complete GitHub evidence files", detail=str(exc))

    for backup_path in backups.values():
        try:
            if backup_path.exists():
                backup_path.unlink()
        except OSError:
            pass
    return None


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


def _review_threads_args(owner: str, name: str, pr_number: int, threads_cursor: str | None = None) -> list[str]:
    args = [
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
    if threads_cursor:
        args.extend(["-f", f"threadsCursor={threads_cursor}"])
    return args


def _review_thread_comments_args(thread_id: str, comments_cursor: str | None = None) -> list[str]:
    args = [
        "api",
        "graphql",
        "-f",
        f"threadId={thread_id}",
        "-f",
        f"query={REVIEW_THREAD_COMMENTS_QUERY}",
    ]
    if comments_cursor:
        args.extend(["-f", f"commentsCursor={comments_cursor}"])
    return args


def review_thread_comments_object_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    current: Any = payload
    for key in ("data", "node", "comments"):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def _page_info_is_complete(page_info: Any) -> bool:
    return isinstance(page_info, dict) and page_info.get("hasNextPage") is False


def _next_cursor(page_info: Any) -> str | None:
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    return cursor if isinstance(cursor, str) and cursor else None


def _fetch_remaining_review_thread_comments(
    *,
    gh_path: str,
    thread: dict[str, Any],
    command_trace: list[dict[str, Any]],
) -> dict[str, Any] | None:
    comments = thread.get("comments")
    if not isinstance(comments, dict) or not isinstance(comments.get("nodes"), list):
        return None
    page_info = comments.get("pageInfo")
    if _page_info_is_complete(page_info):
        return None
    all_nodes = list(comments["nodes"])
    seen_cursors: set[str] = set()
    while isinstance(page_info, dict) and page_info.get("hasNextPage") is True:
        cursor = _next_cursor(page_info)
        if cursor is None:
            break
        if cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            break
        payload, blocker = _run_gh_json(
            gh_path,
            _review_thread_comments_args(thread_id, cursor),
            command_name="gh api graphql",
            command_trace=command_trace,
        )
        if blocker is not None:
            return blocker
        next_comments = review_thread_comments_object_from_payload(payload)
        next_nodes = next_comments.get("nodes") if next_comments is not None else None
        if not isinstance(next_comments, dict) or not isinstance(next_nodes, list):
            break
        all_nodes.extend(next_nodes)
        page_info = next_comments.get("pageInfo")

    comments["nodes"] = all_nodes
    if _page_info_is_complete(page_info):
        comments["pageInfo"] = {"hasNextPage": False, "endCursor": None}
    else:
        comments["pageInfo"] = page_info
    return None


def _fetch_review_threads_payload(
    *,
    gh_path: str,
    owner: str,
    name: str,
    pr_number: int,
    command_trace: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    threads_cursor: str | None = None
    aggregate_payload: dict[str, Any] | None = None
    aggregate_nodes: list[Any] = []
    page_info: Any = None
    seen_cursors: set[str] = set()

    while True:
        payload, blocker = _run_gh_json(
            gh_path,
            _review_threads_args(owner, name, pr_number, threads_cursor),
            command_name="gh api graphql",
            command_trace=command_trace,
        )
        if blocker is not None:
            return None, blocker
        review_threads = review_threads_object(payload)
        nodes = review_threads.get("nodes") if review_threads is not None else None
        if review_threads is None or not isinstance(nodes, list):
            return payload, None
        if aggregate_payload is None:
            aggregate_payload = payload

        for thread in nodes:
            if not isinstance(thread, dict):
                aggregate_nodes.append(thread)
                continue
            completed_thread = dict(thread)
            comments = completed_thread.get("comments")
            if isinstance(comments, dict):
                completed_thread["comments"] = dict(comments)
            blocker = _fetch_remaining_review_thread_comments(
                gh_path=gh_path,
                thread=completed_thread,
                command_trace=command_trace,
            )
            if blocker is not None:
                return None, blocker
            aggregate_nodes.append(completed_thread)

        page_info = review_threads.get("pageInfo")
        if _page_info_is_complete(page_info):
            break
        next_cursor = _next_cursor(page_info)
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        threads_cursor = next_cursor

    if aggregate_payload is None:
        return None, _issue("gh_json_not_object", "gh api graphql must return a JSON object")
    aggregate_review_threads = review_threads_object(aggregate_payload)
    if aggregate_review_threads is not None:
        aggregate_review_threads["nodes"] = aggregate_nodes
        aggregate_review_threads["pageInfo"] = (
            {"hasNextPage": False, "endCursor": None}
            if _page_info_is_complete(page_info)
            else page_info
        )
    return aggregate_payload, None


def _blocked_packet(
    *,
    repo: str,
    pr_number: int,
    out_dir: Path,
    blocker: dict[str, Any],
    command_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    code = blocker["code"]
    if code in {"gh_missing", "gh_auth_failed", "gh_spawn_failed"}:
        action = "install_or_authenticate_gh"
    elif code in {"github_rate_limited", "github_network_failed", "gh_command_timeout"}:
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
    command_trace: list[dict[str, Any]] = []
    pr_payload, blocker = _run_gh_json(
        gh_path,
        pr_args,
        command_name="gh pr view",
        command_trace=command_trace,
    )
    if blocker is not None:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=blocker,
            command_trace=command_trace,
        )
    threads_payload, blocker = _fetch_review_threads_payload(
        gh_path=gh_path,
        owner=owner,
        name=name,
        pr_number=pr_number,
        command_trace=command_trace,
    )
    if blocker is not None:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=blocker,
            command_trace=command_trace,
        )
    _findings, review_thread_warnings = review_thread_findings_from_payload(threads_payload)
    if review_thread_warnings:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=_issue(
                "review_thread_evidence_incomplete",
                "review thread evidence is incomplete or malformed",
                warnings=review_thread_warnings,
            ),
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
    blocker = write_evidence_files(
        [
            (pr_file, pr_payload),
            (threads_file, threads_payload),
            (summary_file, packet),
        ]
    )
    if blocker is not None:
        return _blocked_packet(
            repo=repo,
            pr_number=pr_number,
            out_dir=out_dir,
            blocker=blocker,
            command_trace=command_trace,
        )
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


def _check_url(item: dict[str, Any]) -> str:
    url = item.get("detailsUrl") or item.get("targetUrl")
    return url.strip() if isinstance(url, str) else ""


def _check_finding_id(pr_number: Any, item: dict[str, Any], name: str, state: str, workflow: str) -> str:
    identity = {
        "check": name,
        "source_type": item.get("__typename") or "",
        "state": state,
        "workflow": workflow,
    }
    digest = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return f"pr-{pr_number or 'unknown'}-check-{digest}"


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
        workflow = _check_workflow(item)
        url = _check_url(item)
        finding: dict[str, Any] = {
            "id": _check_finding_id(pr_number, item, name, state, workflow),
            "check": name,
            "state": state,
            "workflow": workflow,
            "source": "status_check_rollup",
        }
        if url:
            finding["url"] = url
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


def review_threads_object(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    current: Any = payload
    for key in ("data", "repository", "pullRequest", "reviewThreads"):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def review_threads_nodes(payload: Any) -> list[Any] | None:
    review_threads = review_threads_object(payload)
    if review_threads is None:
        return None
    nodes = review_threads.get("nodes")
    return nodes if isinstance(nodes, list) else None


def _pagination_warnings(review_threads: dict[str, Any], nodes: list[Any]) -> list[str]:
    warnings: list[str] = []
    page_info = review_threads.get("pageInfo")
    if not isinstance(page_info, dict) or not isinstance(page_info.get("hasNextPage"), bool):
        warnings.append("reviewThreads pageInfo.hasNextPage is required")
    elif page_info["hasNextPage"]:
        warnings.append("reviewThreads payload is incomplete; more review threads are available")
    for index, thread in enumerate(nodes, start=1):
        if not isinstance(thread, dict):
            continue
        comments = thread.get("comments")
        if not isinstance(comments, dict):
            warnings.append(f"review thread {index} comments must be an object with nodes and pageInfo")
            continue
        comment_page_info = comments.get("pageInfo")
        if not isinstance(comment_page_info, dict) or not isinstance(comment_page_info.get("hasNextPage"), bool):
            warnings.append(f"review thread {index} comments pageInfo.hasNextPage is required")
        elif comment_page_info["hasNextPage"]:
            warnings.append(f"review thread {index} comments payload is incomplete; more comments are available")
    return warnings


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
    review_threads = review_threads_object(payload)
    nodes = review_threads.get("nodes") if review_threads is not None else None
    if review_threads is None or not isinstance(nodes, list):
        return [], ["review threads payload must contain a GitHub reviewThreads JSON object"]

    warnings: list[str] = _pagination_warnings(review_threads, nodes)
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
