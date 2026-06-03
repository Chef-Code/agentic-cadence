import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence.epochs import checksum_json, complete_epoch, completed_continue_count, elect_candidates, fail_epoch
from codex_cadence.epochs import executor_result_failure_reason
from codex_cadence.epochs import read_active_epoch_records, self_check_decision, start_epoch
from codex_cadence.store import epoch_path, snapshot_path


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


def json_status(path):
    return json.loads(path.read_text(encoding="utf-8"))["status"]


def write_snapshot(root, snapshot):
    path = snapshot_path(root, snapshot["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


def valid_continue_check(epoch, completed_continues=0, snapshot_after=None):
    current_snapshot = snapshot_after or valid_snapshot(id="snapshot-after")
    return {
        "epoch_id": epoch["id"],
        "decision": "CONTINUE",
        "epoch_grounded": True,
        "current_snapshot_grounded": True,
        "repo_confidence": "high",
        "uncertainty": "low",
        "epoch_health": "good",
        "brake_status": "DRIVE",
        "elected_next": [{"id": "task-next", "task_type": "execution", "bucket": "S"}],
        "epoch_policy_checksum": checksum_json(epoch["policy"]),
        "snapshot_before_id": epoch["snapshot_before"]["id"],
        "snapshot_before_checksum": checksum_json(epoch["snapshot_before"]),
        "snapshot_after_id": current_snapshot["id"],
        "snapshot_after_checksum": checksum_json(current_snapshot),
        "completed_continue_count": completed_continues,
        "current_snapshot_ci": "green",
    }


class EpochLifecycleTests(unittest.TestCase):
    def test_executor_result_failure_reason_uses_stable_codes(self):
        cases = [
            (True, "ok", {"status": "failed"}, "executor_result_failed"),
            (True, "ok", {"status": "blocked"}, "executor_result_blocked"),
            (True, "ok", {"status": "stopped", "blockers": ["timeout"]}, "executor_result_timed_out"),
            (True, "ok", {"status": "stopped", "blockers": ["operator stop"]}, "executor_result_stopped"),
            (False, "executor result commands_run[0] is denied by command_policy", {}, "executor_result_policy_violation"),
            (False, "executor result missing required check command", {}, "executor_result_invalid"),
        ]

        for valid, reason, evidence, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(executor_result_failure_reason(valid, reason, evidence), expected)

    def test_start_epoch_records_policy_and_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "task-1", "task_type": "execution", "title": "First task"}]
            snapshot_before = valid_snapshot()

            epoch = start_epoch(
                root,
                repo="local/test",
                branch="main",
                tasks=tasks,
                snapshot_before=snapshot_before,
            )

            self.assertEqual(epoch["status"], "ACTIVE")
            self.assertEqual(epoch["repo"], "local/test")
            self.assertEqual(epoch["branch"], "main")
            self.assertEqual(epoch["tasks"], tasks)
            self.assertEqual(epoch["completed_tasks"], [])
            self.assertEqual(epoch["snapshot_before"], snapshot_before)
            self.assertEqual(epoch["policy"]["max_tasks_per_epoch"], 3)
            self.assertTrue(epoch_path(root, "active", epoch["id"]).exists())

    def test_complete_epoch_moves_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(
                root,
                repo="local/test",
                branch="main",
                tasks=[{"id": "task-1", "task_type": "execution"}],
                snapshot_before=valid_snapshot(),
            )

            completed = complete_epoch(root, epoch["id"], decision="STOP", summary="done")

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["decision"], "STOP")
            self.assertEqual(completed["summary"], "done")
            self.assertFalse(epoch_path(root, "active", epoch["id"]).exists())
            self.assertTrue(epoch_path(root, "completed", epoch["id"]).exists())

    def test_start_epoch_retries_generated_id_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collision_id = "epoch-collision"
            collision_path = epoch_path(root, "active", collision_id)
            original_exists = Path.exists

            def exists_with_race(path):
                if path == collision_path:
                    return True
                return original_exists(path)

            with mock.patch(
                "codex_cadence.epochs.epoch_id",
                side_effect=[collision_id, "epoch-collision-retry"],
            ), mock.patch("pathlib.Path.exists", exists_with_race):
                second = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-2"))

            self.assertEqual(second["id"], "epoch-collision-retry")
            self.assertTrue(epoch_path(root, "active", second["id"]).exists())

    def test_start_epoch_rejects_existing_active_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-1"))

            with self.assertRaisesRegex(RuntimeError, "active epoch already exists"):
                start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-2"))

    def test_read_active_epoch_records_returns_sorted_records_without_creating_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(read_active_epoch_records(root), [])
            self.assertFalse((root / "epochs").exists())

            first = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())
            records = read_active_epoch_records(root)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0][0], epoch_path(root, "active", first["id"]))
            self.assertEqual(records[0][1]["id"], first["id"])

    def test_read_active_epoch_records_rejects_non_object_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_dir = root / "epochs" / "active"
            active_dir.mkdir(parents=True)
            active_path = active_dir / "epoch-list.json"
            active_path.write_text(json.dumps([]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"epoch-list\.json.*list"):
                read_active_epoch_records(root)

    def test_complete_epoch_uses_move_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())

            original_unlink = Path.unlink
            calls = []

            def tracked_unlink(path):
                calls.append(path)
                return original_unlink(path)

            with mock.patch("pathlib.Path.unlink", tracked_unlink):
                complete_epoch(root, epoch["id"], decision="STOP")

            self.assertIn(epoch_path(root, "active", epoch["id"]), calls)
            self.assertFalse(epoch_path(root, "active", epoch["id"]).exists())
            self.assertTrue(epoch_path(root, "completed", epoch["id"]).exists())

    def test_complete_epoch_write_failure_leaves_active_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())
            active_path = epoch_path(root, "active", epoch["id"])
            completed_path = epoch_path(root, "completed", epoch["id"])

            def fail_completed_write(path, data):
                if path == completed_path:
                    raise OSError("write failed")
                raise AssertionError("unexpected write")

            with mock.patch("codex_cadence.epochs.atomic_write_json", fail_completed_write):
                with self.assertRaisesRegex(OSError, "write failed"):
                    complete_epoch(root, epoch["id"], decision="STOP")

            self.assertTrue(active_path.exists())
            self.assertFalse(completed_path.exists())
            self.assertEqual(json_status(active_path), "ACTIVE")

    def test_complete_epoch_unlink_failure_rolls_back_terminal_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())
            active_path = epoch_path(root, "active", epoch["id"])
            completed_path = epoch_path(root, "completed", epoch["id"])
            original_unlink = Path.unlink

            def fail_active_unlink(path):
                if path == active_path:
                    raise OSError("unlink failed")
                return original_unlink(path)

            with mock.patch("pathlib.Path.unlink", fail_active_unlink):
                with self.assertRaisesRegex(OSError, "unlink failed"):
                    complete_epoch(root, epoch["id"], decision="STOP")

            self.assertTrue(active_path.exists())
            self.assertFalse(completed_path.exists())
            self.assertEqual(json_status(active_path), "ACTIVE")

    def test_fail_epoch_moves_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(
                root,
                repo="local/test",
                branch="main",
                tasks=[{"id": "task-1", "task_type": "execution"}],
                snapshot_before=valid_snapshot(),
            )

            failed = fail_epoch(root, epoch["id"], reason="blocked")

            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["failure_reason"], "blocked")
            self.assertFalse(epoch_path(root, "active", epoch["id"]).exists())
            self.assertTrue(epoch_path(root, "failed", epoch["id"]).exists())

    def test_fail_epoch_unlink_failure_rolls_back_failed_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())
            active_path = epoch_path(root, "active", epoch["id"])
            failed_path = epoch_path(root, "failed", epoch["id"])
            original_unlink = Path.unlink

            def fail_active_unlink(path):
                if path == active_path:
                    raise OSError("unlink failed")
                return original_unlink(path)

            with mock.patch("pathlib.Path.unlink", fail_active_unlink):
                with self.assertRaisesRegex(OSError, "unlink failed"):
                    fail_epoch(root, epoch["id"], reason="blocked")

            self.assertTrue(active_path.exists())
            self.assertFalse(failed_path.exists())
            self.assertEqual(json_status(active_path), "ACTIVE")

    def test_complete_epoch_rejects_continue_after_epoch_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-1"))
            first_after = valid_snapshot(id="snapshot-after-1")
            write_snapshot(root, first_after)
            first_active_path = epoch_path(root, "active", first["id"])
            first_active = json.loads(first_active_path.read_text(encoding="utf-8"))
            first_active["last_self_check"] = valid_continue_check(first, completed_continues=0, snapshot_after=first_after)
            first_active_path.write_text(json.dumps(first_active), encoding="utf-8")
            complete_epoch(root, first["id"], decision="CONTINUE")
            self.assertEqual(completed_continue_count(root), 1)

            second = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-2"))
            second_after = valid_snapshot(id="snapshot-after-2")
            write_snapshot(root, second_after)
            second_active_path = epoch_path(root, "active", second["id"])
            second_active = json.loads(second_active_path.read_text(encoding="utf-8"))
            second_active["last_self_check"] = valid_continue_check(second, completed_continues=1, snapshot_after=second_after)
            second_active_path.write_text(json.dumps(second_active), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "CONTINUE exceeds max_epochs_without_user_approval"):
                complete_epoch(root, second["id"], decision="CONTINUE")

            self.assertTrue(second_active_path.exists())

    def test_complete_epoch_rejects_continue_without_snapshot_after_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot())
            active_path = epoch_path(root, "active", epoch["id"])
            active = json.loads(active_path.read_text(encoding="utf-8"))
            active["last_self_check"] = valid_continue_check(epoch, completed_continues=0)
            active["last_self_check"].pop("snapshot_after_id")
            active["last_self_check"].pop("snapshot_after_checksum")
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "CONTINUE self-check must include snapshot_after_id"):
                complete_epoch(root, epoch["id"], decision="CONTINUE")

            self.assertTrue(active_path.exists())

    def test_complete_epoch_rejects_stale_snapshot_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-before"))
            snapshot_after = valid_snapshot(
                id="snapshot-after",
                captured_at="2000-01-01T00:00:00Z",
            )
            write_snapshot(root, snapshot_after)
            active_path = epoch_path(root, "active", epoch["id"])
            active = json.loads(active_path.read_text(encoding="utf-8"))
            active["last_self_check"] = valid_continue_check(epoch, completed_continues=0, snapshot_after=snapshot_after)
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "snapshot_after must be captured after epoch start"):
                complete_epoch(root, epoch["id"], decision="CONTINUE")

            self.assertTrue(active_path.exists())

    def test_complete_epoch_rejects_snapshot_after_matching_snapshot_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_before = valid_snapshot(id="snapshot-before")
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=snapshot_before)
            write_snapshot(root, snapshot_before)
            active_path = epoch_path(root, "active", epoch["id"])
            active = json.loads(active_path.read_text(encoding="utf-8"))
            active["last_self_check"] = valid_continue_check(epoch, completed_continues=0, snapshot_after=snapshot_before)
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "snapshot_after must be distinct"):
                complete_epoch(root, epoch["id"], decision="CONTINUE")

            self.assertTrue(active_path.exists())

    def test_complete_epoch_reruns_persisted_self_check_governance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(id="snapshot-before"))
            snapshot_after = valid_snapshot(id="snapshot-after")
            write_snapshot(root, snapshot_after)
            active_path = epoch_path(root, "active", epoch["id"])
            active = json.loads(active_path.read_text(encoding="utf-8"))
            active["last_self_check"] = valid_continue_check(epoch, completed_continues=0, snapshot_after=snapshot_after)
            active["last_self_check"]["uncertainty"] = "high"
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "no longer passes governance: uncertainty is high"):
                complete_epoch(root, epoch["id"], decision="CONTINUE")

            self.assertTrue(active_path.exists())

    def test_start_epoch_deep_copies_caller_owned_structures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "task-1", "task_type": "execution", "metadata": {"risk": "low"}}]
            snapshot_before = valid_snapshot()
            policy = {"max_tasks_per_epoch": 2, "limits": {"minutes": 15}}

            epoch = start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=snapshot_before, policy=policy)

            tasks[0]["metadata"]["risk"] = "high"
            snapshot_before["known_failures"].append("ci")
            policy["limits"]["minutes"] = 30

            self.assertEqual(epoch["tasks"][0]["metadata"]["risk"], "low")
            self.assertEqual(epoch["snapshot_before"]["known_failures"], [])
            self.assertEqual(epoch["policy"]["limits"]["minutes"], 15)

    def test_start_epoch_rejects_invalid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "invalid snapshot_before"):
                start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before={"id": "snapshot-1"})

    def test_start_epoch_requires_repo_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "epoch repo is required"):
                start_epoch(root, repo=None, branch="main", tasks=[], snapshot_before=valid_snapshot())
            with self.assertRaisesRegex(ValueError, "epoch branch is required"):
                start_epoch(root, repo="local/test", branch=None, tasks=[], snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_snapshot_repo_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "snapshot repo does not match"):
                start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(repo="other/repo"))

    def test_start_epoch_rejects_snapshot_branch_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "snapshot branch does not match"):
                start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=valid_snapshot(branch="feature"))

    def test_start_epoch_rejects_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "invalid snapshot_before"):
                start_epoch(root, repo="local/test", branch="main", tasks=[], snapshot_before=None)

    def test_start_epoch_rejects_tasks_over_policy_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": f"task-{index}", "task_type": "execution"} for index in range(4)]

            with self.assertRaisesRegex(ValueError, "exceeds max_tasks_per_epoch"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_too_many_discovery_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [
                {"id": "task-1", "task_type": "discovery"},
                {"id": "task-2", "task_type": "discovery"},
            ]

            with self.assertRaisesRegex(ValueError, "exceeds max_discovery_tasks_per_epoch"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_malformed_task_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "epoch task 0 must be a JSON object"):
                start_epoch(root, repo="local/test", branch="main", tasks=["bad"], snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_missing_task_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "epoch task 0 task_type must be execution or discovery"):
                start_epoch(root, repo="local/test", branch="main", tasks=[{"id": "task-1"}], snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_self_evolution_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "task-1", "task_type": "execution", "drivers": ["self_evolution"]}]

            with self.assertRaisesRegex(ValueError, "self-evolution execution requires protocol approval"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_non_executable_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "task-1", "task_type": "execution", "executable": False}]

            with self.assertRaisesRegex(ValueError, "must be executable"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_agent_proposal_without_elect_allowance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "proposal-1", "task_type": "discovery", "source": "agent_proposal"}]

            with self.assertRaisesRegex(ValueError, "agent proposal requires elect allowance"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())

    def test_start_epoch_rejects_xl_task_without_decomposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [{"id": "task-xl", "task_type": "execution", "bucket": "XL"}]

            with self.assertRaisesRegex(ValueError, "XL task requires approval or decomposition"):
                start_epoch(root, repo="local/test", branch="main", tasks=tasks, snapshot_before=valid_snapshot())


