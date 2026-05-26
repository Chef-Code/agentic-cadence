from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION
from codex_cadence.model import estimate_task
from codex_cadence.repo_state import snapshot_repo
from codex_cadence.store import (
    HANDOFF_STATES,
    atomic_write_json,
    ensure_layout,
    exclusive_lock,
    handoff_path,
    handoff_state_dir,
    lock_path,
    read_brake,
    read_json,
    snapshot_path,
    utc_now,
    validate_record_id,
)


def _format_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "unknown" if value is None else str(value)


def _join_drivers(drivers: Any) -> str:
    if not isinstance(drivers, list) or not drivers:
        return "none"
    return ", ".join(str(driver) for driver in drivers)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "handoff")[:48].strip("-") or "handoff"


def _checksum_message(message: str) -> str:
    return "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()


def _checksum_json(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_signature(handoff_id: str, checksum: str, status: str = "READY") -> str:
    return f"<!-- codex-handoff:{PROTOCOL_VERSION} id={handoff_id} status={status} sha={checksum.removeprefix('sha256:')} -->"


def _checksum_estimate_binding(
    *,
    title: str,
    message: str,
    source: dict[str, Any],
    estimate: dict[str, Any],
) -> str:
    return _checksum_json(
        {
            "title": title,
            "message_checksum": _checksum_message(message),
            "estimate_input": source,
            "estimate": estimate,
        }
    )


def _cadence_state(brake: dict[str, Any]) -> dict[str, Any]:
    legacy_brake = brake["status"]
    state_by_brake = {
        "DRIVE": "PLAY_ON",
        "NEUTRAL": "HUDDLE",
        "PARK": "TIMEOUT",
    }
    return {
        "state": state_by_brake[legacy_brake],
        "legacy_brake": legacy_brake,
        "can_start_work": legacy_brake == "DRIVE",
        "requires_operator_resume": legacy_brake == "PARK",
    }


def _status_payload(root: Path, brake: dict[str, Any]) -> dict[str, Any]:
    counts = {
        state: len(list(handoff_state_dir(root, state).glob("*.json")))
        for state in HANDOFF_STATES
    }
    return {
        "root": str(root),
        "brake": brake,
        "cadence": _cadence_state(brake),
        "counts": counts,
    }


def discover_remote_url(cwd: str | Path, remote: str = "origin") -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=Path(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _snapshot_id(snapshot: dict[str, Any], repo: str | None) -> str:
    stamp_source = snapshot.get("captured_at")
    if not isinstance(stamp_source, str) or not stamp_source:
        stamp_source = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = stamp_source.replace(":", "").replace("-", "")
    basis = repo or snapshot.get("branch") or "repo"
    return f"{stamp}-{_slugify(str(basis))}-{secrets.token_hex(4)}"


def _validate_handoff_record(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    required = ("protocol_version", "id", "status", "checksum", "signature", "message")
    for key in required:
        if key not in data:
            errors.append(f"missing {key}")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"unsupported protocol_version: {data.get('protocol_version')}")
    if "message" in data and "checksum" in data:
        actual = _checksum_message(data["message"])
        if actual != data["checksum"]:
            errors.append("checksum mismatch")
    if "id" in data and "checksum" in data and "signature" in data:
        expected = _create_signature(data["id"], data["checksum"], data.get("status", "READY"))
        ready_expected = _create_signature(data["id"], data["checksum"], "READY")
        if data["signature"] not in {expected, ready_expected}:
            errors.append("signature mismatch")
    return {
        "valid": not errors,
        "errors": errors,
        "id": data.get("id"),
    }


def _create_clean_square_record(root: Path, handoff: dict[str, Any], summary: str) -> dict[str, Any]:
    handoff_id = validate_record_id(str(handoff.get("id")), "handoff")
    now = utc_now()
    target = root / "logs" / "clean-square" / f"{handoff_id}.json"
    data = {
        "handoff_id": handoff_id,
        "handoff_status": handoff.get("status"),
        "summary": summary,
        "created_at": now,
        "path": str(target),
        "checks": {
            "handoff_written": True,
            "signature_present": bool(handoff.get("signature")),
            "next_session_can_resume": handoff.get("status") in {"READY", "CLAIMED", "COMPLETED"},
        },
    }
    _write_json_once(target, data)
    return data


def _write_json_once(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        if fd is not None:
            os.close(fd)
        if created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def _handoff_conflicts(root: Path, handoff_id: str) -> list[str]:
    conflicts = []
    for state in HANDOFF_STATES:
        if handoff_path(root, state, handoff_id).exists():
            conflicts.append(state)
    return conflicts


def _ensure_handoff_id_available(root: Path, handoff_id: str) -> None:
    conflicts = _handoff_conflicts(root, handoff_id)
    if conflicts:
        states = ", ".join(conflicts)
        raise FileExistsError(f"handoff already exists: {handoff_id} ({states})")


def build_seed_message(
    *,
    title: str,
    summary: str,
    guardrail: str,
    snapshot: dict[str, Any],
    status_payload: dict[str, Any],
    remote_url: str | None,
    next_actions: list[str],
) -> str:
    cadence = status_payload.get("cadence", {})
    counts = status_payload.get("counts", {})
    actions = next_actions or [
        "Run python scripts\\cadence.py status and confirm Cadence is PLAY_ON.",
        "Run python scripts\\cadence.py next-handoff and inspect this seed.",
        "Claim the handoff only if pickup policy allows it.",
    ]

    lines = [
        "Seed for new Codex context window:",
        "",
        f"Task: {title}",
        f"Guardrail: {guardrail}",
        f"Summary: {summary}",
        "",
        "Repository state:",
        f"- Path: {snapshot.get('cwd')}",
        f"- Repo: {snapshot.get('repo')}",
        f"- Remote: {remote_url or 'unknown'}",
        f"- Branch: {snapshot.get('branch')}",
        f"- Head: {snapshot.get('head')}",
        f"- Dirty worktree: {_format_bool(snapshot.get('dirty_worktree'))}",
        f"- Repo confidence: {snapshot.get('repo_confidence')}",
        f"- Repo confidence drivers: {_join_drivers(snapshot.get('repo_confidence_drivers'))}",
        f"- Snapshot: {snapshot.get('path')}",
        "",
        "Cadence state:",
        f"- Runtime root: {status_payload.get('root')}",
        f"- State: {cadence.get('state')}",
        f"- Legacy brake: {cadence.get('legacy_brake')}",
        f"- Ready handoffs: {counts.get('ready')}",
        f"- Claimed handoffs: {counts.get('claimed')}",
        f"- Completed handoffs: {counts.get('completed')}",
        f"- Failed handoffs: {counts.get('failed')}",
        "",
        "New session first actions:",
    ]
    lines.extend(f"{index}. {action}" for index, action in enumerate(actions, start=1))
    lines.extend(
        [
            "",
            "Operating rules:",
            "- Do not auto-merge without explicit operator instruction.",
            "- Do not spend elected review unless the current guardrail allows it.",
            "- Keep the continuation PR-sized and bounded to the handoff objective.",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare_handoff(
    *,
    root: Path,
    cwd: Path,
    handoff_id: str,
    title: str,
    guardrail: str,
    repo: str | None,
    branch: str | None,
    task_type: str,
    drivers: list[str],
    summary: str,
    ci_status: str,
    next_actions: list[str],
) -> dict[str, Any]:
    root = Path(root)
    cwd = Path(cwd)
    ensure_layout(root)
    target = handoff_path(root, "ready", handoff_id)
    _ensure_handoff_id_available(root, handoff_id)

    with exclusive_lock(lock_path(root, "runtime")):
        _ensure_handoff_id_available(root, handoff_id)
        brake = read_brake(root)
        status_payload = _status_payload(root, brake)
        if not status_payload["cadence"]["can_start_work"]:
            raise ValueError(f"prepare-handoff requires Cadence PLAY_ON; current state is {status_payload['cadence']['state']}")

        snapshot = snapshot_repo(cwd, repo=repo, ci_status=ci_status)
        snapshot["id"] = _snapshot_id(snapshot, repo)
        snapshot_target = snapshot_path(root, snapshot["id"])
        if snapshot_target.exists():
            raise FileExistsError(f"snapshot already exists: {snapshot['id']}")
        snapshot["path"] = str(snapshot_target)
        atomic_write_json(snapshot_target, snapshot)

        remote_url = discover_remote_url(cwd)
        message = build_seed_message(
            title=title,
            summary=summary,
            guardrail=guardrail,
            snapshot=snapshot,
            status_payload=status_payload,
            remote_url=remote_url,
            next_actions=next_actions,
        )
        source = {"task_type": task_type, "drivers": list(drivers or [])}
        estimate = estimate_task(
            title=title,
            message=message,
            task_type=task_type,
            drivers=drivers or [],
        )
        now = utc_now()
        checksum = _checksum_message(message)
        handoff = {
            "protocol_version": PROTOCOL_VERSION,
            "id": handoff_id,
            "title": title,
            "status": "READY",
            "guardrail": guardrail,
            "repo": repo,
            "branch": branch or snapshot.get("branch"),
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "checksum": checksum,
            "signature": _create_signature(handoff_id, checksum),
            "message": message,
            "estimate": estimate,
            "estimate_input": source,
            "estimate_checksum": _checksum_estimate_binding(
                title=title,
                message=message,
                source=source,
                estimate=estimate,
            ),
        }

        validation = _validate_handoff_record(handoff)
        if not validation["valid"]:
            raise ValueError(f"generated handoff failed validation: {validation['errors']}")
        clean_square: dict[str, Any] | None = None
        published = False
        try:
            clean_square = _create_clean_square_record(root, handoff, summary)
            _ensure_handoff_id_available(root, handoff_id)
            _write_json_once(target, handoff)
            published = True
            persisted = read_json(target)
            validation = _validate_handoff_record(persisted)
            if not validation["valid"]:
                raise ValueError(f"generated handoff failed validation: {validation['errors']}")
        except Exception:
            if published:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            if clean_square and clean_square.get("path"):
                try:
                    Path(str(clean_square["path"])).unlink()
                except FileNotFoundError:
                    pass
            raise

    return {
        "handoff": persisted,
        "snapshot": snapshot,
        "validation": validation,
        "clean_square": clean_square,
        "stop_current_session": True,
    }
