from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any


CHANGELOG_RELEASE_RE = re.compile(
    r"^##\s+(?P<version>\S+)\s+-\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)
RELEASE_NOTES_HEADING_RE = re.compile(r"^###\s+Release Notes\s*$", re.MULTILINE)


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    issue = {"code": code, "message": message}
    issue.update(extra)
    return issue


def _read_project_metadata(path: Path) -> dict[str, str]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"could not read package metadata file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"could not parse package metadata file {path}: {exc}") from exc

    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("package metadata is missing [project]")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("package metadata is missing project.name")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("package metadata is missing project.version")
    return {"name": name.strip(), "version": version.strip()}


def _read_changelog(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"could not read changelog file {path}: {exc}") from exc


def _changelog_entries(text: str) -> list[dict[str, str]]:
    matches = list(CHANGELOG_RELEASE_RE.finditer(text))
    entries = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        version = match.group("version").strip()
        date = match.group("date").strip()
        entries.append(
            {
                "version": version,
                "date": date,
                "body": body,
                "heading": f"## {version} - {date}",
            }
        )
    return entries


def _find_changelog_entry(text: str, version: str) -> tuple[dict[str, str] | None, str | None]:
    entries = _changelog_entries(text)
    latest_version = entries[0]["version"] if entries else None
    for entry in entries:
        if entry["version"] == version:
            return entry, latest_version
    return None, latest_version


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def _git_scalar(cwd: Path, args: list[str]) -> tuple[str | None, str | None]:
    result = _run_git(cwd, args)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout).strip()
    return result.stdout.strip(), None


