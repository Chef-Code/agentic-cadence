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
EXTERNAL_SCENARIO_SLUG = "external-host-event"
SCENARIOS = (
    ("no-event.json", None),
    ("context-pressure.json", "context"),
    ("operator-stop.json", "operator_stop"),
)
ALLOWED_EVENTS = {"context_pressure", "operator_stop"}
EVENT_GUARDRAILS = {
    "context_pressure": "context",
    "operator_stop": "operator_stop",
}
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
    input_text: str | None = None,
    timeout_seconds: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            input=input_text,
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


def load_host_event_file(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RuntimeError(f"host event file could not be read: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"host event file is not valid JSON: {path}") from exc


def load_host_event_stdin() -> Any:
    try:
        text = sys.stdin.buffer.read().decode("utf-8-sig")
    except OSError as exc:
        raise RuntimeError("host event stdin could not be read") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("host event stdin is not valid UTF-8") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("host event stdin is not valid JSON") from exc


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
    return run_adapter_for_payload(
        host_event=load_host_event(event_name),
        event_label=event_name,
        scenario_slug=scenario_slug,
        expected_guardrail=expected_guardrail,
        work_dir=work_dir,
        cadence_command=cadence_command,
    )


def run_adapter_for_payload(
    *,
    host_event: Any,
    event_label: str,
    scenario_slug: str,
    expected_guardrail: str | None,
    work_dir: Path,
    cadence_command: str,
) -> dict[str, Any]:
    scenario_dir = work_dir / scenario_slug
    runtime_root = scenario_dir / "runtime"
    target_repo = scenario_dir / "repo"
    generated_signal = scenario_dir / "host-signal.json"
    init_target_repo(target_repo)

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
            raise RuntimeError(f"{event_label} must map to a signal payload")
        prepare_packet = packets.get("prepare_handoff")
        if not isinstance(prepare_packet, dict):
            raise RuntimeError(f"{event_label} did not preserve prepare_handoff packet")
        handoff = prepare_packet.get("handoff")
        if not isinstance(handoff, dict):
            raise RuntimeError(f"{event_label} prepare_handoff packet is missing handoff")
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
                raise RuntimeError(f"{event_label} mapped {field} to {observed!r}, expected {expected!r}")
        if not adapter_output.get("stop_current_session"):
            raise RuntimeError(f"{event_label} did not surface stop_current_session")
    elif signal_payload is not None or adapter_output.get("result") != "no_handoff_needed" or packets:
        raise RuntimeError(f"{event_label} should not call Cadence or prepare a handoff")

    return {
        "host_event_file": event_label,
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


def run_external_event(
    args: argparse.Namespace,
    *,
    host_event: Any,
    event_label: str,
    host_event_source: str,
    host_event_file: Path | None,
) -> dict[str, Any]:
    signal_payload = map_host_event_to_signal(host_event)
    expected_guardrail = (
        EVENT_GUARDRAILS[signal_payload["kind"]]
        if isinstance(signal_payload, dict)
        else None
    )

    work_dir = (args.work_dir or DEFAULT_WORK_DIR).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    command_value = cadence_command_value(args.cadence_python, args.cadence_command)
    scenario = run_adapter_for_payload(
        host_event=host_event,
        event_label=event_label,
        scenario_slug=EXTERNAL_SCENARIO_SLUG,
        expected_guardrail=expected_guardrail,
        work_dir=work_dir,
        cadence_command=command_value,
    )

    return {
        "result": "generic_shell_host_binding_event_passed",
        "work_dir": str(work_dir),
        "host_event_source": host_event_source,
        "host_event_file": str(host_event_file) if host_event_file is not None else None,
        "adapter_template_path": ADAPTER_TEMPLATE_DISPLAY,
        "adapter_template": str(ADAPTER_TEMPLATE),
        "mapping_doc_path": MAPPING_DOC_DISPLAY,
        "mapping_doc": str(MAPPING_DOC),
        "host_binding_note": (
            "This generic shell host binding consumes one external host-event JSON payload "
            "and maps it through the adapter template and public CLI."
        ),
        "scenario": scenario,
    }


def run_event_file(args: argparse.Namespace) -> dict[str, Any]:
    host_event_file = args.host_event_file.resolve()
    return run_external_event(
        args,
        host_event=load_host_event_file(host_event_file),
        event_label=str(host_event_file),
        host_event_source="file",
        host_event_file=host_event_file,
    )


def run_event_stdin(args: argparse.Namespace) -> dict[str, Any]:
    return run_external_event(
        args,
        host_event=load_host_event_stdin(),
        event_label="<stdin>",
        host_event_source="stdin",
        host_event_file=None,
    )


def replay_cadence_args(args: argparse.Namespace) -> list[str]:
    if args.cadence_python:
        return ["--cadence-python", args.cadence_python]
    if args.cadence_command:
        return ["--cadence-command", args.cadence_command]
    return []


def run_shell_binding_child(
    child_args: list[str],
    *,
    cadence_args: list[str],
    input_text: str | None = None,
) -> dict[str, Any]:
    result = run(
        [sys.executable, str(SCRIPT_DIR / "run.py"), *child_args, *cadence_args],
        input_text=input_text,
        timeout_seconds=240.0,
    )
    return json.loads(result.stdout)


def require_replay_boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{field} must be a JSON boolean")
    return value


def normalized_replay_behavior(scenario: dict[str, Any]) -> dict[str, Any]:
    packets = scenario.get("packets")
    if not isinstance(packets, dict):
        packets = {}

    prepare_packet = packets.get("prepare_handoff")
    handoff = prepare_packet.get("handoff") if isinstance(prepare_packet, dict) else None
    prepare_stop_current_session = (
        require_replay_boolean(prepare_packet.get("stop_current_session"), "prepare_handoff.stop_current_session")
        if isinstance(prepare_packet, dict)
        else None
    )

    return {
        "host_event": scenario.get("host_event"),
        "mapped_signal_kind": scenario.get("mapped_signal_kind"),
        "adapter_result": scenario.get("adapter_result"),
        "cadence_called": require_replay_boolean(scenario.get("cadence_called"), "cadence_called"),
        "observed_guardrail": scenario.get("observed_guardrail"),
        "observed_summary": scenario.get("observed_summary"),
        "observed_task_type": scenario.get("observed_task_type"),
        "observed_drivers": scenario.get("observed_drivers"),
        "observed_next_action": scenario.get("observed_next_action"),
        "stop_current_session": require_replay_boolean(scenario.get("stop_current_session"), "stop_current_session"),
        "packet_keys": sorted(packets),
        "prepared_handoff_status": handoff.get("status") if isinstance(handoff, dict) else None,
        "prepare_stop_current_session": prepare_stop_current_session,
    }


def bundled_fixture_scenario(summary: dict[str, Any], event_name: str) -> dict[str, Any]:
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, list):
        raise RuntimeError("bundled fixture replay did not return scenarios")
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get("host_event_file") == event_name:
            return scenario
    raise RuntimeError(f"bundled fixture replay did not return {event_name}")


def single_event_scenario(summary: dict[str, Any], label: str) -> dict[str, Any]:
    scenario = summary.get("scenario")
    if not isinstance(scenario, dict):
        raise RuntimeError(f"{label} replay did not return one scenario")
    return scenario


def replay_contract_case(
    *,
    event_name: str,
    work_dir: Path,
    cadence_args: list[str],
    bundled_summary: dict[str, Any],
) -> dict[str, Any]:
    scenario_slug = event_name.removesuffix(".json")
    event_path = HOST_EVENT_DIR / event_name
    fixture_text = event_path.read_text(encoding="utf-8")

    path_results = {
        "bundled_fixture": normalized_replay_behavior(bundled_fixture_scenario(bundled_summary, event_name)),
        "host_event_file": normalized_replay_behavior(
            single_event_scenario(
                run_shell_binding_child(
                    [
                        "--host-event-file",
                        str(event_path),
                        "--work-dir",
                        str(work_dir / "host-event-file" / scenario_slug),
                    ],
                    cadence_args=cadence_args,
                ),
                f"{event_name} file-backed",
            )
        ),
        "host_event_stdin": normalized_replay_behavior(
            single_event_scenario(
                run_shell_binding_child(
                    [
                        "--host-event-stdin",
                        "--work-dir",
                        str(work_dir / "host-event-stdin" / scenario_slug),
                    ],
                    cadence_args=cadence_args,
                    input_text=fixture_text,
                ),
                f"{event_name} stdin-backed",
            )
        ),
    }

    input_paths = ["bundled_fixture", "host_event_file", "host_event_stdin"]
    normalized_behavior = path_results["bundled_fixture"]
    divergent_paths = {
        label: path_results[label]
        for label in input_paths
        if path_results[label] != normalized_behavior
    }
    if divergent_paths:
        raise RuntimeError(
            f"{event_name} host-event behavior diverged across replay paths: "
            f"{json.dumps({'baseline': normalized_behavior, 'all_path_results': path_results}, indent=2, sort_keys=True)}"
        )

    return {
        "host_event_file": event_name,
        "consistent": True,
        "input_paths": input_paths,
        "normalized_behavior": normalized_behavior,
        "path_results": path_results,
    }


def run_replay_contract(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = (args.work_dir or DEFAULT_WORK_DIR).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    cadence_args = replay_cadence_args(args)

    bundled_summary = run_shell_binding_child(
        ["--work-dir", str(work_dir / "bundled-fixtures")],
        cadence_args=cadence_args,
    )
    contract_cases = [
        replay_contract_case(
            event_name=event_name,
            work_dir=work_dir,
            cadence_args=cadence_args,
            bundled_summary=bundled_summary,
        )
        for event_name, _expected_guardrail in SCENARIOS
    ]

    return {
        "result": "generic_shell_host_binding_replay_contract_passed",
        "work_dir": str(work_dir),
        "host_event_dir": str(HOST_EVENT_DIR),
        "adapter_template_path": ADAPTER_TEMPLATE_DISPLAY,
        "adapter_template": str(ADAPTER_TEMPLATE),
        "mapping_doc_path": MAPPING_DOC_DISPLAY,
        "mapping_doc": str(MAPPING_DOC),
        "host_binding_note": (
            "This generic shell host-binding replay contract is not a real host adapter "
            "for Claude, Gemini, or any other coding-agent host."
        ),
        "contract_note": (
            "This replay contract compares fixture, file-backed, and stdin-backed generic shell "
            "host-event paths through the copyable adapter template and public CLI."
        ),
        "contract_cases": contract_cases,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the generic shell host-binding stub example.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory. Refuses existing custom paths by default.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    host_event_group = parser.add_mutually_exclusive_group()
    host_event_group.add_argument("--host-event-file", type=Path, help="External host-event JSON file to process once.")
    host_event_group.add_argument("--host-event-stdin", action="store_true", help="Read one external host-event JSON payload from stdin.")
    host_event_group.add_argument(
        "--replay-contract",
        action="store_true",
        help="Compare bundled fixture, file-backed, and stdin-backed behavior for the same host events.",
    )
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
        if args.replay_contract:
            summary = run_replay_contract(args)
        elif args.host_event_file:
            summary = run_event_file(args)
        elif args.host_event_stdin:
            summary = run_event_stdin(args)
        else:
            summary = run_stub(args)
    except Exception as exc:
        print(f"generic shell host-binding stub failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
