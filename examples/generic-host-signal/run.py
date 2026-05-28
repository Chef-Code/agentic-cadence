#!/usr/bin/env python3
"""Generic host-signal smoke example.

This example exercises the copyable adapter template with host-neutral signal
fixtures. It does not implement a real Claude, Gemini, or other host adapter.
Instead, it proves that future host bindings can map host-observed signals into
the existing public CLI behavior without importing Cadence internals.
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
HOST_SIGNAL_FIXTURES = ROOT / "examples" / "adapter-template" / "host-signal-fixtures"
SHELL_BINDING_DISPLAY = "examples/generic-shell-host-binding/run.py"
SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"
SCENARIOS = (
    ("no-signal.json", None),
    ("context-pressure.json", "context"),
    ("operator-stop.json", "operator_stop"),
)
SHELL_EVENT_BY_FIXTURE = {
    "no-signal.json": "no-event.json",
    "context-pressure.json": "context-pressure.json",
    "operator-stop.json": "operator-stop.json",
}


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
    return "agentic-cadence"


def load_fixture_payload(fixture_name: str) -> Any:
    return json.loads((HOST_SIGNAL_FIXTURES / fixture_name).read_text(encoding="utf-8"))


def prepare_work_dir(path: Path, *, replace_existing: bool) -> None:
    if path.exists():
        if not replace_existing:
            raise RuntimeError(f"work directory already exists: {path}; pass --replace-existing to reuse it")
        shutil.rmtree(path, onerror=remove_readonly)
    path.mkdir(parents=True)


def remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    func(path)


def init_target_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=path)
    no_hooks = path / ".git" / "no-hooks"
    no_hooks.mkdir(parents=True, exist_ok=True)
    run(["git", "config", "user.email", "generic-host-signal@example.com"], cwd=path)
    run(["git", "config", "user.name", "Generic Host Signal"], cwd=path)
    run(["git", "config", "commit.gpgSign", "false"], cwd=path)
    run(["git", "config", "tag.gpgSign", "false"], cwd=path)
    run(["git", "config", "core.hooksPath", str(no_hooks)], cwd=path)
    (path / "README.md").write_text("Generic host-signal smoke target repository.\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=path)
    run(["git", "commit", "--no-gpg-sign", "--no-verify", "-m", "initial generic host-signal target"], cwd=path)


def run_adapter_for_fixture(
    *,
    fixture_name: str,
    expected_guardrail: str | None,
    work_dir: Path,
    cadence_command: str,
) -> dict[str, Any]:
    scenario_slug = fixture_name.removesuffix(".json")
    scenario_dir = work_dir / scenario_slug
    runtime_root = scenario_dir / "runtime"
    target_repo = scenario_dir / "repo"
    init_target_repo(target_repo)
    fixture_payload = load_fixture_payload(fixture_name)

    result = run(
        [
            sys.executable,
            str(ADAPTER_TEMPLATE),
            "--runtime-root",
            str(runtime_root),
            "--repo",
            "local/generic-host-signal-smoke",
            "--cwd",
            str(target_repo),
            "--handoff-id",
            f"generic-{scenario_slug}",
            "--title",
            f"Generic host signal: {scenario_slug}",
            "--summary",
            "Fallback summary when no host fixture is supplied.",
            "--task-type",
            "execution",
            "--driver",
            "reviewer_feedback",
            "--next-action",
            "Fallback next action when no host fixture is supplied.",
            "--host-signal-file",
            str(HOST_SIGNAL_FIXTURES / fixture_name),
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
        if not isinstance(fixture_payload, dict):
            raise RuntimeError(f"{fixture_name} must be a JSON object for signal scenarios")
        prepare_packet = packets.get("prepare_handoff")
        if not isinstance(prepare_packet, dict):
            raise RuntimeError(f"{fixture_name} did not preserve prepare_handoff packet")
        handoff = prepare_packet.get("handoff")
        if not isinstance(handoff, dict):
            raise RuntimeError(f"{fixture_name} prepare_handoff packet is missing handoff")
        clean_square = prepare_packet.get("clean_square")
        estimate_input = handoff.get("estimate_input")
        message = handoff.get("message")
        observed_guardrail = handoff.get("guardrail")
        observed_summary = clean_square.get("summary") if isinstance(clean_square, dict) else None
        observed_task_type = estimate_input.get("task_type") if isinstance(estimate_input, dict) else None
        observed_drivers = estimate_input.get("drivers") if isinstance(estimate_input, dict) else None
        observed_next_action = (
            fixture_payload["next_action"] if isinstance(message, str) and fixture_payload["next_action"] in message else None
        )
        if observed_guardrail != expected_guardrail:
            raise RuntimeError(
                f"{fixture_name} mapped to guardrail {observed_guardrail!r}, expected {expected_guardrail!r}"
            )
        expected_mappings = {
            "summary": (observed_summary, fixture_payload["summary"]),
            "task_type": (observed_task_type, fixture_payload["task_type"]),
            "drivers": (observed_drivers, fixture_payload["drivers"]),
            "next_action": (observed_next_action, fixture_payload["next_action"]),
        }
        for field, (observed, expected) in expected_mappings.items():
            if observed != expected:
                raise RuntimeError(f"{fixture_name} mapped {field} to {observed!r}, expected {expected!r}")
        if not adapter_output.get("stop_current_session"):
            raise RuntimeError(f"{fixture_name} did not surface stop_current_session")
    elif fixture_payload is not None or adapter_output.get("result") != "no_handoff_needed" or packets:
        raise RuntimeError(f"{fixture_name} should not call Cadence or prepare a handoff")

    return {
        "fixture": fixture_name,
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


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = (args.work_dir or (SCRIPT_DIR / "work")).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    command_value = cadence_command_value(args.cadence_python, args.cadence_command)

    scenarios = [
        run_adapter_for_fixture(
            fixture_name=fixture_name,
            expected_guardrail=expected_guardrail,
            work_dir=work_dir,
            cadence_command=command_value,
        )
        for fixture_name, expected_guardrail in SCENARIOS
    ]

    return {
        "result": "generic_host_signal_smoke_passed",
        "work_dir": str(work_dir),
        "adapter_template_path": ADAPTER_TEMPLATE_DISPLAY,
        "adapter_template": str(ADAPTER_TEMPLATE),
        "fixture_dir": str(HOST_SIGNAL_FIXTURES),
        "contract_note": (
            "This smoke exercises generic host-signal fixtures through the copyable adapter template. "
            "It is host-neutral and does not ship a Claude, Gemini, or other real host adapter."
        ),
        "scenarios": scenarios,
    }


def parity_cadence_args(args: argparse.Namespace) -> list[str]:
    if args.cadence_python:
        return ["--cadence-python", args.cadence_python]
    if args.cadence_command:
        return ["--cadence-command", args.cadence_command]
    return []


def run_example_child(
    script: Path,
    child_args: list[str],
    *,
    cadence_args: list[str],
) -> dict[str, Any]:
    result = run(
        [sys.executable, str(script), *child_args, *cadence_args],
        timeout_seconds=300.0,
    )
    return json.loads(result.stdout)


def require_parity_boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{field} must be a JSON boolean")
    return value


def normalized_host_signal_behavior(scenario: dict[str, Any]) -> dict[str, Any]:
    fixture_name = scenario.get("fixture")
    if not isinstance(fixture_name, str):
        raise RuntimeError("generic host-signal scenario is missing fixture")
    fixture_payload = load_fixture_payload(fixture_name)
    signal_kind = fixture_payload.get("kind") if isinstance(fixture_payload, dict) else None
    signal_confidence = fixture_payload.get("confidence") if isinstance(fixture_payload, dict) else None

    packets = scenario.get("packets")
    if not isinstance(packets, dict):
        packets = {}

    prepare_packet = packets.get("prepare_handoff")
    handoff = prepare_packet.get("handoff") if isinstance(prepare_packet, dict) else None
    prepare_stop_current_session = (
        require_parity_boolean(prepare_packet.get("stop_current_session"), "prepare_handoff.stop_current_session")
        if isinstance(prepare_packet, dict)
        else None
    )

    return {
        "signal_kind": signal_kind,
        "signal_confidence": signal_confidence,
        "adapter_result": scenario.get("adapter_result"),
        "cadence_called": require_parity_boolean(scenario.get("cadence_called"), "cadence_called"),
        "observed_guardrail": scenario.get("observed_guardrail"),
        "observed_summary": scenario.get("observed_summary"),
        "observed_task_type": scenario.get("observed_task_type"),
        "observed_drivers": scenario.get("observed_drivers"),
        "observed_next_action": scenario.get("observed_next_action"),
        "stop_current_session": require_parity_boolean(scenario.get("stop_current_session"), "stop_current_session"),
        "packet_keys": sorted(packets),
        "prepared_handoff_status": handoff.get("status") if isinstance(handoff, dict) else None,
        "prepare_stop_current_session": prepare_stop_current_session,
    }


def normalized_shell_replay_behavior(case: dict[str, Any]) -> dict[str, Any]:
    normalized = case.get("normalized_behavior")
    if not isinstance(normalized, dict):
        raise RuntimeError("generic shell replay case is missing normalized_behavior")

    return {
        "signal_kind": normalized.get("mapped_signal_kind"),
        "signal_confidence": normalized.get("mapped_signal_confidence"),
        "adapter_result": normalized.get("adapter_result"),
        "cadence_called": require_parity_boolean(normalized.get("cadence_called"), "cadence_called"),
        "observed_guardrail": normalized.get("observed_guardrail"),
        "observed_summary": normalized.get("observed_summary"),
        "observed_task_type": normalized.get("observed_task_type"),
        "observed_drivers": normalized.get("observed_drivers"),
        "observed_next_action": normalized.get("observed_next_action"),
        "stop_current_session": require_parity_boolean(normalized.get("stop_current_session"), "stop_current_session"),
        "packet_keys": normalized.get("packet_keys"),
        "prepared_handoff_status": normalized.get("prepared_handoff_status"),
        "prepare_stop_current_session": normalized.get("prepare_stop_current_session"),
    }


def scenario_by_fixture(summary: dict[str, Any], fixture_name: str) -> dict[str, Any]:
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, list):
        raise RuntimeError("generic host-signal smoke did not return scenarios")
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get("fixture") == fixture_name:
            return scenario
    raise RuntimeError(f"generic host-signal smoke did not return {fixture_name}")


def shell_case_by_event(summary: dict[str, Any], event_name: str) -> dict[str, Any]:
    cases = summary.get("contract_cases")
    if not isinstance(cases, list):
        raise RuntimeError("generic shell replay contract did not return contract_cases")
    for case in cases:
        if isinstance(case, dict) and case.get("host_event_file") == event_name:
            return case
    raise RuntimeError(f"generic shell replay contract did not return {event_name}")


def parity_contract_case(
    *,
    fixture_name: str,
    host_signal_summary: dict[str, Any],
    shell_replay_summary: dict[str, Any],
) -> dict[str, Any]:
    shell_event_name = SHELL_EVENT_BY_FIXTURE[fixture_name]
    path_results = {
        "generic_host_signal": normalized_host_signal_behavior(scenario_by_fixture(host_signal_summary, fixture_name)),
        "generic_shell_host_binding": normalized_shell_replay_behavior(
            shell_case_by_event(shell_replay_summary, shell_event_name)
        ),
    }
    normalized_behavior = path_results["generic_host_signal"]
    if path_results["generic_shell_host_binding"] != normalized_behavior:
        raise RuntimeError(
            f"{fixture_name} behavior diverged between generic host-signal and shell replay contracts: "
            f"{json.dumps({'baseline': normalized_behavior, 'all_path_results': path_results}, indent=2, sort_keys=True)}"
        )

    return {
        "signal_fixture": fixture_name,
        "shell_host_event_file": shell_event_name,
        "consistent": True,
        "normalized_behavior": normalized_behavior,
        "path_results": path_results,
    }


def run_parity_contract(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = (args.work_dir or (SCRIPT_DIR / "work")).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    cadence_args = parity_cadence_args(args)

    host_signal_summary = run_example_child(
        SCRIPT_DIR / "run.py",
        ["--work-dir", str(work_dir / "generic-host-signal")],
        cadence_args=cadence_args,
    )
    shell_replay_summary = run_example_child(
        SHELL_BINDING_SCRIPT,
        ["--replay-contract", "--work-dir", str(work_dir / "generic-shell-host-binding")],
        cadence_args=cadence_args,
    )
    parity_cases = [
        parity_contract_case(
            fixture_name=fixture_name,
            host_signal_summary=host_signal_summary,
            shell_replay_summary=shell_replay_summary,
        )
        for fixture_name, _expected_guardrail in SCENARIOS
    ]

    return {
        "result": "generic_host_signal_shell_parity_contract_passed",
        "work_dir": str(work_dir),
        "adapter_template_path": ADAPTER_TEMPLATE_DISPLAY,
        "adapter_template": str(ADAPTER_TEMPLATE),
        "fixture_dir": str(HOST_SIGNAL_FIXTURES),
        "shell_binding_path": SHELL_BINDING_DISPLAY,
        "shell_binding": str(SHELL_BINDING_SCRIPT),
        "contract_note": (
            "This generic host-signal smoke to generic shell host-binding replay contract is not a real host adapter. "
            "It compares normalized adapter/CLI-observed behavior across the generic host-signal smoke and the "
            "generic shell host-binding replay contract without claiming Claude, Gemini, or other host support."
        ),
        "parity_cases": parity_cases,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the generic host-signal adapter smoke example.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory. Refuses existing custom paths by default.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    parser.add_argument(
        "--parity-contract",
        action="store_true",
        help="Compare generic host-signal smoke behavior with the generic shell host-binding replay contract.",
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
        if args.parity_contract:
            summary = run_parity_contract(args)
        else:
            summary = run_smoke(args)
    except Exception as exc:
        label = "generic host-signal parity contract" if args.parity_contract else "generic host-signal smoke"
        print(f"{label} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
