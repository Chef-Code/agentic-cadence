from __future__ import annotations

from copy import deepcopy
from typing import Any


BRANCH_POLICY_LIST_FIELDS = (
    "allowed_base_branches",
    "denied_target_branches",
    "required_branch_prefixes",
)
BRANCH_POLICY_FIELDS = (*BRANCH_POLICY_LIST_FIELDS, "allow_current_branch_main")

DEFAULT_BRANCH_POLICY: dict[str, Any] = {
    "allowed_base_branches": [],
    "denied_target_branches": [],
    "required_branch_prefixes": [],
    "allow_current_branch_main": True,
}


def default_branch_policy(*, allow_current_branch_main: bool = True) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_BRANCH_POLICY)
    policy["allow_current_branch_main"] = allow_current_branch_main
    return policy


def _normalize_string_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return list(dict.fromkeys(item.strip() for item in value))


def normalize_branch_policy(
    value: Any,
    *,
    label: str,
    require_object: bool = False,
    absent_allow_current_branch_main: bool = True,
    missing_allow_current_branch_main: bool = False,
) -> dict[str, Any]:
    if value is None:
        if require_object:
            raise ValueError(f"{label} must be a JSON object")
        return default_branch_policy(allow_current_branch_main=absent_allow_current_branch_main)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    unknown_keys = sorted(str(key) for key in value if key not in BRANCH_POLICY_FIELDS)
    if unknown_keys:
        raise ValueError(f"{label} contains unknown keys: {', '.join(unknown_keys)}")

    normalized = {
        field: _normalize_string_list(value.get(field), label=f"{label}.{field}")
        for field in BRANCH_POLICY_LIST_FIELDS
    }
    allow_main = value.get("allow_current_branch_main", missing_allow_current_branch_main)
    if not isinstance(allow_main, bool):
        raise ValueError(f"{label}.allow_current_branch_main must be a boolean")
    normalized["allow_current_branch_main"] = allow_main
    return normalized
