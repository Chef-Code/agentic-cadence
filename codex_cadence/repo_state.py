from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .store import utc_now

REPO_CONFIDENCE_VALUES = ("high", "medium", "low")


def run_git(cwd: str | Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=Path(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(message)
    return result.stdout.strip()


def git_repo_root(cwd: str | Path) -> Path | None:
    try:
        return Path(run_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, RuntimeError):
        return None


def path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def git_ignores_path(repo_root: str | Path, path: str | Path) -> bool:
    root = Path(repo_root).resolve()
    candidate = Path(path).resolve(strict=False)
    if not path_is_relative_to(candidate, root):
        return False
    relative = candidate.relative_to(root)
    if not relative.parts:
        return False
    candidates = [relative.as_posix()]
    if not candidates[0].endswith("/"):
        candidates.append(f"{candidates[0]}/")
    for candidate in candidates:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", candidate],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def runtime_root_safety_issue(root: str | Path, target_cwd: str | Path) -> str | None:
    repo_root = git_repo_root(target_cwd)
    if repo_root is None:
        return None
    # CLI callers resolve --root first; this keeps direct helper calls safe too.
    runtime_root = Path(root).resolve(strict=False)
    if not path_is_relative_to(runtime_root, repo_root):
        return None
    if git_ignores_path(repo_root, runtime_root):
        return None
    return (
        "runtime root is inside target repo but is not ignored; use a root outside the repo, "
        "add the runtime root to .gitignore, or pass --allow-repo-local-root"
    )


def dirty_worktree(cwd: str | Path) -> bool:
    return bool(run_git(cwd, "status", "--porcelain"))


def current_branch(cwd: str | Path) -> str | None:
    branch = run_git(cwd, "branch", "--show-current")
    return branch or None


def current_head(cwd: str | Path) -> str | None:
    try:
        return run_git(cwd, "rev-parse", "--verify", "HEAD")
    except RuntimeError:
        return None


def confidence(
    dirty: bool,
    known_failures: list[str],
    unborn_head: bool = False,
    detached_head: bool = False,
) -> tuple[str, list[str]]:
    drivers = []
    if dirty:
        drivers.append("dirty_worktree")
    if known_failures:
        drivers.append("known_failures")
    if unborn_head:
        drivers.append("unborn_head")
    if detached_head:
        drivers.append("detached_head")
    return ("low" if drivers else "high", drivers)


def local_repo_readiness_evidence() -> dict[str, Any]:
    return {
        "source": "local_git",
        "freshness": "local_only",
        "live": False,
        "stale": False,
        "limitations": [
            "open_prs_not_fetched",
            "review_threads_not_fetched",
            "ci_status_operator_supplied",
        ],
    }


def snapshot_repo(
    cwd: str | Path,
    repo: str | None = None,
    active_pr: int | None = None,
    known_failures: list[str] | None = None,
    ci_status: str = "unknown",
) -> dict[str, Any]:
    repo_cwd = Path(cwd).expanduser().resolve()
    failures = list(known_failures or [])
    dirty = dirty_worktree(repo_cwd)
    branch = current_branch(repo_cwd)
    head = current_head(repo_cwd)
    repo_confidence, drivers = confidence(
        dirty,
        failures,
        unborn_head=head is None,
        detached_head=head is not None and branch is None,
    )
    return {
        "repo": repo,
        "cwd": str(repo_cwd),
        "branch": branch,
        "head": head,
        "ci": ci_status,
        "open_prs": [],
        "active_pr": active_pr,
        "unresolved_review_threads": None,
        "dirty_worktree": dirty,
        "known_failures": failures,
        "repo_confidence": repo_confidence,
        "repo_confidence_drivers": drivers,
        "readiness_evidence": local_repo_readiness_evidence(),
        "captured_at": utc_now(),
    }


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_repo_snapshot(
    snapshot: Any,
    expected_repo: str | None = None,
    expected_branch: str | None = None,
) -> tuple[bool, str | None]:
    if not isinstance(snapshot, dict):
        return False, "snapshot must be a JSON object"
    if not isinstance(snapshot.get("id"), str) or not snapshot["id"].strip():
        return False, "snapshot id is required"
    if "repo" not in snapshot:
        return False, "snapshot repo is required"
    if snapshot.get("repo") is not None and not isinstance(snapshot["repo"], str):
        return False, "snapshot repo must be a string or null"
    if expected_repo is not None and snapshot.get("repo") != expected_repo:
        return False, "snapshot repo does not match epoch repo"
    if not isinstance(snapshot.get("cwd"), str) or not snapshot["cwd"].strip():
        return False, "snapshot cwd is required"
    if snapshot.get("branch") is not None and not isinstance(snapshot["branch"], str):
        return False, "snapshot branch must be a string or null"
    if expected_branch is not None and snapshot.get("branch") != expected_branch:
        return False, "snapshot branch does not match epoch branch"
    if "head" not in snapshot:
        return False, "snapshot head is required"
    if snapshot.get("head") is not None and not isinstance(snapshot["head"], str):
        return False, "snapshot head must be a string or null"
    if snapshot.get("repo_confidence") not in REPO_CONFIDENCE_VALUES:
        return False, "snapshot repo_confidence is invalid"
    if not isinstance(snapshot.get("captured_at"), str) or not snapshot["captured_at"].strip():
        return False, "snapshot captured_at is required"
    if not isinstance(snapshot.get("dirty_worktree"), bool):
        return False, "snapshot dirty_worktree must be a boolean"
    if not _is_string_list(snapshot.get("known_failures")):
        return False, "snapshot known_failures must be a list of strings"
    if not _is_string_list(snapshot.get("repo_confidence_drivers")):
        return False, "snapshot repo_confidence_drivers must be a list of strings"
    if not isinstance(snapshot.get("ci"), str):
        return False, "snapshot ci must be a string"
    if not isinstance(snapshot.get("open_prs"), list):
        return False, "snapshot open_prs must be a list"
    if "active_pr" not in snapshot:
        return False, "snapshot active_pr is required"
    if snapshot.get("active_pr") is not None and (isinstance(snapshot["active_pr"], bool) or not isinstance(snapshot["active_pr"], int)):
        return False, "snapshot active_pr must be an integer or null"
    if "unresolved_review_threads" not in snapshot:
        return False, "snapshot unresolved_review_threads is required"
    if snapshot.get("unresolved_review_threads") is not None and (
        isinstance(snapshot["unresolved_review_threads"], bool) or not isinstance(snapshot["unresolved_review_threads"], int)
    ):
        return False, "snapshot unresolved_review_threads must be an integer or null"
    return True, None
