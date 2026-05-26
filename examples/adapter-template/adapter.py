#!/usr/bin/env python3
"""Copyable public-CLI adapter template for Agentic Cadence.

This file is intentionally small and standard-library only. Real host adapters
should copy the shape, keep their host-specific code at the edges, and continue
to treat Agentic Cadence as a black-box CLI.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


JsonPacket = dict[str, Any]
DEFAULT_CADENCE_TIMEOUT_SECONDS = 120.0

SignalKind = Literal["context_pressure", "operator_stop"]
SignalConfidence = Literal["low", "medium", "high"]
SignalTaskType = Literal["execution", "discovery"]

SIGNAL_KINDS = {"context_pressure", "operator_stop"}
SIGNAL_CONFIDENCES = {"low", "medium", "high"}
SIGNAL_TASK_TYPES = {"execution", "discovery"}
SIGNAL_TASK_DRIVERS = {
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
MAX_SIGNAL_SOURCE_LENGTH = 64


@dataclass(frozen=True)
class HostSessionSignal:
    """Adapter-local host/session signal.

    This is a copyable template helper, not a stable Agentic Cadence Python API.
    Host adapters should map their own host signal into this local shape and
    then pass the existing public CLI arguments to Agentic Cadence.
    """

    kind: SignalKind | str
    source: str
    confidence: SignalConfidence | str
    summary: str
    task_type: SignalTaskType | str
    drivers: Sequence[str]
    next_action: str


def detect_host_session_signal(
    *,
    summary: str,
    task_type: str,
    drivers: Sequence[str],
    next_action: str,
) -> HostSessionSignal | None:
    """Map the host's context-pressure signal into an adapter-local signal.

    Replace this placeholder with the host-specific signal. Examples include a
    context-window warning, an explicit operator stop request, or another host
    signal that says the current agent window should prepare pickup work and stop.
    """

    return HostSessionSignal(
        kind="context_pressure",
        source="adapter-template",
        confidence="high",
        summary=summary,
        task_type=task_type,
        drivers=tuple(drivers),
        next_action=next_action,
    )


def validate_host_session_signal(signal: HostSessionSignal) -> HostSessionSignal:
    """Validate and normalize the adapter-local host/session signal."""

    if not isinstance(signal, HostSessionSignal):
        raise RuntimeError("host session signal must be a HostSessionSignal")
    if signal.kind not in SIGNAL_KINDS:
        raise RuntimeError(f"unsupported host session signal kind: {signal.kind}")
    if signal.confidence not in SIGNAL_CONFIDENCES:
        raise RuntimeError(f"unsupported host session signal confidence: {signal.confidence}")
    if signal.task_type not in SIGNAL_TASK_TYPES:
        raise RuntimeError(f"unsupported host session signal task_type: {signal.task_type}")

    source = _require_non_empty_string("source", signal.source)
    if len(source) > MAX_SIGNAL_SOURCE_LENGTH:
        raise RuntimeError(f"host session signal source must be {MAX_SIGNAL_SOURCE_LENGTH} characters or fewer")

    summary = _require_non_empty_string("summary", signal.summary)
    next_action = _require_non_empty_string("next_action", signal.next_action)
    drivers = _validate_signal_drivers(signal.drivers)

    return HostSessionSignal(
        kind=signal.kind,
        source=source,
        confidence=signal.confidence,
        summary=summary,
        task_type=signal.task_type,
        drivers=tuple(drivers),
        next_action=next_action,
    )


def _require_non_empty_string(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"host session signal {name} must be a non-empty string")
    return value.strip()


def _validate_signal_drivers(drivers: Sequence[str]) -> list[str]:
    if isinstance(drivers, (str, bytes)) or not isinstance(drivers, Sequence):
        raise RuntimeError("host session signal drivers must be a sequence of strings")
    selected = list(drivers)
    for driver in selected:
        if not isinstance(driver, str) or not driver.strip():
            raise RuntimeError("host session signal drivers must be non-empty strings")
        if driver not in SIGNAL_TASK_DRIVERS:
            raise RuntimeError(f"unsupported host session signal driver: {driver}")
    return selected


def render_pickup_text(prepare_packet: JsonPacket) -> str:
    """Render host-specific pickup text around the preserved Cadence packet."""

    handoff = prepare_packet.get("handoff", {})
    handoff_id = handoff.get("id", "<handoff-id>")
    status = handoff.get("status", "<status>")
    return (
        f"Pickup prepared for {handoff_id} with status {status}.\n"
        "Attach the preserved Cadence JSON packet to the next host session.\n"
        "Render host-specific next steps from that packet without rewriting it."
    )


def run_cadence(
    command: list[str],
    *,
    runtime_root: Path,
    cadence_command: Sequence[str],
    timeout_seconds: float = DEFAULT_CADENCE_TIMEOUT_SECONDS,
) -> JsonPacket:
    """Run Agentic Cadence through the public CLI and return its JSON packet."""

    argv = [*cadence_command, "--root", str(runtime_root), *command]
    try:
        result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Cadence command timed out after {timeout_seconds:g}s: {' '.join(argv)}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Cadence command failed with {result.returncode}: {' '.join(argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Cadence command emitted non-JSON: {' '.join(argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc


def require_play_on(status_packet: JsonPacket) -> None:
    state = status_packet.get("cadence", {}).get("state")
    if state != "PLAY_ON":
        raise RuntimeError(f"Cadence state is {state}; adapter must not prepare pickup work")


def split_cadence_command(command: str, *, windows: bool | None = None) -> list[str]:
    """Split a copy-pasted command prefix without damaging Windows paths."""

    use_windows_rules = os.name == "nt" if windows is None else windows
    parts = shlex.split(command, posix=not use_windows_rules)
    if use_windows_rules:
        return [_strip_surrounding_quotes(part) for part in parts]
    return parts


def _strip_surrounding_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def prepare_context_handoff(
    *,
    runtime_root: Path,
    repo: str,
    cwd: Path,
    handoff_id: str,
    title: str,
    summary: str,
    next_action: str,
    cadence_command: Sequence[str],
    task_type: str,
    drivers: Sequence[str] = (),
    cadence_timeout_seconds: float = DEFAULT_CADENCE_TIMEOUT_SECONDS,
    runner: Callable[..., JsonPacket] = run_cadence,
    host_session_signal_detector: Callable[[], HostSessionSignal | None] | None = None,
) -> JsonPacket:
    """Prepare a context handoff and return host-ready pickup data.

    The returned `packets` are the original dictionaries returned by Cadence.
    Host adapters should render around them instead of rewriting their fields.
    """

    signal = (
        host_session_signal_detector()
        if host_session_signal_detector is not None
        else detect_host_session_signal(
            summary=summary,
            task_type=task_type,
            drivers=drivers,
            next_action=next_action,
        )
    )
    if signal is None:
        return {
            "result": "no_handoff_needed",
            "stop_current_session": False,
            "packets": {},
            "pickup_text": "",
        }
    signal = validate_host_session_signal(signal)

    status_packet = runner(
        ["status"],
        runtime_root=runtime_root,
        cadence_command=cadence_command,
        timeout_seconds=cadence_timeout_seconds,
    )
    require_play_on(status_packet)

    sizing_args: list[str] = ["--task-type", signal.task_type]
    for driver in signal.drivers:
        sizing_args.extend(["--driver", driver])

    prepare_packet = runner(
        [
            "prepare-handoff",
            "--id",
            handoff_id,
            "--title",
            title,
            "--guardrail",
            "context",
            "--repo",
            repo,
            "--cwd",
            str(cwd),
            *sizing_args,
            "--summary",
            signal.summary,
            "--next-action",
            signal.next_action,
        ],
        runtime_root=runtime_root,
        cadence_command=cadence_command,
        timeout_seconds=cadence_timeout_seconds,
    )

    return {
        "result": "handoff_prepared",
        "stop_current_session": bool(prepare_packet.get("stop_current_session")),
        "pickup_text": render_pickup_text(prepare_packet),
        "packets": {
            "status": status_packet,
            "prepare_handoff": prepare_packet,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copyable Agentic Cadence adapter template.")
    parser.add_argument("--runtime-root", type=Path, required=True, help="Explicit Cadence runtime root for this host.")
    parser.add_argument("--repo", required=True, help="Repository label to store in the handoff packet.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Repository working directory to snapshot.")
    parser.add_argument("--handoff-id", required=True, help="Stable handoff id for the pickup.")
    parser.add_argument("--title", required=True, help="Human-readable handoff title.")
    parser.add_argument("--summary", required=True, help="Short summary of the current work.")
    parser.add_argument("--next-action", required=True, help="Concrete first action for the next agent.")
    parser.add_argument("--task-type", choices=("execution", "discovery"), required=True, help="Cadence task type.")
    parser.add_argument(
        "--driver",
        action="append",
        default=[],
        help="Task sizing driver. Repeat for each applicable driver, for example --driver migration.",
    )
    parser.add_argument(
        "--cadence-command",
        default="agentic-cadence",
        help='Cadence command prefix, for example: agentic-cadence, "python -m codex_cadence", or a Windows path.',
    )
    parser.add_argument(
        "--cadence-timeout-seconds",
        type=float,
        default=DEFAULT_CADENCE_TIMEOUT_SECONDS,
        help="Timeout for each Cadence CLI subprocess call.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = prepare_context_handoff(
            runtime_root=args.runtime_root,
            repo=args.repo,
            cwd=args.cwd,
            handoff_id=args.handoff_id,
            title=args.title,
            summary=args.summary,
            next_action=args.next_action,
            task_type=args.task_type,
            drivers=args.driver,
            cadence_timeout_seconds=args.cadence_timeout_seconds,
            cadence_command=split_cadence_command(args.cadence_command),
        )
    except Exception as exc:
        print(f"adapter template failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