def _inspect_git(
    cwd: Path,
    *,
    tag: str,
    target_branch: str,
    target_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    tag_summary = {
        "name": tag,
        "exists": False,
        "target_sha": None,
        "matches_target": False,
    }
    summary: dict[str, Any] = {
        "cwd": str(cwd),
        "target_ref": target_ref,
        "target_sha": None,
        "checked_out_sha": None,
        "current_branch": None,
        "target_branch": target_branch,
        "worktree_clean": False,
        "remote_target_sha": None,
        "tag": tag_summary,
    }

    if not cwd.exists():
        blockers.append(_issue("release_cwd_missing", f"release cwd does not exist: {cwd}", cwd=str(cwd)))
        return summary, blockers, warnings
    if not cwd.is_dir():
        blockers.append(_issue("release_cwd_not_directory", f"release cwd is not a directory: {cwd}", cwd=str(cwd)))
        return summary, blockers, warnings

    inside, error = _git_scalar(cwd, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        blockers.append(_issue("git_repo_missing", error or "release cwd is not inside a Git worktree"))
        return summary, blockers, warnings

    target_sha, error = _git_scalar(cwd, ["rev-parse", "--verify", f"{target_ref}^{{commit}}"])
    if target_sha is None:
        blockers.append(
            _issue("target_ref_not_found", f"could not resolve release target ref: {target_ref}", detail=error)
        )
    else:
        summary["target_sha"] = target_sha

    checked_out_sha, error = _git_scalar(cwd, ["rev-parse", "--verify", "HEAD^{commit}"])
    if checked_out_sha is None:
        blockers.append(_issue("checked_out_ref_not_found", "could not resolve checked-out HEAD", detail=error))
    else:
        summary["checked_out_sha"] = checked_out_sha
        if target_sha and checked_out_sha != target_sha:
            blockers.append(
                _issue(
                    "target_ref_not_checked_out",
                    f"release target {target_sha} is not the checked-out HEAD {checked_out_sha}",
                    target_sha=target_sha,
                    checked_out_sha=checked_out_sha,
                )
            )

    current_branch, error = _git_scalar(cwd, ["branch", "--show-current"])
    summary["current_branch"] = current_branch or ""
    if not current_branch:
        blockers.append(_issue("target_branch_detached", "release target must be checked out on a named branch"))
    elif current_branch != target_branch:
        blockers.append(
            _issue(
                "target_branch_mismatch",
                f"release target branch is {current_branch}, expected {target_branch}",
                current_branch=current_branch,
                target_branch=target_branch,
            )
        )

    status = _run_git(cwd, ["status", "--porcelain", "--untracked-files=all"])
    if status.returncode != 0:
        blockers.append(_issue("worktree_status_failed", "could not inspect worktree status", detail=status.stderr.strip()))
    else:
        dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
        summary["worktree_clean"] = not dirty_lines
        if dirty_lines:
            blockers.append(
                _issue(
                    "worktree_not_clean",
                    "release dry run requires a clean worktree",
                    changed_paths=len(dirty_lines),
                )
            )

    if tag:
        check_ref = _run_git(cwd, ["check-ref-format", f"refs/tags/{tag}"])
        if check_ref.returncode != 0:
            blockers.append(_issue("invalid_release_tag", f"release tag is not a valid Git tag name: {tag}"))
        else:
            tag_target = _run_git(cwd, ["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"])
            if tag_target.returncode == 0:
                tag_sha = tag_target.stdout.strip()
                tag_summary["exists"] = True
                tag_summary["target_sha"] = tag_sha
                tag_summary["matches_target"] = bool(target_sha and tag_sha == target_sha)
                if target_sha and tag_sha != target_sha:
                    blockers.append(
                        _issue(
                            "tag_points_to_different_commit",
                            f"release tag {tag} points to {tag_sha}, not target {target_sha}",
                            tag=tag,
                            tag_sha=tag_sha,
                            target_sha=target_sha,
                        )
                    )
            elif tag_target.returncode not in (1,):
                blockers.append(
                    _issue("tag_lookup_failed", f"could not inspect release tag {tag}", detail=tag_target.stderr.strip())
                )

    remote_ref = f"refs/remotes/origin/{target_branch}"
    remote_target = _run_git(cwd, ["rev-parse", "--verify", "--quiet", f"{remote_ref}^{{commit}}"])
    if remote_target.returncode == 0:
        remote_sha = remote_target.stdout.strip()
        summary["remote_target_sha"] = remote_sha
        if target_sha and remote_sha != target_sha:
            blockers.append(
                _issue(
                    "target_not_at_origin_branch",
                    f"release target {target_sha} does not match {remote_ref} {remote_sha}",
                    target_sha=target_sha,
                    remote_ref=remote_ref,
                    remote_sha=remote_sha,
                )
            )
    else:
        warnings.append(
            _issue(
                "remote_branch_missing",
                f"could not verify {remote_ref} from local refs",
                remote_ref=remote_ref,
            )
        )

    return summary, blockers, warnings


def evaluate_release_dry_run(
    cwd: Path,
    *,
    version: str | None = None,
    tag: str | None = None,
    target_branch: str = "main",
    target_ref: str = "HEAD",
) -> dict[str, Any]:
    cwd = cwd.expanduser().resolve()
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    project_name = None
    project_version = None
    try:
        project = _read_project_metadata(cwd / "pyproject.toml")
        project_name = project["name"]
        project_version = project["version"]
    except ValueError as exc:
        blockers.append(_issue("package_metadata_invalid", str(exc)))

    requested_version = version.strip() if isinstance(version, str) else None
    release_version = requested_version or project_version or ""
    if requested_version and project_version and requested_version != project_version:
        blockers.append(
            _issue(
                "version_mismatch",
                f"requested release version {requested_version} does not match package version {project_version}",
                requested_version=requested_version,
                package_version=project_version,
            )
        )
    if not release_version:
        blockers.append(_issue("release_version_missing", "release dry run requires a package version or --version"))

    release_tag = tag.strip() if isinstance(tag, str) else f"v{release_version}" if release_version else ""
    expected_tag = f"v{release_version}" if release_version else ""
    if not release_tag:
        blockers.append(_issue("release_tag_missing", "release dry run requires a non-empty release tag"))
    elif tag and expected_tag and release_tag != expected_tag:
        blockers.append(
            _issue(
                "tag_version_mismatch",
                f"release tag {release_tag} does not match expected tag {expected_tag}",
                tag=release_tag,
                expected_tag=expected_tag,
            )
        )

    changelog_entry = None
    release_notes = ""
    try:
        changelog = _read_changelog(cwd / "CHANGELOG.md")
        if release_version:
            changelog_entry, latest_version = _find_changelog_entry(changelog, release_version)
            if changelog_entry is None:
                blockers.append(
                    _issue("changelog_entry_missing", f"CHANGELOG.md has no release entry for {release_version}")
                )
            else:
                if latest_version != release_version:
                    blockers.append(
                        _issue(
                            "changelog_entry_not_latest",
                            f"CHANGELOG.md latest release entry is {latest_version}, not {release_version}",
                            latest_version=latest_version,
                            release_version=release_version,
                        )
                    )
                if not RELEASE_NOTES_HEADING_RE.search(changelog_entry["body"]):
                    blockers.append(
                        _issue(
                            "changelog_release_notes_missing",
                            f"CHANGELOG.md release entry {release_version} is missing a Release Notes section",
                        )
                    )
                release_notes = f"{changelog_entry['heading']}\n\n{changelog_entry['body'].strip()}\n"
    except ValueError as exc:
        blockers.append(_issue("changelog_invalid", str(exc)))

    git_summary, git_blockers, git_warnings = _inspect_git(
        cwd,
        tag=release_tag,
        target_branch=target_branch,
        target_ref=target_ref,
    )
    blockers.extend(git_blockers)
    warnings.extend(git_warnings)

    if blockers:
        decision = "blocked"
        action = "address_blockers"
    elif git_summary["tag"]["exists"]:
        decision = "ready"
        action = "create_github_release_after_operator_confirmation"
    else:
        decision = "ready"
        action = "create_tag_after_operator_confirmation"

    return {
        "ready_to_release": decision == "ready",
        "decision": decision,
        "recommended_next_action": action,
        "operator_confirmation_required": True,
        "dry_run": True,
        "side_effects": [],
        "blockers": blockers,
        "warnings": warnings,
        "release": {
            "project": project_name,
            "version": release_version,
            "date": changelog_entry["date"] if changelog_entry else None,
            "tag": release_tag,
            "changelog_path": str(cwd / "CHANGELOG.md"),
            "pyproject_path": str(cwd / "pyproject.toml"),
        },
        "git": git_summary,
        "release_notes": release_notes,
        "package_publication": {
            "allowed": False,
            "recommended_next_action": "do_not_publish_package",
            "reason": "package-index publication remains a separate operator-reviewed release decision",
        },
    }
