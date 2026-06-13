import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_cadence.policy_audit import checksum_json
from codex_cadence.pr_readiness import evaluate_pr_readiness
from codex_cadence.pr_cycle import compose_controlled_pr_cycle
from tests.test_pr_cycle import full_pr_cycle_chain
from tests.test_pr_readiness import base_pr, review_threads_payload


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def run_cli(root: Path, *args: str):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def merge_pr():
    return base_pr(
        number=107,
        title="[codex] Add controlled PR-cycle evidence packet",
        headRefName="codex/task-36-controlled-pr-cycle",
        baseRefName="main",
        headRefOid="abc123",
        reviewDecision="APPROVED",
    )


def audit_replay_packet():
    return {
        "protocol_version": "v1",
        "schema_version": "audit-replay.v1",
        "packet": "audit_replay",
        "audit_path": "C:/tmp/runtime/audit/events.jsonl",
        "audit_exists": True,
        "valid": True,
        "lines_seen": 1,
        "records_seen": 1,
        "records_valid": 1,
        "records_invalid": 0,
        "events_by_type": {"controlled_pr_cycle": 1},
        "blockers": [],
        "recommendation": "use_audit_replay_evidence",
    }


def role_readiness_packet(pr, **overrides):
    packet = {
        "protocol_version": "v1",
        "schema_version": "role-readiness.v1",
        "packet": "role_readiness",
        "read_only": True,
        "side_effects": [],
        "valid": True,
        "role_ready": True,
        "recommended_next_action": "use_role_readiness",
        "blockers": [],
        "pr": {
            "path": "pr.json",
            "present": True,
            "checksum": checksum_json(pr),
            "number": pr["number"],
            "head_ref": pr["headRefName"],
            "base_ref": pr["baseRefName"],
            "head_sha": pr["headRefOid"],
            "review_decision": pr["reviewDecision"],
        },
        "review_evidence": {
            "present": True,
            "checksum": checksum_json(review_threads_payload([])),
            "actionable_review_comments": 0,
            "actionable_review_authors": ["reviewer"],
        },
        "role_summary": {
            "review_separation_required": True,
            "builder_claimers": ["builder"],
            "reviewer_claimers": ["reviewer"],
        },
        "limitations": [
            "local_saved_evidence_only",
            "does_not_call_github",
            "does_not_merge_release_or_publish_packages",
        ],
    }
    packet.update(overrides)
    return packet


def controlled_pr_cycle_packet(root: Path):
    chain = full_pr_cycle_chain()
    files = {name: f"{name}.json" for name in chain}
    return compose_controlled_pr_cycle(root=root, files=files, **chain)


def review_threads_for_pr(pr, nodes):
    payload = review_threads_payload(nodes)
    payload["data"]["repository"]["pullRequest"]["number"] = pr["number"]
    return payload


def ready_inputs(root: Path):
    pr = merge_pr()
    review_threads = review_threads_for_pr(pr, [])
    readiness = evaluate_pr_readiness(
        pr,
        required_checks=["Python and protocol checks"],
        required_body_sections=["Summary", "Testing"],
        review_threads=review_threads,
        evidence_captured_at="2026-06-13T10:00:00Z",
        max_evidence_age_minutes=60,
        now="2026-06-13T10:05:00Z",
    )
    return {
        "pr": pr,
        "review_threads": review_threads,
        "pr_readiness": readiness,
        "audit_replay": audit_replay_packet(),
        "controlled_pr_cycle": controlled_pr_cycle_packet(root),
    }


