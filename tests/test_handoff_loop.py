import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
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


def current_head(path):
    return git(path, "rev-parse", "HEAD").stdout.strip()


def checksum_json(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def init_committed_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (Path(path) / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def _rewrite_claimed_handoff(root, update):
    path = Path(root) / "handoffs" / "claimed" / "context-loop.json"
    handoff = json.loads(path.read_text(encoding="utf-8"))
    update(handoff)
    path.write_text(json.dumps(handoff), encoding="utf-8")


def _write_active_epoch_json(root, data):
    active_dir = Path(root) / "epochs" / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "epoch-malformed.json").write_text(json.dumps(data), encoding="utf-8")


class HandoffLoopTests(unittest.TestCase):
    def prepare_resume_handoff(self, root, repo_tmp, *, handoff_id="context-loop", drivers=None):
        args = [
            "prepare-handoff",
            "--id",
            handoff_id,
            "--title",
            "Implement resume verifier",
            "--guardrail",
            "context",
            "--repo",
            "local/test",
            "--cwd",
            repo_tmp,
            "--task-type",
            "execution",
            "--summary",
            "ready for pickup",
        ]
        for driver in drivers or ["multiple_files"]:
            args.extend(["--driver", driver])
        result, packet = run_cli(root, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return packet

    def claim_resume_handoff(self, root, handoff_id="context-loop", *, claimer="test-agent"):
        result, packet = run_cli(root, "claim-handoff", handoff_id, "--claimer", claimer)
        self.assertEqual(result.returncode, 0, result.stderr)
        return packet

    def write_resume_verification_packet(self, root, repo_tmp, *, handoff_id="context-loop", claimer="test-agent"):
        result, packet = run_cli(
            root,
            "verify-resume",
            handoff_id,
            "--cwd",
            repo_tmp,
            "--claimer",
            claimer,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(root) / "resume-verification.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return path, packet

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

    def test_verify_resume_allows_claimed_handoff_with_matching_repo_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            clean_square_path = Path(tmp) / "logs" / "clean-square" / "context-loop.json"
            before = {
                "claimed": claimed_path.read_text(encoding="utf-8"),
                "clean_square": clean_square_path.read_text(encoding="utf-8"),
            }

            result, packet = run_cli(
                tmp,
                "verify-resume",
                "context-loop",
                "--cwd",
                repo_tmp,
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "resume_work")
            self.assertEqual(packet["blockers"], [])
            self.assertEqual(packet["handoff"]["state"], "claimed")
            self.assertTrue(packet["clean_square"]["valid"])
            self.assertEqual(packet["repository"]["current_branch"], "main")
            self.assertFalse(packet["repository"]["dirty_worktree"])
            self.assertEqual(packet["cadence"]["brake_status"], "DRIVE")
            self.assertEqual(packet["policy_evidence"]["status"], "verified")
            self.assertEqual(before["claimed"], claimed_path.read_text(encoding="utf-8"))
            self.assertEqual(before["clean_square"], clean_square_path.read_text(encoding="utf-8"))

    def test_resume_continuation_allows_fresh_matching_resume_packet_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            resume_path, resume_packet = self.write_resume_verification_packet(tmp, repo_tmp)
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            clean_square_path = Path(tmp) / "logs" / "clean-square" / "context-loop.json"
            before = {
                "claimed": claimed_path.read_text(encoding="utf-8"),
                "clean_square": clean_square_path.read_text(encoding="utf-8"),
                "active_epochs": sorted(path.name for path in (Path(tmp) / "epochs" / "active").glob("*.json")),
            }

            result, packet = run_cli(
                tmp,
                "resume-continuation",
                "--resume-verification-file",
                str(resume_path),
                "--cwd",
                repo_tmp,
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(packet["schema_version"], "resume-continuation.v1")
            self.assertEqual(packet["packet"], "resume_continuation")
            self.assertTrue(packet["read_only"])
            self.assertTrue(packet["valid"])
            self.assertTrue(packet["continuable"])
            self.assertEqual(packet["handoff_id"], "context-loop")
            self.assertEqual(packet["claimer"], "test-agent")
            self.assertEqual(packet["blockers"], [])
            self.assertEqual(packet["recommended_next_action"], "start_governed_execution")
            self.assertEqual(packet["resume_verification"]["schema_version"], "resume-verification.v1")
            self.assertEqual(packet["resume_verification"]["checksum"], packet["fresh_resume_verification"]["checksum"])
            self.assertEqual(packet["resume_verification"]["checksum"], checksum_json(resume_packet))
            self.assertFalse(packet["executor_started"])
            self.assertEqual(packet["side_effects"], [])
            self.assertEqual(before["claimed"], claimed_path.read_text(encoding="utf-8"))
            self.assertEqual(before["clean_square"], clean_square_path.read_text(encoding="utf-8"))
            self.assertEqual(before["active_epochs"], sorted(path.name for path in (Path(tmp) / "epochs" / "active").glob("*.json")))

    def test_resume_continuation_blocks_stale_resume_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            resume_path, _resume_packet = self.write_resume_verification_packet(tmp, repo_tmp)
            old = time.time() - 3600
            os.utime(resume_path, (old, old))

            result, packet = run_cli(
                tmp,
                "resume-continuation",
                "--resume-verification-file",
                str(resume_path),
                "--cwd",
                repo_tmp,
                "--claimer",
                "test-agent",
                "--max-resume-age-minutes",
                "5",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertFalse(packet["continuable"])
            self.assertEqual(packet["recommended_next_action"], "inspect_resume_blockers")
            self.assertIn("resume_verification_stale", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])

    def test_resume_continuation_blocks_repo_drift_after_saved_resume_packet(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            resume_path, _resume_packet = self.write_resume_verification_packet(tmp, repo_tmp)
            (Path(repo_tmp) / "README.md").write_text("advanced after resume verification\n", encoding="utf-8")
            git(repo_tmp, "add", "README.md")
            git(repo_tmp, "commit", "-m", "advance after resume verification")

            result, packet = run_cli(
                tmp,
                "resume-continuation",
                "--resume-verification-file",
                str(resume_path),
                "--cwd",
                repo_tmp,
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertFalse(packet["continuable"])
            self.assertEqual(packet["recommended_next_action"], "recreate_handoff")
            codes = {blocker["code"] for blocker in packet["blockers"]}
            self.assertIn("repo_head_mismatch", codes)
            self.assertIn("resume_verification_anchor_mismatch", codes)

    def test_resume_continuation_blocks_existing_active_epoch_before_start(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            resume_path, _resume_packet = self.write_resume_verification_packet(tmp, repo_tmp)
            active_dir = Path(tmp) / "epochs" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            active_epoch = {
                "id": "epoch-existing",
                "status": "ACTIVE",
                "repo": "local/test",
                "branch": "main",
                "snapshot_before": {"id": "snapshot-before", "head": current_head(repo_tmp)},
            }
            (active_dir / "epoch-existing.json").write_text(json.dumps(active_epoch), encoding="utf-8")

            result, packet = run_cli(
                tmp,
                "resume-continuation",
                "--resume-verification-file",
                str(resume_path),
                "--cwd",
                repo_tmp,
                "--claimer",
                "test-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertFalse(packet["continuable"])
            self.assertEqual(packet["recommended_next_action"], "close_or_fail_active_epoch")
            codes = {blocker["code"] for blocker in packet["blockers"]}
            self.assertIn("active_epoch_exists", codes)
            self.assertIn("resume_verification_anchor_mismatch", codes)

    def test_resume_continuation_blocks_different_claimer_without_claiming_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            resume_path, _resume_packet = self.write_resume_verification_packet(tmp, repo_tmp)
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            before = claimed_path.read_text(encoding="utf-8")

            result, packet = run_cli(
                tmp,
                "resume-continuation",
                "--resume-verification-file",
                str(resume_path),
                "--cwd",
                repo_tmp,
                "--claimer",
                "other-agent",
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["recommended_next_action"], "inspect_resume_blockers")
            codes = {blocker["code"] for blocker in packet["blockers"]}
            self.assertIn("resume_claimer_mismatch", codes)
            self.assertIn("handoff_claimed_by_other", codes)
            self.assertEqual(before, claimed_path.read_text(encoding="utf-8"))

    def test_resume_continuation_preserves_next_action_for_non_resumable_saved_packet(self):
        cases = [
            (
                ["multiple_files"],
                "claim_handoff",
            ),
            (
                ["cross_subsystem", "migration"],
                "approve_handoff",
            ),
        ]
        for drivers, expected_action in cases:
            with self.subTest(expected_action=expected_action):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
                    init_committed_repo(repo_tmp)
                    self.prepare_resume_handoff(tmp, repo_tmp, drivers=drivers)
                    verify_result, resume_packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp)
                    self.assertEqual(verify_result.returncode, 2, verify_result.stderr)
                    self.assertFalse(resume_packet["resumable"])
                    self.assertEqual(resume_packet["recommended_next_action"], expected_action)
                    resume_path = Path(tmp) / "resume-verification.json"
                    resume_path.write_text(json.dumps(resume_packet), encoding="utf-8")

                    result, packet = run_cli(
                        tmp,
                        "resume-continuation",
                        "--resume-verification-file",
                        str(resume_path),
                        "--cwd",
                        repo_tmp,
                    )

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(packet["valid"])
                    self.assertFalse(packet["continuable"])
                    self.assertEqual(packet["recommended_next_action"], expected_action)
                    self.assertIn("resume_verification_not_resumable", {blocker["code"] for blocker in packet["blockers"]})
                    self.assertFalse((Path(tmp) / "handoffs" / "claimed" / "context-loop.json").exists())

    def test_verify_resume_rejects_tampered_resume_snapshot_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            (Path(repo_tmp) / "README.md").write_text("advanced head\n", encoding="utf-8")
            git(repo_tmp, "add", "README.md")
            git(repo_tmp, "commit", "-m", "advance head")
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            handoff = json.loads(claimed_path.read_text(encoding="utf-8"))
            handoff["metadata"]["resume_snapshot"]["head"] = current_head(repo_tmp)
            claimed_path.write_text(json.dumps(handoff), encoding="utf-8")

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "recreate_handoff")
            self.assertIn("resume_snapshot_invalid", {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_blocks_stale_head_wrong_branch_and_dirty_worktree(self):
        cases = [
            ("repo_head_mismatch", "recreate_handoff", lambda repo: ((Path(repo) / "README.md").write_text("new head\n", encoding="utf-8"), git(repo, "add", "README.md"), git(repo, "commit", "-m", "new head"))),
            ("repo_branch_mismatch", "recreate_handoff", lambda repo: git(repo, "switch", "-c", "other")),
            ("dirty_worktree", "clean_worktree", lambda repo: (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")),
        ]
        for expected_code, expected_action, mutate_repo in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
                    init_committed_repo(repo_tmp)
                    self.prepare_resume_handoff(tmp, repo_tmp)
                    self.claim_resume_handoff(tmp)
                    mutate_repo(repo_tmp)

                    result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(packet["resumable"])
                    self.assertEqual(packet["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_blocks_missing_clean_square_active_stop_and_epoch_conflict(self):
        def write_epoch_conflict(root):
            active_dir = Path(root) / "epochs" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            for index in (1, 2):
                (active_dir / f"epoch-{index}.json").write_text(
                    json.dumps({"id": f"epoch-{index}", "status": "ACTIVE"}),
                    encoding="utf-8",
                )

        def write_malformed_epoch(root):
            active_dir = Path(root) / "epochs" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            (active_dir / "epoch-bad.json").write_text(
                json.dumps({"id": "epoch-bad", "status": "ACTIVE"}),
                encoding="utf-8",
            )

        def write_epoch_without_head(root):
            active_dir = Path(root) / "epochs" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            (active_dir / "epoch-no-head.json").write_text(
                json.dumps(
                    {
                        "id": "epoch-no-head",
                        "status": "ACTIVE",
                        "repo": "local/test",
                        "branch": "main",
                        "snapshot_before": {"id": "snapshot-before"},
                    }
                ),
                encoding="utf-8",
            )

        def write_epoch_with_mismatch(root, **overrides):
            active_dir = Path(root) / "epochs" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "id": "epoch-mismatch",
                "status": "ACTIVE",
                "repo": "local/test",
                "branch": "main",
                "snapshot_before": {"id": "snapshot-before", "head": "not-the-handoff-head"},
            }
            data.update(overrides)
            (active_dir / "epoch-mismatch.json").write_text(json.dumps(data), encoding="utf-8")

        cases = [
            (
                "clean_square_missing",
                "recreate_handoff",
                lambda root: (Path(root) / "logs" / "clean-square" / "context-loop.json").unlink(),
            ),
            (
                "active_brake_stop",
                "clear_brake",
                lambda root: run_cli(root, "set-brake", "PARK", "--reason", "operator stop"),
            ),
            (
                "active_epoch_conflict",
                "close_or_fail_active_epoch",
                write_epoch_conflict,
            ),
            (
                "active_epoch_invalid",
                "close_or_fail_active_epoch",
                write_malformed_epoch,
            ),
            (
                "active_epoch_invalid",
                "close_or_fail_active_epoch",
                write_epoch_without_head,
            ),
            (
                "active_epoch_repo_mismatch",
                "close_or_fail_active_epoch",
                lambda root: write_epoch_with_mismatch(root, repo="other/repo"),
            ),
            (
                "active_epoch_branch_mismatch",
                "close_or_fail_active_epoch",
                lambda root: write_epoch_with_mismatch(root, branch="other"),
            ),
            (
                "active_epoch_head_mismatch",
                "close_or_fail_active_epoch",
                lambda root: write_epoch_with_mismatch(root),
            ),
        ]
        for expected_code, expected_action, mutate_runtime in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
                    init_committed_repo(repo_tmp)
                    self.prepare_resume_handoff(tmp, repo_tmp)
                    self.claim_resume_handoff(tmp)
                    mutate_runtime(tmp)

                    result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(packet["resumable"])
                    self.assertEqual(packet["recommended_next_action"], expected_action)
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_blocks_missing_approval_and_duplicate_handoff_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp, drivers=["cross_subsystem", "migration"])
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            handoff = json.loads(ready_path.read_text(encoding="utf-8"))
            handoff["status"] = "CLAIMED"
            handoff["claimed_by"] = "test-agent"
            claimed_path.parent.mkdir(parents=True, exist_ok=True)
            claimed_path.write_text(json.dumps(handoff), encoding="utf-8")
            ready_path.unlink()

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "approve_handoff")
            self.assertIn("policy_approval_missing", {blocker["code"] for blocker in packet["blockers"]})

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            self.claim_resume_handoff(tmp)
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            ready_path.parent.mkdir(parents=True, exist_ok=True)
            ready_path.write_text(claimed_path.read_text(encoding="utf-8"), encoding="utf-8")

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "resolve_claim_conflict")
            self.assertIn("handoff_state_conflict", {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_blocks_self_evolution_execution_policy_even_if_manually_claimed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp, drivers=["self_evolution"])
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            handoff = json.loads(ready_path.read_text(encoding="utf-8"))
            handoff["status"] = "CLAIMED"
            handoff["claimed_by"] = "test-agent"
            claimed_path.parent.mkdir(parents=True, exist_ok=True)
            claimed_path.write_text(json.dumps(handoff), encoding="utf-8")
            ready_path.unlink()

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "recreate_handoff")
            self.assertIn("policy_self_evolution_propose_only", {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_blocks_claimed_file_without_claimed_status_and_claimer(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            claimed_path = Path(tmp) / "handoffs" / "claimed" / "context-loop.json"
            handoff = json.loads(ready_path.read_text(encoding="utf-8"))
            claimed_path.parent.mkdir(parents=True, exist_ok=True)
            claimed_path.write_text(json.dumps(handoff), encoding="utf-8")
            ready_path.unlink()

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertIn("handoff_not_claimed", {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_returns_stable_blockers_for_malformed_readable_records(self):
        cases = [
            (
                "handoff_signature_invalid",
                lambda root: _rewrite_claimed_handoff(root, lambda handoff: handoff.update({"message": ["not", "a", "string"]})),
            ),
            (
                "clean_square_invalid",
                lambda root: (Path(root) / "logs" / "clean-square" / "context-loop.json").write_text(
                    json.dumps({"handoff_id": "context-loop", "checks": []}),
                    encoding="utf-8",
                ),
            ),
            (
                "policy_evidence_invalid",
                lambda root: (Path(root) / "approvals" / "context-loop.json").write_text(
                    json.dumps([]),
                    encoding="utf-8",
                ),
            ),
            (
                "active_epoch_invalid",
                lambda root: _write_active_epoch_json(root, []),
            ),
        ]
        for expected_code, mutate_runtime in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
                    init_committed_repo(repo_tmp)
                    self.prepare_resume_handoff(tmp, repo_tmp)
                    self.claim_resume_handoff(tmp)
                    mutate_runtime(tmp)

                    result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp, "--claimer", "test-agent")

                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(packet["resumable"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_recommends_approval_before_claim_for_ready_approval_gated_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp, drivers=["cross_subsystem", "migration"])

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "approve_handoff")
            self.assertIn("handoff_not_claimed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertIn("policy_approval_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_verify_resume_recommends_claim_without_mutating_ready_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo_tmp:
            init_committed_repo(repo_tmp)
            self.prepare_resume_handoff(tmp, repo_tmp)
            ready_path = Path(tmp) / "handoffs" / "ready" / "context-loop.json"
            before = ready_path.read_text(encoding="utf-8")

            result, packet = run_cli(tmp, "verify-resume", "context-loop", "--cwd", repo_tmp)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(packet["resumable"])
            self.assertEqual(packet["recommended_next_action"], "claim_handoff")
            self.assertIn("handoff_not_claimed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(before, ready_path.read_text(encoding="utf-8"))
            self.assertFalse((Path(tmp) / "handoffs" / "claimed" / "context-loop.json").exists())

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