class SelfCheckTests(unittest.TestCase):
    def test_valid_execution_candidate_continues(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "CONTINUE")

    def test_non_green_ci_forces_handoff_under_default_policy(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="unknown",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "HANDOFF")
        self.assertEqual(decision["reason"], "green CI or explicit handoff required")

    def test_elapsed_epoch_over_policy_forces_handoff(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False, "max_minutes_per_epoch": 60},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=61,
        )
        self.assertEqual(decision["decision"], "HANDOFF")
        self.assertEqual(decision["reason"], "epoch exceeded max_minutes_per_epoch")

    def test_completed_epoch_limit_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False, "max_epochs_without_user_approval": 1},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
            completed_continue_count=1,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "max_epochs_without_user_approval reached")

    def test_continuation_defaults_to_ungrounded(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "epoch snapshot required for continuation")

    def test_ungrounded_continuation_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=False,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "epoch snapshot required for continuation")

    def test_no_elected_candidates_stops(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[],
            policy={"allow_recursive_discovery": False},
        )
        self.assertEqual(decision["decision"], "STOP")

    def test_xl_candidate_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "XL"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")

    def test_degraded_epoch_health_forces_handoff(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="degraded",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "HANDOFF")

    def test_neutral_and_park_brake_stop(self):
        for brake_status in ("NEUTRAL", "PARK"):
            with self.subTest(brake_status=brake_status):
                decision = self_check_decision(
                    brake_status=brake_status,
                    repo_confidence="high",
                    uncertainty="low",
                    epoch_health="good",
                    elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                    policy={"allow_recursive_discovery": False},
                )
                self.assertEqual(decision["decision"], "STOP")
                self.assertEqual(decision["reason"], f"brake is {brake_status}")

    def test_invalid_enum_inputs_raise_value_error(self):
        cases = [
            {"brake_status": "ROLL"},
            {"repo_confidence": "none"},
            {"uncertainty": "unknown"},
            {"epoch_health": "bad"},
        ]
        defaults = {
            "brake_status": "DRIVE",
            "repo_confidence": "high",
            "uncertainty": "low",
            "epoch_health": "good",
        }
        for override in cases:
            with self.subTest(override=override):
                kwargs = {**defaults, **override}
                with self.assertRaises(ValueError):
                    self_check_decision(
                        **kwargs,
                        elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                        policy={"allow_recursive_discovery": False},
                        epoch_grounded=True,
                    )

    def test_self_check_rejects_malformed_elected_next(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 task_type must be execution or discovery"):
            self_check_decision(
                brake_status="DRIVE",
                repo_confidence="high",
                uncertainty="low",
                epoch_health="good",
                elected_next=[{"id": "malformed"}],
                policy={"allow_recursive_discovery": False},
                epoch_grounded=True,
            )

    def test_high_uncertainty_forces_handoff(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="high",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "HANDOFF")

    def test_high_candidate_uncertainty_forces_handoff(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S", "uncertainty": "high"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "HANDOFF")
        self.assertEqual(decision["reason"], "uncertainty is high")

    def test_low_repo_confidence_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="low",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "execution", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")

    def test_recursive_discovery_is_blocked(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[{"id": "task-2", "task_type": "discovery", "bucket": "S"}],
            policy={"allow_recursive_discovery": False},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")

    def test_self_evolution_execution_candidate_is_blocked(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[
                {
                    "id": "task-2",
                    "task_type": "execution",
                    "bucket": "S",
                    "drivers": ["self_evolution"],
                }
            ],
            policy={"allow_recursive_discovery": False, "allow_self_evolution": "propose_only"},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "self-evolution execution requires protocol approval")

    def test_recursive_discovery_policy_must_be_boolean(self):
        with self.assertRaisesRegex(ValueError, "allow_recursive_discovery must be a boolean"):
            self_check_decision(
                brake_status="DRIVE",
                repo_confidence="high",
                uncertainty="low",
                epoch_health="good",
                elected_next=[{"id": "task-2", "task_type": "discovery", "bucket": "S"}],
                policy={"allow_recursive_discovery": "false"},
                epoch_grounded=True,
            )

    def test_elected_task_count_over_policy_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[
                {"id": "task-1", "task_type": "execution", "bucket": "S"},
                {"id": "task-2", "task_type": "execution", "bucket": "S"},
            ],
            policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 1},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "elected task count exceeds epoch policy")

    def test_elected_discovery_count_over_policy_asks_approval(self):
        decision = self_check_decision(
            brake_status="DRIVE",
            repo_confidence="high",
            uncertainty="low",
            epoch_health="good",
            elected_next=[
                {"id": "task-1", "task_type": "discovery", "bucket": "S"},
                {"id": "task-2", "task_type": "discovery", "bucket": "S"},
            ],
            policy={"allow_recursive_discovery": True, "max_tasks_per_epoch": 3, "max_discovery_tasks_per_epoch": 1},
            epoch_grounded=True,
            current_snapshot_ci="green",
            epoch_elapsed_minutes=5,
        )
        self.assertEqual(decision["decision"], "ASK_APPROVAL")
        self.assertEqual(decision["reason"], "elected discovery task count exceeds epoch policy")

    def test_election_penalizes_discovery_so_bounded_execution_outranks_discovery(self):
        candidates = [
            {"id": "discovery-1", "task_type": "discovery", "bucket": "S", "score": 100},
            {"id": "execution-1", "task_type": "execution", "bucket": "S", "score": 90},
        ]

        elected = elect_candidates(candidates, max_tasks=1)

        self.assertEqual([candidate["id"] for candidate in elected], ["execution-1"])
        self.assertTrue(elected[0]["elected"])
        self.assertNotIn("elected", candidates[1])

    def test_malformed_candidate_item_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 must be a JSON object"):
            elect_candidates(["not-a-dict"], max_tasks=1)

    def test_missing_task_type_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 task_type must be execution or discovery"):
            elect_candidates([{"id": "task-1", "bucket": "S"}], max_tasks=1)

    def test_invalid_task_type_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 task_type must be execution or discovery"):
            elect_candidates([{"id": "task-1", "task_type": "analysis", "bucket": "S"}], max_tasks=1)

    def test_invalid_bucket_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 bucket must be one of XS, S, M, L, XL"):
            elect_candidates([{"id": "task-1", "task_type": "execution", "bucket": "XXL"}], max_tasks=1)

    def test_string_score_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 score must be numeric"):
            elect_candidates([{"id": "task-1", "task_type": "execution", "bucket": "S", "score": "10"}], max_tasks=1)

    def test_bool_score_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "candidate 0 score must be numeric"):
            elect_candidates([{"id": "task-1", "task_type": "execution", "bucket": "S", "score": True}], max_tasks=1)

    def test_election_deep_copies_candidates(self):
        candidates = [{"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10, "metadata": {"risk": "low"}}]

        elected = elect_candidates(candidates, max_tasks=1)
        elected[0]["metadata"]["risk"] = "high"

        self.assertEqual(candidates[0]["metadata"]["risk"], "low")
        self.assertNotIn("elected", candidates[0])

    def test_election_skips_non_executable_allowance_candidates(self):
        candidates = [
            {
                "id": "agent-proposal-001",
                "task_type": "discovery",
                "bucket": "S",
                "score": 100,
                "requires_user_allowance": True,
                "allowance": "surface",
                "executable": False,
            },
            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10},
        ]

        elected = elect_candidates(candidates, max_tasks=1)

        self.assertEqual([candidate["id"] for candidate in elected], ["task-1"])

    def test_election_skips_surface_allowance_even_when_marked_executable(self):
        candidates = [
            {
                "id": "agent-proposal-001",
                "task_type": "discovery",
                "bucket": "S",
                "score": 100,
                "requires_user_allowance": True,
                "allowance": "surface",
                "executable": True,
            },
            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10},
        ]

        elected = elect_candidates(candidates, max_tasks=1)

        self.assertEqual([candidate["id"] for candidate in elected], ["task-1"])

    def test_election_skips_agent_proposal_without_allowance_flag(self):
        candidates = [
            {
                "id": "agent-proposal-001",
                "task_type": "discovery",
                "bucket": "S",
                "score": 100,
                "source": "agent_proposal",
                "allowance": "elect",
                "executable": True,
            },
            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10},
        ]

        elected = elect_candidates(candidates, max_tasks=1)

        self.assertEqual([candidate["id"] for candidate in elected], ["task-1"])

    def test_election_caps_discovery_candidates(self):
        candidates = [
            {"id": "discovery-1", "task_type": "discovery", "bucket": "S", "score": 100},
            {"id": "discovery-2", "task_type": "discovery", "bucket": "S", "score": 90},
            {"id": "execution-1", "task_type": "execution", "bucket": "S", "score": 10},
        ]

        elected = elect_candidates(candidates, max_tasks=3, max_discovery_tasks=1)

        self.assertEqual([candidate["id"] for candidate in elected], ["discovery-1", "execution-1"])

    def test_election_skips_mutually_exclusive_candidates(self):
        candidates = [
            {
                "id": "task-1",
                "task_type": "execution",
                "bucket": "S",
                "score": 90,
                "relationships": {"mutually_exclusive_with": ["task-2"]},
            },
            {
                "id": "task-2",
                "task_type": "execution",
                "bucket": "S",
                "score": 80,
                "relationships": {"mutually_exclusive_with": ["task-1"]},
            },
            {"id": "task-3", "task_type": "execution", "bucket": "S", "score": 70},
        ]

        elected = elect_candidates(candidates, max_tasks=3)

        self.assertEqual([candidate["id"] for candidate in elected], ["task-1", "task-3"])


if __name__ == "__main__":
    unittest.main()
