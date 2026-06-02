#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write fake controlled executor result evidence")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--status", choices=("succeeded", "failed", "blocked", "stopped"), default="succeeded")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--validation-name", default="controlled-fixture-validation")
    parser.add_argument("--validation-status", choices=("passed", "failed", "skipped"), default="passed")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--blocker", action="append", default=[])
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    started_at = utc_now()
    if args.sleep_seconds > 0:
        time.sleep(args.sleep_seconds)
    ended_at = utc_now()
    command_exit_code = 0 if args.validation_status == "passed" else max(args.exit_code, 1)
    evidence = {
        "schema_version": "generic-executor-result.v1",
        "packet": "executor_result",
        "task_id": task["task"]["id"],
        "executor_id": "controlled-fixture",
        "started_at": started_at,
        "ended_at": ended_at,
        "status": args.status,
        "files_changed": list(args.changed_file),
        "commands_run": [
            {
                "command": args.command,
                "exit_code": command_exit_code,
            }
        ],
        "validation_results": [
            {
                "name": args.validation_name,
                "status": args.validation_status,
                "command": args.command,
            }
        ],
        "summary": args.summary,
        "confidence": "high" if args.status == "succeeded" else "low",
        "blockers": list(args.blocker),
        "dirty_worktree": args.status != "succeeded",
        "resulting_head": task["repo"]["head"] if args.status == "succeeded" else None,
    }
    result_file = Path(args.result_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    return command_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
