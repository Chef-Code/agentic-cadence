#!/usr/bin/env python3
"""Executable public-CLI adapter smoke test.

This example deliberately treats Agentic Cadence as a black-box CLI. It does
not import Cadence internals, does not write runtime records directly, and
preserves the JSON packets returned by each public command.
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


def run(command: list[str], *, cwd: Path = ROOT, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != expect:
        raise RuntimeError(
            f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def cadence_base(cadence_python: str | None, cadence_command: str) -> list[str]:
    python_command = cadence_python or os.environ.get("AGENTIC_CADENCE_PYTHON")
    if python_command:
        return [python_command, "-m", "codex_cadence"]
    return [cadence_command]


def run_cadence(
    base: list[str],
    sequence: list[str],
    trace: list[dict[str, Any]],
    args: list[str],
    *,
    phase: str,
    actor: str,
    root: Path | None = None,
    expect: int = 0,
    stops_current_session: bool = False,
) -> dict[str, Any]:
    command = [*base]
    if root is not None:
        command.extend(["--root", str(root)])
    command.extend(args)
    sequence.append(args[0])
    result = run(command, expect=expect)
    try:
        packet = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"command emitted non-JSON with exit {result.returncode}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc
    trace.append(
        {
            "phase": phase,
            "actor": actor,
            "command": args[0],
            "argv": list(args),
            "returncode": result.returncode,
            "stops_current_session": bool(packet.get("stop_current_session")) or stops_current_session,
        }
    )
    return packet


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
    run(["git", "config", "user.email", "adapter-smoke@example.com"], cwd=path)
    run(["git", "config", "user.name", "Adapter Smoke"], cwd=path)
    run(["git", "config", "commit.gpgSign", "false"], cwd=path)
    run(["git", "config", "tag.gpgSign", "false"], cwd=path)
    run(["git", "config", "core.hooksPath", str(no_hooks)], cwd=path)
    (path / "README.md").write_text("Adapter smoke target repository.\n", encoding="utf-8")
    (path / "notes.py").write_text("# TODO inspect adapter handoff propagation\n", encoding="utf-8")
    run(["git", "add", "README.md", "notes.py"], cwd=path)
    run(["git", "commit", "--no-gpg-sign", "--no-verify", "-m", "initial adapter smoke target"], cwd=path)


def check_run(name: str, *, status: str = "COMPLETED", conclusion: str = "SUCCESS", workflow: str = "tests") -> dict[str, Any]:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "workflowName": workflow,
        "detailsUrl": f"https://example.test/checks/{name}",
    }


def status_context(name: str, *, state: str = "SUCCESS") -> dict[str, Any]:
    return {
        "__typename": "StatusContext",
        "context": name,
        "state": state,
        "targetUrl": f"https://example.test/status/{name}",
    }


def write_pr_fixtures(work_dir: Path) -> tuple[Path, Path, Path]:
    body = work_dir / "pr-body.md"
    template = work_dir / "pull_request_template.md"
    pr_json = work_dir / "pr.json"

    body.write_text("## Summary\nAdapter smoke is ready.\n\n## Testing\n- adapter smoke\n", encoding="utf-8")
    template.write_text("## Summary\n\n## Testing\n", encoding="utf-8")
    pr_json.write_text(
        json.dumps(
            {
                "number": 1,
                "title": "Adapter smoke fixture",
                "state": "OPEN",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "",
                "body": body.read_text(encoding="utf-8"),
                "statusCheckRollup": [
                    check_run("Python and protocol checks"),
                    check_run("adapter preflight", workflow="Agentic Cadence Review"),
                    status_context("CodeRabbit"),
                ],
            }
        ),
        encoding="utf-8",
    )
    return body, template, pr_json


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = (args.work_dir or (SCRIPT_DIR / "work")).resolve()
    replace_existing = args.replace_existing or args.work_dir is None
    prepare_work_dir(work_dir, replace_existing=replace_existing)
    runtime_root = work_dir / "runtime"
    target_repo = work_dir / "repo"
    init_target_repo(target_repo)
    body_file, template_file, pr_json_file = write_pr_fixtures(work_dir)

    base = cadence_base(args.cadence_python, args.cadence_command)
    command_sequence: list[str] = []
    command_trace: list[dict[str, Any]] = []
    packets: dict[str, Any] = {}

    packets["status"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["status"],
        phase="old_session_adapter",
        actor="old_session_adapter",
        root=runtime_root,
    )
    packets["prepare_handoff"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        [
            "prepare-handoff",
            "--id",
            "adapter-gate",
            "--title",
            "Adapter gated pickup",
            "--guardrail",
            "context",
            "--repo",
            "local/adapter-smoke",
            "--cwd",
            str(target_repo),
            "--task-type",
            "discovery",
            "--driver",
            "migration",
            "--driver",
            "cross_subsystem",
            "--driver",
            "unknown_repo_area",
            "--summary",
            "adapter smoke prepares a governed pickup",
            "--ci-status",
            "green",
            "--next-action",
            "claim only after approval",
        ],
        phase="old_session_adapter",
        actor="old_session_adapter",
        root=runtime_root,
        stops_current_session=True,
    )
    if not packets["prepare_handoff"].get("stop_current_session"):
        raise RuntimeError("prepare-handoff did not return stop_current_session")

    packets["claim_before_approval"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["claim-handoff", "adapter-gate", "--claimer", "adapter-smoke"],
        phase="new_session_adapter_before_approval",
        actor="new_session_adapter",
        root=runtime_root,
        expect=3,
    )
    packets["approve_handoff"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["approve-handoff", "adapter-gate", "--approver", "operator"],
        phase="operator_approval",
        actor="operator",
        root=runtime_root,
    )
    packets["claim_after_approval"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["claim-handoff", "adapter-gate", "--claimer", "adapter-smoke"],
        phase="new_session_adapter_after_approval",
        actor="new_session_adapter",
        root=runtime_root,
    )
    packets["complete_handoff"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["complete-handoff", "adapter-gate", "--summary", "adapter smoke completed"],
        phase="new_session_adapter_after_approval",
        actor="new_session_adapter",
        root=runtime_root,
    )
    packets["discover_candidates"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["discover-candidates", "--cwd", str(target_repo), "--intent", "hybrid", "--discovery-mode", "local", "--elect"],
        phase="adapter_utility_packets",
        actor="adapter_harness",
        root=runtime_root,
    )
    packets["pr_body_preflight"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        ["pr-body-preflight", "--body-file", str(body_file), "--pr-template-file", str(template_file)],
        phase="adapter_utility_packets",
        actor="adapter_harness",
    )
    packets["pr_readiness"] = run_cadence(
        base,
        command_sequence,
        command_trace,
        [
            "pr-readiness",
            "--pr-json-file",
            str(pr_json_file),
            "--required-check",
            "Python and protocol checks",
            "--pr-template-file",
            str(template_file),
        ],
        phase="adapter_utility_packets",
        actor="adapter_harness",
    )

    if not packets["claim_before_approval"].get("blocked_by_policy"):
        raise RuntimeError("approval-gated handoff was not blocked before approval")
    if packets["discover_candidates"].get("sources", {}).get("text_markers", 0) < 1:
        raise RuntimeError("discover-candidates did not report the fixture text marker")
    if not packets["pr_body_preflight"].get("ready_to_publish"):
        raise RuntimeError("pr-body-preflight fixture was not ready")
    if not packets["pr_readiness"].get("ready_to_merge"):
        raise RuntimeError("pr-readiness fixture was not ready")

    return {
        "result": "adapter_smoke_passed",
        "work_dir": str(work_dir),
        "runtime_root": str(runtime_root),
        "command_sequence": command_sequence,
        "command_trace": command_trace,
        "contract_note": (
            "This smoke validates the public CLI adapter boundary. Current packets are preserved as returned, "
            "including Codex-compatible packet labels retained by the 0.1.x command surface."
        ),
        "packets": packets,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Agentic Cadence adapter smoke contract.")
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
        print(f"adapter smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
