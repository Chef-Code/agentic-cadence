#!/usr/bin/env python3
"""Black-box CLI smoke test for PR CI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CADENCE = ROOT / "scripts" / "cadence.py"


def run(command: list[str], cwd: Path | None = None, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd or ROOT, text=True, capture_output=True, check=False)
    if result.returncode != expect:
        raise RuntimeError(
            f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def run_cli(root: Path, *args: str, expect: int = 0) -> dict[str, Any] | None:
    result = run([sys.executable, str(CADENCE), "--root", str(root), *args], expect=expect)
    return json.loads(result.stdout) if result.stdout.strip() else None


def init_repo(path: Path) -> None:
    run(["git", "init", "-b", "main"], cwd=path)
    run(["git", "config", "user.email", "ci@example.com"], cwd=path)
    run(["git", "config", "user.name", "CI Smoke"], cwd=path)
    (path / "README.md").write_text("smoke\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=path)
    run(["git", "commit", "-m", "initial"], cwd=path)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        state_root = temp_path / "state"
        repo = temp_path / "repo"
        repo.mkdir()
        init_repo(repo)

        run_cli(state_root, "init")
        run_cli(
            state_root,
            "create-handoff",
            "--id",
            "smoke-1",
            "--title",
            "Smoke handoff",
            "--task-type",
            "execution",
            "--message",
            "Continue the smoke lifecycle.",
        )
        run_cli(state_root, "claim-handoff", "smoke-1", "--claimer", "ci")
        run_cli(state_root, "complete-handoff", "smoke-1", "--summary", "completed")

        run_cli(
            state_root,
            "create-handoff",
            "--id",
            "smoke-approval",
            "--title",
            "Approval handoff",
            "--task-type",
            "discovery",
            "--driver",
            "migration",
            "--driver",
            "cross_subsystem",
            "--driver",
            "unknown_repo_area",
            "--message",
            "Large discovery work should require approval.",
        )
        blocked = run_cli(state_root, "claim-handoff", "smoke-approval", "--claimer", "ci", expect=3)
        if not blocked or not blocked.get("blocked_by_policy"):
            raise RuntimeError("approval-gated handoff was not blocked before approval")
        run_cli(state_root, "approve-handoff", "smoke-approval", "--approver", "ci")
        run_cli(state_root, "claim-handoff", "smoke-approval", "--claimer", "ci")
        run_cli(state_root, "complete-handoff", "smoke-approval", "--summary", "approved and completed")

        before = run_cli(state_root, "snapshot-repo", "--cwd", str(repo), "--repo", "local/test")
        tasks_path = temp_path / "tasks.json"
        write_json(tasks_path, [{"id": "task-1", "task_type": "execution"}])
        epoch = run_cli(
            state_root,
            "start-epoch",
            "--repo",
            "local/test",
            "--branch",
            "main",
            "--tasks-file",
            str(tasks_path),
            "--snapshot-before-file",
            before["path"],
        )

        after = run_cli(state_root, "snapshot-repo", "--cwd", str(repo), "--repo", "local/test", "--ci-status", "green")
        candidates_path = temp_path / "candidates.json"
        write_json(candidates_path, [{"id": "task-2", "task_type": "execution", "bucket": "S"}])
        check = run_cli(
            state_root,
            "self-check",
            "--epoch-id",
            epoch["id"],
            "--candidates-file",
            str(candidates_path),
            "--snapshot-after-file",
            after["path"],
        )
        if check["decision"] != "CONTINUE":
            raise RuntimeError(f"expected CONTINUE self-check, got {check['decision']}: {check}")
        run_cli(state_root, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

    print("CI smoke lifecycle passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
