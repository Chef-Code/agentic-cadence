import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import codex_cadence.executor_readiness as executor_readiness
import codex_cadence.github_evidence as github_evidence
from codex_cadence.approvals import build_operator_approval_verification_packet
from codex_cadence.cli import build_executor_result_validation_payload
from codex_cadence.executor_contract import (
    DEFAULT_EXECUTOR_STOP_CONDITIONS,
    build_execution_run_record,
    build_executor_task_packet,
)
from codex_cadence.executor_runner import run_controlled_executor_fixture
from codex_cadence.model import estimate_task
from codex_cadence.policy_audit import (
    append_audit_record,
    checksum_json,
    execution_start_audit_record,
    executor_epoch_closeout_audit_record,
    operator_approval_verification_audit_record,
    real_executor_invocation_audit_record,
)
from codex_cadence.store import default_root, exclusive_lock, lock_path, snapshot_path as persisted_snapshot_path, utc_now


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def run_cli(root, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_cli_from(cwd, root, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def current_head(path):
    return git(path, "rev-parse", "HEAD").stdout.strip()


def current_branch(path):
    return git(path, "branch", "--show-current").stdout.strip()


def init_committed_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (Path(path) / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def ready_handoff_path(root, handoff_id):
    return Path(root) / "handoffs" / "ready" / f"{handoff_id}.json"


def rewrite_ready_handoff(root, handoff_id, update):
    path = ready_handoff_path(root, handoff_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    update(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def valid_snapshot(**overrides):
    snapshot = {
        "id": "snapshot-1",
        "repo": "local/test",
        "cwd": "/tmp/local-test",
        "branch": "main",
        "head": "abc123",
        "ci": "green",
        "open_prs": [],
        "active_pr": None,
        "unresolved_review_threads": None,
        "dirty_worktree": False,
        "known_failures": [],
        "repo_confidence": "high",
        "repo_confidence_drivers": [],
        "readiness_evidence": {
            "source": "local_git",
            "freshness": "local_only",
            "live": False,
            "stale": False,
            "limitations": [
                "open_prs_not_fetched",
                "review_threads_not_fetched",
                "ci_status_operator_supplied",
            ],
        },
        "captured_at": "2999-05-22T00:00:00Z",
    }
    snapshot.update(overrides)
    return snapshot


def write_active_epoch(root, epoch_id, snapshot_before, policy=None, tasks=None):
    path = Path(root) / "epochs" / "active" / f"{epoch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "id": epoch_id,
        "status": "ACTIVE",
        "repo": snapshot_before.get("repo", "local/test") if isinstance(snapshot_before, dict) else "local/test",
        "branch": snapshot_before.get("branch", "main") if isinstance(snapshot_before, dict) else "main",
        "tasks": list(tasks or []),
        "completed_tasks": [],
        "snapshot_before": snapshot_before,
        "policy": policy if policy is not None else {"allow_recursive_discovery": False},
        "started_at": utc_now(),
        "updated_at": utc_now(),
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_active_epoch_raw(root, epoch_id):
    path = Path(root) / "epochs" / "active" / f"{epoch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": epoch_id, "status": "ACTIVE"}), encoding="utf-8")
    return path


def write_work_ownership(root, ownership_id, **overrides):
    status = overrides.pop("status", "ACTIVE")
    state = overrides.pop("state", status.lower())
    path = Path(root) / "work-ownership" / state / f"{ownership_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "work-ownership.v1",
        "id": ownership_id,
        "task_id": "task-1",
        "candidate_id": "candidate-1",
        "role": "implementer",
        "claimer": "test-agent",
        "repo": "local/test",
        "branch": "main",
        "pr_number": 76,
        "epoch_id": "epoch-1",
        "handoff_id": "handoff-1",
        "status": status,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def write_role_policy(path, **overrides):
    data = {
        "schema_version": "role-policy.v1",
        "roles": [
            {"role": "implementer", "capabilities": ["build", "modify_files"]},
            {"role": "reviewer", "capabilities": ["review", "comment"]},
        ],
        "review_separation": {
            "required": True,
            "builder_roles": ["implementer"],
            "reviewer_roles": ["reviewer"],
        },
    }
    data.update(overrides)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return Path(path), data


def write_role_pr_json(path, **overrides):
    data = {
        "number": 76,
        "title": "Bind role readiness",
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "headRefName": "codex/task-16",
        "baseRefName": "main",
        "headRefOid": "abc123",
        "statusCheckRollup": [],
    }
    data.update(overrides)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return Path(path), data


def write_matching_role_pr_json(path, repo, **overrides):
    data = {
        "headRefName": current_branch(repo),
        "headRefOid": current_head(repo),
    }
    data.update(overrides)
    return write_role_pr_json(
        path,
        **data,
    )


def role_review_threads(
    author="reviewer-agent",
    *,
    resolved=False,
    outdated=False,
    body="Please fix this before merge.",
):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "thread-1",
                                "path": "codex_cadence/roles.py",
                                "line": 42,
                                "isResolved": resolved,
                                "isOutdated": outdated,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {
                                            "id": "comment-1",
                                            "path": "codex_cadence/roles.py",
                                            "line": 42,
                                            "outdated": outdated,
                                            "body": body,
                                            "author": {"login": author},
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        }
    }


def write_role_review_threads(path, payload):
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return Path(path), payload


def governed_execution_task_packet(root, repo, **snapshot_overrides):
    return build_executor_task_packet(
        task={
            "id": "candidate-1",
            "title": "Implement governed execution start",
            "summary": "Start one governed epoch from an approved task packet.",
            "task_type": "execution",
            "bucket": "S",
            "source": "text_marker",
            "drivers": ["governance"],
            "evidence": {"path": "docs/roadmaps/2026-06-03-tasks-8-12-roadmap.md"},
        },
        snapshot=valid_snapshot(
            repo="local/test",
            cwd=str(Path(repo).resolve()),
            branch=current_branch(repo),
            head=current_head(repo),
            **snapshot_overrides,
        ),
        repo_path=repo,
        allowed_paths=["README.md"],
        required_checks=["python -m unittest tests.test_cadence"],
        max_minutes=30,
        max_tasks=1,
        stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
        evidence_path=Path(root) / "executor-result.json",
    )


def write_governed_execution_task(root, repo, task_packet=None):
    packet = governed_execution_task_packet(root, repo) if task_packet is None else task_packet
    task_path = Path(root) / "executor-task.json"
    task_path.write_text(json.dumps(packet), encoding="utf-8")
    approval_token = f"approve-executor-task:{checksum_json(packet)}"
    return task_path, packet, approval_token


OPERATOR_APPROVAL_SECRET = "unit-test-operator-approval-secret"
OPERATOR_APPROVAL_TARGET = "sha256:" + "b" * 64


def iso_z(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sign_operator_approval(packet, secret=OPERATOR_APPROVAL_SECRET):
    signed_payload = {key: value for key, value in packet.items() if key != "signature"}
    body = json.dumps(signed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "hmac-sha256:" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def operator_approval_packet(**overrides):
    now = datetime.now(timezone.utc)
    signature_override = overrides.pop("signature", None)
    packet = {
        "schema_version": "operator-approval.v1",
        "target_checksum": OPERATOR_APPROVAL_TARGET,
        "purpose": "start_governed_execution",
        "operator_id": "operator@example.test",
        "key_id": "local-key-1",
        "issued_at": iso_z(now - timedelta(minutes=1)),
        "expires_at": iso_z(now + timedelta(minutes=10)),
    }
    packet.update(overrides)
    packet["signature"] = signature_override if signature_override is not None else sign_operator_approval(packet)
    return packet


def write_operator_approval(path, **overrides):
    packet = operator_approval_packet(**overrides)
    Path(path).write_text(json.dumps(packet), encoding="utf-8")
    return Path(path), packet


def write_executor_invocation_adapter(path, **overrides):
    packet = {
        "schema_version": "executor-adapter.v1",
        "packet": "executor_adapter",
        "adapter_id": "local-python",
        "adapter_kind": "local_process",
        "command_template": "python -m unittest tests.test_cadence",
        "environment_allowlist": ["PATH", "PYTHONPATH"],
        "max_timeout_seconds": 900,
        "process_start_allowed": False,
    }
    packet.update(overrides)
    Path(path).write_text(json.dumps(packet), encoding="utf-8")
    return Path(path), packet


def write_executor_invocation_rollback(path, task_packet, **overrides):
    packet = {
        "schema_version": "executor-rollback.v1",
        "packet": "executor_rollback_evidence",
        "read_only": True,
        "task_checksum": checksum_json(task_packet),
        "repo": {
            "path": task_packet["repo"]["path"],
            "branch": task_packet["repo"]["branch"],
            "head": task_packet["repo"]["head"],
        },
        "strategy": "restore_clean_checkout",
        "rollback_commands": ["git status --short"],
        "side_effects": [],
    }
    packet.update(overrides)
    Path(path).write_text(json.dumps(packet), encoding="utf-8")
    return Path(path), packet


def executor_invocation_target_descriptor(
    *,
    readiness_packet,
    adapter_packet,
    rollback_packet,
    command,
    cwd,
    expected_result_path,
    environment_allowlist,
    timeout_seconds,
    audit_chain_head,
):
    return {
        "schema_version": "executor-invocation-target.v1",
        "readiness_checksum": checksum_json(readiness_packet),
        "adapter_checksum": checksum_json(adapter_packet),
        "rollback_checksum": checksum_json(rollback_packet),
        "command": command,
        "cwd": str(Path(cwd).expanduser().resolve(strict=False)),
        "expected_result_path": str(Path(expected_result_path).expanduser().resolve(strict=False)),
        "environment_allowlist": list(environment_allowlist),
        "timeout_seconds": timeout_seconds,
        "audit_chain_head": audit_chain_head,
    }


def write_executor_readiness_role_packet(path, task_packet, valid=True, **overrides):
    data = {
        "protocol_version": "v1",
        "schema_version": "role-readiness.v1",
        "packet": "role_readiness",
        "read_only": True,
        "side_effects": [],
        "root": str(Path(path).parent),
        "checked_at": "2999-05-22T00:00:00Z",
        "valid": valid,
        "role_ready": valid,
        "recommended_next_action": "use_role_readiness" if valid else "provide_reviewer_evidence",
        "blockers": [] if valid else [{"code": "reviewer_evidence_missing", "message": "reviewer missing"}],
        "scope": {
            "repo": task_packet["repo"]["name"],
            "branch": task_packet["repo"]["branch"],
            "task_id": task_packet["task"]["id"],
        },
    }
    data.update(overrides)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return Path(path), data


def controlled_fixture_script() -> Path:
    return ROOT / "examples" / "controlled-executor-fixture" / "run.py"


def controlled_fixture_command(
    *,
    status="succeeded",
    exit_code=0,
    summary="Controlled fixture completed.",
    command="python -m unittest tests.test_cadence",
    executable=None,
    sleep_seconds=None,
) -> str:
    parts = [
        f'"{executable or sys.executable}"',
        f'"{controlled_fixture_script()}"',
        "--task-file",
        '"{task_file}"',
        "--result-file",
        '"{result_file}"',
        "--status",
        status,
        "--summary",
        f'"{summary}"',
        "--command",
        f'"{command}"',
        "--validation-name",
        "cadence-tests",
        "--validation-status",
        "passed" if status == "succeeded" else "failed",
        "--exit-code",
        str(exit_code),
        "--changed-file",
        "codex_cadence/executor_runner.py",
    ]
    if status != "succeeded":
        parts.extend(["--blocker", f'"{status} fixture evidence"'])
    if sleep_seconds is not None:
        parts.extend(["--sleep-seconds", str(sleep_seconds)])
    return " ".join(parts)


def real_executor_script(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
repo_path = Path(config["repo_path"])
if config.get("stdout_text"):
    sys.stdout.write(config["stdout_text"])
    sys.stdout.flush()
if config.get("stderr_text"):
    sys.stderr.write(config["stderr_text"])
    sys.stderr.flush()
if config.get("invalid_output"):
    sys.stdout.buffer.write(b"stdout invalid byte: \\xff\\n")
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(b"stderr invalid byte: \\xff\\n")
    sys.stderr.buffer.flush()
if config.get("sleep_seconds"):
    time.sleep(config["sleep_seconds"])
if config.get("touch_repo"):
    readme = repo_path / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "real executor change\\n", encoding="utf-8")
if config.get("create_branch"):
    subprocess.run(["git", "branch", config["create_branch"]], cwd=repo_path, check=True)
if config.get("delete_branch"):
    subprocess.run(["git", "branch", "-D", config["delete_branch"]], cwd=repo_path, check=True)
if config.get("retarget_branch"):
    branch = config["retarget_branch"]
    subprocess.run(["git", "branch", "-f", branch, "HEAD~1"], cwd=repo_path, check=True)
if config.get("delete_git"):
    import shutil

    shutil.rmtree(repo_path / ".git", ignore_errors=True)

if config.get("write_result", True):
    started_at = now()
    ended_at = now()
    configured_files_changed = config.get("files_changed")
    files_changed = (
        configured_files_changed
        if isinstance(configured_files_changed, list)
        else ["README.md"] if config.get("touch_repo") else []
    )
    commands_run = [
        {
            "command": config["command"],
            "exit_code": config.get("exit_code", 0),
        }
    ]
    validation_results = [
        {
            "name": "real-executor-script",
            "status": "passed",
            "command": config["command"],
        }
    ]
    for index, check in enumerate(config.get("required_checks") or [], start=1):
        if check == config["command"]:
            continue
        commands_run.append({"command": check, "exit_code": 0})
        validation_results.append({"name": f"required-check-{index}", "status": "passed", "command": check})
    result = {
        "schema_version": "generic-executor-result.v1",
        "packet": "executor_result",
        "task_id": config["task_id"],
        "executor_id": "unit-real-executor",
        "started_at": started_at,
        "ended_at": ended_at,
        "status": config.get("status", "succeeded"),
        "files_changed": files_changed,
        "commands_run": commands_run,
        "validation_results": validation_results,
        "summary": "Real executor invocation test result.",
        "confidence": "high",
        "blockers": [],
        "dirty_worktree": bool(config.get("touch_repo")),
        "resulting_head": config.get("resulting_head") or config["repo_head"],
    }
    if isinstance(config.get("materialized_change_evidence"), dict):
        result["materialized_change_evidence"] = config["materialized_change_evidence"]
    elif config.get("include_materialized_change_evidence"):
        result["materialized_change_evidence"] = {
            "status": "verified",
            "source": "real_executor_invocation.local_diff",
            "task_id": config["task_id"],
            "resulting_head": config["repo_head"],
            "files": files_changed,
            "limitations": ["verified_against_local_worktree_status"],
        }
    result_path = Path(config["result_path"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")

raise SystemExit(config.get("exit_code", 0))
""".lstrip(),
        encoding="utf-8",
    )
    return path


def command_quote(value) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def write_fake_gh(bin_dir: Path, script: Path) -> Path:
    """Create a fake gh executable that delegates to a Python script."""
    if os.name == "nt":
        fake_gh = bin_dir / "gh.cmd"
        fake_gh.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        fake_gh.chmod(0o755)
    return fake_gh


def write_fake_gh_script(path: Path) -> None:
    path.write_text(
        """
import os
import sys
from pathlib import Path

log = Path(os.environ["GH_CALL_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
fail_mode = os.environ.get("GH_FAIL_MODE")
if fail_mode == "auth":
    sys.stderr.write("not logged into GitHub")
    raise SystemExit(1)
if fail_mode == "rate":
    sys.stderr.write("API rate limit exceeded")
    raise SystemExit(1)
if fail_mode == "network":
    sys.stderr.write("failed to connect to github.com")
    raise SystemExit(1)
if args[:2] == ["pr", "view"]:
    sys.stdout.write(Path(os.environ["GH_PR_JSON"]).read_text(encoding="utf-8"))
    raise SystemExit(0)
if args[:2] == ["api", "graphql"]:
    sys.stdout.write(Path(os.environ["GH_REVIEW_THREADS_JSON"]).read_text(encoding="utf-8"))
    raise SystemExit(0)
sys.stderr.write("unexpected gh arguments: " + " ".join(args))
raise SystemExit(99)
""".lstrip(),
        encoding="utf-8",
    )


def unquoted_controlled_fixture_command(*, status="succeeded") -> str:
    return (
        f'"{sys.executable}" "{controlled_fixture_script()}" '
        "--task-file {task_file} "
        "--result-file {result_file} "
        f"--status {status} "
        "--summary 'fixture completed' "
        "--command 'python -m unittest tests.test_cadence' "
        "--validation-name cadence-tests "
        "--validation-status passed "
        "--exit-code 0 "
        "--changed-file codex_cadence/executor_runner.py"
    )


def write_controlled_fixture_task(root, repo, *, command_policy=None, evidence_name="executor-result.json"):
    evidence_path = Path(root) / evidence_name
    task_packet = build_executor_task_packet(
        task={
            "id": "candidate-1",
            "title": "Implement bounded executor task",
            "summary": "Create generic executor evidence.",
            "task_type": "execution",
            "bucket": "S",
            "source": "text_marker",
            "drivers": [],
            "evidence": {"path": "docs/roadmap.md"},
        },
        snapshot=valid_snapshot(cwd=str(repo)),
        repo_path=repo,
        allowed_paths=["codex_cadence", "tests"],
        required_checks=["python -m unittest tests.test_cadence"],
        max_minutes=1,
        max_tasks=1,
        stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
        evidence_path=evidence_path,
        allowed_commands=(command_policy or {}).get("allowed_commands"),
        denied_commands=(command_policy or {}).get("denied_commands"),
    )
    task_path = Path(root) / "executor-task.json"
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    return task_path, evidence_path, task_packet


def closeout_snapshot(repo, **overrides):
    snapshot = valid_snapshot(
        cwd=str(Path(repo).resolve()),
        branch=current_branch(repo),
        head=current_head(repo),
    )
    snapshot.update(overrides)
    return snapshot


def write_closeout_packets(root, repo, *, task_packet=None, result_evidence=None, snapshot_before=None):
    result_path = Path(root) / "executor-result.json"
    if snapshot_before is None:
        snapshot_before = closeout_snapshot(repo)
    if task_packet is None:
        task_packet = build_executor_task_packet(
            task={
                "id": "candidate-1",
                "title": "Implement epoch closeout",
                "summary": "Wire executor evidence into epoch closeout.",
                "task_type": "execution",
                "bucket": "S",
                "source": "text_marker",
                "drivers": [],
                "evidence": {"path": "docs/roadmap.md"},
            },
            snapshot=snapshot_before,
            repo_path=repo,
            allowed_paths=["README.md", "codex_cadence", "tests"],
            required_checks=[],
            max_minutes=30,
            max_tasks=1,
            stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
            evidence_path=result_path,
        )
    if result_evidence is None:
        task = task_packet.get("task") if isinstance(task_packet, dict) else None
        repo_info = task_packet.get("repo") if isinstance(task_packet, dict) else None
        task_id = task.get("id") if isinstance(task, dict) and isinstance(task.get("id"), str) else None
        if not task_id and isinstance(task_packet, dict) and isinstance(task_packet.get("task_id"), str):
            task_id = task_packet["task_id"]
        if not task_id:
            task_id = "candidate-1"
        resulting_head = None
        if isinstance(task_packet, dict) and isinstance(task_packet.get("resulting_head"), str):
            resulting_head = task_packet["resulting_head"]
        elif isinstance(repo_info, dict) and isinstance(repo_info.get("head"), str):
            resulting_head = repo_info["head"]
        elif isinstance(task_packet, dict) and isinstance(task_packet.get("head"), str):
            resulting_head = task_packet["head"]
        if not resulting_head:
            resulting_head = current_head(repo)
        result_evidence = {
            "schema_version": "generic-executor-result.v1",
            "packet": "executor_result",
            "task_id": task_id,
            "executor_id": "fake-executor",
            "started_at": "2999-05-22T00:00:00Z",
            "ended_at": "2999-05-22T00:05:00Z",
            "status": "succeeded",
            "files_changed": ["README.md"],
            "commands_run": [
                {
                    "command": "python -m unittest tests.test_cadence",
                    "exit_code": 0,
                }
            ],
            "validation_results": [
                {
                    "name": "cadence-tests",
                    "status": "passed",
                    "command": "python -m unittest tests.test_cadence",
                }
            ],
            "summary": "Closeout evidence is ready.",
            "confidence": "high",
            "blockers": [],
            "dirty_worktree": False,
            "resulting_head": resulting_head,
            "materialized_change_evidence": {
                "status": "verified",
                "source": "executor_result.materialized_change_evidence",
                "task_id": task_id,
                "resulting_head": resulting_head,
                "files": ["README.md"],
                "limitations": ["verified_against_result_metadata_not_local_diff"],
            },
        }
    task_path = Path(root) / "executor-task.json"
    snapshot_after_path = Path(root) / "snapshot-after.json"
    snapshot_after = closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    result_path.write_text(json.dumps(result_evidence), encoding="utf-8")
    snapshot_after_path.write_text(json.dumps(snapshot_after), encoding="utf-8")
    return task_path, result_path, snapshot_after_path, task_packet, result_evidence, snapshot_after


def write_execution_run_record(
    root,
    *,
    task_path,
    result_path,
    task_packet,
    result_evidence,
    validation,
    closeout_status="pending",
    repo_overrides=None,
    **overrides,
):
    run_id = overrides.pop("run_id", "execution-run-test-1")
    invocation_id = overrides.pop("invocation_id", "executor-fixture-invocation-test-1")
    bound_validation = dict(validation)
    bound_validation["invocation_id"] = invocation_id
    record = build_execution_run_record(
        run_id=run_id,
        invocation_id=invocation_id,
        task_file=task_path,
        result_file=result_path,
        task_packet=task_packet,
        result_evidence=result_evidence,
        validation_packet=bound_validation,
        closeout_status=closeout_status,
    )
    record["created_at"] = "2999-05-22T00:06:00Z"
    record["updated_at"] = "2999-05-22T00:06:00Z"
    if repo_overrides:
        repo = dict(record["repo"])
        repo.update(repo_overrides)
        record["repo"] = repo
    record.update(overrides)
    run_record_path = Path(root) / "execution-runs" / f"{record['run_id']}.json"
    run_record_path.parent.mkdir(parents=True, exist_ok=True)
    run_record_path.write_text(json.dumps(record), encoding="utf-8")
    return run_record_path, record


def write_closeout_run_record(root, *, task_path, result_path, task_packet, result_evidence, executor_started=False):
    validation = build_executor_result_validation_payload(
        root=Path(root),
        task_file=Path(task_path),
        result_file=Path(result_path),
        task_packet=task_packet,
        result_evidence=result_evidence,
        executor_started=executor_started,
        invocation_id="executor-fixture-invocation-test-1",
    )
    return write_execution_run_record(
        root,
        task_path=task_path,
        result_path=result_path,
        task_packet=task_packet,
        result_evidence=result_evidence,
        validation=validation,
    )


def write_executor_closeout_packet(root, repo, *, epoch_id="epoch-closeout-owned", result_evidence=None):
    task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
        root,
        repo,
        result_evidence=result_evidence,
    )
    write_active_epoch(
        root,
        epoch_id,
        task_packet["snapshot"],
        tasks=[task_packet["task"]],
    )
    run_record_path, _run_record = write_closeout_run_record(
        root,
        task_path=task_path,
        result_path=result_path,
        task_packet=task_packet,
        result_evidence=result_evidence,
    )
    result, output = run_cli(
        root,
        "closeout-executor-result",
        "--epoch-id",
        epoch_id,
        "--task-file",
        str(task_path),
        "--result-file",
        str(result_path),
        "--snapshot-after-file",
        str(snapshot_after_path),
        "--run-record-file",
        str(run_record_path),
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    closeout_path = Path(root) / f"{epoch_id}-executor-closeout.json"
    closeout_path.write_text(json.dumps(output), encoding="utf-8")
    return closeout_path, output, task_packet, result_evidence


def audit_records(root):
    audit_path = Path(root) / "audit" / "events.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def assert_blocked_run_record_closeout_preserved(test_case, root, epoch_id, run_record_path, before_record, before_audit_count, output):
    test_case.assertNotIn("execution_run_record_updated", output["side_effects"])
    test_case.assertNotIn("execution_run_audit_appended", output["side_effects"])
    if run_record_path is not None:
        test_case.assertEqual(
            checksum_json(json.loads(Path(run_record_path).read_text(encoding="utf-8"))),
            checksum_json(before_record),
        )
    test_case.assertTrue((Path(root) / "epochs" / "active" / f"{epoch_id}.json").exists())
    test_case.assertFalse((Path(root) / "epochs" / "completed" / f"{epoch_id}.json").exists())
    test_case.assertFalse((Path(root) / "epochs" / "failed" / f"{epoch_id}.json").exists())
    records = audit_records(root)
    test_case.assertEqual(len(records), before_audit_count + 1)
    test_case.assertEqual(records[-1]["event"], "executor_epoch_closeout")


def claimed_handoff_path(root, handoff_id):
    return Path(root) / "handoffs" / "claimed" / f"{handoff_id}.json"


class CadenceCliTests(unittest.TestCase):
    def test_write_closeout_packets_preserves_explicit_empty_overrides(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            task_path, result_path, _snapshot_after_path, task_packet, result_evidence, _snapshot_after = (
                write_closeout_packets(
                    tmp,
                    repo,
                    task_packet={},
                    result_evidence={},
                    snapshot_before={},
                )
            )

            self.assertEqual(task_packet, {})
            self.assertEqual(result_evidence, {})
            self.assertEqual(json.loads(task_path.read_text(encoding="utf-8")), {})
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), {})

    def test_write_closeout_packets_derives_default_result_from_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _task_path, _result_path, _snapshot_after_path, task_packet, _result_evidence, _snapshot_after = (
                write_closeout_packets(tmp, repo)
            )
            task_packet["task"]["id"] = "candidate-custom"
            task_packet["repo"]["head"] = "f" * 40

            _task_path, result_path, _snapshot_after_path, _task_packet, result_evidence, _snapshot_after = (
                write_closeout_packets(
                    tmp,
                    repo,
                    task_packet=task_packet,
                )
            )

            self.assertEqual(result_evidence["task_id"], "candidate-custom")
            self.assertEqual(result_evidence["resulting_head"], "f" * 40)
            self.assertEqual(result_evidence["materialized_change_evidence"]["task_id"], "candidate-custom")
            self.assertEqual(result_evidence["materialized_change_evidence"]["resulting_head"], "f" * 40)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result_evidence)

    def test_write_closeout_packets_preserves_explicit_empty_snapshot_override(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            task_path, result_path, _snapshot_after_path, task_packet, result_evidence, _snapshot_after = (
                write_closeout_packets(
                    tmp,
                    repo,
                    result_evidence={},
                    snapshot_before={},
                )
            )

            self.assertEqual(task_packet["snapshot"], {})
            self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["snapshot"], {})
            self.assertEqual(result_evidence, {})
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), {})

    def test_default_root_uses_cadence_runtime_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                self.assertEqual(default_root(), Path(tmp) / ".codex" / "cadence")

    def test_legacy_transmission_root_env_still_overrides_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODEX_TRANSMISSION_ROOT": tmp}, clear=True):
                self.assertEqual(default_root(), Path(tmp))

    def test_existing_legacy_default_root_preserves_brake_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            legacy_root.mkdir(parents=True)
            (legacy_root / "brake.json").write_text(
                json.dumps({
                    "status": "PARK",
                    "reason": "operator pause",
                    "scope": "global",
                    "resume_requires": "manual_ack",
                    "updated_at": "2026-05-22T00:00:00Z",
                }),
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                self.assertEqual(default_root(), legacy_root)

    def test_cadence_root_env_overrides_existing_legacy_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)

            with mock.patch.dict(
                os.environ,
                {"HOME": tmp, "USERPROFILE": tmp, "CODEX_CADENCE_ROOT": str(cadence_root)},
                clear=True,
            ):
                self.assertEqual(default_root(), cadence_root)

    def test_conflicting_root_env_vars_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cadence_root = Path(tmp) / "cadence"
            legacy_root = Path(tmp) / "transmission"

            with mock.patch.dict(
                os.environ,
                {"CODEX_CADENCE_ROOT": str(cadence_root), "CODEX_TRANSMISSION_ROOT": str(legacy_root)},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "point to different roots"):
                    default_root()

    def test_matching_root_env_vars_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "same"

            with mock.patch.dict(
                os.environ,
                {"CODEX_CADENCE_ROOT": str(root), "CODEX_TRANSMISSION_ROOT": str(root)},
                clear=True,
            ):
                self.assertEqual(default_root(), root)

    def test_both_default_roots_fail_closed_without_explicit_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "both default runtime roots exist"):
                    default_root()

    def test_explicit_cli_root_works_when_both_default_roots_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            explicit_root = home / "explicit"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--root",
                        str(explicit_root),
                        "status",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(Path(output["root"]), explicit_root.resolve())

    def test_cli_fails_closed_when_both_default_roots_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "status"],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("both default runtime roots exist", result.stderr)

    def test_status_output_shape_remains_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "status")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(output.keys()), {"root", "brake", "cadence", "counts", "next_ready"})
            self.assertEqual(output["counts"], {
                "ready": 0,
                "claimed": 0,
                "completed": 0,
                "failed": 0,
            })
            self.assertEqual(output["cadence"], {
                "state": "PLAY_ON",
                "legacy_brake": "DRIVE",
                "can_start_work": True,
                "requires_operator_resume": False,
            })

    def test_status_maps_brake_to_football_cadence_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _brake = run_cli(tmp, "set-brake", "NEUTRAL", "--reason", "operator huddle")
            self.assertEqual(result.returncode, 0, result.stderr)
            result, output = run_cli(tmp, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["cadence"], {
                "state": "HUDDLE",
                "legacy_brake": "NEUTRAL",
                "can_start_work": False,
                "requires_operator_resume": False,
            })

            result, _brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator timeout")
            self.assertEqual(result.returncode, 0, result.stderr)
            result, output = run_cli(tmp, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["cadence"], {
                "state": "TIMEOUT",
                "legacy_brake": "PARK",
                "can_start_work": False,
                "requires_operator_resume": True,
            })

    def test_init_creates_drive_brake_and_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "init")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["brake"]["status"], "DRIVE")
            self.assertTrue((Path(tmp) / "brake.json").exists())
            self.assertTrue((Path(tmp) / "handoffs" / "ready").is_dir())

    def test_handoff_claim_and_complete_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "handoff-1",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["status"], "READY")
            self.assertIn("codex-handoff:v1", created["signature"])

            result, claimed = run_cli(tmp, "claim-handoff", "handoff-1", "--claimer", "test-agent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(claimed["status"], "CLAIMED")
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "handoff-1.json").exists())

            result, completed = run_cli(tmp, "complete-handoff", "handoff-1", "--summary", "done")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertTrue((Path(tmp) / "handoffs" / "completed" / "handoff-1.json").exists())

    def test_long_valid_handoff_id_can_move_through_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            handoff_id = "h" * 128
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                handoff_id,
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["id"], handoff_id)

            result, claimed = run_cli(tmp, "claim-handoff", handoff_id, "--claimer", "test-agent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(claimed["status"], "CLAIMED")

            result, completed = run_cli(tmp, "complete-handoff", handoff_id, "--summary", "done")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")

    def test_claim_write_failure_leaves_handoff_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "handoff-failure",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            ready_path = ready_handoff_path(tmp, "handoff-failure")
            claimed_path = claimed_handoff_path(tmp, "handoff-failure")

            def fail_claimed_write(path, data):
                if path == claimed_path:
                    raise OSError("write failed")
                raise AssertionError("unexpected write")

            with mock.patch("codex_cadence.cli.atomic_write_json", fail_claimed_write):
                with self.assertRaisesRegex(OSError, "write failed"):
                    import codex_cadence.cli as cadence

                    args = type(
                        "Args",
                        (),
                        {"root": Path(tmp), "handoff_id": "handoff-failure", "claimer": "test-agent"},
                    )()
                    cadence.claim_handoff(args)

            self.assertTrue(ready_path.exists())
            self.assertFalse(claimed_path.exists())
            self.assertEqual(json.loads(ready_path.read_text(encoding="utf-8"))["status"], "READY")

    def test_claim_handoff_uses_runtime_lock_for_brake_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "runtime-locked",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            with exclusive_lock(lock_path(Path(tmp), "runtime")):
                result, output = run_cli(tmp, "claim-handoff", "runtime-locked", "--claimer", "test-agent")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("lock already held: runtime.lock", result.stderr)
            self.assertTrue(ready_handoff_path(tmp, "runtime-locked").exists())

    def test_claim_blocks_unsized_handoff_without_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "unsized-stripped-1",
                "--title",
                "Stripped estimate",
                "--task-type",
                "execution",
                "--message",
                "Estimate fields were stripped after creation.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rewrite_ready_handoff(
                tmp,
                "unsized-stripped-1",
                lambda data: (data.pop("estimate"), data.pop("estimate_input"), data.pop("estimate_checksum")),
            )

            result, output = run_cli(tmp, "claim-handoff", "unsized-stripped-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "handoff estimate is required for pickup")
            self.assertTrue(ready_handoff_path(tmp, "unsized-stripped-1").exists())

    def test_create_handoff_requires_task_type_for_new_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "unsized-1",
                "--title",
                "Unsized",
                "--message",
                "This handoff has no task sizing.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--task-type", result.stderr)
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "unsized-1.json").exists())

    def test_create_handoff_rejects_path_traversal_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "create-handoff",
                "--id",
                r"..\escaped",
                "--title",
                "Escaped",
                "--task-type",
                "execution",
                "--message",
                "Do not escape state directories.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("handoff id", result.stderr)
            self.assertFalse((Path(tmp) / "handoffs" / "escaped.json").exists())

    def test_claim_blocks_estimated_handoff_missing_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "missing-estimate-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "missing-estimate-1", lambda data: data.pop("estimate"))

            result, output = run_cli(tmp, "claim-handoff", "missing-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "handoff estimate is required for pickup")
            self.assertTrue(path.exists())

    def test_claim_blocks_pre_binding_estimated_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "pre-binding-estimate-1",
                "--title",
                "Small execution",
                "--task-type",
                "execution",
                "--driver",
                "reviewer_feedback",
                "--message",
                "Resolve one reviewer finding.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def remove_binding(data):
                data.pop("estimate_input")
                data.pop("estimate_checksum")

            path = rewrite_ready_handoff(tmp, "pre-binding-estimate-1", remove_binding)

            result, output = run_cli(tmp, "claim-handoff", "pre-binding-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "estimate input must be an object")
            self.assertTrue(path.exists())

    def test_brake_blocks_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "blocked-1",
                "--title",
                "Blocked",
                "--task-type",
                "execution",
                "--message",
                "Do not pick this up.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, brake = run_cli(tmp, "set-brake", "NEUTRAL", "--reason", "manual brake")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "NEUTRAL")

            result, claim = run_cli(tmp, "claim-handoff", "blocked-1", "--claimer", "test-agent")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(claim["claimed"])
            self.assertEqual(claim["blocked_by_brake"]["status"], "NEUTRAL")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "blocked-1.json").exists())

    def test_set_brake_cannot_clear_to_drive_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(tmp, "set-brake", "DRIVE", "--reason", "resume")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("invalid choice", result.stderr)

    def test_clear_brake_returns_to_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, cleared = run_cli(tmp, "clear-brake", "--reason", "operator approved resume")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cleared["status"], "DRIVE")
            self.assertEqual(cleared["reason"], "operator approved resume")

    def test_move_handoff_unlink_failure_rolls_back_target_state(self):
        from codex_cadence.cli import move_handoff

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_cli(root, "init")
            handoff_id = "handoff-rollback"
            ready_path = ready_handoff_path(root, handoff_id)
            ready_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "v1",
                        "id": handoff_id,
                        "status": "READY",
                        "checksum": "sha256:abc",
                        "signature": "sig",
                        "message": "test",
                    }
                ),
                encoding="utf-8",
            )
            claimed_path = claimed_handoff_path(root, handoff_id)
            original_unlink = Path.unlink

            def fail_ready_unlink(path):
                if path == ready_path:
                    raise OSError("unlink failed")
                return original_unlink(path)

            with mock.patch("pathlib.Path.unlink", fail_ready_unlink):
                with self.assertRaisesRegex(OSError, "unlink failed"):
                    move_handoff(root, handoff_id, "ready", "claimed", {"claimed_by": "tester"})

            self.assertTrue(ready_path.exists())
            self.assertFalse(claimed_path.exists())

    def test_claim_blocks_xl_handoff_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "xl-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "xl-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "decompose_or_ask_approval")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "xl-1.json").exists())

    def test_claim_blocks_xl_handoff_with_missing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "missing-policy-1",
                "--title",
                "Large execution",
                "--task-type",
                "execution",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "irreversible_operation",
                "--message",
                "Execute a large change.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "missing-policy-1", lambda data: data["estimate"].pop("policy"))

            result, output = run_cli(tmp, "claim-handoff", "missing-policy-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_blocks_internally_inconsistent_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "spoofed-estimate-1",
                "--title",
                "Large execution",
                "--task-type",
                "execution",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "irreversible_operation",
                "--message",
                "Execute a large change.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def spoof(data):
                data["estimate"]["bucket"] = "S"
                data["estimate"]["policy"] = {
                    "action": "pick_up",
                    "check_in_every_minutes": 10,
                    "handoff_after_minutes": 25,
                    "pickup_requires_approval": False,
                }

            path = rewrite_ready_handoff(tmp, "spoofed-estimate-1", spoof)

            result, output = run_cli(tmp, "claim-handoff", "spoofed-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertIn("canonical", output["blocked_by_policy"]["reason"])
            self.assertTrue(path.exists())

    def test_claim_blocks_self_consistent_downgraded_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "downgraded-estimate-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def downgrade(data):
                source = {"task_type": "execution", "drivers": ["reviewer_feedback"]}
                data["estimate_input"] = source
                data["estimate"] = estimate_task(
                    title=data["title"],
                    message=data["message"],
                    task_type=source["task_type"],
                    drivers=source["drivers"],
                )

            path = rewrite_ready_handoff(tmp, "downgraded-estimate-1", downgrade)

            result, output = run_cli(tmp, "claim-handoff", "downgraded-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "estimate checksum mismatch")
            self.assertTrue(path.exists())

    def test_claim_blocks_non_dict_estimate_predictably(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-estimate-1",
                "--title",
                "Bad estimate",
                "--task-type",
                "execution",
                "--message",
                "Persisted estimate is corrupt.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "bad-estimate-1", lambda data: data.update({"estimate": "bad"}))

            result, output = run_cli(tmp, "claim-handoff", "bad-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_blocks_non_dict_estimate_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-policy-1",
                "--title",
                "Bad policy",
                "--task-type",
                "execution",
                "--message",
                "Persisted policy is corrupt.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "bad-policy-1", lambda data: data["estimate"].update({"policy": ["bad"]}))

            result, output = run_cli(tmp, "claim-handoff", "bad-policy-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_allows_xl_handoff_with_operator_approval_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "xl-approved-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, approval = run_cli(tmp, "approve-handoff", "xl-approved-1", "--approver", "operator")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(approval["handoff_id"], "xl-approved-1")
            self.assertTrue((Path(tmp) / "approvals" / "xl-approved-1.json").exists())

            result, output = run_cli(tmp, "claim-handoff", "xl-approved-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["status"], "CLAIMED")

    def test_claim_blocks_approval_gated_handoff_with_self_attested_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-metadata-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--metadata",
                "pickup_approved=true",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "bad-metadata-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertTrue(output["blocked_by_policy"]["pickup_requires_approval"])
            self.assertEqual(output["blocked_by_policy"]["action"], "decompose_or_ask_approval")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "bad-metadata-1.json").exists())

    def test_claim_blocks_self_evolution_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "self-evolution-execution-1",
                "--title",
                "Rewrite protocol",
                "--task-type",
                "execution",
                "--driver",
                "self_evolution",
                "--message",
                "Modify the protocol rules directly.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "self-evolution-execution-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "self_evolution_propose_only")

    def test_claim_blocks_high_uncertainty_discovery_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "uncertain-discovery-1",
                "--title",
                "Unknown area discovery",
                "--task-type",
                "discovery",
                "--driver",
                "unknown_repo_area",
                "--driver",
                "cross_subsystem",
                "--message",
                "Investigate the unfamiliar subsystem.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["estimate"]["uncertainty"]["level"], "high")

            result, output = run_cli(tmp, "claim-handoff", "uncertain-discovery-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertTrue(output["blocked_by_policy"]["pickup_requires_approval"])
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "uncertain-discovery-1.json").exists())

    def test_validate_detects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "checksum-1",
                "--title",
                "Checksum",
                "--task-type",
                "execution",
                "--message",
                "Original.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(tmp) / "handoffs" / "ready" / "checksum-1.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["message"] = "Tampered."
            path.write_text(json.dumps(data), encoding="utf-8")

            result, validation = run_cli(tmp, "validate-handoff", "checksum-1")
            self.assertEqual(result.returncode, 1)
            self.assertFalse(validation["valid"])
            self.assertIn("checksum mismatch", validation["errors"])

    def test_clean_square_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "clean-1",
                "--title",
                "Clean",
                "--task-type",
                "execution",
                "--message",
                "Ready for pickup.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, log = run_cli(tmp, "clean-square", "clean-1", "--summary", "old session stopped")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(log["checks"]["handoff_written"])
            self.assertTrue((Path(tmp) / "logs" / "clean-square" / "clean-1.json").exists())

    def test_plan_task_outputs_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "plan-task",
                "--title",
                "Fix CI",
                "--task-type",
                "execution",
                "--driver",
                "ci_verification",
                "--message",
                "Resolve one failing test.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["title"], "Fix CI")
            self.assertEqual(output["estimate"]["task_type"], "execution")
            self.assertIn(output["estimate"]["bucket"], ["S", "M"])

    def test_create_handoff_can_embed_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "estimated-1",
                "--title",
                "Investigate architecture",
                "--task-type",
                "discovery",
                "--driver",
                "unknown_repo_area",
                "--driver",
                "cross_subsystem",
                "--message",
                "Investigate why two subsystems disagree.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["estimate"]["task_type"], "discovery")
            self.assertEqual(created["estimate_input"]["task_type"], "discovery")
            self.assertIsNotNone(created["estimate_checksum"])
            self.assertTrue(created["estimate"]["policy"]["pickup_requires_approval"])

    def test_snapshot_repo_command_writes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, snapshot = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(snapshot["repo"], "local/test")
            self.assertEqual(snapshot["repo_confidence"], "high")
            self.assertEqual(snapshot["path"], str(Path(tmp) / "snapshots" / f"{snapshot['id']}.json"))
            self.assertTrue((Path(tmp) / "snapshots" / f"{snapshot['id']}.json").exists())

    def test_snapshot_repo_command_repeated_calls_write_distinct_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            first_result, first = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")
            second_result, second = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertNotEqual(first["id"], second["id"])
            self.assertTrue((Path(tmp) / "snapshots" / f"{first['id']}.json").exists())
            self.assertTrue((Path(tmp) / "snapshots" / f"{second['id']}.json").exists())
            self.assertEqual(len(list((Path(tmp) / "snapshots").glob("*.json"))), 2)

    def test_snapshot_repo_command_invalid_cwd_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            result, output = run_cli(tmp, "snapshot-repo", "--cwd", str(missing), "--repo", "local/test")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_snapshot_repo_command_records_explicit_ci_status(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, output = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test", "--ci-status", "green")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["ci"], "green")

    def test_snapshot_repo_rejects_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_snapshot_repo_allows_ignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            (repo / ".gitignore").write_text(".cadence-runtime/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore cadence runtime")
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_snapshot_repo_allows_parent_ignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            (repo / ".gitignore").write_text(".codex/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore codex runtime")
            runtime_root = repo / ".codex" / "cadence"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_snapshot_repo_allows_explicit_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(
                runtime_root,
                "--allow-repo-local-root",
                "snapshot-repo",
                "--cwd",
                repo,
                "--repo",
                "local/test",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_prepare_handoff_rejects_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(
                runtime_root,
                "prepare-handoff",
                "--title",
                "Context handoff",
                "--guardrail",
                "manual",
                "--cwd",
                repo,
                "--task-type",
                "execution",
                "--summary",
                "Summarize current progress.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_status_rejects_unignored_repo_local_runtime_root_from_current_repo(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli_from(repo, runtime_root, "status")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_plan_task_does_not_guard_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli_from(
                repo,
                runtime_root,
                "plan-task",
                "--title",
                "Plan a small task",
                "--task-type",
                "execution",
                "--message",
                "Implement a small task.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["title"], "Plan a small task")
            self.assertFalse(runtime_root.exists())

    def test_discover_candidates_does_not_guard_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "discover-candidates", "--cwd", repo)

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("intent is required unless --interactive or --discovery-mode off is used", result.stderr)
            self.assertNotIn("runtime root is inside target repo", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_discover_candidates_requires_intent_for_local_mode(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intent is required unless --interactive or --discovery-mode off is used", result.stderr)

    def test_discover_candidates_expanded_mode_fails_closed_without_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo, "--discovery-mode", "expanded")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("expanded discovery mode is reserved for v2", result.stderr)
            self.assertNotIn("intent is required", result.stderr)

    def test_discover_candidates_expanded_mode_does_not_prompt_interactive_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(tmp),
                    "discover-candidates",
                    "--cwd",
                    repo,
                    "--discovery-mode",
                    "expanded",
                    "--interactive",
                ],
                input="1\n",
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expanded discovery mode is reserved for v2", result.stderr)
            self.assertNotIn("Choose discovery intent", result.stderr)

    def test_discover_candidates_off_does_not_require_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo, "--discovery-mode", "off")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["candidates"], [])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["sources"]["review_findings"], 0)
            self.assertEqual(output["sources"]["text_markers"], 0)
            self.assertEqual(output["sources"]["proposals"], 0)

    def test_discover_candidates_elects_known_failure(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "discover-candidates",
                "--cwd",
                repo,
                "--intent",
                "merge_readiness",
                "--known-failure",
                "unit tests",
                "--elect",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["elected_next"][0]["source"], "known_failure")

    def test_discover_candidates_accepts_pr_json_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            pr_json = Path(tmp) / "pr.json"
            pr_json.write_text(
                json.dumps(
                    {
                        "number": 67,
                        "statusCheckRollup": [
                            {
                                "__typename": "CheckRun",
                                "name": "Python and protocol checks",
                                "status": "COMPLETED",
                                "conclusion": "FAILURE",
                                "workflowName": "PR Checks",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "discover-candidates",
                "--cwd",
                repo,
                "--intent",
                "merge_readiness",
                "--pr-json-file",
                str(pr_json),
                "--elect",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["sources"]["pr_check_failures"], 1)
            self.assertEqual(output["elected_next"][0]["source"], "pr_check_failure")

    def test_discover_candidates_accepts_review_threads_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            threads = Path(tmp) / "threads.json"
            threads.write_text(
                json.dumps({
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {
                                            "id": "thread-1",
                                            "isResolved": False,
                                            "isOutdated": False,
                                            "path": "codex_cadence/candidates.py",
                                            "line": 448,
                                            "comments": {
                                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                                "nodes": [
                                                    {
                                                        "id": "comment-1",
                                                        "body": "Map this thread to a finding.",
                                                        "outdated": False,
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "discover-candidates",
                "--cwd",
                repo,
                "--intent",
                "merge_readiness",
                "--review-threads-file",
                str(threads),
                "--elect",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["sources"]["review_findings"], 1)
            self.assertEqual(output["elected_next"][0]["source"], "review_finding")

    def test_post_write_pr_evidence_gate_cli_reads_materialization_and_sync_summary(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            root = Path(tmp) / "runtime"
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            pr = {
                "number": 330,
                "title": "[codex] Harden post-write gate",
                "state": "OPEN",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "",
                "body": "## Summary\nReady.\n\n## Testing\n- unit tests\n",
                "headRefName": "codex/post-write-gate",
                "baseRefName": "main",
                "headRefOid": "abc123",
                "statusCheckRollup": [
                    {
                        "__typename": "CheckRun",
                        "name": "Python and protocol checks",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                        "workflowName": "PR Checks",
                    }
                ],
            }
            threads = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": 330,
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            }
            captured_at = "2026-06-11T10:10:00Z"
            pr_payload = dict(pr)
            pr_payload["github_evidence"] = {"source": "gh_pr_view", "captured_at": captured_at}
            threads_payload = dict(threads)
            threads_payload["github_evidence"] = {"source": "gh_graphql_review_threads", "captured_at": captured_at}
            pr_path = evidence_dir / "pr-330.json"
            threads_path = evidence_dir / "pr-330-review-threads.json"
            sync_path = evidence_dir / "pr-330-github-evidence.json"
            pr_path.write_text(json.dumps(pr_payload), encoding="utf-8")
            threads_path.write_text(json.dumps(threads_payload), encoding="utf-8")
            sync_packet = {
                "protocol_version": "cadence.v1",
                "schema_version": "github-evidence-sync.v1",
                "packet": "github_evidence_sync",
                "valid": True,
                "decision": "saved",
                "recommended_next_action": "use_saved_github_evidence",
                "repo": "Chef-Code/agentic-cadence",
                "pr_number": 330,
                "out_dir": str(evidence_dir),
                "captured_at": captured_at,
                "evidence": {"source": "github_live_readonly", "captured_at": captured_at},
                "files": {
                    "pr_json": str(pr_path),
                    "review_threads_json": str(threads_path),
                    "summary_json": str(sync_path),
                },
                "pr": {
                    "number": 330,
                    "title": pr["title"],
                    "state": "OPEN",
                    "head_ref": pr["headRefName"],
                    "base_ref": pr["baseRefName"],
                    "head_sha": pr["headRefOid"],
                },
                "blockers": [],
                "warnings": [],
                "side_effects": ["wrote_pr_json", "wrote_review_threads_json", "wrote_evidence_summary"],
                "github_write_started": False,
                "command_trace": [],
            }
            sync_path.write_text(json.dumps(sync_packet), encoding="utf-8")
            materialization_path = Path(tmp) / "review-response-materialization.json"
            materialization_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "cadence.v1",
                        "schema_version": "review-response-materialization.v1",
                        "packet": "review_response_materialization",
                        "generated_at": "2026-06-11T10:05:00Z",
                        "valid": True,
                        "decision": "materialized",
                        "approval_state": "approved",
                        "github_write_started": True,
                        "plan_checksum": "sha256:plan",
                        "target_checksum": "sha256:target",
                        "pr": {
                            "number": "330",
                            "head_ref": pr["headRefName"],
                            "base_ref": pr["baseRefName"],
                            "head_sha": pr["headRefOid"],
                            "url": "https://github.com/Chef-Code/agentic-cadence/pull/330",
                        },
                        "side_effects": ["updated_pr_body"],
                        "github_writes": [{"kind": "update_pr_body"}],
                        "blockers": [],
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                root,
                "post-write-pr-evidence-gate",
                "--cwd",
                repo,
                "--materialization-file",
                str(materialization_path),
                "--github-evidence-file",
                str(sync_path),
                "--required-check",
                "Python and protocol checks",
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Testing",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "post-write-pr-evidence-gate.v1")
            self.assertEqual(output["recommended_next_action"], "ready_for_review")
            self.assertTrue(output["ready_for_review"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["materialization"]["pr_number"], "330")
            self.assertEqual(output["refresh"]["head_sha"], "abc123")
            self.assertEqual(output["follow_up_candidates"], [])

    def test_github_evidence_sync_writes_read_only_evidence_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            out_dir = Path(tmp) / "evidence"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_script = Path(tmp) / "fake_gh.py"
            pr_json = Path(tmp) / "pr-source.json"
            review_threads_json = Path(tmp) / "threads-source.json"
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_script(fake_script)
            write_fake_gh(fake_bin, fake_script)
            pr_json.write_text(
                json.dumps(
                    {
                        "number": 67,
                        "url": "https://github.com/Chef-Code/agentic-cadence/pull/67",
                        "title": "[codex] Add local branch policy gates",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "",
                        "body": "## Summary\nReady.\n\n## Validation\nTests.\n",
                        "headRefName": "codex/task-4-branch-policy",
                        "baseRefName": "main",
                        "headRefOid": "abc123",
                        "statusCheckRollup": [
                            {
                                "__typename": "CheckRun",
                                "name": "Python and protocol checks",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                                "workflowName": "PR Checks",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            review_threads_json.write_text(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [
                                            {
                                                "id": "thread-1",
                                                "isResolved": False,
                                                "isOutdated": False,
                                                "path": "codex_cadence/cli.py",
                                                "line": 120,
                                                "comments": {
                                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                                    "nodes": [
                                                        {
                                                            "id": "comment-1",
                                                            "body": "Handle this actionable review finding.",
                                                            "outdated": False,
                                                            "author": {"login": "coderabbitai"},
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_PR_JSON"] = str(pr_json)
            env["GH_REVIEW_THREADS_JSON"] = str(review_threads_json)
            env["GH_CALL_LOG"] = str(gh_log)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "Chef-Code/agentic-cadence",
                    "--pr-number",
                    "67",
                    "--out-dir",
                    str(out_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output["valid"])
            self.assertEqual(output["schema_version"], "github-evidence-sync.v1")
            self.assertEqual(output["packet"], "github_evidence_sync")
            self.assertFalse(output["github_write_started"])
            self.assertEqual(output["side_effects"], ["wrote_pr_json", "wrote_review_threads_json", "wrote_evidence_summary"])
            self.assertIn("read_only_gh", output["evidence"]["limitations"])
            self.assertTrue(Path(output["files"]["pr_json"]).exists())
            self.assertTrue(Path(output["files"]["review_threads_json"]).exists())
            saved_pr = json.loads(Path(output["files"]["pr_json"]).read_text(encoding="utf-8"))
            saved_threads = json.loads(Path(output["files"]["review_threads_json"]).read_text(encoding="utf-8"))
            self.assertEqual(saved_pr["number"], 67)
            self.assertEqual(saved_pr["github_evidence"]["freshness"], "live")
            self.assertEqual(saved_threads["github_evidence"]["freshness"], "live")
            saved_pull_request = saved_threads["data"]["repository"]["pullRequest"]
            self.assertEqual(saved_pull_request["number"], 67)
            self.assertEqual(saved_pull_request["url"], "https://github.com/Chef-Code/agentic-cadence/pull/67")
            gh_calls = gh_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(gh_calls), 2)
            self.assertTrue(gh_calls[0].startswith("pr view 67 "))
            self.assertTrue(gh_calls[1].startswith("api graphql "))
            self.assertNotIn("pr merge", "\n".join(gh_calls))
            self.assertNotIn("pr edit", "\n".join(gh_calls))

    def test_github_evidence_sync_paginates_review_threads_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            pr_payload = {
                "number": 68,
                "title": "Task 5",
                "state": "OPEN",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "",
                "body": "",
                "headRefName": "codex/task-5-github-evidence-sync",
                "baseRefName": "main",
                "headRefOid": "abc123",
                "statusCheckRollup": [],
            }
            review_page_1 = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "thread-cursor-1"},
                                "nodes": [
                                    {
                                        "id": "thread-1",
                                        "isResolved": False,
                                        "isOutdated": False,
                                        "path": "codex_cadence/github_evidence.py",
                                        "line": 45,
                                        "comments": {
                                            "pageInfo": {"hasNextPage": True, "endCursor": "comment-cursor-1"},
                                            "nodes": [
                                                {
                                                    "id": "comment-1",
                                                    "body": "First actionable comment.",
                                                    "path": "codex_cadence/github_evidence.py",
                                                    "line": 45,
                                                    "outdated": False,
                                                    "author": {"login": "coderabbitai"},
                                                }
                                            ],
                                        },
                                    }
                                ],
                            }
                        }
                    }
                }
            }
            comment_page_2 = {
                "data": {
                    "node": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "body": "Second actionable comment.",
                                    "path": "codex_cadence/github_evidence.py",
                                    "line": 46,
                                    "outdated": False,
                                    "author": {"login": "coderabbitai"},
                                }
                            ],
                        }
                    }
                }
            }
            review_page_2 = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "id": "thread-2",
                                        "isResolved": True,
                                        "isOutdated": False,
                                        "path": "docs/session-handoff.md",
                                        "line": 12,
                                        "comments": {
                                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                                            "nodes": [],
                                        },
                                    }
                                ],
                            }
                        }
                    }
                }
            }

            def completed(payload):
                return subprocess.CompletedProcess(["gh"], 0, stdout=json.dumps(payload), stderr="")

            with mock.patch(
                "codex_cadence.github_evidence.subprocess.run",
                side_effect=[
                    completed(pr_payload),
                    completed(review_page_1),
                    completed(comment_page_2),
                    completed(review_page_2),
                ],
            ) as run_mock:
                output = github_evidence.sync_github_evidence(
                    repo="Chef-Code/agentic-cadence",
                    pr_number=68,
                    out_dir=out_dir,
                    gh_bin="gh",
                )

            self.assertTrue(output["valid"])
            saved_threads = json.loads(Path(output["files"]["review_threads_json"]).read_text(encoding="utf-8"))
            review_threads = saved_threads["data"]["repository"]["pullRequest"]["reviewThreads"]
            self.assertEqual(review_threads["pageInfo"], {"hasNextPage": False, "endCursor": None})
            self.assertEqual([thread["id"] for thread in review_threads["nodes"]], ["thread-1", "thread-2"])
            thread_1_comments = review_threads["nodes"][0]["comments"]
            self.assertEqual(thread_1_comments["pageInfo"], {"hasNextPage": False, "endCursor": None})
            self.assertEqual([comment["id"] for comment in thread_1_comments["nodes"]], ["comment-1", "comment-2"])
            calls = [" ".join(str(part) for part in call.args[0]) for call in run_mock.call_args_list]
            self.assertEqual(len(calls), 4)
            self.assertTrue(any("threadId=thread-1" in call for call in calls))
            self.assertTrue(any("commentsCursor=comment-cursor-1" in call for call in calls))
            self.assertTrue(any("threadsCursor=thread-cursor-1" in call for call in calls))

    def test_github_evidence_sync_timeout_returns_blocker_without_files(self):
        pr_payload = {
            "number": 68,
            "title": "Task 5",
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "body": "",
            "headRefName": "codex/task-5-github-evidence-sync",
            "baseRefName": "main",
            "headRefOid": "abc123",
            "statusCheckRollup": [],
        }
        cases = {
            "pr": [subprocess.TimeoutExpired(cmd=["gh", "pr", "view"], timeout=60)],
            "graphql": [
                subprocess.CompletedProcess(["gh"], 0, stdout=json.dumps(pr_payload), stderr=""),
                subprocess.TimeoutExpired(cmd=["gh", "api", "graphql"], timeout=60),
            ],
        }
        for name, side_effect in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    out_dir = Path(tmp) / "evidence"
                    with mock.patch("codex_cadence.github_evidence.subprocess.run", side_effect=side_effect):
                        output = github_evidence.sync_github_evidence(
                            repo="Chef-Code/agentic-cadence",
                            pr_number=68,
                            out_dir=out_dir,
                            gh_bin="gh",
                        )

                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], "retry_github_evidence_sync")
                    self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"gh_command_timeout"})
                    self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_spawn_failure_returns_blocker_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            with mock.patch(
                "codex_cadence.github_evidence.subprocess.run",
                side_effect=OSError("cannot execute gh"),
            ):
                output = github_evidence.sync_github_evidence(
                    repo="Chef-Code/agentic-cadence",
                    pr_number=68,
                    out_dir=out_dir,
                    gh_bin="gh",
                )

            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "install_or_authenticate_gh")
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"gh_spawn_failed"})
            self.assertFalse(out_dir.exists())

    def test_cli_github_evidence_sync_rejects_repo_local_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_committed_repo(repo)
            out_dir = repo / "evidence"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_script = Path(tmp) / "fake_gh.py"
            pr_json = Path(tmp) / "pr-source.json"
            review_threads_json = Path(tmp) / "threads-source.json"
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_script(fake_script)
            write_fake_gh(fake_bin, fake_script)
            pr_json.write_text(
                json.dumps(
                    {
                        "number": 68,
                        "title": "Task 5",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "",
                        "body": "",
                        "headRefName": "codex/task-5-github-evidence-sync",
                        "baseRefName": "main",
                        "headRefOid": "abc123",
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            review_threads_json.write_text(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [],
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_PR_JSON"] = str(pr_json)
            env["GH_REVIEW_THREADS_JSON"] = str(review_threads_json)
            env["GH_CALL_LOG"] = str(gh_log)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "Chef-Code/agentic-cadence",
                    "--pr-number",
                    "68",
                    "--out-dir",
                    str(out_dir),
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("github evidence out-dir is inside a git worktree", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_missing_gh_returns_blocker_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            out_dir = Path(tmp) / "evidence"
            empty_bin = Path(tmp) / "empty-bin"
            empty_bin.mkdir()
            env = os.environ.copy()
            env["PATH"] = str(empty_bin)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "Chef-Code/agentic-cadence",
                    "--pr-number",
                    "67",
                    "--out-dir",
                    str(out_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            output = json.loads(result.stdout)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "install_or_authenticate_gh")
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"gh_missing"})
            self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_malformed_repo_returns_blocker_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            out_dir = Path(tmp) / "evidence"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "not-a-slug",
                    "--pr-number",
                    "67",
                    "--out-dir",
                    str(out_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            output = json.loads(result.stdout)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_github_evidence_sync")
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"repo_slug_invalid"})
            self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_malformed_json_returns_blocker_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            out_dir = Path(tmp) / "evidence"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_script = Path(tmp) / "fake_gh.py"
            pr_json = Path(tmp) / "pr-source.json"
            review_threads_json = Path(tmp) / "threads-source.json"
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_script(fake_script)
            write_fake_gh(fake_bin, fake_script)
            pr_json.write_text("{not valid json", encoding="utf-8")
            review_threads_json.write_text(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [],
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_PR_JSON"] = str(pr_json)
            env["GH_REVIEW_THREADS_JSON"] = str(review_threads_json)
            env["GH_CALL_LOG"] = str(gh_log)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "Chef-Code/agentic-cadence",
                    "--pr-number",
                    "67",
                    "--out-dir",
                    str(out_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            output = json.loads(result.stdout)
            self.assertFalse(output["valid"])
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"gh_json_invalid"})
            self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_incomplete_review_threads_returns_blocker_without_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            out_dir = Path(tmp) / "evidence"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_script = Path(tmp) / "fake_gh.py"
            pr_json = Path(tmp) / "pr-source.json"
            review_threads_json = Path(tmp) / "threads-source.json"
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_script(fake_script)
            write_fake_gh(fake_bin, fake_script)
            pr_json.write_text(
                json.dumps(
                    {
                        "number": 67,
                        "title": "Task 5",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "",
                        "body": "",
                        "headRefName": "codex/task-5-github-evidence-sync",
                        "baseRefName": "main",
                        "headRefOid": "abc123",
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            review_threads_json.write_text(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                        "nodes": [],
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_PR_JSON"] = str(pr_json)
            env["GH_REVIEW_THREADS_JSON"] = str(review_threads_json)
            env["GH_CALL_LOG"] = str(gh_log)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "github-evidence-sync",
                    "--repo",
                    "Chef-Code/agentic-cadence",
                    "--pr-number",
                    "67",
                    "--out-dir",
                    str(out_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1)
            output = json.loads(result.stdout)
            self.assertFalse(output["valid"])
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"review_thread_evidence_incomplete"})
            self.assertFalse(out_dir.exists())

    def test_github_evidence_sync_write_failure_removes_partial_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "evidence"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_script = Path(tmp) / "fake_gh.py"
            pr_json = Path(tmp) / "pr-source.json"
            review_threads_json = Path(tmp) / "threads-source.json"
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_script(fake_script)
            fake_gh = write_fake_gh(fake_bin, fake_script)
            pr_json.write_text(
                json.dumps(
                    {
                        "number": 67,
                        "title": "Task 5",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                        "reviewDecision": "",
                        "body": "",
                        "headRefName": "codex/task-5-github-evidence-sync",
                        "baseRefName": "main",
                        "headRefOid": "abc123",
                        "statusCheckRollup": [],
                    }
                ),
                encoding="utf-8",
            )
            review_threads_json.write_text(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [],
                                    }
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["GH_PR_JSON"] = str(pr_json)
            env["GH_REVIEW_THREADS_JSON"] = str(review_threads_json)
            env["GH_CALL_LOG"] = str(gh_log)
            real_replace = github_evidence.os.replace

            def fail_on_review_threads_replace(src, dst):
                if Path(dst).name == "pr-67-review-threads.json":
                    raise OSError("simulated write failure")
                return real_replace(src, dst)

            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch("codex_cadence.github_evidence.os.replace", side_effect=fail_on_review_threads_replace):
                    output = github_evidence.sync_github_evidence(
                        repo="Chef-Code/agentic-cadence",
                        pr_number=67,
                        out_dir=out_dir,
                        gh_bin=str(fake_gh),
                    )

            self.assertFalse(output["valid"])
            self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {"evidence_write_failed"})
            self.assertFalse((out_dir / "pr-67.json").exists())
            self.assertFalse((out_dir / "pr-67-review-threads.json").exists())
            self.assertFalse((out_dir / "pr-67-github-evidence.json").exists())

    def test_github_evidence_sync_failing_gh_returns_blockers_without_files(self):
        cases = [
            ("auth", "gh_auth_failed", "install_or_authenticate_gh"),
            ("rate", "github_rate_limited", "retry_github_evidence_sync"),
            ("network", "github_network_failed", "retry_github_evidence_sync"),
        ]
        for fail_mode, expected_code, expected_action in cases:
            with self.subTest(fail_mode=fail_mode):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "runtime"
                    out_dir = Path(tmp) / "evidence"
                    fake_bin = Path(tmp) / "bin"
                    fake_bin.mkdir()
                    fake_script = Path(tmp) / "fake_gh.py"
                    gh_log = Path(tmp) / "gh.log"
                    write_fake_gh_script(fake_script)
                    write_fake_gh(fake_bin, fake_script)
                    env = os.environ.copy()
                    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
                    env["GH_FAIL_MODE"] = fail_mode
                    env["GH_CALL_LOG"] = str(gh_log)

                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--root",
                            str(root),
                            "github-evidence-sync",
                            "--repo",
                            "Chef-Code/agentic-cadence",
                            "--pr-number",
                            "67",
                            "--out-dir",
                            str(out_dir),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=env,
                    )

                    self.assertEqual(result.returncode, 1)
                    output = json.loads(result.stdout)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertEqual({blocker["code"] for blocker in output["blockers"]}, {expected_code})
                    self.assertFalse(out_dir.exists())

    def test_discover_candidates_interactive_reads_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(tmp),
                    "discover-candidates",
                    "--cwd",
                    repo,
                    "--interactive",
                ],
                input="1\n",
                text=True,
                capture_output=True,
                check=False,
            )
            output = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["intent"], "merge_readiness")

    def test_loop_tick_reports_no_candidates_without_starting_execution(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "no_candidates")
            self.assertEqual(output["reason"], "no elected candidate")
            self.assertTrue(output["read_only"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["snapshot"]["repo"], "local/test")
            self.assertTrue(Path(output["snapshot"]["path"]).exists())
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_run_plan_reports_no_candidates_without_starting_runner(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-run-plan",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "loop-run-plan.v1")
            self.assertEqual(output["packet"], "loop_run_plan")
            self.assertTrue(output["read_only"])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertFalse(output["release_started"])
            self.assertFalse(output["package_publication_started"])
            self.assertFalse(output["role_assignment_started"])
            self.assertFalse(output["agent_scheduling_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertEqual(output["recommended_next_action"], "stop_no_candidates")
            self.assertNotIn("audit_record", output)
            self.assertEqual(output["loop_tick"]["recommended_next_action"], "no_candidates")
            self.assertNotIn("audit_record", output["loop_tick"])
            self.assertEqual(output["planned_steps"][0]["name"], "loop_tick")
            self.assertEqual(output["planned_steps"][0]["status"], "computed")
            self.assertFalse(output["planned_steps"][0]["audited"])
            self.assertFalse((Path(tmp) / "audit" / "events.jsonl").exists())
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_run_plan_emits_executor_task_approval_plan_for_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            evidence_path = Path(tmp) / "executor-result.json"

            result, output = run_cli(
                tmp,
                "loop-run-plan",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "notes.py",
                "--required-check",
                "python -m unittest tests.test_cadence",
                "--executor-evidence-path",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "request_operator_approval")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertFalse(output["release_started"])
            self.assertFalse(output["package_publication_started"])
            self.assertFalse(output["role_assignment_started"])
            self.assertFalse(output["agent_scheduling_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertNotIn("audit_record", output)
            self.assertEqual(output["loop_tick"]["recommended_next_action"], "approve_executor_task")
            self.assertNotIn("audit_record", output["loop_tick"])
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["packet"], "executor_task")
            self.assertEqual(output["executor_task_checksum"], checksum_json(executor_task))
            step_names = [step["name"] for step in output["planned_steps"]]
            self.assertEqual(step_names, ["loop_tick", "operator_approval", "start_governed_execution"])
            self.assertEqual(output["planned_steps"][0]["status"], "computed")
            self.assertFalse(output["planned_steps"][0]["audited"])
            self.assertEqual(output["planned_steps"][1]["status"], "required")
            self.assertEqual(output["planned_steps"][1]["target_checksum"], checksum_json(executor_task))
            self.assertEqual(output["planned_steps"][2]["status"], "blocked_until_approval")
            self.assertNotIn("approval_token_hint", output["planned_steps"][2])
            self.assertTrue(output["planned_steps"][2]["operator_approval_required"])
            self.assertEqual(output["planned_steps"][2]["target_checksum"], checksum_json(executor_task))
            self.assertFalse((Path(tmp) / "audit" / "events.jsonl").exists())
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def write_controlled_loop_start_inputs(self, tmp, repo, *, executor_evidence_path=None):
        marker = Path(repo) / "notes.py"
        marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
        git(repo, "add", "notes.py")
        git(repo, "commit", "-m", "add repo health marker")
        plan_path = Path(tmp) / "loop-run-plan.json"
        task_path = Path(tmp) / "executor-task.json"
        start_path = Path(tmp) / "execution-start.json"
        evidence_path = Path(executor_evidence_path) if executor_evidence_path is not None else Path(tmp) / "executor-result.json"

        plan_result, plan = run_cli(
            tmp,
            "loop-run-plan",
            "--cwd",
            repo,
            "--repo",
            "local/test",
            "--intent",
            "repo_health",
            "--emit-executor-task",
            "--allowed-path",
            "notes.py",
            "--required-check",
            "python -m unittest tests.test_cadence",
            "--executor-evidence-path",
            str(evidence_path),
        )
        self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        task_path.write_text(json.dumps(plan["executor_task"]), encoding="utf-8")

        start_result, start = run_cli(
            tmp,
            "start-governed-execution",
            "--task-file",
            str(task_path),
            "--approval-token",
            f"approve-executor-task:{plan['executor_task_checksum']}",
        )
        self.assertEqual(start_result.returncode, 0, start_result.stderr)
        start_path.write_text(json.dumps(start), encoding="utf-8")
        return plan_path, task_path, start_path, plan, start

    def write_controlled_loop_invocation_plan_inputs(self, tmp, repo):
        executor_result_path = Path(tmp) / "executor-results" / "executor-result.json"
        loop_plan_path, task_path, execution_start_path, loop_plan, execution_start = self.write_controlled_loop_start_inputs(
            tmp,
            repo,
            executor_evidence_path=executor_result_path,
        )
        controlled_start_result, controlled_start = run_cli(
            tmp,
            "controlled-loop-start",
            "--loop-run-plan-file",
            str(loop_plan_path),
            "--execution-start-file",
            str(execution_start_path),
        )
        self.assertEqual(controlled_start_result.returncode, 0, controlled_start_result.stderr)
        controlled_start_path = Path(tmp) / "controlled-loop-start.json"
        controlled_start_path.write_text(json.dumps(controlled_start), encoding="utf-8")

        task_packet = loop_plan["executor_task"]
        task_id = task_packet["task"]["id"]
        write_work_ownership(
            tmp,
            "ownership-1",
            task_id=task_id,
            candidate_id=task_id,
            branch=current_branch(repo),
            head=current_head(repo),
            epoch_id=execution_start["epoch_id"],
            handoff_id=None,
        )

        readiness_result, readiness = run_cli(
            tmp,
            "executor-invocation-readiness",
            "--cwd",
            repo,
            "--task-file",
            str(task_path),
            "--epoch-id",
            execution_start["epoch_id"],
            "--ownership-target",
            "ownership-1",
            "--expected-result-path",
            str(executor_result_path),
        )
        self.assertEqual(readiness_result.returncode, 0, readiness_result.stderr)
        readiness_path = Path(tmp) / "executor-invocation-readiness.json"
        readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

        audit_result, audit_replay = run_cli(tmp, "audit-replay")
        self.assertEqual(audit_result.returncode, 0, audit_result.stderr)
        self.assertTrue(audit_replay["valid"])

        command = "python -m unittest tests.test_cadence"
        adapter_path, adapter_packet = write_executor_invocation_adapter(
            Path(tmp) / "executor-adapter.json",
            command_template=command,
        )
        rollback_path, rollback_packet = write_executor_invocation_rollback(
            Path(tmp) / "executor-rollback.json",
            task_packet,
        )
        environment_allowlist = ["PATH", "PYTHONPATH"]
        target = executor_invocation_target_descriptor(
            readiness_packet=readiness,
            adapter_packet=adapter_packet,
            rollback_packet=rollback_packet,
            command=command,
            cwd=repo,
            expected_result_path=executor_result_path,
            environment_allowlist=environment_allowlist,
            timeout_seconds=300,
            audit_chain_head=audit_replay["chain_head"],
        )
        approval_path, _approval_packet = write_operator_approval(
            Path(tmp) / "executor-invocation-approval.json",
            target_checksum=checksum_json(target),
            purpose="real_executor_invocation",
        )
        invocation_plan_result, invocation_plan = run_cli(
            tmp,
            "executor-invocation-plan",
            "--cwd",
            repo,
            "--readiness-file",
            str(readiness_path),
            "--approval-file",
            str(approval_path),
            "--approval-secret",
            OPERATOR_APPROVAL_SECRET,
            "--adapter-file",
            str(adapter_path),
            "--rollback-file",
            str(rollback_path),
            "--command",
            command,
            "--env-allow",
            "PATH",
            "--env-allow",
            "PYTHONPATH",
            "--timeout-seconds",
            "300",
            "--expected-result-path",
            str(executor_result_path),
        )
        self.assertEqual(invocation_plan_result.returncode, 0, invocation_plan_result.stderr)
        invocation_plan_path = Path(tmp) / "executor-invocation-plan.json"
        invocation_plan_path.write_text(json.dumps(invocation_plan), encoding="utf-8")

        return {
            "controlled_start_path": controlled_start_path,
            "readiness_path": readiness_path,
            "invocation_plan_path": invocation_plan_path,
            "controlled_start": controlled_start,
            "readiness": readiness,
            "invocation_plan": invocation_plan,
            "execution_start": execution_start,
            "loop_plan": loop_plan,
            "task_packet": task_packet,
        }

    def write_controlled_loop_real_invocation_inputs(self, tmp, repo, *, result_status="succeeded"):
        inputs, plan_path, plan = self.write_real_executor_invocation_plan(tmp, repo, result_status=result_status)
        readiness = json.loads(Path(inputs["readiness_path"]).read_text(encoding="utf-8"))
        task = readiness["task"]
        epoch = readiness["active_epoch"]
        controlled_start = {
            "protocol_version": "v1",
            "schema_version": "controlled-loop-start.v1",
            "packet": "controlled_loop_start",
            "read_only": True,
            "valid": True,
            "controlled_start_status": "completed",
            "recommended_next_action": "plan_executor_invocation",
            "runner_started": False,
            "executor_started": False,
            "epoch_started": False,
            "pr_action_started": False,
            "github_write_started": False,
            "merge_started": False,
            "release_started": False,
            "package_publication_started": False,
            "role_assignment_started": False,
            "agent_scheduling_started": False,
            "loop_continuation_started": False,
            "side_effects": [],
            "files": {},
            "execution_start": {"task_file": task["file"]},
            "executor_task_checksum": task["checksum"],
            "task_id": task["id"],
            "epoch_id": epoch["id"],
            "blockers": [],
        }
        controlled_start_path = Path(tmp) / "controlled-loop-start.json"
        controlled_start_path.write_text(json.dumps(controlled_start), encoding="utf-8")
        controlled_plan_result, controlled_plan = run_cli(
            tmp,
            "controlled-loop-invocation-plan",
            "--controlled-loop-start-file",
            str(controlled_start_path),
            "--readiness-file",
            str(inputs["readiness_path"]),
            "--invocation-plan-file",
            str(plan_path),
        )
        self.assertEqual(controlled_plan_result.returncode, 0, controlled_plan_result.stderr)
        controlled_plan_path = Path(tmp) / "controlled-loop-invocation-plan.json"
        controlled_plan_path.write_text(json.dumps(controlled_plan), encoding="utf-8")

        invocation_result, invocation = self.run_invoke_real_executor_cli(tmp, plan_path)
        self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
        invocation_path = Path(invocation["record_file"])
        return {
            "inputs": inputs,
            "controlled_start_path": controlled_start_path,
            "controlled_start": controlled_start,
            "controlled_plan_path": controlled_plan_path,
            "controlled_plan": controlled_plan,
            "plan_path": plan_path,
            "plan": plan,
            "invocation_path": invocation_path,
            "invocation": invocation,
            "result_path": Path(invocation["result_file"]),
        }

    def write_controlled_loop_closeout_inputs(self, tmp, repo, *, result_status="succeeded"):
        inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo, result_status=result_status)
        controlled_result, controlled_real_invocation = run_cli(
            tmp,
            "controlled-loop-real-invocation",
            "--controlled-invocation-plan-file",
            str(inputs["controlled_plan_path"]),
            "--real-invocation-file",
            str(inputs["invocation_path"]),
        )
        self.assertEqual(controlled_result.returncode, 0, controlled_result.stderr)
        controlled_real_invocation_path = Path(tmp) / "controlled-loop-real-invocation.json"
        controlled_real_invocation_path.write_text(json.dumps(controlled_real_invocation), encoding="utf-8")

        readiness = json.loads(Path(inputs["inputs"]["readiness_path"]).read_text(encoding="utf-8"))
        task_file = Path(readiness["task"]["file"])
        snapshot_after_path = Path(tmp) / "snapshot-after.json"
        snapshot_after_path.write_text(
            json.dumps(closeout_snapshot(repo, id="snapshot-after-controlled-closeout", captured_at="2999-05-22T00:20:00Z")),
            encoding="utf-8",
        )
        closeout_result, closeout = run_cli(
            tmp,
            "closeout-executor-result",
            "--epoch-id",
            controlled_real_invocation["epoch_id"],
            "--task-file",
            str(task_file),
            "--result-file",
            str(inputs["result_path"]),
            "--snapshot-after-file",
            str(snapshot_after_path),
            "--real-invocation-file",
            str(inputs["invocation_path"]),
            "--cwd",
            repo,
        )
        self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
        closeout_path = Path(tmp) / "executor-closeout.json"
        closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
        return {
            **inputs,
            "controlled_real_invocation_path": controlled_real_invocation_path,
            "controlled_real_invocation": controlled_real_invocation,
            "task_path": task_file,
            "snapshot_after_path": snapshot_after_path,
            "closeout_path": closeout_path,
            "closeout": closeout,
        }

    def run_controlled_loop_closeout_cli(self, tmp, inputs, **overrides):
        values = {
            "controlled_real_invocation_file": inputs["controlled_real_invocation_path"],
            "closeout_file": inputs["closeout_path"],
        }
        values.update(overrides)
        return run_cli(
            tmp,
            "controlled-loop-closeout",
            "--controlled-real-invocation-file",
            str(values["controlled_real_invocation_file"]),
            "--closeout-file",
            str(values["closeout_file"]),
        )

    def controlled_loop_closeout_supplied_file_texts(self, inputs):
        return {
            "controlled_real_invocation": inputs["controlled_real_invocation_path"].read_text(encoding="utf-8"),
            "closeout": inputs["closeout_path"].read_text(encoding="utf-8"),
            "real_invocation": inputs["invocation_path"].read_text(encoding="utf-8"),
        }

    def refresh_controlled_loop_closeout_audits(self, tmp, inputs, closeout, updated_invocation):
        closeout["real_invocation"]["after_checksum"] = checksum_json(updated_invocation)
        closeout["real_invocation"]["audit_record"] = append_audit_record(
            Path(tmp),
            real_executor_invocation_audit_record(
                updated_invocation,
                invocation_record_file=str(inputs["invocation_path"]),
                action="update_real_executor_invocation_closeout",
                reason="real executor invocation closeout status updated",
            ),
        )
        task_packet = json.loads(inputs["task_path"].read_text(encoding="utf-8"))
        result_evidence = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
        closeout_without_audit = {key: value for key, value in closeout.items() if key != "audit_record"}
        closeout["audit_record"] = append_audit_record(
            Path(tmp),
            executor_epoch_closeout_audit_record(closeout_without_audit, task_packet, result_evidence),
        )

    def test_controlled_loop_start_composes_plan_and_execution_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, plan, start = self.write_controlled_loop_start_inputs(tmp, repo)

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-start.v1")
            self.assertEqual(output["packet"], "controlled_loop_start")
            self.assertTrue(output["read_only"])
            self.assertEqual(output["controlled_start_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "plan_executor_invocation")
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertFalse(output["release_started"])
            self.assertFalse(output["package_publication_started"])
            self.assertFalse(output["role_assignment_started"])
            self.assertFalse(output["agent_scheduling_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertEqual(output["loop_run_plan_checksum"], checksum_json(plan))
            self.assertEqual(output["execution_start_checksum"], checksum_json(start))
            self.assertEqual(output["executor_task_checksum"], plan["executor_task_checksum"])
            self.assertEqual(output["execution_start"]["task_checksum"], plan["executor_task_checksum"])
            self.assertEqual(output["blockers"], [])
            audit_events = Path(tmp) / "audit" / "events.jsonl"
            audit_lines = audit_events.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            self.assertIn("execution_start_decision", audit_lines[0])

    def test_controlled_loop_start_blocks_mismatched_execution_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, _plan, start = self.write_controlled_loop_start_inputs(tmp, repo)
            start["task_checksum"] = "sha256:" + "0" * 64
            start_path.write_text(json.dumps(start), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-start.v1")
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "recreate_execution_start")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertIn("execution_start_task_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertFalse(output["release_started"])
            self.assertFalse(output["package_publication_started"])
            self.assertFalse(output["role_assignment_started"])
            self.assertFalse(output["agent_scheduling_started"])
            self.assertFalse(output["loop_continuation_started"])
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            self.assertIn("execution_start_decision", audit_lines[0])

    def test_controlled_loop_start_blocks_unapproved_execution_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, _plan, start = self.write_controlled_loop_start_inputs(tmp, repo)
            start["approval_state"] = "missing"
            start_path.write_text(json.dumps(start), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_execution_start")
            self.assertIn("execution_start_invalid", {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_start_blocks_malformed_embedded_executor_task(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, plan, start = self.write_controlled_loop_start_inputs(tmp, repo)
            malformed_task = {
                "schema_version": "generic-executor-task.v1",
                "packet": "executor_task",
                "task": {},
            }
            plan["executor_task"] = malformed_task
            plan["executor_task_checksum"] = checksum_json(malformed_task)
            start["task_checksum"] = plan["executor_task_checksum"]
            start["task_id"] = None
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            start_path.write_text(json.dumps(start), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "regenerate_loop_run_plan")
            self.assertIn("loop_run_plan_not_ready", {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_start_prioritizes_malformed_plan_before_start_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, plan, _start = self.write_controlled_loop_start_inputs(tmp, repo)
            plan["executor_task_checksum"] = ""
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "regenerate_loop_run_plan")
            self.assertIn("loop_run_plan_not_ready", blocker_codes)
            self.assertNotIn("execution_start_task_mismatch", blocker_codes)

    def test_controlled_loop_start_blocks_start_without_active_epoch_binding(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, _plan, _start = self.write_controlled_loop_start_inputs(tmp, repo)
            for path in (Path(tmp) / "epochs" / "active").glob("*.json"):
                path.unlink()

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_execution_start")
            self.assertIn("execution_start_invalid", {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_start_blocks_start_without_audit_binding(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            plan_path, _task_path, start_path, _plan, _start = self.write_controlled_loop_start_inputs(tmp, repo)
            audit_path = Path(tmp) / "audit" / "events.jsonl"
            audit_path.unlink()

            result, output = run_cli(
                tmp,
                "controlled-loop-start",
                "--loop-run-plan-file",
                str(plan_path),
                "--execution-start-file",
                str(start_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_start_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_execution_start")
            self.assertIn("execution_start_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertFalse(audit_path.exists())

    def test_controlled_loop_start_blocks_side_effect_contaminated_inputs(self):
        cases = [
            ("loop_plan", "github_write_started", True, "loop_run_plan_not_ready", "regenerate_loop_run_plan"),
            ("execution_start", "pr_action_started", True, "execution_start_invalid", "inspect_execution_start"),
            ("execution_start", "github_write_started", "true", "execution_start_invalid", "inspect_execution_start"),
        ]
        for target, flag, value, expected_code, expected_action in cases:
            with self.subTest(target=target, flag=flag):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    plan_path, _task_path, start_path, plan, start = self.write_controlled_loop_start_inputs(tmp, repo)
                    audit_path = Path(tmp) / "audit" / "events.jsonl"
                    audit_before = audit_path.read_text(encoding="utf-8")
                    if target == "loop_plan":
                        plan[flag] = value
                        plan_path.write_text(json.dumps(plan), encoding="utf-8")
                    else:
                        start[flag] = value
                        start_path.write_text(json.dumps(start), encoding="utf-8")

                    result, output = run_cli(
                        tmp,
                        "controlled-loop-start",
                        "--loop-run-plan-file",
                        str(plan_path),
                        "--execution-start-file",
                        str(start_path),
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["controlled_start_status"], "blocked")
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(audit_path.read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_composes_start_readiness_and_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-invocation-plan.v1")
            self.assertEqual(output["packet"], "controlled_loop_invocation_plan")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_invocation_plan_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "invoke_real_executor")
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["controlled_loop_start_checksum"], checksum_json(inputs["controlled_start"]))
            self.assertEqual(output["readiness_checksum"], checksum_json(inputs["readiness"]))
            self.assertEqual(output["invocation_plan_checksum"], checksum_json(inputs["invocation_plan"]))
            self.assertEqual(output["task_id"], inputs["task_packet"]["task"]["id"])
            self.assertEqual(output["epoch_id"], inputs["execution_start"]["epoch_id"])
            self.assertEqual(output["target_checksum"], inputs["invocation_plan"]["target_checksum"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_mismatched_invocation_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            invocation_plan = dict(inputs["invocation_plan"])
            invocation_plan["readiness"] = dict(invocation_plan["readiness"])
            invocation_plan["readiness"]["checksum"] = "sha256:" + "0" * 64
            inputs["invocation_plan_path"].write_text(json.dumps(invocation_plan), encoding="utf-8")
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_invocation_plan_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "recreate_executor_invocation_plan")
            self.assertIn("invocation_plan_readiness_mismatch", blocker_codes)
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_missing_readiness_file_anchor(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            invocation_plan = dict(inputs["invocation_plan"])
            invocation_plan["readiness"] = dict(invocation_plan["readiness"])
            del invocation_plan["readiness"]["file"]
            inputs["invocation_plan_path"].write_text(json.dumps(invocation_plan), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_invocation_plan")
            self.assertIn("invocation_plan_readiness_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_target_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            invocation_plan = dict(inputs["invocation_plan"])
            invocation_plan["target_checksum"] = "sha256:" + "0" * 64
            inputs["invocation_plan_path"].write_text(json.dumps(invocation_plan), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_invocation_plan")
            self.assertIn("invocation_plan_target_checksum_mismatch", blocker_codes)
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_missing_controlled_start_and_readiness_anchors(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            controlled_start = dict(inputs["controlled_start"])
            controlled_start.pop("task_id")
            controlled_start.pop("epoch_id")
            controlled_start.pop("executor_task_checksum")
            inputs["controlled_start_path"].write_text(json.dumps(controlled_start), encoding="utf-8")
            readiness = dict(inputs["readiness"])
            readiness["task"] = dict(readiness["task"])
            readiness["task"].pop("id")
            readiness["task"].pop("checksum")
            readiness["active_epoch"] = dict(readiness["active_epoch"])
            readiness["active_epoch"].pop("id")
            inputs["readiness_path"].write_text(json.dumps(readiness), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_executor_invocation_readiness")
            self.assertIn("controlled_start_readiness_mismatch", blocker_codes)
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_controlled_start_readiness_task_file_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            controlled_start = dict(inputs["controlled_start"])
            controlled_start["execution_start"] = dict(controlled_start["execution_start"])
            controlled_start["execution_start"]["task_file"] = str(Path(tmp) / "other-task.json")
            inputs["controlled_start_path"].write_text(json.dumps(controlled_start), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_executor_invocation_readiness")
            self.assertIn("controlled_start_readiness_mismatch", blocker_codes)
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_controlled_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            controlled_start = dict(inputs["controlled_start"])
            controlled_start["side_effects"] = ["executor_started"]
            inputs["controlled_start_path"].write_text(json.dumps(controlled_start), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "recreate_controlled_loop_start")
            self.assertIn("controlled_start_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_readiness(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            readiness = dict(inputs["readiness"])
            readiness["side_effects"] = ["executor_started"]
            inputs["readiness_path"].write_text(json.dumps(readiness), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_executor_invocation_readiness")
            self.assertIn("readiness_not_invocable", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_invocation_plan_blocks_side_effect_contaminated_invocation_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_invocation_plan_inputs(tmp, repo)
            audit_before = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8")
            invocation_plan = dict(inputs["invocation_plan"])
            invocation_plan["github_write_started"] = True
            inputs["invocation_plan_path"].write_text(json.dumps(invocation_plan), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-invocation-plan",
                "--controlled-loop-start-file",
                str(inputs["controlled_start_path"]),
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--invocation-plan-file",
                str(inputs["invocation_plan_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_invocation_plan")
            self.assertIn("invocation_plan_not_invocable", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual((Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8"), audit_before)

    def test_controlled_loop_real_invocation_composes_plan_and_recorded_invocation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-real-invocation.v1")
            self.assertEqual(output["packet"], "controlled_loop_real_invocation")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_real_invocation_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "closeout_executor_result")
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["controlled_invocation_plan_checksum"], checksum_json(inputs["controlled_plan"]))
            self.assertEqual(output["invocation_plan_checksum"], checksum_json(inputs["plan"]))
            self.assertEqual(output["real_invocation_checksum"], checksum_json(inputs["invocation"]))
            self.assertEqual(output["result_evidence_checksum"], inputs["invocation"]["result_evidence_checksum"])
            self.assertEqual(output["task_id"], inputs["inputs"]["task_packet"]["task"]["id"])
            self.assertEqual(output["epoch_id"], inputs["controlled_plan"]["epoch_id"])
            self.assertEqual(output["target_checksum"], inputs["plan"]["target_checksum"])
            self.assertEqual(output["real_invocation"]["invocation_id"], inputs["invocation"]["invocation_id"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_mismatched_plan_checksum(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            invocation["plan_checksum"] = "sha256:" + "0" * 64
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_real_invocation_status"], "blocked")
            self.assertEqual(output["side_effects"], [])
            self.assertIn("real_invocation_plan_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_missing_invocation_audit_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            audit_path = Path(tmp) / "audit" / "events.jsonl"
            records = audit_records(tmp)
            retained_records = [
                record
                for record in records
                if not (
                    record.get("event") == "real_executor_invocation_record"
                    and record.get("action") == "record_real_executor_invocation"
                    and record.get("invocation_id") == inputs["invocation"]["invocation_id"]
                )
            ]
            audit_path.write_text("\n".join(json.dumps(record) for record in retained_records) + "\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("real_invocation_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), retained_records)

    def test_controlled_loop_real_invocation_blocks_invalid_invocation_id_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            invocation["invocation_id"] = "../bad"
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("real_invocation_identity_missing", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_invalid_invocation_audit_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            audit_path = Path(tmp) / "audit" / "events.jsonl"
            records = audit_records(tmp)
            for record in records:
                if (
                    record.get("event") == "real_executor_invocation_record"
                    and record.get("action") == "record_real_executor_invocation"
                    and record.get("invocation_id") == inputs["invocation"]["invocation_id"]
                ):
                    record["event_hash"] = "sha256:" + "f" * 64
                    break
            audit_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("real_invocation_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), records)

    def test_controlled_loop_real_invocation_blocks_broken_runtime_audit_chain(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            audit_path = Path(tmp) / "audit" / "events.jsonl"
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "unsupported_audit_event"}) + "\n")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("real_invocation_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertIsNone(output["result_evidence"])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_does_not_read_mismatched_result_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            secret_path = Path(tmp) / "secret-result.json"
            secret_path.write_text(json.dumps({"secret": "do-not-emit"}), encoding="utf-8")
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            invocation["result_file"] = str(secret_path)
            invocation["result_evidence_checksum"] = checksum_json({"secret": "do-not-emit"})
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIsNone(output["result_evidence"])
            self.assertNotIn("do-not-emit", result.stdout)
            self.assertIn("real_invocation_result_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_malformed_real_invocation_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            invocation["packet"] = "executor_invocation_plan"
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_real_invocation_evidence")
            self.assertIn("real_invocation_packet_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_completed_closeout_status(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            invocation["closeout_status"] = "completed"
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_real_invocation_evidence")
            self.assertIn("real_invocation_closeout_not_pending", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_result_tampering(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            result_packet = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
            result_packet["summary"] = "tampered result"
            inputs["result_path"].write_text(json.dumps(result_packet), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("real_invocation_result_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_real_invocation_blocks_side_effect_contaminated_plan_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_real_invocation_inputs(tmp, repo)
            controlled_plan = json.loads(inputs["controlled_plan_path"].read_text(encoding="utf-8"))
            controlled_plan["side_effects"] = ["unexpected_audit_append"]
            inputs["controlled_plan_path"].write_text(json.dumps(controlled_plan), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "controlled-loop-real-invocation",
                "--controlled-invocation-plan-file",
                str(inputs["controlled_plan_path"]),
                "--real-invocation-file",
                str(inputs["invocation_path"]),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("controlled_invocation_plan_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_composes_controlled_real_invocation_and_closeout(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            audit_before = audit_records(tmp)
            files_before = self.controlled_loop_closeout_supplied_file_texts(inputs)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNotNone(output)
            self.assertEqual(output["schema_version"], "controlled-loop-closeout.v1")
            self.assertEqual(output["packet"], "controlled_loop_closeout")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_closeout_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "controlled_loop_tick")
            self.assertEqual(output["side_effects"], [])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertFalse(output["release_started"])
            self.assertFalse(output["package_publication_started"])
            self.assertFalse(output["role_assignment_started"])
            self.assertFalse(output["agent_scheduling_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertFalse(output["operator_confirmation_required"])
            self.assertEqual(output["controlled_real_invocation_checksum"], checksum_json(inputs["controlled_real_invocation"]))
            self.assertEqual(output["closeout_checksum"], checksum_json(inputs["closeout"]))
            self.assertEqual(output["real_invocation_before_checksum"], checksum_json(inputs["controlled_real_invocation"]["real_invocation"]))
            self.assertEqual(output["real_invocation_after_checksum"], checksum_json(updated_invocation))
            self.assertEqual(output["epoch_closeout_checksum"], inputs["closeout"]["real_invocation"]["epoch_closeout_checksum"])
            self.assertEqual(output["task_id"], inputs["controlled_real_invocation"]["task_id"])
            self.assertEqual(output["epoch_id"], inputs["controlled_real_invocation"]["epoch_id"])
            self.assertEqual(output["closeout_status"], "completed")
            self.assertEqual(output["blockers"], [])
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(self.controlled_loop_closeout_supplied_file_texts(inputs), files_before)

    def test_controlled_loop_closeout_accepts_terminal_failed_closeout(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo, result_status="blocked")
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            audit_before = audit_records(tmp)
            files_before = self.controlled_loop_closeout_supplied_file_texts(inputs)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNotNone(output)
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_closeout_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "controlled_loop_tick")
            self.assertEqual(output["closeout_status"], "failed")
            self.assertEqual(output["closeout"]["closeout_status"], "failed")
            self.assertEqual(output["closeout"]["executor_result_status"], "blocked")
            self.assertEqual(output["real_invocation"]["closeout_status"], "failed")
            self.assertEqual(output["real_invocation_after_checksum"], checksum_json(updated_invocation))
            self.assertEqual(output["blockers"], [])
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(self.controlled_loop_closeout_supplied_file_texts(inputs), files_before)

    def test_controlled_loop_closeout_accepts_runtime_relative_invocation_paths_saved_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            relative_invocation_path = str(Path("real-executor-invocations") / inputs["invocation_path"].name)
            controlled = json.loads(inputs["controlled_real_invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            controlled["files"]["real_invocation"] = relative_invocation_path
            closeout["real_invocation"]["path"] = relative_invocation_path
            task_packet = json.loads(inputs["task_path"].read_text(encoding="utf-8"))
            result_evidence = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
            closeout_without_audit = {key: value for key, value in closeout.items() if key != "audit_record"}
            closeout["audit_record"] = append_audit_record(
                Path(tmp),
                executor_epoch_closeout_audit_record(closeout_without_audit, task_packet, result_evidence),
            )
            controlled_path = evidence_dir / "controlled-loop-real-invocation.json"
            closeout_path = evidence_dir / "executor-closeout.json"
            controlled_path.write_text(json.dumps(controlled), encoding="utf-8")
            closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
            inputs = {
                **inputs,
                "controlled_real_invocation_path": controlled_path,
                "closeout_path": closeout_path,
            }
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNotNone(output)
            self.assertTrue(output["valid"])
            self.assertEqual(output["files"]["real_invocation"], str(inputs["invocation_path"]))
            self.assertEqual(output["recommended_next_action"], "controlled_loop_tick")
            self.assertEqual(output["blockers"], [])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_accepts_closeout_relative_audit_paths_saved_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            relative_audit_path = os.path.relpath(Path(tmp) / "audit" / "events.jsonl", evidence_dir)
            closeout["real_invocation"]["audit_record"]["path"] = relative_audit_path
            task_packet = json.loads(inputs["task_path"].read_text(encoding="utf-8"))
            result_evidence = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
            closeout_without_audit = {key: value for key, value in closeout.items() if key != "audit_record"}
            closeout["audit_record"] = append_audit_record(
                Path(tmp),
                executor_epoch_closeout_audit_record(closeout_without_audit, task_packet, result_evidence),
            )
            closeout["audit_record"]["path"] = relative_audit_path
            closeout_path = evidence_dir / "executor-closeout.json"
            closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
            inputs = {**inputs, "closeout_path": closeout_path}
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNotNone(output)
            self.assertTrue(output["valid"])
            self.assertEqual(output["recommended_next_action"], "controlled_loop_tick")
            self.assertEqual(output["blockers"], [])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_mismatched_pre_closeout_invocation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            closeout["real_invocation"]["before_checksum"] = "sha256:" + "0" * 64
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_closeout_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("closeout_invocation_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_stale_updated_invocation_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            updated_invocation["closeout_status"] = "stale"
            inputs["invocation_path"].write_text(json.dumps(updated_invocation), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("real_invocation_closeout_mismatch", blocker_codes)
            self.assertIn("closeout_invocation_mismatch", blocker_codes)
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_non_terminal_closeout(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            closeout["closeout_status"] = "blocked"
            invocation["closeout_status"] = "blocked"
            closeout_core_packet = {
                key: value
                for key, value in closeout.items()
                if key not in {"audit_record", "run_record", "real_invocation"}
            }
            epoch_closeout_checksum = checksum_json(closeout_core_packet)
            invocation["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
            inputs["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("closeout_not_terminal", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_stale_controlled_real_invocation_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            controlled = json.loads(inputs["controlled_real_invocation_path"].read_text(encoding="utf-8"))
            controlled["real_invocation"]["closeout_status"] = "stale"
            inputs["controlled_real_invocation_path"].write_text(json.dumps(controlled), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_real_invocation")
            self.assertIn("controlled_real_invocation_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_updated_invocation_immutable_anchor_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            updated_invocation["plan_checksum"] = "sha256:" + "0" * 64
            inputs["invocation_path"].write_text(json.dumps(updated_invocation), encoding="utf-8")

            self.refresh_controlled_loop_closeout_audits(tmp, inputs, closeout, updated_invocation)
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("real_invocation_closeout_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_updated_invocation_snapshot_after_checksum_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            updated_invocation["snapshot_after_checksum"] = "sha256:" + "0" * 64
            inputs["invocation_path"].write_text(json.dumps(updated_invocation), encoding="utf-8")
            self.refresh_controlled_loop_closeout_audits(tmp, inputs, closeout, updated_invocation)
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("real_invocation_closeout_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_tampered_closeout_audit_reference(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            closeout["audit_record"]["event_hash"] = "sha256:" + "0" * 64
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("closeout_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_closeout_audit_task_checksum_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            task_packet = json.loads(inputs["task_path"].read_text(encoding="utf-8"))
            result_evidence = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
            closeout_without_audit = {key: value for key, value in closeout.items() if key != "audit_record"}
            audit_record = executor_epoch_closeout_audit_record(closeout_without_audit, task_packet, result_evidence)
            audit_record["task_packet_checksum"] = "sha256:" + "0" * 64
            closeout["audit_record"] = append_audit_record(Path(tmp), audit_record)
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("closeout_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_tampered_real_invocation_audit_reference(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            closeout["real_invocation"]["audit_record"]["event_hash"] = "sha256:" + "0" * 64
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("real_invocation_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_blocks_real_invocation_audit_payload_checksum_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            updated_invocation = json.loads(inputs["invocation_path"].read_text(encoding="utf-8"))
            audit_record = real_executor_invocation_audit_record(
                updated_invocation,
                invocation_record_file=str(inputs["invocation_path"]),
                action="update_real_executor_invocation_closeout",
                reason="real executor invocation closeout status updated",
            )
            audit_record["payload_checksum"] = "sha256:" + "0" * 64
            closeout["real_invocation"]["audit_record"] = append_audit_record(Path(tmp), audit_record)
            task_packet = json.loads(inputs["task_path"].read_text(encoding="utf-8"))
            result_evidence = json.loads(inputs["result_path"].read_text(encoding="utf-8"))
            closeout_without_audit = {key: value for key, value in closeout.items() if key != "audit_record"}
            closeout["audit_record"] = append_audit_record(
                Path(tmp),
                executor_epoch_closeout_audit_record(closeout_without_audit, task_packet, result_evidence),
            )
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_closeout_evidence")
            self.assertIn("real_invocation_audit_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_does_not_read_mismatched_invocation_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            secret_path = Path(tmp) / "secret.json"
            secret_path.write_text(json.dumps({"secret": "do-not-emit"}), encoding="utf-8")
            controlled = json.loads(inputs["controlled_real_invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            controlled["files"]["real_invocation"] = str(secret_path)
            closeout["real_invocation"]["path"] = str(secret_path)
            inputs["controlled_real_invocation_path"].write_text(json.dumps(controlled), encoding="utf-8")
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)
            files_before = self.controlled_loop_closeout_supplied_file_texts(inputs)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertIsNone(output["real_invocation"])
            self.assertNotIn("do-not-emit", result.stdout)
            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("controlled_real_invocation_invalid", blocker_codes)
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(self.controlled_loop_closeout_supplied_file_texts(inputs), files_before)

    def test_controlled_loop_closeout_blocks_tampered_controlled_task_id(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            controlled = json.loads(inputs["controlled_real_invocation_path"].read_text(encoding="utf-8"))
            controlled["task_id"] = "forged-task-id"
            inputs["controlled_real_invocation_path"].write_text(json.dumps(controlled), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_real_invocation")
            self.assertIn("controlled_real_invocation_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_closeout_does_not_read_mismatched_audit_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_controlled_loop_closeout_inputs(tmp, repo)
            closeout = json.loads(inputs["closeout_path"].read_text(encoding="utf-8"))
            closeout["audit_record"]["path"] = str(Path(tmp) / "not-the-audit-log.jsonl")
            inputs["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_closeout_cli(tmp, inputs)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            closeout_audit_blockers = [
                blocker for blocker in output["blockers"] if blocker["code"] == "closeout_audit_mismatch"
            ]
            self.assertTrue(closeout_audit_blockers)
            self.assertFalse(any("line could not be read" in blocker["message"] for blocker in closeout_audit_blockers))
            self.assertEqual(audit_records(tmp), audit_before)

    def test_loop_tick_stops_at_executor_contract_for_elected_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "requires_executor_contract")
            self.assertEqual(output["reason"], "executor task packet has not been emitted")
            self.assertTrue(output["executor_contract_required"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertIn("executor_contract_not_implemented", output["limitations"])
            self.assertEqual(output["cadence"]["state"], "PLAY_ON")
            self.assertEqual(output["snapshot"]["repo_confidence"], "high")
            self.assertEqual(output["elected_next"][0]["source"], "text_marker")
            self.assertEqual(output["candidate_discovery"]["elected_next"][0]["source"], "text_marker")
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_tick_can_emit_generic_executor_task_without_starting_execution(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            evidence_path = Path(tmp) / "executor-result.json"

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "codex_cadence",
                "--allowed-path",
                "tests",
                "--required-check",
                "python -m unittest tests.test_cadence",
                "--executor-evidence-path",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertEqual(output["reason"], "executor task packet emitted for operator approval")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["executor_contract_required"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["packet"], "executor_task")
            self.assertEqual(executor_task["schema_version"], "generic-executor-task.v1")
            self.assertEqual(executor_task["task"]["id"], output["elected_next"][0]["id"])
            self.assertEqual(executor_task["repo"]["path"], str(Path(repo).resolve()))
            self.assertEqual(executor_task["allowed_paths"], ["codex_cadence", "tests"])
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(executor_task["expected_output"]["evidence_path"], str(evidence_path))
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_tick_records_audit_entry_for_executor_task_decision(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "tests",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "loop_tick_decision")
            self.assertTrue(Path(audit_ref["path"]).exists())
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "loop_tick_decision")
            self.assertEqual(record["action"], "approve_executor_task")
            self.assertEqual(record["tick_id"], output["tick_id"])
            self.assertEqual(record["repo"], "local/test")
            self.assertEqual(record["branch"], output["snapshot"]["branch"])
            self.assertEqual(record["head"], output["snapshot"]["head"])
            self.assertEqual(record["executor_task_id"], output["executor_task"]["task"]["id"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))

    def test_start_governed_execution_starts_epoch_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect governed execution start\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add governed execution marker")
            loop_result, loop_output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "notes.py",
                "--required-check",
                "python -m unittest tests.test_cadence",
            )
            self.assertEqual(loop_result.returncode, 0, loop_result.stderr)
            task_packet = loop_output["executor_task"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            approval_token = f"approve-executor-task:{checksum_json(task_packet)}"

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "execution-start.v1")
            self.assertEqual(output["packet"], "execution_start")
            self.assertTrue(output["valid"])
            self.assertEqual(output["blockers"], [])
            self.assertTrue(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertEqual(output["recommended_next_action"], "handoff_to_executor")
            self.assertEqual(output["approval_state"], "approved")
            self.assertEqual(output["task_checksum"], checksum_json(task_packet))
            self.assertEqual(output["task_id"], task_packet["task"]["id"])
            self.assertEqual(output["repo"]["branch"], current_branch(repo))
            self.assertEqual(output["repo"]["head"], current_head(repo))
            active_epochs = list((Path(tmp) / "epochs" / "active").glob("*.json"))
            self.assertEqual(len(active_epochs), 1)
            epoch = json.loads(active_epochs[0].read_text(encoding="utf-8"))
            self.assertEqual(output["epoch_id"], epoch["id"])
            self.assertEqual(epoch["repo"], "local/test")
            self.assertEqual(epoch["branch"], current_branch(repo))
            self.assertEqual(epoch["tasks"][0]["id"], task_packet["task"]["id"])
            self.assertEqual(epoch["tasks"][0]["task_type"], task_packet["task"]["task_type"])
            self.assertEqual(epoch["snapshot_before"]["head"], current_head(repo))
            self.assertIn("executor_not_started", output["limitations"])

    def test_verify_operator_approval_accepts_fresh_matching_hmac_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            approval_path, packet = write_operator_approval(Path(tmp) / "operator-approval.json")

            result, output = run_cli(
                tmp,
                "verify-operator-approval",
                "--approval-file",
                str(approval_path),
                "--target-checksum",
                OPERATOR_APPROVAL_TARGET,
                "--purpose",
                "start_governed_execution",
                "--approval-secret",
                OPERATOR_APPROVAL_SECRET,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "operator-approval-verification.v1")
            self.assertEqual(output["approval_schema_version"], "operator-approval.v1")
            self.assertEqual(output["packet"], "operator_approval_verification")
            self.assertTrue(output["valid"])
            self.assertTrue(output["signature_verified"])
            self.assertEqual(output["approval_state"], "approved")
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["recommended_next_action"], "use_operator_approval_evidence")
            self.assertEqual(output["target_checksum"], OPERATOR_APPROVAL_TARGET)
            self.assertEqual(output["approval_checksum"], checksum_json(packet))
            self.assertEqual(output["purpose"], "start_governed_execution")
            self.assertEqual(output["operator_id"], "operator@example.test")
            self.assertEqual(output["key_id"], "local-key-1")
            self.assertEqual(output["side_effects"], ["operator_approval_audit_appended"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertIn("approval_not_execution_authority", output["limitations"])
            self.assertIn("audit_record", output)

            records = audit_records(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["event"], "operator_approval_verification")
            self.assertEqual(records[0]["operator_id"], "operator@example.test")
            self.assertEqual(records[0]["key_id"], "local-key-1")
            self.assertEqual(records[0]["approval_checksum"], checksum_json(packet))
            self.assertTrue(records[0]["signature_verified"])
            self.assertIn("checked_at", records[0])
            replay_result, replay = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["operator_approval_verification"], 1)

    def test_verify_operator_approval_can_read_secret_from_named_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            approval_path, _packet = write_operator_approval(Path(tmp) / "operator-approval.json")
            env = os.environ.copy()
            env.pop("CADENCE_OPERATOR_APPROVAL_SECRET", None)
            env["TEST_OPERATOR_APPROVAL_SECRET"] = OPERATOR_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    tmp,
                    "verify-operator-approval",
                    "--approval-file",
                    str(approval_path),
                    "--target-checksum",
                    OPERATOR_APPROVAL_TARGET,
                    "--purpose",
                    "start_governed_execution",
                    "--approval-secret-env",
                    "TEST_OPERATOR_APPROVAL_SECRET",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            output = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertNotIn(OPERATOR_APPROVAL_SECRET, result.stdout)
            self.assertNotIn("approval_secret", output)

    def test_verify_operator_approval_blocks_when_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            import codex_cadence.cli as cadence_cli

            approval_path, _packet = write_operator_approval(Path(tmp) / "operator-approval.json")
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "approval_file": str(approval_path),
                    "target_checksum": OPERATOR_APPROVAL_TARGET,
                    "purpose": "start_governed_execution",
                    "approval_secret": OPERATOR_APPROVAL_SECRET,
                    "approval_secret_env": "CADENCE_OPERATOR_APPROVAL_SECRET",
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.verify_operator_approval_command(args)

            self.assertEqual(code, 1)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertEqual(emitted[0]["approval_state"], "blocked")
            self.assertTrue(emitted[0]["signature_verified"])
            self.assertEqual(emitted[0]["side_effects"], [])
            self.assertIn("operator_approval_audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertEqual(audit_records(tmp), [])

    def test_verify_operator_approval_blocks_bad_identity_target_time_and_signature(self):
        now = datetime.now(timezone.utc)
        cases = [
            (
                "missing_operator",
                {"operator_id": ""},
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_operator_missing",
            ),
            (
                "weak_key",
                {"key_id": "x"},
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_key_id_weak",
            ),
            (
                "expired",
                {
                    "issued_at": iso_z(now - timedelta(hours=2)),
                    "expires_at": iso_z(now - timedelta(hours=1)),
                },
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_expired",
            ),
            (
                "future_issued",
                {
                    "issued_at": iso_z(now + timedelta(hours=1)),
                    "expires_at": iso_z(now + timedelta(hours=2)),
                },
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_issued_in_future",
            ),
            (
                "too_long_window",
                {
                    "issued_at": iso_z(now - timedelta(minutes=1)),
                    "expires_at": iso_z(now + timedelta(hours=2)),
                },
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_window_too_long",
            ),
            (
                "wrong_purpose",
                {},
                OPERATOR_APPROVAL_TARGET,
                "real_executor_invocation",
                "operator_approval_purpose_mismatch",
            ),
            (
                "wrong_target",
                {},
                "sha256:" + "c" * 64,
                "start_governed_execution",
                "operator_approval_target_mismatch",
            ),
            (
                "bad_signature",
                {"signature": "hmac-sha256:" + "0" * 64},
                OPERATOR_APPROVAL_TARGET,
                "start_governed_execution",
                "operator_approval_signature_invalid",
            ),
        ]
        for name, overrides, target_checksum, purpose, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    approval_path, _packet = write_operator_approval(Path(tmp) / "operator-approval.json", **overrides)

                    result, output = run_cli(
                        tmp,
                        "verify-operator-approval",
                        "--approval-file",
                        str(approval_path),
                        "--target-checksum",
                        target_checksum,
                        "--purpose",
                        purpose,
                        "--approval-secret",
                        OPERATOR_APPROVAL_SECRET,
                    )

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(output["schema_version"], "operator-approval-verification.v1")
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["approval_state"], "blocked")
                    self.assertFalse(output["executor_started"])
                    self.assertFalse(output["epoch_started"])
                    self.assertFalse(output["pr_action_started"])
                    if expected_code == "operator_approval_signature_invalid":
                        self.assertFalse(output["signature_verified"])
                    else:
                        self.assertTrue(output["signature_verified"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(audit_records(tmp), [])

    def test_operator_approval_core_verifier_rejects_unsupported_purpose(self):
        packet = operator_approval_packet(purpose="custom_out_of_protocol_action")

        output = build_operator_approval_verification_packet(
            approval=packet,
            approval_file=Path("operator-approval.json"),
            expected_target_checksum=OPERATOR_APPROVAL_TARGET,
            expected_purpose="custom_out_of_protocol_action",
            approval_secret=OPERATOR_APPROVAL_SECRET,
        )

        self.assertFalse(output["valid"])
        self.assertTrue(output["signature_verified"])
        self.assertIn("operator_approval_purpose_mismatch", {blocker["code"] for blocker in output["blockers"]})

    def test_operator_approval_fractional_checked_at_audit_replays(self):
        now = datetime(2999, 5, 22, 0, 0, 0, 750000, tzinfo=timezone.utc)
        packet = operator_approval_packet(
            issued_at="2999-05-22T00:00:00.500000Z",
            expires_at="2999-05-22T00:10:00.500000Z",
        )
        output = build_operator_approval_verification_packet(
            approval=packet,
            approval_file=Path("operator-approval.json"),
            expected_target_checksum=OPERATOR_APPROVAL_TARGET,
            expected_purpose="start_governed_execution",
            approval_secret=OPERATOR_APPROVAL_SECRET,
            now=now,
        )

        self.assertTrue(output["valid"], output["blockers"])
        self.assertEqual(output["checked_at"], "2999-05-22T00:00:00.750000Z")
        with tempfile.TemporaryDirectory() as tmp:
            append_audit_record(Path(tmp), operator_approval_verification_audit_record(output))

            result, replay = run_cli(tmp, "audit-replay")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["operator_approval_verification"], 1)

    def test_verify_operator_approval_rejects_unverifiable_secret_and_malformed_timestamps(self):
        now = datetime.now(timezone.utc)
        cases = [
            (
                {},
                [],
                "operator_approval_secret_missing",
            ),
            (
                {"issued_at": "not-a-timestamp", "expires_at": iso_z(now + timedelta(minutes=10))},
                ["--approval-secret", OPERATOR_APPROVAL_SECRET],
                "operator_approval_timestamp_invalid",
            ),
            (
                {"issued_at": iso_z(now - timedelta(minutes=1)), "expires_at": iso_z(now - timedelta(minutes=2))},
                ["--approval-secret", OPERATOR_APPROVAL_SECRET],
                "operator_approval_timestamp_invalid",
            ),
        ]
        for overrides, secret_args, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp:
                    approval_path, _packet = write_operator_approval(Path(tmp) / "operator-approval.json", **overrides)

                    result, output = run_cli(
                        tmp,
                        "verify-operator-approval",
                        "--approval-file",
                        str(approval_path),
                        "--target-checksum",
                        OPERATOR_APPROVAL_TARGET,
                        "--purpose",
                        "start_governed_execution",
                        *secret_args,
                    )

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(output["recommended_next_action"], "fix_operator_approval")
                    self.assertEqual(audit_records(tmp), [])

    def test_start_governed_execution_preserves_agent_proposal_allowance_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_packet = build_executor_task_packet(
                task={
                    "id": "agent-proposal-001",
                    "title": "Explore the next repo capability from local signals",
                    "summary": "Run a bounded proposal discovery task.",
                    "task_type": "discovery",
                    "bucket": "S",
                    "source": "agent_proposal",
                    "drivers": ["unknown_repo_area"],
                    "evidence": {"intent": "product_evolution"},
                    "requires_user_allowance": True,
                    "allowance": "elect",
                    "allowance_reason": "operator allowed proposal election",
                },
                snapshot=valid_snapshot(
                    repo="local/test",
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                repo_path=repo,
                allowed_paths=["README.md"],
                required_checks=[],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo, task_packet)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            active_epochs = list((Path(tmp) / "epochs" / "active").glob("*.json"))
            self.assertEqual(len(active_epochs), 1)
            epoch = json.loads(active_epochs[0].read_text(encoding="utf-8"))
            epoch_task = epoch["tasks"][0]
            self.assertEqual(epoch_task["source"], "agent_proposal")
            self.assertTrue(epoch_task["requires_user_allowance"])
            self.assertEqual(epoch_task["allowance"], "elect")
            self.assertEqual(epoch_task["allowance_reason"], "operator allowed proposal election")

    def test_start_governed_execution_binds_matching_work_ownership(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)
            ownership_path, _ownership = write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=current_branch(repo),
                head=current_head(repo),
                epoch_id=None,
                handoff_id=None,
            )

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
                "--ownership-target",
                "ownership-1",
                "--ownership-role",
                "implementer",
                "--ownership-claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["epoch_started"])
            self.assertEqual(output["ownership"]["id"], "ownership-1")
            self.assertEqual(output["ownership"]["epoch_id"], output["epoch_id"])
            self.assertIn("work_ownership_epoch_bound", output["side_effects"])
            updated_ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_ownership["epoch_id"], output["epoch_id"])
            self.assertEqual(updated_ownership["status"], "ACTIVE")
            records = audit_records(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["event"], "execution_start_decision")
            self.assertEqual(records[0]["ownership_id"], "ownership-1")
            self.assertEqual(records[0]["ownership_record_checksum"], checksum_json(output["ownership"]))
            replay_result, replay = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])

    def test_start_governed_execution_blocks_missing_mismatched_and_duplicate_work_ownership(self):
        cases = [
            ("ownership_record_missing", lambda tmp, repo, task_packet: None, "missing-ownership", {}),
            (
                "ownership_claimer_mismatch",
                lambda tmp, repo, task_packet: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=current_branch(repo),
                    head=current_head(repo),
                    claimer="other-agent",
                    epoch_id=None,
                    handoff_id=None,
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_role_mismatch",
                lambda tmp, repo, task_packet: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=current_branch(repo),
                    head=current_head(repo),
                    role="reviewer",
                    epoch_id=None,
                    handoff_id=None,
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_head_mismatch",
                lambda tmp, repo, task_packet: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=current_branch(repo),
                    head="0" * 40,
                    epoch_id=None,
                    handoff_id=None,
                ),
                "ownership-1",
                {},
            ),
            (
                "duplicate_active_ownership",
                lambda tmp, repo, task_packet: (
                    write_work_ownership(
                        tmp,
                        "ownership-1",
                        task_id=task_packet["task"]["id"],
                        candidate_id=task_packet["task"]["id"],
                        branch=current_branch(repo),
                        head=current_head(repo),
                        epoch_id=None,
                        handoff_id=None,
                    ),
                    write_work_ownership(
                        tmp,
                        "ownership-2",
                        task_id=task_packet["task"]["id"],
                        candidate_id=task_packet["task"]["id"],
                        branch=current_branch(repo),
                        head=current_head(repo),
                        claimer="other-agent",
                        epoch_id=None,
                        handoff_id=None,
                    ),
                ),
                "ownership-1",
                {},
            ),
        ]
        for expected_code, seed_ownership, target, overrides in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)
                    seed_ownership(tmp, repo, task_packet)
                    args = [
                        "start-governed-execution",
                        "--task-file",
                        str(task_path),
                        "--approval-token",
                        approval_token,
                        "--ownership-target",
                        target,
                        "--ownership-role",
                        "implementer",
                        "--ownership-claimer",
                        "test-agent",
                    ]
                    for key, value in overrides.items():
                        args.extend([key, value])

                    result, output = run_cli(tmp, *args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["epoch_started"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])
                    self.assertEqual(audit_records(tmp), [])

    def test_start_governed_execution_existing_blockers_precede_work_ownership_validation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, _approval_token = write_governed_execution_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--ownership-target",
                "missing-ownership",
                "--ownership-role",
                "implementer",
                "--ownership-claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["epoch_started"])
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("operator_approval_missing", codes)
            self.assertNotIn("ownership_record_missing", codes)

    def test_start_governed_execution_rolls_back_ownership_binding_when_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)
            ownership_path, ownership_before = write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=current_branch(repo),
                head=current_head(repo),
                epoch_id=None,
                handoff_id=None,
            )
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "task_file": str(task_path),
                    "approval_token": approval_token,
                    "cwd": None,
                    "ownership_target": "ownership-1",
                    "ownership_role": "implementer",
                    "ownership_claimer": "test-agent",
                    "ownership_max_age_minutes": 5,
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.start_governed_execution_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertFalse(emitted[0]["epoch_started"])
            self.assertIn("audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertIn("work_ownership_epoch_binding_rollback", emitted[0]["side_effects"])
            self.assertEqual(json.loads(ownership_path.read_text(encoding="utf-8")), ownership_before)
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_missing_approval(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, _approval_token = write_governed_execution_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(output["schema_version"], "execution-start.v1")
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["approval_state"], "missing")
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertIn("operator_approval_missing", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_approval_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)

            def assert_approval_mismatch(token):
                result, output = run_cli(
                    tmp,
                    "start-governed-execution",
                    "--task-file",
                    str(task_path),
                    "--approval-token",
                    token,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(output["valid"])
                self.assertFalse(output["epoch_started"])
                self.assertFalse(output["executor_started"])
                self.assertEqual(output["approval_state"], "mismatch")
                self.assertEqual(output["recommended_next_action"], "approve_executor_task")
                self.assertIn("operator_approval_mismatch", {blocker["code"] for blocker in output["blockers"]})
                self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

            assert_approval_mismatch("approve-executor-task:sha256:" + "0" * 64)

            tampered_packet = dict(task_packet)
            tampered_packet["task"] = dict(task_packet["task"])
            tampered_packet["task"]["title"] = "Tampered approved task title"
            task_path.write_text(json.dumps(tampered_packet), encoding="utf-8")

            assert_approval_mismatch(approval_token)

    def test_start_governed_execution_blocks_stale_head(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            (Path(repo) / "README.md").write_text("changed after task approval\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "advance head")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_task")
            self.assertIn("repo_head_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_branch_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            git(repo, "switch", "-c", "feature/task-8")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_task")
            self.assertIn("repo_branch_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            (Path(repo) / "README.md").write_text("dirty after task approval\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_task")
            self.assertIn("dirty_worktree", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_missing_repo_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_packet = governed_execution_task_packet(tmp, repo)
            missing_repo = Path(tmp) / "missing-repo"
            task_packet["repo"]["path"] = str(missing_repo)
            task_packet["snapshot"]["cwd"] = str(missing_repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo, task_packet)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "recreate_executor_task")
            self.assertIn("repo_inspection_failed", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_non_drive_brake(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "clear_brake")
            self.assertIn("brake_not_drive", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_active_epoch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)
            write_active_epoch(
                tmp,
                "existing-epoch",
                valid_snapshot(
                    repo="local/test",
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                tasks=[{"id": "existing-task", "task_type": "execution"}],
            )

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["task_checksum"], checksum_json(task_packet))
            self.assertEqual(output["recommended_next_action"], "close_or_fail_active_epoch")
            self.assertIn("active_epoch_exists", {blocker["code"] for blocker in output["blockers"]})
            active_epochs = list((Path(tmp) / "epochs" / "active").glob("*.json"))
            self.assertEqual(len(active_epochs), 1)

    def test_start_governed_execution_blocks_malformed_active_epoch_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            active_path = Path(tmp) / "epochs" / "active" / "bad-epoch.json"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(json.dumps(["not", "an", "epoch"]), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "close_or_fail_active_epoch")
            self.assertIn("active_epoch_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue(active_path.exists())

    def test_start_governed_execution_blocks_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps({"schema_version": "generic-executor-task.v1"}), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                "approve-executor-task:sha256:" + "0" * 64,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_task_packet")
            self.assertIn("executor_task_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_malformed_task_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_packet = governed_execution_task_packet(tmp, repo)
            task_packet["task"]["evidence"] = "docs/roadmap.md"
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo, task_packet)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_task_packet")
            self.assertIn("executor_task_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertIn("task.evidence", output["reason"])
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_blocks_malformed_brake_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            (Path(tmp) / "brake.json").write_text(json.dumps({"status": "WARP"}), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["recommended_next_action"], "inspect_runtime_state")
            self.assertIn("brake_state_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_rechecks_repo_inside_runtime_lock(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            emitted = []
            lock_seen = False
            original_snapshot_repo = cadence_cli.snapshot_repo

            def dirty_repo_after_lock(repo_path, **kwargs):
                nonlocal lock_seen
                lock_seen = (Path(tmp) / "locks" / "runtime.lock").exists()
                (Path(repo) / "README.md").write_text("dirty during locked recheck\n", encoding="utf-8")
                return original_snapshot_repo(repo_path, **kwargs)

            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "task_file": str(task_path),
                    "approval_token": approval_token,
                    "cwd": None,
                },
            )()

            with mock.patch.object(cadence_cli, "snapshot_repo", dirty_repo_after_lock):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.start_governed_execution_command(args)

            self.assertEqual(code, 2)
            self.assertTrue(lock_seen)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertFalse(emitted[0]["epoch_started"])
            self.assertIn("dirty_worktree", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_rolls_back_epoch_when_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            task_path, _task_packet, approval_token = write_governed_execution_task(tmp, repo)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "task_file": str(task_path),
                    "approval_token": approval_token,
                    "cwd": None,
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.start_governed_execution_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertFalse(emitted[0]["epoch_started"])
            self.assertFalse(emitted[0]["executor_started"])
            self.assertEqual(emitted[0]["recommended_next_action"], "inspect_runtime_state")
            self.assertNotIn("audit_record", emitted[0])
            self.assertIn("audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_start_governed_execution_records_replayable_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, task_packet, approval_token = write_governed_execution_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                approval_token,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "execution_start_decision")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "execution_start_decision")
            self.assertEqual(record["action"], "handoff_to_executor")
            self.assertTrue(record["valid"])
            self.assertTrue(record["epoch_started"])
            self.assertFalse(record["executor_started"])
            self.assertEqual(record["task_file"], str(task_path))
            self.assertEqual(record["task_checksum"], checksum_json(task_packet))
            replay_result, replay = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["records_valid"], 1)
            self.assertEqual(replay["events_by_type"]["execution_start_decision"], 1)

    def test_start_governed_execution_blocked_decision_does_not_append_success_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _task_packet, _approval_token = write_governed_execution_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "start-governed-execution",
                "--task-file",
                str(task_path),
                "--approval-token",
                "approve-executor-task:sha256:" + "0" * 64,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["epoch_started"])
            self.assertNotIn("audit_record", output)
            self.assertFalse((Path(tmp) / "audit" / "events.jsonl").exists())
            replay_result, replay = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["records_valid"], 0)
            self.assertEqual(replay["events_by_type"], {})

    def write_valid_executor_invocation_plan_inputs(
        self,
        tmp,
        repo,
        *,
        command="python -m unittest tests.test_cadence",
        timeout_seconds=300,
        task_mutator=None,
        readiness_task_file_arg=None,
        readiness_cwd=None,
    ):
        status_result, _status = run_cli(tmp, "status")
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        task_path, task_packet, _approval_token = write_governed_execution_task(tmp, repo)
        task_packet["expected_output"]["evidence_path"] = str(Path(tmp) / "executor-results" / "executor-result.json")
        if task_mutator is not None:
            task_mutator(task_packet)
        task_path.write_text(json.dumps(task_packet), encoding="utf-8")
        task_checksum = checksum_json(task_packet)
        write_active_epoch(
            tmp,
            "epoch-1",
            valid_snapshot(
                repo="local/test",
                cwd=str(Path(repo).resolve()),
                branch=current_branch(repo),
                head=current_head(repo),
            ),
            tasks=[
                {
                    "id": task_packet["task"]["id"],
                    "task_type": "execution",
                    "executor_task_checksum": task_checksum,
                }
            ],
        )
        write_work_ownership(
            tmp,
            "ownership-1",
            task_id=task_packet["task"]["id"],
            candidate_id=task_packet["task"]["id"],
            branch=current_branch(repo),
            head=current_head(repo),
            epoch_id="epoch-1",
            handoff_id=None,
        )

        readiness_args = [
            "executor-invocation-readiness",
            "--cwd",
            repo,
            "--task-file",
            str(readiness_task_file_arg if readiness_task_file_arg is not None else task_path),
            "--epoch-id",
            "epoch-1",
            "--ownership-target",
            "ownership-1",
            "--expected-result-path",
            task_packet["expected_output"]["evidence_path"],
        ]
        if readiness_cwd is None:
            readiness_result, readiness_packet = run_cli(tmp, *readiness_args)
        else:
            readiness_result, readiness_packet = run_cli_from(readiness_cwd, tmp, *readiness_args)
        self.assertEqual(readiness_result.returncode, 0, readiness_result.stderr)
        readiness_path = Path(tmp) / "executor-invocation-readiness.json"
        readiness_path.write_text(json.dumps(readiness_packet), encoding="utf-8")

        audit_seed = build_operator_approval_verification_packet(
            approval=operator_approval_packet(),
            approval_file=Path(tmp) / "seed-operator-approval.json",
            expected_target_checksum=OPERATOR_APPROVAL_TARGET,
            expected_purpose="start_governed_execution",
            approval_secret=OPERATOR_APPROVAL_SECRET,
        )
        append_audit_record(Path(tmp), operator_approval_verification_audit_record(audit_seed))
        audit_result, audit_replay = run_cli(tmp, "audit-replay")
        self.assertEqual(audit_result.returncode, 0, audit_result.stderr)
        self.assertTrue(audit_replay["valid"])

        adapter_path, adapter_packet = write_executor_invocation_adapter(
            Path(tmp) / "executor-adapter.json",
            command_template=command,
        )
        rollback_path, rollback_packet = write_executor_invocation_rollback(
            Path(tmp) / "executor-rollback.json",
            task_packet,
        )
        environment_allowlist = ["PATH", "PYTHONPATH"]
        target = executor_invocation_target_descriptor(
            readiness_packet=readiness_packet,
            adapter_packet=adapter_packet,
            rollback_packet=rollback_packet,
            command=command,
            cwd=repo,
            expected_result_path=task_packet["expected_output"]["evidence_path"],
            environment_allowlist=environment_allowlist,
            timeout_seconds=timeout_seconds,
            audit_chain_head=audit_replay["chain_head"],
        )
        approval_path, approval_packet = write_operator_approval(
            Path(tmp) / "executor-invocation-approval.json",
            target_checksum=checksum_json(target),
            purpose="real_executor_invocation",
        )
        return {
            "task_packet": task_packet,
            "readiness_path": readiness_path,
            "readiness_packet": readiness_packet,
            "adapter_path": adapter_path,
            "adapter_packet": adapter_packet,
            "rollback_path": rollback_path,
            "rollback_packet": rollback_packet,
            "approval_path": approval_path,
            "approval_packet": approval_packet,
            "command": command,
            "environment_allowlist": environment_allowlist,
            "timeout_seconds": timeout_seconds,
            "target": target,
            "target_checksum": checksum_json(target),
            "audit_replay": audit_replay,
        }

    def run_executor_invocation_plan_cli(self, tmp, repo, inputs, **overrides):
        return run_cli(
            tmp,
            "executor-invocation-plan",
            "--cwd",
            str(overrides.get("cwd", repo)),
            "--readiness-file",
            str(inputs["readiness_path"]),
            "--approval-file",
            str(inputs["approval_path"]),
            "--approval-secret",
            OPERATOR_APPROVAL_SECRET,
            "--adapter-file",
            str(inputs["adapter_path"]),
            "--rollback-file",
            str(overrides.get("rollback_file", inputs["rollback_path"])),
            "--command",
            overrides.get("command", inputs["command"]),
            "--env-allow",
            "PATH",
            "--env-allow",
            "PYTHONPATH",
            "--timeout-seconds",
            str(overrides.get("timeout_seconds", inputs["timeout_seconds"])),
            "--expected-result-path",
            str(overrides.get("expected_result_path", inputs["task_packet"]["expected_output"]["evidence_path"])),
        )

    def write_real_executor_invocation_plan(
        self,
        tmp,
        repo,
        *,
        timeout_seconds=300,
        touch_repo=False,
        write_result=True,
        include_materialized_change_evidence=False,
        materialized_change_evidence=None,
        sleep_seconds=None,
        delete_git=False,
        create_branch=None,
        delete_branch=None,
        retarget_branch=None,
        resulting_head=None,
        stdout_text=None,
        stderr_text=None,
        invalid_output=False,
        files_changed=None,
        result_status="succeeded",
        task_mutator=None,
        readiness_task_file_arg=None,
        readiness_cwd=None,
    ):
        script_path = real_executor_script(Path(tmp) / "real-executor.py")
        config_path = Path(tmp) / "real-executor-config.json"
        command = f"{command_quote(sys.executable)} {command_quote(script_path)} {command_quote(config_path)}"
        inputs = self.write_valid_executor_invocation_plan_inputs(
            tmp,
            repo,
            command=command,
            timeout_seconds=timeout_seconds,
            task_mutator=task_mutator,
            readiness_task_file_arg=readiness_task_file_arg,
            readiness_cwd=readiness_cwd,
        )
        config = {
            "command": command,
            "create_branch": create_branch,
            "delete_branch": delete_branch,
            "exit_code": 0,
            "files_changed": files_changed,
            "include_materialized_change_evidence": include_materialized_change_evidence,
            "invalid_output": invalid_output,
            "materialized_change_evidence": materialized_change_evidence,
            "repo_head": current_head(repo),
            "repo_path": str(Path(repo).resolve()),
            "result_path": inputs["task_packet"]["expected_output"]["evidence_path"],
            "resulting_head": resulting_head,
            "retarget_branch": retarget_branch,
            "required_checks": inputs["task_packet"]["required_checks"],
            "sleep_seconds": sleep_seconds,
            "status": result_status,
            "stderr_text": stderr_text,
            "stdout_text": stdout_text,
            "task_id": inputs["task_packet"]["task"]["id"],
            "touch_repo": touch_repo,
            "write_result": write_result,
            "delete_git": delete_git,
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        plan_result, plan = self.run_executor_invocation_plan_cli(tmp, repo, inputs)
        self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
        plan_path = Path(tmp) / "executor-invocation-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return inputs, plan_path, plan

    def write_controlled_loop_tick_chain(self, tmp, repo):
        inputs, plan_path, plan = self.write_real_executor_invocation_plan(tmp, repo)
        task_packet = inputs["task_packet"]
        task_path = Path(tmp) / "executor-task.json"
        task_checksum = checksum_json(task_packet)
        loop_tick = {
            "protocol_version": "v1",
            "packet": "loop_tick",
            "tick_id": "loop-tick-controlled-1",
            "mode": "single_tick",
            "read_only": True,
            "executor_started": False,
            "epoch_started": False,
            "pr_action_started": False,
            "operator_confirmation_required": True,
            "executor_contract_required": False,
            "recommended_next_action": "approve_executor_task",
            "reason": "executor task packet emitted for operator approval",
            "brake": {"status": "DRIVE", "reason": None, "scope": "global"},
            "cadence": {"state": "PLAY_ON", "can_start_work": True},
            "snapshot": task_packet["snapshot"],
            "candidate_discovery": {"elected_next": [task_packet["task"]]},
            "elected_next": [task_packet["task"]],
            "executor_task": task_packet,
            "policy": {},
            "limitations": ["executor_not_started"],
        }
        loop_tick_path = Path(tmp) / "loop-tick.json"
        loop_tick_path.write_text(json.dumps(loop_tick), encoding="utf-8")
        ownership_path = Path(tmp) / "work-ownership" / "active" / "ownership-1.json"
        ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
        execution_start = {
            "protocol_version": "v1",
            "schema_version": "execution-start.v1",
            "packet": "execution_start",
            "valid": True,
            "blockers": [],
            "epoch_started": True,
            "executor_started": False,
            "pr_action_started": False,
            "recommended_next_action": "handoff_to_executor",
            "reason": "approved executor task started a governed epoch",
            "approval_state": "approved",
            "task_checksum": task_checksum,
            "task_id": task_packet["task"]["id"],
            "task_file": str(task_path),
            "epoch_id": "epoch-1",
            "repo": {
                "name": task_packet["repo"]["name"],
                "path": task_packet["repo"]["path"],
                "branch": task_packet["repo"]["branch"],
                "head": task_packet["repo"]["head"],
            },
            "ownership": ownership,
            "side_effects": ["epoch_started"],
            "limitations": ["executor_not_started"],
        }
        execution_start_path = Path(tmp) / "execution-start.json"
        execution_start_path.write_text(json.dumps(execution_start), encoding="utf-8")

        invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)
        self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
        invocation_path = Path(invocation_output["record_file"])
        result_path = Path(invocation_output["result_file"])
        snapshot_after_path = Path(tmp) / "snapshot-after.json"
        snapshot_after_path.write_text(
            json.dumps(closeout_snapshot(repo, id="snapshot-after-controlled", captured_at="2999-05-22T00:10:00Z")),
            encoding="utf-8",
        )

        closeout_result, closeout_output = run_cli(
            tmp,
            "closeout-executor-result",
            "--epoch-id",
            "epoch-1",
            "--task-file",
            str(task_path),
            "--result-file",
            str(result_path),
            "--snapshot-after-file",
            str(snapshot_after_path),
            "--real-invocation-file",
            str(invocation_path),
            "--cwd",
            repo,
        )
        self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
        closeout_path = Path(tmp) / "executor-closeout.json"
        closeout_path.write_text(json.dumps(closeout_output), encoding="utf-8")

        return {
            "loop_tick_path": loop_tick_path,
            "task_path": task_path,
            "execution_start_path": execution_start_path,
            "readiness_path": inputs["readiness_path"],
            "plan_path": plan_path,
            "invocation_path": invocation_path,
            "result_path": result_path,
            "snapshot_after_path": snapshot_after_path,
            "closeout_path": closeout_path,
            "task_packet": task_packet,
            "plan": plan,
            "invocation_before_closeout": invocation_output,
            "closeout": closeout_output,
        }

    def controlled_loop_tick_git_pr_plan(self, **overrides):
        git_pr_plan = {
            "protocol_version": "v1",
            "schema_version": "git-pr-plan.v1",
            "packet": "git_pr_plan",
            "plan_id": "embedded-plan",
            "ready_to_review": True,
            "decision": "ready",
            "recommended_next_action": "review_git_pr_plan",
            "dry_run": True,
            "operator_confirmation_required": True,
            "side_effects": [],
            "approval_state": "not_approved",
            "execution_authority": "none",
            "merge_readiness": "not_evaluated",
            "proposed_branch": "codex/task-23-controlled-single-tick-run-packet",
            "proposed_commit_message": "Add controlled single tick run packet",
            "proposed_pr_title": "[codex] Add controlled single tick run packet",
            "proposed_pr_body": "## Summary\n- Add controlled tick\n\n## Validation\n- tests",
        }
        git_pr_plan.update(overrides)
        return git_pr_plan

    def write_controlled_loop_tick_git_pr_plan_anchor(self, tmp, chain, git_pr_plan, *, filename="git-pr-plan.json"):
        git_pr_plan_path = Path(tmp) / filename
        git_pr_plan_path.write_text(json.dumps(git_pr_plan), encoding="utf-8")
        invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
        closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
        closeout["git_pr_plan"] = git_pr_plan
        closeout_core_packet = {
            key: value
            for key, value in closeout.items()
            if key not in {"audit_record", "run_record", "real_invocation"}
        }
        epoch_closeout_checksum = checksum_json(closeout_core_packet)
        invocation["epoch_closeout_checksum"] = epoch_closeout_checksum
        closeout["real_invocation"]["epoch_closeout_checksum"] = epoch_closeout_checksum
        closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
        chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
        chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
        return git_pr_plan_path

    def run_controlled_loop_tick_cli(self, tmp, chain, **overrides):
        cwd = overrides.pop("cwd", None)
        values = {
            "loop_tick_file": chain["loop_tick_path"],
            "task_file": chain["task_path"],
            "execution_start_file": chain["execution_start_path"],
            "readiness_file": chain["readiness_path"],
            "invocation_plan_file": chain["plan_path"],
            "real_invocation_file": chain["invocation_path"],
            "result_file": chain["result_path"],
            "snapshot_after_file": chain["snapshot_after_path"],
            "closeout_file": chain["closeout_path"],
            "git_pr_plan_file": None,
        }
        values.update(overrides)
        args = [
            "controlled-loop-tick",
            "--loop-tick-file",
            str(values["loop_tick_file"]),
            "--task-file",
            str(values["task_file"]),
            "--execution-start-file",
            str(values["execution_start_file"]),
            "--readiness-file",
            str(values["readiness_file"]),
            "--invocation-plan-file",
            str(values["invocation_plan_file"]),
            "--real-invocation-file",
            str(values["real_invocation_file"]),
            "--result-file",
            str(values["result_file"]),
            "--snapshot-after-file",
            str(values["snapshot_after_file"]),
            "--closeout-file",
            str(values["closeout_file"]),
        ]
        if values["git_pr_plan_file"] is not None:
            args.extend(["--git-pr-plan-file", str(values["git_pr_plan_file"])])
        if cwd is not None:
            return run_cli_from(cwd, tmp, *args)
        return run_cli(tmp, *args)

    def write_controlled_loop_run_summary_chain(self, tmp, repo, *, emit_git_pr_plan=False):
        marker = Path(repo) / "notes.py"
        marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
        git(repo, "add", "notes.py")
        git(repo, "commit", "-m", "add repo health marker")

        result_path = Path(tmp) / "executor-results" / "executor-result.json"
        loop_plan_args = [
            "loop-run-plan",
            "--cwd",
            repo,
            "--repo",
            "local/test",
            "--intent",
            "repo_health",
            "--emit-executor-task",
            "--allowed-path",
            "notes.py",
            "--required-check",
            "python -m unittest tests.test_cadence",
            "--executor-evidence-path",
            str(result_path),
        ]
        if emit_git_pr_plan:
            loop_plan_args.extend(["--allowed-path", "README.md"])
        loop_plan_result, loop_plan = run_cli(
            tmp,
            *loop_plan_args,
        )
        self.assertEqual(loop_plan_result.returncode, 0, loop_plan_result.stderr)
        loop_run_plan_path = Path(tmp) / "loop-run-plan.json"
        loop_run_plan_path.write_text(json.dumps(loop_plan), encoding="utf-8")
        loop_tick_path = Path(tmp) / "loop-tick.json"
        loop_tick_path.write_text(json.dumps(loop_plan["loop_tick"]), encoding="utf-8")
        task_packet = loop_plan["executor_task"]
        task_path = Path(tmp) / "executor-task.json"
        task_path.write_text(json.dumps(task_packet), encoding="utf-8")
        task_checksum = checksum_json(task_packet)
        epoch_id = "epoch-1"
        write_active_epoch(
            tmp,
            epoch_id,
            task_packet["snapshot"],
            tasks=[
                {
                    "id": task_packet["task"]["id"],
                    "task_type": "execution",
                    "executor_task_checksum": task_checksum,
                }
            ],
        )
        execution_start = {
            "protocol_version": "v1",
            "schema_version": "execution-start.v1",
            "packet": "execution_start",
            "valid": True,
            "blockers": [],
            "epoch_started": True,
            "executor_started": False,
            "pr_action_started": False,
            "recommended_next_action": "handoff_to_executor",
            "reason": "approved executor task started a governed epoch",
            "approval_state": "approved",
            "task_checksum": task_checksum,
            "task_id": task_packet["task"]["id"],
            "task_file": str(task_path),
            "epoch_id": epoch_id,
            "repo": {
                "name": task_packet["repo"]["name"],
                "path": task_packet["repo"]["path"],
                "branch": task_packet["repo"]["branch"],
                "head": task_packet["repo"]["head"],
            },
            "side_effects": ["epoch_started"],
            "limitations": ["executor_not_started"],
        }
        execution_start["audit_record"] = append_audit_record(
            Path(tmp),
            execution_start_audit_record(execution_start),
        )
        execution_start_path = Path(tmp) / "execution-start.json"
        execution_start_path.write_text(json.dumps(execution_start), encoding="utf-8")

        controlled_start_result, controlled_start = run_cli(
            tmp,
            "controlled-loop-start",
            "--loop-run-plan-file",
            str(loop_run_plan_path),
            "--execution-start-file",
            str(execution_start_path),
        )
        self.assertEqual(controlled_start_result.returncode, 0, controlled_start_result.stderr)
        controlled_start_path = Path(tmp) / "controlled-loop-start.json"
        controlled_start_path.write_text(json.dumps(controlled_start), encoding="utf-8")

        write_work_ownership(
            tmp,
            "ownership-1",
            task_id=task_packet["task"]["id"],
            candidate_id=task_packet["task"]["id"],
            branch=current_branch(repo),
            head=current_head(repo),
            epoch_id=execution_start["epoch_id"],
            handoff_id=None,
        )
        readiness_result, readiness = run_cli(
            tmp,
            "executor-invocation-readiness",
            "--cwd",
            repo,
            "--task-file",
            str(task_path),
            "--epoch-id",
            execution_start["epoch_id"],
            "--ownership-target",
            "ownership-1",
            "--expected-result-path",
            str(result_path),
        )
        self.assertEqual(readiness_result.returncode, 0, readiness_result.stderr)
        readiness_path = Path(tmp) / "executor-invocation-readiness.json"
        readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

        audit_result, audit_replay = run_cli(tmp, "audit-replay")
        self.assertEqual(audit_result.returncode, 0, audit_result.stderr)
        self.assertTrue(audit_replay["valid"])
        script_path = real_executor_script(Path(tmp) / "real-executor.py")
        config_path = Path(tmp) / "real-executor-config.json"
        command = f"{command_quote(sys.executable)} {command_quote(script_path)} {command_quote(config_path)}"
        config_path.write_text(
            json.dumps(
                {
                    "command": command,
                    "exit_code": 0,
                    "files_changed": ["README.md"] if emit_git_pr_plan else None,
                    "include_materialized_change_evidence": emit_git_pr_plan,
                    "invalid_output": False,
                    "materialized_change_evidence": None,
                    "repo_head": current_head(repo),
                    "repo_path": str(Path(repo).resolve()),
                    "result_path": str(result_path),
                    "resulting_head": None,
                    "required_checks": task_packet["required_checks"],
                    "sleep_seconds": None,
                    "status": "succeeded",
                    "stderr_text": None,
                    "stdout_text": None,
                    "task_id": task_packet["task"]["id"],
                    "touch_repo": emit_git_pr_plan,
                    "write_result": True,
                    "delete_git": False,
                }
            ),
            encoding="utf-8",
        )
        adapter_path, adapter_packet = write_executor_invocation_adapter(
            Path(tmp) / "executor-adapter.json",
            command_template=command,
        )
        rollback_path, rollback_packet = write_executor_invocation_rollback(Path(tmp) / "executor-rollback.json", task_packet)
        environment_allowlist = ["PATH", "PYTHONPATH"]
        target = executor_invocation_target_descriptor(
            readiness_packet=readiness,
            adapter_packet=adapter_packet,
            rollback_packet=rollback_packet,
            command=command,
            cwd=repo,
            expected_result_path=result_path,
            environment_allowlist=environment_allowlist,
            timeout_seconds=300,
            audit_chain_head=audit_replay["chain_head"],
        )
        approval_path, _approval = write_operator_approval(
            Path(tmp) / "executor-invocation-approval.json",
            target_checksum=checksum_json(target),
            purpose="real_executor_invocation",
        )
        invocation_plan_result, invocation_plan = run_cli(
            tmp,
            "executor-invocation-plan",
            "--cwd",
            repo,
            "--readiness-file",
            str(readiness_path),
            "--approval-file",
            str(approval_path),
            "--approval-secret",
            OPERATOR_APPROVAL_SECRET,
            "--adapter-file",
            str(adapter_path),
            "--rollback-file",
            str(rollback_path),
            "--command",
            command,
            "--env-allow",
            "PATH",
            "--env-allow",
            "PYTHONPATH",
            "--timeout-seconds",
            "300",
            "--expected-result-path",
            str(result_path),
        )
        self.assertEqual(invocation_plan_result.returncode, 0, invocation_plan_result.stderr)
        invocation_plan_path = Path(tmp) / "executor-invocation-plan.json"
        invocation_plan_path.write_text(json.dumps(invocation_plan), encoding="utf-8")

        controlled_plan_result, controlled_plan = run_cli(
            tmp,
            "controlled-loop-invocation-plan",
            "--controlled-loop-start-file",
            str(controlled_start_path),
            "--readiness-file",
            str(readiness_path),
            "--invocation-plan-file",
            str(invocation_plan_path),
        )
        self.assertEqual(controlled_plan_result.returncode, 0, controlled_plan_result.stderr)
        controlled_plan_path = Path(tmp) / "controlled-loop-invocation-plan.json"
        controlled_plan_path.write_text(json.dumps(controlled_plan), encoding="utf-8")

        invocation_result, invocation = self.run_invoke_real_executor_cli(
            tmp,
            invocation_plan_path,
            side_effect_mode="materialized_changes" if emit_git_pr_plan else "evidence_only",
        )
        self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
        invocation_path = Path(invocation["record_file"])
        snapshot_after_path = Path(tmp) / "snapshot-after.json"
        snapshot_after_path.write_text(
            json.dumps(
                closeout_snapshot(
                    repo,
                    id="snapshot-after-run-summary",
                    captured_at="2999-05-22T00:30:00Z",
                    dirty_worktree=emit_git_pr_plan,
                    repo_confidence="low" if emit_git_pr_plan else "high",
                    repo_confidence_drivers=["dirty_worktree"] if emit_git_pr_plan else [],
                )
            ),
            encoding="utf-8",
        )

        controlled_real_result, controlled_real = run_cli(
            tmp,
            "controlled-loop-real-invocation",
            "--controlled-invocation-plan-file",
            str(controlled_plan_path),
            "--real-invocation-file",
            str(invocation_path),
        )
        self.assertEqual(controlled_real_result.returncode, 0, controlled_real_result.stderr)
        controlled_real_path = Path(tmp) / "controlled-loop-real-invocation.json"
        controlled_real_path.write_text(json.dumps(controlled_real), encoding="utf-8")

        closeout_args = [
            "closeout-executor-result",
            "--epoch-id",
            execution_start["epoch_id"],
            "--task-file",
            str(task_path),
            "--result-file",
            str(result_path),
            "--snapshot-after-file",
            str(snapshot_after_path),
            "--real-invocation-file",
            str(invocation_path),
            "--cwd",
            repo,
        ]
        if emit_git_pr_plan:
            closeout_args.append("--emit-git-pr-plan")
        closeout_result, closeout = run_cli(tmp, *closeout_args)
        self.assertEqual(closeout_result.returncode, 0, closeout_result.stdout or closeout_result.stderr)
        closeout_path = Path(tmp) / "executor-closeout.json"
        closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
        git_pr_plan_path = None
        if isinstance(closeout.get("git_pr_plan"), dict):
            git_pr_plan_path = Path(tmp) / "git-pr-plan.json"
            git_pr_plan_path.write_text(json.dumps(closeout["git_pr_plan"]), encoding="utf-8")

        controlled_closeout_result, controlled_closeout = run_cli(
            tmp,
            "controlled-loop-closeout",
            "--controlled-real-invocation-file",
            str(controlled_real_path),
            "--closeout-file",
            str(closeout_path),
        )
        self.assertEqual(controlled_closeout_result.returncode, 0, controlled_closeout_result.stderr)
        controlled_closeout_path = Path(tmp) / "controlled-loop-closeout.json"
        controlled_closeout_path.write_text(json.dumps(controlled_closeout), encoding="utf-8")

        controlled_tick_result, controlled_tick = self.run_controlled_loop_tick_cli(
            tmp,
            {
                "loop_tick_path": loop_tick_path,
                "task_path": task_path,
                "execution_start_path": execution_start_path,
                "readiness_path": readiness_path,
                "plan_path": invocation_plan_path,
                "invocation_path": invocation_path,
                "result_path": result_path,
                "snapshot_after_path": snapshot_after_path,
                "closeout_path": closeout_path,
            },
        )
        self.assertEqual(controlled_tick_result.returncode, 0, controlled_tick_result.stderr)
        controlled_tick_path = Path(tmp) / "controlled-loop-tick.json"
        controlled_tick_path.write_text(json.dumps(controlled_tick), encoding="utf-8")

        return {
            "loop_run_plan_path": loop_run_plan_path,
            "controlled_start_path": controlled_start_path,
            "controlled_plan_path": controlled_plan_path,
            "controlled_real_path": controlled_real_path,
            "controlled_closeout_path": controlled_closeout_path,
            "controlled_tick_path": controlled_tick_path,
            "git_pr_plan_path": git_pr_plan_path,
            "loop_run_plan": loop_plan,
            "controlled_start": controlled_start,
            "controlled_plan": controlled_plan,
            "controlled_real": controlled_real,
            "controlled_closeout": controlled_closeout,
            "controlled_tick": controlled_tick,
            "task_packet": task_packet,
            "execution_start": execution_start,
        }

    def write_controlled_loop_outcome_plan_chain(self, tmp, repo, *, emit_git_pr_plan=False):
        chain = self.write_controlled_loop_run_summary_chain(tmp, repo, emit_git_pr_plan=emit_git_pr_plan)
        summary_result, summary = self.run_controlled_loop_run_summary_cli(tmp, chain)
        self.assertEqual(summary_result.returncode, 0, summary_result.stderr)
        summary_path = Path(tmp) / "controlled-loop-run-summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        chain["controlled_run_summary_path"] = summary_path
        chain["controlled_run_summary"] = summary
        return chain

    def mark_controlled_loop_outcome_git_pr_plan_ready(self, chain):
        controlled_closeout = json.loads(json.dumps(chain["controlled_closeout"]))
        closeout = controlled_closeout["closeout"]
        git_pr_plan = closeout.get("git_pr_plan")
        self.assertIsInstance(git_pr_plan, dict)
        git_pr_plan = json.loads(json.dumps(git_pr_plan))
        git_pr_plan.update(
            {
                "ready_to_review": True,
                "decision": "ready",
                "recommended_next_action": "review_git_pr_plan",
                "blockers": [],
            }
        )
        git_pr_plan["pr_body_preflight"] = {
            "ready_to_publish": True,
            "decision": "ready",
            "recommended_next_action": "review_git_pr_plan",
            "blockers": [],
            "warnings": [],
            "template_summary": {
                "required_sections": ["Summary", "Validation"],
                "missing_sections": [],
            },
        }
        closeout["git_pr_plan"] = git_pr_plan
        closeout["next_decision"] = dict(
            closeout["next_decision"],
            recommended_next_action="review_git_pr_plan",
            git_pr_plan_ready=True,
        )
        controlled_closeout["closeout"] = closeout
        controlled_closeout["closeout_checksum"] = checksum_json(closeout)

        controlled_tick = json.loads(json.dumps(chain["controlled_tick"]))
        controlled_tick["next_decision"] = dict(closeout["next_decision"])
        controlled_tick["checksums"]["closeout"] = controlled_closeout["closeout_checksum"]
        self.assertIsNotNone(chain["git_pr_plan_path"])
        chain["git_pr_plan_path"].write_text(json.dumps(git_pr_plan), encoding="utf-8")
        controlled_tick.setdefault("files", {})["git_pr_plan"] = str(chain["git_pr_plan_path"])
        controlled_tick["checksums"]["git_pr_plan"] = checksum_json(git_pr_plan)

        summary = json.loads(json.dumps(chain["controlled_run_summary"]))
        summary["next_decision"] = dict(controlled_tick["next_decision"])
        summary["checksums"]["controlled_closeout"] = checksum_json(controlled_closeout)
        summary["checksums"]["controlled_loop_tick"] = checksum_json(controlled_tick)

        chain["controlled_closeout_path"].write_text(json.dumps(controlled_closeout), encoding="utf-8")
        chain["controlled_tick_path"].write_text(json.dumps(controlled_tick), encoding="utf-8")
        chain["controlled_run_summary_path"].write_text(json.dumps(summary), encoding="utf-8")
        chain["controlled_closeout"] = controlled_closeout
        chain["controlled_tick"] = controlled_tick
        chain["controlled_run_summary"] = summary
        return chain

    def set_controlled_loop_outcome_source_decision(self, chain, next_decision):
        controlled_closeout = json.loads(json.dumps(chain["controlled_closeout"]))
        controlled_closeout["closeout"]["next_decision"] = dict(next_decision)
        controlled_closeout["closeout_checksum"] = checksum_json(controlled_closeout["closeout"])

        controlled_tick = json.loads(json.dumps(chain["controlled_tick"]))
        controlled_tick["next_decision"] = dict(next_decision)
        controlled_tick["checksums"]["closeout"] = controlled_closeout["closeout_checksum"]

        summary = json.loads(json.dumps(chain["controlled_run_summary"]))
        summary["next_decision"] = dict(next_decision)
        summary["checksums"]["controlled_closeout"] = checksum_json(controlled_closeout)
        summary["checksums"]["controlled_loop_tick"] = checksum_json(controlled_tick)

        chain["controlled_closeout_path"].write_text(json.dumps(controlled_closeout), encoding="utf-8")
        chain["controlled_tick_path"].write_text(json.dumps(controlled_tick), encoding="utf-8")
        chain["controlled_run_summary_path"].write_text(json.dumps(summary), encoding="utf-8")
        chain["controlled_closeout"] = controlled_closeout
        chain["controlled_tick"] = controlled_tick
        chain["controlled_run_summary"] = summary
        return chain

    def run_controlled_loop_run_summary_cli(self, tmp, chain, **overrides):
        values = {
            "loop_run_plan_file": chain["loop_run_plan_path"],
            "controlled_loop_start_file": chain["controlled_start_path"],
            "controlled_invocation_plan_file": chain["controlled_plan_path"],
            "controlled_real_invocation_file": chain["controlled_real_path"],
            "controlled_closeout_file": chain["controlled_closeout_path"],
            "controlled_loop_tick_file": chain["controlled_tick_path"],
        }
        values.update(overrides)
        return run_cli(
            tmp,
            "controlled-loop-run-summary",
            "--loop-run-plan-file",
            str(values["loop_run_plan_file"]),
            "--controlled-loop-start-file",
            str(values["controlled_loop_start_file"]),
            "--controlled-invocation-plan-file",
            str(values["controlled_invocation_plan_file"]),
            "--controlled-real-invocation-file",
            str(values["controlled_real_invocation_file"]),
            "--controlled-closeout-file",
            str(values["controlled_closeout_file"]),
            "--controlled-loop-tick-file",
            str(values["controlled_loop_tick_file"]),
        )

    def run_controlled_loop_outcome_plan_cli(self, tmp, chain, **overrides):
        values = {
            "controlled_run_summary_file": chain["controlled_run_summary_path"],
            "controlled_closeout_file": chain["controlled_closeout_path"],
            "controlled_loop_tick_file": chain["controlled_tick_path"],
        }
        values.update(overrides)
        return run_cli(
            tmp,
            "controlled-loop-outcome-plan",
            "--controlled-run-summary-file",
            str(values["controlled_run_summary_file"]),
            "--controlled-closeout-file",
            str(values["controlled_closeout_file"]),
            "--controlled-loop-tick-file",
            str(values["controlled_loop_tick_file"]),
        )

    def write_controlled_loop_run_manifest_plan_chain(self, tmp, repo):
        chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo)
        outcome_result, outcome = self.run_controlled_loop_outcome_plan_cli(tmp, chain)
        self.assertEqual(outcome_result.returncode, 0, outcome_result.stderr)
        outcome_path = Path(tmp) / "controlled-loop-outcome-plan.json"
        outcome_path.write_text(json.dumps(outcome), encoding="utf-8")
        chain["controlled_outcome_plan_path"] = outcome_path
        chain["controlled_outcome_plan"] = outcome
        return chain

    def write_controlled_loop_run_manifest_approval_chain(self, tmp, repo):
        chain = self.write_controlled_loop_run_manifest_plan_chain(tmp, repo)
        manifest_result, manifest = self.run_controlled_loop_run_manifest_plan_cli(tmp, chain)
        self.assertEqual(manifest_result.returncode, 0, manifest_result.stderr)
        manifest_path = Path(tmp) / "controlled-loop-run-manifest-plan.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        chain["controlled_run_manifest_plan_path"] = manifest_path
        chain["controlled_run_manifest_plan"] = manifest
        approval_path, approval = write_operator_approval(
            Path(tmp) / "controlled-loop-run-manifest-approval.json",
            target_checksum=checksum_json(manifest),
            purpose="controlled_loop_run_manifest",
        )
        chain["controlled_run_manifest_approval_path"] = approval_path
        chain["controlled_run_manifest_approval"] = approval
        return chain

    def run_controlled_loop_run_manifest_plan_cli(self, tmp, chain, **overrides):
        values = {
            "controlled_run_summary_file": chain["controlled_run_summary_path"],
            "controlled_closeout_file": chain["controlled_closeout_path"],
            "controlled_loop_tick_file": chain["controlled_tick_path"],
            "controlled_outcome_plan_file": chain["controlled_outcome_plan_path"],
        }
        values.update(overrides)
        return run_cli(
            tmp,
            "controlled-loop-run-manifest-plan",
            "--controlled-run-summary-file",
            str(values["controlled_run_summary_file"]),
            "--controlled-closeout-file",
            str(values["controlled_closeout_file"]),
            "--controlled-loop-tick-file",
            str(values["controlled_loop_tick_file"]),
            "--controlled-outcome-plan-file",
            str(values["controlled_outcome_plan_file"]),
        )

    def run_controlled_loop_run_manifest_approval_cli(self, tmp, chain, **overrides):
        values = {
            "controlled_run_manifest_plan_file": chain["controlled_run_manifest_plan_path"],
            "approval_file": chain["controlled_run_manifest_approval_path"],
            "approval_secret": OPERATOR_APPROVAL_SECRET,
        }
        values.update(overrides)
        return run_cli(
            tmp,
            "controlled-loop-run-manifest-approval",
            "--controlled-run-manifest-plan-file",
            str(values["controlled_run_manifest_plan_file"]),
            "--approval-file",
            str(values["approval_file"]),
            "--approval-secret",
            str(values["approval_secret"]),
        )

    def controlled_loop_run_summary_input_file_contents(self, chain):
        return {
            name: Path(path).read_text(encoding="utf-8")
            for name, path in {
                "loop_run_plan": chain["loop_run_plan_path"],
                "controlled_start": chain["controlled_start_path"],
                "controlled_plan": chain["controlled_plan_path"],
                "controlled_real": chain["controlled_real_path"],
                "controlled_closeout": chain["controlled_closeout_path"],
                "controlled_tick": chain["controlled_tick_path"],
            }.items()
        }

    def controlled_loop_tick_args(self, tmp, chain, **overrides):
        values = {
            "root": Path(tmp),
            "loop_tick_file": str(chain["loop_tick_path"]),
            "task_file": str(chain["task_path"]),
            "execution_start_file": str(chain["execution_start_path"]),
            "readiness_file": str(chain["readiness_path"]),
            "invocation_plan_file": str(chain["plan_path"]),
            "real_invocation_file": str(chain["invocation_path"]),
            "result_file": str(chain["result_path"]),
            "snapshot_after_file": str(chain["snapshot_after_path"]),
            "closeout_file": str(chain["closeout_path"]),
            "git_pr_plan_file": None,
        }
        values.update(overrides)
        return type("Args", (), values)()

    def run_invoke_real_executor_cli(self, tmp, plan_path, **overrides):
        args = [
            "invoke-real-executor",
            "--plan-file",
            str(plan_path),
            "--approval-secret",
            OPERATOR_APPROVAL_SECRET,
            "--side-effect-mode",
            overrides.get("side_effect_mode", "evidence_only"),
        ]
        if "max_plan_age_minutes" in overrides:
            args.extend(["--max-plan-age-minutes", str(overrides["max_plan_age_minutes"])])
        return run_cli(tmp, *args)

    def test_executor_invocation_plan_accepts_matching_evidence_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs = self.write_valid_executor_invocation_plan_inputs(tmp, repo)
            audit_before = audit_records(tmp)

            result, output = run_cli(
                tmp,
                "executor-invocation-plan",
                "--cwd",
                repo,
                "--readiness-file",
                str(inputs["readiness_path"]),
                "--approval-file",
                str(inputs["approval_path"]),
                "--approval-secret",
                OPERATOR_APPROVAL_SECRET,
                "--adapter-file",
                str(inputs["adapter_path"]),
                "--rollback-file",
                str(inputs["rollback_path"]),
                "--command",
                inputs["command"],
                "--env-allow",
                "PATH",
                "--env-allow",
                "PYTHONPATH",
                "--timeout-seconds",
                str(inputs["timeout_seconds"]),
                "--expected-result-path",
                inputs["task_packet"]["expected_output"]["evidence_path"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "executor-invocation-plan.v1")
            self.assertEqual(output["packet"], "executor_invocation_plan")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_invocation_planned"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["recommended_next_action"], "invoke_real_executor")
            self.assertEqual(output["target_checksum"], inputs["target_checksum"])
            self.assertEqual(output["readiness"]["checksum"], checksum_json(inputs["readiness_packet"]))
            self.assertEqual(output["approval"]["purpose"], "real_executor_invocation")
            self.assertEqual(output["adapter"]["id"], "local-python")
            self.assertEqual(output["rollback"]["checksum"], checksum_json(inputs["rollback_packet"]))
            self.assertEqual(output["command"]["command"], inputs["command"])
            self.assertEqual(output["audit_chain"]["chain_head"], inputs["audit_replay"]["chain_head"])
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(current_head(repo), inputs["task_packet"]["repo"]["head"])

    def test_executor_invocation_plan_blocks_unready_inputs_without_side_effects(self):
        def mutate_package_publication_command(task_packet):
            task_packet["command_policy"].update({"allowed_commands": ["python"]})

        def mutate_powershell_wrapper_command(task_packet):
            task_packet["command_policy"].update(
                {"allowed_commands": ["python -m unittest tests.test_cadence", "powershell"]}
            )

        cases = [
            (
                "wrong-approval-target",
                "python -m unittest tests.test_cadence",
                lambda tmp, repo, inputs: write_operator_approval(
                    inputs["approval_path"],
                    target_checksum="sha256:" + "0" * 64,
                    purpose="real_executor_invocation",
                ),
                [],
                "approval_target_mismatch",
            ),
            (
                "denied-command",
                "git push origin HEAD",
                lambda tmp, repo, inputs: None,
                [],
                "executor_command_denied",
            ),
            (
                "package-publication-command",
                "python -m twine upload dist/*",
                lambda tmp, repo, inputs: None,
                [],
                "executor_command_denied",
            ),
            (
                "powershell-wrapper-push-command",
                "powershell -Command git push origin HEAD",
                lambda tmp, repo, inputs: None,
                [],
                "executor_command_denied",
            ),
            (
                "missing-rollback",
                "python -m unittest tests.test_cadence",
                lambda tmp, repo, inputs: None,
                ["--rollback-file", str(Path("missing-rollback.json"))],
                "rollback_evidence_missing",
            ),
            (
                "broken-audit-chain",
                "python -m unittest tests.test_cadence",
                lambda tmp, repo, inputs: (Path(tmp) / "audit" / "events.jsonl").write_text(
                    (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").replace(
                        "operator approval identity evidence accepted",
                        "tampered accepted approval",
                    ),
                    encoding="utf-8",
                ),
                [],
                "audit_chain_not_clean",
            ),
        ]
        for name, command, mutate, arg_overrides, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    task_mutator = None
                    if name == "package-publication-command":
                        task_mutator = mutate_package_publication_command
                    if name == "powershell-wrapper-push-command":
                        task_mutator = mutate_powershell_wrapper_command
                    inputs = self.write_valid_executor_invocation_plan_inputs(
                        tmp,
                        repo,
                        command=command,
                        task_mutator=task_mutator,
                    )
                    mutate(tmp, repo, inputs)
                    rollback_args = arg_overrides or ["--rollback-file", str(inputs["rollback_path"])]
                    audit_before = audit_records(tmp)

                    result, output = run_cli(
                        tmp,
                        "executor-invocation-plan",
                        "--cwd",
                        repo,
                        "--readiness-file",
                        str(inputs["readiness_path"]),
                        "--approval-file",
                        str(inputs["approval_path"]),
                        "--approval-secret",
                        OPERATOR_APPROVAL_SECRET,
                        "--adapter-file",
                        str(inputs["adapter_path"]),
                        *rollback_args,
                        "--command",
                        command,
                        "--env-allow",
                        "PATH",
                        "--env-allow",
                        "PYTHONPATH",
                        "--timeout-seconds",
                        str(inputs["timeout_seconds"]),
                        "--expected-result-path",
                        inputs["task_packet"]["expected_output"]["evidence_path"],
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["executor_started"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(audit_records(tmp), audit_before)

    def test_invoke_real_executor_runs_approved_plan_and_writes_invocation_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, plan = self.write_real_executor_invocation_plan(tmp, repo)
            audit_before = audit_records(tmp)

            result, output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "real-executor-invocation.v1")
            self.assertEqual(output["packet"], "real_executor_invocation")
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertFalse(output["timed_out"])
            self.assertEqual(output["side_effect_mode"], "evidence_only")
            self.assertEqual(output["plan_checksum"], checksum_json(plan))
            self.assertEqual(output["command"]["command"], inputs["command"])
            self.assertEqual(output["process"]["exit_code"], 0)
            self.assertEqual(output["blockers"], [])
            self.assertIn("real_executor_process_started", output["side_effects"])
            self.assertIn("real_executor_invocation_record_written", output["side_effects"])
            self.assertEqual(output["repository_before"]["head"], current_head(repo))
            self.assertFalse(output["repository_before"]["dirty_worktree"])
            self.assertFalse(output["repository_after"]["dirty_worktree"])
            self.assertEqual(output["rollback"]["checksum"], checksum_json(inputs["rollback_packet"]))
            self.assertEqual(output["audit_chain"]["chain_head"], plan["audit_chain"]["chain_head"])
            self.assertTrue(Path(output["result_file"]).exists())
            self.assertTrue(Path(output["stdout_log"]).exists())
            self.assertTrue(Path(output["stderr_log"]).exists())
            record_path = Path(output["record_file"])
            self.assertTrue(record_path.exists())
            self.assertEqual(record_path.parent, Path(tmp) / "real-executor-invocations")
            self.assertEqual(json.loads(record_path.read_text(encoding="utf-8")), output)
            records = audit_records(tmp)
            self.assertEqual(len(records), len(audit_before) + 1)
            self.assertEqual(records[-1]["event"], "real_executor_invocation_record")
            self.assertEqual(records[-1]["action"], "record_real_executor_invocation")
            self.assertEqual(records[-1]["invocation_id"], output["invocation_id"])
            self.assertEqual(records[-1]["invocation_record_file"], str(record_path))
            self.assertEqual(records[-1]["invocation_record_checksum"], checksum_json(output))
            self.assertEqual(records[-1]["result_evidence_checksum"], output["result_evidence_checksum"])
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["events_by_type"]["real_executor_invocation_record"], 1)

    def test_controlled_loop_run_summary_composes_saved_controlled_chain(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_summary_chain(tmp, repo)
            audit_before = audit_records(tmp)
            files_before = {
                name: Path(path).read_text(encoding="utf-8")
                for name, path in {
                    "loop_run_plan": chain["loop_run_plan_path"],
                    "controlled_start": chain["controlled_start_path"],
                    "controlled_plan": chain["controlled_plan_path"],
                    "controlled_real": chain["controlled_real_path"],
                    "controlled_closeout": chain["controlled_closeout_path"],
                    "controlled_tick": chain["controlled_tick_path"],
                }.items()
            }

            result, output = self.run_controlled_loop_run_summary_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-run-summary.v1")
            self.assertEqual(output["packet"], "controlled_loop_run_summary")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_run_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "review_controlled_loop_run")
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertTrue(output["summarized_controlled_tick"]["executor_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertFalse(output["github_write_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["source_tick_id"], chain["controlled_tick"]["source_tick_id"])
            self.assertEqual(output["task"]["id"], chain["task_packet"]["task"]["id"])
            self.assertEqual(output["epoch"]["id"], chain["execution_start"]["epoch_id"])
            self.assertEqual(output["checksums"]["controlled_loop_tick"], checksum_json(chain["controlled_tick"]))
            self.assertEqual(output["checksums"]["controlled_closeout"], checksum_json(chain["controlled_closeout"]))
            self.assertEqual([step["name"] for step in output["steps"]], [
                "loop_run_plan",
                "controlled_loop_start",
                "controlled_invocation_plan",
                "controlled_real_invocation",
                "controlled_closeout",
                "controlled_loop_tick",
            ])
            self.assertEqual({step["status"] for step in output["steps"]}, {"accepted"})
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    name: Path(path).read_text(encoding="utf-8")
                    for name, path in {
                        "loop_run_plan": chain["loop_run_plan_path"],
                        "controlled_start": chain["controlled_start_path"],
                        "controlled_plan": chain["controlled_plan_path"],
                        "controlled_real": chain["controlled_real_path"],
                        "controlled_closeout": chain["controlled_closeout_path"],
                        "controlled_tick": chain["controlled_tick_path"],
                    }.items()
                },
                files_before,
            )

    def test_controlled_loop_run_summary_blocks_controlled_plan_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_summary_chain(tmp, repo)
            controlled_plan = json.loads(chain["controlled_plan_path"].read_text(encoding="utf-8"))
            controlled_plan["target_checksum"] = "sha256:" + "0" * 64
            chain["controlled_plan_path"].write_text(json.dumps(controlled_plan), encoding="utf-8")
            audit_before = audit_records(tmp)
            files_before = self.controlled_loop_run_summary_input_file_contents(chain)

            result, output = self.run_controlled_loop_run_summary_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_run_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_controlled_loop_run_blockers")
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertIn("controlled_invocation_plan_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            blocked_step = next(step for step in output["steps"] if step["name"] == "controlled_invocation_plan")
            self.assertEqual(blocked_step["status"], "blocked")
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(self.controlled_loop_run_summary_input_file_contents(chain), files_before)

    def test_controlled_loop_run_summary_reports_stable_blocker_classes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_summary_chain(tmp, repo)
            originals = self.controlled_loop_run_summary_input_file_contents(chain)

            def run_mutated_case(path_key, mutate_packet, expected_code):
                path = chain[path_key]
                packet = json.loads(Path(path).read_text(encoding="utf-8"))
                mutate_packet(packet)
                Path(path).write_text(json.dumps(packet), encoding="utf-8")
                audit_before = audit_records(tmp)
                files_before = self.controlled_loop_run_summary_input_file_contents(chain)

                result, output = self.run_controlled_loop_run_summary_cli(tmp, chain)

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIsNotNone(output)
                self.assertFalse(output["valid"])
                self.assertEqual(output["recommended_next_action"], "inspect_controlled_loop_run_blockers")
                self.assertEqual(output["side_effects"], [])
                self.assertNotIn("audit_record", output)
                self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                self.assertEqual(audit_records(tmp), audit_before)
                self.assertEqual(self.controlled_loop_run_summary_input_file_contents(chain), files_before)
                for name, content in originals.items():
                    Path(
                        {
                            "loop_run_plan": chain["loop_run_plan_path"],
                            "controlled_start": chain["controlled_start_path"],
                            "controlled_plan": chain["controlled_plan_path"],
                            "controlled_real": chain["controlled_real_path"],
                            "controlled_closeout": chain["controlled_closeout_path"],
                            "controlled_tick": chain["controlled_tick_path"],
                        }[name]
                    ).write_text(content, encoding="utf-8")

            cases = [
                (
                    "packet mismatch",
                    "controlled_real_path",
                    lambda packet: packet.update({"schema_version": "not-controlled-real-invocation.v1"}),
                    "controlled_run_packet_mismatch",
                ),
                (
                    "not completed",
                    "controlled_real_path",
                    lambda packet: packet.update({"controlled_real_invocation_status": "blocked"}),
                    "controlled_real_invocation_not_completed",
                ),
                (
                    "unexpected next action",
                    "controlled_closeout_path",
                    lambda packet: packet.update({"recommended_next_action": "unexpected"}),
                    "controlled_closeout_unexpected_next_action",
                ),
                (
                    "missing file anchor",
                    "controlled_plan_path",
                    lambda packet: packet["files"].pop("controlled_loop_start"),
                    "controlled_loop_start_file_mismatch",
                ),
                (
                    "missing task anchor",
                    "controlled_plan_path",
                    lambda packet: packet.pop("task_id", None),
                    "controlled_run_task_mismatch",
                ),
                (
                    "missing epoch anchor",
                    "controlled_real_path",
                    lambda packet: packet.pop("epoch_id", None),
                    "controlled_run_epoch_mismatch",
                ),
                (
                    "controlled tick task checksum",
                    "controlled_tick_path",
                    lambda packet: packet["task"].update({"checksum": "sha256:" + "0" * 64}),
                    "controlled_tick_task_checksum_mismatch",
                ),
                (
                    "controlled tick invocation-plan checksum",
                    "controlled_tick_path",
                    lambda packet: packet["checksums"].update({"invocation_plan": "sha256:" + "1" * 64}),
                    "controlled_tick_invocation_plan_checksum_mismatch",
                ),
                (
                    "unexpected completed blockers",
                    "controlled_closeout_path",
                    lambda packet: packet.update({"blockers": [{"code": "unexpected_blocker"}]}),
                    "controlled_closeout_unexpected_blockers",
                ),
                (
                    "unexpected side effects",
                    "controlled_start_path",
                    lambda packet: packet.update({"side_effects": ["unexpected"]}),
                    "controlled_loop_start_unexpected_side_effects",
                ),
            ]
            for label, path_key, mutate_packet, expected_code in cases:
                with self.subTest(label):
                    run_mutated_case(path_key, mutate_packet, expected_code)

            audit_before = audit_records(tmp)
            files_before = self.controlled_loop_run_summary_input_file_contents(chain)
            result, output = self.run_controlled_loop_run_summary_cli(
                tmp,
                chain,
                controlled_closeout_file=Path(tmp) / "missing-controlled-closeout.json",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertIn("controlled_closeout_evidence_missing", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(self.controlled_loop_run_summary_input_file_contents(chain), files_before)

    def test_controlled_loop_outcome_plan_recommends_git_pr_materialization_approval(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo, emit_git_pr_plan=True)
            self.mark_controlled_loop_outcome_git_pr_plan_ready(chain)
            audit_before = audit_records(tmp)
            files_before = {
                "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-outcome-plan.v1")
            self.assertEqual(output["packet"], "controlled_loop_outcome_plan")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["outcome_plan_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "request_git_pr_materialization_approval")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["loop_continuation_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["source_decision"]["decision"], "generate_git_pr_plan")
            self.assertEqual(output["source_decision"]["recommended_next_action"], "review_git_pr_plan")
            self.assertTrue(output["git_pr_plan"]["ready_to_review"])
            self.assertEqual(output["git_pr_plan"]["side_effects"], [])
            self.assertEqual(output["task"]["id"], chain["task_packet"]["task"]["id"])
            self.assertEqual(output["epoch"]["id"], chain["execution_start"]["epoch_id"])
            self.assertEqual(output["checksums"]["controlled_run_summary"], checksum_json(chain["controlled_run_summary"]))
            self.assertEqual(output["checksums"]["controlled_closeout"], checksum_json(chain["controlled_closeout"]))
            self.assertEqual(output["checksums"]["controlled_loop_tick"], checksum_json(chain["controlled_tick"]))
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                    "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                    "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_outcome_plan_inspects_unready_embedded_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo, emit_git_pr_plan=True)

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["outcome_plan_status"], "blocked")
            self.assertEqual(output["source_decision"]["decision"], "generate_git_pr_plan")
            self.assertEqual(output["recommended_next_action"], "inspect_git_pr_plan_blockers")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["git_pr_plan"]["ready_to_review"])
            self.assertIn("git_pr_plan_not_ready", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_inspects_malformed_ready_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo, emit_git_pr_plan=True)
            self.mark_controlled_loop_outcome_git_pr_plan_ready(chain)
            controlled_closeout = chain["controlled_closeout"]
            controlled_closeout["closeout"]["git_pr_plan"]["approval_state"] = "approved"
            controlled_closeout["closeout_checksum"] = checksum_json(controlled_closeout["closeout"])
            controlled_tick = chain["controlled_tick"]
            controlled_tick["checksums"]["closeout"] = controlled_closeout["closeout_checksum"]
            summary = chain["controlled_run_summary"]
            summary["checksums"]["controlled_closeout"] = checksum_json(controlled_closeout)
            summary["checksums"]["controlled_loop_tick"] = checksum_json(controlled_tick)
            chain["controlled_closeout_path"].write_text(json.dumps(controlled_closeout), encoding="utf-8")
            chain["controlled_tick_path"].write_text(json.dumps(controlled_tick), encoding="utf-8")
            chain["controlled_run_summary_path"].write_text(json.dumps(summary), encoding="utf-8")

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["outcome_plan_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_git_pr_plan_blockers")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["git_pr_plan"]["approval_state"], "approved")
            self.assertIn("git_pr_plan_approval_state_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_blocks_unanchored_ready_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo, emit_git_pr_plan=True)
            self.mark_controlled_loop_outcome_git_pr_plan_ready(chain)
            controlled_tick = chain["controlled_tick"]
            controlled_tick["files"].pop("git_pr_plan")
            controlled_tick["checksums"].pop("git_pr_plan")
            summary = chain["controlled_run_summary"]
            summary["checksums"]["controlled_loop_tick"] = checksum_json(controlled_tick)
            chain["controlled_tick_path"].write_text(json.dumps(controlled_tick), encoding="utf-8")
            chain["controlled_run_summary_path"].write_text(json.dumps(summary), encoding="utf-8")

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_git_pr_plan_blockers")
            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("git_pr_plan_checksum_mismatch", blocker_codes)
            self.assertIn("git_pr_plan_unanchored", blocker_codes)
            self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_recommends_git_pr_plan_when_no_plan_exists(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo)

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["source_decision"]["decision"], "generate_git_pr_plan")
            self.assertEqual(output["recommended_next_action"], "run_git_pr_plan")
            self.assertFalse(output["operator_confirmation_required"])
            self.assertIsNone(output["git_pr_plan"])
            self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_maps_bounded_terminal_decisions(self):
        cases = [
            (
                {"decision": "continue", "recommended_next_action": "wait_for_next_executor_result", "reason": "remaining task"},
                "plan_next_controlled_executor_step",
            ),
            (
                {"decision": "handoff", "recommended_next_action": "prepare_handoff", "reason": "executor failed"},
                "inspect_executor_failure",
            ),
            (
                {"decision": "stop", "recommended_next_action": "stop_active_loop", "reason": "executor stopped"},
                "stop_active_loop",
            ),
            (
                {"decision": "validate_more_evidence", "recommended_next_action": "fix_executor_evidence", "reason": "bad evidence"},
                "fix_executor_evidence",
            ),
        ]
        for next_decision, expected_action in cases:
            with self.subTest(decision=next_decision["decision"]):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo)
                    self.set_controlled_loop_outcome_source_decision(chain, next_decision)

                    result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output["valid"])
                    self.assertEqual(output["source_decision"], next_decision)
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_blocks_unbounded_source_action(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo)
            self.set_controlled_loop_outcome_source_decision(
                chain,
                {
                    "decision": "stop",
                    "recommended_next_action": "merge_after_operator_confirmation",
                    "reason": "crafted action",
                },
            )

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_controlled_loop_outcome_blockers")
            self.assertIn("controlled_loop_outcome_action_unsupported", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])

    def test_controlled_loop_outcome_plan_blocks_stale_summary_tick_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_outcome_plan_chain(tmp, repo, emit_git_pr_plan=True)
            controlled_tick = json.loads(chain["controlled_tick_path"].read_text(encoding="utf-8"))
            controlled_tick["next_decision"]["recommended_next_action"] = "unexpected_action"
            chain["controlled_tick_path"].write_text(json.dumps(controlled_tick), encoding="utf-8")
            audit_before = audit_records(tmp)
            files_before = {
                "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_outcome_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["outcome_plan_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_run_summary")
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertIn("controlled_loop_tick_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                    "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                    "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_run_manifest_plan_binds_terminal_sequence_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_plan_chain(tmp, repo)
            audit_before = audit_records(tmp)
            files_before = {
                "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                "outcome": chain["controlled_outcome_plan_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_run_manifest_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-run-manifest-plan.v1")
            self.assertEqual(output["packet"], "controlled_loop_run_manifest_plan")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["manifest_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "review_controlled_run_manifest")
            self.assertTrue(output["operator_confirmation_required"])
            for flag in [
                "runner_started",
                "executor_started",
                "epoch_started",
                "pr_action_started",
                "github_write_started",
                "merge_started",
                "release_started",
                "package_publication_started",
                "role_assignment_started",
                "agent_scheduling_started",
                "loop_continuation_started",
            ]:
                self.assertFalse(output[flag], flag)
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["next_controlled_action"], chain["controlled_outcome_plan"]["recommended_next_action"])
            self.assertEqual(output["checksums"]["controlled_run_summary"], checksum_json(chain["controlled_run_summary"]))
            self.assertEqual(output["checksums"]["controlled_closeout"], checksum_json(chain["controlled_closeout"]))
            self.assertEqual(output["checksums"]["controlled_loop_tick"], checksum_json(chain["controlled_tick"]))
            self.assertEqual(output["checksums"]["controlled_outcome_plan"], checksum_json(chain["controlled_outcome_plan"]))
            evidence_files = output["run_manifest"]["evidence_files"]
            self.assertEqual(evidence_files["loop_run_plan"], str(chain["loop_run_plan_path"]))
            self.assertEqual(evidence_files["controlled_loop_start"], str(chain["controlled_start_path"]))
            self.assertEqual(evidence_files["controlled_invocation_plan"], str(chain["controlled_plan_path"]))
            self.assertEqual(evidence_files["controlled_real_invocation"], str(chain["controlled_real_path"]))
            self.assertEqual(evidence_files["controlled_closeout"], str(chain["controlled_closeout_path"]))
            self.assertEqual(evidence_files["controlled_loop_tick"], str(chain["controlled_tick_path"]))
            self.assertEqual(evidence_files["controlled_run_summary"], str(chain["controlled_run_summary_path"]))
            self.assertEqual(evidence_files["controlled_outcome_plan"], str(chain["controlled_outcome_plan_path"]))
            self.assertEqual(evidence_files["task"], str(chain["controlled_tick"]["files"]["task"]))
            self.assertEqual(evidence_files["result"], str(chain["controlled_tick"]["files"]["result"]))
            self.assertEqual(
                [step["command"] for step in output["run_manifest"]["command_sequence"]],
                [
                    "loop-run-plan",
                    "start-governed-execution",
                    "controlled-loop-start",
                    "executor-invocation-readiness",
                    "executor-invocation-plan",
                    "controlled-loop-invocation-plan",
                    "invoke-real-executor",
                    "controlled-loop-real-invocation",
                    "closeout-executor-result",
                    "controlled-loop-closeout",
                    "controlled-loop-tick",
                    "controlled-loop-run-summary",
                    "controlled-loop-outcome-plan",
                ],
            )
            self.assertEqual(
                output["run_manifest"]["command_sequence"][10]["allowed_side_effects_when_executed"],
                ["controlled_loop_tick_audit_appended"],
            )
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                    "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                    "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                    "outcome": chain["controlled_outcome_plan_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_run_manifest_plan_blocks_summary_drift_as_stale_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_plan_chain(tmp, repo)
            summary = chain["controlled_run_summary"]
            summary["checksums"]["controlled_closeout"] = "sha256:" + "0" * 64
            chain["controlled_run_summary_path"].write_text(json.dumps(summary), encoding="utf-8")
            chain["controlled_outcome_plan"]["checksums"]["controlled_run_summary"] = checksum_json(summary)
            chain["controlled_outcome_plan_path"].write_text(
                json.dumps(chain["controlled_outcome_plan"]),
                encoding="utf-8",
            )
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_run_manifest_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_outcome_plan")
            self.assertIn(
                "controlled_run_manifest_controlled_closeout_checksum_mismatch",
                {blocker["code"] for blocker in output["blockers"]},
            )
            self.assertEqual(output["checksums"]["controlled_closeout"], checksum_json(chain["controlled_closeout"]))
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_run_manifest_plan_blocks_tampered_outcome_action(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_plan_chain(tmp, repo)
            chain["controlled_outcome_plan"]["recommended_next_action"] = "stop_active_loop"
            chain["controlled_outcome_plan_path"].write_text(
                json.dumps(chain["controlled_outcome_plan"]),
                encoding="utf-8",
            )
            audit_before = audit_records(tmp)

            result, output = self.run_controlled_loop_run_manifest_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_outcome_plan")
            self.assertEqual(output["next_controlled_action"], "run_git_pr_plan")
            self.assertIn(
                "controlled_outcome_plan_recommended_next_action_mismatch",
                {blocker["code"] for blocker in output["blockers"]},
            )
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(audit_records(tmp), audit_before)

    def test_controlled_loop_run_manifest_plan_blocks_stale_outcome_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_plan_chain(tmp, repo)
            outcome_plan = chain["controlled_outcome_plan"]
            outcome_plan["checksums"]["controlled_loop_tick"] = "sha256:" + "0" * 64
            chain["controlled_outcome_plan_path"].write_text(json.dumps(outcome_plan), encoding="utf-8")
            audit_before = audit_records(tmp)
            files_before = {
                "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                "outcome": chain["controlled_outcome_plan_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_run_manifest_plan_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["manifest_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "refresh_controlled_loop_outcome_plan")
            self.assertIn(
                "controlled_outcome_plan_controlled_loop_tick_checksum_mismatch",
                {blocker["code"] for blocker in output["blockers"]},
            )
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "summary": chain["controlled_run_summary_path"].read_text(encoding="utf-8"),
                    "closeout": chain["controlled_closeout_path"].read_text(encoding="utf-8"),
                    "tick": chain["controlled_tick_path"].read_text(encoding="utf-8"),
                    "outcome": chain["controlled_outcome_plan_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_run_manifest_approval_accepts_target_bound_operator_approval_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_approval_chain(tmp, repo)
            audit_before = audit_records(tmp)
            files_before = {
                "manifest": chain["controlled_run_manifest_plan_path"].read_text(encoding="utf-8"),
                "approval": chain["controlled_run_manifest_approval_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_run_manifest_approval_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-run-manifest-approval.v1")
            self.assertEqual(output["packet"], "controlled_loop_run_manifest_approval")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["approval_status"], "completed")
            self.assertEqual(output["recommended_next_action"], "review_controlled_run_manifest_approval")
            self.assertTrue(output["operator_confirmation_required"])
            for flag in [
                "runner_started",
                "executor_started",
                "epoch_started",
                "pr_action_started",
                "github_write_started",
                "merge_started",
                "release_started",
                "package_publication_started",
                "role_assignment_started",
                "agent_scheduling_started",
                "loop_continuation_started",
            ]:
                self.assertFalse(output[flag], flag)
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["controlled_run_manifest_plan"]["checksum"], checksum_json(chain["controlled_run_manifest_plan"]))
            self.assertEqual(output["approval"]["state"], "approved")
            self.assertEqual(output["approval"]["target_checksum"], checksum_json(chain["controlled_run_manifest_plan"]))
            self.assertEqual(output["approval"]["purpose"], "controlled_loop_run_manifest")
            self.assertTrue(output["approval"]["signature_verified"])
            self.assertEqual(output["next_controlled_action"], "review_approved_controlled_run_manifest")
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "manifest": chain["controlled_run_manifest_plan_path"].read_text(encoding="utf-8"),
                    "approval": chain["controlled_run_manifest_approval_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_run_manifest_approval_blocks_mismatched_approval_target_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_run_manifest_approval_chain(tmp, repo)
            approval_path, _approval = write_operator_approval(
                chain["controlled_run_manifest_approval_path"],
                target_checksum="sha256:" + "0" * 64,
                purpose="controlled_loop_run_manifest",
            )
            chain["controlled_run_manifest_approval_path"] = approval_path
            audit_before = audit_records(tmp)
            files_before = {
                "manifest": chain["controlled_run_manifest_plan_path"].read_text(encoding="utf-8"),
                "approval": chain["controlled_run_manifest_approval_path"].read_text(encoding="utf-8"),
            }

            result, output = self.run_controlled_loop_run_manifest_approval_cli(tmp, chain)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["approval_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "fix_controlled_run_manifest_approval")
            self.assertIn("operator_approval_target_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])
            self.assertNotIn("audit_record", output)
            self.assertEqual(audit_records(tmp), audit_before)
            self.assertEqual(
                {
                    "manifest": chain["controlled_run_manifest_plan_path"].read_text(encoding="utf-8"),
                    "approval": chain["controlled_run_manifest_approval_path"].read_text(encoding="utf-8"),
                },
                files_before,
            )

    def test_controlled_loop_tick_accepts_existing_real_invocation_closeout_chain(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            invocation_before = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            closeout_before = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))

            result, output = run_cli(
                tmp,
                "controlled-loop-tick",
                "--loop-tick-file",
                str(chain["loop_tick_path"]),
                "--task-file",
                str(chain["task_path"]),
                "--execution-start-file",
                str(chain["execution_start_path"]),
                "--readiness-file",
                str(chain["readiness_path"]),
                "--invocation-plan-file",
                str(chain["plan_path"]),
                "--real-invocation-file",
                str(chain["invocation_path"]),
                "--result-file",
                str(chain["result_path"]),
                "--snapshot-after-file",
                str(chain["snapshot_after_path"]),
                "--closeout-file",
                str(chain["closeout_path"]),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "controlled-loop-tick.v1")
            self.assertEqual(output["packet"], "controlled_loop_tick")
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_tick_status"], "completed")
            self.assertEqual(output["source_tick_id"], "loop-tick-controlled-1")
            self.assertEqual(output["task"]["id"], chain["task_packet"]["task"]["id"])
            self.assertEqual(output["epoch"]["id"], "epoch-1")
            self.assertEqual(output["real_invocation"]["closeout_status"], "completed")
            self.assertTrue(output["executor_started"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual({step["status"] for step in output["steps"]}, {"accepted"})
            self.assertIn("controlled_loop_tick_audit_appended", output["side_effects"])
            self.assertIn("audit_record", output)
            self.assertEqual(json.loads(chain["invocation_path"].read_text(encoding="utf-8")), invocation_before)
            self.assertEqual(json.loads(chain["closeout_path"].read_text(encoding="utf-8")), closeout_before)
            records = audit_records(tmp)
            self.assertEqual(records[-1]["event"], "controlled_loop_tick")
            self.assertEqual(records[-1]["task_id"], chain["task_packet"]["task"]["id"])
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["events_by_type"]["controlled_loop_tick"], 1)

    def test_controlled_loop_tick_audits_optional_git_pr_plan_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            git_pr_plan = self.controlled_loop_tick_git_pr_plan()
            git_pr_plan_path = self.write_controlled_loop_tick_git_pr_plan_anchor(tmp, chain, git_pr_plan)

            result, output = self.run_controlled_loop_tick_cli(tmp, chain, git_pr_plan_file=git_pr_plan_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            records = audit_records(tmp)
            self.assertEqual(records[-1]["git_pr_plan_file"], str(git_pr_plan_path))
            self.assertEqual(records[-1]["git_pr_plan_checksum"], checksum_json(git_pr_plan))
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])

    def test_controlled_loop_tick_blocks_unsafe_optional_git_pr_plan_without_append(self):
        cases = [
            ("not_ready", {"ready_to_review": False}, "git_pr_plan_not_ready"),
            ("not_dry_run", {"dry_run": False}, "git_pr_plan_not_dry_run"),
            ("operator_confirmation_missing", {"operator_confirmation_required": False}, "git_pr_plan_operator_confirmation_missing"),
            ("side_effects_present", {"side_effects": ["created_branch"]}, "git_pr_plan_side_effects_present"),
            ("approval_state_invalid", {"approval_state": "approved"}, "git_pr_plan_approval_state_invalid"),
            ("execution_authority_invalid", {"execution_authority": "operator_approved_git_pr_materialization"}, "git_pr_plan_execution_authority_invalid"),
            ("proposed_branch_missing", {"proposed_branch": ""}, "git_pr_plan_proposed_branch_missing"),
        ]
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            before_audit_count = len(audit_records(tmp))

            for name, overrides, expected_code in cases:
                with self.subTest(name=name):
                    git_pr_plan = self.controlled_loop_tick_git_pr_plan(**overrides)
                    git_pr_plan_path = self.write_controlled_loop_tick_git_pr_plan_anchor(
                        tmp,
                        chain,
                        git_pr_plan,
                        filename=f"git-pr-plan-{name}.json",
                    )

                    result, output = self.run_controlled_loop_tick_cli(tmp, chain, git_pr_plan_file=git_pr_plan_path)

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["controlled_tick_status"], "blocked")
                    self.assertFalse(output["side_effects"])
                    self.assertNotIn("audit_record", output)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_accepts_saved_relative_anchors_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as other:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            root = Path(tmp)

            def rel(path):
                return str(Path(path).relative_to(root))

            execution_start = json.loads(chain["execution_start_path"].read_text(encoding="utf-8"))
            execution_start["task_file"] = rel(chain["task_path"])
            chain["execution_start_path"].write_text(json.dumps(execution_start), encoding="utf-8")

            invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            invocation["invocation_cwd"] = str(root)
            invocation["record_file"] = rel(chain["invocation_path"])
            invocation["plan_file"] = rel(chain["plan_path"])
            invocation["result_file"] = rel(chain["result_path"])
            invocation["task_file"] = rel(chain["task_path"])
            closeout["task_file"] = rel(chain["task_path"])
            closeout["result_file"] = rel(chain["result_path"])
            closeout["snapshot_after_file"] = rel(chain["snapshot_after_path"])
            closeout["real_invocation"]["path"] = rel(chain["invocation_path"])
            closeout_core_packet = {
                key: value
                for key, value in closeout.items()
                if key not in {"audit_record", "run_record", "real_invocation"}
            }
            epoch_closeout_checksum = checksum_json(closeout_core_packet)
            invocation["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
            chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")

            result, output = self.run_controlled_loop_tick_cli(tmp, chain, cwd=other)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["blockers"], [])

    def test_controlled_loop_tick_blocks_mismatched_readiness_without_audit_append(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            readiness = json.loads(chain["readiness_path"].read_text(encoding="utf-8"))
            readiness["active_epoch"]["id"] = "epoch-drifted"
            chain["readiness_path"].write_text(json.dumps(readiness), encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "controlled-loop-tick",
                "--loop-tick-file",
                str(chain["loop_tick_path"]),
                "--task-file",
                str(chain["task_path"]),
                "--execution-start-file",
                str(chain["execution_start_path"]),
                "--readiness-file",
                str(chain["readiness_path"]),
                "--invocation-plan-file",
                str(chain["plan_path"]),
                "--real-invocation-file",
                str(chain["invocation_path"]),
                "--result-file",
                str(chain["result_path"]),
                "--snapshot-after-file",
                str(chain["snapshot_after_path"]),
                "--closeout-file",
                str(chain["closeout_path"]),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["controlled_tick_status"], "blocked")
            self.assertFalse(output["side_effects"])
            self.assertNotIn("audit_record", output)
            self.assertIn("readiness_epoch_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertIn("invocation_plan_readiness_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_blocks_malformed_closeout_validation_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            closeout["validation"] = "not-a-validation-packet"
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "controlled-loop-tick",
                "--loop-tick-file",
                str(chain["loop_tick_path"]),
                "--task-file",
                str(chain["task_path"]),
                "--execution-start-file",
                str(chain["execution_start_path"]),
                "--readiness-file",
                str(chain["readiness_path"]),
                "--invocation-plan-file",
                str(chain["plan_path"]),
                "--real-invocation-file",
                str(chain["invocation_path"]),
                "--result-file",
                str(chain["result_path"]),
                "--snapshot-after-file",
                str(chain["snapshot_after_path"]),
                "--closeout-file",
                str(chain["closeout_path"]),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("closeout_validation_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_blocks_stale_pre_closeout_invocation_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            chain["invocation_path"].write_text(json.dumps(chain["invocation_before_closeout"]), encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("closeout_invocation_mismatch", codes)
            self.assertIn("real_invocation_closeout_mismatch", codes)
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_accepts_terminal_failed_closeout(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            closeout["closeout_status"] = "failed"
            invocation["closeout_status"] = "failed"
            closeout_core_packet = {
                key: value
                for key, value in closeout.items()
                if key not in {"audit_record", "run_record", "real_invocation"}
            }
            epoch_closeout_checksum = checksum_json(closeout_core_packet)
            invocation["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
            chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["controlled_tick_status"], "completed")
            self.assertEqual(output["epoch"]["closeout_status"], "failed")
            self.assertEqual(output["real_invocation"]["closeout_status"], "failed")
            self.assertIn("controlled_loop_tick_audit_appended", output["side_effects"])
            self.assertIn("audit_record", output)

    def test_controlled_loop_tick_blocks_non_terminal_closeout_without_append(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            closeout["closeout_status"] = "blocked"
            invocation["closeout_status"] = "blocked"
            closeout_core_packet = {
                key: value
                for key, value in closeout.items()
                if key not in {"audit_record", "run_record", "real_invocation"}
            }
            epoch_closeout_checksum = checksum_json(closeout_core_packet)
            invocation["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["epoch_closeout_checksum"] = epoch_closeout_checksum
            closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
            chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertNotIn("audit_record", output)
            self.assertIn("closeout_not_completed", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_blocks_real_invocation_epoch_drift_without_append(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            invocation["epoch_id"] = "epoch-tampered"
            closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
            chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertNotIn("audit_record", output)
            self.assertIn("real_invocation_closeout_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_blocks_audit_required_identity_drift_without_append(self):
        cases = [
            ("missing source tick id", "loop_tick_identity_missing"),
            ("missing invocation id", "real_invocation_identity_missing"),
        ]
        for label, expected_code in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    chain = self.write_controlled_loop_tick_chain(tmp, repo)
                    if expected_code == "loop_tick_identity_missing":
                        loop_tick = json.loads(chain["loop_tick_path"].read_text(encoding="utf-8"))
                        loop_tick.pop("tick_id")
                        chain["loop_tick_path"].write_text(json.dumps(loop_tick), encoding="utf-8")
                    else:
                        invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
                        invocation.pop("invocation_id")
                        closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
                        closeout["real_invocation"].pop("invocation_id", None)
                        closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
                        chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")
                        chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")
                    before_audit_count = len(audit_records(tmp))

                    result, output = self.run_controlled_loop_tick_cli(tmp, chain)

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertNotIn("audit_record", output)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(len(audit_records(tmp)), before_audit_count)
                    replay_result, replay_output = run_cli(tmp, "audit-replay")
                    self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
                    self.assertTrue(replay_output["valid"])

    def test_controlled_loop_tick_blocks_unanchored_optional_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            git_pr_plan_path = Path(tmp) / "git-pr-plan.json"
            git_pr_plan_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "v1",
                        "schema_version": "git-pr-plan.v1",
                        "packet": "git_pr_plan",
                    }
                ),
                encoding="utf-8",
            )
            before_audit_count = len(audit_records(tmp))

            result, output = self.run_controlled_loop_tick_cli(tmp, chain, git_pr_plan_file=git_pr_plan_path)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("git_pr_plan_unanchored", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_controlled_loop_tick_reports_representative_stable_blocker_codes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            packet_paths = {
                "loop_tick": chain["loop_tick_path"],
                "execution_start": chain["execution_start_path"],
                "invocation_plan": chain["plan_path"],
                "real_invocation": chain["invocation_path"],
                "closeout": chain["closeout_path"],
            }
            pristine_packets = {
                name: json.loads(path.read_text(encoding="utf-8"))
                for name, path in packet_paths.items()
            }
            git_pr_plan_path = Path(tmp) / "mismatched-git-pr-plan.json"

            def fresh_packets():
                return {
                    name: json.loads(json.dumps(packet))
                    for name, packet in pristine_packets.items()
                }

            def write_packets(packets):
                for name, packet in packets.items():
                    packet_paths[name].write_text(json.dumps(packet), encoding="utf-8")

            cases = [
                (
                    "missing result evidence",
                    "result_evidence_missing",
                    lambda packets: None,
                    {"result_file": Path(tmp) / "missing-result.json"},
                ),
                (
                    "packet mismatch",
                    "controlled_tick_packet_mismatch",
                    lambda packets: packets["invocation_plan"].__setitem__("schema_version", "wrong-schema.v1"),
                    {},
                ),
                (
                    "loop tick not ready",
                    "loop_tick_not_ready",
                    lambda packets: packets["loop_tick"].__setitem__("recommended_next_action", "blocked"),
                    {},
                ),
                (
                    "execution start task mismatch",
                    "execution_start_task_mismatch",
                    lambda packets: packets["execution_start"].__setitem__("task_checksum", "sha256:" + "b" * 64),
                    {},
                ),
                (
                    "real invocation result mismatch",
                    "real_invocation_result_mismatch",
                    lambda packets: packets["real_invocation"].__setitem__("result_file", str(Path(tmp) / "other-result.json")),
                    {},
                ),
                (
                    "closeout task mismatch",
                    "closeout_task_mismatch",
                    lambda packets: packets["closeout"].__setitem__("task_file", str(Path(tmp) / "other-task.json")),
                    {},
                ),
                (
                    "closeout result mismatch",
                    "closeout_result_mismatch",
                    lambda packets: packets["closeout"].__setitem__("result_file", str(Path(tmp) / "other-result.json")),
                    {},
                ),
                (
                    "closeout snapshot mismatch",
                    "closeout_snapshot_mismatch",
                    lambda packets: packets["closeout"].__setitem__("snapshot_after_file", str(Path(tmp) / "other-snapshot.json")),
                    {},
                ),
                (
                    "git pr plan mismatch",
                    "git_pr_plan_mismatch",
                    lambda packets: (
                        packets["closeout"].__setitem__(
                            "git_pr_plan",
                            {
                                "protocol_version": "v1",
                                "schema_version": "git-pr-plan.v1",
                                "packet": "git_pr_plan",
                                "plan_id": "embedded-plan",
                            },
                        ),
                        git_pr_plan_path.write_text(
                            json.dumps(
                                {
                                    "protocol_version": "v1",
                                    "schema_version": "git-pr-plan.v1",
                                    "packet": "git_pr_plan",
                                    "plan_id": "supplied-plan",
                                }
                            ),
                            encoding="utf-8",
                        ),
                    ),
                    {"git_pr_plan_file": git_pr_plan_path},
                ),
            ]

            for label, expected_code, mutate, overrides in cases:
                with self.subTest(label=label):
                    if git_pr_plan_path.exists():
                        git_pr_plan_path.unlink()
                    packets = fresh_packets()
                    mutate(packets)
                    write_packets(packets)

                    result, output = self.run_controlled_loop_tick_cli(tmp, chain, **overrides)

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_tick_blocks_real_invocation_path_anchor_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            invocation = json.loads(chain["invocation_path"].read_text(encoding="utf-8"))
            invocation["record_file"] = str(Path(tmp) / "other-real-invocation.json")
            invocation["plan_file"] = str(Path(tmp) / "other-plan.json")
            chain["invocation_path"].write_text(json.dumps(invocation), encoding="utf-8")

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("real_invocation_record_mismatch", codes)
            self.assertIn("real_invocation_plan_mismatch", codes)

    def test_controlled_loop_tick_blocks_closeout_real_invocation_path_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            closeout = json.loads(chain["closeout_path"].read_text(encoding="utf-8"))
            closeout["real_invocation"]["path"] = str(Path(tmp) / "other-invocation.json")
            chain["closeout_path"].write_text(json.dumps(closeout), encoding="utf-8")

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("closeout_invocation_mismatch", {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_tick_blocks_invalid_snapshot_after_shape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            snapshot_after = json.loads(chain["snapshot_after_path"].read_text(encoding="utf-8"))
            snapshot_after.pop("readiness_evidence")
            chain["snapshot_after_path"].write_text(json.dumps(snapshot_after), encoding="utf-8")

            result, output = self.run_controlled_loop_tick_cli(tmp, chain)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("snapshot_after_invalid", {blocker["code"] for blocker in output["blockers"]})

    def test_controlled_loop_tick_reports_audit_append_failure_without_audit_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            chain = self.write_controlled_loop_tick_chain(tmp, repo)
            before_audit_count = len(audit_records(tmp))
            emitted = []
            args = self.controlled_loop_tick_args(tmp, chain)

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.controlled_loop_tick_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertEqual(emitted[0]["controlled_tick_status"], "blocked")
            self.assertNotIn("audit_record", emitted[0])
            self.assertEqual(emitted[0]["side_effects"], [])
            self.assertIn("controlled_loop_tick_audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_real_executor_invocation_closeout_accepts_relative_plan_invocation_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as other:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = run_cli_from(
                tmp,
                tmp,
                "invoke-real-executor",
                "--plan-file",
                plan_path.name,
                "--approval-secret",
                OPERATOR_APPROVAL_SECRET,
                "--side-effect-mode",
                "evidence_only",
            )

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            self.assertEqual(invocation_output["plan_file"], str(plan_path.resolve()))
            self.assertEqual(invocation_output["invocation_cwd"], str(Path(tmp).resolve()))
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after-relative-plan",
                        captured_at="2999-05-22T00:10:00Z",
                    )
                ),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli_from(
                other,
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
            self.assertTrue(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "completed")
            self.assertEqual(closeout_output["real_invocation"]["invocation_id"], invocation_output["invocation_id"])

    def test_real_executor_invocation_closeout_resolves_relative_readiness_task_file_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as other:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                tmp,
                repo,
                readiness_task_file_arg="executor-task.json",
                readiness_cwd=tmp,
            )
            self.assertEqual(inputs["readiness_packet"]["task"]["file"], "executor-task.json")
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after-relative-readiness-task",
                        captured_at="2999-05-22T00:10:00Z",
                    )
                ),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli_from(
                other,
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
            self.assertTrue(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "completed")

    def test_real_executor_invocation_closeout_blocks_structurally_when_update_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after-audit-failure",
                        captured_at="2999-05-22T00:10:00Z",
                    )
                ),
                encoding="utf-8",
            )
            records_before_closeout = audit_records(tmp)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "epoch_id": "epoch-1",
                    "task_file": str(task_path),
                    "result_file": str(result_path),
                    "snapshot_after_file": str(snapshot_after_path),
                    "run_record_file": None,
                    "real_invocation_file": str(invocation_path),
                    "allow_repo_local_root": False,
                    "cwd": repo,
                    "required_body_section": [],
                    "emit_git_pr_plan": False,
                    "pr_template_file": None,
                    "policy_file": None,
                    "base_branch": "main",
                    "branch_prefix": "codex/",
                },
            )()
            original_append = cadence_cli.append_audit_record

            def fail_real_invocation_update(root, record):
                if (
                    record.get("event") == "real_executor_invocation_record"
                    and record.get("action") == "update_real_executor_invocation_closeout"
                ):
                    raise OSError("disk full")
                return original_append(root, record)

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=fail_real_invocation_update):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.closeout_executor_result_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            output = emitted[0]
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "completed")
            self.assertEqual(output["epoch_status"], "COMPLETED")
            self.assertIn("real_invocation_audit_append_failed", {blocker["code"] for blocker in output["blockers"]})
            self.assertIn("epoch_completed", output["side_effects"])
            self.assertIn("real_executor_invocation_audit_append_failed", output["side_effects"])
            self.assertIn("real_executor_invocation_record_update_rolled_back", output["side_effects"])
            self.assertNotIn("real_executor_invocation_record_updated", output["side_effects"])
            self.assertNotIn("real_executor_invocation_audit_appended", output["side_effects"])
            self.assertNotIn("audit_record_appended", output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), invocation_output)
            self.assertTrue(output["real_invocation"]["rollback_record_restored"])
            self.assertEqual(output["real_invocation"]["after_checksum"], checksum_json(invocation_output))
            self.assertEqual(output["next_decision"]["recommended_next_action"], "recover_closeout_audit")
            self.assertEqual(audit_records(tmp), records_before_closeout)
            self.assertTrue((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_invoke_real_executor_blocks_structurally_when_audit_append_fails_after_run(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "plan_file": str(plan_path),
                    "approval_secret": OPERATOR_APPROVAL_SECRET,
                    "approval_secret_env": "CADENCE_OPERATOR_APPROVAL_SECRET",
                    "side_effect_mode": "evidence_only",
                    "allow_repo_local_root": False,
                    "max_plan_age_minutes": 15,
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.invoke_real_executor_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            output = emitted[0]
            self.assertFalse(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertEqual(output["recommended_next_action"], "inspect_runtime_state")
            self.assertIn("audit_append_failed", {blocker["code"] for blocker in output["blockers"]})
            self.assertIn("real_executor_invocation_audit_append_failed", output["side_effects"])
            record_path = Path(output["record_file"])
            expected_record = dict(output)
            expected_record.pop("audit_record_error", None)
            self.assertEqual(json.loads(record_path.read_text(encoding="utf-8")), expected_record)

    def test_invoke_real_executor_does_not_audit_blocked_record_when_rewrite_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "plan_file": str(plan_path),
                    "approval_secret": OPERATOR_APPROVAL_SECRET,
                    "approval_secret_env": "CADENCE_OPERATOR_APPROVAL_SECRET",
                    "side_effect_mode": "evidence_only",
                    "allow_repo_local_root": False,
                    "max_plan_age_minutes": 15,
                },
            )()

            with mock.patch.object(
                cadence_cli,
                "append_audit_record",
                side_effect=OSError("disk full"),
            ) as append_mock:
                with mock.patch.object(cadence_cli, "atomic_write_json", side_effect=OSError("write denied")):
                    with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                        code = cadence_cli.invoke_real_executor_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(append_mock.call_count, 1)
            self.assertEqual(len(emitted), 1)
            output = emitted[0]
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            blocker_codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("audit_append_failed", blocker_codes)
            self.assertIn("invocation_record_write_failed", blocker_codes)
            self.assertNotIn("audit_record_error", output)
            persisted_record = json.loads(Path(output["record_file"]).read_text(encoding="utf-8"))
            self.assertEqual(persisted_record["closeout_status"], "pending")
            self.assertTrue(persisted_record["valid"])

    def test_invoke_real_executor_blocks_stale_or_uninvocable_plan_before_start(self):
        cases = [
            (
                "stale-plan",
                lambda plan: plan.update({"checked_at": iso_z(datetime.now(timezone.utc) - timedelta(hours=1))}),
                "plan_packet_stale",
            ),
            (
                "not-invocable-plan",
                lambda plan: plan.update({"valid": False, "executor_invocation_planned": False}),
                "plan_not_invocable",
            ),
        ]
        for name, mutate, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    inputs, plan_path, plan = self.write_real_executor_invocation_plan(tmp, repo)
                    mutate(plan)
                    plan_path.write_text(json.dumps(plan), encoding="utf-8")

                    result, output = self.run_invoke_real_executor_cli(tmp, plan_path)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["executor_started"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertFalse(Path(inputs["task_packet"]["expected_output"]["evidence_path"]).exists())
                    invocation_dir = Path(tmp) / "real-executor-invocations"
                    self.assertEqual(list(invocation_dir.glob("*.json")) if invocation_dir.exists() else [], [])

    def test_invoke_real_executor_blocks_stale_result_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo, write_result=False)
            result_path = Path(inputs["task_packet"]["expected_output"]["evidence_path"])
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({"stale": True}), encoding="utf-8")

            result, output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertIn("executor_result_stale", {blocker["code"] for blocker in output["blockers"]})
            invocation_dir = Path(tmp) / "real-executor-invocations"
            self.assertEqual(list(invocation_dir.glob("*.json")) if invocation_dir.exists() else [], [])

    def test_invoke_real_executor_rechecks_drift_before_start(self):
        now = datetime.now(timezone.utc)

        def mutate_changed_head(tmp, repo, inputs):
            (Path(repo) / "README.md").write_text("changed before invoke\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "change before invoke")

        def mutate_brake(tmp, repo, inputs):
            (Path(tmp) / "brake.json").write_text(
                json.dumps(
                    {
                        "status": "NEUTRAL",
                        "reason": "operator pause",
                        "scope": "global",
                        "resume_requires": None,
                        "updated_at": utc_now(),
                    }
                ),
                encoding="utf-8",
            )

        def mutate_missing_epoch(tmp, repo, inputs):
            (Path(tmp) / "epochs" / "active" / "epoch-1.json").unlink()

        def mutate_expired_approval(tmp, repo, inputs):
            write_operator_approval(
                inputs["approval_path"],
                target_checksum=inputs["target_checksum"],
                purpose="real_executor_invocation",
                issued_at=iso_z(now - timedelta(hours=2)),
                expires_at=iso_z(now - timedelta(hours=1)),
            )

        def mutate_rollback_mismatch(tmp, repo, inputs):
            inputs["rollback_path"].write_text(
                json.dumps({**inputs["rollback_packet"], "task_checksum": "sha256:" + "0" * 64}),
                encoding="utf-8",
            )

        def mutate_denied_command(tmp, repo, inputs):
            task_path = Path(inputs["readiness_packet"]["task"]["file"])
            task_packet = json.loads(task_path.read_text(encoding="utf-8"))
            task_packet["command_policy"]["allowed_commands"] = ["git status"]
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

        cases = [
            ("changed-head", mutate_changed_head, "repo_head_mismatch"),
            ("non-drive-brake", mutate_brake, "brake_not_drive"),
            ("missing-epoch", mutate_missing_epoch, "active_epoch_missing"),
            ("expired-approval", mutate_expired_approval, "approval_recheck_failed"),
            ("rollback-mismatch", mutate_rollback_mismatch, "rollback_recheck_failed"),
            ("denied-command", mutate_denied_command, "executor_command_denied"),
        ]
        for name, mutate, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
                    mutate(tmp, repo, inputs)

                    result, output = self.run_invoke_real_executor_cli(tmp, plan_path)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["executor_started"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertFalse(Path(inputs["task_packet"]["expected_output"]["evidence_path"]).exists())

    def test_invoke_real_executor_rejects_unignored_repo_local_runtime_root_from_plan_cwd(self):
        with tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            (Path(repo) / ".gitignore").write_text(".cadence-runtime/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore runtime root")
            _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(runtime_root, repo)
            (Path(repo) / ".gitignore").write_text("", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "unignore runtime root")

            result, output = run_cli_from(
                repo,
                runtime_root,
                "invoke-real-executor",
                "--plan-file",
                str(plan_path),
                "--approval-secret",
                OPERATOR_APPROVAL_SECRET,
                "--side-effect-mode",
                "evidence_only",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertIn("runtime_root_unsafe", {blocker["code"] for blocker in output["blockers"]})

    def test_invoke_real_executor_honors_repo_local_runtime_root_override(self):
        with tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            (Path(repo) / ".gitignore").write_text(".cadence-runtime/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore runtime root")
            _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(runtime_root, repo)
            (Path(repo) / ".gitignore").write_text("", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "unignore runtime root")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "--allow-repo-local-root",
                    "invoke-real-executor",
                    "--plan-file",
                    str(plan_path),
                    "--approval-secret",
                    OPERATOR_APPROVAL_SECRET,
                    "--side-effect-mode",
                    "evidence_only",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            output = json.loads(result.stdout) if result.stdout.strip() else None

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["side_effects"], [])
            self.assertIn("repo_head_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertNotIn("runtime_root_unsafe", {blocker["code"] for blocker in output["blockers"]})

    def test_invoke_real_executor_replaces_invalid_process_output_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo, invalid_output=True)

            result, output = self.run_invoke_real_executor_cli(
                tmp,
                plan_path,
                side_effect_mode="evidence_only",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertTrue(Path(output["record_file"]).exists())
            self.assertEqual(
                Path(output["stdout_log"]).read_text(encoding="utf-8"),
                "stdout invalid byte: \ufffd\n",
            )
            self.assertEqual(
                Path(output["stderr_log"]).read_text(encoding="utf-8"),
                "stderr invalid byte: \ufffd\n",
            )

    def test_invoke_real_executor_enforces_result_and_side_effect_modes(self):
        bogus_head = "0" * 40
        bogus_materialized_evidence = {
            "status": "verified",
            "source": "real_executor_invocation.local_diff",
            "task_id": "wrong-task",
            "resulting_head": "0" * 40,
            "files": ["bogus.txt"],
            "limitations": [],
        }
        self_consistent_false_head_evidence = {
            "status": "verified",
            "source": "real_executor_invocation.local_diff",
            "task_id": "candidate-1",
            "resulting_head": bogus_head,
            "files": ["README.md"],
            "limitations": [],
        }
        cases = [
            (
                "missing-result",
                {"write_result": False},
                "evidence_only",
                "executor_result_missing",
                False,
                None,
            ),
            (
                "timeout",
                {
                    "timeout_seconds": 3,
                    "sleep_seconds": 10,
                    "stdout_text": "stdout before timeout\n",
                    "stderr_text": "stderr before timeout\n",
                },
                "evidence_only",
                "executor_process_timeout",
                False,
                {
                    "stdout": "stdout before timeout\n",
                    "stderr": "stderr before timeout\n",
                },
            ),
            (
                "evidence-only-dirty",
                {"touch_repo": True, "include_materialized_change_evidence": True},
                "evidence_only",
                "unexpected_repo_modification",
                True,
                None,
            ),
            (
                "evidence-only-clean-claimed-materialized-evidence",
                {
                    "include_materialized_change_evidence": True,
                    "files_changed": ["README.md"],
                },
                "evidence_only",
                "unexpected_repo_modification",
                False,
                None,
            ),
            (
                "materialized-missing-evidence",
                {"touch_repo": True},
                "materialized_changes",
                "materialized_change_evidence_missing",
                True,
                None,
            ),
            (
                "materialized-bogus-evidence",
                {"touch_repo": True, "materialized_change_evidence": bogus_materialized_evidence},
                "materialized_changes",
                "materialized_change_evidence_missing",
                True,
                None,
            ),
            (
                "materialized-self-consistent-false-head",
                {
                    "touch_repo": True,
                    "materialized_change_evidence": self_consistent_false_head_evidence,
                    "resulting_head": bogus_head,
                },
                "materialized_changes",
                "materialized_change_evidence_missing",
                True,
                None,
            ),
            (
                "materialized-evidence",
                {"touch_repo": True, "include_materialized_change_evidence": True},
                "materialized_changes",
                None,
                True,
                None,
            ),
            (
                "materialized-clean-claimed-materialized-evidence",
                {
                    "include_materialized_change_evidence": True,
                    "files_changed": ["README.md"],
                },
                "materialized_changes",
                "materialized_change_evidence_missing",
                False,
                None,
            ),
            (
                "post-process-repo-missing",
                {"delete_git": True},
                "evidence_only",
                "unexpected_repo_modification",
                None,
                None,
            ),
        ]
        for name, plan_options, side_effect_mode, expected_code, expect_dirty, expected_logs in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo, **plan_options)

                    result, output = self.run_invoke_real_executor_cli(
                        tmp,
                        plan_path,
                        side_effect_mode=side_effect_mode,
                    )

                    self.assertEqual(result.returncode, 0 if expected_code is None else 2, result.stderr)
                    self.assertEqual(output["side_effect_mode"], side_effect_mode)
                    self.assertTrue(output["executor_started"])
                    self.assertEqual(output["repository_after"]["dirty_worktree"], expect_dirty)
                    self.assertTrue(Path(output["record_file"]).exists())
                    persisted_invocation = json.loads(Path(output["record_file"]).read_text(encoding="utf-8"))
                    self.assertEqual(
                        persisted_invocation["materialized_change_evidence"],
                        output["materialized_change_evidence"],
                    )
                    if expected_logs is not None:
                        self.assertEqual(Path(output["stdout_log"]).read_text(encoding="utf-8"), expected_logs["stdout"])
                        self.assertEqual(Path(output["stderr_log"]).read_text(encoding="utf-8"), expected_logs["stderr"])
                    if expected_code is None:
                        self.assertTrue(output["valid"])
                        self.assertEqual(output["blockers"], [])
                        self.assertEqual(output["materialized_change_evidence"]["status"], "verified")
                    else:
                        self.assertFalse(output["valid"])
                        self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    if name in {
                        "evidence-only-dirty",
                        "evidence-only-clean-claimed-materialized-evidence",
                        "materialized-clean-claimed-materialized-evidence",
                    }:
                        self.assertEqual(output["materialized_change_evidence"]["status"], "absent")
                        self.assertEqual(output["materialized_change_evidence"]["files"], [])

    def test_invoke_real_executor_blocks_hidden_branch_ref_changes(self):
        cases = (
            (
                "added",
                {"create_branch": "codex/hidden-side-effect"},
                lambda repo: None,
                lambda changes: self.assertIn("codex/hidden-side-effect", changes["added"]),
            ),
            (
                "removed",
                {"delete_branch": "codex/preexisting-side-effect"},
                lambda repo: git(repo, "branch", "codex/preexisting-side-effect"),
                lambda changes: self.assertIn("codex/preexisting-side-effect", changes["removed"]),
            ),
            (
                "changed",
                {"retarget_branch": "codex/retargeted-side-effect"},
                lambda repo: (
                    (Path(repo) / "second.txt").write_text("second\n", encoding="utf-8"),
                    git(repo, "add", "second.txt"),
                    git(repo, "commit", "-m", "second"),
                    git(repo, "branch", "codex/retargeted-side-effect"),
                ),
                lambda changes: self.assertIn("codex/retargeted-side-effect", changes["changed"]),
            ),
        )
        for change_kind, plan_options, arrange, assert_change in cases:
            for side_effect_mode in ("evidence_only", "materialized_changes"):
                with self.subTest(change_kind=change_kind, side_effect_mode=side_effect_mode):
                    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                        init_committed_repo(repo)
                        arrange(repo)
                        _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                            tmp,
                            repo,
                            **plan_options,
                        )

                        result, output = self.run_invoke_real_executor_cli(
                            tmp,
                            plan_path,
                            side_effect_mode=side_effect_mode,
                        )

                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertFalse(output["valid"])
                        self.assertTrue(output["executor_started"])
                        self.assertFalse(output["repository_after"]["dirty_worktree"])
                        self.assertEqual(output["repository_after"]["head"], output["repository_before"]["head"])
                        self.assertEqual(output["repository_after"]["branch"], output["repository_before"]["branch"])
                        blockers = output["blockers"]
                        self.assertIn("unexpected_repo_modification", {blocker["code"] for blocker in blockers})
                        branch_ref_blocker = next(
                            blocker
                            for blocker in blockers
                            if blocker["code"] == "unexpected_repo_modification"
                            and "local_branch_ref_changes" in blocker
                        )
                        assert_change(branch_ref_blocker["local_branch_ref_changes"])

    def test_invoke_real_executor_records_created_branch_ref_in_repository_evidence(self):
        for side_effect_mode in ("evidence_only", "materialized_changes"):
            with self.subTest(side_effect_mode=side_effect_mode):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    _inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                        tmp,
                        repo,
                        create_branch="codex/hidden-side-effect",
                    )

                    result, output = self.run_invoke_real_executor_cli(
                        tmp,
                        plan_path,
                        side_effect_mode=side_effect_mode,
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertTrue(output["executor_started"])
                    self.assertFalse(output["repository_after"]["dirty_worktree"])
                    self.assertEqual(output["repository_after"]["head"], output["repository_before"]["head"])
                    self.assertEqual(output["repository_after"]["branch"], output["repository_before"]["branch"])
                    self.assertIn("unexpected_repo_modification", {blocker["code"] for blocker in output["blockers"]})
                    self.assertIn("codex/hidden-side-effect", output["repository_after"]["local_branch_refs"])

    def test_executor_invocation_plan_blocks_stale_and_mismatched_anchors(self):
        now = datetime.now(timezone.utc)

        def mutate_ownership_epoch(tmp, repo, inputs):
            ownership_path = Path(inputs["readiness_packet"]["ownership"]["path"])
            ownership_record = json.loads(ownership_path.read_text(encoding="utf-8"))
            ownership_record["epoch_id"] = "epoch-other"
            ownership_path.write_text(json.dumps(ownership_record), encoding="utf-8")
            return {}

        def mutate_active_epoch_checksum(tmp, repo, inputs):
            epoch_path = Path(tmp) / "epochs" / "active" / "epoch-1.json"
            epoch_record = json.loads(epoch_path.read_text(encoding="utf-8"))
            epoch_record["tasks"][0]["executor_task_checksum"] = "sha256:" + "0" * 64
            epoch_path.write_text(json.dumps(epoch_record), encoding="utf-8")
            return {}

        def mutate_duplicate_active_epoch_task(tmp, repo, inputs):
            epoch_path = Path(tmp) / "epochs" / "active" / "epoch-1.json"
            epoch_record = json.loads(epoch_path.read_text(encoding="utf-8"))
            duplicate_task = dict(epoch_record["tasks"][0])
            duplicate_task["executor_task_checksum"] = "sha256:" + "0" * 64
            epoch_record["tasks"].append(duplicate_task)
            epoch_path.write_text(json.dumps(epoch_record), encoding="utf-8")
            return {}

        def mutate_duplicate_other_active_epoch_task(tmp, repo, inputs):
            epoch_path = Path(tmp) / "epochs" / "active" / "epoch-1.json"
            epoch_record = json.loads(epoch_path.read_text(encoding="utf-8"))
            other_task = {
                "id": "other-task",
                "task_type": "execution",
                "executor_task_checksum": "sha256:" + "1" * 64,
            }
            epoch_record["tasks"].extend([other_task, dict(other_task)])
            epoch_path.write_text(json.dumps(epoch_record), encoding="utf-8")
            return {}

        def mutate_completed_active_epoch_task(tmp, repo, inputs):
            epoch_path = Path(tmp) / "epochs" / "active" / "epoch-1.json"
            epoch_record = json.loads(epoch_path.read_text(encoding="utf-8"))
            epoch_record["completed_tasks"] = [inputs["task_packet"]["task"]["id"]]
            epoch_path.write_text(json.dumps(epoch_record), encoding="utf-8")
            return {}

        cases = [
            (
                "stale-readiness",
                lambda tmp, repo, inputs: (
                    inputs["readiness_packet"].update({"checked_at": iso_z(now - timedelta(hours=1))}),
                    inputs["readiness_path"].write_text(json.dumps(inputs["readiness_packet"]), encoding="utf-8"),
                    {},
                )[2],
                "readiness_packet_stale",
            ),
            (
                "changed-head",
                lambda tmp, repo, inputs: (
                    (Path(repo) / "README.md").write_text("changed after readiness\n", encoding="utf-8"),
                    git(repo, "add", "README.md"),
                    git(repo, "commit", "-m", "change after readiness"),
                    {},
                )[3],
                "repo_head_mismatch",
            ),
            (
                "ownership-epoch-mismatch",
                mutate_ownership_epoch,
                "ownership_epoch_mismatch",
            ),
            (
                "active-epoch-task-checksum-mismatch",
                mutate_active_epoch_checksum,
                "task_checksum_mismatch",
            ),
            (
                "active-epoch-duplicate-task-id",
                mutate_duplicate_active_epoch_task,
                "active_epoch_task_duplicate",
            ),
            (
                "active-epoch-duplicate-other-task-id",
                mutate_duplicate_other_active_epoch_task,
                "active_epoch_task_duplicate",
            ),
            (
                "active-epoch-task-already-completed",
                mutate_completed_active_epoch_task,
                "active_epoch_task_completed",
            ),
            (
                "wrong-approval-purpose",
                lambda tmp, repo, inputs: (
                    write_operator_approval(
                        inputs["approval_path"],
                        target_checksum=inputs["target_checksum"],
                        purpose="start_governed_execution",
                    ),
                    {},
                )[1],
                "approval_purpose_mismatch",
            ),
            (
                "expired-approval",
                lambda tmp, repo, inputs: (
                    write_operator_approval(
                        inputs["approval_path"],
                        target_checksum=inputs["target_checksum"],
                        purpose="real_executor_invocation",
                        issued_at=iso_z(now - timedelta(hours=2)),
                        expires_at=iso_z(now - timedelta(hours=1)),
                    ),
                    {},
                )[1],
                "approval_expired",
            ),
            (
                "invalid-adapter",
                lambda tmp, repo, inputs: (
                    inputs["adapter_path"].write_text(
                        json.dumps({**inputs["adapter_packet"], "schema_version": "executor-adapter.v2"}),
                        encoding="utf-8",
                    ),
                    {},
                )[1],
                "adapter_contract_invalid",
            ),
            (
                "invalid-result-path",
                lambda tmp, repo, inputs: {
                    "expected_result_path": str(Path(repo) / "executor-result-outside-runtime.json")
                },
                "result_path_outside_runtime",
            ),
            (
                "malformed-rebound-task-result-path",
                lambda tmp, repo, inputs: (
                    inputs["task_packet"]["expected_output"].update({"evidence_path": None}),
                    Path(inputs["readiness_packet"]["task"]["file"]).write_text(
                        json.dumps(inputs["task_packet"]),
                        encoding="utf-8",
                    ),
                    {},
                )[2],
                "executor_task_invalid",
            ),
            (
                "invalid-cwd",
                lambda tmp, repo, inputs: {"cwd": str(Path(tmp) / "not-a-git-repo")},
                "repo_inspection_failed",
            ),
        ]
        for name, mutate, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    inputs = self.write_valid_executor_invocation_plan_inputs(tmp, repo)
                    overrides = mutate(tmp, repo, inputs)
                    audit_before = audit_records(tmp)

                    result, output = self.run_executor_invocation_plan_cli(tmp, repo, inputs, **overrides)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["executor_started"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertEqual(audit_records(tmp), audit_before)

    def test_executor_invocation_readiness_accepts_matching_epoch_ownership_and_role_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            status_result, _status = run_cli(tmp, "status")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            task_path, task_packet, _approval_token = write_governed_execution_task(tmp, repo)
            task_packet["expected_output"]["evidence_path"] = str(Path(tmp) / "executor-results" / "executor-result.json")
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            task_checksum = checksum_json(task_packet)
            epoch_path = write_active_epoch(
                tmp,
                "epoch-1",
                valid_snapshot(
                    repo="local/test",
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                tasks=[
                    {
                        "id": task_packet["task"]["id"],
                        "task_type": "execution",
                        "executor_task_checksum": task_checksum,
                    }
                ],
            )
            ownership_path, ownership_before = write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=current_branch(repo),
                head=current_head(repo),
                epoch_id="epoch-1",
                handoff_id=None,
            )
            role_path, _role_packet = write_executor_readiness_role_packet(
                Path(tmp) / "role-readiness.json",
                task_packet,
            )
            epoch_before = json.loads(epoch_path.read_text(encoding="utf-8"))

            result, output = run_cli(
                tmp,
                "executor-invocation-readiness",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--epoch-id",
                "epoch-1",
                "--ownership-target",
                "ownership-1",
                "--expected-result-path",
                task_packet["expected_output"]["evidence_path"],
                "--role-readiness-file",
                str(role_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "executor-invocation-readiness.v1")
            self.assertEqual(output["packet"], "executor_invocation_readiness")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_invocation_ready"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["recommended_next_action"], "invoke_real_executor")
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["task"]["checksum"], task_checksum)
            self.assertEqual(output["active_epoch"]["id"], "epoch-1")
            self.assertEqual(output["ownership"]["id"], "ownership-1")
            self.assertTrue(output["role_readiness"]["valid"])
            self.assertNotIn("executor_pid", output)
            self.assertEqual(json.loads(epoch_path.read_text(encoding="utf-8")), epoch_before)
            self.assertEqual(json.loads(ownership_path.read_text(encoding="utf-8")), ownership_before)
            self.assertEqual(audit_records(tmp), [])
            self.assertEqual(current_head(repo), task_packet["repo"]["head"])

    def test_executor_invocation_readiness_accepts_repo_subdirectory_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            status_result, _status = run_cli(tmp, "status")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            subdir = Path(repo) / "src"
            subdir.mkdir()
            task_path, task_packet, _approval_token = write_governed_execution_task(tmp, repo)
            task_packet["expected_output"]["evidence_path"] = str(Path(tmp) / "executor-results" / "executor-result.json")
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            write_active_epoch(
                tmp,
                "epoch-1",
                valid_snapshot(
                    repo="local/test",
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                tasks=[
                    {
                        "id": task_packet["task"]["id"],
                        "task_type": "execution",
                        "executor_task_checksum": checksum_json(task_packet),
                    }
                ],
            )
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=current_branch(repo),
                head=current_head(repo),
                epoch_id="epoch-1",
                handoff_id=None,
            )

            result, output = run_cli(
                tmp,
                "executor-invocation-readiness",
                "--cwd",
                str(subdir),
                "--task-file",
                str(task_path),
                "--epoch-id",
                "epoch-1",
                "--ownership-target",
                "ownership-1",
                "--expected-result-path",
                task_packet["expected_output"]["evidence_path"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["recommended_next_action"], "invoke_real_executor")
            self.assertEqual(output["repository"]["requested_cwd"], str(subdir.resolve()))
            self.assertEqual(output["repository"]["cwd"], str(Path(repo).resolve()))

    def test_executor_invocation_readiness_converts_path_resolution_errors_to_blockers(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_packet = governed_execution_task_packet(tmp, repo)
            task_packet["repo"]["path"] = str(Path(repo) / "bad-repo-path")
            original_resolve = Path.resolve

            def guarded_resolve(path_self, *args, **kwargs):
                if "bad-repo-path" in str(path_self) or "bad-result-path" in str(path_self):
                    raise ValueError("synthetic path resolution failure")
                return original_resolve(path_self, *args, **kwargs)

            with mock.patch.object(Path, "resolve", guarded_resolve):
                _repository, repo_blockers = executor_readiness._repo_blockers(
                    cwd=Path(repo),
                    task_packet=task_packet,
                )

            self.assertIn("repo_path_invalid", {blocker["code"] for blocker in repo_blockers})

            task_packet = governed_execution_task_packet(tmp, repo)
            supplied_path = Path(tmp) / "executor-results" / "executor-result.json"
            task_packet["expected_output"]["evidence_path"] = str(Path(tmp) / "executor-results" / "bad-result-path.json")
            with mock.patch.object(Path, "resolve", guarded_resolve):
                result_blockers = executor_readiness._result_path_blockers(
                    root=Path(tmp),
                    task_packet=task_packet,
                    expected_result_path=supplied_path,
                )

            self.assertIn("result_path_invalid", {blocker["code"] for blocker in result_blockers})

    def test_executor_invocation_readiness_blocks_unready_inputs_without_side_effects(self):
        def rewrite_ownership_epoch(root, ownership_id, epoch_id):
            path = Path(root) / "work-ownership" / "active" / f"{ownership_id}.json"
            packet = json.loads(path.read_text(encoding="utf-8"))
            packet["epoch_id"] = epoch_id
            path.write_text(json.dumps(packet), encoding="utf-8")

        cases = [
            (
                "missing-ownership",
                lambda tmp, repo, task_path, task_packet: {
                    "args": ["--ownership-target", "missing-ownership"],
                    "expected_code": "ownership_record_missing",
                    "expected_action": "fix_ownership",
                },
            ),
            (
                "dirty-worktree",
                lambda tmp, repo, task_path, task_packet: (
                    (Path(repo) / "README.md").write_text("dirty before real executor readiness\n", encoding="utf-8"),
                    {
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "dirty_worktree",
                        "expected_action": "operator_review",
                    },
                )[1],
            ),
            (
                "non-drive-brake",
                lambda tmp, repo, task_path, task_packet: (
                    run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop"),
                    {
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "brake_not_drive",
                        "expected_action": "operator_review",
                    },
                )[1],
            ),
            (
                "repo-path-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["repo"].update({"path": str((Path(repo).parent / "different-repo").resolve())}),
                    task_packet["snapshot"].update({"cwd": task_packet["repo"]["path"]}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "repo_path_mismatch",
                        "expected_action": "refresh_task_evidence",
                    },
                )[3],
            ),
            (
                "repo-branch-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["repo"].update({"branch": "codex/not-current"}),
                    task_packet["snapshot"].update({"branch": "codex/not-current"}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "repo_branch_mismatch",
                        "expected_action": "refresh_task_evidence",
                    },
                )[3],
            ),
            (
                "repo-head-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["repo"].update({"head": "0" * 40}),
                    task_packet["snapshot"].update({"head": "0" * 40}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "repo_head_mismatch",
                        "expected_action": "refresh_task_evidence",
                    },
                )[3],
            ),
            (
                "task-checksum-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    write_active_epoch(
                        tmp,
                        "epoch-1",
                        valid_snapshot(
                            repo="local/test",
                            cwd=str(Path(repo).resolve()),
                            branch=current_branch(repo),
                            head=current_head(repo),
                        ),
                        tasks=[
                            {
                                "id": task_packet["task"]["id"],
                                "task_type": "execution",
                                "executor_task_checksum": "sha256:" + "0" * 64,
                            }
                        ],
                    ),
                    {
                        "skip_epoch": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "task_checksum_mismatch",
                        "expected_action": "refresh_task_evidence",
                        "expected_blocker_fields": {
                            "expected": checksum_json(task_packet),
                            "actual": "sha256:" + "0" * 64,
                        },
                    },
                )[1],
            ),
            (
                "missing-active-epoch",
                lambda tmp, repo, task_path, task_packet: (
                    (Path(tmp) / "epochs" / "active" / "epoch-1.json").unlink(),
                    {
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "active_epoch_missing",
                        "expected_action": "close_or_fail_active_epoch",
                    },
                )[1],
            ),
            (
                "active-epoch-id-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    rewrite_ownership_epoch(tmp, "ownership-1", "epoch-2"),
                    {
                        "args": ["--epoch-id", "epoch-2", "--ownership-target", "ownership-1"],
                        "expected_code": "active_epoch_id_mismatch",
                        "expected_action": "close_or_fail_active_epoch",
                    },
                )[1],
            ),
            (
                "ownership-epoch-mismatch",
                lambda tmp, repo, task_path, task_packet: (
                    rewrite_ownership_epoch(tmp, "ownership-1", "epoch-2"),
                    {
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "ownership_epoch_mismatch",
                        "expected_action": "fix_ownership",
                    },
                )[1],
            ),
            (
                "invalid-result-path",
                lambda tmp, repo, task_path, task_packet: {
                    "args": [
                        "--ownership-target",
                        "ownership-1",
                        "--expected-result-path",
                        str(Path(repo) / "executor-result.json"),
                    ],
                    "expected_code": "result_path_outside_runtime",
                    "expected_action": "inspect_policy_blockers",
                },
            ),
            (
                "malformed-command-policy",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet.update({"command_policy": []}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "command_policy_invalid",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "missing-branch-policy",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet.pop("branch_policy"),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "branch_policy_invalid",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "branch-policy-current-main-disallowed",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["branch_policy"].update({"allow_current_branch_main": False}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "branch_policy_current_branch_main_disallowed",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "required-checks-missing",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet.update({"required_checks": []}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "required_checks_missing",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "required-check-denied-by-command-policy",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["command_policy"].update({"denied_commands": ["python -m unittest"]}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "required_checks_invalid",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "required-check-outside-allowed-command-policy",
                lambda tmp, repo, task_path, task_packet: (
                    task_packet["command_policy"].update({"allowed_commands": ["python scripts/validate_protocol.py"]}),
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8"),
                    {
                        "rewrite_epoch_checksum": True,
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "required_checks_invalid",
                        "expected_action": "inspect_policy_blockers",
                    },
                )[2],
            ),
            (
                "non-object-task-json",
                lambda tmp, repo, task_path, task_packet: (
                    task_path.write_text(json.dumps(["not", "a", "packet"]), encoding="utf-8"),
                    {
                        "args": ["--ownership-target", "ownership-1"],
                        "expected_code": "executor_task_invalid",
                        "expected_action": "refresh_task_evidence",
                    },
                )[1],
            ),
            (
                "unreadable-role-readiness",
                lambda tmp, repo, task_path, task_packet: {
                    "args": [
                        "--ownership-target",
                        "ownership-1",
                        "--role-readiness-file",
                        str(Path(tmp) / "missing-role-readiness.json"),
                    ],
                    "expected_code": "role_readiness_unreadable",
                    "expected_action": "operator_review",
                },
            ),
            (
                "failed-role-readiness",
                lambda tmp, repo, task_path, task_packet: (
                    write_executor_readiness_role_packet(
                        Path(tmp) / "role-readiness.json",
                        task_packet,
                        valid=False,
                    ),
                    {
                        "args": [
                            "--ownership-target",
                            "ownership-1",
                            "--role-readiness-file",
                            str(Path(tmp) / "role-readiness.json"),
                        ],
                        "expected_code": "role_readiness_blocked",
                        "expected_action": "operator_review",
                    },
                )[1],
            ),
            (
                "wrong-role-protocol-version",
                lambda tmp, repo, task_path, task_packet: (
                    write_executor_readiness_role_packet(
                        Path(tmp) / "role-readiness.json",
                        task_packet,
                        protocol_version="v0",
                    ),
                    {
                        "args": [
                            "--ownership-target",
                            "ownership-1",
                            "--role-readiness-file",
                            str(Path(tmp) / "role-readiness.json"),
                        ],
                        "expected_code": "role_readiness_invalid",
                        "expected_action": "operator_review",
                    },
                )[1],
            ),
            (
                "missing-role-scope",
                lambda tmp, repo, task_path, task_packet: (
                    write_executor_readiness_role_packet(
                        Path(tmp) / "role-readiness.json",
                        task_packet,
                        scope={},
                    ),
                    {
                        "args": [
                            "--ownership-target",
                            "ownership-1",
                            "--role-readiness-file",
                            str(Path(tmp) / "role-readiness.json"),
                        ],
                        "expected_code": "role_readiness_scope_mismatch",
                        "expected_action": "operator_review",
                    },
                )[1],
            ),
        ]
        for name, setup_case in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    status_result, _status = run_cli(tmp, "status")
                    self.assertEqual(status_result.returncode, 0, status_result.stderr)
                    task_path, task_packet, _approval_token = write_governed_execution_task(tmp, repo)
                    task_packet["expected_output"]["evidence_path"] = str(Path(tmp) / "executor-results" / "executor-result.json")
                    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
                    if name != "task-checksum-mismatch":
                        write_active_epoch(
                            tmp,
                            "epoch-1",
                            valid_snapshot(
                                repo="local/test",
                                cwd=str(Path(repo).resolve()),
                                branch=current_branch(repo),
                                head=current_head(repo),
                            ),
                            tasks=[
                                {
                                    "id": task_packet["task"]["id"],
                                    "task_type": "execution",
                                    "executor_task_checksum": checksum_json(task_packet),
                                }
                            ],
                        )
                    write_work_ownership(
                        tmp,
                        "ownership-1",
                        task_id=task_packet["task"]["id"],
                        candidate_id=task_packet["task"]["id"],
                        branch=current_branch(repo),
                        head=current_head(repo),
                        epoch_id="epoch-1",
                        handoff_id=None,
                    )
                    case = setup_case(tmp, repo, task_path, task_packet)
                    if case.get("rewrite_epoch_checksum"):
                        epoch_file = Path(tmp) / "epochs" / "active" / "epoch-1.json"
                        epoch = json.loads(epoch_file.read_text(encoding="utf-8"))
                        epoch["tasks"][0]["executor_task_checksum"] = checksum_json(task_packet)
                        epoch_file.write_text(json.dumps(epoch), encoding="utf-8")
                    epoch_files_before = {
                        path.name: path.read_text(encoding="utf-8")
                        for path in (Path(tmp) / "epochs" / "active").glob("*.json")
                    }
                    ownership_files_before = {
                        path.name: path.read_text(encoding="utf-8")
                        for path in (Path(tmp) / "work-ownership" / "active").glob("*.json")
                    }
                    args = [
                        "executor-invocation-readiness",
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--epoch-id",
                        "epoch-1",
                        "--expected-result-path",
                        task_packet["expected_output"]["evidence_path"],
                    ]
                    args.extend(case["args"])

                    result, output = run_cli(tmp, *args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["executor_invocation_ready"])
                    self.assertFalse(output["executor_started"])
                    self.assertEqual(output["side_effects"], [])
                    self.assertEqual(output["recommended_next_action"], case["expected_action"])
                    self.assertIn(case["expected_code"], {blocker["code"] for blocker in output["blockers"]})
                    if "expected_blocker_fields" in case:
                        matching_blocker = next(
                            blocker for blocker in output["blockers"] if blocker["code"] == case["expected_code"]
                        )
                        for field, value in case["expected_blocker_fields"].items():
                            self.assertEqual(matching_blocker.get(field), value)
                    epoch_files_after = {
                        path.name: path.read_text(encoding="utf-8")
                        for path in (Path(tmp) / "epochs" / "active").glob("*.json")
                    }
                    ownership_files_after = {
                        path.name: path.read_text(encoding="utf-8")
                        for path in (Path(tmp) / "work-ownership" / "active").glob("*.json")
                    }
                    self.assertEqual(epoch_files_after, epoch_files_before)
                    self.assertEqual(ownership_files_after, ownership_files_before)
                    self.assertEqual(audit_records(tmp), [])

    def test_executor_invocation_readiness_blocks_symlinked_result_directory(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            status_result, _status = run_cli(tmp, "status")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            outside_results = Path(tmp) / "outside-results"
            outside_results.mkdir()
            result_dir = Path(tmp) / "executor-results"
            try:
                os.symlink(outside_results, result_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            task_path, task_packet, _approval_token = write_governed_execution_task(tmp, repo)
            task_packet["expected_output"]["evidence_path"] = str(result_dir / "executor-result.json")
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            write_active_epoch(
                tmp,
                "epoch-1",
                valid_snapshot(
                    repo="local/test",
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                tasks=[
                    {
                        "id": task_packet["task"]["id"],
                        "task_type": "execution",
                        "executor_task_checksum": checksum_json(task_packet),
                    }
                ],
            )
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=current_branch(repo),
                head=current_head(repo),
                epoch_id="epoch-1",
                handoff_id=None,
            )

            result, output = run_cli(
                tmp,
                "executor-invocation-readiness",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--epoch-id",
                "epoch-1",
                "--ownership-target",
                "ownership-1",
                "--expected-result-path",
                task_packet["expected_output"]["evidence_path"],
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("result_path_outside_runtime", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["side_effects"], [])

    def test_loop_tick_policy_file_bounds_executor_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "required_checks": ["python -m unittest tests.test_cadence"],
                        "max_executor_time_minutes": 15,
                        "stop_conditions": ["brake_not_drive", "timeout"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["allowed_paths"], ["codex_cadence"])
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(executor_task["limits"]["max_minutes"], 15)
            self.assertEqual(
                executor_task["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout"],
            )
            self.assertEqual(output["policy"]["source"], str(policy_file))

    def test_loop_tick_policy_file_emits_executor_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "allowed_commands": ["python -m unittest tests.test_cadence"],
                        "denied_commands": ["python -m pip install"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertEqual(
                output["executor_task"]["command_policy"],
                {
                    "allowed_commands": ["python -m unittest tests.test_cadence"],
                    "denied_commands": ["python -m pip install"],
                },
            )
            self.assertEqual(output["policy"]["allowed_commands"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(output["policy"]["denied_commands"], ["python -m pip install"])

    def test_loop_tick_policy_file_emits_executor_branch_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            branch_policy = {
                "allowed_base_branches": ["main"],
                "denied_target_branches": ["main", "release"],
                "required_branch_prefixes": ["codex/"],
                "allow_current_branch_main": False,
            }
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "branch_policy": branch_policy,
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertEqual(output["policy"]["branch_policy"], branch_policy)
            self.assertEqual(output["executor_task"]["branch_policy"], branch_policy)

    def test_loop_tick_policy_file_partial_branch_policy_allows_current_main_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "branch_policy": {
                            "allowed_base_branches": ["main"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["policy"]["branch_policy"]["allow_current_branch_main"])
            self.assertTrue(output["executor_task"]["branch_policy"]["allow_current_branch_main"])

    def test_loop_tick_policy_file_rejects_malformed_branch_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "branch_policy": {
                            "allowed_base_branches": "main",
                            "allow_current_branch_main": "false",
                        },
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIsNone(output)
            self.assertIn("loop policy branch_policy.allowed_base_branches must be a list", result.stderr)

    def test_loop_tick_policy_file_rejects_unknown_branch_policy_keys(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "branch_policy": {
                            "required_branch_prefix": ["codex/"],
                            "allow_current_branch_main": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIsNone(output)
            self.assertIn("loop policy branch_policy contains unknown keys: required_branch_prefix", result.stderr)

    def test_loop_tick_policy_file_keeps_policy_stop_conditions_with_cli_additions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "stop_conditions": ["requires_review"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--stop-condition",
                "custom_stop",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output["executor_task"]["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "requires_review", "custom_stop"],
            )

    def test_loop_tick_policy_file_trims_checks_and_stop_conditions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "required_checks": [" python -m unittest tests.test_cadence "],
                        "stop_conditions": [" requires_review "],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(
                executor_task["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "requires_review"],
            )

    def test_loop_tick_policy_file_rejects_allowed_path_with_nul(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence\0bad"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("loop policy allowed_paths[0] must be repo-relative", result.stderr)

    def test_loop_tick_keeps_builtin_stop_conditions_with_cli_additions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "codex_cadence",
                "--stop-condition",
                "custom_stop",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output["executor_task"]["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "custom_stop"],
            )

    def test_loop_tick_policy_file_denies_disallowed_executor_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "denied_paths": ["tests"],
                        "max_executor_time_minutes": 15,
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--allowed-path",
                "tests",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "policy_denied")
            self.assertEqual(output["reason"], "executor allowed path tests is denied by policy")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["executor_contract_required"])
            self.assertIsNone(output["executor_task"])
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "loop_tick_decision")
            self.assertEqual(record["action"], "policy_denied")
            self.assertEqual(record["reason"], "executor allowed path tests is denied by policy")

    def test_loop_tick_policy_file_denies_executor_time_over_cap(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "max_executor_time_minutes": 15,
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--executor-time-limit-minutes",
                "20",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "policy_denied")
            self.assertEqual(output["reason"], "executor time limit exceeds policy max_executor_time_minutes")
            self.assertIsNone(output["executor_task"])

    def test_loop_tick_rejects_malformed_cli_allowed_path_without_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "../outside",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("executor allowed path ../outside must be repo-relative", result.stderr)

    def test_loop_tick_requires_approval_for_low_confidence_repo(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "merge_readiness",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("dirty_worktree", output["snapshot"]["repo_confidence_drivers"])
            self.assertEqual(output["elected_next"][0]["source"], "git_status")
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_requires_approval_for_low_confidence_without_candidates(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--discovery-mode",
                "off",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("dirty_worktree", output["snapshot"]["repo_confidence_drivers"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_requires_approval_for_red_ci_signal(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--active-pr",
                "49",
                "--ci-status",
                "red",
                "--discovery-mode",
                "off",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["snapshot"]["ci"], "red")
            self.assertEqual(output["snapshot"]["active_pr"], 49)
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("red_ci", output["snapshot"]["repo_confidence_drivers"])
            self.assertEqual(output["elected_next"], [])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_blocks_when_cadence_state_disallows_work(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            result, _brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "blocked")
            self.assertEqual(output["reason"], "cadence state is TIMEOUT")
            self.assertEqual(output["cadence"]["state"], "TIMEOUT")
            self.assertFalse(output["cadence"]["can_start_work"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_controlled_executor_fixture_runs_success_and_validates_result(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertEqual(output["packet"], "controlled_executor_fixture_run")
            self.assertEqual(output["schema_version"], "controlled-executor-fixture-run.v1")
            self.assertIn("controlled_fixture_only", output["limitations"])
            self.assertNotIn("branch_policy_not_implemented", output["limitations"])
            self.assertEqual(output["result_status"], "succeeded")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            self.assertEqual(output["task_file"], str(task_path))
            self.assertEqual(output["result_file"], str(evidence_path))
            result_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result_evidence["task_id"], task_packet["task"]["id"])
            self.assertEqual(result_evidence["resulting_head"], task_packet["repo"]["head"])
            self.assertEqual(output["run_record"]["closeout_status"], "pending")
            self.assertTrue(Path(output["run_record"]["path"]).exists())
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 3)
            invocation_record = json.loads(audit_lines[0])
            validation_record = json.loads(audit_lines[1])
            run_record = json.loads(audit_lines[2])
            self.assertEqual(invocation_record["event"], "executor_fixture_invocation")
            self.assertEqual(invocation_record["action"], "start_controlled_executor_fixture")
            self.assertEqual(invocation_record["task_id"], "candidate-1")
            self.assertEqual(validation_record["event"], "executor_result_validation")
            self.assertTrue(validation_record["valid"])
            self.assertEqual(run_record["event"], "execution_run_record")
            self.assertEqual(run_record["action"], "record_execution_run")
            self.assertEqual(run_record["closeout_status"], "pending")
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["records_valid"], 3)
            self.assertEqual(replay_output["events_by_type"]["executor_fixture_invocation"], 1)
            self.assertEqual(replay_output["events_by_type"]["executor_result_validation"], 1)
            self.assertEqual(replay_output["events_by_type"]["execution_run_record"], 1)

    def test_controlled_executor_fixture_accepts_failed_evidence_from_nonzero_command(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="failed", exit_code=7, summary="Fixture command failed."),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["command_exit_code"], 7)
            self.assertEqual(output["result_status"], "failed")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")

    def test_controlled_executor_fixture_formats_unquoted_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime root"
            repo = Path(tmp) / "repo with spaces"
            root.mkdir()
            repo.mkdir()
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(root, repo)

            result, output = run_cli(
                root,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                unquoted_controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"], output["reason"])
            self.assertTrue(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_repo_local_runtime_root_location(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as tasks_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tasks_tmp, repo)

            result, output = run_cli_from(
                repo,
                runtime_root,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_controlled_executor_fixture_rejects_missing_repo_path_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, task_packet = write_controlled_fixture_task(tmp, repo)
            missing_repo = Path(tmp) / "missing-repo"
            task_packet["repo"]["path"] = str(missing_repo)
            task_packet["snapshot"]["cwd"] = str(missing_repo)
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor task repo.path must exist and be a directory")
            self.assertEqual(output["recommended_next_action"], "fix_executor_task_packet")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_malformed_result_evidence_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            fixture = Path(tmp) / "malformed-fixture.py"
            fixture.write_text(
                "from pathlib import Path\n"
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--result-file', required=True)\n"
                "parser.add_argument('--task-file')\n"
                "parser.add_argument('--status')\n"
                "parser.add_argument('--summary')\n"
                "parser.add_argument('--command')\n"
                "parser.add_argument('--validation-name')\n"
                "parser.add_argument('--validation-status')\n"
                "parser.add_argument('--exit-code')\n"
                "parser.add_argument('--changed-file')\n"
                "args = parser.parse_args()\n"
                "Path(args.result_file).write_text('{not-json', encoding='utf-8')\n",
                encoding="utf-8",
            )
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            with mock.patch("codex_cadence.executor_runner._controlled_fixture_script", return_value=fixture):
                output = run_controlled_executor_fixture(
                    root=Path(tmp),
                    task_file=task_path,
                    command_template=controlled_fixture_command(status="succeeded", executable=sys.executable).replace(
                        str(controlled_fixture_script()),
                        str(fixture),
                    ),
                    timeout_seconds=10,
                )

            self.assertFalse(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertEqual(output["reason"], "executor result evidence file was not written")
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")
            self.assertTrue(evidence_path.exists())

    def test_controlled_executor_fixture_example_exit_code_matches_evidence_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result = subprocess.run(
                [
                    sys.executable,
                    str(controlled_fixture_script()),
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(evidence_path),
                    "--status",
                    "failed",
                    "--summary",
                    "Fixture failed validation.",
                    "--command",
                    "python -m unittest tests.test_cadence",
                    "--validation-status",
                    "failed",
                    "--exit-code",
                    "0",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["commands_run"][0]["exit_code"], 1)

    def test_controlled_executor_fixture_timeout_writes_stopped_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded", sleep_seconds=5),
                "--timeout-seconds",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["timed_out"])
            self.assertEqual(output["result_status"], "stopped")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            result_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result_evidence["status"], "stopped")
            self.assertIn("timeout", result_evidence["blockers"])

    def test_controlled_executor_fixture_rejects_untrusted_python_executable_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            fake_python = Path(tmp) / "bin" / "python.exe"

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(executable=fake_python),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command template must use the current Python interpreter")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_allows_stopped_evidence_over_observed_runtime_limit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            with mock.patch("codex_cadence.executor_runner.time.monotonic", side_effect=[0.0, 120.0]):
                output = run_controlled_executor_fixture(
                    root=Path(tmp),
                    task_file=task_path,
                    command_template=controlled_fixture_command(status="stopped", summary="Fixture stopped."),
                    timeout_seconds=10,
                )

            self.assertTrue(output["valid"], output["reason"])
            self.assertEqual(output["result_status"], "stopped")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")

    def test_controlled_executor_fixture_rejects_success_after_active_brake_stop(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "stop_active_loop")
            self.assertEqual(output["active_stop"]["brake_status"], "PARK")

    def test_controlled_executor_fixture_blocks_disallowed_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(command="git push origin main"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command violates disabled push permission")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_policy")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_enforces_task_command_policy_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(
                tmp,
                repo,
                command_policy={"denied_commands": ["python -m pip install"]},
            )

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(command="python -m pip install ."),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command is denied by command_policy")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_policy")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_malformed_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                "python -c {missing_placeholder}",
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertIn("invalid executor command template", output["reason"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_non_fixture_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                f'"{sys.executable}" -m unittest tests.test_cadence',
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command template must invoke the controlled fixture script")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_evidence_path_outside_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(
                tmp,
                repo,
                evidence_name=str(Path(outside) / "executor-result.json"),
            )

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor result evidence path must stay inside the runtime root")
            self.assertEqual(output["recommended_next_action"], "fix_executor_task_packet")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_existing_result_file_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            evidence_path.write_text("preserve me\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor result evidence file already exists")
            self.assertEqual(output["recommended_next_action"], "remove_stale_executor_evidence")
            self.assertEqual(evidence_path.read_text(encoding="utf-8"), "preserve me\n")

    def test_controlled_executor_fixture_rejects_success_evidence_from_nonzero_command(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            fixture = Path(tmp) / "nonzero-success-fixture.py"
            fixture.write_text(
                "from datetime import datetime, timezone\n"
                "from pathlib import Path\n"
                "import argparse, json\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--task-file', required=True)\n"
                "parser.add_argument('--result-file', required=True)\n"
                "parser.add_argument('--status')\n"
                "parser.add_argument('--summary')\n"
                "parser.add_argument('--command', required=True)\n"
                "parser.add_argument('--validation-name')\n"
                "parser.add_argument('--validation-status')\n"
                "parser.add_argument('--exit-code')\n"
                "parser.add_argument('--changed-file')\n"
                "args = parser.parse_args()\n"
                "task = json.loads(Path(args.task_file).read_text(encoding='utf-8'))\n"
                "now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')\n"
                "evidence = {\n"
                "    'schema_version': 'generic-executor-result.v1',\n"
                "    'packet': 'executor_result',\n"
                "    'task_id': task['task']['id'],\n"
                "    'executor_id': 'fake-nonzero-success',\n"
                "    'started_at': now,\n"
                "    'ended_at': now,\n"
                "    'status': 'succeeded',\n"
                "    'files_changed': ['codex_cadence/executor_runner.py'],\n"
                "    'commands_run': [{'command': args.command, 'exit_code': 0}],\n"
                "    'validation_results': [{'name': 'cadence-tests', 'status': 'passed', 'command': args.command}],\n"
                "    'summary': 'Wrote success evidence but exited nonzero.',\n"
                "    'confidence': 'high',\n"
                "    'blockers': [],\n"
                "    'dirty_worktree': False,\n"
                "    'resulting_head': task['repo']['head'],\n"
                "}\n"
                "Path(args.result_file).write_text(json.dumps(evidence), encoding='utf-8')\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            with mock.patch("codex_cadence.executor_runner._controlled_fixture_script", return_value=fixture):
                output = run_controlled_executor_fixture(
                    root=Path(tmp),
                    task_file=task_path,
                    command_template=controlled_fixture_command(status="succeeded").replace(
                        str(controlled_fixture_script()),
                        str(fixture),
                    ),
                    timeout_seconds=10,
                )

            self.assertFalse(output["valid"])
            self.assertEqual(output["command_exit_code"], 7)
            self.assertEqual(output["reason"], "executor fixture command exit code must be 0 when result status is succeeded")
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")

    def test_validate_executor_result_command_reports_valid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed the bounded task.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["reason"], "ok")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            self.assertFalse(output["executor_started"])
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "executor_result_validation")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "record_executor_result")
            self.assertEqual(record["task_id"], "candidate-1")
            self.assertTrue(record["valid"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_rejects_success_after_active_brake_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed after an operator stop.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "cadence brake is PARK; executor result must report stopped before completion can be recorded",
            )
            self.assertEqual(output["recommended_next_action"], "stop_active_loop")
            self.assertEqual(output["active_stop"]["brake_status"], "PARK")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "stop_active_loop")
            self.assertFalse(record["valid"])

    def test_validate_executor_result_requires_root_for_completion_with_brake_stop_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed without proving the current brake state.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")
            env = {
                **os.environ,
                "HOME": tmp,
                "USERPROFILE": tmp,
            }
            env.pop("CODEX_CADENCE_ROOT", None)
            env.pop("CODEX_TRANSMISSION_ROOT", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "validate-executor-result",
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(evidence_path),
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            output = json.loads(result.stdout)

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["reason"], "runtime root is required to validate brake_not_drive stop condition")
            self.assertEqual(output["recommended_next_action"], "provide_runtime_root")

    def test_validate_executor_result_rejects_unexpected_result_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected_path = root / "expected-result.json"
            actual_path = root / "actual-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=expected_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed the bounded task.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            actual_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(actual_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["reason"], "executor result file does not match task expected_output.evidence_path")
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")

    def test_validate_executor_result_rejects_unignored_repo_local_audit_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(repo).resolve())),
                repo_path=repo,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed the bounded task.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(Path(repo).resolve()))["head"],
            }
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_validate_executor_result_command_exits_nonzero_for_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [],
                "validation_results": [],
                "summary": "Fake executor claimed success without checks.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "executor_result_validation")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "fix_executor_evidence")
            self.assertFalse(record["valid"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = []
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task packet must be a JSON object",
            )
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "fix_executor_evidence")
            self.assertFalse(record["valid"])
            self.assertNotIn("task_id", record)
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_repo_path_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(tmp).resolve())),
                repo_path=tmp,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_packet["repo"]["path"] = ["bad"]
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task repo.path is required",
            )
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_repo_path_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            malformed_path = f"{Path(tmp).anchor}bad\0path"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(tmp).resolve())),
                repo_path=tmp,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_packet["snapshot"]["cwd"] = malformed_path
            task_packet["repo"]["path"] = malformed_path
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    }
                ],
                "validation_results": [
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    }
                ],
                "summary": "Fake executor completed the bounded task.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": task_packet["repo"]["head"],
            }
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task snapshot cwd and repo.path must be absolute local paths",
            )
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_rejects_repo_local_root_with_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = {"schema_version": "generic-executor-task.v1"}
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli_from(
                repo,
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_validate_executor_result_rejects_repo_local_root_from_outside_repo_with_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = {"schema_version": "generic-executor-task.v1"}
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli_from(
                outside,
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_epoch_cli_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(epoch["status"], "ACTIVE")

            result, completed = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "STOP", "--summary", "done")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["decision"], "STOP")

    def test_start_epoch_blocks_when_brake_is_parked(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("start-epoch requires brake DRIVE", result.stderr)
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_fail_epoch_moves_active_epoch_to_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, failed = run_cli(tmp, "fail-epoch", epoch["id"], "--reason", "blocked")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["failure_reason"], "blocked")
            self.assertFalse((Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json").exists())
            self.assertTrue((Path(tmp) / "epochs" / "failed" / f"{epoch['id']}.json").exists())

    def test_complete_epoch_rejects_continue_without_persisted_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE requires a persisted CONTINUE self-check", result.stderr)

    def test_complete_epoch_allows_continue_after_persisted_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")

            result, completed = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["decision"], "CONTINUE")

    def test_self_check_asks_approval_after_completed_epoch_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            snapshot_before_1 = Path(tmp) / "snapshot-before-1.json"
            snapshot_before_1.write_text(json.dumps(valid_snapshot(id="snapshot-before-1")), encoding="utf-8")
            snapshot_after_1 = Path(tmp) / "snapshot-after-1.json"
            snapshot_after_1.write_text(json.dumps(valid_snapshot(id="snapshot-after-1")), encoding="utf-8")
            result, first = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_1),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                first["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_1),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")
            self.assertEqual(check["completed_continue_count"], 0)
            result, completed = run_cli(tmp, "complete-epoch", first["id"], "--decision", "CONTINUE")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["decision"], "CONTINUE")

            snapshot_before_2 = Path(tmp) / "snapshot-before-2.json"
            snapshot_before_2.write_text(json.dumps(valid_snapshot(id="snapshot-before-2")), encoding="utf-8")
            snapshot_after_2 = Path(tmp) / "snapshot-after-2.json"
            snapshot_after_2.write_text(json.dumps(valid_snapshot(id="snapshot-after-2")), encoding="utf-8")
            result, second = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_2),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                second["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_2),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["completed_continue_count"], 1)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "max_epochs_without_user_approval reached")

    def test_self_check_requires_green_ci_for_default_continuation_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", ci="unknown")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "green CI or explicit handoff required")

    def test_complete_epoch_rejects_continue_when_epoch_exceeds_time_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            active_path = Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            from codex_cadence.epochs import checksum_json as checksum_epoch_json

            snapshot_after = valid_snapshot(id="snapshot-after")
            persisted_snapshot_after = persisted_snapshot_path(Path(tmp), snapshot_after["id"])
            persisted_snapshot_after.parent.mkdir(parents=True, exist_ok=True)
            persisted_snapshot_after.write_text(json.dumps(snapshot_after), encoding="utf-8")
            active["started_at"] = "2026-05-22T00:00:00Z"
            active["last_self_check"] = {
                "epoch_id": epoch["id"],
                "decision": "CONTINUE",
                "epoch_grounded": True,
                "current_snapshot_grounded": True,
                "brake_status": "DRIVE",
                "elected_next": [{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                "epoch_policy_checksum": "sha256:stale",
                "snapshot_before_id": active["snapshot_before"]["id"],
                "snapshot_before_checksum": checksum_epoch_json(active["snapshot_before"]),
                "snapshot_after_id": snapshot_after["id"],
                "snapshot_after_checksum": checksum_epoch_json(snapshot_after),
                "completed_continue_count": 0,
                "current_snapshot_ci": "green",
            }
            active["last_self_check"]["epoch_policy_checksum"] = checksum_epoch_json(active["policy"])
            active_path.write_text(json.dumps(active), encoding="utf-8")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE exceeds max_minutes_per_epoch", result.stderr)

    def test_complete_epoch_rejects_replayed_continue_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            active_path = Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            from codex_cadence.epochs import checksum_json as checksum_epoch_json

            active["last_self_check"] = {
                "epoch_id": "other-epoch",
                "decision": "CONTINUE",
                "epoch_grounded": True,
                "current_snapshot_grounded": True,
                "brake_status": "DRIVE",
                "elected_next": [{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                "epoch_policy_checksum": checksum_epoch_json(active["policy"]),
                "snapshot_before_id": active["snapshot_before"]["id"],
                "snapshot_before_checksum": checksum_epoch_json(active["snapshot_before"]),
                "completed_continue_count": 0,
                "current_snapshot_ci": "green",
            }
            active_path.write_text(json.dumps(active), encoding="utf-8")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE self-check epoch does not match active epoch", result.stderr)

    def test_complete_epoch_rechecks_brake_before_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE requires brake to remain DRIVE", result.stderr)

    def test_complete_epoch_continue_uses_runtime_lock_for_brake_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")

            with exclusive_lock(lock_path(Path(tmp), "runtime")):
                result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("lock already held: runtime.lock", result.stderr)
            self.assertTrue((Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json").exists())

    def test_start_epoch_rejects_object_tasks_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(json.dumps({"id": "task-1"}), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_start_epoch_requires_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "start-epoch", "--repo", "local/test", "--branch", "main")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--snapshot-before-file", result.stderr)

    def test_start_epoch_rejects_list_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps([{"id": "snapshot-1"}]), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_start_epoch_rejects_invalid_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps({"id": "snapshot-1", "repo_confidence": "high"}), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("invalid snapshot_before", result.stderr)

    def test_start_epoch_rejects_snapshot_repo_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(repo="other/repo")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot repo does not match", result.stderr)

    def test_start_epoch_rejects_snapshot_branch_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(branch="feature")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot branch does not match", result.stderr)

    def test_start_epoch_rejects_existing_active_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_snapshot_path = Path(tmp) / "snapshot-1.json"
            first_snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-1")), encoding="utf-8")
            second_snapshot_path = Path(tmp) / "snapshot-2.json"
            second_snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-2")), encoding="utf-8")
            result, _ = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(first_snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(second_snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch already exists", result.stderr)

    def test_start_epoch_rejects_tasks_over_policy_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks = [{"id": f"task-{index}", "task_type": "execution"} for index in range(4)]
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("exceeds max_tasks_per_epoch", result.stderr)

    def test_start_epoch_rejects_too_many_discovery_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks = [
                {"id": "task-1", "task_type": "discovery"},
                {"id": "task-2", "task_type": "discovery"},
            ]
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("exceeds max_discovery_tasks_per_epoch", result.stderr)

    def test_start_epoch_requires_repo_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(tmp, "start-epoch", "--branch", "main", "--snapshot-before-file", str(snapshot_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--repo", result.stderr)

            result, output = run_cli(tmp, "start-epoch", "--repo", "local/test", "--snapshot-before-file", str(snapshot_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--branch", result.stderr)

    def test_start_epoch_rejects_missing_task_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(json.dumps([{"id": "task-1"}]), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("task_type must be execution or discovery", result.stderr)

    def test_self_check_blocks_high_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "self-check", "--uncertainty", "high")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "HANDOFF")

    def test_self_check_uses_candidate_uncertainty_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S", "uncertainty": "high"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")

    def test_self_check_requires_epoch_snapshot_to_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertFalse(output["epoch_grounded"])
            self.assertEqual(output["reason"], "epoch snapshot required for continuation")

    def test_self_check_requires_current_snapshot_to_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "self-check", "--epoch-id", epoch["id"], "--candidates-file", str(candidates_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["epoch_grounded"])
            self.assertFalse(output["current_snapshot_grounded"])
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "current repo snapshot required for continuation")

    def test_self_check_rejects_stale_snapshot_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(valid_snapshot(id="snapshot-after", captured_at="2000-01-01T00:00:00Z")),
                encoding="utf-8",
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot_after must be captured after epoch start", result.stderr)

    def test_self_check_uses_current_snapshot_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before", repo_confidence="high")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", repo_confidence="low")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["epoch_grounded"])
            self.assertTrue(output["current_snapshot_grounded"])
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")

    def test_self_check_snapshot_confidence_overrides_cli_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before", repo_confidence="high")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", repo_confidence="low")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--repo-confidence",
                "high",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")

    def test_self_check_recursive_discovery_flag_does_not_override_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "discovery", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--allow-recursive-discovery",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "recursive discovery requires approval")

    def test_self_check_caps_election_to_stored_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-tight-policy",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 1},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 9},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-tight-policy",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "2",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_accepts_discover_candidates_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-payload", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-payload",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_honors_discover_candidates_low_repo_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-low-confidence", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "low", "uncertainty": "low"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-low-confidence",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "repo confidence is low")

    def test_self_check_honors_discover_candidates_high_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-high-uncertainty", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "high"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-high-uncertainty",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "uncertainty is high")

    def test_self_check_honors_discover_candidates_high_candidate_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-high-growth", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "low", "candidate_growth": "high"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-high-growth",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.get("candidate_growth"), "high")
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "uncertainty is high")

    def test_self_check_honors_discover_candidates_medium_candidate_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-discovery-medium-growth",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "low", "candidate_growth": "medium"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                            {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                            {"id": "task-3", "task_type": "execution", "bucket": "S", "score": 10},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-medium-growth",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.get("candidate_growth"), "medium")
            self.assertEqual(output["uncertainty"], "medium")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_caps_discovery_election_to_stored_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-discovery-limit",
                valid_snapshot(),
                policy={"allow_recursive_discovery": True, "max_tasks_per_epoch": 3, "max_discovery_tasks_per_epoch": 1},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "discovery-1", "task_type": "discovery", "bucket": "S", "score": 100},
                        {"id": "discovery-2", "task_type": "discovery", "bucket": "S", "score": 90},
                        {"id": "execution-1", "task_type": "execution", "bucket": "S", "score": 10},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-limit",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["discovery-1", "execution-1"])

    def test_self_check_medium_uncertainty_shrinks_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-medium-uncertainty",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                        {"id": "task-3", "task_type": "execution", "bucket": "S", "score": 10},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-medium-uncertainty",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
                "--uncertainty",
                "medium",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_watch_epoch_health_shrinks_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-watch-health",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-watch-health",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
                "--epoch-health",
                "watch",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_rejects_invalid_active_epoch_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-invalid-snapshot", {"repo_confidence": "high"})
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-invalid-snapshot",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot_before is invalid", result.stderr)

    def test_self_check_rejects_completed_record_in_active_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            path = write_active_epoch(tmp, "epoch-completed-active", valid_snapshot())
            data = json.loads(path.read_text(encoding="utf-8"))
            data["status"] = "COMPLETED"
            path.write_text(json.dumps(data), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-completed-active",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch status must be ACTIVE", result.stderr)

    def test_self_check_rejects_mismatched_active_epoch_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            path = write_active_epoch(tmp, "epoch-path-id", valid_snapshot())
            data = json.loads(path.read_text(encoding="utf-8"))
            data["id"] = "epoch-record-id"
            path.write_text(json.dumps(data), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-path-id",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch id does not match path", result.stderr)

    def test_self_check_rejects_multiple_active_epochs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch_raw(tmp, "epoch-1")
            write_active_epoch_raw(tmp, "epoch-2")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-1",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("expected exactly one active epoch", result.stderr)

    def test_self_check_rejects_malformed_recursive_discovery_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-bad-policy",
                valid_snapshot(),
                policy={"allow_recursive_discovery": "false"},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "discovery", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-bad-policy",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("allow_recursive_discovery must be a boolean", result.stderr)

    def test_self_check_rejects_object_candidates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(json.dumps({"id": "task-1"}), encoding="utf-8")

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_self_check_rejects_malformed_candidate_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(json.dumps(["not-a-dict"]), encoding="utf-8")

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("candidate 0 must be a JSON object", result.stderr)

    def test_closeout_executor_result_completes_epoch_and_embeds_dry_run_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            git(repo, "switch", "-c", "feature/epoch-closeout")
            (Path(repo) / "README.md").write_text("hello\ncloseout\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "implement epoch closeout fixture")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-success",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-success",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "executor-epoch-closeout.v1")
            self.assertTrue(output["valid"])
            self.assertEqual(output["closeout_status"], "completed")
            self.assertEqual(output["epoch_status"], "COMPLETED")
            self.assertEqual(output["executor_result_status"], "succeeded")
            self.assertEqual(output["task_checksum"], checksum_json(task_packet))
            self.assertEqual(output["next_decision"]["decision"], "generate_git_pr_plan")
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertEqual(
                output["side_effects"],
                ["epoch_completed", "execution_run_record_updated", "execution_run_audit_appended", "audit_record_appended"],
            )
            self.assertTrue(output["git_pr_plan"]["dry_run"])
            self.assertTrue(output["git_pr_plan"]["ready_to_review"])
            self.assertEqual(output["git_pr_plan"]["side_effects"], [])
            completed_path = Path(tmp) / "epochs" / "completed" / "epoch-closeout-success.json"
            self.assertTrue(completed_path.exists())
            self.assertFalse((Path(tmp) / "epochs" / "active" / "epoch-closeout-success.json").exists())
            completed_epoch = json.loads(completed_path.read_text(encoding="utf-8"))
            self.assertEqual(completed_epoch["decision"], "STOP")
            self.assertEqual(completed_epoch["completed_tasks"], ["candidate-1"])
            self.assertEqual(completed_epoch["executor_closeout"]["result_file_checksum"], checksum_json(result_evidence))
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 2)
            run_record_audit = json.loads(audit_lines[0])
            self.assertEqual(run_record_audit["event"], "execution_run_record")
            self.assertEqual(run_record_audit["action"], "update_execution_run_closeout")
            audit_record = json.loads(audit_lines[1])
            self.assertEqual(audit_record["event"], "executor_epoch_closeout")
            self.assertEqual(audit_record["action"], "generate_git_pr_plan")
            self.assertEqual(audit_record["epoch_id"], "epoch-closeout-success")
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(audit_record["payload_checksum"], checksum_json(payload_without_audit))

    def test_controlled_executor_fixture_run_record_is_accepted_by_closeout(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(
                    cwd=str(Path(repo).resolve()),
                    branch=current_branch(repo),
                    head=current_head(repo),
                ),
                repo_path=repo,
                allowed_paths=["codex_cadence", "tests"],
                required_checks=["python -m unittest tests.test_cadence"],
                max_minutes=1,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            write_active_epoch(
                tmp,
                "epoch-closeout-run-record",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )

            fixture_result, fixture_output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(fixture_result.returncode, 0, fixture_result.stderr)
            self.assertTrue(fixture_output["valid"])
            run_record_ref = fixture_output["run_record"]
            run_record_path = Path(run_record_ref["path"])
            self.assertTrue(run_record_path.exists())
            run_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            result_evidence = json.loads((Path(tmp) / "executor-result.json").read_text(encoding="utf-8"))
            self.assertEqual(run_record["schema_version"], "execution-run.v1")
            self.assertEqual(run_record["packet"], "execution_run")
            self.assertEqual(run_record["closeout_status"], "pending")
            self.assertEqual(run_record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(run_record["result_evidence_checksum"], checksum_json(result_evidence))
            self.assertEqual(run_record_ref["checksum"], checksum_json(run_record))

            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )
            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-record",
                "--task-file",
                str(task_path),
                "--result-file",
                str(Path(tmp) / "executor-result.json"),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
            self.assertTrue(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "completed")
            self.assertIn("execution_run_record_updated", closeout_output["side_effects"])
            self.assertIn("execution_run_audit_appended", closeout_output["side_effects"])
            self.assertIn("audit_record_appended", closeout_output["side_effects"])
            updated_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_record["closeout_status"], "completed")
            self.assertEqual(updated_record["epoch_id"], "epoch-closeout-run-record")
            self.assertEqual(updated_record["epoch_closeout_checksum"], closeout_output["run_record"]["epoch_closeout_checksum"])
            self.assertEqual(closeout_output["run_record"]["after_checksum"], checksum_json(updated_record))
            self.assertEqual(closeout_output["run_record"]["audit_record"]["event"], "execution_run_record")
            records = audit_records(tmp)
            self.assertEqual(
                [record["event"] for record in records],
                [
                    "executor_fixture_invocation",
                    "executor_result_validation",
                    "execution_run_record",
                    "execution_run_record",
                    "executor_epoch_closeout",
                ],
            )
            self.assertEqual(records[3]["closeout_status"], "completed")
            self.assertEqual(records[3]["action"], "update_execution_run_closeout")
            self.assertEqual(records[3]["run_record_checksum"], checksum_json(updated_record))
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["records_valid"], 5)
            self.assertEqual(replay_output["events_by_type"]["execution_run_record"], 2)

            rerun_result, rerun_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-record",
                "--task-file",
                str(task_path),
                "--result-file",
                str(Path(tmp) / "executor-result.json"),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(rerun_result.returncode, 1)
            self.assertFalse(rerun_output["valid"])
            self.assertEqual(rerun_output["closeout_status"], "already_closed")
            self.assertNotIn("execution_run_record_updated", rerun_output["side_effects"])
            self.assertEqual(checksum_json(json.loads(run_record_path.read_text(encoding="utf-8"))), checksum_json(updated_record))
            self.assertEqual(len(audit_records(tmp)), 5)

    def test_run_record_closeout_blocks_structurally_when_update_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            (
                task_path,
                result_path,
                snapshot_after_path,
                task_packet,
                result_evidence,
                _snapshot_after,
            ) = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-record-audit-failure",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )
            records_before_closeout = audit_records(tmp)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "epoch_id": "epoch-closeout-run-record-audit-failure",
                    "task_file": str(task_path),
                    "result_file": str(result_path),
                    "snapshot_after_file": str(snapshot_after_path),
                    "run_record_file": str(run_record_path),
                    "real_invocation_file": None,
                    "allow_repo_local_root": False,
                    "cwd": repo,
                    "required_body_section": [],
                    "emit_git_pr_plan": False,
                    "pr_template_file": None,
                    "policy_file": None,
                    "base_branch": "main",
                    "branch_prefix": "codex/",
                },
            )()
            original_append = cadence_cli.append_audit_record

            def fail_run_record_update(root, record):
                if (
                    record.get("event") == "execution_run_record"
                    and record.get("action") == "update_execution_run_closeout"
                ):
                    raise OSError("disk full")
                return original_append(root, record)

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=fail_run_record_update):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.closeout_executor_result_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            output = emitted[0]
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "completed")
            self.assertEqual(output["epoch_status"], "COMPLETED")
            self.assertIn("run_record_audit_append_failed", {blocker["code"] for blocker in output["blockers"]})
            self.assertIn("epoch_completed", output["side_effects"])
            self.assertIn("execution_run_audit_append_failed", output["side_effects"])
            self.assertIn("execution_run_record_update_rolled_back", output["side_effects"])
            self.assertNotIn("execution_run_record_updated", output["side_effects"])
            self.assertNotIn("execution_run_audit_appended", output["side_effects"])
            self.assertNotIn("audit_record_appended", output["side_effects"])
            self.assertEqual(json.loads(run_record_path.read_text(encoding="utf-8")), run_record)
            self.assertTrue(output["run_record"]["rollback_record_restored"])
            self.assertEqual(output["run_record"]["after_checksum"], checksum_json(run_record))
            self.assertEqual(output["next_decision"]["recommended_next_action"], "recover_closeout_audit")
            self.assertEqual(audit_records(tmp), records_before_closeout)
            self.assertTrue(
                (Path(tmp) / "epochs" / "completed" / "epoch-closeout-run-record-audit-failure.json").exists()
            )

    def test_closeout_executor_result_requires_exactly_one_evidence_artifact(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-requires-evidence",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    tmp,
                    "closeout-executor-result",
                    "--epoch-id",
                    "epoch-closeout-requires-evidence",
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(result_path),
                    "--snapshot-after-file",
                    str(snapshot_after_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("one of the arguments --run-record-file --real-invocation-file is required", result.stderr)

            both_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    tmp,
                    "closeout-executor-result",
                    "--epoch-id",
                    "epoch-closeout-requires-evidence",
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(result_path),
                    "--snapshot-after-file",
                    str(snapshot_after_path),
                    "--run-record-file",
                    str(Path(tmp) / "execution-runs" / "run.json"),
                    "--real-invocation-file",
                    str(Path(tmp) / "real-executor-invocations" / "invocation.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(both_result.returncode, 2)
            self.assertEqual(both_result.stdout, "")
            self.assertIn("not allowed with argument", both_result.stderr)

    def test_real_executor_invocation_record_is_accepted_by_closeout_and_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                tmp,
                repo,
                touch_repo=True,
                include_materialized_change_evidence=True,
            )
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            task_alias_dir = Path(tmp) / "task-path-alias"
            task_alias_dir.mkdir()
            closeout_task_path = task_alias_dir / ".." / task_path.name

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(
                tmp,
                plan_path,
                side_effect_mode="materialized_changes",
            )

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            self.assertTrue(invocation_output["valid"])
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            result_evidence = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(invocation_output["result_evidence_checksum"], checksum_json(result_evidence))
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after",
                        captured_at="2999-05-22T00:10:00Z",
                        dirty_worktree=True,
                        repo_confidence="low",
                        repo_confidence_drivers=["dirty_worktree"],
                    )
                ),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(closeout_task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 0, closeout_result.stderr)
            self.assertTrue(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "completed")
            self.assertTrue(closeout_output["executor_started"])
            self.assertEqual(closeout_output["validation"]["invocation_id"], invocation_output["invocation_id"])
            self.assertEqual(closeout_output["real_invocation"]["path"], str(invocation_path))
            self.assertEqual(closeout_output["real_invocation"]["invocation_id"], invocation_output["invocation_id"])
            self.assertEqual(closeout_output["real_invocation"]["before_checksum"], checksum_json(invocation_output))
            self.assertIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertIn("real_executor_invocation_audit_appended", closeout_output["side_effects"])
            self.assertIn("audit_record_appended", closeout_output["side_effects"])
            self.assertTrue(closeout_output["git_pr_plan"]["dry_run"])
            self.assertFalse(closeout_output["pr_action_started"])
            updated_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_invocation["closeout_status"], "completed")
            self.assertEqual(updated_invocation["epoch_id"], "epoch-1")
            self.assertEqual(updated_invocation["epoch_status"], "COMPLETED")
            self.assertEqual(
                updated_invocation["epoch_closeout_checksum"],
                closeout_output["real_invocation"]["epoch_closeout_checksum"],
            )
            self.assertEqual(closeout_output["real_invocation"]["after_checksum"], checksum_json(updated_invocation))
            completed_epoch = json.loads(
                (Path(tmp) / "epochs" / "completed" / "epoch-1.json").read_text(
                    encoding="utf-8",
                )
            )
            self.assertEqual(completed_epoch["executor_closeout"]["result_file_checksum"], checksum_json(result_evidence))
            records = audit_records(tmp)
            invocation_update_records = [
                record
                for record in records
                if record.get("event") == "real_executor_invocation_record"
                and record.get("action") == "update_real_executor_invocation_closeout"
            ]
            self.assertEqual(len(invocation_update_records), 1)
            invocation_update = invocation_update_records[0]
            self.assertEqual(invocation_update["invocation_id"], invocation_output["invocation_id"])
            self.assertEqual(
                invocation_update["epoch_closeout_checksum"],
                closeout_output["real_invocation"]["epoch_closeout_checksum"],
            )
            self.assertEqual(invocation_update["result_evidence_checksum"], checksum_json(result_evidence))
            self.assertEqual(invocation_update["invocation_record_checksum"], checksum_json(updated_invocation))
            closeout_records = [record for record in records if record.get("event") == "executor_epoch_closeout"]
            self.assertEqual(len(closeout_records), 1)
            payload_without_audit = dict(closeout_output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(closeout_records[0]["payload_checksum"], checksum_json(payload_without_audit))
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["events_by_type"]["real_executor_invocation_record"], 2)

    def test_real_executor_invocation_closeout_blocks_missing_invocation_audit_anchor(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            audit_path = Path(tmp) / "audit" / "events.jsonl"
            records = audit_records(tmp)
            retained_records = [record for record in records if record.get("event") != "real_executor_invocation_record"]
            audit_path.write_text(
                "".join(json.dumps(record, sort_keys=True) + "\n" for record in retained_records),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("audit_chain_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_ownership_anchor_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            ownership_path = Path(inputs["readiness_packet"]["ownership"]["path"])
            ownership_record = json.loads(ownership_path.read_text(encoding="utf-8"))
            ownership_record["claimer"] = "other-agent"
            ownership_path.write_text(json.dumps(ownership_record), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("ownership_closeout_blocked", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_dirty_file_content_tampering(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                tmp,
                repo,
                touch_repo=True,
                include_materialized_change_evidence=True,
            )
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(
                tmp,
                plan_path,
                side_effect_mode="materialized_changes",
            )

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            (Path(repo) / "README.md").write_text("tampered after invocation\n", encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after",
                        captured_at="2999-05-22T00:10:00Z",
                        dirty_worktree=True,
                        repo_confidence="low",
                        repo_confidence_drivers=["dirty_worktree"],
                    )
                ),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("materialized_change_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_result_file_tampering(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            tampered_result = json.loads(result_path.read_text(encoding="utf-8"))
            tampered_result["summary"] = "Tampered after invocation."
            result_path.write_text(json.dumps(tampered_result), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("invocation_checksum_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_mutable_record_checksum_tampering(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            tampered_result = json.loads(result_path.read_text(encoding="utf-8"))
            tampered_result["summary"] = "Tampered after invocation with matching mutable checksum."
            result_path.write_text(json.dumps(tampered_result), encoding="utf-8")
            tampered_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            tampered_invocation["result_evidence_checksum"] = checksum_json(tampered_result)
            invocation_path.write_text(json.dumps(tampered_invocation), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("invocation_checksum_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_false_snapshot_after_dirty_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                tmp,
                repo,
                touch_repo=True,
                include_materialized_change_evidence=True,
            )
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(
                tmp,
                plan_path,
                side_effect_mode="materialized_changes",
            )

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("materialized_change_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_unreported_dirty_files(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(
                tmp,
                repo,
                touch_repo=True,
                include_materialized_change_evidence=True,
            )
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(
                tmp,
                plan_path,
                side_effect_mode="materialized_changes",
            )

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            before_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            result_path = Path(invocation_output["result_file"])
            (Path(repo) / "unreported.txt").write_text("unreported dirty file\n", encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(closeout_snapshot(repo, id="snapshot-after", captured_at="2999-05-22T00:10:00Z")),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("materialized_change_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertEqual(json.loads(invocation_path.read_text(encoding="utf-8")), before_invocation)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_real_executor_invocation_closeout_blocks_tampered_repo_transition(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            inputs, plan_path, _plan = self.write_real_executor_invocation_plan(tmp, repo)
            task_packet = inputs["task_packet"]
            task_path = Path(tmp) / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")

            invocation_result, invocation_output = self.run_invoke_real_executor_cli(tmp, plan_path)

            self.assertEqual(invocation_result.returncode, 0, invocation_result.stderr)
            invocation_path = Path(invocation_output["record_file"])
            result_path = Path(invocation_output["result_file"])
            original_head = invocation_output["repository_before"]["head"]
            (Path(repo) / "post-invocation.txt").write_text("post invocation commit\n", encoding="utf-8")
            git(repo, "add", "post-invocation.txt")
            git(repo, "commit", "-m", "post invocation commit")
            new_head = current_head(repo)
            tampered_invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
            tampered_invocation["repository_after"]["head"] = new_head
            branch = tampered_invocation["repository_after"]["branch"]
            tampered_invocation["repository_after"]["local_branch_refs"][branch] = new_head
            invocation_path.write_text(json.dumps(tampered_invocation), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(
                    closeout_snapshot(
                        repo,
                        id="snapshot-after",
                        head=original_head,
                        captured_at="2999-05-22T00:10:00Z",
                    )
                ),
                encoding="utf-8",
            )

            closeout_result, closeout_output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--real-invocation-file",
                str(invocation_path),
                "--cwd",
                repo,
            )

            self.assertEqual(closeout_result.returncode, 1)
            self.assertFalse(closeout_output["valid"])
            self.assertEqual(closeout_output["closeout_status"], "blocked")
            self.assertIn("invocation_checksum_mismatch", {blocker["code"] for blocker in closeout_output["blockers"]})
            self.assertNotIn("real_executor_invocation_record_updated", closeout_output["side_effects"])
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-1.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-1.json").exists())

    def test_closeout_executor_result_blocks_run_record_task_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-task-mismatch",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, _record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
                task_packet_checksum=checksum_json({"tampered": True}),
            )
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-task-mismatch",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertIn("run_task_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                "epoch-closeout-run-task-mismatch",
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_run_record_result_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-result-mismatch",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, _record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
                result_evidence_checksum=checksum_json({"tampered": True}),
            )
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-result-mismatch",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_result_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                "epoch-closeout-run-result-mismatch",
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_run_record_validation_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-validation-mismatch",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, _record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
                validation_packet_checksum=checksum_json({"tampered": True}),
            )
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-validation-mismatch",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_validation_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                "epoch-closeout-run-validation-mismatch",
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_run_record_repo_anchor_mismatch(self):
        for field, value in (
            ("name", "other/repo"),
            ("path", "C:/outside/repo"),
            ("branch", "other-branch"),
            ("head", "deadbeef"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                init_committed_repo(repo)
                task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                    tmp,
                    repo,
                )
                epoch_id = f"epoch-closeout-run-repo-{field}"
                write_active_epoch(
                    tmp,
                    epoch_id,
                    task_packet["snapshot"],
                    tasks=[task_packet["task"]],
                )
                validation = build_executor_result_validation_payload(
                    root=Path(tmp),
                    task_file=task_path,
                    result_file=result_path,
                    task_packet=task_packet,
                    result_evidence=result_evidence,
                    executor_started=False,
                )
                run_record_path, _record = write_execution_run_record(
                    tmp,
                    task_path=task_path,
                    result_path=result_path,
                    task_packet=task_packet,
                    result_evidence=result_evidence,
                    validation=validation,
                    repo_overrides={field: value},
                )
                before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
                before_audit_count = len(audit_records(tmp))

                result, output = run_cli(
                    tmp,
                    "closeout-executor-result",
                    "--epoch-id",
                    epoch_id,
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(result_path),
                    "--snapshot-after-file",
                    str(snapshot_after_path),
                    "--run-record-file",
                    str(run_record_path),
                )

                self.assertEqual(result.returncode, 1)
                self.assertFalse(output["valid"])
                self.assertIn("run_repo_anchor_mismatch", {blocker["code"] for blocker in output["blockers"]})
                assert_blocked_run_record_closeout_preserved(
                    self,
                    tmp,
                    epoch_id,
                    run_record_path,
                    before_record,
                    before_audit_count,
                    output,
                )

    def test_closeout_executor_result_blocks_partial_run_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-partial",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
            )
            record.pop("validation_packet_checksum")
            run_record_path.write_text(json.dumps(record), encoding="utf-8")
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-partial",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_record_incomplete", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                "epoch-closeout-run-partial",
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_malformed_run_record_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            epoch_id = "epoch-closeout-run-malformed"
            write_active_epoch(
                tmp,
                epoch_id,
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path = Path(tmp) / "execution-runs" / "execution-run-malformed.json"
            run_record_path.parent.mkdir(parents=True, exist_ok=True)
            run_record_path.write_text("{bad json", encoding="utf-8")
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                epoch_id,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIsNotNone(output)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertIn("run_record_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertNotIn("execution_run_record_updated", output["side_effects"])
            self.assertTrue((Path(tmp) / "epochs" / "active" / f"{epoch_id}.json").exists())
            records = audit_records(tmp)
            self.assertEqual(len(records), before_audit_count + 1)
            self.assertEqual(records[-1]["event"], "executor_epoch_closeout")

    def test_closeout_executor_result_blocks_run_record_outside_runtime_ledger(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            epoch_id = "epoch-closeout-run-outside-ledger"
            write_active_epoch(
                tmp,
                epoch_id,
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            _canonical_path, record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
            )
            run_record_path = Path(outside) / "execution-run-test-1.json"
            run_record_path.write_text(json.dumps(record), encoding="utf-8")
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                epoch_id,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_record_path_mismatch", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                epoch_id,
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_tampered_run_record_invocation_id(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            epoch_id = "epoch-closeout-run-invocation-mismatch"
            write_active_epoch(
                tmp,
                epoch_id,
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
            )
            record["invocation_id"] = "executor-fixture-invocation-tampered"
            run_record_path.write_text(json.dumps(record), encoding="utf-8")
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                epoch_id,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_validation_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                epoch_id,
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_blocks_run_record_closeout_replay(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-run-replay",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            validation = build_executor_result_validation_payload(
                root=Path(tmp),
                task_file=task_path,
                result_file=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                executor_started=False,
            )
            run_record_path, _record = write_execution_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
                validation=validation,
                closeout_status="completed",
            )
            before_record = json.loads(run_record_path.read_text(encoding="utf-8"))
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-run-replay",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertIn("run_record_closeout_replay", {blocker["code"] for blocker in output["blockers"]})
            assert_blocked_run_record_closeout_preserved(
                self,
                tmp,
                "epoch-closeout-run-replay",
                run_record_path,
                before_record,
                before_audit_count,
                output,
            )

    def test_closeout_executor_result_policy_file_blocks_embedded_git_pr_plan(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            git(repo, "switch", "-c", "feature/epoch-closeout")
            (Path(repo) / "README.md").write_text("hello\nbranch policy closeout\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "implement branch policy closeout fixture")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-branch-policy",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "branch_policy": {
                            "allowed_base_branches": ["main"],
                            "denied_target_branches": [],
                            "required_branch_prefixes": ["codex/"],
                            "allow_current_branch_main": True,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-branch-policy",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
                "--policy-file",
                str(policy_file),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["closeout_status"], "completed")
            self.assertFalse(output["git_pr_plan"]["ready_to_review"])
            self.assertEqual(output["next_decision"]["decision"], "generate_git_pr_plan")
            self.assertFalse(output["next_decision"]["git_pr_plan_ready"])
            self.assertEqual(output["git_pr_plan"]["recommended_next_action"], "address_blockers")
            self.assertIn(
                "branch_policy_required_prefix_missing",
                {blocker["code"] for blocker in output["git_pr_plan"]["blockers"]},
            )

    def test_closeout_executor_result_keeps_epoch_active_when_other_tasks_remain(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "README.md").write_text("hello\npartial closeout\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "complete first task")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            remaining_task = {
                "id": "candidate-2",
                "title": "Complete second task",
                "summary": "Follow-up epoch task.",
                "task_type": "execution",
                "bucket": "S",
                "source": "text_marker",
                "drivers": [],
                "evidence": {"path": "docs/roadmap.md"},
            }
            write_active_epoch(
                tmp,
                "epoch-closeout-partial",
                task_packet["snapshot"],
                tasks=[task_packet["task"], remaining_task],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-partial",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["closeout_status"], "task_completed")
            self.assertEqual(output["epoch_status"], "ACTIVE")
            self.assertEqual(output["next_decision"]["decision"], "continue")
            self.assertEqual(
                output["side_effects"],
                ["epoch_task_completed", "execution_run_record_updated", "execution_run_audit_appended", "audit_record_appended"],
            )
            self.assertIsNone(output["git_pr_plan"])
            active_path = Path(tmp) / "epochs" / "active" / "epoch-closeout-partial.json"
            self.assertTrue(active_path.exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-partial.json").exists())
            active_epoch = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active_epoch["completed_tasks"], ["candidate-1"])
            self.assertEqual(active_epoch["tasks"], [task_packet["task"], remaining_task])

    def test_closeout_executor_result_blocks_duplicate_epoch_task_ids(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "README.md").write_text("hello\nduplicate task id\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "complete duplicate task")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            duplicate_task = dict(task_packet["task"])
            duplicate_task["title"] = "Duplicate task id should block closeout"
            write_active_epoch(
                tmp,
                "epoch-closeout-duplicate-task",
                task_packet["snapshot"],
                tasks=[task_packet["task"], duplicate_task],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-duplicate-task",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertIn("invalid_active_epoch", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-closeout-duplicate-task.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-duplicate-task.json").exists())

    def test_closeout_executor_result_does_not_require_pr_template_for_partial_success(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "README.md").write_text("hello\npartial missing template\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "complete first task")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            remaining_task = {
                "id": "candidate-2",
                "title": "Complete second task",
                "summary": "Follow-up epoch task.",
                "task_type": "execution",
                "bucket": "S",
                "source": "text_marker",
                "drivers": [],
                "evidence": {"path": "docs/roadmap.md"},
            }
            write_active_epoch(
                tmp,
                "epoch-closeout-partial-template",
                task_packet["snapshot"],
                tasks=[task_packet["task"], remaining_task],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-partial-template",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
                "--pr-template-file",
                str(Path(tmp) / "missing-template.md"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["closeout_status"], "task_completed")
            self.assertIsNone(output["git_pr_plan"])
            active_epoch = json.loads(
                (Path(tmp) / "epochs" / "active" / "epoch-closeout-partial-template.json").read_text(encoding="utf-8")
            )
            self.assertEqual(active_epoch["completed_tasks"], ["candidate-1"])
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-partial-template.json").exists())

    def test_closeout_executor_result_blocks_snapshot_after_before_executor_end(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            snapshot_after["captured_at"] = "2999-05-22T00:01:00Z"
            snapshot_after_path.write_text(json.dumps(snapshot_after), encoding="utf-8")
            write_active_epoch(
                tmp,
                "epoch-closeout-stale-after",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-stale-after",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertIn("stale_snapshot_after", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-closeout-stale-after.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-stale-after.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "failed" / "epoch-closeout-stale-after.json").exists())

    def test_closeout_executor_result_validates_pr_plan_inputs_before_epoch_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "README.md").write_text("hello\nmissing template\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "complete template task")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-missing-template",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-missing-template",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
                "--emit-git-pr-plan",
                "--cwd",
                repo,
                "--pr-template-file",
                str(Path(tmp) / "missing-template.md"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIsNone(output)
            self.assertIn("could not read PR template file", result.stderr)
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-closeout-missing-template.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-missing-template.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "failed" / "epoch-closeout-missing-template.json").exists())
            self.assertFalse((Path(tmp) / "audit" / "events.jsonl").exists())

    def test_closeout_executor_result_does_not_reread_empty_pr_template_after_terminal_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            (Path(repo) / "README.md").write_text("hello\nempty template\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "complete empty template task")
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-empty-template",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            template_path = Path(tmp) / "empty-template.md"
            template_path.write_text("No markdown headings here.\n", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "epoch_id": "epoch-closeout-empty-template",
                    "task_file": str(task_path),
                    "result_file": str(result_path),
                    "snapshot_after_file": str(snapshot_after_path),
                    "allow_repo_local_root": False,
                    "emit_git_pr_plan": True,
                    "cwd": repo,
                    "base_branch": "main",
                    "branch_prefix": "cadence",
                    "pr_template_file": str(template_path),
                    "required_body_section": [],
                },
            )()
            emitted = []
            calls = 0

            def one_read_only(path):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise ValueError("template was read again")
                return []

            with mock.patch.object(cadence_cli, "load_template_sections", one_read_only):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.closeout_executor_result_command(args)

            self.assertEqual(code, 0)
            self.assertEqual(calls, 1)
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0]["closeout_status"], "completed")
            self.assertEqual(emitted[0]["snapshot_after_checksum"], checksum_json(snapshot_after))
            completed_path = Path(tmp) / "epochs" / "completed" / "epoch-closeout-empty-template.json"
            self.assertTrue(completed_path.exists())
            completed_epoch = json.loads(completed_path.read_text(encoding="utf-8"))
            self.assertEqual(completed_epoch["executor_closeout"]["result_file_checksum"], checksum_json(result_evidence))

    def test_closeout_executor_result_fails_epoch_for_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
                result_evidence={
                    "schema_version": "generic-executor-result.v1",
                    "packet": "executor_result",
                    "task_id": "candidate-1",
                    "executor_id": "fake-executor",
                    "started_at": "2999-05-22T00:00:00Z",
                    "ended_at": "2999-05-22T00:05:00Z",
                    "status": "blocked",
                    "files_changed": [],
                    "commands_run": [],
                    "validation_results": [],
                    "summary": "Executor hit an operator approval blocker.",
                    "confidence": "medium",
                    "blockers": ["operator approval required"],
                    "dirty_worktree": True,
                    "resulting_head": None,
                },
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-blocked",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-blocked",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["closeout_status"], "failed")
            self.assertEqual(output["epoch_status"], "FAILED")
            self.assertEqual(output["failure_reason"], "executor_result_blocked")
            self.assertEqual(output["next_decision"]["decision"], "handoff")
            failed_path = Path(tmp) / "epochs" / "failed" / "epoch-closeout-blocked.json"
            self.assertTrue(failed_path.exists())
            failed_epoch = json.loads(failed_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_epoch["failure_reason"], "executor_result_blocked")

    def test_closeout_executor_result_blocks_stale_task_snapshot_without_closing_epoch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            stale_epoch_snapshot = dict(task_packet["snapshot"])
            stale_epoch_snapshot["id"] = "snapshot-before-different"
            write_active_epoch(
                tmp,
                "epoch-closeout-stale",
                stale_epoch_snapshot,
                tasks=[task_packet["task"]],
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-stale",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertEqual(output["next_decision"]["decision"], "validate_more_evidence")
            self.assertIn("stale_task_snapshot", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "epochs" / "active" / "epoch-closeout-stale.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "completed" / "epoch-closeout-stale.json").exists())
            self.assertFalse((Path(tmp) / "epochs" / "failed" / "epoch-closeout-stale.json").exists())

    def test_closeout_executor_result_blocks_active_epoch_conflict(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )
            write_active_epoch(tmp, "epoch-conflict-1", task_packet["snapshot"], tasks=[task_packet["task"]])
            write_active_epoch(tmp, "epoch-conflict-2", task_packet["snapshot"], tasks=[task_packet["task"]])

            result, output = run_cli(
                tmp,
                "closeout-executor-result",
                "--epoch-id",
                "epoch-conflict-1",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["closeout_status"], "blocked")
            self.assertIn("active_epoch_conflict", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(len(list((Path(tmp) / "epochs" / "active").glob("*.json"))), 2)

    def test_closeout_executor_result_rerun_reports_already_closed_without_second_completion(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, snapshot_after_path, task_packet, result_evidence, _snapshot_after = write_closeout_packets(
                tmp,
                repo,
            )
            run_record_path, _run_record = write_closeout_run_record(
                tmp,
                task_path=task_path,
                result_path=result_path,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )
            write_active_epoch(
                tmp,
                "epoch-closeout-rerun",
                task_packet["snapshot"],
                tasks=[task_packet["task"]],
            )
            args = [
                "closeout-executor-result",
                "--epoch-id",
                "epoch-closeout-rerun",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--run-record-file",
                str(run_record_path),
            ]

            first_result, first_output = run_cli(tmp, *args)
            second_result, second_output = run_cli(tmp, *args)

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertTrue(first_output["valid"])
            self.assertEqual(second_result.returncode, 1)
            self.assertFalse(second_output["valid"])
            self.assertEqual(second_output["closeout_status"], "already_closed")
            self.assertIn("epoch_already_closed", {blocker["code"] for blocker in second_output["blockers"]})
            self.assertEqual(len(list((Path(tmp) / "epochs" / "completed").glob("epoch-closeout-rerun.json"))), 1)
            self.assertEqual(len(list((Path(tmp) / "epochs" / "failed").glob("epoch-closeout-rerun.json"))), 0)
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 2)
            self.assertEqual(json.loads(audit_lines[0])["event"], "execution_run_record")
            self.assertEqual(json.loads(audit_lines[1])["event"], "executor_epoch_closeout")

    def test_work_ownership_status_surfaces_valid_active_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            write_work_ownership(tmp, "ownership-1", branch=current_branch(repo))

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "work-ownership-status.v1")
            self.assertEqual(output["packet"], "work_ownership_status")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["recommended_next_action"], "use_work_ownership_status")
            self.assertEqual(output["counts"]["active"], 1)
            self.assertEqual(output["records"][0]["id"], "ownership-1")
            self.assertEqual(output["records"][0]["task_id"], "task-1")
            self.assertEqual(output["records"][0]["branch"], current_branch(repo))

    def test_claim_work_ownership_writes_active_record_and_replayable_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            head = current_head(repo)

            result, output = run_cli(
                tmp,
                "claim-work-ownership",
                "--id",
                "ownership-claim-1",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--branch",
                branch,
                "--head",
                head,
                "--task-id",
                "task-1",
                "--candidate-id",
                "candidate-1",
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "work-ownership-claim.v1")
            self.assertEqual(output["packet"], "work_ownership_claim")
            self.assertFalse(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["recommended_next_action"], "use_work_ownership_record")
            self.assertEqual(output["ownership_id"], "ownership-claim-1")
            self.assertIn("work_ownership_active_written", output["side_effects"])
            self.assertIn("work_ownership_audit_appended", output["side_effects"])
            record_path = Path(output["record"]["path"])
            self.assertTrue(record_path.exists())
            self.assertEqual(record_path.parent, Path(tmp) / "work-ownership" / "active")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], "work-ownership.v1")
            self.assertEqual(record["status"], "ACTIVE")
            self.assertEqual(record["task_id"], "task-1")
            self.assertEqual(record["candidate_id"], "candidate-1")
            self.assertEqual(record["role"], "implementer")
            self.assertEqual(record["claimer"], "test-agent")
            self.assertEqual(record["repo"], "local/test")
            self.assertEqual(record["branch"], branch)
            self.assertEqual(record["head"], head)
            self.assertEqual(list((Path(tmp) / "work-ownership" / "active").glob("*.json")), [record_path])

            records = audit_records(tmp)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["event"], "work_ownership_mutation")
            self.assertEqual(records[0]["action"], "claim_work_ownership")
            self.assertEqual(records[0]["ownership_id"], "ownership-claim-1")
            payload_without_audit_ref = dict(output)
            payload_without_audit_ref.pop("audit_record")
            self.assertEqual(records[0]["payload_checksum"], checksum_json(payload_without_audit_ref))

            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["events_by_type"]["work_ownership_mutation"], 1)

    def test_claim_work_ownership_blocks_duplicate_active_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-existing", branch=branch)

            result, output = run_cli(
                tmp,
                "claim-work-ownership",
                "--id",
                "ownership-new",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--branch",
                branch,
                "--head",
                current_head(repo),
                "--task-id",
                "task-1",
                "--candidate-id",
                "candidate-1",
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "close_or_fail_active_ownership")
            self.assertIn("duplicate_active_ownership", {blocker["code"] for blocker in output["blockers"]})
            self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-new.json").exists())
            self.assertEqual(audit_records(tmp), [])

    def test_claim_work_ownership_blocks_repo_state_and_identity_preflight(self):
        cases = [
            (
                "repo_branch_mismatch",
                "inspect_repo_state",
                lambda repo, branch, head: {"--branch": "other-branch"},
                lambda repo: None,
            ),
            (
                "repo_head_mismatch",
                "inspect_repo_state",
                lambda repo, branch, head: {"--head": "0" * 40},
                lambda repo: None,
            ),
            (
                "dirty_worktree",
                "clean_worktree",
                lambda repo, branch, head: {},
                lambda repo: (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8"),
            ),
            (
                "ownership_role_invalid",
                "fix_ownership_request",
                lambda repo, branch, head: {"--role": "bad role"},
                lambda repo: None,
            ),
            (
                "ownership_claimer_invalid",
                "fix_ownership_request",
                lambda repo, branch, head: {"--claimer": "bad claimer"},
                lambda repo: None,
            ),
        ]
        for expected_code, expected_action, arg_overrides, mutate_repo in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    head = current_head(repo)
                    mutate_repo(repo)
                    args = {
                        "--id": "ownership-new",
                        "--cwd": repo,
                        "--repo": "local/test",
                        "--branch": branch,
                        "--head": head,
                        "--task-id": "task-1",
                        "--candidate-id": "candidate-1",
                        "--role": "implementer",
                        "--claimer": "test-agent",
                    }
                    args.update(arg_overrides(repo, branch, head))
                    cli_args = ["claim-work-ownership"]
                    for key, value in args.items():
                        cli_args.extend([key, value])

                    result, output = run_cli(tmp, *cli_args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-new.json").exists())
                    self.assertEqual(audit_records(tmp), [])

    def test_claim_work_ownership_blocks_stale_and_malformed_registry_evidence(self):
        cases = [
            (
                "ownership_stale",
                lambda tmp, repo, branch: write_work_ownership(
                    tmp,
                    "ownership-stale",
                    branch=branch,
                    created_at="2000-01-01T00:00:00Z",
                    updated_at="2000-01-01T00:00:00Z",
                ),
            ),
            (
                "ownership_required_field_missing",
                lambda tmp, repo, branch: (
                    lambda path_data: (
                        path_data[0].write_text(
                            json.dumps({key: value for key, value in path_data[1].items() if key != "task_id"}),
                            encoding="utf-8",
                        )
                    )
                )(write_work_ownership(tmp, "ownership-malformed", branch=branch)),
            ),
            (
                "ownership_registry_state_invalid",
                lambda tmp, repo, branch: (Path(tmp) / "work-ownership").write_text("not a directory\n", encoding="utf-8"),
            ),
        ]
        for expected_code, seed_registry in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    seed_registry(tmp, repo, branch)

                    result, output = run_cli(
                        tmp,
                        "claim-work-ownership",
                        "--id",
                        "ownership-new",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--branch",
                        branch,
                        "--head",
                        current_head(repo),
                        "--task-id",
                        "task-1",
                        "--candidate-id",
                        "candidate-1",
                        "--role",
                        "implementer",
                        "--claimer",
                        "test-agent",
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-new.json").exists())
                    self.assertEqual(audit_records(tmp), [])

    def test_claim_work_ownership_rolls_back_record_when_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            branch = current_branch(repo)
            head = current_head(repo)
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "id": "ownership-audit-fail",
                    "cwd": repo,
                    "repo": "local/test",
                    "branch": branch,
                    "head": head,
                    "task_id": "task-1",
                    "candidate_id": "candidate-1",
                    "role": "implementer",
                    "claimer": "test-agent",
                    "pr_number": None,
                    "epoch_id": None,
                    "handoff_id": None,
                    "max_age_minutes": 5,
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.claim_work_ownership_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertFalse(emitted[0]["ownership_written"])
            self.assertIn("audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertIn("work_ownership_active_rollback", emitted[0]["side_effects"])
            self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-audit-fail.json").exists())
            self.assertEqual(audit_records(tmp), [])

    def test_close_and_fail_work_ownership_move_active_record_with_audit(self):
        cases = [
            ("close-work-ownership", "CLOSED", "closed", "close_work_ownership"),
            ("fail-work-ownership", "FAILED", "failed", "fail_work_ownership"),
        ]
        for command, status, state, action in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    head = current_head(repo)
                    write_work_ownership(tmp, "ownership-1", branch=branch, head=head)

                    result, output = run_cli(
                        tmp,
                        command,
                        "ownership-1",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--branch",
                        branch,
                        "--head",
                        head,
                        "--task-id",
                        "task-1",
                        "--claimer",
                        "test-agent",
                        "--summary",
                        f"{status.lower()} locally",
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output["schema_version"], "work-ownership-closeout.v1")
                    self.assertEqual(output["packet"], "work_ownership_closeout")
                    self.assertTrue(output["valid"])
                    self.assertEqual(output["closeout_status"], status)
                    self.assertIn("work_ownership_active_moved", output["side_effects"])
                    self.assertIn("work_ownership_audit_appended", output["side_effects"])
                    self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
                    target = Path(tmp) / "work-ownership" / state / "ownership-1.json"
                    self.assertTrue(target.exists())
                    record = json.loads(target.read_text(encoding="utf-8"))
                    self.assertEqual(record["status"], status)
                    self.assertEqual(record["closeout"]["summary"], f"{status.lower()} locally")
                    self.assertEqual(record["closeout"]["claimer"], "test-agent")
                    self.assertEqual(record["closeout"]["head"], head)

                    records = audit_records(tmp)
                    self.assertEqual(len(records), 1)
                    self.assertEqual(records[0]["event"], "work_ownership_mutation")
                    self.assertEqual(records[0]["action"], action)
                    self.assertEqual(records[0]["closeout_status"], status)
                    payload_without_audit_ref = dict(output)
                    payload_without_audit_ref.pop("audit_record")
                    self.assertEqual(records[0]["payload_checksum"], checksum_json(payload_without_audit_ref))

                    replay_result, replay_output = run_cli(tmp, "audit-replay")
                    self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
                    self.assertTrue(replay_output["valid"])

    def test_close_and_fail_work_ownership_roll_back_move_when_audit_append_fails(self):
        cases = [
            ("close_work_ownership_command", "closed"),
            ("fail_work_ownership_command", "failed"),
        ]
        for command_name, state in cases:
            with self.subTest(command_name=command_name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    import codex_cadence.cli as cadence_cli

                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    head = current_head(repo)
                    active_path, before = write_work_ownership(tmp, "ownership-1", branch=branch, head=head)
                    emitted = []
                    args = type(
                        "Args",
                        (),
                        {
                            "root": Path(tmp),
                            "target": "ownership-1",
                            "cwd": repo,
                            "repo": "local/test",
                            "branch": branch,
                            "head": head,
                            "task_id": "task-1",
                            "claimer": "test-agent",
                            "summary": "done locally",
                        },
                    )()

                    with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                        with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                            code = getattr(cadence_cli, command_name)(args)

                    self.assertEqual(code, 2)
                    self.assertEqual(len(emitted), 1)
                    self.assertFalse(emitted[0]["valid"])
                    self.assertFalse(emitted[0]["ownership_moved"])
                    self.assertIn("audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
                    self.assertIn("work_ownership_closeout_rollback", emitted[0]["side_effects"])
                    self.assertTrue(active_path.exists())
                    self.assertEqual(json.loads(active_path.read_text(encoding="utf-8")), before)
                    self.assertFalse((Path(tmp) / "work-ownership" / state / "ownership-1.json").exists())
                    self.assertEqual(audit_records(tmp), [])

    def test_complete_work_ownership_from_closeout_closes_matching_active_record_with_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            branch = task_packet["repo"]["branch"]
            head = task_packet["repo"]["head"]
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=branch,
                head=head,
                epoch_id=closeout_packet["epoch_id"],
            )

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(closeout_path),
                "--closeout-checksum",
                checksum_json(closeout_packet),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
                "--summary",
                "completed via executor closeout",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "work-ownership-closeout.v1")
            self.assertEqual(output["packet"], "work_ownership_closeout")
            self.assertTrue(output["valid"])
            self.assertEqual(output["closeout_status"], "CLOSED")
            self.assertFalse((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            closed_path = Path(tmp) / "work-ownership" / "closed" / "ownership-1.json"
            self.assertTrue(closed_path.exists())
            closed_record = json.loads(closed_path.read_text(encoding="utf-8"))
            self.assertEqual(closed_record["status"], "CLOSED")
            self.assertEqual(closed_record["closeout"]["executor_closeout_file"], str(closeout_path))
            self.assertEqual(closed_record["closeout"]["executor_closeout_checksum"], checksum_json(closeout_packet))
            self.assertEqual(closed_record["closeout"]["executor_closeout_status"], "completed")
            self.assertEqual(closed_record["closeout"]["epoch_id"], "epoch-closeout-owned")

            ownership_records = [record for record in audit_records(tmp) if record["event"] == "work_ownership_mutation"]
            self.assertEqual(len(ownership_records), 1)
            self.assertEqual(ownership_records[0]["action"], "close_work_ownership")
            self.assertEqual(ownership_records[0]["executor_closeout_file"], str(closeout_path))
            self.assertEqual(ownership_records[0]["executor_closeout_checksum"], checksum_json(closeout_packet))
            self.assertEqual(ownership_records[0]["executor_closeout_status"], "completed")
            self.assertEqual(ownership_records[0]["epoch_id"], "epoch-closeout-owned")

            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])

    def test_complete_work_ownership_from_closeout_blocks_mismatched_anchors_before_move(self):
        cases = [
            (
                "ownership_record_missing",
                lambda tmp, closeout_packet, task_packet, branch, head: None,
                "missing-ownership",
                {},
            ),
            (
                "ownership_task_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id="task-other",
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_candidate_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {"--candidate-id": "candidate-other"},
            ),
            (
                "ownership_role_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {"--role": "reviewer"},
            ),
            (
                "ownership_candidate_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id="candidate-other",
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {"--candidate-id": "candidate-other"},
            ),
            (
                "ownership_claimer_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {"--claimer": "other-agent"},
            ),
            (
                "ownership_branch_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch="other-branch",
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_head_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head="0" * 40,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_head_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_epoch_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id="epoch-other",
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_closeout_checksum_mismatch",
                lambda tmp, closeout_packet, task_packet, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    task_id=task_packet["task"]["id"],
                    candidate_id=task_packet["task"]["id"],
                    branch=branch,
                    head=head,
                    epoch_id=closeout_packet["epoch_id"],
                ),
                "ownership-1",
                {"--closeout-checksum": "sha256:" + "f" * 64},
            ),
        ]
        for expected_code, seed_ownership, target, overrides in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                        tmp,
                        repo,
                        epoch_id="epoch-closeout-owned",
                    )
                    branch = task_packet["repo"]["branch"]
                    head = task_packet["repo"]["head"]
                    seed_ownership(tmp, closeout_packet, task_packet, branch, head)
                    before_audit_count = len(audit_records(tmp))
                    args = {
                        "--cwd": repo,
                        "--closeout-file": str(closeout_path),
                        "--closeout-checksum": checksum_json(closeout_packet),
                        "--candidate-id": task_packet["task"]["id"],
                        "--role": "implementer",
                        "--claimer": "test-agent",
                        "--summary": "completed via executor closeout",
                    }
                    args.update(overrides)
                    cli_args = ["complete-work-ownership-from-closeout", target]
                    for key, value in args.items():
                        cli_args.extend([key, value])

                    result, output = run_cli(tmp, *cli_args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
                    if target == "ownership-1":
                        self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
                    self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_failed_closeout_without_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-blocked",
                result_evidence={
                    "schema_version": "generic-executor-result.v1",
                    "packet": "executor_result",
                    "task_id": "candidate-1",
                    "executor_id": "fake-executor",
                    "started_at": "2999-05-22T00:00:00Z",
                    "ended_at": "2999-05-22T00:05:00Z",
                    "status": "blocked",
                    "files_changed": [],
                    "commands_run": [],
                    "validation_results": [],
                    "summary": "Executor hit an operator approval blocker.",
                    "confidence": "medium",
                    "blockers": ["operator approval required"],
                    "dirty_worktree": True,
                    "resulting_head": None,
                },
            )
            branch = task_packet["repo"]["branch"]
            head = task_packet["repo"]["head"]
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=branch,
                head=head,
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(closeout_path),
                "--closeout-checksum",
                checksum_json(closeout_packet),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
                "--summary",
                "completed via executor closeout",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_not_completed", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_mutated_task_file_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            task_file = Path(closeout_packet["task_file"])
            mutated_task = dict(task_packet)
            mutated_task["task"] = dict(task_packet["task"])
            mutated_task["task"]["id"] = "candidate-other"
            task_file.write_text(json.dumps(mutated_task), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(closeout_path),
                "--closeout-checksum",
                checksum_json(closeout_packet),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
                "--summary",
                "completed via executor closeout",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_task_checksum_mismatch", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_minimal_handwritten_closeout_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            minimal_closeout = {
                "schema_version": "executor-epoch-closeout.v1",
                "packet": "executor_epoch_closeout",
                "valid": True,
                "reason": "executor result succeeded",
                "epoch_id": closeout_packet["epoch_id"],
                "closeout_status": "completed",
                "task_file": closeout_packet["task_file"],
                "task_checksum": closeout_packet["task_checksum"],
            }
            minimal_closeout_path = Path(tmp) / "minimal-executor-closeout.json"
            minimal_closeout_path.write_text(json.dumps(minimal_closeout), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(minimal_closeout_path),
                "--closeout-checksum",
                checksum_json(minimal_closeout),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_mutated_validation_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            mutated_closeout = dict(closeout_packet)
            mutated_validation = dict(closeout_packet["validation"])
            mutated_validation["executor_started"] = not mutated_validation["executor_started"]
            mutated_closeout["validation"] = mutated_validation
            mutated_closeout["operator_confirmation_required"] = False
            mutated_closeout_path = Path(tmp) / "mutated-executor-closeout.json"
            mutated_closeout_path.write_text(json.dumps(mutated_closeout), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(mutated_closeout_path),
                "--closeout-checksum",
                checksum_json(mutated_closeout),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_missing_execution_reference_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            unbound_closeout = dict(closeout_packet)
            unbound_closeout.pop("run_record", None)
            unbound_closeout.pop("real_invocation", None)
            unbound_closeout_path = Path(tmp) / "unbound-executor-closeout.json"
            unbound_closeout_path.write_text(json.dumps(unbound_closeout), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(unbound_closeout_path),
                "--closeout-checksum",
                checksum_json(unbound_closeout),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_forged_audit_reference_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            forged_closeout = dict(closeout_packet)
            forged_audit = dict(closeout_packet["audit_record"])
            forged_audit["event_hash"] = "sha256:" + "f" * 64
            forged_closeout["audit_record"] = forged_audit
            forged_closeout_path = Path(tmp) / "forged-audit-executor-closeout.json"
            forged_closeout_path.write_text(json.dumps(forged_closeout), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(forged_closeout_path),
                "--closeout-checksum",
                checksum_json(forged_closeout),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_blocks_invalid_audit_chain_index_before_move(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            _closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            forged_closeout = dict(closeout_packet)
            forged_audit = dict(closeout_packet["audit_record"])
            forged_audit["chain_index"] = 0
            forged_closeout["audit_record"] = forged_audit
            forged_closeout_path = Path(tmp) / "invalid-chain-index-executor-closeout.json"
            forged_closeout_path.write_text(json.dumps(forged_closeout), encoding="utf-8")
            write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))

            result, output = run_cli(
                tmp,
                "complete-work-ownership-from-closeout",
                "ownership-1",
                "--cwd",
                repo,
                "--closeout-file",
                str(forged_closeout_path),
                "--closeout-checksum",
                checksum_json(forged_closeout),
                "--candidate-id",
                task_packet["task"]["id"],
                "--role",
                "implementer",
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_closeout_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_complete_work_ownership_from_closeout_rolls_back_move_when_audit_append_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.cli as cadence_cli

            init_committed_repo(repo)
            closeout_path, closeout_packet, task_packet, _result_evidence = write_executor_closeout_packet(
                tmp,
                repo,
                epoch_id="epoch-closeout-owned",
            )
            active_path, before = write_work_ownership(
                tmp,
                "ownership-1",
                task_id=task_packet["task"]["id"],
                candidate_id=task_packet["task"]["id"],
                branch=task_packet["repo"]["branch"],
                head=task_packet["repo"]["head"],
                epoch_id=closeout_packet["epoch_id"],
            )
            before_audit_count = len(audit_records(tmp))
            emitted = []
            args = type(
                "Args",
                (),
                {
                    "root": Path(tmp),
                    "target": "ownership-1",
                    "cwd": repo,
                    "closeout_file": closeout_path,
                    "closeout_checksum": checksum_json(closeout_packet),
                    "candidate_id": task_packet["task"]["id"],
                    "role": "implementer",
                    "claimer": "test-agent",
                    "summary": "completed via executor closeout",
                },
            )()

            with mock.patch.object(cadence_cli, "append_audit_record", side_effect=OSError("disk full")):
                with mock.patch.object(cadence_cli, "emit", lambda payload: emitted.append(payload)):
                    code = cadence_cli.complete_work_ownership_from_closeout_command(args)

            self.assertEqual(code, 2)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(emitted[0]["valid"])
            self.assertFalse(emitted[0]["ownership_moved"])
            self.assertIn("audit_append_failed", {blocker["code"] for blocker in emitted[0]["blockers"]})
            self.assertIn("work_ownership_closeout_rollback", emitted[0]["side_effects"])
            self.assertTrue(active_path.exists())
            self.assertEqual(json.loads(active_path.read_text(encoding="utf-8")), before)
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
            self.assertEqual(len(audit_records(tmp)), before_audit_count)

    def test_closeout_work_ownership_removes_destination_when_source_unlink_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            import codex_cadence.ownership as ownership

            init_committed_repo(repo)
            branch = current_branch(repo)
            head = current_head(repo)
            active_path, _data = write_work_ownership(tmp, "ownership-1", branch=branch, head=head)
            original_unlink = Path.unlink

            def fail_active_unlink(path, *args, **kwargs):
                if path == active_path:
                    raise OSError("cannot remove source")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", fail_active_unlink):
                packet = ownership.closeout_work_ownership(
                    root=Path(tmp),
                    cwd=Path(repo),
                    target="ownership-1",
                    closeout_status="CLOSED",
                    repo="local/test",
                    branch=branch,
                    head=head,
                    task_id="task-1",
                    claimer="test-agent",
                    summary="done locally",
                )

            self.assertFalse(packet["valid"])
            self.assertFalse(packet["ownership_moved"])
            self.assertIn("ownership_record_write_failed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertIn("work_ownership_destination_rollback", packet["side_effects"])
            self.assertTrue(active_path.exists())
            self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())

    def test_closeout_work_ownership_rejects_invalid_executor_closeout_anchor_contract(self):
        cases = [
            (
                "ownership_closeout_packet_invalid",
                {"closeout_status": "FAILED"},
            ),
            (
                "ownership_required_field_missing",
                {"epoch_id": None},
            ),
            (
                "ownership_required_field_missing",
                {"candidate_id": None},
            ),
            (
                "ownership_required_field_missing",
                {"role": None},
            ),
            (
                "ownership_closeout_packet_invalid",
                {"executor_closeout_checksum": "sha256:bad"},
            ),
            (
                "ownership_closeout_not_completed",
                {"executor_closeout_status": "failed"},
            ),
        ]
        for expected_code, overrides in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    import codex_cadence.ownership as ownership

                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    head = current_head(repo)
                    active_path, _data = write_work_ownership(
                        tmp,
                        "ownership-1",
                        task_id="candidate-1",
                        candidate_id="candidate-1",
                        branch=branch,
                        head=head,
                        epoch_id="epoch-1",
                    )
                    args = {
                        "root": Path(tmp),
                        "cwd": Path(repo),
                        "target": "ownership-1",
                        "closeout_status": "CLOSED",
                        "repo": "local/test",
                        "branch": branch,
                        "head": head,
                        "task_id": "candidate-1",
                        "claimer": "test-agent",
                        "summary": "done locally",
                        "candidate_id": "candidate-1",
                        "role": "implementer",
                        "epoch_id": "epoch-1",
                        "executor_closeout_file": str(Path(tmp) / "executor-closeout.json"),
                        "executor_closeout_checksum": "sha256:" + "a" * 64,
                        "executor_closeout_status": "completed",
                    }
                    args.update(overrides)

                    packet = ownership.closeout_work_ownership(**args)

                    self.assertFalse(packet["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})
                    self.assertTrue(active_path.exists())
                    self.assertFalse((Path(tmp) / "work-ownership" / "closed" / "ownership-1.json").exists())
                    self.assertFalse((Path(tmp) / "work-ownership" / "failed" / "ownership-1.json").exists())

    def test_close_work_ownership_blocks_missing_mismatched_closed_and_malformed_records(self):
        cases = [
            (
                "ownership_record_missing",
                lambda tmp, repo, branch, head: None,
                "missing-ownership",
                {},
            ),
            (
                "ownership_repo_mismatch",
                lambda tmp, repo, branch, head: write_work_ownership(tmp, "ownership-1", repo="local/other", branch=branch, head=head),
                "ownership-1",
                {},
            ),
            (
                "ownership_branch_mismatch",
                lambda tmp, repo, branch, head: write_work_ownership(tmp, "ownership-1", branch="other-branch", head=head),
                "ownership-1",
                {},
            ),
            (
                "ownership_task_mismatch",
                lambda tmp, repo, branch, head: write_work_ownership(tmp, "ownership-1", branch=branch, head=head),
                "ownership-1",
                {"--task-id": "task-other"},
            ),
            (
                "ownership_head_mismatch",
                lambda tmp, repo, branch, head: write_work_ownership(tmp, "ownership-1", branch=branch, head="0" * 40),
                "ownership-1",
                {},
            ),
            (
                "ownership_claimer_mismatch",
                lambda tmp, repo, branch, head: write_work_ownership(tmp, "ownership-1", branch=branch, head=head),
                "ownership-1",
                {"--claimer": "other-agent"},
            ),
            (
                "ownership_closed",
                lambda tmp, repo, branch, head: write_work_ownership(
                    tmp,
                    "ownership-1",
                    state="closed",
                    status="CLOSED",
                    branch=branch,
                    head=head,
                ),
                "ownership-1",
                {},
            ),
            (
                "ownership_required_field_missing",
                lambda tmp, repo, branch, head: (
                    lambda path_data: path_data[0].write_text(
                        json.dumps({key: value for key, value in path_data[1].items() if key != "task_id"}),
                        encoding="utf-8",
                    )
                )(write_work_ownership(tmp, "ownership-1", branch=branch, head=head)),
                "ownership-1",
                {},
            ),
        ]
        for expected_code, seed_registry, target, overrides in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    head = current_head(repo)
                    seed_registry(tmp, repo, branch, head)
                    args = {
                        "--cwd": repo,
                        "--repo": "local/test",
                        "--branch": branch,
                        "--head": head,
                        "--task-id": "task-1",
                        "--claimer": "test-agent",
                        "--summary": "done locally",
                    }
                    args.update(overrides)
                    cli_args = ["close-work-ownership", target]
                    for key, value in args.items():
                        cli_args.extend([key, value])

                    result, output = run_cli(tmp, *cli_args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    if target == "ownership-1" and expected_code != "ownership_closed":
                        self.assertTrue((Path(tmp) / "work-ownership" / "active" / "ownership-1.json").exists())
                    if expected_code != "ownership_closed":
                        self.assertFalse((Path(tmp) / "work-ownership" / "closed" / f"{target}.json").exists())
                    self.assertEqual(audit_records(tmp), [])

    def test_work_ownership_status_blocks_duplicate_active_task_branch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-1", branch=branch)
            write_work_ownership(tmp, "ownership-2", branch=branch, claimer="other-agent")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "resolve_duplicate_ownership")
            self.assertIn("duplicate_active_ownership", {blocker["code"] for blocker in output["blockers"]})

    def test_work_ownership_status_ignores_other_repo_same_task_id(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-1", branch=branch)
            write_work_ownership(tmp, "ownership-other", repo="local/other", branch=branch)

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["counts"]["active"], 1)
            self.assertEqual([record["id"] for record in output["records"]], ["ownership-1"])

    def test_work_ownership_status_reports_malformed_record_even_when_task_filter_is_set(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            path, data = write_work_ownership(tmp, "ownership-malformed", branch=current_branch(repo))
            data.pop("task_id")
            path.write_text(json.dumps(data), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_required_field_missing", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["records"][0]["id"], "ownership-malformed")

    def test_work_ownership_status_blocks_duplicate_when_duplicate_record_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-1", branch=branch)
            path, data = write_work_ownership(tmp, "ownership-2", branch=branch, claimer="other-agent")
            data["candidate_id"] = "not a valid candidate id"
            path.write_text(json.dumps(data), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("ownership_id_invalid", codes)
            self.assertIn("duplicate_active_ownership", codes)

    def test_work_ownership_status_rejects_future_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            write_work_ownership(
                tmp,
                "ownership-1",
                branch=current_branch(repo),
                updated_at="2999-01-01T00:00:00Z",
            )

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_timestamp_invalid", {blocker["code"] for blocker in output["blockers"]})

    def test_work_ownership_status_rejects_symlinked_registry_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            outside = Path(tmp) / "outside-active"
            outside.mkdir()
            active = Path(tmp) / "work-ownership" / "active"
            active.parent.mkdir(parents=True, exist_ok=True)
            try:
                active.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            write_work_ownership(outside.parent, "ownership-1", branch=current_branch(repo))

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_registry_state_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["records"], [])

    def test_work_ownership_status_rejects_invalid_registry_parent_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(tmp) / "work-ownership").write_text("not a directory\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_registry_state_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["records"], [])

    def test_work_ownership_status_rejects_symlinked_record_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            outside_root = Path(tmp) / "outside-root"
            outside_path, _data = write_work_ownership(outside_root, "ownership-1", branch=current_branch(repo))
            active = Path(tmp) / "work-ownership" / "active"
            active.mkdir(parents=True, exist_ok=True)
            link = active / "ownership-1.json"
            try:
                link.symlink_to(outside_path)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"file symlink unavailable: {exc}")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_record_path_invalid", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["records"][0]["path"], str(link))

    def test_work_ownership_status_rejects_record_id_mismatch_with_filename(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            path, data = write_work_ownership(tmp, "ownership-a", branch=current_branch(repo))
            data["id"] = "ownership-b"
            path.write_text(json.dumps(data), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_id_mismatch", {blocker["code"] for blocker in output["blockers"]})

    def test_work_ownership_status_reports_repo_inspection_failed_as_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as non_repo:
            result, output = run_cli(
                tmp,
                "work-ownership-status",
                "--cwd",
                non_repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(output["schema_version"], "work-ownership-status.v1")
            self.assertFalse(output["valid"])
            self.assertIn("repo_inspection_failed", {blocker["code"] for blocker in output["blockers"]})

    def test_validate_work_ownership_blocks_duplicate_active_task_branch(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-1", branch=branch)
            write_work_ownership(tmp, "ownership-2", branch=branch, claimer="other-agent")

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                "ownership-1",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--require-active",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "resolve_duplicate_ownership")
            self.assertIn("duplicate_active_ownership", {blocker["code"] for blocker in output["blockers"]})

    def test_validate_work_ownership_rejects_path_outside_runtime_registry(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            outside_path, _data = write_work_ownership(Path(tmp) / "outside-runtime", "ownership-1", branch=current_branch(repo))

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                str(outside_path),
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_record_outside_registry", {blocker["code"] for blocker in output["blockers"]})

    def test_validate_work_ownership_reports_missing_registry_path_target(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            missing_path = Path(tmp) / "work-ownership" / "active" / "missing-ownership.json"
            missing_path.parent.mkdir(parents=True, exist_ok=True)

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                str(missing_path),
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("ownership_record_missing", codes)
            self.assertNotIn("ownership_id_invalid", codes)

    def test_validate_work_ownership_rejects_record_id_mismatch_with_target(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            path, data = write_work_ownership(tmp, "ownership-a", branch=current_branch(repo))
            data["id"] = "ownership-b"
            path.write_text(json.dumps(data), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                "ownership-a",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--require-active",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertIn("ownership_id_mismatch", {blocker["code"] for blocker in output["blockers"]})

    def test_validate_work_ownership_prefers_registry_id_over_local_path_collision(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as collision_cwd:
            init_committed_repo(repo)
            write_work_ownership(tmp, "ownership-1", branch=current_branch(repo))
            (Path(collision_cwd) / "ownership-1").write_text("not registry evidence\n", encoding="utf-8")

            result, output = run_cli_from(
                collision_cwd,
                tmp,
                "validate-work-ownership",
                "ownership-1",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--require-active",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["record"]["id"], "ownership-1")

    def test_validate_work_ownership_blocks_malformed_duplicate_record(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(tmp, "ownership-1", branch=branch)
            path, data = write_work_ownership(tmp, "ownership-2", branch=branch, claimer="other-agent")
            data["candidate_id"] = "not a valid candidate id"
            path.write_text(json.dumps(data), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                "ownership-1",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--require-active",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            codes = {blocker["code"] for blocker in output["blockers"]}
            self.assertIn("ownership_id_invalid", codes)
            self.assertIn("duplicate_active_ownership", codes)

    def test_validate_work_ownership_reports_repo_inspection_failed_as_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as non_repo:
            write_work_ownership(tmp, "ownership-1")

            result, output = run_cli(
                tmp,
                "validate-work-ownership",
                "ownership-1",
                "--cwd",
                non_repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(output["schema_version"], "work-ownership-validation.v1")
            self.assertFalse(output["valid"])
            self.assertIn("repo_inspection_failed", {blocker["code"] for blocker in output["blockers"]})

    def test_validate_work_ownership_blocks_closed_stale_and_repo_mismatch(self):
        cases = [
            (
                "closed-ownership",
                {"state": "closed", "status": "CLOSED"},
                "ownership_closed",
            ),
            (
                "stale-ownership",
                {"updated_at": "2000-01-01T00:00:00Z"},
                "ownership_stale",
            ),
            (
                "repo-mismatch",
                {"repo": "local/other"},
                "ownership_repo_mismatch",
            ),
        ]
        for ownership_id, overrides, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    write_work_ownership(tmp, ownership_id, branch=current_branch(repo), **overrides)

                    result, output = run_cli(
                        tmp,
                        "validate-work-ownership",
                        ownership_id,
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--require-active",
                        "--max-age-minutes",
                        "5",
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(output["schema_version"], "work-ownership-validation.v1")
                    self.assertEqual(output["packet"], "work_ownership_validation")
                    self.assertTrue(output["read_only"])
                    self.assertFalse(output["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_accepts_separated_builder_and_review_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(
                tmp,
                "ownership-builder",
                branch=branch,
                role="implementer",
                claimer="builder-agent",
                task_id="task-1",
            )
            policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
            pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
            review_threads_path, _threads = write_role_review_threads(
                Path(tmp) / "review-threads.json",
                role_review_threads(author="reviewer-agent"),
            )

            result, output = run_cli(
                tmp,
                "role-readiness",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--role-policy-file",
                str(policy_path),
                "--pr-json-file",
                str(pr_path),
                "--review-threads-file",
                str(review_threads_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "role-readiness.v1")
            self.assertEqual(output["packet"], "role_readiness")
            self.assertTrue(output["read_only"])
            self.assertTrue(output["valid"])
            self.assertTrue(output["role_ready"])
            self.assertEqual(output["blockers"], [])
            self.assertEqual(output["recommended_next_action"], "use_role_readiness")
            self.assertEqual(output["side_effects"], [])
            self.assertEqual(output["role_summary"]["builder_claimers"], ["builder-agent"])
            self.assertEqual(output["role_summary"]["reviewer_claimers"], ["reviewer-agent"])
            self.assertEqual(output["review_evidence"]["actionable_review_authors"], ["reviewer-agent"])
            self.assertIn("does_not_call_github", output["limitations"])

    def test_role_readiness_ignores_builder_replies_when_independent_reviewer_exists(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(
                tmp,
                "ownership-builder",
                branch=branch,
                role="implementer",
                claimer="builder-agent",
                task_id="task-1",
            )
            review_threads = role_review_threads(author="reviewer-agent", body="Please tighten this check.")
            comments = review_threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"][
                "nodes"
            ]
            comments.append(
                {
                    "id": "comment-2",
                    "path": "codex_cadence/roles.py",
                    "line": 42,
                    "outdated": False,
                    "body": "Fixed in latest push.",
                    "author": {"login": "builder-agent"},
                }
            )
            policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
            pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
            review_threads_path, _threads = write_role_review_threads(
                Path(tmp) / "review-threads.json",
                review_threads,
            )

            result, output = run_cli(
                tmp,
                "role-readiness",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--role-policy-file",
                str(policy_path),
                "--pr-json-file",
                str(pr_path),
                "--review-threads-file",
                str(review_threads_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["review_evidence"]["actionable_review_authors"], ["reviewer-agent"])
            self.assertEqual(output["review_evidence"]["ignored_builder_review_authors"], ["builder-agent"])
            self.assertNotIn("review_separation_conflict", {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_blocks_missing_policy_unknown_role_and_same_claimer_review(self):
        cases = [
            (
                "missing-policy",
                None,
                {"role": "implementer", "claimer": "builder-agent"},
                role_review_threads(author="reviewer-agent"),
                "role_policy_missing",
                "provide_role_policy",
            ),
            (
                "unknown-role",
                {},
                {"role": "planner", "claimer": "builder-agent"},
                role_review_threads(author="reviewer-agent"),
                "ownership_role_unknown",
                "fix_role_policy_or_ownership",
            ),
            (
                "invalid-separation-role",
                {
                    "review_separation": {
                        "required": True,
                        "builder_roles": ["builder-unknown"],
                        "reviewer_roles": ["reviewer"],
                    }
                },
                {"role": "implementer", "claimer": "builder-agent"},
                role_review_threads(author="reviewer-agent"),
                "role_policy_invalid",
                "provide_role_policy",
            ),
            (
                "same-claimer-review",
                {},
                {"role": "implementer", "claimer": "builder-agent"},
                role_review_threads(author="builder-agent"),
                "review_separation_conflict",
                "assign_independent_reviewer",
            ),
        ]
        for name, policy_overrides, ownership_overrides, review_threads, expected_code, expected_action in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    write_work_ownership(
                        tmp,
                        "ownership-builder",
                        branch=branch,
                        task_id="task-1",
                        **ownership_overrides,
                    )
                    args = [
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                    ]
                    if policy_overrides is not None:
                        policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json", **policy_overrides)
                        args.extend(["--role-policy-file", str(policy_path)])
                    pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
                    review_threads_path, _threads = write_role_review_threads(
                        Path(tmp) / "review-threads.json",
                        review_threads,
                    )
                    args.extend(["--pr-json-file", str(pr_path), "--review-threads-file", str(review_threads_path)])

                    result, output = run_cli(tmp, *args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIsNotNone(output, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["role_ready"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})
                    self.assertTrue(output["read_only"])
                    self.assertEqual(output["side_effects"], [])

    def test_role_readiness_blocks_stale_ownership_and_missing_builder_evidence(self):
        cases = [
            (
                "stale-builder",
                {
                    "role": "implementer",
                    "claimer": "builder-agent",
                    "created_at": "2000-01-01T00:00:00Z",
                    "updated_at": "2000-01-01T00:00:00Z",
                },
                "ownership_stale",
                "refresh_ownership_evidence",
            ),
            (
                "missing-builder",
                None,
                "builder_ownership_missing",
                "claim_work_ownership",
            ),
        ]
        for name, ownership_overrides, expected_code, expected_action in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    if ownership_overrides is not None:
                        write_work_ownership(
                            tmp,
                            "ownership-builder",
                            branch=branch,
                            task_id="task-1",
                            **ownership_overrides,
                        )
                    policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
                    pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
                    review_threads_path, _threads = write_role_review_threads(
                        Path(tmp) / "review-threads.json",
                        role_review_threads(author="reviewer-agent"),
                    )

                    result, output = run_cli(
                        tmp,
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--role-policy-file",
                        str(policy_path),
                        "--pr-json-file",
                        str(pr_path),
                        "--review-threads-file",
                        str(review_threads_path),
                        "--max-ownership-age-minutes",
                        "5",
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertFalse(output["role_ready"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_forwards_duplicate_active_ownership(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            for ownership_id, claimer in (
                ("ownership-builder-1", "builder-agent-1"),
                ("ownership-builder-2", "builder-agent-2"),
            ):
                write_work_ownership(
                    tmp,
                    ownership_id,
                    branch=branch,
                    role="implementer",
                    claimer=claimer,
                    task_id="task-1",
                )
            policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
            pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
            review_threads_path, _threads = write_role_review_threads(
                Path(tmp) / "review-threads.json",
                role_review_threads(author="reviewer-agent"),
            )

            result, output = run_cli(
                tmp,
                "role-readiness",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--role-policy-file",
                str(policy_path),
                "--pr-json-file",
                str(pr_path),
                "--review-threads-file",
                str(review_threads_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "refresh_ownership_evidence")
            self.assertIn("duplicate_active_ownership", {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_blocks_pr_and_review_evidence_refresh_inputs(self):
        cases = [
            ("missing-pr", "missing-pr", "pr_evidence_missing"),
            ("invalid-pr", "invalid-pr", "pr_evidence_invalid"),
            ("malformed-review", "malformed-review", "review_thread_evidence_invalid"),
        ]
        for name, mode, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    write_work_ownership(
                        tmp,
                        "ownership-builder",
                        branch=branch,
                        role="implementer",
                        claimer="builder-agent",
                        task_id="task-1",
                    )
                    policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
                    args = [
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--role-policy-file",
                        str(policy_path),
                    ]
                    if mode != "missing-pr":
                        if mode == "invalid-pr":
                            pr_path = Path(tmp) / "pr.json"
                            pr_path.write_text("{}", encoding="utf-8")
                        else:
                            pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
                        args.extend(["--pr-json-file", str(pr_path)])

                    review_threads_path = Path(tmp) / "review-threads.json"
                    if mode == "malformed-review":
                        review_threads_path.write_text(json.dumps({"data": {}}), encoding="utf-8")
                    else:
                        write_role_review_threads(
                            review_threads_path,
                            role_review_threads(author="reviewer-agent"),
                        )
                    args.extend(["--review-threads-file", str(review_threads_path)])

                    result, output = run_cli(tmp, *args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], "refresh_pr_evidence")
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_reports_readable_non_object_evidence_as_invalid(self):
        cases = [
            ("role-policy", "role_policy_invalid", "provide_role_policy"),
            ("pr", "pr_evidence_invalid", "refresh_pr_evidence"),
            ("review-threads", "review_thread_evidence_invalid", "refresh_pr_evidence"),
        ]
        for target, expected_code, expected_action in cases:
            with self.subTest(target=target):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    write_work_ownership(
                        tmp,
                        "ownership-builder",
                        branch=branch,
                        role="implementer",
                        claimer="builder-agent",
                        task_id="task-1",
                    )
                    policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
                    pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
                    review_threads_path, _threads = write_role_review_threads(
                        Path(tmp) / "review-threads.json",
                        role_review_threads(author="reviewer-agent"),
                    )
                    if target == "role-policy":
                        policy_path.write_text("[]", encoding="utf-8")
                    elif target == "pr":
                        pr_path.write_text("[]", encoding="utf-8")
                    else:
                        review_threads_path.write_text("[]", encoding="utf-8")

                    result, output = run_cli(
                        tmp,
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--role-policy-file",
                        str(policy_path),
                        "--pr-json-file",
                        str(pr_path),
                        "--review-threads-file",
                        str(review_threads_path),
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_blocks_mismatched_pr_repo_and_ownership_anchors(self):
        cases = [
            (
                "pr-branch",
                {"headRefName": "other-branch"},
                {},
                None,
                "pr_branch_mismatch",
                "refresh_pr_evidence",
            ),
            (
                "pr-head",
                {"headRefOid": "stale-head"},
                {},
                None,
                "pr_head_mismatch",
                "refresh_pr_evidence",
            ),
            (
                "pr-number",
                {"number": 999},
                {"pr_number": 123},
                None,
                "pr_number_mismatch",
                "refresh_pr_evidence",
            ),
            (
                "requested-branch",
                {},
                {"branch": "other-branch"},
                "other-branch",
                "repo_branch_mismatch",
                "inspect_repo_state",
            ),
            (
                "ownership-head",
                {},
                {"head": "stale-head"},
                None,
                "ownership_head_mismatch",
                "refresh_ownership_evidence",
            ),
        ]
        for name, pr_overrides, ownership_overrides, requested_branch, expected_code, expected_action in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    ownership_values = dict(ownership_overrides)
                    ownership_branch = ownership_values.pop("branch", branch)
                    write_work_ownership(
                        tmp,
                        "ownership-builder",
                        branch=ownership_branch,
                        role="implementer",
                        claimer="builder-agent",
                        task_id="task-1",
                        **ownership_values,
                    )
                    policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
                    pr_path, _pr = write_matching_role_pr_json(
                        Path(tmp) / "pr.json",
                        repo,
                        **pr_overrides,
                    )
                    review_threads_path, _threads = write_role_review_threads(
                        Path(tmp) / "review-threads.json",
                        role_review_threads(author="reviewer-agent"),
                    )
                    args = [
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--role-policy-file",
                        str(policy_path),
                        "--pr-json-file",
                        str(pr_path),
                        "--review-threads-file",
                        str(review_threads_path),
                    ]
                    if requested_branch is not None:
                        args.extend(["--branch", requested_branch])

                    result, output = run_cli(tmp, *args)

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(output["valid"])
                    self.assertEqual(output["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in output["blockers"]})

    def test_role_readiness_uses_default_ownership_freshness_window(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            branch = current_branch(repo)
            write_work_ownership(
                tmp,
                "ownership-builder",
                branch=branch,
                role="implementer",
                claimer="builder-agent",
                task_id="task-1",
                created_at="2000-01-01T00:00:00Z",
                updated_at="2000-01-01T00:00:00Z",
            )
            policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
            pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
            review_threads_path, _threads = write_role_review_threads(
                Path(tmp) / "review-threads.json",
                role_review_threads(author="reviewer-agent"),
            )

            result, output = run_cli(
                tmp,
                "role-readiness",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--task-id",
                "task-1",
                "--role-policy-file",
                str(policy_path),
                "--pr-json-file",
                str(pr_path),
                "--review-threads-file",
                str(review_threads_path),
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("ownership_stale", {blocker["code"] for blocker in output["blockers"]})
            self.assertEqual(output["scope"]["max_ownership_age_minutes"], 1440)
            self.assertEqual(output["recommended_next_action"], "refresh_ownership_evidence")

    def test_role_readiness_ignores_resolved_or_outdated_review_threads_for_separation_conflicts(self):
        cases = [
            ("resolved", role_review_threads(author="builder-agent", resolved=True)),
            ("outdated", role_review_threads(author="builder-agent", outdated=True)),
        ]
        for name, review_threads in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    branch = current_branch(repo)
                    write_work_ownership(
                        tmp,
                        "ownership-builder",
                        branch=branch,
                        role="implementer",
                        claimer="builder-agent",
                        task_id="task-1",
                    )
                    policy_path, _policy = write_role_policy(Path(tmp) / "role-policy.json")
                    pr_path, _pr = write_matching_role_pr_json(Path(tmp) / "pr.json", repo)
                    review_threads_path, _threads = write_role_review_threads(
                        Path(tmp) / "review-threads.json",
                        review_threads,
                    )

                    result, output = run_cli(
                        tmp,
                        "role-readiness",
                        "--cwd",
                        repo,
                        "--repo",
                        "local/test",
                        "--task-id",
                        "task-1",
                        "--role-policy-file",
                        str(policy_path),
                        "--pr-json-file",
                        str(pr_path),
                        "--review-threads-file",
                        str(review_threads_path),
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIsNotNone(output, result.stderr)
                    codes = {blocker["code"] for blocker in output["blockers"]}
                    self.assertIn("reviewer_evidence_missing", codes)
                    self.assertNotIn("review_separation_conflict", codes)
                    self.assertEqual(output["review_evidence"]["actionable_review_authors"], [])
                    self.assertEqual(output["recommended_next_action"], "provide_reviewer_evidence")


if __name__ == "__main__":
    unittest.main()
