from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.policy_audit import (
    append_audit_record,
    checksum_json,
    controlled_pr_cycle_audit_record,
    validate_controlled_pr_cycle_audit_record,
)
from codex_cadence.store import read_json

CONTROLLED_PR_CYCLE_SCHEMA_VERSION = "controlled-pr-cycle.v1"

REQUIRED_STEP_ORDER = (
    "controlled_loop_tick",
    "git_pr_materialization",
    "initial_post_write_gate",
)

OPTIONAL_STEP_ORDER = (
    "review_response_materialization",
    "review_response_post_write_gate",
    "review_thread_resolution_materialization",
    "review_thread_resolution_post_write_gate",
)

STEP_ORDER = (*REQUIRED_STEP_ORDER, *OPTIONAL_STEP_ORDER)


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _pr_number_from_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    marker = "/pull/"
    if marker not in value:
        return None
    suffix = value.rsplit(marker, 1)[-1]
    digits = []
    for char in suffix:
        if not char.isdigit():
            break
        digits.append(char)
    return "".join(digits) or None


def _target_number(value: Any, url: Any) -> str | None:
    if value is not None and not (isinstance(value, str) and not value.strip()):
        return str(value)
    return _pr_number_from_url(url)


def _packet_type_blockers(
    packet: Any,
    *,
    step: str,
    expected_packet: str,
    expected_schema: str,
) -> list[dict[str, Any]]:
    if not isinstance(packet, dict):
        return [_issue(f"{step}_packet_invalid", f"{step} evidence must be a JSON object")]
    blockers: list[dict[str, Any]] = []
    if packet.get("schema_version") != expected_schema:
        blockers.append(
            _issue(
                "pr_cycle_packet_mismatch",
                f"{step} schema_version is invalid",
                step=step,
                expected=expected_schema,
                actual=packet.get("schema_version"),
            )
        )
    if packet.get("packet") != expected_packet:
        blockers.append(
            _issue(
                "pr_cycle_packet_mismatch",
                f"{step} packet is invalid",
                step=step,
                expected=expected_packet,
                actual=packet.get("packet"),
            )
        )
    return blockers


def _git_pr_target(packet: dict[str, Any]) -> dict[str, Any]:
    repository = packet.get("repository") if isinstance(packet.get("repository"), dict) else {}
    pr_url = packet.get("pr_url")
    return {
        "number": _target_number(packet.get("pr_number"), pr_url),
        "head_ref": packet.get("proposed_branch"),
        "base_ref": repository.get("base_branch"),
        "head_sha": repository.get("current_head"),
        "url": pr_url,
    }


def _pr_target(packet: dict[str, Any]) -> dict[str, Any]:
    pr = packet.get("pr") if isinstance(packet.get("pr"), dict) else {}
    url = pr.get("url")
    return {
        "number": _target_number(pr.get("number"), url),
        "head_ref": pr.get("head_ref"),
        "base_ref": pr.get("base_ref"),
        "head_sha": pr.get("head_sha"),
        "url": url,
    }


def _gate_target(packet: dict[str, Any]) -> dict[str, Any]:
    materialization = packet.get("materialization") if isinstance(packet.get("materialization"), dict) else {}
    url = materialization.get("pr_url")
    return {
        "number": _target_number(materialization.get("pr_number"), url),
        "head_ref": materialization.get("head_ref"),
        "base_ref": materialization.get("base_ref"),
        "head_sha": materialization.get("head_sha"),
        "url": url,
    }


def _gate_refresh_target(packet: dict[str, Any]) -> dict[str, Any]:
    refresh = packet.get("refresh") if isinstance(packet.get("refresh"), dict) else {}
    url = refresh.get("pr_url")
    return {
        "number": _target_number(refresh.get("pr_number"), url),
        "head_ref": refresh.get("head_ref"),
        "base_ref": refresh.get("base_ref"),
        "head_sha": refresh.get("head_sha"),
        "url": url,
    }


