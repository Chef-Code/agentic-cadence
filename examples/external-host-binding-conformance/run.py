#!/usr/bin/env python3
"""Generic external host-binding conformance harness.

This harness checks an external host-binding command against the generic shell
host-event replay contract. It is not a real host adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_SCRIPT = ROOT / "examples" / "adapter-template" / "host_signal_contract.py"
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"
SHELL_BINDING_DISPLAY = "examples/generic-shell-host-binding/run.py"
HOST_EVENT_DIR = ROOT / "examples" / "generic-shell-host-binding" / "host-events"
DEFAULT_WORK_DIR = SCRIPT_DIR / "work"
WORK_DIR_MARKER = ".external-host-binding-conformance-work"
WINDOWS_JUNCTION_REPARSE_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
SCENARIOS = ("no-event.json", "context-pressure.json", "operator-stop.json")


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout_seconds: float = 360.0,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout_seconds}s: {shlex.join(command)}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed with {result.returncode}: {shlex.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def run_json(command: list[str], *, timeout_seconds: float = 360.0) -> dict[str, Any]:
    result = run(command, timeout_seconds=timeout_seconds)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"command did not emit JSON: {shlex.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"command emitted JSON {type(payload).__name__}, expected object: {shlex.join(command)}")
    return payload


def prepare_work_dir(path: Path, *, replace_existing: bool) -> None:
    unresolved_path = absolute_unresolved_path(path)
    reject_symlink_path_components(unresolved_path)
    path = unresolved_path.resolve(strict=False)
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"work directory path is not a directory: {path}")
        if not replace_existing:
            raise RuntimeError(f"work directory already exists: {path}; pass --replace-existing to reuse it")
        ensure_safe_replacement_target(path)
        shutil.rmtree(path, onerror=remove_readonly)
    path.mkdir(parents=True)
    (path / WORK_DIR_MARKER).write_text("Disposable external host-binding conformance work directory.\n", encoding="utf-8")


def ensure_safe_replacement_target(path: Path) -> None:
    path = path.resolve(strict=False)
    default_work_dir = DEFAULT_WORK_DIR.resolve(strict=False)
    if path == default_work_dir or path.is_relative_to(default_work_dir):
        return

    blocked_paths = {
        Path(path.anchor).resolve(),
        Path.home().resolve(),
        ROOT.resolve(),
        SCRIPT_DIR.resolve(),
    }
    if path in blocked_paths:
        raise RuntimeError(f"refusing to remove unsafe work directory target: {path}")

    marker = path / WORK_DIR_MARKER
    if not marker.is_file():
        raise RuntimeError(f"refusing to remove custom work directory without {WORK_DIR_MARKER} marker: {path}")


def absolute_unresolved_path(path: Path) -> Path:
    path = path.expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def reject_symlink_path_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            redirect_kind = redirecting_path_kind(current)
        except OSError as exc:
            raise RuntimeError(f"could not inspect work directory path component {current}: {exc}") from exc
        if redirect_kind:
            if current == path:
                raise RuntimeError(f"refusing to use {redirect_kind} work directory: {current}")
            raise RuntimeError(f"refusing to use work directory through {redirect_kind} path component: {current}")


def redirecting_path_kind(path: Path) -> str | None:
    if path.is_symlink():
        return "symlink"
    if path_is_junction(path):
        return "junction"
    return None


def path_is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return is_junction()
    try:
        path_stat = path.stat(follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return getattr(path_stat, "st_reparse_tag", None) == WINDOWS_JUNCTION_REPARSE_TAG


def remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    func(path)


def portable_path(path: Path | str) -> str:
    return Path(path).as_posix()


def cadence_args(args: argparse.Namespace) -> list[str]:
    if args.cadence_python:
        return ["--cadence-python", args.cadence_python]
    if args.cadence_command:
        return ["--cadence-command", args.cadence_command]
    return []


def portable_cadence_args(args: argparse.Namespace) -> list[str]:
    if args.cadence_python:
        return ["--cadence-python", portable_path(args.cadence_python)]
    if args.cadence_command:
        return ["--cadence-command", args.cadence_command]
    return []


def format_binding_command_template(
    template: str,
    *,
    args: argparse.Namespace,
    event_path: Path,
    case_work_dir: Path,
    event_name: str,
) -> list[str]:
    case_slug = event_name.removesuffix(".json")
    values = {
        "host_event_file": portable_path(event_path),
        "case_work_dir": portable_path(case_work_dir),
        "case_name": event_name,
        "case_slug": case_slug,
        "cadence_python": portable_path(args.cadence_python) if args.cadence_python else "",
        "cadence_command": args.cadence_command or "",
        "cadence_args": join_binding_command_args(portable_cadence_args(args)),
        "python": portable_path(sys.executable),
        "repo_root": portable_path(ROOT),
    }
    try:
        formatted = template.format(**values)
    except KeyError as exc:
        raise RuntimeError(f"unknown binding command template placeholder: {exc.args[0]}") from exc
    try:
        command = split_binding_command_template(formatted)
    except ValueError as exc:
        raise RuntimeError(f"binding command template could not be parsed: {exc}") from exc
    if not command:
        raise RuntimeError("binding command template produced an empty command")
    return command


def split_binding_command_template(command_line: str) -> list[str]:
    if not command_line.strip():
        return []
    if os.name == "nt":
        return split_windows_command_line(command_line)
    return shlex.split(command_line, posix=True)


def join_binding_command_args(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def split_windows_command_line(command_line: str) -> list[str]:
    import ctypes

    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    if not argv:
        raise ValueError("Windows command line parser failed")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def external_binding_command(
    args: argparse.Namespace,
    *,
    event_path: Path,
    case_work_dir: Path,
    event_name: str,
) -> list[str]:
    if args.binding_command_template:
        return format_binding_command_template(
            args.binding_command_template,
            args=args,
            event_path=event_path,
            case_work_dir=case_work_dir,
            event_name=event_name,
        )
    return [
        sys.executable,
        str(SHELL_BINDING_SCRIPT),
        "--host-event-file",
        str(event_path),
        "--work-dir",
        str(case_work_dir),
        *cadence_args(args),
    ]


def require_boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{field} must be a JSON boolean")
    return value


def normalized_scenario_behavior(
    scenario: dict[str, Any],
    *,
    expected_behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packets = scenario.get("packets", {})
    if not isinstance(packets, dict):
        raise RuntimeError("packets must be a JSON object")

    prepare_packet = packets.get("prepare_handoff")
    handoff = prepare_packet.get("handoff") if isinstance(prepare_packet, dict) else None
    clean_square = prepare_packet.get("clean_square") if isinstance(prepare_packet, dict) else None
    estimate_input = handoff.get("estimate_input") if isinstance(handoff, dict) else None
    message = handoff.get("message") if isinstance(handoff, dict) else None
    prepare_stop_current_session = (
        require_boolean(prepare_packet.get("stop_current_session"), "prepare_handoff.stop_current_session")
        if isinstance(prepare_packet, dict)
        else None
    )
    cadence_called = require_boolean(scenario.get("cadence_called"), "cadence_called")
    if cadence_called != bool(packets):
        raise RuntimeError("cadence_called must match whether packets were returned")
    stop_current_session = require_boolean(scenario.get("stop_current_session"), "stop_current_session")
    if prepare_stop_current_session is not None and stop_current_session != prepare_stop_current_session:
        raise RuntimeError("stop_current_session must match prepare_handoff.stop_current_session")

    expected_next_action = expected_behavior.get("observed_next_action") if expected_behavior else None
    observed_next_action = (
        expected_next_action
        if isinstance(message, str) and isinstance(expected_next_action, str) and expected_next_action in message
        else None
    )

    return {
        "host_event": scenario.get("host_event"),
        "mapped_signal_kind": scenario.get("mapped_signal_kind"),
        "mapped_signal_confidence": scenario.get("mapped_signal_confidence"),
        "adapter_result": scenario.get("adapter_result"),
        "cadence_called": cadence_called,
        "observed_guardrail": handoff.get("guardrail") if isinstance(handoff, dict) else None,
        "observed_summary": clean_square.get("summary") if isinstance(clean_square, dict) else None,
        "observed_task_type": estimate_input.get("task_type") if isinstance(estimate_input, dict) else None,
        "observed_drivers": estimate_input.get("drivers") if isinstance(estimate_input, dict) else None,
        "observed_next_action": observed_next_action,
        "stop_current_session": stop_current_session,
        "packet_keys": sorted(packets),
        "prepared_handoff_status": handoff.get("status") if isinstance(handoff, dict) else None,
        "prepare_stop_current_session": prepare_stop_current_session,
    }


def normalized_shell_replay_behavior(case: dict[str, Any]) -> dict[str, Any]:
    normalized = case.get("normalized_behavior")
    if not isinstance(normalized, dict):
        raise RuntimeError("generic shell replay case is missing normalized_behavior")
    return {
        "host_event": normalized.get("host_event"),
        "mapped_signal_kind": normalized.get("mapped_signal_kind"),
        "mapped_signal_confidence": normalized.get("mapped_signal_confidence"),
        "adapter_result": normalized.get("adapter_result"),
        "cadence_called": require_boolean(normalized.get("cadence_called"), "cadence_called"),
        "observed_guardrail": normalized.get("observed_guardrail"),
        "observed_summary": normalized.get("observed_summary"),
        "observed_task_type": normalized.get("observed_task_type"),
        "observed_drivers": normalized.get("observed_drivers"),
        "observed_next_action": normalized.get("observed_next_action"),
        "stop_current_session": require_boolean(normalized.get("stop_current_session"), "stop_current_session"),
        "packet_keys": normalized.get("packet_keys"),
        "prepared_handoff_status": normalized.get("prepared_handoff_status"),
        "prepare_stop_current_session": normalized.get("prepare_stop_current_session"),
    }


def shell_case_by_event(summary: dict[str, Any], event_name: str) -> dict[str, Any]:
    cases = summary.get("contract_cases")
    if not isinstance(cases, list):
        raise RuntimeError("generic shell replay contract did not return contract_cases")
    for case in cases:
        if isinstance(case, dict) and case.get("host_event_file") == event_name:
            return case
    raise RuntimeError(f"generic shell replay contract did not return {event_name}")


def extract_external_scenario(payload: dict[str, Any], event_name: str) -> dict[str, Any]:
    scenario = payload.get("scenario")
    if isinstance(scenario, dict):
        return scenario
    scenario_fields = {
        "adapter_result",
        "cadence_called",
        "observed_guardrail",
        "observed_summary",
        "observed_task_type",
        "observed_drivers",
        "observed_next_action",
        "stop_current_session",
        "packets",
    }
    if scenario_fields <= set(payload):
        return payload
    raise RuntimeError(f"{event_name} external binding output must contain a scenario object or scenario-shaped payload")


def conformance_case(
    *,
    event_name: str,
    args: argparse.Namespace,
    work_dir: Path,
    shell_replay_summary: dict[str, Any],
) -> dict[str, Any]:
    event_path = HOST_EVENT_DIR / event_name
    case_work_dir = work_dir / "external-binding" / event_name.removesuffix(".json")
    command = external_binding_command(args, event_path=event_path, case_work_dir=case_work_dir, event_name=event_name)
    external_payload = run_json(command)
    external_scenario = extract_external_scenario(external_payload, event_name)

    normalized_behavior = normalized_shell_replay_behavior(shell_case_by_event(shell_replay_summary, event_name))
    path_results = {
        "generic_shell_baseline": normalized_behavior,
        "external_binding": normalized_scenario_behavior(external_scenario, expected_behavior=normalized_behavior),
    }
    if path_results["external_binding"] != normalized_behavior:
        raise RuntimeError(
            f"{event_name} diverged from generic shell baseline: "
            f"{json.dumps({'baseline': normalized_behavior, 'external_binding': path_results['external_binding']}, indent=2, sort_keys=True)}"
        )

    return {
        "host_event_file": event_name,
        "consistent": True,
        "command": command,
        "normalized_behavior": normalized_behavior,
        "path_results": path_results,
    }


def run_conformance(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = absolute_unresolved_path(args.work_dir or DEFAULT_WORK_DIR)
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    work_dir = work_dir.resolve(strict=False)
    child_cadence_args = cadence_args(args)

    schema_summary = run_json([sys.executable, str(SCHEMA_SCRIPT)])
    shell_replay_summary = run_json(
        [
            sys.executable,
            str(SHELL_BINDING_SCRIPT),
            "--replay-contract",
            "--work-dir",
            str(work_dir / "generic-shell-baseline"),
            *child_cadence_args,
        ]
    )
    conformance_cases = [
        conformance_case(
            event_name=event_name,
            args=args,
            work_dir=work_dir,
            shell_replay_summary=shell_replay_summary,
        )
        for event_name in SCENARIOS
    ]

    return {
        "result": "external_host_binding_conformance_passed",
        "work_dir": str(work_dir),
        "host_event_dir": str(HOST_EVENT_DIR),
        "schema_contract_result": schema_summary.get("result"),
        "baseline_contract_result": shell_replay_summary.get("result"),
        "baseline_binding_path": SHELL_BINDING_DISPLAY,
        "baseline_binding": str(SHELL_BINDING_SCRIPT),
        "binding_command_mode": "template" if args.binding_command_template else "default_generic_shell",
        "binding_command_template": args.binding_command_template,
        "contract_note": (
            "This generic external host-binding conformance harness is not a real host adapter. "
            "It compares a supplied binding command against generic shell host-event replay behavior "
            "without claiming Claude, Gemini, or other host support."
        ),
        "conformance_cases": conformance_cases,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a generic external host-binding conformance harness.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory. Refuses existing custom paths by default.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    parser.add_argument(
        "--binding-command-template",
        help=(
            "External binding command template. Placeholders include {host_event_file}, {case_work_dir}, "
            "{case_name}, {case_slug}, {cadence_python}, {cadence_command}, {cadence_args}, {python}, and {repo_root}."
        ),
    )
    parser.add_argument("--cadence-command", help="Installed Cadence command to pass to child binding commands.")
    parser.add_argument(
        "--cadence-python",
        help="Run Cadence in child binding commands as '<python> -m codex_cadence'.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_conformance(args)
    except Exception as exc:
        print(f"external host-binding conformance failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
