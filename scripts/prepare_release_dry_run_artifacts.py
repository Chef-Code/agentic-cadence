#!/usr/bin/env python3
"""Prepare GitHub Actions artifacts from a release dry-run packet."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def escape_command(value: object) -> str:
    """Escape GitHub workflow command data and properties."""
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def issue_value(issue: object, key: str, default: str) -> object:
    if isinstance(issue, dict):
        return issue.get(key, default)
    return issue


def prepare_artifacts(
    packet_path: Path = Path("release-dry-run.json"),
    notes_path: Path = Path("release-notes.md"),
    github_output_path: Path | None = None,
) -> dict[str, str]:
    packet: dict[str, Any] = json.loads(packet_path.read_text(encoding="utf-8"))
    notes_path.write_text(packet.get("release_notes", ""), encoding="utf-8")

    ready_to_release = str(packet.get("ready_to_release")).lower()
    operator_confirmation_required = str(packet.get("operator_confirmation_required")).lower()

    print(f"ready_to_release={packet.get('ready_to_release')}")
    print(f"operator_confirmation_required={packet.get('operator_confirmation_required')}")
    print(f"recommended_next_action={packet.get('recommended_next_action')}")

    for warning in packet.get("warnings", []):
        code = escape_command(issue_value(warning, "code", "release_warning"))
        message = escape_command(issue_value(warning, "message", "release warning"))
        print(f"::warning title={code}::{message}")
    for blocker in packet.get("blockers", []):
        code = escape_command(issue_value(blocker, "code", "release_blocker"))
        message = escape_command(issue_value(blocker, "message", "release blocker"))
        print(f"::error title={code}::{message}")

    if github_output_path is not None:
        with github_output_path.open("a", encoding="utf-8") as output:
            output.write(f"ready_to_release={ready_to_release}\n")
            output.write(f"operator_confirmation_required={operator_confirmation_required}\n")

    return {
        "ready_to_release": ready_to_release,
        "operator_confirmation_required": operator_confirmation_required,
    }


def main() -> int:
    github_output = os.environ.get("GITHUB_OUTPUT")
    prepare_artifacts(github_output_path=Path(github_output) if github_output else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
