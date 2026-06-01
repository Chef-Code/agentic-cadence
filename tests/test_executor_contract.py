import tempfile
import unittest
from pathlib import Path

from codex_cadence.executor_contract import (
    DEFAULT_EXECUTOR_STOP_CONDITIONS,
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
        stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
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

    def test_accepts_clean_medium_confidence_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["snapshot"]["repo_confidence"] = "medium"

            valid, reason = validate_executor_task_packet(packet)

            self.assertTrue(valid, reason)

            result_valid, result_reason = validate_executor_result_evidence(valid_result(), packet)

            self.assertTrue(result_valid, result_reason)

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

    def test_task_packet_rejects_invalid_embedded_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["snapshot"].pop("readiness_evidence")

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task snapshot is invalid: snapshot readiness_evidence is required")

    def test_task_packet_rejects_snapshot_repo_or_cwd_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    {"repo": "other/repo"},
                    {},
                    "executor task snapshot repo must match repo.name",
                ),
                (
                    {"cwd": str(Path(tmp) / "other-checkout")},
                    {},
                    "executor task snapshot cwd must match repo.path",
                ),
                (
                    {},
                    {"path": str(Path(tmp) / "other-checkout")},
                    "executor task snapshot cwd must match repo.path",
                ),
            ]

            for snapshot_updates, repo_updates, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    packet = valid_task_packet(Path(tmp))
                    packet["snapshot"].update(snapshot_updates)
                    packet["repo"].update(repo_updates)

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_task_packet_rejects_missing_or_blank_repo_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                None,
                "",
                "   ",
            ]

            for repo_name in cases:
                with self.subTest(repo_name=repo_name):
                    packet = valid_task_packet(Path(tmp))
                    if repo_name is None:
                        packet["repo"].pop("name")
                    else:
                        packet["repo"]["name"] = repo_name

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, "executor task repo.name is required")

    def test_task_packet_rejects_relative_or_unresolvable_repo_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            malformed_absolute_path = f"{Path(tmp).anchor}bad\0path"
            cases = [
                (
                    ".",
                    ".",
                    "executor task snapshot cwd and repo.path must be absolute local paths",
                ),
                (
                    "..",
                    "..",
                    "executor task snapshot cwd and repo.path must be absolute local paths",
                ),
                (
                    "~",
                    "~",
                    "executor task snapshot cwd and repo.path must be absolute local paths",
                ),
                (
                    "bad\0path",
                    str(Path(tmp)),
                    "executor task snapshot cwd and repo.path must be absolute local paths",
                ),
                (
                    malformed_absolute_path,
                    malformed_absolute_path,
                    "executor task snapshot cwd and repo.path must be absolute local paths",
                ),
            ]

            for snapshot_cwd, repo_path, expected_reason in cases:
                with self.subTest(snapshot_cwd=snapshot_cwd, repo_path=repo_path):
                    packet = valid_task_packet(Path(tmp))
                    packet["snapshot"]["cwd"] = snapshot_cwd
                    packet["repo"]["path"] = repo_path

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_task_packet_rejects_snapshot_branch_or_head_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    {"branch": "feature"},
                    "executor task snapshot branch must match repo.branch",
                ),
                (
                    {"head": "def456"},
                    "executor task snapshot head must match repo.head",
                ),
            ]

            for snapshot_updates, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    packet = valid_task_packet(Path(tmp))
                    packet["snapshot"].update(snapshot_updates)

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_task_packet_rejects_low_confidence_or_dirty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    {"repo_confidence": "low", "repo_confidence_drivers": ["red_ci"]},
                    "executor task snapshot repo_confidence must not be low",
                ),
                (
                    {"dirty_worktree": True, "repo_confidence": "high"},
                    "executor task snapshot dirty_worktree must be false",
                ),
            ]

            for snapshot_updates, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    packet = valid_task_packet(Path(tmp))
                    packet["snapshot"].update(snapshot_updates)

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

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
                                "command": 'bash --norc -lc "git commit -m change"',
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
                                "command": "/usr/bin/git push origin HEAD",
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
                                "command": "/opt/homebrew/bin/gh pr create --fill",
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

    def test_result_evidence_enforces_task_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": ["python -m unittest tests.test_executor_contract"],
                "denied_commands": ["python -m pip install"],
            }
            cases = [
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m pip install .",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract; "
                                    "python -m pip install ."
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": (
                                    'bash -lc "python -m unittest tests.test_executor_contract '
                                    '&& python -m pip install ."'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": (
                                    'pwsh -Command "python -m unittest tests.test_executor_contract; '
                                    'python -m pip install ."'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": (
                                    'cmd /c "python -m unittest tests.test_executor_contract && '
                                    'python -m pip install ."'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract\n"
                                    "python -m pip install ."
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    'bash -lc "python -m unittest tests.test_executor_contract\n'
                                    'python -m pip install ."'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract "
                                    "$(python -m pip install .)"
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    'bash -lc "python -m unittest tests.test_executor_contract '
                                    '$(python -m pip install .)"'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract "
                                    "`python -m pip install .`"
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    'bash -lc "python -m unittest tests.test_executor_contract '
                                    '`python -m pip install .`"'
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": "(python -m pip install .)",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": 'bash -lc "(python -m pip install .)"',
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is denied by command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python scripts/unknown.py",
                                "exit_code": 0,
                            },
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is outside allowed command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract && "
                                    "python scripts/unknown.py"
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[0] is outside allowed command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": (
                                    "python -m unittest tests.test_executor_contract "
                                    "$(python scripts/unknown.py)"
                                ),
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is outside allowed command_policy",
                ),
                (
                    valid_result(
                        commands_run=[
                            {
                                "command": "python -m unittest tests.test_executor_contract",
                                "exit_code": 0,
                            },
                            {
                                "command": "(python scripts/unknown.py)",
                                "exit_code": 0,
                            },
                        ],
                    ),
                    "executor result commands_run[1] is outside allowed command_policy",
                ),
            ]

            for evidence, expected_reason in cases:
                with self.subTest(reason=expected_reason):
                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_denies_grouped_commands_without_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": [],
                "denied_commands": ["python -m pip install"],
            }
            evidence = valid_result(
                commands_run=[
                    {
                        "command": "python -m unittest tests.test_executor_contract",
                        "exit_code": 0,
                    },
                    {
                        "command": "(python -m pip install .)",
                        "exit_code": 0,
                    },
                ],
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result commands_run[1] is denied by command_policy")

    def test_task_packet_rejects_malformed_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    {"allowed_commands": ["python -m unittest"], "denied_commands": [123]},
                    "executor task command_policy.denied_commands must be a list of strings",
                ),
                (
                    {"allowed_commands": None, "denied_commands": []},
                    "executor task command_policy.allowed_commands must be a list of strings",
                ),
                (
                    {"allowed_commands": [], "denied_commands": None},
                    "executor task command_policy.denied_commands must be a list of strings",
                ),
            ]

            for command_policy, expected_reason in cases:
                with self.subTest(command_policy=command_policy):
                    packet = valid_task_packet(Path(tmp))
                    packet["command_policy"] = command_policy

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_rejects_null_command_policy_fields_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    {"allowed_commands": None, "denied_commands": []},
                    "invalid executor task packet: executor task command_policy.allowed_commands must be a list of strings",
                ),
                (
                    {"allowed_commands": [], "denied_commands": None},
                    "invalid executor task packet: executor task command_policy.denied_commands must be a list of strings",
                ),
            ]

            for command_policy, expected_reason in cases:
                with self.subTest(command_policy=command_policy):
                    task_packet = valid_task_packet(Path(tmp))
                    task_packet["command_policy"] = command_policy

                    valid, reason = validate_executor_result_evidence(valid_result(), task_packet)

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

    def test_result_evidence_rejects_elapsed_time_over_task_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["limits"]["max_minutes"] = 4
            evidence = valid_result(
                started_at="2999-05-22T00:00:00Z",
                ended_at="2999-05-22T00:05:00Z",
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result elapsed time exceeds task limit")

    def test_task_packet_rejects_missing_builtin_stop_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["stop_conditions"] = ["brake_not_drive", "timeout"]

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task stop_conditions must include built-in safety stops")

    def test_task_packet_rejects_relative_expected_evidence_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                "executor-result.json",
                f"{Path(tmp).anchor}bad\0result.json",
            ]

            for evidence_path in cases:
                with self.subTest(evidence_path=evidence_path):
                    packet = valid_task_packet(Path(tmp))
                    packet["expected_output"]["evidence_path"] = evidence_path

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, "executor task expected_output.evidence_path must be absolute")
