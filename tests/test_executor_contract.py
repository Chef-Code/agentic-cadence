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
        "resulting_head": "abc123",
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

    def test_task_packet_rejects_wrong_protocol_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["protocol_version"] = "unsupported"

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task protocol_version is invalid")

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

    def test_accepts_success_failure_blocked_and_stopped_result_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                valid_result(status="succeeded", blockers=[], dirty_worktree=False),
                valid_result(status="failed", blockers=["unit test failed"], dirty_worktree=True, resulting_head=None),
                valid_result(status="blocked", blockers=["operator approval needed"], dirty_worktree=True, resulting_head=None),
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

    def test_result_evidence_rejects_success_without_required_check_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(commands_run=[], validation_results=[])

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(
                reason,
                "executor result missing required check command: python -m unittest tests.test_executor_contract",
            )

    def test_result_evidence_rejects_success_without_any_validation_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["required_checks"] = []
            evidence = valid_result(commands_run=[], validation_results=[])

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result successful status requires validation evidence")

    def test_result_evidence_rejects_success_without_any_command_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["required_checks"] = []
            evidence = valid_result(commands_run=[])

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result successful status requires command evidence")

    def test_result_evidence_rejects_success_when_required_check_fails_or_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 1,
                            }
                        ],
                    ),
                    "executor result required check command failed: python -m unittest tests.test_executor_contract",
                ),
                (
                    valid_result(
                        validation_results=[
                            {
                                "name": "executor-contract-tests",
                                "status": "skipped",
                                "command": "python -m unittest tests.test_executor_contract",
                            }
                        ],
                    ),
                    "executor result required check validation did not pass: python -m unittest tests.test_executor_contract",
                ),
                (
                    valid_result(
                        validation_results=[
                            {
                                "name": "unrelated-check",
                                "status": "passed",
                                "command": "python -m unittest tests.test_cadence",
                            }
                        ],
                    ),
                    "executor result missing required check validation: python -m unittest tests.test_executor_contract",
                ),
            ]

            for evidence, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_rejects_success_with_failed_validation_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(
                validation_results=[
                    {
                        "name": "executor-contract-tests",
                        "status": "passed",
                        "command": "python -m unittest tests.test_executor_contract",
                    },
                    {
                        "name": "other-check",
                        "status": "failed",
                    },
                ]
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result successful status requires all validation_results to pass")

    def test_result_evidence_rejects_forbidden_head_change_or_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                (
                    valid_result(resulting_head="def456"),
                    "executor result resulting_head must match task repo head when commits are forbidden",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git commit -m change",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled commit permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git push origin HEAD",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "gh pr create --fill",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled PR creation permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git status && git commit -m change",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled commit permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git -C . commit -m change",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled commit permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git -c user.name=bot commit -m change",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled commit permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git status; git push origin HEAD",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "git --no-pager push origin HEAD",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": 'bash -lc "gh pr create --fill"',
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled PR creation permission",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "gh --repo owner/repo pr create --fill",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] violates disabled PR creation permission",
                ),
            ]

            for evidence, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_rejects_success_without_resulting_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                valid_result(resulting_head=None),
                valid_result(),
            ]
            del cases[1]["resulting_head"]

            for evidence in cases:
                with self.subTest(keys=sorted(evidence.keys())):
                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, "executor result resulting_head is required when status is succeeded")

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