def _target_blockers(step: str, target: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for field in ("number", "head_ref", "base_ref", "head_sha"):
        actual = target.get(field)
        expected_value = expected.get(field)
        if not _non_empty_string(actual) or not _non_empty_string(expected_value):
            blockers.append(
                _issue(
                    "pr_cycle_pr_target_anchor_missing",
                    "PR-cycle evidence must identify PR number, branch, base, and head SHA",
                    step=step,
                    field=field,
                    expected=expected_value,
                    actual=actual,
                )
            )
        elif str(actual) != str(expected_value):
            blockers.append(
                _issue(
                    "pr_cycle_pr_target_mismatch",
                    "PR-cycle evidence PR target does not match the Git/PR materialization target",
                    step=step,
                    field=field,
                    expected=expected_value,
                    actual=actual,
                )
            )
    return blockers


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_blockers(step: str, packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not _non_empty_string(packet.get("generated_at")):
        return [
            _issue(
                "pr_cycle_timestamp_missing",
                "PR-cycle evidence steps must include generated_at",
                step=step,
            )
        ]
    try:
        _parse_utc(packet.get("generated_at"))
    except (TypeError, ValueError) as exc:
        return [
            _issue(
                "pr_cycle_timestamp_invalid",
                "PR-cycle step generated_at is invalid",
                step=step,
                generated_at=packet.get("generated_at"),
                error=str(exc),
            )
        ]
    return []


def _chronology_blockers(packets: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    previous_step: str | None = None
    previous_time: datetime | None = None
    for step in (
        "controlled_loop_tick",
        "git_pr_materialization",
        "initial_post_write_gate",
        "review_response_materialization",
        "review_response_post_write_gate",
        "review_thread_resolution_materialization",
        "review_thread_resolution_post_write_gate",
    ):
        packet = packets.get(step)
        if not isinstance(packet, dict):
            continue
        if any(blocker.get("step") == step for blocker in _timestamp_blockers(step, packet)):
            continue
        try:
            timestamp = _parse_utc(packet.get("generated_at"))
        except (TypeError, ValueError):
            continue
        if timestamp is None:
            continue
        if previous_time is not None and timestamp < previous_time:
            blockers.append(
                _issue(
                    "pr_cycle_step_order_invalid",
                    "PR-cycle evidence steps must be in chronological order",
                    previous_step=previous_step,
                    previous_generated_at=previous_time.isoformat().replace("+00:00", "Z"),
                    step=step,
                    generated_at=timestamp.isoformat().replace("+00:00", "Z"),
                )
            )
        previous_step = step
        previous_time = timestamp
    return blockers


def _step(name: str, file_value: Any, packet: Any, blockers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "status": "accepted" if not blockers else "blocked",
        "file": str(file_value) if file_value is not None else None,
        "checksum": checksum_json(packet) if isinstance(packet, dict) else None,
        "blocker_codes": [blocker["code"] for blocker in blockers],
    }


def _gate_materialization_blockers(
    *,
    gate_step: str,
    gate: dict[str, Any],
    materialization_step: str,
    materialization: dict[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    summary = gate.get("materialization") if isinstance(gate.get("materialization"), dict) else {}
    expected_checksum = checksum_json(materialization)
    if summary.get("result_checksum") != expected_checksum:
        blockers.append(
            _issue(
                "pr_cycle_materialization_checksum_mismatch",
                "post-write gate materialization checksum does not match the supplied materialization packet",
                gate_step=gate_step,
                materialization_step=materialization_step,
                expected=expected_checksum,
                actual=summary.get("result_checksum"),
            )
        )
    if summary.get("type") != materialization.get("packet"):
        blockers.append(
            _issue(
                "pr_cycle_materialization_type_mismatch",
                "post-write gate materialization type does not match the supplied materialization packet",
                gate_step=gate_step,
                materialization_step=materialization_step,
                expected=materialization.get("packet"),
                actual=summary.get("type"),
            )
        )
    return blockers


def _recommendation(valid: bool, final_gate: dict[str, Any] | None) -> tuple[str, str]:
    if not valid:
        return "inspect_pr_cycle_blockers", "blocked"
    action = final_gate.get("recommended_next_action") if isinstance(final_gate, dict) else None
    if action == "ready_for_review":
        return "plan_merge_readiness", "ready_for_merge_planning"
    if _non_empty_string(action):
        return str(action), str(action)
    return "operator_review", "operator_review"


def _read_packet(path: Path, *, step: str) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        packet = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [_issue(f"{step}_evidence_unreadable", f"{step} evidence could not be read as JSON", path=str(path), error=str(exc))]
    if not isinstance(packet, dict):
        return None, [_issue(f"{step}_packet_invalid", f"{step} evidence must be a JSON object", path=str(path))]
    return packet, []


def compose_controlled_pr_cycle_from_files(
    *,
    root: Path,
    controlled_loop_tick_file: Path,
    git_pr_materialization_file: Path,
    initial_post_write_gate_file: Path,
    review_response_materialization_file: Path | None = None,
    review_response_post_write_gate_file: Path | None = None,
    review_thread_resolution_materialization_file: Path | None = None,
    review_thread_resolution_post_write_gate_file: Path | None = None,
) -> dict[str, Any]:
    file_inputs: dict[str, Path | None] = {
        "controlled_loop_tick": controlled_loop_tick_file,
        "git_pr_materialization": git_pr_materialization_file,
        "initial_post_write_gate": initial_post_write_gate_file,
        "review_response_materialization": review_response_materialization_file,
        "review_response_post_write_gate": review_response_post_write_gate_file,
        "review_thread_resolution_materialization": review_thread_resolution_materialization_file,
        "review_thread_resolution_post_write_gate": review_thread_resolution_post_write_gate_file,
    }
    packets: dict[str, Any] = {}
    initial_step_blockers: dict[str, list[dict[str, Any]]] = {}
    for step, path in file_inputs.items():
        if path is None:
            continue
        packet, blockers = _read_packet(path, step=step)
        packets[step] = packet
        initial_step_blockers[step] = blockers
    return compose_controlled_pr_cycle(
        root=root,
        controlled_loop_tick=packets.get("controlled_loop_tick"),
        git_pr_materialization=packets.get("git_pr_materialization"),
        initial_post_write_gate=packets.get("initial_post_write_gate"),
        review_response_materialization=packets.get("review_response_materialization"),
        review_response_post_write_gate=packets.get("review_response_post_write_gate"),
        review_thread_resolution_materialization=packets.get("review_thread_resolution_materialization"),
        review_thread_resolution_post_write_gate=packets.get("review_thread_resolution_post_write_gate"),
        files={step: str(path) for step, path in file_inputs.items() if path is not None},
        initial_step_blockers=initial_step_blockers,
    )


def compose_controlled_pr_cycle(
    *,
    root: Path,
    controlled_loop_tick: Any,
    git_pr_materialization: Any,
    initial_post_write_gate: Any,
    review_response_materialization: Any | None = None,
    review_response_post_write_gate: Any | None = None,
    review_thread_resolution_materialization: Any | None = None,
    review_thread_resolution_post_write_gate: Any | None = None,
    files: dict[str, Any] | None = None,
    initial_step_blockers: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(strict=False)
    packets: dict[str, Any] = {
        "controlled_loop_tick": controlled_loop_tick,
        "git_pr_materialization": git_pr_materialization,
        "initial_post_write_gate": initial_post_write_gate,
        "review_response_materialization": review_response_materialization,
        "review_response_post_write_gate": review_response_post_write_gate,
        "review_thread_resolution_materialization": review_thread_resolution_materialization,
        "review_thread_resolution_post_write_gate": review_thread_resolution_post_write_gate,
    }
    files = dict(files or {})
    step_blockers: dict[str, list[dict[str, Any]]] = {step: list((initial_step_blockers or {}).get(step, [])) for step in STEP_ORDER}
    blockers: list[dict[str, Any]] = []
    for step in STEP_ORDER:
        blockers.extend(step_blockers[step])

    for step in REQUIRED_STEP_ORDER:
        if packets.get(step) is None:
            blocker = _issue(f"{step}_missing", f"{step} evidence is required")
            step_blockers[step].append(blocker)
            blockers.append(blocker)

    optional_pairs = (
        (
            "review_response_materialization",
            "review_response_post_write_gate",
            "review_response_post_write_gate_missing",
            "review_response_post_write_gate requires review_response_materialization evidence",
        ),
        (
            "review_thread_resolution_materialization",
            "review_thread_resolution_post_write_gate",
            "thread_resolution_post_write_gate_missing",
            "review_thread_resolution_post_write_gate requires review_thread_resolution_materialization evidence",
        ),
    )
    for materialization_step, gate_step, missing_gate_code, missing_materialization_message in optional_pairs:
        if packets.get(materialization_step) is not None and packets.get(gate_step) is None:
            blocker = _issue(missing_gate_code, f"{gate_step} evidence is required after {materialization_step}")
            step_blockers[gate_step].append(blocker)
            blockers.append(blocker)
        if packets.get(gate_step) is not None and packets.get(materialization_step) is None:
            blocker = _issue(f"{materialization_step}_missing", missing_materialization_message)
            step_blockers[materialization_step].append(blocker)
            blockers.append(blocker)

    expected_types = {
        "controlled_loop_tick": ("controlled_loop_tick", "controlled-loop-tick.v1"),
        "git_pr_materialization": ("git_pr_materialization", "git-pr-materialization.v1"),
        "initial_post_write_gate": ("post_write_pr_evidence_gate", "post-write-pr-evidence-gate.v1"),
        "review_response_materialization": ("review_response_materialization", "review-response-materialization.v1"),
        "review_response_post_write_gate": ("post_write_pr_evidence_gate", "post-write-pr-evidence-gate.v1"),
        "review_thread_resolution_materialization": (
            "review_thread_resolution_materialization",
            "review-thread-resolution-materialization.v1",
        ),
        "review_thread_resolution_post_write_gate": ("post_write_pr_evidence_gate", "post-write-pr-evidence-gate.v1"),
    }
    for step, (expected_packet, expected_schema) in expected_types.items():
        packet = packets.get(step)
        if packet is None:
            continue
        step_type_blockers = _packet_type_blockers(
            packet,
            step=step,
            expected_packet=expected_packet,
            expected_schema=expected_schema,
        )
        step_blockers[step].extend(step_type_blockers)
        blockers.extend(step_type_blockers)

    for step, packet in packets.items():
        if packet is None or not isinstance(packet, dict):
            continue
        time_blockers = _timestamp_blockers(step, packet)
        step_blockers[step].extend(time_blockers)
        blockers.extend(time_blockers)

    for step, packet in packets.items():
        if packet is None or not isinstance(packet, dict):
            continue
        packet_blockers = packet.get("blockers")
        if isinstance(packet_blockers, list) and packet_blockers:
            blocker = _issue(
                "pr_cycle_step_blockers_present",
                "supplied PR-cycle step carries blockers and cannot be composed as accepted evidence",
                step=step,
                blocker_codes=[
                    item.get("code")
                    for item in packet_blockers
                    if isinstance(item, dict) and _non_empty_string(item.get("code"))
                ],
            )
            step_blockers[step].append(blocker)
            blockers.append(blocker)

    if isinstance(controlled_loop_tick, dict):
        if controlled_loop_tick.get("valid") is not True or controlled_loop_tick.get("controlled_tick_status") != "completed":
            blocker = _issue("controlled_loop_tick_not_completed", "controlled loop tick evidence must be valid and completed")
            step_blockers["controlled_loop_tick"].append(blocker)
            blockers.append(blocker)
        if controlled_loop_tick.get("executor_started") is not True:
            blocker = _issue("controlled_loop_tick_executor_not_started", "controlled loop tick must include started executor evidence")
            step_blockers["controlled_loop_tick"].append(blocker)
            blockers.append(blocker)

    if isinstance(controlled_loop_tick, dict) and isinstance(git_pr_materialization, dict):
        loop_checksums = (
            controlled_loop_tick.get("checksums")
            if isinstance(controlled_loop_tick.get("checksums"), dict)
            else {}
        )
        loop_git_pr_plan_checksum = loop_checksums.get("git_pr_plan")
        materialization_plan_checksum = git_pr_materialization.get("plan_checksum")
        if not _non_empty_string(loop_git_pr_plan_checksum):
            blocker = _issue(
                "pr_cycle_git_pr_plan_anchor_missing",
                "controlled loop tick must include the Git/PR plan checksum used by the PR materialization",
                step="controlled_loop_tick",
                field="checksums.git_pr_plan",
            )
            step_blockers["controlled_loop_tick"].append(blocker)
            blockers.append(blocker)
        elif not _non_empty_string(materialization_plan_checksum):
            blocker = _issue(
                "pr_cycle_git_pr_plan_anchor_missing",
                "Git/PR materialization must include the materialized plan checksum",
                step="git_pr_materialization",
                field="plan_checksum",
            )
            step_blockers["git_pr_materialization"].append(blocker)
            blockers.append(blocker)
        elif loop_git_pr_plan_checksum != materialization_plan_checksum:
            blocker = _issue(
                "pr_cycle_git_pr_plan_checksum_mismatch",
                "controlled loop tick Git/PR plan checksum must match the materialized plan checksum",
                expected=loop_git_pr_plan_checksum,
                actual=materialization_plan_checksum,
            )
            step_blockers["git_pr_materialization"].append(blocker)
            blockers.append(blocker)

    materialization_steps = (
        "git_pr_materialization",
        "review_response_materialization",
        "review_thread_resolution_materialization",
    )
    for step in materialization_steps:
        packet = packets.get(step)
        if packet is None or not isinstance(packet, dict):
            continue
        if packet.get("valid") is not True or packet.get("decision") != "materialized":
            blocker = _issue("pr_cycle_materialization_not_materialized", "materialization evidence must be valid and materialized", step=step)
            step_blockers[step].append(blocker)
            blockers.append(blocker)
        if packet.get("approval_state") != "approved":
            blocker = _issue("pr_cycle_materialization_not_approved", "materialization evidence must be operator-approved", step=step)
            step_blockers[step].append(blocker)
            blockers.append(blocker)
        if step != "git_pr_materialization" and packet.get("github_write_started") is not True:
            blocker = _issue("pr_cycle_materialization_write_missing", "review materialization evidence must show a GitHub write", step=step)
            step_blockers[step].append(blocker)
            blockers.append(blocker)
        if step == "git_pr_materialization" and not packet.get("side_effects"):
            blocker = _issue("pr_cycle_materialization_write_missing", "Git/PR materialization evidence must include side effects", step=step)
            step_blockers[step].append(blocker)
            blockers.append(blocker)

    gate_pairs = (
        ("initial_post_write_gate", "git_pr_materialization"),
        ("review_response_post_write_gate", "review_response_materialization"),
        ("review_thread_resolution_post_write_gate", "review_thread_resolution_materialization"),
    )
    for gate_step, materialization_step in gate_pairs:
        gate = packets.get(gate_step)
        materialization = packets.get(materialization_step)
        if gate is None or materialization is None or not isinstance(gate, dict) or not isinstance(materialization, dict):
            continue
        if gate.get("valid") is not True:
            blocker = _issue("pr_cycle_post_write_gate_blocked", "post-write gate evidence must be valid", step=gate_step)
            step_blockers[gate_step].append(blocker)
            blockers.append(blocker)
        if gate.get("github_write_started") is not False:
            blocker = _issue("pr_cycle_post_write_gate_not_read_only", "post-write gate must be read-only", step=gate_step)
            step_blockers[gate_step].append(blocker)
            blockers.append(blocker)
        gate_blockers = _gate_materialization_blockers(
            gate_step=gate_step,
            gate=gate,
            materialization_step=materialization_step,
            materialization=materialization,
        )
        step_blockers[gate_step].extend(gate_blockers)
        blockers.extend(gate_blockers)

    if isinstance(git_pr_materialization, dict):
        expected_target = _git_pr_target(git_pr_materialization)
        for step in (
            "initial_post_write_gate",
            "review_response_materialization",
            "review_response_post_write_gate",
            "review_thread_resolution_materialization",
            "review_thread_resolution_post_write_gate",
        ):
            packet = packets.get(step)
            if packet is None or not isinstance(packet, dict):
                continue
            target = _gate_target(packet) if "post_write_gate" in step else _pr_target(packet)
            target_blockers = _target_blockers(step, target, expected_target)
            step_blockers[step].extend(target_blockers)
            blockers.extend(target_blockers)
            if "post_write_gate" in step:
                refresh_blockers = _target_blockers(f"{step}.refresh", _gate_refresh_target(packet), expected_target)
                step_blockers[step].extend(refresh_blockers)
                blockers.extend(refresh_blockers)

    chronology_blockers = _chronology_blockers(packets)
    for blocker in chronology_blockers:
        step = blocker.get("step")
        if isinstance(step, str) and step in step_blockers:
            step_blockers[step].append(blocker)
        blockers.append(blocker)

    final_gate_name = "initial_post_write_gate"
    if packets.get("review_response_post_write_gate") is not None:
        final_gate_name = "review_response_post_write_gate"
    if packets.get("review_thread_resolution_post_write_gate") is not None:
        final_gate_name = "review_thread_resolution_post_write_gate"
    final_gate = packets.get(final_gate_name) if isinstance(packets.get(final_gate_name), dict) else None

    valid = not blockers
    recommended_next_action, final_recommendation = _recommendation(valid, final_gate)
    checksums = {step: checksum_json(packet) for step, packet in packets.items() if isinstance(packet, dict)}
    materialized_steps = [
        step
        for step in (
            "git_pr_materialization",
            "review_response_materialization",
            "review_thread_resolution_materialization",
        )
        if isinstance(packets.get(step), dict)
    ]
    step_names = [*REQUIRED_STEP_ORDER]
    for step in OPTIONAL_STEP_ORDER:
        if packets.get(step) is not None or step_blockers.get(step):
            step_names.append(step)
    steps = [_step(step, files.get(step), packets.get(step), step_blockers.get(step, [])) for step in step_names]
    pr = _git_pr_target(git_pr_materialization) if isinstance(git_pr_materialization, dict) else {}
    payload: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": CONTROLLED_PR_CYCLE_SCHEMA_VERSION,
        "packet": "controlled_pr_cycle",
        "cycle_id": f"controlled-pr-cycle-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
        "valid": valid,
        "controlled_pr_cycle_status": "completed" if valid else "blocked",
        "reason": (
            "controlled PR-cycle evidence is internally consistent"
            if valid
            else "controlled PR-cycle evidence is incomplete or mismatched"
        ),
        "recommended_next_action": recommended_next_action,
        "final_recommendation": final_recommendation,
        "operator_confirmation_required": not valid,
        "github_write_started": False,
        "side_effects": [],
        "command_trace": [],
        "pr": pr,
        "materialized_steps": materialized_steps,
        "final_post_write_gate": final_gate_name,
        "files": {step: str(path) for step, path in files.items()},
        "checksums": checksums,
        "steps": steps,
        "blockers": blockers,
        "warnings": [],
        "limitations": [
            "composes_existing_local_evidence_only",
            "does_not_start_executor",
            "does_not_retry_executor",
            "does_not_execute_git_commands",
            "does_not_call_github",
            "does_not_post_comments",
            "does_not_update_pr_body",
            "does_not_resolve_review_threads",
            "does_not_merge",
            "does_not_release",
            "does_not_publish_packages",
            "does_not_assign_roles",
            "does_not_schedule_agents",
            "does_not_continue_loop",
        ],
    }
    if valid:
        payload["side_effects"].append("controlled_pr_cycle_audit_appended")
        try:
            audit_record = controlled_pr_cycle_audit_record(payload)
            audit_blockers = validate_controlled_pr_cycle_audit_record(audit_record, 0)
            if audit_blockers:
                raise ValueError(f"controlled_pr_cycle audit record is invalid: {audit_blockers[0]['code']}")
            audit_ref = append_audit_record(root, audit_record)
            audit_ref["payload_checksum"] = audit_record["payload_checksum"]
            payload["audit_record"] = audit_ref
        except Exception as exc:
            payload["valid"] = False
            payload["controlled_pr_cycle_status"] = "blocked"
            payload["reason"] = "controlled PR-cycle audit record could not be written"
            payload["recommended_next_action"] = "recover_controlled_pr_cycle_audit"
            payload["final_recommendation"] = "blocked"
            payload["operator_confirmation_required"] = True
            payload["side_effects"] = []
            payload["blockers"] = [
                _issue(
                    "controlled_pr_cycle_audit_append_failed",
                    "controlled PR-cycle audit record could not be written",
                    error=str(exc),
                )
            ]
    return payload
