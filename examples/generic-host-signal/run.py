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
SCENARIOS = (
    ("no-signal.json", None),
    ("context-pressure.json", "context"),
    ("operator-stop.json", "operator_stop"),
)


def run(command: list[str], *, cwd: Path = ROOT, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != expect:
        raise RuntimeError(
            f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def cadence_command_value(cadence_python: str | None, cadence_command: str) -> str:
    python_command = cadence_python or os.environ.get("AGENTIC_CADENCE_PYTHON")
    if python_command:
        return f'"{python_command}" -m codex_cadence'
    return cadence_command


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the generic host-signal adapter smoke example.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory. Refuses existing custom paths by default.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    parser.add_argument("--cadence-command", default="agentic-cadence", help="Installed Cadence command to invoke.")
    parser.add_argument(
        "--cadence-python",
        help="Run Cadence as '<python> -m codex_cadence'. Also configurable through AGENTIC_CADENCE_PYTHON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_smoke(args)
    except Exception as exc:
        print(f"generic host-signal smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
