import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence.handoff_loop import build_seed_message, prepare_handoff


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


class HandoffLoopTests(unittest.TestCase):
    def test_seed_message_includes_repo_and_pickup_state(self):
        snapshot = {
            "cwd": r"C:\repo",
            "repo": "owner/repo",
            "branch": "main",
            "head": "abc123",
            "dirty_worktree": False,
            "repo_confidence": "high",
            "repo_confidence_drivers": [],
            "path": r"C:\runtime\snapshots\snapshot.json",
        }
        status = {
            "root": r"C:\runtime",
            "cadence": {"state": "PLAY_ON", "legacy_brake": "DRIVE"},
            "counts": {"ready": 0, "claimed": 0, "completed": 0, "failed": 0},
        }

        message = build_seed_message(
            title="Implement prepare-handoff",
            summary="design written",
            guardrail="context",
            snapshot=snapshot,
            status_payload=status,
            remote_url="https://github.com/owner/repo.git",
            next_actions=["Run status", "Claim this handoff"],
        )

        self.assertIn("Implement prepare-handoff", message)
        self.assertIn("owner/repo", message)
        self.assertIn("abc123", message)
        self.assertIn("PLAY_ON", message)
        self.assertIn("Run status", message)
        self.assertIn("Do not auto-merge", message)

    def test_prepare_handoff_creates_valid_ready_handoff_and_clean_square(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            runtime = Path(tmp)
            init_committed_repo(repo_tmp)

            result = prepare_handoff(
                root=runtime,
                cwd=Path(repo_tmp),
                handoff_id="context-loop",
                title="Implement prepare-handoff",
                guardrail="context",
                repo="local/test",
                branch="main",
                task_type="execution",
                drivers=["multiple_files"],
                summary="design written",
                ci_status="unknown",
                next_actions=["Run status", "Claim this handoff"],
            )

            ready_path = runtime / "handoffs" / "ready" / "context-loop.json"
            self.assertTrue(ready_path.exists())
            self.assertFalse((runtime / "handoffs" / "claimed" / "context-loop.json").exists())
            self.assertTrue((runtime / "logs" / "clean-square" / "context-loop.json").exists())
            self.assertTrue(result["validation"]["valid"])
            self.assertTrue(result["stop_current_session"])
            self.assertEqual(result["handoff"]["id"], "context-loop")
            self.assertEqual(result["handoff"]["status"], "READY")
            self.assertIsNotNone(result["handoff"]["estimate"])
            self.assertTrue(Path(result["snapshot"]["path"]).exists())

            created = json.loads(ready_path.read_text(encoding="utf-8"))
            self.assertEqual(created["status"], "READY")

    def test_prepare_handoff_cli_creates_ready_handoff_for_next_pickup(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--driver",
                "multiple_files",
                "--summary",
                "design written",
                "--next-action",
                "Run status",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["stop_current_session"])
            self.assertEqual(output["handoff"]["id"], "context-loop")

            next_result, next_output = run_cli(tmp, "next-handoff")
            self.assertEqual(next_result.returncode, 0, next_result.stderr)
            self.assertEqual(next_output["handoff"]["id"], "context-loop")

            validate_result, validation = run_cli(tmp, "validate-handoff", "context-loop")
            self.assertEqual(validate_result.returncode, 0, validate_result.stderr)
            self.assertTrue(validation["valid"])

            claim_result, claimed = run_cli(tmp, "claim-handoff", "context-loop", "--claimer", "test")
            self.assertEqual(claim_result.returncode, 0, claim_result.stderr)
            self.assertEqual(claimed["id"], "context-loop")
            self.assertTrue((Path(tmp) / "handoffs" / "claimed" / "context-loop.json").exists())

    def test_prepare_handoff_cli_requires_explicit_guardrail(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            result, output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "missing guardrail",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertIn("--guardrail", result.stderr)

    def test_prepare_handoff_cli_duplicate_id_fails_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            first_result, _ = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "original summary",
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            original = json.loads(ready_path.read_text(encoding="utf-8"))

            duplicate_result, duplicate_output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "replacement summary",
            )

            self.assertNotEqual(duplicate_result.returncode, 0)
            self.assertIsNone(duplicate_output)
            self.assertEqual(original, json.loads(ready_path.read_text(encoding="utf-8")))

    def test_prepare_handoff_cli_duplicate_id_in_claimed_state_fails_without_new_ready(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)

            first_result, _ = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "original summary",
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            claim_result, _ = run_cli(tmp, "claim-handoff", "context-loop", "--claimer", "test")
            self.assertEqual(claim_result.returncode, 0, claim_result.stderr)

            duplicate_result, duplicate_output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "replacement summary",
            )

            self.assertNotEqual(duplicate_result.returncode, 0)
            self.assertIsNone(duplicate_output)
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "context-loop.json").exists())
            self.assertTrue((Path(tmp) / "handoffs" / "claimed" / "context-loop.json").exists())

    def test_prepare_handoff_cli_brake_neutral_blocks_creation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            brake_result, _ = run_cli(tmp, "set-brake", "NEUTRAL", "--reason", "pause")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "blocked",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "context-loop.json").exists())

    def test_prepare_handoff_cli_brake_park_blocks_creation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            brake_result, _ = run_cli(tmp, "set-brake", "PARK", "--reason", "timeout")
            self.assertEqual(brake_result.returncode, 0, brake_result.stderr)

            result, output = run_cli(
                tmp,
                "prepare-handoff",
                "--id",
                "context-loop",
                "--title",
                "Implement prepare-handoff",
                "--guardrail",
                "context",
                "--repo",
                "local/test",
                "--cwd",
                repo_tmp,
                "--task-type",
                "execution",
                "--summary",
                "blocked",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(output)
            self.assertFalse((Path(tmp) / "handoffs" / "ready" / "context-loop.json").exists())
            self.assertEqual([], list((Path(tmp) / "snapshots").glob("*.json")))
            self.assertFalse((Path(tmp) / "logs" / "clean-square" / "context-loop.json").exists())

    def test_prepare_handoff_rechecks_duplicate_id_after_runtime_lock(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            runtime = Path(tmp)
            init_committed_repo(repo_tmp)
            ready_path = runtime / "handoffs" / "ready" / "context-loop.json"

            class CreateDuplicateOnEnter:
                def __enter__(self):
                    ready_path.parent.mkdir(parents=True, exist_ok=True)
                    ready_path.write_text(json.dumps({"id": "context-loop", "message": "original"}), encoding="utf-8")

                def __exit__(self, exc_type, exc, traceback):
                    return False

            with mock.patch("codex_cadence.handoff_loop.exclusive_lock", return_value=CreateDuplicateOnEnter()):
                with self.assertRaises(FileExistsError):
                    prepare_handoff(
                        root=runtime,
                        cwd=Path(repo_tmp),
                        handoff_id="context-loop",
                        title="Implement prepare-handoff",
                        guardrail="context",
                        repo="local/test",
                        branch="main",
                        task_type="execution",
                        drivers=["multiple_files"],
                        summary="design written",
                        ci_status="unknown",
                        next_actions=[],
                    )

            self.assertEqual({"id": "context-loop", "message": "original"}, json.loads(ready_path.read_text(encoding="utf-8")))

    def test_prepare_handoff_does_not_publish_ready_when_clean_square_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            runtime = Path(tmp)
            init_committed_repo(repo_tmp)

            with mock.patch("codex_cadence.handoff_loop._create_clean_square_record", side_effect=RuntimeError("write failed")):
                with self.assertRaises(RuntimeError):
                    prepare_handoff(
                        root=runtime,
                        cwd=Path(repo_tmp),
                        handoff_id="context-loop",
                        title="Implement prepare-handoff",
                        guardrail="context",
                        repo="local/test",
                        branch="main",
                        task_type="execution",
                        drivers=["multiple_files"],
                        summary="design written",
                        ci_status="unknown",
                        next_actions=[],
                    )

            self.assertFalse((runtime / "handoffs" / "ready" / "context-loop.json").exists())

    def test_prepare_handoff_does_not_overwrite_ready_created_before_final_publish(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            runtime = Path(tmp)
            init_committed_repo(repo_tmp)
            ready_path = runtime / "handoffs" / "ready" / "context-loop.json"

            def create_concurrent_ready(_root, _handoff, _summary):
                ready_path.parent.mkdir(parents=True, exist_ok=True)
                ready_path.write_text(json.dumps({"id": "context-loop", "message": "concurrent"}), encoding="utf-8")
                return {"handoff_id": "context-loop", "path": str(runtime / "logs" / "clean-square" / "context-loop.json")}

            with mock.patch("codex_cadence.handoff_loop._create_clean_square_record", side_effect=create_concurrent_ready):
                with self.assertRaises(FileExistsError):
                    prepare_handoff(
                        root=runtime,
                        cwd=Path(repo_tmp),
                        handoff_id="context-loop",
                        title="Implement prepare-handoff",
                        guardrail="context",
                        repo="local/test",
                        branch="main",
                        task_type="execution",
                        drivers=["multiple_files"],
                        summary="design written",
                        ci_status="unknown",
                        next_actions=[],
                    )

            self.assertEqual({"id": "context-loop", "message": "concurrent"}, json.loads(ready_path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
