#!/usr/bin/env python3
"""Fail a release dry-run workflow when the packet is blocked or unsafe."""

from __future__ import annotations

import os


def enforce_release_dry_run_result(
    *,
    ready_to_release: str | None,
    operator_confirmation_required: str | None,
) -> int:
    """Return non-zero unless the dry-run packet is ready and still requires confirmation."""
    if operator_confirmation_required != "true":
        print(
            "::error title=operator_confirmation_required::"
            "Release dry-run packets must require operator confirmation."
        )
        return 1
    if ready_to_release != "true":
        print(
            "::error title=release_blocked::"
            "Release dry run reported blockers. Inspect release-dry-run artifact."
        )
        return 1
    print("No tags, GitHub releases, or package publications are created by this workflow.")
    return 0


def main() -> int:
    """Enforce the release dry-run result using GitHub Actions step outputs."""
    return enforce_release_dry_run_result(
        ready_to_release=os.environ.get("READY_TO_RELEASE"),
        operator_confirmation_required=os.environ.get("OPERATOR_CONFIRMATION_REQUIRED"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
