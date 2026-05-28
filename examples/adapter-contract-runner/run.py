#!/usr/bin/env python3
"""Generic pre-claim adapter-contract runner.

This runner composes generic adapter contracts before future host-binding
claims. It is not a real host adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import string
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORK_DIR = SCRIPT_DIR / "work"
WORK_DIR_MARKER = ".adapter-contract-runner-work"
SCHEMA_SCRIPT = ROOT / "examples" / "adapter-template" / "host_signal_contract.py"
GENERIC_HOST_SIGNAL_SCRIPT = ROOT / "examples" / "generic-host-signal" / "run.py"
GENERIC_SHELL_BINDING_SCRIPT = ROOT / "examples" / "generic-shell-host-binding" / "run.py"
EXTERNAL_CONFORMANCE_SCRIPT = ROOT / "examples" / "external-host-binding-conformance" / "run.py"
MAPPING_EVIDENCE_PATH = "examples/adapter-template/host-binding-mapping.md"
REQUIRED_CONTRACT_LABELS = [
    "host_signal_schema",
    "generic_host_signal_smoke",
    "generic_shell_replay",
    "generic_host_shell_parity",
    "external_host_binding_conformance",
]


def run(command: list[str], *, timeout_seconds: float = 600.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the generic pre-claim adapter-contract suite.")
    parser.add_argument("--work-dir", type=Path, help="Disposable work directory.")
    parser.add_argument("--replace-existing", action="store_true", help="Remove an existing --work-dir before running.")
    parser.add_argument("--binding-command-template", help="External binding command template for conformance.")
    parser.add_argument(
        "--evidence-summary",
        action="store_true",
        help="Emit compact PR evidence JSON without nested child packets.",
    )
    cadence_group = parser.add_mutually_exclusive_group()
    cadence_group.add_argument("--cadence-command", help="Installed Cadence command to pass to child contracts.")
    cadence_group.add_argument("--cadence-python", help="Run Cadence in child contracts as '<python> -m codex_cadence'.")
    return parser


def remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    func(path)


def ensure_safe_replacement_target(path: Path) -> None:
    path = path.resolve(strict=False)
    default_work_dir = DEFAULT_WORK_DIR.resolve(strict=False)
    if path == default_work_dir or path.is_relative_to(default_work_dir):
        return

    blocked_paths = {
        Path(path.anchor).resolve(),
        Path.home().resolve(),
        ROOT.resolve(),
        SCRIPT_DIR.resolve(),
    }
    if path in blocked_paths:
        raise RuntimeError(f"refusing to remove unsafe work directory target: {path}")

    marker = path / WORK_DIR_MARKER
    if not marker.is_file():
        raise RuntimeError(f"refusing to remove custom work directory without {WORK_DIR_MARKER} marker: {path}")


def prepare_work_dir(path: Path, *, replace_existing: bool) -> Path:
    path = path.expanduser().resolve(strict=False)
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"work directory path is not a directory: {path}")
        if not replace_existing:
            raise RuntimeError(f"work directory already exists: {path}; pass --replace-existing to reuse it")
        ensure_safe_replacement_target(path)
        shutil.rmtree(path, onerror=remove_readonly)
    path.mkdir(parents=True)
    (path / WORK_DIR_MARKER).write_text("Disposable generic adapter-contract runner work directory.\n", encoding="utf-8")
    return path


def cadence_args(args: argparse.Namespace) -> list[str]:
    if args.cadence_python:
        return ["--cadence-python", args.cadence_python]
    if args.cadence_command:
        return ["--cadence-command", args.cadence_command]
    return []


def contract_commands(args: argparse.Namespace, work_dir: Path) -> list[tuple[str, list[str]]]:
    child_cadence_args = cadence_args(args)
    external_command = [
        sys.executable,
        str(EXTERNAL_CONFORMANCE_SCRIPT),
        "--work-dir",
        str(work_dir / "external-host-binding-conformance"),
        *child_cadence_args,
    ]
    if args.binding_command_template:
        external_command.extend(["--binding-command-template", args.binding_command_template])

    return [
        ("host_signal_schema", [sys.executable, str(SCHEMA_SCRIPT)]),
        (
            "generic_host_signal_smoke",
            [
                sys.executable,
                str(GENERIC_HOST_SIGNAL_SCRIPT),
                "--work-dir",
                str(work_dir / "generic-host-signal-smoke"),
                *child_cadence_args,
            ],
        ),
        (
            "generic_shell_replay",
            [
                sys.executable,
                str(GENERIC_SHELL_BINDING_SCRIPT),
                "--replay-contract",
                "--work-dir",
                str(work_dir / "generic-shell-replay"),
                *child_cadence_args,
            ],
        ),
        (
            "generic_host_shell_parity",
            [
                sys.executable,
                str(GENERIC_HOST_SIGNAL_SCRIPT),
                "--parity-contract",
                "--work-dir",
                str(work_dir / "generic-host-shell-parity"),
                *child_cadence_args,
            ],
        ),
        ("external_host_binding_conformance", external_command),
    ]


def run_json_contract(label: str, command: list[str]) -> dict[str, Any]:
    try:
        result = run(command)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label} timed out after {exc.timeout}s: {' '.join(command)}") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed with {result.returncode}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    try:
        summary = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{label} did not emit JSON: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc
    if not isinstance(summary, dict):
        raise RuntimeError(f"{label} emitted JSON {type(summary).__name__}, expected object")

    return {
        "label": label,
        "command": command,
        "result": summary.get("result"),
        "summary": summary,
    }


def run_preclaim_contracts(args: argparse.Namespace) -> dict[str, Any]:
    work_dir = prepare_work_dir(
        args.work_dir or DEFAULT_WORK_DIR,
        replace_existing=args.replace_existing or args.work_dir is None,
    )
    contracts = [
        run_json_contract(label, command)
        for label, command in contract_commands(args, work_dir)
    ]
    return {
        "result": "adapter_contract_preclaim_passed",
        "work_dir": str(work_dir),
        "binding_command_mode": "template" if args.binding_command_template else "default_generic_shell",
        "binding_command_template": args.binding_command_template,
        "contract_note": (
            "This generic pre-claim adapter-contract runner composes schema, smoke, replay, parity, "
            "and external conformance checks without claiming Claude, Gemini, or other host support."
        ),
        "contracts": contracts,
    }


def binding_template_field_names(template: Any) -> set[str]:
    if not isinstance(template, str):
        return set()

    try:
        parsed_fields = [
            field_name
            for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template)
            if field_name
        ]
    except ValueError:
        return set()

    return {
        field_name.split(".", 1)[0].split("[", 1)[0]
        for field_name in parsed_fields
    }


def compact_evidence_summary(summary: dict[str, Any]) -> dict[str, Any]:
    contracts = [
        {
            "label": contract.get("label"),
            "result": contract.get("result"),
        }
        for contract in summary.get("contracts", [])
    ]
    observed_labels = [contract["label"] for contract in contracts]
    template = summary.get("binding_command_template")
    template_fields = binding_template_field_names(template)
    all_required_contracts_observed = all(label in observed_labels for label in REQUIRED_CONTRACT_LABELS)
    observed_contracts_passed = all(str(contract.get("result", "")).endswith("_passed") for contract in contracts)

    return {
        "result": summary.get("result"),
        "evidence_mode": "compact",
        "binding_command_mode": summary.get("binding_command_mode"),
        "binding_command_template": template,
        "contract_note": summary.get("contract_note"),
        "contracts": contracts,
        "checklist_evidence": {
            "generic_only": True,
            "mapping_evidence_path": MAPPING_EVIDENCE_PATH,
            "required_contract_labels": REQUIRED_CONTRACT_LABELS,
            "observed_contract_labels": observed_labels,
            "all_required_contracts_observed": all_required_contracts_observed,
            "all_contracts_passed": summary.get("result") == "adapter_contract_preclaim_passed"
            and all_required_contracts_observed
            and observed_contracts_passed,
            "binding_template_placeholders": {
                "host_event_file": "host_event_file" in template_fields,
                "case_work_dir": "case_work_dir" in template_fields,
                "cadence_args": "cadence_args" in template_fields,
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_preclaim_contracts(args)
    except Exception as exc:
        print(f"adapter contract runner failed: {exc}", file=sys.stderr)
        return 1
    output = compact_evidence_summary(summary) if args.evidence_summary else summary
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
