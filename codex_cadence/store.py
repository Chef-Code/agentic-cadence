from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import PROTOCOL_VERSION

HANDOFF_STATES = ("ready", "claimed", "completed", "failed")
BRAKE_STATUSES = ("DRIVE", "NEUTRAL", "PARK")
EPOCH_STATES = ("active", "completed", "failed")
SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_root() -> Path:
    cadence_configured = os.environ.get("CODEX_CADENCE_ROOT")
    legacy_configured = os.environ.get("CODEX_TRANSMISSION_ROOT")
    if cadence_configured and legacy_configured:
        cadence_path = Path(cadence_configured).expanduser()
        legacy_path = Path(legacy_configured).expanduser()
        if cadence_path.resolve(strict=False) != legacy_path.resolve(strict=False):
            raise RuntimeError(
                "CODEX_CADENCE_ROOT and CODEX_TRANSMISSION_ROOT point to different roots; set only one"
            )
        return cadence_path
    configured = cadence_configured or legacy_configured
    if configured:
        return Path(configured).expanduser()
    codex_home = Path.home() / ".codex"
    legacy_root = codex_home / "transmission"
    cadence_root = codex_home / "cadence"
    if legacy_root.exists() and cadence_root.exists():
        raise RuntimeError(
            "both default runtime roots exist; set --root, CODEX_CADENCE_ROOT, or CODEX_TRANSMISSION_ROOT"
        )
    if legacy_root.exists():
        return legacy_root
    return cadence_root


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_record_id(record_id: str, kind: str) -> str:
    if not isinstance(record_id, str) or not record_id:
        raise ValueError(f"{kind} id is required")
    if "/" in record_id or "\\" in record_id or ".." in record_id:
        raise ValueError(f"{kind} id cannot contain path traversal or separators")
    if not SAFE_RECORD_ID.fullmatch(record_id):
        raise ValueError(f"{kind} id contains unsupported characters")
    if record_id.endswith((".", " ")):
        raise ValueError(f"{kind} id cannot end with a dot or space")
    if record_id.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{kind} id uses a reserved Windows filename")
    return record_id


def brake_path(root: Path) -> Path:
    return root / "brake.json"


def handoff_state_dir(root: Path, state: str) -> Path:
    return root / "handoffs" / state


def handoff_path(root: Path, state: str, handoff_id: str) -> Path:
    return handoff_state_dir(root, state) / f"{validate_record_id(handoff_id, 'handoff')}.json"


def approval_path(root: Path, handoff_id: str) -> Path:
    return root / "approvals" / f"{validate_record_id(handoff_id, 'handoff')}.json"


def snapshot_path(root: Path, snapshot_id: str) -> Path:
    return root / "snapshots" / f"{validate_record_id(snapshot_id, 'snapshot')}.json"


def epoch_state_dir(root: Path, state: str) -> Path:
    return root / "epochs" / state


def epoch_path(root: Path, state: str, epoch_id: str) -> Path:
    return epoch_state_dir(root, state) / f"{validate_record_id(epoch_id, 'epoch')}.json"


def lock_path(root: Path, name: str) -> Path:
    return root / "locks" / f"{validate_record_id(name, 'lock')}.lock"


def record_lock_path(root: Path, kind: str, record_id: str) -> Path:
    validate_record_id(record_id, kind)
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:32]
    return lock_path(root, f"{kind}-{digest}")


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"lock already held: {path.name}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def ensure_layout(root: Path) -> None:
    for state in HANDOFF_STATES:
        handoff_state_dir(root, state).mkdir(parents=True, exist_ok=True)
    for state in EPOCH_STATES:
        epoch_state_dir(root, state).mkdir(parents=True, exist_ok=True)
    (root / "approvals").mkdir(parents=True, exist_ok=True)
    (root / "audit").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "clean-square").mkdir(parents=True, exist_ok=True)
    (root / "snapshots").mkdir(parents=True, exist_ok=True)
    (root / "plans").mkdir(parents=True, exist_ok=True)
    if not brake_path(root).exists():
        atomic_write_json(
            brake_path(root),
            {
                "status": "DRIVE",
                "reason": None,
                "scope": "global",
                "resume_requires": None,
                "updated_at": utc_now(),
            },
        )


def read_brake(root: Path) -> dict[str, Any]:
    ensure_layout(root)
    brake = read_json(brake_path(root))
    status = brake.get("status")
    if status not in BRAKE_STATUSES:
        raise ValueError(f"Invalid brake status in {brake_path(root)}: {status}")
    return brake