class MergeDecisionTests(unittest.TestCase):
    def test_merge_decision_plan_accepts_fresh_green_bound_evidence(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertTrue(packet["valid"], packet["blockers"])
            self.assertEqual(packet["schema_version"], "merge-decision-plan.v1")
            self.assertEqual(packet["packet"], "merge_decision_plan")
            self.assertTrue(packet["read_only"])
            self.assertEqual(packet["recommended_next_action"], "merge_after_operator_confirmation")
            self.assertFalse(packet["merge_started"])
            self.assertTrue(packet["operator_confirmation_required"])
            self.assertEqual(packet["pr"]["number"], "107")
            self.assertEqual(packet["controlled_pr_cycle"]["status"], "completed")
            self.assertIn("does_not_call_github", packet["limitations"])
            self.assertIn("does_not_merge", packet["limitations"])

    def test_merge_decision_plan_requires_controlled_pr_cycle(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["controlled_pr_cycle"] = None

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "address_blockers")
            self.assertIn("merge_decision_controlled_pr_cycle_missing", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_requires_controlled_pr_cycle_audit_evidence(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["audit_replay"] = {
                **inputs["audit_replay"],
                "events_by_type": {"controlled_pr_cycle": "1"},
            }

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "address_blockers")
            self.assertIn("audit_replay_controlled_pr_cycle_missing", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_requires_versioned_evidence_packets(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["audit_replay"] = {**inputs["audit_replay"], "schema_version": "audit-replay.v0"}
            inputs["controlled_pr_cycle"] = {**inputs["controlled_pr_cycle"], "schema_version": "controlled-pr-cycle.v0"}
            inputs["role_readiness"] = role_readiness_packet(inputs["pr"], schema_version="role-readiness.v0")

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertIn("merge_decision_audit_replay_schema_invalid", {blocker["code"] for blocker in packet["blockers"]})
            self.assertIn(
                "merge_decision_controlled_pr_cycle_schema_invalid",
                {blocker["code"] for blocker in packet["blockers"]},
            )
            self.assertIn("role_readiness_schema_invalid", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_requires_controlled_pr_cycle_audit_reference_binding(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            controlled_pr_cycle = dict(inputs["controlled_pr_cycle"])
            controlled_pr_cycle.pop("audit_record", None)

            packet = plan_merge_decision(root=root, files={}, **{**inputs, "controlled_pr_cycle": controlled_pr_cycle})

            self.assertFalse(packet["valid"])
            self.assertIn("controlled_pr_cycle_audit_reference_invalid", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

            tampered = dict(inputs["controlled_pr_cycle"])
            tampered["audit_record"] = {
                **tampered["audit_record"],
                "payload_checksum": "sha256:" + ("0" * 64),
            }

            packet = plan_merge_decision(root=root, files={}, **{**inputs, "controlled_pr_cycle": tampered})

            self.assertFalse(packet["valid"])
            self.assertIn("controlled_pr_cycle_audit_checksum_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_thin_tampered_ready_readiness_packet(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["pr_readiness"] = {
                "ready_to_merge": True,
                "decision": "ready",
                "recommended_next_action": "merge_after_operator_confirmation",
                "pr": {
                    "number": inputs["pr"]["number"],
                    "head_ref": inputs["pr"]["headRefName"],
                    "base_ref": inputs["pr"]["baseRefName"],
                    "head_sha": inputs["pr"]["headRefOid"],
                },
                "blockers": [],
                "waiting": [],
            }

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertIn("pr_readiness_evidence_incomplete", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_stale_ready_readiness_packet(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            readiness = dict(inputs["pr_readiness"])
            readiness["readiness_evidence"] = {
                **readiness["readiness_evidence"],
                "freshness": "stale",
                "stale": True,
            }
            inputs["pr_readiness"] = readiness

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
            self.assertIn("pr_readiness_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_ready_readiness_that_disagrees_with_pr_review_decision(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["pr"] = {**inputs["pr"], "reviewDecision": "CHANGES_REQUESTED"}

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "respond_to_review")
            self.assertIn("review_changes_requested", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_wrong_pr_review_threads(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["review_threads"] = review_threads_payload([])

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "address_blockers")
            self.assertIn("merge_decision_review_threads_pr_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_unresolved_review_threads(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            review_threads = review_threads_for_pr(
                inputs["pr"],
                [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Please fix this.",
                                    "path": "codex_cadence/merge_decision.py",
                                    "line": 10,
                                    "author": {"login": "reviewer"},
                                    "outdated": False,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                ]
            )
            inputs["review_threads"] = review_threads
            inputs["pr_readiness"] = evaluate_pr_readiness(
                inputs["pr"],
                required_checks=["Python and protocol checks"],
                required_body_sections=["Summary", "Testing"],
                review_threads=review_threads,
                evidence_captured_at="2026-06-13T10:00:00Z",
                max_evidence_age_minutes=60,
                now="2026-06-13T10:05:00Z",
            )

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "respond_to_review")
            self.assertIn("unresolved_review_comment", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_merge_decision_plan_blocks_role_readiness_failure(self):
        from codex_cadence.merge_decision import plan_merge_decision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ready_inputs(root)
            inputs["role_readiness"] = role_readiness_packet(
                inputs["pr"],
                valid=False,
                role_ready=False,
                recommended_next_action="assign_independent_reviewer",
                blockers=[
                    {
                        "code": "reviewer_evidence_missing",
                        "message": "current reviewer evidence is missing",
                    }
                ],
            )

            packet = plan_merge_decision(root=root, files={}, **inputs)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "assign_independent_reviewer")
            self.assertIn("role_readiness_blocked", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(packet["merge_started"])

    def test_cli_merge_decision_plan_reads_saved_packets_without_github_or_merge_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            evidence = Path(tmp) / "evidence"
            inputs = ready_inputs(root)
            paths = {name: write_json(evidence / f"{name}.json", packet) for name, packet in inputs.items()}

            result, output = run_cli(
                root,
                "merge-decision-plan",
                "--pr-json-file",
                str(paths["pr"]),
                "--review-threads-file",
                str(paths["review_threads"]),
                "--pr-readiness-file",
                str(paths["pr_readiness"]),
                "--audit-replay-file",
                str(paths["audit_replay"]),
                "--controlled-pr-cycle-file",
                str(paths["controlled_pr_cycle"]),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"], output["blockers"])
            self.assertEqual(output["packet"], "merge_decision_plan")
            self.assertEqual(output["recommended_next_action"], "merge_after_operator_confirmation")
            self.assertEqual(output["command_trace"], [])
            self.assertFalse(output["merge_started"])
            self.assertIn("does_not_call_github", output["limitations"])
            self.assertIn("does_not_merge", output["limitations"])


if __name__ == "__main__":
    unittest.main()
