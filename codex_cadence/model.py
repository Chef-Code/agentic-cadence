from __future__ import annotations

from typing import Any

TASK_TYPES = ("execution", "discovery")
BUCKETS = ("XS", "S", "M", "L", "XL")

DEFAULT_EPOCH_POLICY = {
    "max_tasks_per_epoch": 3,
    "max_minutes_per_epoch": 60,
    "max_epochs_without_user_approval": 1,
    "allow_self_evolution": "propose_only",
    "next_epoch_requires": "green_ci_or_explicit_handoff",
    "max_discovery_tasks_per_epoch": 1,
    "allow_recursive_discovery": False,
}

DRIVER_WEIGHTS = {
    "reviewer_feedback": 10,
    "ci_verification": 15,
    "external_review": 15,
    "multiple_files": 15,
    "unknown_repo_area": 25,
    "unclear_requirements": 25,
    "cross_subsystem": 30,
    "migration": 35,
    "irreversible_operation": 40,
    "self_evolution": 35,
}

POLICIES = {
    "XS": {
        "check_in_every_minutes": 5,
        "handoff_after_minutes": 10,
        "pickup_requires_approval": False,
        "action": "pick_up",
    },
    "S": {
        "check_in_every_minutes": 10,
        "handoff_after_minutes": 25,
        "pickup_requires_approval": False,
        "action": "pick_up",
    },
    "M": {
        "check_in_every_minutes": 15,
        "handoff_after_minutes": 45,
        "pickup_requires_approval": False,
        "action": "pick_up_with_checkpoint",
    },
    "L": {
        "check_in_every_minutes": 20,
        "handoff_after_minutes": 60,
        "pickup_requires_approval": True,
        "action": "split_or_require_handoff_plan",
    },
    "XL": {
        "check_in_every_minutes": 30,
        "handoff_after_minutes": 60,
        "pickup_requires_approval": True,
        "action": "decompose_or_ask_approval",
    },
}

MINUTES = {
    "XS": {"min": 0, "max": 10},
    "S": {"min": 10, "max": 30},
    "M": {"min": 30, "max": 60},
    "L": {"min": 60, "max": 180},
    "XL": {"min": 180, "max": None},
}


def policy_for_bucket(bucket: str) -> dict[str, Any]:
    if bucket not in POLICIES:
        raise ValueError(f"Unsupported bucket: {bucket}")
    return dict(POLICIES[bucket])


def bucket_for_score(score: int) -> str:
    if score < 10:
        return "XS"
    if score < 30:
        return "S"
    if score < 55:
        return "M"
    if score < 85:
        return "L"
    return "XL"


def classify_uncertainty(score: int, drivers: list[str] | None = None) -> dict[str, Any]:
    if score >= 70:
        level = "high"
    elif score >= 30:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "score": score, "drivers": list(drivers or [])}


def score_task(task_type: str, drivers: list[str]) -> int:
    if task_type not in TASK_TYPES:
        raise ValueError(f"Unsupported task_type: {task_type}")

    score = 0
    for driver in drivers:
        if driver not in DRIVER_WEIGHTS:
            raise ValueError(f"Unsupported task driver: {driver}")
        score += DRIVER_WEIGHTS[driver]
    if task_type == "discovery":
        score += 30
    return min(score, 100)


def confidence_for_score(score: int) -> str:
    if score >= 70:
        return "low"
    if score >= 30:
        return "medium"
    return "high"


def estimate_task(
    title: str,
    message: str,
    task_type: str,
    drivers: list[str] | None = None,
) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("title is required")
    if not message.strip():
        raise ValueError("message is required")

    selected_drivers = list(drivers or [])
    score = score_task(task_type, selected_drivers)
    bucket = bucket_for_score(score)
    return {
        "task_type": task_type,
        "bucket": bucket,
        "confidence": confidence_for_score(score),
        "score": score,
        "expected_minutes": dict(MINUTES[bucket]),
        "drivers": list(selected_drivers),
        "uncertainty": classify_uncertainty(score, selected_drivers),
        "policy": policy_for_bucket(bucket),
    }


def _validated_indicator_score(field: str, value: str, scores: dict[str, int]) -> int:
    if value not in scores:
        raise ValueError(f"Unsupported {field}: {value}")
    return scores[value]


def epoch_health(
    ci_oscillation: bool = False,
    diff_churn: str = "low",
    candidate_growth: str = "low",
    review_disagreement: str = "none",
    rollback_risk: str = "low",
) -> dict[str, Any]:
    score = 100
    score -= 30 if ci_oscillation else 0
    score -= _validated_indicator_score("diff_churn", diff_churn, {"low": 0, "medium": 15, "high": 30})
    score -= _validated_indicator_score("candidate_growth", candidate_growth, {"low": 0, "medium": 10, "high": 20})
    score -= _validated_indicator_score(
        "review_disagreement",
        review_disagreement,
        {"none": 0, "minor": 10, "moderate": 20, "high": 30},
    )
    score -= _validated_indicator_score("rollback_risk", rollback_risk, {"low": 0, "medium": 15, "high": 30})

    if score < 50:
        status = "degraded"
    elif score < 80:
        status = "watch"
    else:
        status = "good"

    return {
        "status": status,
        "score": max(score, 0),
        "indicators": {
            "ci_oscillation": ci_oscillation,
            "diff_churn": diff_churn,
            "candidate_growth": candidate_growth,
            "review_disagreement": review_disagreement,
            "rollback_risk": rollback_risk,
        },
    }


def governance_permissions() -> dict[str, bool]:
    return {
        "may_modify_execution_logic": False,
        "may_modify_protocol_rules": False,
        "may_modify_governance_rules": False,
        "may_propose_protocol_changes": True,
    }
