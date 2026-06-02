import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence.executor_contract import DEFAULT_EXECUTOR_STOP_CONDITIONS, build_executor_task_packet
from codex_cadence.executor_runner import run_controlled_executor_fixture
from codex_cadence.model import estimate_task
from codex_cadence.policy_audit import checksum_json
from codex_cadence.store import default_root, exclusive_lock, lock_path, snapshot_path as persisted_snapshot_path, utc_now


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def run_cli(root, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_cli_from(cwd, root, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def init_committed_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (Path(path) / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def ready_handoff_path(root, handoff_id):
    return Path(root) / "handoffs" / "ready" / f"{handoff_id}.json"


def rewrite_ready_handoff(root, handoff_id, update):
    path = ready_handoff_path(root, handoff_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    update(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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


def write_active_epoch(root, epoch_id, snapshot_before, policy=None):
    path = Path(root) / "epochs" / "active" / f"{epoch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "id": epoch_id,
        "status": "ACTIVE",
        "repo": "local/test",
        "branch": "main",
        "tasks": [],
        "completed_tasks": [],
        "snapshot_before": snapshot_before,
        "policy": policy if policy is not None else {"allow_recursive_discovery": False},
        "started_at": utc_now(),
        "updated_at": utc_now(),
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_active_epoch_raw(root, epoch_id):
    path = Path(root) / "epochs" / "active" / f"{epoch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": epoch_id, "status": "ACTIVE"}), encoding="utf-8")
    return path


def controlled_fixture_script() -> Path:
    return ROOT / "examples" / "controlled-executor-fixture" / "run.py"


def controlled_fixture_command(
    *,
    status="succeeded",
    exit_code=0,
    summary="Controlled fixture completed.",
    command="python -m unittest tests.test_cadence",
    executable=None,
    sleep_seconds=None,
) -> str:
    parts = [
        f'"{executable or sys.executable}"',
        f'"{controlled_fixture_script()}"',
        "--task-file",
        '"{task_file}"',
        "--result-file",
        '"{result_file}"',
        "--status",
        status,
        "--summary",
        f'"{summary}"',
        "--command",
        f'"{command}"',
        "--validation-name",
        "cadence-tests",
        "--validation-status",
        "passed" if status == "succeeded" else "failed",
        "--exit-code",
        str(exit_code),
        "--changed-file",
        "codex_cadence/executor_runner.py",
    ]
    if status != "succeeded":
        parts.extend(["--blocker", f'"{status} fixture evidence"'])
    if sleep_seconds is not None:
        parts.extend(["--sleep-seconds", str(sleep_seconds)])
    return " ".join(parts)


def write_controlled_fixture_task(root, repo, *, command_policy=None, evidence_name="executor-result.json"):
    evidence_path = Path(root) / evidence_name
    task_packet = build_executor_task_packet(
        task={
            "id": "candidate-1",
            "title": "Implement bounded executor task",
            "summary": "Create generic executor evidence.",
            "task_type": "execution",
            "bucket": "S",
            "source": "text_marker",
            "drivers": [],
            "evidence": {"path": "docs/roadmap.md"},
        },
        snapshot=valid_snapshot(cwd=str(repo)),
        repo_path=repo,
        allowed_paths=["codex_cadence", "tests"],
        required_checks=["python -m unittest tests.test_cadence"],
        max_minutes=1,
        max_tasks=1,
        stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
        evidence_path=evidence_path,
        allowed_commands=(command_policy or {}).get("allowed_commands"),
        denied_commands=(command_policy or {}).get("denied_commands"),
    )
    task_path = Path(root) / "executor-task.json"
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    return task_path, evidence_path, task_packet


def claimed_handoff_path(root, handoff_id):
    return Path(root) / "handoffs" / "claimed" / f"{handoff_id}.json"


class CadenceCliTests(unittest.TestCase):
    def test_default_root_uses_cadence_runtime_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                self.assertEqual(default_root(), Path(tmp) / ".codex" / "cadence")

    def test_legacy_transmission_root_env_still_overrides_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODEX_TRANSMISSION_ROOT": tmp}, clear=True):
                self.assertEqual(default_root(), Path(tmp))

    def test_existing_legacy_default_root_preserves_brake_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            legacy_root.mkdir(parents=True)
            (legacy_root / "brake.json").write_text(
                json.dumps({
                    "status": "PARK",
                    "reason": "operator pause",
                    "scope": "global",
                    "resume_requires": "manual_ack",
                    "updated_at": "2026-05-22T00:00:00Z",
                }),
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                self.assertEqual(default_root(), legacy_root)

    def test_cadence_root_env_overrides_existing_legacy_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)

            with mock.patch.dict(
                os.environ,
                {"HOME": tmp, "USERPROFILE": tmp, "CODEX_CADENCE_ROOT": str(cadence_root)},
                clear=True,
            ):
                self.assertEqual(default_root(), cadence_root)

    def test_conflicting_root_env_vars_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cadence_root = Path(tmp) / "cadence"
            legacy_root = Path(tmp) / "transmission"

            with mock.patch.dict(
                os.environ,
                {"CODEX_CADENCE_ROOT": str(cadence_root), "CODEX_TRANSMISSION_ROOT": str(legacy_root)},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "point to different roots"):
                    default_root()

    def test_matching_root_env_vars_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "same"

            with mock.patch.dict(
                os.environ,
                {"CODEX_CADENCE_ROOT": str(root), "CODEX_TRANSMISSION_ROOT": str(root)},
                clear=True,
            ):
                self.assertEqual(default_root(), root)

    def test_both_default_roots_fail_closed_without_explicit_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "both default runtime roots exist"):
                    default_root()

    def test_explicit_cli_root_works_when_both_default_roots_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            explicit_root = home / "explicit"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--root",
                        str(explicit_root),
                        "status",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(Path(output["root"]), explicit_root.resolve())

    def test_cli_fails_closed_when_both_default_roots_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=True):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "status"],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("both default runtime roots exist", result.stderr)

    def test_status_output_shape_remains_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "status")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(output.keys()), {"root", "brake", "cadence", "counts", "next_ready"})
            self.assertEqual(output["counts"], {
                "ready": 0,
                "claimed": 0,
                "completed": 0,
                "failed": 0,
            })
            self.assertEqual(output["cadence"], {
                "state": "PLAY_ON",
                "legacy_brake": "DRIVE",
                "can_start_work": True,
                "requires_operator_resume": False,
            })

    def test_status_maps_brake_to_football_cadence_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _brake = run_cli(tmp, "set-brake", "NEUTRAL", "--reason", "operator huddle")
            self.assertEqual(result.returncode, 0, result.stderr)
            result, output = run_cli(tmp, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["cadence"], {
                "state": "HUDDLE",
                "legacy_brake": "NEUTRAL",
                "can_start_work": False,
                "requires_operator_resume": False,
            })

            result, _brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator timeout")
            self.assertEqual(result.returncode, 0, result.stderr)
            result, output = run_cli(tmp, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["cadence"], {
                "state": "TIMEOUT",
                "legacy_brake": "PARK",
                "can_start_work": False,
                "requires_operator_resume": True,
            })

    def test_init_creates_drive_brake_and_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "init")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["brake"]["status"], "DRIVE")
            self.assertTrue((Path(tmp) / "brake.json").exists())
            self.assertTrue((Path(tmp) / "handoffs" / "ready").is_dir())

    def test_handoff_claim_and_complete_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "handoff-1",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["status"], "READY")
            self.assertIn("codex-handoff:v1", created["signature"])

            result, claimed = run_cli(tmp, "claim-handoff", "handoff-1", "--claimer", "test-agent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(claimed["status"], "CLAIMED")
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "handoff-1.json").exists())

            result, completed = run_cli(tmp, "complete-handoff", "handoff-1", "--summary", "done")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertTrue((Path(tmp) / "handoffs" / "completed" / "handoff-1.json").exists())

    def test_long_valid_handoff_id_can_move_through_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            handoff_id = "h" * 128
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                handoff_id,
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["id"], handoff_id)

            result, claimed = run_cli(tmp, "claim-handoff", handoff_id, "--claimer", "test-agent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(claimed["status"], "CLAIMED")

            result, completed = run_cli(tmp, "complete-handoff", handoff_id, "--summary", "done")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")

    def test_claim_write_failure_leaves_handoff_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "handoff-failure",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            ready_path = ready_handoff_path(tmp, "handoff-failure")
            claimed_path = claimed_handoff_path(tmp, "handoff-failure")

            def fail_claimed_write(path, data):
                if path == claimed_path:
                    raise OSError("write failed")
                raise AssertionError("unexpected write")

            with mock.patch("codex_cadence.cli.atomic_write_json", fail_claimed_write):
                with self.assertRaisesRegex(OSError, "write failed"):
                    import codex_cadence.cli as cadence

                    args = type(
                        "Args",
                        (),
                        {"root": Path(tmp), "handoff_id": "handoff-failure", "claimer": "test-agent"},
                    )()
                    cadence.claim_handoff(args)

            self.assertTrue(ready_path.exists())
            self.assertFalse(claimed_path.exists())
            self.assertEqual(json.loads(ready_path.read_text(encoding="utf-8"))["status"], "READY")

    def test_claim_handoff_uses_runtime_lock_for_brake_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "runtime-locked",
                "--title",
                "Review PR 1",
                "--task-type",
                "execution",
                "--message",
                "Continue from here.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            with exclusive_lock(lock_path(Path(tmp), "runtime")):
                result, output = run_cli(tmp, "claim-handoff", "runtime-locked", "--claimer", "test-agent")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("lock already held: runtime.lock", result.stderr)
            self.assertTrue(ready_handoff_path(tmp, "runtime-locked").exists())

    def test_claim_blocks_unsized_handoff_without_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "unsized-stripped-1",
                "--title",
                "Stripped estimate",
                "--task-type",
                "execution",
                "--message",
                "Estimate fields were stripped after creation.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rewrite_ready_handoff(
                tmp,
                "unsized-stripped-1",
                lambda data: (data.pop("estimate"), data.pop("estimate_input"), data.pop("estimate_checksum")),
            )

            result, output = run_cli(tmp, "claim-handoff", "unsized-stripped-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "handoff estimate is required for pickup")
            self.assertTrue(ready_handoff_path(tmp, "unsized-stripped-1").exists())

    def test_create_handoff_requires_task_type_for_new_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "unsized-1",
                "--title",
                "Unsized",
                "--message",
                "This handoff has no task sizing.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--task-type", result.stderr)
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "unsized-1.json").exists())

    def test_create_handoff_rejects_path_traversal_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "create-handoff",
                "--id",
                r"..\escaped",
                "--title",
                "Escaped",
                "--task-type",
                "execution",
                "--message",
                "Do not escape state directories.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("handoff id", result.stderr)
            self.assertFalse((Path(tmp) / "handoffs" / "escaped.json").exists())

    def test_claim_blocks_estimated_handoff_missing_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "missing-estimate-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "missing-estimate-1", lambda data: data.pop("estimate"))

            result, output = run_cli(tmp, "claim-handoff", "missing-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "handoff estimate is required for pickup")
            self.assertTrue(path.exists())

    def test_claim_blocks_pre_binding_estimated_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "pre-binding-estimate-1",
                "--title",
                "Small execution",
                "--task-type",
                "execution",
                "--driver",
                "reviewer_feedback",
                "--message",
                "Resolve one reviewer finding.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def remove_binding(data):
                data.pop("estimate_input")
                data.pop("estimate_checksum")

            path = rewrite_ready_handoff(tmp, "pre-binding-estimate-1", remove_binding)

            result, output = run_cli(tmp, "claim-handoff", "pre-binding-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "estimate input must be an object")
            self.assertTrue(path.exists())

    def test_brake_blocks_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "blocked-1",
                "--title",
                "Blocked",
                "--task-type",
                "execution",
                "--message",
                "Do not pick this up.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, brake = run_cli(tmp, "set-brake", "NEUTRAL", "--reason", "manual brake")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "NEUTRAL")

            result, claim = run_cli(tmp, "claim-handoff", "blocked-1", "--claimer", "test-agent")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(claim["claimed"])
            self.assertEqual(claim["blocked_by_brake"]["status"], "NEUTRAL")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "blocked-1.json").exists())

    def test_set_brake_cannot_clear_to_drive_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(tmp, "set-brake", "DRIVE", "--reason", "resume")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("invalid choice", result.stderr)

    def test_clear_brake_returns_to_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, cleared = run_cli(tmp, "clear-brake", "--reason", "operator approved resume")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cleared["status"], "DRIVE")
            self.assertEqual(cleared["reason"], "operator approved resume")

    def test_move_handoff_unlink_failure_rolls_back_target_state(self):
        from codex_cadence.cli import move_handoff

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_cli(root, "init")
            handoff_id = "handoff-rollback"
            ready_path = ready_handoff_path(root, handoff_id)
            ready_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "v1",
                        "id": handoff_id,
                        "status": "READY",
                        "checksum": "sha256:abc",
                        "signature": "sig",
                        "message": "test",
                    }
                ),
                encoding="utf-8",
            )
            claimed_path = claimed_handoff_path(root, handoff_id)
            original_unlink = Path.unlink

            def fail_ready_unlink(path):
                if path == ready_path:
                    raise OSError("unlink failed")
                return original_unlink(path)

            with mock.patch("pathlib.Path.unlink", fail_ready_unlink):
                with self.assertRaisesRegex(OSError, "unlink failed"):
                    move_handoff(root, handoff_id, "ready", "claimed", {"claimed_by": "tester"})

            self.assertTrue(ready_path.exists())
            self.assertFalse(claimed_path.exists())

    def test_claim_blocks_xl_handoff_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "xl-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "xl-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "decompose_or_ask_approval")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "xl-1.json").exists())

    def test_claim_blocks_xl_handoff_with_missing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "missing-policy-1",
                "--title",
                "Large execution",
                "--task-type",
                "execution",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "irreversible_operation",
                "--message",
                "Execute a large change.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "missing-policy-1", lambda data: data["estimate"].pop("policy"))

            result, output = run_cli(tmp, "claim-handoff", "missing-policy-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_blocks_internally_inconsistent_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "spoofed-estimate-1",
                "--title",
                "Large execution",
                "--task-type",
                "execution",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "irreversible_operation",
                "--message",
                "Execute a large change.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def spoof(data):
                data["estimate"]["bucket"] = "S"
                data["estimate"]["policy"] = {
                    "action": "pick_up",
                    "check_in_every_minutes": 10,
                    "handoff_after_minutes": 25,
                    "pickup_requires_approval": False,
                }

            path = rewrite_ready_handoff(tmp, "spoofed-estimate-1", spoof)

            result, output = run_cli(tmp, "claim-handoff", "spoofed-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertIn("canonical", output["blocked_by_policy"]["reason"])
            self.assertTrue(path.exists())

    def test_claim_blocks_self_consistent_downgraded_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "downgraded-estimate-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def downgrade(data):
                source = {"task_type": "execution", "drivers": ["reviewer_feedback"]}
                data["estimate_input"] = source
                data["estimate"] = estimate_task(
                    title=data["title"],
                    message=data["message"],
                    task_type=source["task_type"],
                    drivers=source["drivers"],
                )

            path = rewrite_ready_handoff(tmp, "downgraded-estimate-1", downgrade)

            result, output = run_cli(tmp, "claim-handoff", "downgraded-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertEqual(output["blocked_by_policy"]["reason"], "estimate checksum mismatch")
            self.assertTrue(path.exists())

    def test_claim_blocks_non_dict_estimate_predictably(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-estimate-1",
                "--title",
                "Bad estimate",
                "--task-type",
                "execution",
                "--message",
                "Persisted estimate is corrupt.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "bad-estimate-1", lambda data: data.update({"estimate": "bad"}))

            result, output = run_cli(tmp, "claim-handoff", "bad-estimate-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_blocks_non_dict_estimate_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-policy-1",
                "--title",
                "Bad policy",
                "--task-type",
                "execution",
                "--message",
                "Persisted policy is corrupt.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = rewrite_ready_handoff(tmp, "bad-policy-1", lambda data: data["estimate"].update({"policy": ["bad"]}))

            result, output = run_cli(tmp, "claim-handoff", "bad-policy-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "malformed_estimate")
            self.assertTrue(path.exists())

    def test_claim_allows_xl_handoff_with_operator_approval_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "xl-approved-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, approval = run_cli(tmp, "approve-handoff", "xl-approved-1", "--approver", "operator")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(approval["handoff_id"], "xl-approved-1")
            self.assertTrue((Path(tmp) / "approvals" / "xl-approved-1.json").exists())

            result, output = run_cli(tmp, "claim-handoff", "xl-approved-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["status"], "CLAIMED")

    def test_claim_blocks_approval_gated_handoff_with_self_attested_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "bad-metadata-1",
                "--title",
                "Large migration",
                "--task-type",
                "discovery",
                "--driver",
                "migration",
                "--driver",
                "cross_subsystem",
                "--driver",
                "unknown_repo_area",
                "--metadata",
                "pickup_approved=true",
                "--message",
                "Investigate and migrate several subsystems.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "bad-metadata-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertTrue(output["blocked_by_policy"]["pickup_requires_approval"])
            self.assertEqual(output["blocked_by_policy"]["action"], "decompose_or_ask_approval")
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "bad-metadata-1.json").exists())

    def test_claim_blocks_self_evolution_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "self-evolution-execution-1",
                "--title",
                "Rewrite protocol",
                "--task-type",
                "execution",
                "--driver",
                "self_evolution",
                "--message",
                "Modify the protocol rules directly.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "claim-handoff", "self-evolution-execution-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertEqual(output["blocked_by_policy"]["action"], "self_evolution_propose_only")

    def test_claim_blocks_high_uncertainty_discovery_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "uncertain-discovery-1",
                "--title",
                "Unknown area discovery",
                "--task-type",
                "discovery",
                "--driver",
                "unknown_repo_area",
                "--driver",
                "cross_subsystem",
                "--message",
                "Investigate the unfamiliar subsystem.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["estimate"]["uncertainty"]["level"], "high")

            result, output = run_cli(tmp, "claim-handoff", "uncertain-discovery-1", "--claimer", "test-agent")

            self.assertEqual(result.returncode, 3)
            self.assertFalse(output["claimed"])
            self.assertTrue(output["blocked_by_policy"]["pickup_requires_approval"])
            self.assertTrue((Path(tmp) / "handoffs" / "ready" / "uncertain-discovery-1.json").exists())

    def test_validate_detects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "checksum-1",
                "--title",
                "Checksum",
                "--task-type",
                "execution",
                "--message",
                "Original.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(tmp) / "handoffs" / "ready" / "checksum-1.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["message"] = "Tampered."
            path.write_text(json.dumps(data), encoding="utf-8")

            result, validation = run_cli(tmp, "validate-handoff", "checksum-1")
            self.assertEqual(result.returncode, 1)
            self.assertFalse(validation["valid"])
            self.assertIn("checksum mismatch", validation["errors"])

    def test_clean_square_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "clean-1",
                "--title",
                "Clean",
                "--task-type",
                "execution",
                "--message",
                "Ready for pickup.",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, log = run_cli(tmp, "clean-square", "clean-1", "--summary", "old session stopped")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(log["checks"]["handoff_written"])
            self.assertTrue((Path(tmp) / "logs" / "clean-square" / "clean-1.json").exists())

    def test_plan_task_outputs_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(
                tmp,
                "plan-task",
                "--title",
                "Fix CI",
                "--task-type",
                "execution",
                "--driver",
                "ci_verification",
                "--message",
                "Resolve one failing test.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["title"], "Fix CI")
            self.assertEqual(output["estimate"]["task_type"], "execution")
            self.assertIn(output["estimate"]["bucket"], ["S", "M"])

    def test_create_handoff_can_embed_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, created = run_cli(
                tmp,
                "create-handoff",
                "--id",
                "estimated-1",
                "--title",
                "Investigate architecture",
                "--task-type",
                "discovery",
                "--driver",
                "unknown_repo_area",
                "--driver",
                "cross_subsystem",
                "--message",
                "Investigate why two subsystems disagree.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(created["estimate"]["task_type"], "discovery")
            self.assertEqual(created["estimate_input"]["task_type"], "discovery")
            self.assertIsNotNone(created["estimate_checksum"])
            self.assertTrue(created["estimate"]["policy"]["pickup_requires_approval"])

    def test_snapshot_repo_command_writes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, snapshot = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(snapshot["repo"], "local/test")
            self.assertEqual(snapshot["repo_confidence"], "high")
            self.assertEqual(snapshot["path"], str(Path(tmp) / "snapshots" / f"{snapshot['id']}.json"))
            self.assertTrue((Path(tmp) / "snapshots" / f"{snapshot['id']}.json").exists())

    def test_snapshot_repo_command_repeated_calls_write_distinct_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            first_result, first = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")
            second_result, second = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test")

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertNotEqual(first["id"], second["id"])
            self.assertTrue((Path(tmp) / "snapshots" / f"{first['id']}.json").exists())
            self.assertTrue((Path(tmp) / "snapshots" / f"{second['id']}.json").exists())
            self.assertEqual(len(list((Path(tmp) / "snapshots").glob("*.json"))), 2)

    def test_snapshot_repo_command_invalid_cwd_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            result, output = run_cli(tmp, "snapshot-repo", "--cwd", str(missing), "--repo", "local/test")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_snapshot_repo_command_records_explicit_ci_status(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, output = run_cli(tmp, "snapshot-repo", "--cwd", repo_tmp, "--repo", "local/test", "--ci-status", "green")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["ci"], "green")

    def test_snapshot_repo_rejects_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_snapshot_repo_allows_ignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            (repo / ".gitignore").write_text(".cadence-runtime/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore cadence runtime")
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_snapshot_repo_allows_parent_ignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            (repo / ".gitignore").write_text(".codex/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore codex runtime")
            runtime_root = repo / ".codex" / "cadence"

            result, output = run_cli(runtime_root, "snapshot-repo", "--cwd", repo, "--repo", "local/test")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_snapshot_repo_allows_explicit_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(
                runtime_root,
                "--allow-repo-local-root",
                "snapshot-repo",
                "--cwd",
                repo,
                "--repo",
                "local/test",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo"], "local/test")
            self.assertTrue(runtime_root.exists())

    def test_prepare_handoff_rejects_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(
                runtime_root,
                "prepare-handoff",
                "--title",
                "Context handoff",
                "--guardrail",
                "manual",
                "--cwd",
                repo,
                "--task-type",
                "execution",
                "--summary",
                "Summarize current progress.",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_status_rejects_unignored_repo_local_runtime_root_from_current_repo(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli_from(repo, runtime_root, "status")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_plan_task_does_not_guard_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli_from(
                repo,
                runtime_root,
                "plan-task",
                "--title",
                "Plan a small task",
                "--task-type",
                "execution",
                "--message",
                "Implement a small task.",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["title"], "Plan a small task")
            self.assertFalse(runtime_root.exists())

    def test_discover_candidates_does_not_guard_unignored_repo_local_runtime_root(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            repo = Path(repo_tmp)
            init_committed_repo(repo)
            runtime_root = repo / ".cadence-runtime"

            result, output = run_cli(runtime_root, "discover-candidates", "--cwd", repo)

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("intent is required unless --interactive or --discovery-mode off is used", result.stderr)
            self.assertNotIn("runtime root is inside target repo", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_discover_candidates_requires_intent_for_local_mode(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intent is required unless --interactive or --discovery-mode off is used", result.stderr)

    def test_discover_candidates_expanded_mode_fails_closed_without_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo, "--discovery-mode", "expanded")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("expanded discovery mode is reserved for v2", result.stderr)
            self.assertNotIn("intent is required", result.stderr)

    def test_discover_candidates_expanded_mode_does_not_prompt_interactive_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(tmp),
                    "discover-candidates",
                    "--cwd",
                    repo,
                    "--discovery-mode",
                    "expanded",
                    "--interactive",
                ],
                input="1\n",
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expanded discovery mode is reserved for v2", result.stderr)
            self.assertNotIn("Choose discovery intent", result.stderr)

    def test_discover_candidates_off_does_not_require_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(tmp, "discover-candidates", "--cwd", repo, "--discovery-mode", "off")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["candidates"], [])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["sources"]["review_findings"], 0)
            self.assertEqual(output["sources"]["text_markers"], 0)
            self.assertEqual(output["sources"]["proposals"], 0)

    def test_discover_candidates_elects_known_failure(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "discover-candidates",
                "--cwd",
                repo,
                "--intent",
                "merge_readiness",
                "--known-failure",
                "unit tests",
                "--elect",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["elected_next"][0]["source"], "known_failure")

    def test_discover_candidates_accepts_review_threads_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            threads = Path(tmp) / "threads.json"
            threads.write_text(
                json.dumps({
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "id": "thread-1",
                                            "isResolved": False,
                                            "isOutdated": False,
                                            "path": "codex_cadence/candidates.py",
                                            "line": 448,
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "id": "comment-1",
                                                        "body": "Map this thread to a finding.",
                                                        "outdated": False,
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "discover-candidates",
                "--cwd",
                repo,
                "--intent",
                "merge_readiness",
                "--review-threads-file",
                str(threads),
                "--elect",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["sources"]["review_findings"], 1)
            self.assertEqual(output["elected_next"][0]["source"], "review_finding")

    def test_discover_candidates_interactive_reads_intent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(tmp),
                    "discover-candidates",
                    "--cwd",
                    repo,
                    "--interactive",
                ],
                input="1\n",
                text=True,
                capture_output=True,
                check=False,
            )
            output = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["intent"], "merge_readiness")

    def test_loop_tick_reports_no_candidates_without_starting_execution(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "no_candidates")
            self.assertEqual(output["reason"], "no elected candidate")
            self.assertTrue(output["read_only"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["snapshot"]["repo"], "local/test")
            self.assertTrue(Path(output["snapshot"]["path"]).exists())
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_tick_stops_at_executor_contract_for_elected_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "requires_executor_contract")
            self.assertEqual(output["reason"], "executor task packet has not been emitted")
            self.assertTrue(output["executor_contract_required"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertIn("executor_contract_not_implemented", output["limitations"])
            self.assertEqual(output["cadence"]["state"], "PLAY_ON")
            self.assertEqual(output["snapshot"]["repo_confidence"], "high")
            self.assertEqual(output["elected_next"][0]["source"], "text_marker")
            self.assertEqual(output["candidate_discovery"]["elected_next"][0]["source"], "text_marker")
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_tick_can_emit_generic_executor_task_without_starting_execution(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            evidence_path = Path(tmp) / "executor-result.json"

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "codex_cadence",
                "--allowed-path",
                "tests",
                "--required-check",
                "python -m unittest tests.test_cadence",
                "--executor-evidence-path",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertEqual(output["reason"], "executor task packet emitted for operator approval")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["executor_contract_required"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["packet"], "executor_task")
            self.assertEqual(executor_task["schema_version"], "generic-executor-task.v1")
            self.assertEqual(executor_task["task"]["id"], output["elected_next"][0]["id"])
            self.assertEqual(executor_task["repo"]["path"], str(Path(repo).resolve()))
            self.assertEqual(executor_task["allowed_paths"], ["codex_cadence", "tests"])
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(executor_task["expected_output"]["evidence_path"], str(evidence_path))
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_loop_tick_records_audit_entry_for_executor_task_decision(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "tests",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "loop_tick_decision")
            self.assertTrue(Path(audit_ref["path"]).exists())
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "loop_tick_decision")
            self.assertEqual(record["action"], "approve_executor_task")
            self.assertEqual(record["tick_id"], output["tick_id"])
            self.assertEqual(record["repo"], "local/test")
            self.assertEqual(record["branch"], output["snapshot"]["branch"])
            self.assertEqual(record["head"], output["snapshot"]["head"])
            self.assertEqual(record["executor_task_id"], output["executor_task"]["task"]["id"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))

    def test_loop_tick_policy_file_bounds_executor_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "required_checks": ["python -m unittest tests.test_cadence"],
                        "max_executor_time_minutes": 15,
                        "stop_conditions": ["brake_not_drive", "timeout"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["allowed_paths"], ["codex_cadence"])
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(executor_task["limits"]["max_minutes"], 15)
            self.assertEqual(
                executor_task["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout"],
            )
            self.assertEqual(output["policy"]["source"], str(policy_file))

    def test_loop_tick_policy_file_emits_executor_command_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "allowed_commands": ["python -m unittest tests.test_cadence"],
                        "denied_commands": ["python -m pip install"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approve_executor_task")
            self.assertEqual(
                output["executor_task"]["command_policy"],
                {
                    "allowed_commands": ["python -m unittest tests.test_cadence"],
                    "denied_commands": ["python -m pip install"],
                },
            )
            self.assertEqual(output["policy"]["allowed_commands"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(output["policy"]["denied_commands"], ["python -m pip install"])

    def test_loop_tick_policy_file_keeps_policy_stop_conditions_with_cli_additions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "stop_conditions": ["requires_review"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--stop-condition",
                "custom_stop",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output["executor_task"]["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "requires_review", "custom_stop"],
            )

    def test_loop_tick_policy_file_trims_checks_and_stop_conditions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "required_checks": [" python -m unittest tests.test_cadence "],
                        "stop_conditions": [" requires_review "],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["required_checks"], ["python -m unittest tests.test_cadence"])
            self.assertEqual(
                executor_task["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "requires_review"],
            )

    def test_loop_tick_policy_file_rejects_allowed_path_with_nul(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence\0bad"],
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("loop policy allowed_paths[0] must be repo-relative", result.stderr)

    def test_loop_tick_keeps_builtin_stop_conditions_with_cli_additions(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "codex_cadence",
                "--stop-condition",
                "custom_stop",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output["executor_task"]["stop_conditions"],
                ["brake_not_drive", "operator_stop", "context_pressure", "timeout", "custom_stop"],
            )

    def test_loop_tick_policy_file_denies_disallowed_executor_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "denied_paths": ["tests"],
                        "max_executor_time_minutes": 15,
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--allowed-path",
                "tests",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "policy_denied")
            self.assertEqual(output["reason"], "executor allowed path tests is denied by policy")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["executor_contract_required"])
            self.assertIsNone(output["executor_task"])
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "loop_tick_decision")
            self.assertEqual(record["action"], "policy_denied")
            self.assertEqual(record["reason"], "executor allowed path tests is denied by policy")

    def test_loop_tick_policy_file_denies_executor_time_over_cap(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "allowed_paths": ["codex_cadence"],
                        "max_executor_time_minutes": 15,
                    }
                ),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--policy-file",
                str(policy_file),
                "--executor-time-limit-minutes",
                "20",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "policy_denied")
            self.assertEqual(output["reason"], "executor time limit exceeds policy max_executor_time_minutes")
            self.assertIsNone(output["executor_task"])

    def test_loop_tick_rejects_malformed_cli_allowed_path_without_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
                "--emit-executor-task",
                "--allowed-path",
                "../outside",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("executor allowed path ../outside must be repo-relative", result.stderr)

    def test_loop_tick_requires_approval_for_low_confidence_repo(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "merge_readiness",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("dirty_worktree", output["snapshot"]["repo_confidence_drivers"])
            self.assertEqual(output["elected_next"][0]["source"], "git_status")
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_requires_approval_for_low_confidence_without_candidates(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--discovery-mode",
                "off",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["elected_next"], [])
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("dirty_worktree", output["snapshot"]["repo_confidence_drivers"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_requires_approval_for_red_ci_signal(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--active-pr",
                "49",
                "--ci-status",
                "red",
                "--discovery-mode",
                "off",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "approval_required")
            self.assertEqual(output["reason"], "repo confidence is low")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertEqual(output["snapshot"]["ci"], "red")
            self.assertEqual(output["snapshot"]["active_pr"], 49)
            self.assertEqual(output["snapshot"]["repo_confidence"], "low")
            self.assertIn("red_ci", output["snapshot"]["repo_confidence_drivers"])
            self.assertEqual(output["elected_next"], [])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_loop_tick_blocks_when_cadence_state_disallows_work(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            result, _brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "loop-tick",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "blocked")
            self.assertEqual(output["reason"], "cadence state is TIMEOUT")
            self.assertEqual(output["cadence"]["state"], "TIMEOUT")
            self.assertFalse(output["cadence"]["can_start_work"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])

    def test_controlled_executor_fixture_runs_success_and_validates_result(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["executor_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertEqual(output["packet"], "controlled_executor_fixture_run")
            self.assertEqual(output["schema_version"], "controlled-executor-fixture-run.v1")
            self.assertEqual(output["result_status"], "succeeded")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            self.assertEqual(output["task_file"], str(task_path))
            self.assertEqual(output["result_file"], str(evidence_path))
            result_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result_evidence["task_id"], task_packet["task"]["id"])
            self.assertEqual(result_evidence["resulting_head"], task_packet["repo"]["head"])
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 2)
            invocation_record = json.loads(audit_lines[0])
            validation_record = json.loads(audit_lines[1])
            self.assertEqual(invocation_record["event"], "executor_fixture_invocation")
            self.assertEqual(invocation_record["action"], "start_controlled_executor_fixture")
            self.assertEqual(invocation_record["task_id"], "candidate-1")
            self.assertEqual(validation_record["event"], "executor_result_validation")
            self.assertTrue(validation_record["valid"])
            replay_result, replay_output = run_cli(tmp, "audit-replay")
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["records_valid"], 2)
            self.assertEqual(replay_output["events_by_type"]["executor_fixture_invocation"], 1)
            self.assertEqual(replay_output["events_by_type"]["executor_result_validation"], 1)

    def test_controlled_executor_fixture_accepts_failed_evidence_from_nonzero_command(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="failed", exit_code=7, summary="Fixture command failed."),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["command_exit_code"], 7)
            self.assertEqual(output["result_status"], "failed")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")

    def test_controlled_executor_fixture_timeout_writes_stopped_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded", sleep_seconds=5),
                "--timeout-seconds",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertTrue(output["timed_out"])
            self.assertEqual(output["result_status"], "stopped")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            result_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(result_evidence["status"], "stopped")
            self.assertIn("timeout", result_evidence["blockers"])

    def test_controlled_executor_fixture_rejects_untrusted_python_executable_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            fake_python = Path(tmp) / "bin" / "python.exe"

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(executable=fake_python),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command template must use the current Python interpreter")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_allows_stopped_evidence_over_observed_runtime_limit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            with mock.patch("codex_cadence.executor_runner.time.monotonic", side_effect=[0.0, 120.0]):
                output = run_controlled_executor_fixture(
                    root=Path(tmp),
                    task_file=task_path,
                    command_template=controlled_fixture_command(status="stopped", summary="Fixture stopped."),
                    timeout_seconds=10,
                )

            self.assertTrue(output["valid"], output["reason"])
            self.assertEqual(output["result_status"], "stopped")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")

    def test_controlled_executor_fixture_rejects_success_after_active_brake_stop(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "stop_active_loop")
            self.assertEqual(output["active_stop"]["brake_status"], "PARK")

    def test_controlled_executor_fixture_blocks_disallowed_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(command="git push origin main"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command violates disabled push permission")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_policy")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_enforces_task_command_policy_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(
                tmp,
                repo,
                command_policy={"denied_commands": ["python -m pip install"]},
            )

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(command="python -m pip install ."),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command is denied by command_policy")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_policy")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_malformed_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                "python -c {missing_placeholder}",
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertIn("invalid executor command template", output["reason"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_non_fixture_command_template_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                f'"{sys.executable}" -m unittest tests.test_cadence',
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor command template must invoke the controlled fixture script")
            self.assertEqual(output["recommended_next_action"], "fix_executor_command_template")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_evidence_path_outside_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(
                tmp,
                repo,
                evidence_name=str(Path(outside) / "executor-result.json"),
            )

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor result evidence path must stay inside the runtime root")
            self.assertEqual(output["recommended_next_action"], "fix_executor_task_packet")
            self.assertFalse(evidence_path.exists())

    def test_controlled_executor_fixture_rejects_existing_result_file_before_running(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)
            evidence_path.write_text("preserve me\n", encoding="utf-8")

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded"),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertFalse(output["executor_started"])
            self.assertEqual(output["reason"], "executor result evidence file already exists")
            self.assertEqual(output["recommended_next_action"], "remove_stale_executor_evidence")
            self.assertEqual(evidence_path.read_text(encoding="utf-8"), "preserve me\n")

    def test_controlled_executor_fixture_rejects_success_evidence_from_nonzero_command(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, _evidence_path, _task_packet = write_controlled_fixture_task(tmp, repo)

            result, output = run_cli(
                tmp,
                "run-controlled-executor-fixture",
                "--task-file",
                str(task_path),
                "--command-template",
                controlled_fixture_command(status="succeeded", exit_code=7),
                "--timeout-seconds",
                "10",
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["command_exit_code"], 7)
            self.assertEqual(output["reason"], "executor fixture command exit code must be 0 when result status is succeeded")
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")

    def test_validate_executor_result_command_reports_valid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["reason"], "ok")
            self.assertEqual(output["recommended_next_action"], "record_executor_result")
            self.assertFalse(output["executor_started"])
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "executor_result_validation")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "record_executor_result")
            self.assertEqual(record["task_id"], "candidate-1")
            self.assertTrue(record["valid"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_rejects_success_after_active_brake_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "summary": "Fake executor completed after an operator stop.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "cadence brake is PARK; executor result must report stopped before completion can be recorded",
            )
            self.assertEqual(output["recommended_next_action"], "stop_active_loop")
            self.assertEqual(output["active_stop"]["brake_status"], "PARK")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "stop_active_loop")
            self.assertFalse(record["valid"])

    def test_validate_executor_result_requires_root_for_completion_with_brake_stop_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "summary": "Fake executor completed without proving the current brake state.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")
            env = {
                **os.environ,
                "HOME": tmp,
                "USERPROFILE": tmp,
            }
            env.pop("CODEX_CADENCE_ROOT", None)
            env.pop("CODEX_TRANSMISSION_ROOT", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "validate-executor-result",
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(evidence_path),
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            output = json.loads(result.stdout)

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["reason"], "runtime root is required to validate brake_not_drive stop condition")
            self.assertEqual(output["recommended_next_action"], "provide_runtime_root")

    def test_validate_executor_result_rejects_unexpected_result_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected_path = root / "expected-result.json"
            actual_path = root / "actual-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=expected_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            actual_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(actual_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["reason"], "executor result file does not match task expected_output.evidence_path")
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")

    def test_validate_executor_result_rejects_unignored_repo_local_audit_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(repo).resolve())),
                repo_path=repo,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "resulting_head": valid_snapshot(cwd=str(Path(repo).resolve()))["head"],
            }
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_validate_executor_result_command_exits_nonzero_for_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "executor-result.json"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(root)),
                repo_path=root,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=evidence_path,
            )
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
                "commands_run": [],
                "validation_results": [],
                "summary": "Fake executor claimed success without checks.",
                "confidence": "high",
                "blockers": [],
                "dirty_worktree": False,
                "resulting_head": valid_snapshot(cwd=str(root))["head"],
            }
            task_path = root / "executor-task.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            evidence_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(evidence_path),
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")
            audit_ref = output["audit_record"]
            self.assertEqual(audit_ref["event"], "executor_result_validation")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "fix_executor_evidence")
            self.assertFalse(record["valid"])
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = []
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task packet must be a JSON object",
            )
            self.assertEqual(output["recommended_next_action"], "fix_executor_evidence")
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["schema_version"], "cadence-audit.v1")
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["action"], "fix_executor_evidence")
            self.assertFalse(record["valid"])
            self.assertNotIn("task_id", record)
            payload_without_audit = dict(output)
            payload_without_audit.pop("audit_record")
            self.assertEqual(record["payload_checksum"], checksum_json(payload_without_audit))
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_repo_path_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(tmp).resolve())),
                repo_path=tmp,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_packet["repo"]["path"] = ["bad"]
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task repo.path is required",
            )
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_audits_malformed_repo_path_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            malformed_path = f"{Path(tmp).anchor}bad\0path"
            task_packet = build_executor_task_packet(
                task={
                    "id": "candidate-1",
                    "title": "Implement bounded executor task",
                    "summary": "Create generic executor evidence.",
                    "task_type": "execution",
                    "bucket": "S",
                    "source": "text_marker",
                    "drivers": [],
                    "evidence": {"path": "docs/roadmap.md"},
                },
                snapshot=valid_snapshot(cwd=str(Path(tmp).resolve())),
                repo_path=tmp,
                allowed_paths=["codex_cadence"],
                required_checks=["python -m unittest tests.test_executor_contract"],
                max_minutes=30,
                max_tasks=1,
                stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
                evidence_path=Path(tmp) / "executor-result.json",
            )
            task_packet["snapshot"]["cwd"] = malformed_path
            task_packet["repo"]["path"] = malformed_path
            result_evidence = {
                "schema_version": "generic-executor-result.v1",
                "packet": "executor_result",
                "task_id": "candidate-1",
                "executor_id": "fake-executor",
                "started_at": "2999-05-22T00:00:00Z",
                "ended_at": "2999-05-22T00:05:00Z",
                "status": "succeeded",
                "files_changed": ["codex_cadence/executor_contract.py"],
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
                "resulting_head": task_packet["repo"]["head"],
            }
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(output["valid"])
            self.assertEqual(
                output["reason"],
                "invalid executor task packet: executor task snapshot cwd and repo.path must be absolute local paths",
            )
            audit_lines = (Path(tmp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 1)
            record = json.loads(audit_lines[0])
            self.assertEqual(record["event"], "executor_result_validation")
            self.assertEqual(record["task_packet_checksum"], checksum_json(task_packet))
            self.assertEqual(record["result_evidence_checksum"], checksum_json(result_evidence))

    def test_validate_executor_result_rejects_repo_local_root_with_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = {"schema_version": "generic-executor-task.v1"}
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli_from(
                repo,
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_validate_executor_result_rejects_repo_local_root_from_outside_repo_with_malformed_task_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            init_committed_repo(repo)
            runtime_root = Path(repo) / ".cadence-runtime"
            task_packet = {"schema_version": "generic-executor-task.v1"}
            result_evidence = {"schema_version": "generic-executor-result.v1"}
            task_path = Path(tmp) / "executor-task.json"
            result_path = Path(tmp) / "executor-result.json"
            task_path.write_text(json.dumps(task_packet), encoding="utf-8")
            result_path.write_text(json.dumps(result_evidence), encoding="utf-8")

            result, output = run_cli_from(
                outside,
                runtime_root,
                "validate-executor-result",
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("runtime root is inside target repo but is not ignored", result.stderr)
            self.assertFalse(runtime_root.exists())

    def test_epoch_cli_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(epoch["status"], "ACTIVE")

            result, completed = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "STOP", "--summary", "done")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["decision"], "STOP")

    def test_start_epoch_blocks_when_brake_is_parked(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("start-epoch requires brake DRIVE", result.stderr)
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_fail_epoch_moves_active_epoch_to_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, failed = run_cli(tmp, "fail-epoch", epoch["id"], "--reason", "blocked")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["failure_reason"], "blocked")
            self.assertFalse((Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json").exists())
            self.assertTrue((Path(tmp) / "epochs" / "failed" / f"{epoch['id']}.json").exists())

    def test_complete_epoch_rejects_continue_without_persisted_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE requires a persisted CONTINUE self-check", result.stderr)

    def test_complete_epoch_allows_continue_after_persisted_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")

            result, completed = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["decision"], "CONTINUE")

    def test_self_check_asks_approval_after_completed_epoch_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            snapshot_before_1 = Path(tmp) / "snapshot-before-1.json"
            snapshot_before_1.write_text(json.dumps(valid_snapshot(id="snapshot-before-1")), encoding="utf-8")
            snapshot_after_1 = Path(tmp) / "snapshot-after-1.json"
            snapshot_after_1.write_text(json.dumps(valid_snapshot(id="snapshot-after-1")), encoding="utf-8")
            result, first = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_1),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                first["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_1),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")
            self.assertEqual(check["completed_continue_count"], 0)
            result, completed = run_cli(tmp, "complete-epoch", first["id"], "--decision", "CONTINUE")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(completed["decision"], "CONTINUE")

            snapshot_before_2 = Path(tmp) / "snapshot-before-2.json"
            snapshot_before_2.write_text(json.dumps(valid_snapshot(id="snapshot-before-2")), encoding="utf-8")
            snapshot_after_2 = Path(tmp) / "snapshot-after-2.json"
            snapshot_after_2.write_text(json.dumps(valid_snapshot(id="snapshot-after-2")), encoding="utf-8")
            result, second = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_2),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                second["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_2),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["completed_continue_count"], 1)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "max_epochs_without_user_approval reached")

    def test_self_check_requires_green_ci_for_default_continuation_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", ci="unknown")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "green CI or explicit handoff required")

    def test_complete_epoch_rejects_continue_when_epoch_exceeds_time_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            active_path = Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            from codex_cadence.epochs import checksum_json as checksum_epoch_json

            snapshot_after = valid_snapshot(id="snapshot-after")
            persisted_snapshot_after = persisted_snapshot_path(Path(tmp), snapshot_after["id"])
            persisted_snapshot_after.parent.mkdir(parents=True, exist_ok=True)
            persisted_snapshot_after.write_text(json.dumps(snapshot_after), encoding="utf-8")
            active["started_at"] = "2026-05-22T00:00:00Z"
            active["last_self_check"] = {
                "epoch_id": epoch["id"],
                "decision": "CONTINUE",
                "epoch_grounded": True,
                "current_snapshot_grounded": True,
                "brake_status": "DRIVE",
                "elected_next": [{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                "epoch_policy_checksum": "sha256:stale",
                "snapshot_before_id": active["snapshot_before"]["id"],
                "snapshot_before_checksum": checksum_epoch_json(active["snapshot_before"]),
                "snapshot_after_id": snapshot_after["id"],
                "snapshot_after_checksum": checksum_epoch_json(snapshot_after),
                "completed_continue_count": 0,
                "current_snapshot_ci": "green",
            }
            active["last_self_check"]["epoch_policy_checksum"] = checksum_epoch_json(active["policy"])
            active_path.write_text(json.dumps(active), encoding="utf-8")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE exceeds max_minutes_per_epoch", result.stderr)

    def test_complete_epoch_rejects_replayed_continue_self_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            active_path = Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            from codex_cadence.epochs import checksum_json as checksum_epoch_json

            active["last_self_check"] = {
                "epoch_id": "other-epoch",
                "decision": "CONTINUE",
                "epoch_grounded": True,
                "current_snapshot_grounded": True,
                "brake_status": "DRIVE",
                "elected_next": [{"id": "task-2", "task_type": "execution", "bucket": "S"}],
                "epoch_policy_checksum": checksum_epoch_json(active["policy"]),
                "snapshot_before_id": active["snapshot_before"]["id"],
                "snapshot_before_checksum": checksum_epoch_json(active["snapshot_before"]),
                "completed_continue_count": 0,
                "current_snapshot_ci": "green",
            }
            active_path.write_text(json.dumps(active), encoding="utf-8")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE self-check epoch does not match active epoch", result.stderr)

    def test_complete_epoch_rechecks_brake_before_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")
            result, brake = run_cli(tmp, "set-brake", "PARK", "--reason", "operator stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(brake["status"], "PARK")

            result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("CONTINUE requires brake to remain DRIVE", result.stderr)

    def test_complete_epoch_continue_uses_runtime_lock_for_brake_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, check = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(check["decision"], "CONTINUE")

            with exclusive_lock(lock_path(Path(tmp), "runtime")):
                result, output = run_cli(tmp, "complete-epoch", epoch["id"], "--decision", "CONTINUE")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("lock already held: runtime.lock", result.stderr)
            self.assertTrue((Path(tmp) / "epochs" / "active" / f"{epoch['id']}.json").exists())

    def test_start_epoch_rejects_object_tasks_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(json.dumps({"id": "task-1"}), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_start_epoch_requires_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "start-epoch", "--repo", "local/test", "--branch", "main")

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--snapshot-before-file", result.stderr)

    def test_start_epoch_rejects_list_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps([{"id": "snapshot-1"}]), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_start_epoch_rejects_invalid_snapshot_before_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps({"id": "snapshot-1", "repo_confidence": "high"}), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("invalid snapshot_before", result.stderr)

    def test_start_epoch_rejects_snapshot_repo_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(repo="other/repo")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot repo does not match", result.stderr)

    def test_start_epoch_rejects_snapshot_branch_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot(branch="feature")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot branch does not match", result.stderr)

    def test_start_epoch_rejects_existing_active_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_snapshot_path = Path(tmp) / "snapshot-1.json"
            first_snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-1")), encoding="utf-8")
            second_snapshot_path = Path(tmp) / "snapshot-2.json"
            second_snapshot_path.write_text(json.dumps(valid_snapshot(id="snapshot-2")), encoding="utf-8")
            result, _ = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(first_snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(second_snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch already exists", result.stderr)

    def test_start_epoch_rejects_tasks_over_policy_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks = [{"id": f"task-{index}", "task_type": "execution"} for index in range(4)]
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("exceeds max_tasks_per_epoch", result.stderr)

    def test_start_epoch_rejects_too_many_discovery_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks = [
                {"id": "task-1", "task_type": "discovery"},
                {"id": "task-2", "task_type": "discovery"},
            ]
            tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("exceeds max_discovery_tasks_per_epoch", result.stderr)

    def test_start_epoch_requires_repo_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")

            result, output = run_cli(tmp, "start-epoch", "--branch", "main", "--snapshot-before-file", str(snapshot_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--repo", result.stderr)

            result, output = run_cli(tmp, "start-epoch", "--repo", "local/test", "--snapshot-before-file", str(snapshot_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--branch", result.stderr)

    def test_start_epoch_rejects_missing_task_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(json.dumps([{"id": "task-1"}]), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--tasks-file",
                str(tasks_path),
                "--snapshot-before-file",
                str(snapshot_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("task_type must be execution or discovery", result.stderr)

    def test_self_check_blocks_high_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = run_cli(tmp, "self-check", "--uncertainty", "high")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "HANDOFF")

    def test_self_check_uses_candidate_uncertainty_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S", "uncertainty": "high"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")

    def test_self_check_requires_epoch_snapshot_to_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertFalse(output["epoch_grounded"])
            self.assertEqual(output["reason"], "epoch snapshot required for continuation")

    def test_self_check_requires_current_snapshot_to_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(tmp, "self-check", "--epoch-id", epoch["id"], "--candidates-file", str(candidates_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["epoch_grounded"])
            self.assertFalse(output["current_snapshot_grounded"])
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "current repo snapshot required for continuation")

    def test_self_check_rejects_stale_snapshot_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(
                json.dumps(valid_snapshot(id="snapshot-after", captured_at="2000-01-01T00:00:00Z")),
                encoding="utf-8",
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot_after must be captured after epoch start", result.stderr)

    def test_self_check_uses_current_snapshot_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before", repo_confidence="high")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", repo_confidence="low")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["epoch_grounded"])
            self.assertTrue(output["current_snapshot_grounded"])
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")

    def test_self_check_snapshot_confidence_overrides_cli_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_before_path = Path(tmp) / "snapshot-before.json"
            snapshot_before_path.write_text(json.dumps(valid_snapshot(id="snapshot-before", repo_confidence="high")), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after", repo_confidence="low")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_before_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--repo-confidence",
                "high",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")

    def test_self_check_recursive_discovery_flag_does_not_override_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "discovery", "bucket": "S"}]),
                encoding="utf-8",
            )
            result, epoch = run_cli(
                tmp,
                "start-epoch",
                "--repo",
                "local/test",
                "--branch",
                "main",
                "--snapshot-before-file",
                str(snapshot_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                epoch["id"],
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--allow-recursive-discovery",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "recursive discovery requires approval")

    def test_self_check_caps_election_to_stored_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-tight-policy",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 1},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 10},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 9},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-tight-policy",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "2",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_accepts_discover_candidates_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-payload", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-payload",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_honors_discover_candidates_low_repo_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-low-confidence", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "low", "uncertainty": "low"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-low-confidence",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["repo_confidence"], "low")
            self.assertEqual(output["decision"], "ASK_APPROVAL")
            self.assertEqual(output["reason"], "repo confidence is low")

    def test_self_check_honors_discover_candidates_high_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-high-uncertainty", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "high"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-high-uncertainty",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "uncertainty is high")

    def test_self_check_honors_discover_candidates_high_candidate_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-discovery-high-growth", valid_snapshot())
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "low", "candidate_growth": "high"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-high-growth",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.get("candidate_growth"), "high")
            self.assertEqual(output["uncertainty"], "high")
            self.assertEqual(output["decision"], "HANDOFF")
            self.assertEqual(output["reason"], "uncertainty is high")

    def test_self_check_honors_discover_candidates_medium_candidate_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-discovery-medium-growth",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "discover-output.json"
            candidates_path.write_text(
                json.dumps(
                    {
                        "intent": "merge_readiness",
                        "run_signals": {"repo_confidence": "high", "uncertainty": "low", "candidate_growth": "medium"},
                        "candidates": [
                            {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                            {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                            {"id": "task-3", "task_type": "execution", "bucket": "S", "score": 10},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-medium-growth",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.get("candidate_growth"), "medium")
            self.assertEqual(output["uncertainty"], "medium")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_caps_discovery_election_to_stored_epoch_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-discovery-limit",
                valid_snapshot(),
                policy={"allow_recursive_discovery": True, "max_tasks_per_epoch": 3, "max_discovery_tasks_per_epoch": 1},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "discovery-1", "task_type": "discovery", "bucket": "S", "score": 100},
                        {"id": "discovery-2", "task_type": "discovery", "bucket": "S", "score": 90},
                        {"id": "execution-1", "task_type": "execution", "bucket": "S", "score": 10},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-discovery-limit",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["discovery-1", "execution-1"])

    def test_self_check_medium_uncertainty_shrinks_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-medium-uncertainty",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                        {"id": "task-3", "task_type": "execution", "bucket": "S", "score": 10},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-medium-uncertainty",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
                "--uncertainty",
                "medium",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_watch_epoch_health_shrinks_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-watch-health",
                valid_snapshot(),
                policy={"allow_recursive_discovery": False, "max_tasks_per_epoch": 3},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps(
                    [
                        {"id": "task-1", "task_type": "execution", "bucket": "S", "score": 30},
                        {"id": "task-2", "task_type": "execution", "bucket": "S", "score": 20},
                    ]
                ),
                encoding="utf-8",
            )
            snapshot_after_path = Path(tmp) / "snapshot-after.json"
            snapshot_after_path.write_text(json.dumps(valid_snapshot(id="snapshot-after")), encoding="utf-8")

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-watch-health",
                "--candidates-file",
                str(candidates_path),
                "--snapshot-after-file",
                str(snapshot_after_path),
                "--max-tasks",
                "3",
                "--epoch-health",
                "watch",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["decision"], "CONTINUE")
            self.assertEqual(output["effective_max_tasks"], 1)
            self.assertEqual([candidate["id"] for candidate in output["elected_next"]], ["task-1"])

    def test_self_check_rejects_invalid_active_epoch_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(tmp, "epoch-invalid-snapshot", {"repo_confidence": "high"})
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-invalid-snapshot",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("snapshot_before is invalid", result.stderr)

    def test_self_check_rejects_completed_record_in_active_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            path = write_active_epoch(tmp, "epoch-completed-active", valid_snapshot())
            data = json.loads(path.read_text(encoding="utf-8"))
            data["status"] = "COMPLETED"
            path.write_text(json.dumps(data), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-completed-active",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch status must be ACTIVE", result.stderr)

    def test_self_check_rejects_mismatched_active_epoch_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            path = write_active_epoch(tmp, "epoch-path-id", valid_snapshot())
            data = json.loads(path.read_text(encoding="utf-8"))
            data["id"] = "epoch-record-id"
            path.write_text(json.dumps(data), encoding="utf-8")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-path-id",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("active epoch id does not match path", result.stderr)

    def test_self_check_rejects_multiple_active_epochs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch_raw(tmp, "epoch-1")
            write_active_epoch_raw(tmp, "epoch-2")
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "execution", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-1",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("expected exactly one active epoch", result.stderr)

    def test_self_check_rejects_malformed_recursive_discovery_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(tmp, "init")
            write_active_epoch(
                tmp,
                "epoch-bad-policy",
                valid_snapshot(),
                policy={"allow_recursive_discovery": "false"},
            )
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(
                json.dumps([{"id": "task-2", "task_type": "discovery", "bucket": "S"}]),
                encoding="utf-8",
            )

            result, output = run_cli(
                tmp,
                "self-check",
                "--epoch-id",
                "epoch-bad-policy",
                "--candidates-file",
                str(candidates_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("allow_recursive_discovery must be a boolean", result.stderr)

    def test_self_check_rejects_object_candidates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(json.dumps({"id": "task-1"}), encoding="utf-8")

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)

    def test_self_check_rejects_malformed_candidate_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidates_path = Path(tmp) / "candidates.json"
            candidates_path.write_text(json.dumps(["not-a-dict"]), encoding="utf-8")

            result, output = run_cli(tmp, "self-check", "--candidates-file", str(candidates_path))

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("candidate 0 must be a JSON object", result.stderr)


if __name__ == "__main__":
    unittest.main()
