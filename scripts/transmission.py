#!/usr/bin/env python3
"""Legacy wrapper for the renamed Agentic Cadence CLI."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transmission_control.cli import *  # noqa: F403
from transmission_control.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
