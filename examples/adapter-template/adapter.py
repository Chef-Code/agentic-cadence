#!/usr/bin/env python3
"""Copyable public-CLI adapter template for Agentic Cadence.

This file is intentionally small and standard-library only. Real host adapters
should copy the shape, keep their host-specific code at the edges, and continue
to treat Agentic Cadence as a black-box CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


JsonPacket = dict[str, Any]


def detect_context_pressure() -> bool:
    """Map the host's context-pressure signal into a boolean.

    Replace this placeholder with the host-specific signal. Examples include a
    context-window warning, an explicit operator stop request, or another signal
    that says the current agent window should prepare pickup work and stop.
    """

    return True


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


def run_cadence(command: list[str], *, runtime_root: Path, cadence_command: Sequence[str]) -> JsonPacket:
    """Run Agentic Cadence through the public CLI and return its JSON packet."""

    argv = [*cadence_command, "--root", str(runtime_root), *command]
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
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
    task_type: str = "execution",
    drivers: Sequence[str] = (),
    runner: Callable[..., JsonPacket] = run_cadence,
    context_pressure_detector: Callable[[], bool] = detect_context_pressure,
) -> JsonPacket:
    """Prepare a context handoff and return host-ready pickup data.

    The returned `packets` are the original dictionaries returned by Cadence.
    Host adapters should render around them instead of rewriting their fields.
    """

    if not context_pressure_detector():
        return {
            "result": "no_handoff_needed",
            "stop_current_session": False,
            "packets": {},
            "pickup_text": "",
        }

    status_packet = runner(["status"], runtime_root=runtime_root, cadence_command=cadence_command)
    require_play_on(status_packet)

    sizing_args: list[str] = ["--task-type", task_type]
    for driver in drivers:
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
            summary,
            "--next-action",
            next_action,
        ],
        runtime_root=runtime_root,
        cadence_command=cadence_command,
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
    parser.add_argument("--task-type", choices=("execution", "discovery"), default="execution", help="Cadence task type.")
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
            cadence_command=split_cadence_command(args.cadence_command),
        )
    except Exception as exc:
        print(f"adapter template failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
