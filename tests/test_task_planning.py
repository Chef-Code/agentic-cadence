import unittest

from codex_cadence.model import (
    DEFAULT_EPOCH_POLICY,
    bucket_for_score,
    classify_uncertainty,
    epoch_health,
    estimate_task,
    governance_permissions,
    policy_for_bucket,
    score_task,
)


class TaskPlanningTests(unittest.TestCase):
    def test_execution_task_with_clear_scope_is_small(self):
        estimate = estimate_task(
            title="Fix reviewer typo",
            message="Change one reviewer-requested label.",
            task_type="execution",
            drivers=["reviewer_feedback"],
        )

        self.assertEqual(estimate["bucket"], "S")
        self.assertEqual(estimate["policy"]["pickup_requires_approval"], False)
        self.assertEqual(estimate["task_type"], "execution")

    def test_discovery_task_is_penalized(self):
        estimate = estimate_task(
            title="Investigate architecture inconsistency",
            message="Assess whether the current architecture should be changed.",
            task_type="discovery",
            drivers=["unknown_repo_area", "cross_subsystem", "unclear_requirements"],
        )

        self.assertIn(estimate["bucket"], {"L", "XL"})
        self.assertEqual(estimate["policy"]["pickup_requires_approval"], True)
        self.assertEqual(estimate["uncertainty"]["level"], "high")

    def test_policy_for_medium_requires_checkpoint(self):
        policy = policy_for_bucket("M")

        self.assertEqual(policy["check_in_every_minutes"], 15)
        self.assertEqual(policy["handoff_after_minutes"], 45)
        self.assertFalse(policy["pickup_requires_approval"])

    def test_policy_for_xl_blocks_pickup(self):
        policy = policy_for_bucket("XL")

        self.assertTrue(policy["pickup_requires_approval"])
        self.assertEqual(policy["action"], "decompose_or_ask_approval")

    def test_all_bucket_policies_are_defined(self):
        expected_policies = {
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

        for bucket, expected in expected_policies.items():
            with self.subTest(bucket=bucket):
                self.assertEqual(policy_for_bucket(bucket), expected)

    def test_score_boundaries_map_to_buckets(self):
        cases = [
            (0, "XS"),
            (9, "XS"),
            (10, "S"),
            (29, "S"),
            (30, "M"),
            (54, "M"),
            (55, "L"),
            (84, "L"),
            (85, "XL"),
            (100, "XL"),
        ]

        for score, bucket in cases:
            with self.subTest(score=score):
                self.assertEqual(bucket_for_score(score), bucket)

    def test_invalid_task_inputs_raise_value_error(self):
        with self.assertRaises(ValueError):
            policy_for_bucket("XXL")

        with self.assertRaises(ValueError):
            estimate_task(
                title="Unsupported task",
                message="Try to classify unsupported task type.",
                task_type="research",
            )

        with self.assertRaises(ValueError):
            estimate_task(
                title=" ",
                message="Missing a usable title.",
                task_type="execution",
            )

        with self.assertRaises(ValueError):
            estimate_task(
                title="Missing message",
                message=" ",
                task_type="execution",
            )

    def test_unknown_driver_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported task driver: typo_driver"):
            score_task("execution", ["typo_driver"])

    def test_driver_lists_are_not_shared_with_callers_or_each_other(self):
        drivers = ["reviewer_feedback"]

        estimate = estimate_task(
            title="Fix reviewer typo",
            message="Change one reviewer-requested label.",
            task_type="execution",
            drivers=drivers,
        )
        uncertainty = classify_uncertainty(10, drivers)
        drivers.append("ci_verification")

        self.assertEqual(estimate["drivers"], ["reviewer_feedback"])
        self.assertEqual(estimate["uncertainty"]["drivers"], ["reviewer_feedback"])
        self.assertEqual(uncertainty["drivers"], ["reviewer_feedback"])

        estimate["drivers"].append("external_review")

        self.assertEqual(estimate["uncertainty"]["drivers"], ["reviewer_feedback"])

    def test_uncertainty_levels(self):
        self.assertEqual(classify_uncertainty(10)["level"], "low")
        self.assertEqual(classify_uncertainty(45)["level"], "medium")
        self.assertEqual(classify_uncertainty(80)["level"], "high")

    def test_epoch_health_degraded_on_ci_oscillation(self):
        health = epoch_health(
            ci_oscillation=True,
            diff_churn="high",
            candidate_growth="high",
            review_disagreement="moderate",
            rollback_risk="medium",
        )

        self.assertEqual(health["status"], "degraded")

    def test_epoch_health_rejects_unknown_indicator_values(self):
        cases = [
            ("diff_churn", "extreme"),
            ("candidate_growth", "explosive"),
            ("review_disagreement", "severe"),
            ("rollback_risk", "unknown"),
        ]

        for field, value in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f"Unsupported {field}: {value}"):
                    epoch_health(**{field: value})

    def test_governance_defaults_are_conservative(self):
        permissions = governance_permissions()

        self.assertFalse(permissions["may_modify_execution_logic"])
        self.assertFalse(permissions["may_modify_protocol_rules"])
        self.assertFalse(permissions["may_modify_governance_rules"])
        self.assertTrue(permissions["may_propose_protocol_changes"])
        self.assertFalse(DEFAULT_EPOCH_POLICY["allow_recursive_discovery"])


if __name__ == "__main__":
    unittest.main()
