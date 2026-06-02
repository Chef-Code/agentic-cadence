from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.executor_contract import (
    EXECUTOR_RESULT_SCHEMA_VERSION,
    validate_executor_command,
    validate_executor_result_evidence,
    validate_executor_task_packet,
)
from codex_cadence.policy_audit import (
    append_audit_record,
    executor_fixture_invocation_audit_record,
    executor_result_validation_audit_record,
)
from codex_cadence.repo_state import runtime_root_safety_issue
from codex_cadence.store import read_brake, read_json, utc_now

CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION = "controlled-executor-fixture-run.v1"


class CommandTemplateError(ValueError):
    """Raised when the fixture command template cannot be formatted safely."""


def _cadence_state(brake: dict[str, Any]) -> str:
    status = brake.get("status")
    if status == "DRIVE":
        return "PLAY_ON"
    if status == "NEUTRAL":
        return "HUDDLE"
    return "TIMEOUT"


def _format_fixture_command(command_template: str, *, task_file: Path, result_file: Path, repo_path: Path) -> str:
    try:
        return command_template.format(
            task_file=str(task_file),
            result_file=str(result_file),
            repo_path=str(repo_path),
        )
    except (KeyError, IndexError, ValueError) as exc:
        raise CommandTemplateError(f"invalid executor command template: {exc}") from exc


def _stopped_timeout_evidence(task_packet: dict[str, Any], command: str, *, started_at: str, ended_at: str) -> dict[str, Any]:
    return {
        "schema_version": EXECUTOR_RESULT_SCHEMA_VERSION,
        "packet": "executor_result",
        "task_id": task_packet["task"]["id"],
        "executor_id": "controlled-fixture-runner",
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "stopped",
        "files_changed": [],
        "commands_run": [
            {
                "command": command,
                "exit_code": 124,
            }
        ],
        "validation_results": [],
        "summary": "Controlled executor fixture stopped after its timeout.",
        "confidence": "low",
        "blockers": ["timeout"],
        "dirty_worktree": True,
        "resulting_head": None,
    }


def _validate_fixture_result(
    *,
    root: Path,
    task_file: Path,
    result_file: Path,
    task_packet: dict[str, Any],
    result_evidence: Any,
) -> tuple[bool, str, str, dict[str, Any] | None]:
    valid, reason = validate_executor_result_evidence(result_evidence, task_packet)
    if valid:
        expected_output = task_packet.get("expected_output") if isinstance(task_packet, dict) else {}
        expected_path = expected_output.get("evidence_path") if isinstance(expected_output, dict) else None
        if expected_path is not None and Path(expected_path).expanduser().resolve() != result_file.expanduser().resolve():
            valid = False
            reason = "executor result file does not match task expected_output.evidence_path"
    active_stop = None
    stop_conditions = task_packet.get("stop_conditions") if isinstance(task_packet, dict) else []
    result_status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
    needs_brake_check = (
        valid
        and isinstance(stop_conditions, list)
        and "brake_not_drive" in stop_conditions
        and result_status != "stopped"
    )
    if needs_brake_check:
        brake = read_brake(root)
        if brake["status"] != "DRIVE":
            valid = False
            reason = (
                f"cadence brake is {brake['status']}; "
                "executor result must report stopped before completion can be recorded"
            )
            active_stop = {
                "brake_status": brake["status"],
                "cadence": _cadence_state(brake),
                "reason": brake.get("reason"),
                "required_result_status": "stopped",
            }
    recommended_next_action = "record_executor_result" if valid else "fix_executor_evidence"
    if active_stop is not None:
        recommended_next_action = "stop_active_loop"
    return valid, reason, recommended_next_action, active_stop


def _validation_payload(
    *,
    valid: bool,
    reason: str,
    task_file: Path,
    result_file: Path,
    recommended_next_action: str,
    active_stop: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "packet": "executor_result_validation",
        "valid": valid,
        "reason": reason,
        "task_file": str(task_file),
        "result_file": str(result_file),
        "executor_started": True,
        "recommended_next_action": recommended_next_action,
    }
    if active_stop is not None:
        payload["active_stop"] = active_stop
    return payload


