#!/usr/bin/env python3
"""Build and verify Agentic Cadence package installation modes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def venv_command(venv: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    directory = "Scripts" if os.name == "nt" else "bin"
    return venv / directory / f"{name}{suffix}"


def newest_wheel() -> Path:
    wheels = sorted((ROOT / "dist").glob("agentic_cadence-*.whl"))
    if not wheels:
        raise SystemExit("no agentic_cadence wheel found in dist")
    return wheels[-1]


def verify_console_commands(venv: Path) -> None:
    run([str(venv_command(venv, "agentic-cadence")), "--help"])
    run([str(venv_command(venv, "codex-cadence")), "--help"])
    run([str(venv_command(venv, "codex-transmission")), "--help"])
    # Keep this literal for workflow coverage tests: python -m codex_cadence
    run([str(venv_python(venv)), "-m", "codex_cadence", "--help"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "cadence"
        result = subprocess.run(
            [str(venv_command(venv, "agentic-cadence")), "--root", str(root), "audit-replay"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            raise SystemExit(result.returncode)
        payload = json.loads(result.stdout)
        if payload.get("schema_version") != "audit-replay.v1" or not payload.get("valid"):
            raise SystemExit(f"installed audit-replay returned unexpected payload: {payload}")


def main() -> int:
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    for egg_info in ROOT.glob("*.egg-info"):
        shutil.rmtree(egg_info, ignore_errors=True)

    # Keep this literal for workflow coverage tests: python -m build
    run([sys.executable, "-m", "build"])
    wheel = newest_wheel()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)

        wheel_venv = tmp_root / "wheel-venv"
        run([sys.executable, "-m", "venv", str(wheel_venv)])
        run([str(venv_python(wheel_venv)), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(venv_python(wheel_venv)), "-m", "pip", "install", str(wheel)])
        verify_console_commands(wheel_venv)

        editable_venv = tmp_root / "editable-venv"
        run([sys.executable, "-m", "venv", str(editable_venv)])
        run([str(venv_python(editable_venv)), "-m", "pip", "install", "--upgrade", "pip"])
        # Keep this literal for workflow coverage tests: pip install -e
        run([str(venv_python(editable_venv)), "-m", "pip", "install", "-e", str(ROOT)])
        verify_console_commands(editable_venv)

    print("Package verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
