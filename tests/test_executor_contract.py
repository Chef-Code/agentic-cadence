import tempfile
import unittest
from pathlib import Path

from codex_cadence.executor_contract import (
    build_executor_task_packet,
    validate_executor_result_evidence,
    validate_executor_task_packet,
)


def valid_snapshot(**overrides):
    snapshot = {
        "id": "snapshot-1",
        "repo": "local/test",
        "cwd": "/tmp/local-test",
        "branch": "main",
        "head": "abc123",
        "ci": "green",
        "open_prs": [],
        "active_pr": None,
        "unresolved_review_threads": None,
        "dirty_worktree": False,
        "known_failures": [],
        "repo_confidence": "high",
        "repo_confidence_drivers": [],
        "readiness_evidence": {
            "source": "local_git",
            "freshness": "local_only",
            "live": False,
            "stale": False,
            "limitations": [
                "open_prs_not_fetched",
                "review_threads_not_fetched",
                "ci_status_operator_supplied",
            ],
        },
        "captured_at": "2999-05-22T00:00:00Z",
    }
    snapshot.update(overrides)
    return snapshot


def valid_candidate(**overrides):
    candidate = {
        "id": "candidate-1",
        "title": "Implement bounded executor task",
        "summary": "Create the smallest generic executor contract slice.",
        "task_type": "execution",
        "bucket": "S",
        "source": "text_marker",
        "drivers": ["ci_verification"],
        "evidence": {"path": "docs/roadmap.md", "line": 317},
    }
    candidate.update(overrides)
    return candidate


def valid_task_packet(tmp):
    return build_executor_task_packet(
        task=valid_candidate(),
        snapshot=valid_snapshot(cwd=str(tmp)),
        repo_path=tmp,
        allowed_paths=["codex_cadence", "tests"],
        required_checks=["python -m unittest tests.test_executor_contract"],
        max_minutes=30,
        max_tasks=1,
        stop_conditions=["brake_not_drive", "operator_stop", "timeout"],
        evidence_path=Path(tmp) / "executor-result.json",
    )


def valid_result(**overrides):
    result = {
        "schema_version": "generic-executor-result.v1",
        "packet": "executor_result",
        "task_id": "candidate-1",
        "executor_id": "fake-executor",
        "started_at": "2999-05-22T00:00:00Z",
        "ended_at": "2999-05-22T00:05:00Z",
        "status": "succeeded",
        "files_changed": ["codex_cadence/executor_contract.py", "tests/test_executor_contract.py"],
        "commands_run": [
            {
                "command": "python -m unittest tests.test_executor_contract",
                "exit_code": 0,
            }
        ],
        "validation_results": [
            {
                "name": "executor-contract-tests",
                "status": "passed",
                "command": "python -m unittest tests.test_executor_contract",
            }
        ],
        "summary": "Fake executor completed the bounded task.",
        "confidence": "high",
        "blockers": [],
        "dirty_worktree": False,
        "resulting_head": "def456",
    }
    result.update(overrides)
    return result


class ExecutorContractTests(unittest.TestCase):
    def test_builds_valid_executor_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))

            valid, reason = validate_executor_task_packet(packet)

            self.assertTrue(valid, reason)
            self.assertEqual(packet["schema_version"], "generic-executor-task.v1")
            self.assertEqual(packet["packet"], "executor_task")
            self.assertEqual(packet["task"]["id"], "candidate-1")
            self.assertEqual(packet["snapshot"]["id"], "snapshot-1")
            self.assertEqual(packet["allowed_paths"], ["codex_cadence", "tests"])
            self.assertEqual(packet["required_checks"], ["python -m unittest tests.test_executor_contract"])
            self.assertEqual(packet["limits"]["max_minutes"], 30)
            self.assertEqual(packet["limits"]["max_tasks"], 1)
            self.assertTrue(str(packet["expected_output"]["evidence_path"]).endswith("executor-result.json"))

    def test_task_packet_rejects_absolute_or_parent_allowed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["allowed_paths"] = ["codex_cadence", "../outside"]

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task allowed_paths[1] must be repo-relative")

            packet["allowed_paths"] = ["codex_cadence", "/absolute"]

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task allowed_paths[1] must be repo-relative")

    def test_task_packet_rejects_unknown_task_type_and_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["task"]["task_type"] = "maintenance"

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task task.task_type must be execution or discovery")

            packet = valid_task_packet(Path(tmp))
            packet["task"]["bucket"] = "XXL"

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task task.bucket must be one of XS, S, M, L, XL")

    def test_accepts_success_failure_and_stopped_result_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                valid_result(status="succeeded", blockers=[], dirty_worktree=False),
                valid_result(status="failed", blockers=["unit test failed"], dirty_worktree=True, resulting_head=None),
                valid_result(status="stopped", blockers=["timeout"], dirty_worktree=True, resulting_head=None),
            ]

            for evidence in cases:
                with self.subTest(status=evidence["status"]):
                    valid, reason = validate_executor_result_evidence(evidence, task_packet)
                    self.assertTrue(valid, reason)

    def test_result_evidence_rejects_disallowed_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(files_changed=["codex_cadence/executor_contract.py", "docs/roadmap.md"])

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result files_changed[1] is outside allowed_paths")

    def test_result_evidence_rejects_dirty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(status="succeeded", dirty_worktree=True)

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result dirty_worktree must be false when status is succeeded")

    def test_result_evidence_rejects_malformed_timestamp_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(
                started_at="2999-05-22T00:05:00Z",
                ended_at="2999-05-22T00:00:00Z",
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result ended_at must be at or after started_at")
