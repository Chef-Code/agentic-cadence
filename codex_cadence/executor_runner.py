from __future__ import annotations

import json
import os
import secrets
import shutil
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.executor_contract import (
    EXECUTOR_RESULT_SCHEMA_VERSION,
    build_execution_run_record,
    checksum_json,
    validate_executor_command,
    validate_executor_result_evidence,
    validate_executor_task_packet,
)
from codex_cadence.policy_audit import (
    append_audit_record,
    execution_run_record_audit_record,
    executor_fixture_invocation_audit_record,
    executor_result_validation_audit_record,
)
from codex_cadence.repo_state import runtime_root_safety_issue
from codex_cadence.store import atomic_write_json, execution_run_path, read_brake, read_json, utc_now

CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION = "controlled-executor-fixture-run.v1"


class CommandTemplateError(ValueError):
    """Raised when the fixture command template cannot be formatted safely."""


def _failure_payload(
    *,
    reason: str,
    task_file: Path,
    result_file: Path | None,
    recommended_next_action: str,
    command: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": CONTROLLED_EXECUTOR_FIXTURE_SCHEMA_VERSION,
        "packet": "controlled_executor_fixture_run",
        "valid": False,
        "reason": reason,
        "task_file": str(task_file),
        "result_file": str(result_file) if result_file is not None else None,
        "executor_started": False,
        "pr_action_started": False,
        "timed_out": False,
        "recommended_next_action": recommended_next_action,
    }
    if command is not None:
        payload["command"] = command
    return payload


def _cadence_state(brake: dict[str, Any]) -> str:
    status = brake.get("status")
    if status == "DRIVE":
        return "PLAY_ON"
    if status == "NEUTRAL":
        return "HUDDLE"
    return "TIMEOUT"


def _generated_record_id(prefix: str) -> str:
    stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{prefix}-{stamp}-{secrets.token_hex(4)}"


def _format_fixture_command(command_template: str, *, task_file: Path, result_file: Path, repo_path: Path) -> str:
    try:
        template_tokens = shlex.split(command_template, posix=True)
        formatted_tokens = [
            token.format(
                task_file=str(task_file),
                result_file=str(result_file),
                repo_path=str(repo_path),
            )
            for token in template_tokens
        ]
        return shlex.join(formatted_tokens)
    except (AttributeError, KeyError, IndexError, ValueError) as exc:
        raise CommandTemplateError(f"invalid executor command template: {exc}") from exc


def _controlled_fixture_script() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "controlled-executor-fixture" / "run.py"


def _path_inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _normalized_local_path(value: Path) -> str:
    return os.path.normcase(str(value.expanduser().resolve(strict=False)))


def _trusted_python_executable(value: str) -> bool:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        candidate = raw
    else:
        if raw.name != value:
            return False
        resolved = shutil.which(value)
        if resolved is None:
            return False
        candidate = Path(resolved)
    return _normalized_local_path(candidate) == _normalized_local_path(Path(sys.executable))


def _fixture_argv(command: str) -> list[str]:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise CommandTemplateError(f"invalid executor command template: {exc}") from exc
    expected_script = _controlled_fixture_script().resolve(strict=False)
    if len(argv) < 2:
        raise CommandTemplateError("executor command template must invoke the controlled fixture script")
    if not _trusted_python_executable(argv[0]):
        raise CommandTemplateError("executor command template must use the current Python interpreter")
    script_path = Path(argv[1]).expanduser()
    if not script_path.is_absolute() or script_path.resolve(strict=False) != expected_script:
        raise CommandTemplateError("executor command template must invoke the controlled fixture script")
    argv[0] = str(Path(sys.executable).expanduser().resolve(strict=False))
    return argv


