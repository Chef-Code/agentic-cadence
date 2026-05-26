#!/usr/bin/env python3
"""Free preflight gate for the Codex PR review workflow.

This script performs deterministic checks before any OpenAI-backed review action
is allowed to run. It intentionally depends only on the standard library, git,
and GitHub metadata available to the workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CANONICAL_MARKER_RE = re.compile(
    r"\A## Codex Review\r?\n\r?\n"
    r"<!--\s*codex-review:v1\s+head=(?P<head>[^\s>]+)\s+dedupe=(?P<dedupe>[^\s>]+)\s*-->"
)
TRUSTED_MARKER_AUTHORS = {"github-actions[bot]"}
SKIP_LABELS = {"codex-review-skip", "skip-codex-review"}
ELECT_LABELS = {"codex-review-elect", "elect-codex-review"}
FORCE_LABELS = {"codex-review-force", "force-codex-review"}

DOCS_ONLY_EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".adoc",
}
DOCS_ONLY_PATH_PREFIXES = (
    "docs/",
)
DOCS_ONLY_FILENAMES = {
    "README",
    "README.md",
    "CHANGELOG",
    "CHANGELOG.md",
}


def normalize_changed_files(changed_files: list[str]) -> list[str]:
    normalized = []
    for path in changed_files:
        value = path.strip().replace("\\", "/")
        if value:
            normalized.append(value)
    return normalized


def compute_dedupe_key(head_sha: str, changed_files: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(b"codex-review-preflight:v1\n")
    digest.update(head_sha.strip().encode("utf-8"))
    digest.update(b"\n")
    for path in sorted(normalize_changed_files(changed_files)):
        digest.update(path.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def normalize_labels(labels: list[str] | None) -> set[str]:
    return {str(label).strip().lower() for label in labels or [] if str(label).strip()}


def is_docs_only_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return False
    if normalized in DOCS_ONLY_FILENAMES:
        return True
    return normalized.startswith(DOCS_ONLY_PATH_PREFIXES) and (
        Path(normalized).suffix in DOCS_ONLY_EXTENSIONS
    )


def parse_review_markers(comments: list[dict[str, Any]]) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    for comment in comments:
        if not is_trusted_marker_comment(comment):
            continue
        body = str(comment.get("body") or "")
        match = CANONICAL_MARKER_RE.match(body)
        if match:
            markers.append(match.groupdict())
    return markers


def is_trusted_marker_comment(comment: dict[str, Any]) -> bool:
    user = comment.get("user")
    login = ""
    if isinstance(user, dict):
        login = str(user.get("login") or "")
    return login in TRUSTED_MARKER_AUTHORS


def has_matching_review_marker(
    comments: list[dict[str, Any]], head_sha: str, dedupe_key: str
) -> bool:
    for marker in parse_review_markers(comments):
        if marker["head"] == head_sha and marker["dedupe"] == dedupe_key:
            return True
    return False


def decide_preflight(
    *,
    head_sha: str,
    changed_files: list[str],
    comments: list[dict[str, Any]],
    comments_available: bool,
    pr_title: str,
    pr_body: str,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    files = normalize_changed_files(changed_files)
    dedupe_key = compute_dedupe_key(head_sha, files)
    normalized_labels = normalize_labels(labels)

    decision: dict[str, Any] = {
        "should_run": False,
        "reason": "not_evaluated",
        "dedupe_key": dedupe_key,
        "changed_files_count": len(files),
        "fail_check": False,
    }

    if normalized_labels & SKIP_LABELS:
        decision["reason"] = "operator_skip"
        return decision

    review_elected = bool(normalized_labels & ELECT_LABELS)

    if normalized_labels & FORCE_LABELS:
        decision["should_run"] = True
        decision["reason"] = "force_requested"
        return decision

    if not head_sha.strip():
        decision["reason"] = "missing_head_sha"
        decision["fail_check"] = True
        return decision

    if not files:
        decision["reason"] = "no_diff"
        return decision

    if not review_elected and all(is_docs_only_path(path) for path in files):
        decision["reason"] = "docs_only"
        return decision

    if not review_elected:
        decision["reason"] = "not_elected"
        return decision

    if not comments_available:
        decision["reason"] = "comments_unavailable"
        decision["fail_check"] = True
        return decision

    if has_matching_review_marker(comments, head_sha, dedupe_key):
        decision["reason"] = "already_reviewed"
        return decision

    decision["should_run"] = True
    decision["reason"] = "operator_elected"
    return decision


def read_changed_files_from_file(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def read_changed_files_from_git(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout.splitlines()


def load_comments_from_file(path: Path) -> tuple[list[dict[str, Any]], bool]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], False
    if not isinstance(data, list):
        return [], False
    comments = [item for item in data if isinstance(item, dict)]
    return comments, True


def parse_labels_json(value: str) -> list[str]:
    if not value:
        return []
    try:
        labels = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(labels, list):
        return []
    return [str(label) for label in labels]


def load_comments_from_github(
    *, repo: str, pr_number: str, token: str
) -> tuple[list[dict[str, Any]], bool]:
    if not repo or not pr_number or not token:
        return [], False

    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
            f"?per_page=100&page={page}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "codex-review-preflight",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                page_comments = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return [], False

        if not isinstance(page_comments, list):
            return [], False

        comments.extend(item for item in page_comments if isinstance(item, dict))
        if len(page_comments) < 100:
            return comments, True
        page += 1


def format_output_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\r", " ").replace("\n", " ")


def write_github_outputs(decision: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output:
        for key in (
            "should_run",
            "reason",
            "dedupe_key",
            "changed_files_count",
            "fail_check",
        ):
            output.write(f"{key}={format_output_value(decision[key])}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head-sha", default=os.environ.get("HEAD_SHA", ""))
    parser.add_argument("--base-ref", default=os.environ.get("BASE_REF", ""))
    parser.add_argument("--head-ref", default=os.environ.get("HEAD_REF", ""))
    parser.add_argument("--changed-files-file")
    parser.add_argument("--comments-file")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr-number", default=os.environ.get("PR_NUMBER", ""))
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--pr-title", default=os.environ.get("PR_TITLE", ""))
    parser.add_argument("--pr-body", default=os.environ.get("PR_BODY", ""))
    parser.add_argument("--labels-json", default=os.environ.get("PR_LABELS_JSON", ""))
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.changed_files_file:
            changed_files = read_changed_files_from_file(Path(args.changed_files_file))
        else:
            changed_files = read_changed_files_from_git(args.base_ref, args.head_ref)
    except (RuntimeError, OSError) as error:
        changed_files = []
        comments: list[dict[str, Any]] = []
        decision = {
            "should_run": False,
            "reason": "diff_unavailable",
            "dedupe_key": compute_dedupe_key(args.head_sha, changed_files),
            "changed_files_count": 0,
            "fail_check": True,
            "error": str(error),
        }
        if args.github_output:
            write_github_outputs(decision, Path(args.github_output))
        print(json.dumps(decision, sort_keys=True))
        return 1

    if args.comments_file:
        comments, comments_available = load_comments_from_file(Path(args.comments_file))
    else:
        comments, comments_available = load_comments_from_github(
            repo=args.repo, pr_number=args.pr_number, token=args.github_token
        )

    decision = decide_preflight(
        head_sha=args.head_sha,
        changed_files=changed_files,
        comments=comments,
        comments_available=comments_available,
        pr_title=args.pr_title,
        pr_body=args.pr_body,
        labels=parse_labels_json(args.labels_json),
    )

    if args.github_output:
        write_github_outputs(decision, Path(args.github_output))

    if args.json or not args.github_output:
        print(json.dumps(decision, sort_keys=True))
    else:
        print(
            "Codex review preflight: "
            f"should_run={format_output_value(decision['should_run'])} "
            f"reason={decision['reason']} "
            f"dedupe_key={decision['dedupe_key']}"
        )
    return 1 if decision["fail_check"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