def run_controlled_executor_fixture(
    *,
    root: Path,
    task_file: str | Path,
    command_template: str,
    timeout_seconds: int,
    allow_repo_local_root: bool = False,
) -> dict[str, Any]:
    task_path = Path(task_file).expanduser().resolve(strict=False)
    task_packet = read_json(task_path)
    valid_task, task_reason = validate_executor_task_packet(task_packet)
    if not valid_task:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION,
            "packet": "controlled_executor_fixture_run",
            "valid": False,
            "reason": f"invalid executor task packet: {task_reason}",
            "task_file": str(task_path),
            "result_file": None,
            "executor_started": False,
            "recommended_next_action": "fix_executor_task_packet",
        }
    repo_path = Path(task_packet["repo"]["path"]).expanduser().resolve(strict=False)
    if not allow_repo_local_root:
        issue = runtime_root_safety_issue(root, repo_path)
        if issue:
            raise ValueError(issue)
    result_file = Path(task_packet["expected_output"]["evidence_path"]).expanduser().resolve(strict=False)
    try:
        command = _format_fixture_command(
            command_template,
            task_file=task_path,
            result_file=result_file,
            repo_path=repo_path,
        )
    except CommandTemplateError as exc:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION,
            "packet": "controlled_executor_fixture_run",
            "valid": False,
            "reason": str(exc),
            "task_file": str(task_path),
            "result_file": str(result_file),
            "executor_started": False,
            "pr_action_started": False,
            "timed_out": False,
            "recommended_next_action": "fix_executor_command_template",
        }
    valid_command, command_reason = validate_executor_command(command, task_packet)
    if not valid_command:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION,
            "packet": "controlled_executor_fixture_run",
            "valid": False,
            "reason": command_reason,
            "task_file": str(task_path),
            "result_file": str(result_file),
            "command": command,
            "executor_started": False,
            "pr_action_started": False,
            "timed_out": False,
            "recommended_next_action": "fix_executor_command_policy",
        }

    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(json.dumps(task_packet, indent=2, sort_keys=True), encoding="utf-8")
    result_file.parent.mkdir(parents=True, exist_ok=True)
    if result_file.exists():
        result_file.unlink()

    invocation_payload = {
        "protocol_version": PROTOCOL_VERSION,
        "packet": "controlled_executor_fixture_invocation",
        "task_file": str(task_path),
        "result_file": str(result_file),
        "command": command,
        "executor_started": True,
        "reason": "controlled executor fixture command starting",
    }
    invocation_audit = append_audit_record(
        root,
        executor_fixture_invocation_audit_record(invocation_payload, task_packet),
    )

    started_at = utc_now()
    timed_out = False
    command_exit_code: int | None = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=repo_path,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        command_exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        command_exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        result_file.write_text(
            json.dumps(
                _stopped_timeout_evidence(task_packet, command, started_at=started_at, ended_at=utc_now()),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    if result_file.exists():
        result_evidence = read_json(result_file)
    else:
        result_evidence = {}
        valid = False
        reason = "executor result evidence file was not written"
        recommended_next_action = "fix_executor_evidence"
        active_stop = None
    if result_file.exists():
        valid, reason, recommended_next_action, active_stop = _validate_fixture_result(
            root=root,
            task_file=task_path,
            result_file=result_file,
            task_packet=task_packet,
            result_evidence=result_evidence,
        )

    validation_payload = _validation_payload(
        valid=valid,
        reason=reason,
        task_file=task_path,
        result_file=result_file,
        recommended_next_action=recommended_next_action,
        active_stop=active_stop,
    )
    validation_audit = append_audit_record(
        root,
        executor_result_validation_audit_record(validation_payload, task_packet, result_evidence),
    )

    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION,
        "packet": "controlled_executor_fixture_run",
        "valid": valid,
        "reason": reason,
        "task_file": str(task_path),
        "result_file": str(result_file),
        "command": command,
        "command_exit_code": command_exit_code,
        "executor_started": True,
        "pr_action_started": False,
        "timed_out": timed_out,
        "result_status": result_evidence.get("status") if isinstance(result_evidence, dict) else None,
        "recommended_next_action": recommended_next_action,
        "stdout": stdout,
        "stderr": stderr,
        "audit_record": validation_audit,
        "invocation_audit_record": invocation_audit,
        "limitations": [
            "controlled_fixture_only",
            "real_executor_invocation_blocked",
            "branch_policy_not_implemented",
            "git_pr_automation_not_started",
        ],
    }
    if active_stop is not None:
        payload["active_stop"] = active_stop
    return payload
