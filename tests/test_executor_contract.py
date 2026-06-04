import tempfile
import unittest
from pathlib import Path

from codex_cadence.executor_contract import (
    DEFAULT_EXECUTOR_STOP_CONDITIONS,
    _raw_command_substitutions,
    build_executor_task_packet,
    validate_executor_command,
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

    def test_task_packet_preserves_agent_proposal_allowance_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = build_executor_task_packet(
                task=valid_candidate(
                    task_type="discovery",
                    source="agent_proposal",
                    requires_user_allowance=True,
                    allowance="elect",
                    allowance_reason="operator allowed proposal election",
                ),
                snapshot=valid_snapshot(cwd=str(tmp)),
                repo_path=tmp,
                allowed_paths=["codex_cadence"],
                required_checks=[],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )

            valid, reason = validate_executor_task_packet(packet)

            self.assertTrue(valid, reason)
            self.assertTrue(packet["task"]["requires_user_allowance"])
            self.assertEqual(packet["task"]["allowance"], "elect")
            self.assertEqual(packet["task"]["allowance_reason"], "operator allowed proposal election")

    def test_task_packet_rejects_agent_proposal_without_elect_allowance(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["task"]["source"] = "agent_proposal"
            packet["task"]["requires_user_allowance"] = True
            packet["task"]["allowance"] = "surface"

            valid, reason = validate_executor_task_packet(packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor task task agent proposal requires elect allowance")

    def test_accepts_clean_medium_confidence_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = valid_task_packet(Path(tmp))
            packet["snapshot"]["repo_confidence"] = "medium"

            valid, reason = validate_executor_task_packet(packet)

            self.assertTrue(valid, reason)

            result_valid, result_reason = validate_executor_result_evidence(valid_result(), packet)

            self.assertTrue(result_valid, result_reason)

    def test_task_packet_rejects_malformed_task_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                ("drivers", "governance", "executor task task.drivers must be a list of strings"),
                ("drivers", [""], "executor task task.drivers must be a list of strings"),
                ("evidence", "docs/roadmap.md", "executor task task.evidence must be a JSON object"),
            ]

            for field, value, expected_reason in cases:
                with self.subTest(field=field, value=value):
                    packet = valid_task_packet(Path(tmp))
                    packet["task"][field] = value

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

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
                                "command": "git -c alias.x=push x origin HEAD",
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
                                "command": (
                                    "python -m unittest tests.test_executor_contract # ok\n"
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
                                    "cmd /c python -m unittest tests.test_executor_contract "
                                    "&& python -m pip install ."
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
                                "command": "python -m $(echo pip) install .",
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
                                "command": "python -m pip $(echo install) .",
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
                                "command": "`echo python -m pip install .`",
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
                                "command": "{ python -m pip install .; }",
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
                                "command": 'bash -lc "{ python -m pip install .; }"',
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
                                    "python -m unittest tests.test_executor_contract || "
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
                                    "python -m unittest tests.test_executor_contract | "
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
                                    "python -m unittest tests.test_executor_contract & "
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
                                "command": (
                                    "python -m unittest tests.test_executor_contract "
                                    "$(echo $(python scripts/unknown.py))"
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
                                    "`python scripts/unknown.py`"
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

    def test_result_evidence_denies_git_alias_push_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": [],
                "denied_commands": ["git push"],
            }
            evidence = valid_result(
                status="failed",
                commands_run=[
                    {
                        "command": "git -c alias.x=push x origin main",
                        "exit_code": 1,
                    },
                ],
                validation_results=[],
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result commands_run[0] is denied by command_policy")

    def test_result_evidence_denies_unquoted_cmd_payload_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": [],
                "denied_commands": ["python -m pip install"],
            }
            evidence = valid_result(
                status="failed",
                commands_run=[
                    {
                        "command": "cmd /c python -m pip install .",
                        "exit_code": 1,
                    },
                ],
                validation_results=[],
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result commands_run[0] is denied by command_policy")

    def test_result_evidence_rejects_shell_expansion_policy_bypasses(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": ["python -m unittest tests.test_executor_contract"],
                "denied_commands": ["python -m pip install"],
            }
            cases = [
                (
                    "python -m unittest tests.test_executor_contract < <(python -m pip install .)",
                    "executor result commands_run[0] is denied by command_policy",
                ),
                (
                    "python -m pip${IFS}install .",
                    "executor result commands_run[0] is denied by command_policy",
                ),
            ]

            for command, expected_reason in cases:
                with self.subTest(command=command):
                    evidence = valid_result(
                        status="failed",
                        commands_run=[
                            {
                                "command": command,
                                "exit_code": 1,
                            },
                        ],
                        validation_results=[],
                    )

                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_rejects_shell_expansion_permission_bypasses(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                (
                    'git -c alias.x="!sh -c \'git push origin main\'" x',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "git $(printf push) origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "git $(echo $(echo push)) origin main",
                    "executor result commands_run[0] contains unsupported shell expansion",
                ),
                (
                    "bash -lc 'p=push; git $p origin main'",
                    "executor result commands_run[0] contains unsupported shell expansion",
                ),
                (
                    "git status # ok\ngit push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "sudo -u root git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "sudo --user root git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "env --chdir /tmp git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "env -C /tmp git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "GIT_ALIAS=push git --config-env=alias.x=GIT_ALIAS x origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "env GIT_ALIAS=push git --config-env=alias.x=GIT_ALIAS x origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "git -c alias.one=two -c alias.two=push one origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "command git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "nohup git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "time git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    'env -S "git push origin main"',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    'env --split-string "git push origin main"',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "/usr/bin/time -f %E git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "time --format=%E git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "time --output /tmp/t git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "exec git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "nice git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "timeout 10 git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "builtin command git push origin main",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    'eval "git push origin main"',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    'bash -lc "git\\\n push origin main"',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "bash -c $'git push origin main'",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "bash -lc $'git push origin main'",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "zsh -c $'git push origin main'",
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    'bash -c $"git push origin main"',
                    "executor result commands_run[0] violates disabled push permission",
                ),
                (
                    "env -S'git push origin main'",
                    "executor result commands_run[0] violates disabled push permission",
                ),
            ]

            for command, expected_reason in cases:
                with self.subTest(command=command):
                    evidence = valid_result(
                        status="failed",
                        commands_run=[
                            {
                                "command": command,
                                "exit_code": 1,
                            },
                        ],
                        validation_results=[],
                    )

                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_handles_deep_nested_substitution_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            command = "push"
            for _ in range(80):
                command = f"echo $({command})"
            evidence = valid_result(
                status="failed",
                commands_run=[
                    {
                        "command": f"git $({command}) origin main",
                        "exit_code": 1,
                    },
                ],
                validation_results=[],
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result commands_run[0] contains unsupported shell expansion")

    def test_result_evidence_does_not_treat_literal_text_as_git_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                "echo git push origin main",
                "echo '$(git push origin main)'",
                "echo '$HOME'",
            ]

            for command in cases:
                with self.subTest(command=command):
                    evidence = valid_result(
                        status="failed",
                        commands_run=[
                            {
                                "command": command,
                                "exit_code": 0,
                            },
                        ],
                        validation_results=[],
                    )

                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertTrue(valid, reason)

    def test_result_evidence_rejects_opaque_python_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            evidence = valid_result(
                status="failed",
                commands_run=[
                    {
                        "command": """python -c 'print("$(git push origin main)")'""",
                        "exit_code": 0,
                    },
                ],
                validation_results=[],
            )

            valid, reason = validate_executor_result_evidence(evidence, task_packet)

            self.assertFalse(valid)
            self.assertEqual(reason, "executor result commands_run[0] contains unsupported shell expansion")

    def test_command_substitution_extraction_ignores_literal_parentheses(self):
        self.assertEqual(
            _raw_command_substitutions('python -m unittest tests.test_executor_contract $(echo "(")'),
            ['echo "("'],
        )
        self.assertEqual(
            _raw_command_substitutions("python -m unittest tests.test_executor_contract $(echo $(pwd))"),
            ["echo $(pwd)"],
        )

    def test_task_packet_rejects_malformed_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    None,
                    "executor task command_policy must be a JSON object",
                ),
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

    def test_task_packet_rejects_malformed_branch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    None,
                    "executor task branch_policy must be a JSON object",
                ),
                (
                    {"allowed_base_branches": "main", "allow_current_branch_main": False},
                    "executor task branch_policy.allowed_base_branches must be a list of non-empty strings",
                ),
                (
                    {"denied_target_branches": [""], "allow_current_branch_main": False},
                    "executor task branch_policy.denied_target_branches must be a list of non-empty strings",
                ),
                (
                    {"required_branch_prefixes": [123], "allow_current_branch_main": False},
                    "executor task branch_policy.required_branch_prefixes must be a list of non-empty strings",
                ),
                (
                    {"allowed_base_branches": ["main"], "allow_current_branch_main": "false"},
                    "executor task branch_policy.allow_current_branch_main must be a boolean",
                ),
                (
                    {"required_branch_prefix": ["codex/"], "allow_current_branch_main": False},
                    "executor task branch_policy contains unknown keys: required_branch_prefix",
                ),
            ]

            for branch_policy, expected_reason in cases:
                with self.subTest(branch_policy=branch_policy):
                    packet = valid_task_packet(Path(tmp))
                    packet["branch_policy"] = branch_policy

                    valid, reason = validate_executor_task_packet(packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_result_evidence_rejects_null_command_policy_fields_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                (
                    None,
                    "invalid executor task packet: executor task command_policy must be a JSON object",
                ),
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

    def test_validate_executor_command_uses_task_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            task_packet["command_policy"] = {
                "allowed_commands": ["python examples/controlled-executor-fixture/run.py"],
                "denied_commands": ["python -m pip install"],
            }

            valid, reason = validate_executor_command(
                "python examples/controlled-executor-fixture/run.py --task-file task.json --result-file result.json",
                task_packet,
            )

            self.assertTrue(valid, reason)

            valid, reason = validate_executor_command(
                "python -m pip install .",
                task_packet,
            )

            self.assertFalse(valid)
            self.assertEqual(reason, "executor command is denied by command_policy")

    def test_validate_executor_command_rejects_disabled_live_git_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))

            cases = [
                ("git commit -m fixture", "executor command violates disabled commit permission"),
                ("git push origin main", "executor command violates disabled push permission"),
                ("gh pr create --fill", "executor command violates disabled PR creation permission"),
                ("git merge feature", "executor command violates disabled merge permission"),
                ("gh pr merge 63 --merge", "executor command violates disabled merge permission"),
                ("gh release create v1.0.0", "executor command violates disabled release permission"),
                ("gh release upload v1.0.0 dist/pkg.whl", "executor command violates disabled release permission"),
                ("git tag v1.0.0", "executor command violates disabled release permission"),
                ("twine upload dist/*", "executor command violates disabled package publication permission"),
                ("twine --repository testpypi upload dist/*", "executor command violates disabled package publication permission"),
                ("python3 -m twine upload dist/*", "executor command violates disabled package publication permission"),
                (
                    "python3.11 -m twine upload dist/*",
                    "executor command violates disabled package publication permission",
                ),
                (
                    "python -m twine --config-file .pypirc upload dist/*",
                    "executor command violates disabled package publication permission",
                ),
                ("python -m twine upload dist/*", "executor command violates disabled package publication permission"),
                ("py -m twine upload dist/*", "executor command violates disabled package publication permission"),
                ("npm publish", "executor command violates disabled package publication permission"),
                ("npm --registry https://registry.npmjs.org publish", "executor command violates disabled package publication permission"),
                ("pnpm publish", "executor command violates disabled package publication permission"),
                ("yarn publish", "executor command violates disabled package publication permission"),
                ("yarn npm publish", "executor command violates disabled package publication permission"),
                ("poetry -C pkg publish", "executor command violates disabled package publication permission"),
                ("uv --directory pkg publish", "executor command violates disabled package publication permission"),
                ("hatch publish", "executor command violates disabled package publication permission"),
                ("flit publish", "executor command violates disabled package publication permission"),
                ("git -c alias.x='!gh pr create --fill' x", "executor command violates disabled PR creation permission"),
                ("git -c alias.x='!gh release create v1.0.0' x", "executor command violates disabled release permission"),
                ("git -c alias.x='!twine upload dist/*' x", "executor command violates disabled package publication permission"),
                (
                    "echo ok ; git -c alias.x='!gh pr create --fill' x",
                    "executor command violates disabled PR creation permission",
                ),
                (
                    "echo ok && git -c alias.x='!gh release create v1.0.0' x",
                    "executor command violates disabled release permission",
                ),
                (
                    "echo ok | git -c alias.x='!twine upload dist/*' x",
                    "executor command violates disabled package publication permission",
                ),
                ("powershell -EncodedCommand AAAA", "executor command contains unsupported shell expansion"),
                ("powershell /EncodedCommand AAAA", "executor command contains unsupported shell expansion"),
                ("powershell /enc AAAA", "executor command contains unsupported shell expansion"),
                (
                    "python -c \"import subprocess; subprocess.run(['git','push','origin','main'])\"",
                    "executor command contains unsupported shell expansion",
                ),
                (
                    "python -X dev -c \"import subprocess; subprocess.run(['git','push','origin','main'])\"",
                    "executor command contains unsupported shell expansion",
                ),
            ]

            for command, expected_reason in cases:
                with self.subTest(command=command):
                    valid, reason = validate_executor_command(command, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)

    def test_validate_executor_command_allows_python_tool_config_flags_after_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                "python script.py -c config.yml",
                "python -x script.py -c config.yml",
                "python -m pytest -c pytest.ini",
            ]

            for command in cases:
                with self.subTest(command=command):
                    valid, reason = validate_executor_command(command, task_packet)

                    self.assertTrue(valid, reason)

    def test_validate_executor_command_allows_read_only_git_tag_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                "git tag",
                "git tag --list",
                "git tag -l v*",
                "git tag --points-at HEAD",
                "git tag --verify v1.0.0",
            ]

            for command in cases:
                with self.subTest(command=command):
                    valid, reason = validate_executor_command(command, task_packet)

                    self.assertTrue(valid, reason)

    def test_result_evidence_rejects_merge_release_or_package_publication_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = valid_task_packet(Path(tmp))
            cases = [
                ("git merge feature", "executor result commands_run[0] violates disabled merge permission"),
                ("gh pr merge 63 --merge", "executor result commands_run[0] violates disabled merge permission"),
                ("gh release create v1.0.0", "executor result commands_run[0] violates disabled release permission"),
                ("gh release upload v1.0.0 dist/pkg.whl", "executor result commands_run[0] violates disabled release permission"),
                ("git tag v1.0.0", "executor result commands_run[0] violates disabled release permission"),
                ("twine upload dist/*", "executor result commands_run[0] violates disabled package publication permission"),
                (
                    "twine --repository testpypi upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "python3 -m twine upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "python3.11 -m twine upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "python -m twine upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "python -m twine --config-file .pypirc upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "py -m twine upload dist/*",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                ("npm publish", "executor result commands_run[0] violates disabled package publication permission"),
                (
                    "npm --registry https://registry.npmjs.org publish",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                ("pnpm publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("yarn publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("yarn npm publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("poetry -C pkg publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("uv --directory pkg publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("hatch publish", "executor result commands_run[0] violates disabled package publication permission"),
                ("flit publish", "executor result commands_run[0] violates disabled package publication permission"),
                (
                    "git -c alias.x='!gh pr create --fill' x",
                    "executor result commands_run[0] violates disabled PR creation permission",
                ),
                (
                    "git -c alias.x='!gh release create v1.0.0' x",
                    "executor result commands_run[0] violates disabled release permission",
                ),
                (
                    "git -c alias.x='!twine upload dist/*' x",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                (
                    "echo ok ; git -c alias.x='!gh pr create --fill' x",
                    "executor result commands_run[0] violates disabled PR creation permission",
                ),
                (
                    "echo ok && git -c alias.x='!gh release create v1.0.0' x",
                    "executor result commands_run[0] violates disabled release permission",
                ),
                (
                    "echo ok | git -c alias.x='!twine upload dist/*' x",
                    "executor result commands_run[0] violates disabled package publication permission",
                ),
                ("powershell -EncodedCommand AAAA", "executor result commands_run[0] contains unsupported shell expansion"),
                ("powershell /EncodedCommand AAAA", "executor result commands_run[0] contains unsupported shell expansion"),
                ("powershell /enc AAAA", "executor result commands_run[0] contains unsupported shell expansion"),
                (
                    "python -c \"import subprocess; subprocess.run(['git','push','origin','main'])\"",
                    "executor result commands_run[0] contains unsupported shell expansion",
                ),
                (
                    "python -X dev -c \"import subprocess; subprocess.run(['git','push','origin','main'])\"",
                    "executor result commands_run[0] contains unsupported shell expansion",
                ),
            ]

            for command, expected_reason in cases:
                with self.subTest(command=command):
                    evidence = valid_result(
                        status="failed",
                        commands_run=[
                            {
                                "command": command,
                                "exit_code": 1,
                            }
                        ],
                        validation_results=[],
                    )

                    valid, reason = validate_executor_result_evidence(evidence, task_packet)

                    self.assertFalse(valid)
                    self.assertEqual(reason, expected_reason)