def _fixture_declared_commands(argv: list[str]) -> list[str]:
    commands: list[str] = []
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--command" and index + 1 < len(argv):
            commands.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("--command="):
            command = token.removeprefix("--command=")
            if command:
                commands.append(command)
        index += 1
    return commands


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
        return _failure_payload(
            reason=f"invalid executor task packet: {task_reason}",
            task_file=task_path,
            result_file=None,
            recommended_next_action="fix_executor_task_packet",
        )
    repo_path = Path(task_packet["repo"]["path"]).expanduser().resolve(strict=False)
    result_file = Path(task_packet["expected_output"]["evidence_path"]).expanduser().resolve(strict=False)
    if not repo_path.exists() or not repo_path.is_dir():
        return _failure_payload(
            reason="executor task repo.path must exist and be a directory",
            task_file=task_path,
            result_file=result_file,
            recommended_next_action="fix_executor_task_packet",
        )
    if not allow_repo_local_root:
        issue = runtime_root_safety_issue(root, repo_path)
        if issue:
            raise ValueError(issue)
    root_path = Path(root).expanduser().resolve(strict=False)
    if not _path_inside(root_path, result_file):
        return _failure_payload(
            reason="executor result evidence path must stay inside the runtime root",
            task_file=task_path,
            result_file=result_file,
            recommended_next_action="fix_executor_task_packet",
        )
    try:
        command = _format_fixture_command(
            command_template,
            task_file=task_path,
            result_file=result_file,
            repo_path=repo_path,
        )
    except CommandTemplateError as exc:
        return _failure_payload(
            reason=str(exc),
            task_file=task_path,
            result_file=result_file,
            recommended_next_action="fix_executor_command_template",
        )
    try:
        argv = _fixture_argv(command)
    except CommandTemplateError as exc:
        return _failure_payload(
            reason=str(exc),
            task_file=task_path,
            result_file=result_file,
            recommended_next_action="fix_executor_command_template",
            command=command,
        )
    for declared_command in _fixture_declared_commands(argv):
        valid_command, command_reason = validate_executor_command(declared_command, task_packet)
        if not valid_command:
            return _failure_payload(
                reason=command_reason,
                task_file=task_path,
                result_file=result_file,
                recommended_next_action="fix_executor_command_policy",
                command=command,
            )
    if result_file.exists():
        return _failure_payload(
            reason="executor result evidence file already exists",
            task_file=task_path,
            result_file=result_file,
            recommended_next_action="remove_stale_executor_evidence",
            command=command,
        )

    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(json.dumps(task_packet, indent=2, sort_keys=True), encoding="utf-8")
    result_file.parent.mkdir(parents=True, exist_ok=True)

    invocation_id = _generated_record_id("executor-fixture-invocation")
    run_id = _generated_record_id("execution-run")
    invocation_payload = {
        "protocol_version": PROTOCOL_VERSION,
        "packet": "controlled_executor_fixture_invocation",
        "invocation_id": invocation_id,
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
    observed_started = time.monotonic()
    effective_timeout_seconds = min(timeout_seconds, task_packet["limits"]["max_minutes"] * 60)
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_path,
            shell=False,
            text=True,
            capture_output=True,
            timeout=effective_timeout_seconds,
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
    observed_elapsed_seconds = time.monotonic() - observed_started

    result_read_failed = False
    if result_file.exists():
        try:
            result_evidence = read_json(result_file)
        except (json.JSONDecodeError, OSError, ValueError):
            result_evidence = {}
            valid = False
            reason = "executor result evidence file was not written"
            recommended_next_action = "fix_executor_evidence"
            active_stop = None
            result_read_failed = True
    else:
        result_evidence = {}
        valid = False
        reason = "executor result evidence file was not written"
        recommended_next_action = "fix_executor_evidence"
        active_stop = None
    if result_file.exists() and not result_read_failed:
        valid, reason, recommended_next_action, active_stop = _validate_fixture_result(
            root=root,
            task_file=task_path,
            result_file=result_file,
            task_packet=task_packet,
            result_evidence=result_evidence,
        )
        result_status = result_evidence.get("status") if isinstance(result_evidence, dict) else None
        if valid and result_status == "succeeded" and command_exit_code != 0:
            valid = False
            reason = "executor fixture command exit code must be 0 when result status is succeeded"
            recommended_next_action = "fix_executor_evidence"
        if valid and result_status != "stopped" and observed_elapsed_seconds > task_packet["limits"]["max_minutes"] * 60:
            valid = False
            reason = "executor fixture observed runtime exceeds task limit"
            recommended_next_action = "fix_executor_evidence"

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
    run_record = build_execution_run_record(
        run_id=run_id,
        invocation_id=invocation_id,
        task_file=task_path,
        result_file=result_file,
        task_packet=task_packet,
        result_evidence=result_evidence,
        validation_packet=validation_payload,
        closeout_status="pending",
    )
    run_record_file = execution_run_path(root, run_id)
    atomic_write_json(run_record_file, run_record)
    run_record_audit = append_audit_record(
        root,
        execution_run_record_audit_record(
            run_record,
            run_record_file=str(run_record_file),
            action="record_execution_run",
            reason="execution run record written",
        ),
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
        "run_record": {
            "path": str(run_record_file),
            "run_id": run_id,
            "invocation_id": invocation_id,
            "checksum": checksum_json(run_record),
            "closeout_status": run_record["closeout_status"],
            "audit_record": run_record_audit,
        },
        "limitations": [
            "controlled_fixture_only",
            "real_executor_invocation_blocked",
            "git_pr_automation_not_started",
        ],
    }
    if active_stop is not None:
        payload["active_stop"] = active_stop
    return payload
