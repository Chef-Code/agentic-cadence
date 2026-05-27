#!/usr/bin/env python3
"""Generic shell host-binding stub example.

This example simulates a thin host binding without integrating with a real
Claude, Gemini, or other agent host. It maps simple host-event JSON files into
the adapter template's host-signal shape, then invokes the copyable adapter
template through subprocess and public CLI behavior only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
ADAPTER_TEMPLATE_DISPLAY = "examples/adapter-template/adapter.py"
ADAPTER_TEMPLATE = ROOT / "examples" / "adapter-template" / "adapter.py"
MAPPING_DOC_DISPLAY = "examples/adapter-template/host-binding-mapping.md"
MAPPING_DOC = ROOT / "examples" / "adapter-template" / "host-binding-mapping.md"
HOST_EVENT_DIR = SCRIPT_DIR / "host-events"
DEFAULT_WORK_DIR = SCRIPT_DIR / "work"
WORK_DIR_MARKER = ".generic-shell-host-binding-work"
DEFAULT_CADENCE_COMMAND = "agentic-cadence"
SCENARIOS = (
    ("no-event.json", None),
    ("context-pressure.json", "context"),
    ("operator-stop.json", "operator_stop"),
)
ALLOWED_EVENTS = {"context_pressure", "operator_stop"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_TASK_TYPES = {"execution", "discovery"}
ALLOWED_DRIVERS = {
    "reviewer_feedback",
    "ci_verification",
    "external_review",
    "multiple_files",
    "unknown_repo_area",
    "unclear_requirements",
    "cross_subsystem",
    "migration",
    "irreversible_operation",
    "self_evolution",
}
MAX_SOURCE_LENGTH = 64


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    expect: int = 0,
    timeout_seconds: float = 120.0,
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
        raise RuntimeError(f"command timed out after {timeout_seconds}s: {' '.join(command)}") from exc
    if result.returncode != expect:
        raise RuntimeError(
            f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def cadence_command_value(cadence_python: str | None, cadence_command: str | None) -> str:
    if cadence_python:
        return f'"{cadence_python}" -m codex_cadence'
    if cadence_command:
        return cadence_command
    env_python = os.environ.get("AGENTIC_CADENCE_PYTHON")
    if env_python:
        return f'"{env_python}" -m codex_cadence'
    return DEFAULT_CADENCE_COMMAND


def prepare_work_dir(path: Path, *, replace_existing: bool) -> None:
    path = path.resolve()
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"work directory path is not a directory: {path}")
        if not replace_existing:
            raise RuntimeError(f"work directory already exists: {path}; pass --replace-existing to reuse it")
        ensure_safe_replacement_target(path)
        shutil.rmtree(path, onerror=remove_readonly)
    path.mkdir(parents=True)
    (path / WORK_DIR_MARKER).write_text("Disposable generic shell host-binding work directory.\n", encoding="utf-8")


def ensure_safe_replacement_target(path: Path) -> None:
    default_work_dir = DEFAULT_WORK_DIR.resolve()
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


def remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    func(path)


def init_target_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=path)
    no_hooks = path / ".git" / "no-hooks"
    no_hooks.mkdir(parents=True, exist_ok=True)
    run(["git", "config", "user.email", "generic-shell-host-binding@example.com"], cwd=path)
    run(["git", "config", "user.name", "Generic Shell Host Binding"], cwd=path)
    run(["git", "config", "commit.gpgSign", "false"], cwd=path)
    run(["git", "config", "tag.gpgSign", "false"], cwd=path)
    run(["git", "config", "core.hooksPath", str(no_hooks)], cwd=path)
    (path / "README.md").write_text("Generic shell host-binding target repository.\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=path)
    run(["git", "commit", "--no-gpg-sign", "--no-verify", "-m", "initial generic shell host target"], cwd=path)


def load_host_event(event_name: str) -> Any:
    return json.loads((HOST_EVENT_DIR / event_name).read_text(encoding="utf-8"))


def map_host_event_to_signal(event_payload: Any) -> dict[str, Any] | None:
    if event_payload is None:
        return None
    if not isinstance(event_payload, dict):
        raise RuntimeError("host event must be a JSON object or null")

    event = event_payload.get("event")
    if event not in ALLOWED_EVENTS:
        raise RuntimeError(f"unsupported host event: {event!r}")

    confidence = event_payload.get("confidence")
    if confidence not in ALLOWED_CONFIDENCE:
        raise RuntimeError(f"unsupported host event confidence: {confidence!r}")

    task_type = event_payload.get("task_type")
    if task_type not in ALLOWED_TASK_TYPES:
        raise RuntimeError(f"unsupported host event task_type: {task_type!r}")

    drivers = validate_host_event_drivers(event_payload.get("drivers"))

    for field in ("source", "summary", "next_action"):
        if not isinstance(event_payload.get(field), str) or not event_payload[field].strip():
            raise RuntimeError(f"host event field {field!r} must be a non-empty string")

    if len(event_payload["source"]) > MAX_SOURCE_LENGTH:
        raise RuntimeError(f"host event source must be {MAX_SOURCE_LENGTH} characters or fewer")

    return {
        "kind": event,
        "source": event_payload["source"],
        "confidence": confidence,
        "summary": event_payload["summary"],
        "task_type": task_type,
        "drivers": drivers,
        "next_action": event_payload["next_action"],
    }


def validate_host_event_drivers(drivers: Any) -> list[str]:
    if not isinstance(drivers, list):
        raise RuntimeError("host event drivers must be a JSON array")
    selected = list(drivers)
    for driver in selected:
        if not isinstance(driver, str) or not driver.strip():
            raise RuntimeError("host event drivers must be non-empty strings")
        if driver not in ALLOWED_DRIVERS:
            raise RuntimeError(f"unsupported host event driver: {driver}")
    return selected


def run_adapter_for_event(
    *,
    event_name: str,
    expected_guardrail: str | None,
    work_dir: Path,
    cadence_command: str,
) -> dict[str, Any]:
    scenario_slug = event_name.removesuffix(".json")
    scenario_dir = work_dir / scenario_slug
    runtime_root = scenario_dir / "runtime"
    target_repo = scenario_dir / "repo"
    generated_signal = scenario_dir / "host-signal.json"
    init_target_repo(target_repo)

    host_event = load_host_event(event_name)
    signal_payload = map_host_event_to_signal(host_event)
    generated_signal.parent.mkdir(parents=True, exist_ok=True)
    generated_signal.write_text(json.dumps(signal_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = run(
        [
            sys.executable,
            str(ADAPTER_TEMPLATE),
            "--runtime-root",
            str(runtime_root),
            "--repo",
            "local/generic-shell-host-binding",
            "--cwd",
            str(target_repo),
            "--handoff-id",
            f"generic-shell-{scenario_slug}",
            "--title",
            f"Generic shell host binding: {scenario_slug}",
            "--summary",
            "Fallback summary when no generic shell host event is supplied.",
            "--task-type",
            "execution",
            "--driver",
            "reviewer_feedback",
            "--next-action",
            "Fallback next action when no generic shell host event is supplied.",
            "--host-signal-file",
            str(generated_signal),
            "--cadence-command",
            cadence_command,
        ],
    )
    adapter_output = json.loads(result.stdout)
    packets = adapter_output.get("packets", {})

    observed_guardrail = None
    observed_summary = None
    observed_task_type = None
    observed_drivers = None
    observed_next_action = None
    if expected_guardrail is not None:
        if not isinstance(signal_payload, dict):
            raise RuntimeError(f"{event_name} must map to a signal payload")
        prepare_packet = packets.get("prepare_handoff")
        if not isinstance(prepare_packet, dict):
            raise RuntimeError(f"{event_name} did not preserve prepare_handoff packet")
        handoff = prepare_packet.get("handoff")
        if not isinstance(handoff, dict):
            raise RuntimeError(f"{event_name} prepare_handoff packet is missing handoff")
        clean_square = prepare_packet.get("clean_square")
        estimate_input = handoff.get("estimate_input")
        message = handoff.get("message")
        observed_guardrail = handoff.get("guardrail")
        observed_summary = clean_square.get("summary") if isinstance(clean_square, dict) else None
        observed_task_type = estimate_input.get("task_type") if isinstance(estimate_input, dict) else None
        observed_drivers = estimate_input.get("drivers") if isinstance(estimate_input, dict) else None
        observed_next_action = (
            signal_payload["next_action"] if isinstance(message, str) and signal_payload["next_action"] in message else None
        )
        expected_mappings = {
            "guardrail": (observed_guardrail, expected_guardrail),
            "summary": (observed_summary, signal_payload["summary"]),
            "task_type": (observed_task_type, signal_payload["task_type"]),
            "drivers": (observed_drivers, signal_payload["drivers"]),
            "next_action": (observed_next_action, signal_payload["next_action"]),
        }
        for field, (observed, expected) in expected_mappings.items():
            if observed != expected:
                raise RuntimeError(f"{event_name} mapped {field} to {observed!r}, expected {expected!r}")
        if not adapter_output.get("stop_current_session"):
            raise RuntimeError(f"{event_name} did not surface stop_current_session")
    elif signal_payload is not None or adapter_output.get("result") != "no_handoff_needed" or packets:
        raise RuntimeError(f"{event_name} should not call Cadence or prepare a handoff")

    return {
        "host_event_file": event_name,
        "host_event": host_event.get("event") if isinstance(host_event, dict) else None,
        "mapped_signal_kind": signal_payload.get("kind") if isinstance(signal_payload, dict) else None,
        "adapter_result": adapter_output.get("result"),
        "cadence_called": bool(packets),
        "observed_guardrail": observed_guardrail,
        "observed_summary": observed_summary,
        "observed_task_type": observed_task_type,
        "observed_drivers": observed_drivers,
        "observed_next_action": observed_next_action,
        "stop_current_session": bool(adapter_output.get("stop_current_session")),
        "packets": packets,
    }


def run_stub(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = (args.work_dir or DEFAULT_WORK_DIR).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    command_value = cadence_command_value(args.cadence_python, args.cadence_command)

    scenarios = [
        run_adapter_for_event(
            event_name=event_name,
            expected_guardrail=expected_guardrail,
            work_dir=work_dir,
            cadence_command=command_value,
        )
        for event_name, expected_guardrail in SCENARIOS
    ]

    return {
        "result": "generic_shell_host_binding_stub_passed",
        "work_dir": str(work_dir),
        "host_event_dir": str(HOST_EVENT_DIR),
        "adapter_template_path": ADAPTER_TEMPLATE_DISPLAY,
        "adapter_template": str(ADAPTER_TEMPLATE),
        "mapping_doc_path": MAPPING_DOC_DISPLAY,
        "mapping_doc": str(MAPPING_DOC),
        "host_binding_note": (
            "This is a runnable generic shell host-binding pattern, not a real host adapter "
            "for Claude, Gemini, or any other coding-agent host."
        ),
        "scenarios": scenarios,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the generic shell host-binding stub example.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory. Refuses existing custom paths by default.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    parser.add_argument("--cadence-command", help="Installed Cadence command to invoke.")
    parser.add_argument(
        "--cadence-python",
        help=(
            "Run Cadence as '<python> -m codex_cadence'. When omitted, AGENTIC_CADENCE_PYTHON is used "
            "only if --cadence-command is also omitted."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_stub(args)
    except Exception as exc:
        print(f"generic shell host-binding stub failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
