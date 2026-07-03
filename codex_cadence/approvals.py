from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from codex_cadence import PROTOCOL_VERSION

OPERATOR_APPROVAL_SCHEMA_VERSION = "operator-approval.v1"
OPERATOR_APPROVAL_VERIFICATION_SCHEMA_VERSION = "operator-approval-verification.v1"
OPERATOR_APPROVAL_PURPOSES = {
    "start_governed_execution",
    "git_pr_materialization",
    "real_executor_invocation",
    "controlled_loop_run_manifest",
    "controlled_loop_runner_execution",
    "controlled_loop_runner_start",
    "controlled_loop_runner_stage_execution",
    "controlled_loop_runner_stage_retry",
    "release",
    "package_publication",
}
MAX_OPERATOR_APPROVAL_WINDOW = timedelta(minutes=60)
HMAC_SIGNATURE_PREFIX = "hmac-sha256:"
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_APPROVAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{2,127}$")
SIGNATURE_PATTERN = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")


def approval_blocker(code: str, message: str, **extra: Any) -> dict[str, Any]:
    blocker = {"code": code, "message": message}
    blocker.update(extra)
    return blocker


def checksum_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def operator_approval_signature_payload(approval: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in approval.items() if key != "signature"}


def operator_approval_signature(approval: dict[str, Any], secret: str | bytes) -> str:
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = secret
    body = json.dumps(
        operator_approval_signature_payload(approval),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return HMAC_SIGNATURE_PREFIX + hmac.new(secret_bytes, body, hashlib.sha256).hexdigest()


def parse_approval_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def operator_approval_recommendation(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "use_operator_approval_evidence"
    return "fix_operator_approval"


def operator_approval_reason(valid: bool, blockers: list[dict[str, Any]]) -> str:
    if valid:
        return "operator approval identity evidence accepted"
    if blockers:
        return blockers[0]["message"]
    return "operator approval identity evidence blocked"


def build_operator_approval_verification_packet(
    *,
    approval: Any,
    approval_file: Path,
    expected_target_checksum: str,
    expected_purpose: str,
    approval_secret: str | bytes | None,
    expected_operator_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    blockers: list[dict[str, Any]] = []
    signature_verified = False
    approval_checksum = checksum_json(approval) if isinstance(approval, dict) else None
    approval_fields = approval if isinstance(approval, dict) else {}

    if not isinstance(approval, dict):
        blockers.append(
            approval_blocker(
                "operator_approval_invalid",
                "operator approval packet must be a JSON object",
            )
        )
    else:
        schema_version = approval.get("schema_version")
        if schema_version != OPERATOR_APPROVAL_SCHEMA_VERSION:
            blockers.append(
                approval_blocker(
                    "operator_approval_schema_invalid",
                    "operator approval schema_version must be operator-approval.v1",
                    expected_schema_version=OPERATOR_APPROVAL_SCHEMA_VERSION,
                    actual_schema_version=schema_version,
                )
            )

        target_checksum = approval.get("target_checksum")
        if not isinstance(target_checksum, str) or CHECKSUM_PATTERN.fullmatch(target_checksum) is None:
            blockers.append(
                approval_blocker(
                    "operator_approval_target_invalid",
                    "operator approval target_checksum must be a sha256 checksum",
                )
            )
        elif target_checksum != expected_target_checksum:
            blockers.append(
                approval_blocker(
                    "operator_approval_target_mismatch",
                    "operator approval target_checksum does not match requested target",
                    expected_target_checksum=expected_target_checksum,
                    actual_target_checksum=target_checksum,
                )
            )

        if CHECKSUM_PATTERN.fullmatch(expected_target_checksum) is None:
            blockers.append(
                approval_blocker(
                    "operator_approval_target_invalid",
                    "requested target_checksum must be a sha256 checksum",
                    actual_target_checksum=expected_target_checksum,
                )
            )

        purpose = approval.get("purpose")
        if not isinstance(purpose, str) or not purpose:
            blockers.append(
                approval_blocker(
                    "operator_approval_purpose_missing",
                    "operator approval purpose is required",
                )
            )
        elif (
            not isinstance(expected_purpose, str)
            or expected_purpose not in OPERATOR_APPROVAL_PURPOSES
            or purpose not in OPERATOR_APPROVAL_PURPOSES
            or purpose != expected_purpose
        ):
            blockers.append(
                approval_blocker(
                    "operator_approval_purpose_mismatch",
                    "operator approval purpose does not match a supported requested purpose",
                    expected_purpose=expected_purpose,
                    actual_purpose=purpose,
                )
            )

        expected_operator = expected_operator_id.strip() if isinstance(expected_operator_id, str) else None
        if expected_operator_id is not None and not expected_operator:
            blockers.append(
                approval_blocker(
                    "operator_approval_expected_operator_invalid",
                    "expected operator_id must be a non-empty string",
                )
            )

        operator_id = approval.get("operator_id")
        if not isinstance(operator_id, str) or not operator_id.strip():
            blockers.append(
                approval_blocker(
                    "operator_approval_operator_missing",
                    "operator approval operator_id is required",
                )
            )
        elif expected_operator is not None and operator_id.strip() != expected_operator:
            blockers.append(
                approval_blocker(
                    "operator_approval_operator_mismatch",
                    "operator approval operator_id does not match the expected operator",
                    expected_operator_id=expected_operator,
                    actual_operator_id=operator_id.strip(),
                )
            )

        key_id = approval.get("key_id")
        if not isinstance(key_id, str) or SAFE_APPROVAL_ID_PATTERN.fullmatch(key_id) is None:
            blockers.append(
                approval_blocker(
                    "operator_approval_key_id_weak",
                    "operator approval key_id must be a stable non-empty local key identifier",
                )
            )

        issued_at = parse_approval_timestamp(approval.get("issued_at"))
        expires_at = parse_approval_timestamp(approval.get("expires_at"))
        if issued_at is None or expires_at is None:
            blockers.append(
                approval_blocker(
                    "operator_approval_timestamp_invalid",
                    "operator approval issued_at and expires_at must be timezone-aware ISO-8601 timestamps",
                )
            )
        elif expires_at <= issued_at:
            blockers.append(
                approval_blocker(
                    "operator_approval_timestamp_invalid",
                    "operator approval expires_at must be after issued_at",
                )
            )
        else:
            if expires_at - issued_at > MAX_OPERATOR_APPROVAL_WINDOW:
                blockers.append(
                    approval_blocker(
                        "operator_approval_window_too_long",
                        "operator approval validity window must not exceed 60 minutes",
                        max_window_seconds=int(MAX_OPERATOR_APPROVAL_WINDOW.total_seconds()),
                    )
                )
            if expires_at <= checked_at:
                blockers.append(
                    approval_blocker(
                        "operator_approval_expired",
                        "operator approval has expired",
                        expires_at=approval.get("expires_at"),
                    )
                )
            if issued_at > checked_at:
                blockers.append(
                    approval_blocker(
                        "operator_approval_issued_in_future",
                        "operator approval issued_at is in the future",
                        issued_at=approval.get("issued_at"),
                    )
                )

        signature = approval.get("signature")
        if not isinstance(signature, str) or SIGNATURE_PATTERN.fullmatch(signature) is None:
            blockers.append(
                approval_blocker(
                    "operator_approval_signature_invalid",
                    "operator approval signature must be an hmac-sha256 digest",
                )
            )
        elif not approval_secret:
            blockers.append(
                approval_blocker(
                    "operator_approval_secret_missing",
                    "operator approval signature cannot be verified without an approval secret",
                )
            )
        else:
            expected_signature = operator_approval_signature(approval, approval_secret)
            signature_verified = hmac.compare_digest(signature, expected_signature)
            if not signature_verified:
                blockers.append(
                    approval_blocker(
                        "operator_approval_signature_invalid",
                        "operator approval signature does not verify against the signed approval fields",
                    )
                )

    valid = not blockers
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": OPERATOR_APPROVAL_VERIFICATION_SCHEMA_VERSION,
        "approval_schema_version": approval_fields.get("schema_version"),
        "packet": "operator_approval_verification",
        "read_only": False,
        "valid": valid,
        "approval_state": "approved" if valid else "blocked",
        "approval_file": str(approval_file),
        "target_checksum": expected_target_checksum,
        "approval_target_checksum": approval_fields.get("target_checksum"),
        "approval_checksum": approval_checksum,
        "purpose": expected_purpose,
        "approval_purpose": approval_fields.get("purpose"),
        "operator_id": approval_fields.get("operator_id"),
        "key_id": approval_fields.get("key_id"),
        "issued_at": approval_fields.get("issued_at"),
        "expires_at": approval_fields.get("expires_at"),
        "signature_verified": signature_verified,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "epoch_started": False,
        "executor_started": False,
        "pr_action_started": False,
        "merge_started": False,
        "release_started": False,
        "package_publish_started": False,
        "blockers": blockers,
        "recommended_next_action": operator_approval_recommendation(blockers),
        "reason": operator_approval_reason(valid, blockers),
        "side_effects": [],
        "limitations": [
            "approval_not_execution_authority",
            "executor_not_started",
            "git_pr_writes_not_started",
            "merge_release_publish_out_of_scope",
            "external_identity_provider_out_of_scope",
        ],
    }
    return {key: value for key, value in payload.items() if value is not None}
