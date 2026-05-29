#!/usr/bin/env python3
"""Validate generic host-signal and shell host-event fixture contracts.

This helper is example/contract scoped. It validates the checked-in generic
payloads before any real host adapter exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HOST_SIGNAL_DIR = Path(__file__).resolve().parent / "host-signal-fixtures"
HOST_EVENT_DIR = ROOT / "examples" / "generic-shell-host-binding" / "host-events"
DEFAULT_CASES = (
    ("no-signal.json", "no-event.json", None),
    ("context-pressure.json", "context-pressure.json", "context_pressure"),
    ("reviewer-loop.json", "reviewer-loop.json", "reviewer_loop"),
    ("ci-loop.json", "ci-loop.json", "ci_loop"),
    ("operator-stop.json", "operator-stop.json", "operator_stop"),
)
HOST_SIGNAL_FIELDS = {
    "kind",
    "source",
    "confidence",
    "summary",
    "task_type",
    "drivers",
    "next_action",
}
HOST_EVENT_FIELDS = {
    "event",
    "source",
    "confidence",
    "summary",
    "task_type",
    "drivers",
    "next_action",
}
ALLOWED_KINDS = {"context_pressure", "reviewer_loop", "ci_loop", "operator_stop"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_TASK_TYPES = {"execution", "discovery"}
ALLOWED_DRIVERS = {
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
MAX_SOURCE_LENGTH = 64


def load_json(path: Path, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RuntimeError(f"{label} could not be read: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from exc


def require_exact_fields(payload: dict[str, Any], expected_fields: set[str], label: str) -> None:
    extra = sorted(set(payload) - expected_fields)
    if extra:
        raise RuntimeError(f"{label} has unsupported fields: {', '.join(extra)}")
    missing = sorted(expected_fields - set(payload))
    if missing:
        raise RuntimeError(f"{label} is missing fields: {', '.join(missing)}")


def require_non_empty_string(payload: dict[str, Any], field: str, label: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise RuntimeError(f"{label} field {field!r} must be a non-empty string")
    stripped = value.strip()
    if not stripped:
        raise RuntimeError(f"{label} field {field!r} must be a non-empty string")
    if value != stripped:
        raise RuntimeError(f"{label} field {field!r} must not have leading or trailing whitespace")
    return value


def validate_source(payload: dict[str, Any], label: str) -> str:
    source = require_non_empty_string(payload, "source", label)
    if len(source) > MAX_SOURCE_LENGTH:
        raise RuntimeError(f"{label} source must be {MAX_SOURCE_LENGTH} characters or fewer")
    return source


def require_allowed_value(payload: dict[str, Any], field: str, allowed: set[str], label: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(f"{label} field {field!r} must be one of: {choices}")
    return value


def validate_drivers(payload: dict[str, Any], label: str) -> list[str]:
    drivers = payload.get("drivers")
    if not isinstance(drivers, list):
        raise RuntimeError(f"{label} drivers must be a JSON array")
    selected = list(drivers)
    for driver in selected:
        if not isinstance(driver, str) or not driver.strip():
            raise RuntimeError(f"{label} drivers must be non-empty strings")
        if driver != driver.strip():
            raise RuntimeError(f"{label} drivers must not have leading or trailing whitespace")
        if driver not in ALLOWED_DRIVERS:
            raise RuntimeError(f"{label} has unsupported driver: {driver}")
    return selected


def normalize_signal_fields(payload: dict[str, Any], *, kind_field: str, label: str) -> dict[str, Any]:
    validate_source(payload, label)
    return {
        "kind": require_allowed_value(payload, kind_field, ALLOWED_KINDS, label),
        "confidence": require_allowed_value(payload, "confidence", ALLOWED_CONFIDENCE, label),
        "summary": require_non_empty_string(payload, "summary", label),
        "task_type": require_allowed_value(payload, "task_type", ALLOWED_TASK_TYPES, label),
        "drivers": validate_drivers(payload, label),
        "next_action": require_non_empty_string(payload, "next_action", label),
    }


def normalize_host_signal_fixture(payload: Any, label: str) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} host signal fixture must be a JSON object or null")
    require_exact_fields(payload, HOST_SIGNAL_FIELDS, label)
    return normalize_signal_fields(payload, kind_field="kind", label=label)


def normalize_host_event(payload: Any, label: str) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} host event must be a JSON object or null")
    require_exact_fields(payload, HOST_EVENT_FIELDS, label)
    return normalize_signal_fields(payload, kind_field="event", label=label)


def parse_case(value: str) -> tuple[str, str]:
    parts = value.split(":", 1)
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError("--case must use host-signal.json:host-event.json")
    return (parts[0], parts[1])


def validate_expected_kind(payload: dict[str, Any] | None, expected_kind: str | None, label: str) -> None:
    if expected_kind is None:
        if payload is not None:
            raise RuntimeError(f"{label} expected no signal, got kind {payload['kind']!r}")
        return
    if payload is None:
        raise RuntimeError(f"{label} expected kind {expected_kind!r}, got null")
    if payload["kind"] != expected_kind:
        raise RuntimeError(f"{label} expected kind {expected_kind!r}, got {payload['kind']!r}")


def contract_case(
    host_signal_dir: Path,
    host_event_dir: Path,
    signal_name: str,
    event_name: str,
    *,
    expected_kind: str | None = None,
    enforce_expected_kind: bool = False,
) -> dict[str, Any]:
    normalized_signal = normalize_host_signal_fixture(
        load_json(host_signal_dir / signal_name, f"{signal_name} host signal fixture"),
        signal_name,
    )
    normalized_event = normalize_host_event(
        load_json(host_event_dir / event_name, f"{event_name} host event"),
        event_name,
    )
    if normalized_signal != normalized_event:
        raise RuntimeError(
            f"{signal_name} drifted from {event_name}: "
            f"{json.dumps({'host_signal': normalized_signal, 'host_event': normalized_event}, sort_keys=True)}"
        )
    if enforce_expected_kind:
        validate_expected_kind(normalized_signal, expected_kind, signal_name)
        validate_expected_kind(normalized_event, expected_kind, event_name)
    return {
        "host_signal_fixture": signal_name,
        "host_event_file": event_name,
        "expected_kind": expected_kind,
        "schema_valid": True,
        "fixture_pair_aligned": True,
        "normalized_host_signal": normalized_signal,
        "normalized_host_event": normalized_event,
    }


def run_contract(args: argparse.Namespace) -> dict[str, Any]:
    cases = (
        [(signal_name, event_name, None, False) for signal_name, event_name in args.case]
        if args.case
        else [(signal_name, event_name, expected_kind, True) for signal_name, event_name, expected_kind in DEFAULT_CASES]
    )
    host_signal_dir = args.host_signal_dir.resolve()
    host_event_dir = args.host_event_dir.resolve()
    return {
        "result": "host_signal_contract_schema_passed",
        "host_signal_fixture_dir": str(host_signal_dir),
        "host_event_dir": str(host_event_dir),
        "contract_note": (
            "This schema contract validates generic host-signal fixtures and shell host-event payloads. "
            "It is not a real host adapter and does not claim Claude, Gemini, or other host support."
        ),
        "contract_cases": [
            contract_case(
                host_signal_dir,
                host_event_dir,
                signal_name,
                event_name,
                expected_kind=expected_kind,
                enforce_expected_kind=enforce_expected_kind,
            )
            for signal_name, event_name, expected_kind, enforce_expected_kind in cases
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate generic host-signal fixture and host-event schemas.")
    parser.add_argument("--host-signal-dir", type=Path, default=HOST_SIGNAL_DIR)
    parser.add_argument("--host-event-dir", type=Path, default=HOST_EVENT_DIR)
    parser.add_argument("--case", type=parse_case, action="append", help="Fixture pair as host-signal.json:host-event.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_contract(args)
    except Exception as exc:
        print(f"host signal contract schema failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
