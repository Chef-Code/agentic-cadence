import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_cadence.repo_state import local_repo_readiness_evidence, snapshot_repo, validate_repo_snapshot


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def init_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")


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
        "readiness_evidence": local_repo_readiness_evidence(),
        "captured_at": "2999-05-22T00:00:00Z",
    }
    snapshot.update(overrides)
    return snapshot


class RepoStateTests(unittest.TestCase):
    def test_clean_committed_repo_snapshots_high_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_repo(repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "initial")

            snapshot = snapshot_repo(repo, repo="local/test")

            self.assertEqual(snapshot["repo"], "local/test")
            self.assertEqual(snapshot["branch"], "main")
            self.assertFalse(snapshot["dirty_worktree"])
            self.assertEqual(snapshot["repo_confidence"], "high")
            self.assertEqual(snapshot["repo_confidence_drivers"], [])
            self.assertIsInstance(snapshot["head"], str)
            self.assertEqual(snapshot["readiness_evidence"]["source"], "local_git")
            self.assertEqual(snapshot["readiness_evidence"]["freshness"], "local_only")
            self.assertFalse(snapshot["readiness_evidence"]["live"])
            self.assertFalse(snapshot["readiness_evidence"]["stale"])
            self.assertIn("open_prs_not_fetched", snapshot["readiness_evidence"]["limitations"])

    def test_dirty_unborn_repo_snapshots_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_repo(repo)
            (repo / "README.md").write_text("uncommitted\n", encoding="utf-8")

            snapshot = snapshot_repo(repo, repo="local/test")

            self.assertEqual(snapshot["repo"], "local/test")
            self.assertEqual(snapshot["branch"], "main")
            self.assertIsNone(snapshot["head"])
            self.assertTrue(snapshot["dirty_worktree"])
            self.assertEqual(snapshot["repo_confidence"], "low")
            self.assertIn("dirty_worktree", snapshot["repo_confidence_drivers"])
            self.assertIn("unborn_head", snapshot["repo_confidence_drivers"])

    def test_clean_detached_head_snapshots_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_repo(repo)
            (repo / "README.md").write_text("first\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "first")
            first_head = git(repo, "rev-parse", "HEAD").stdout.strip()
            (repo / "README.md").write_text("second\n", encoding="utf-8")
            git(repo, "commit", "-am", "second")
            git(repo, "checkout", "--detach", first_head)

            snapshot = snapshot_repo(repo, repo="local/test")

            self.assertEqual(snapshot["head"], first_head)
            self.assertIsNone(snapshot["branch"])
            self.assertFalse(snapshot["dirty_worktree"])
            self.assertEqual(snapshot["repo_confidence"], "low")
            self.assertIn("detached_head", snapshot["repo_confidence_drivers"])

    def test_known_failures_lower_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_repo(repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "initial")

            snapshot = snapshot_repo(repo, repo="local/test", known_failures=["ci"])

            self.assertEqual(snapshot["known_failures"], ["ci"])
            self.assertEqual(snapshot["repo_confidence"], "low")
            self.assertIn("known_failures", snapshot["repo_confidence_drivers"])

    def test_snapshot_validation_requires_readiness_evidence(self):
        snapshot = valid_snapshot()
        snapshot.pop("readiness_evidence")

        valid, reason = validate_repo_snapshot(snapshot, expected_repo="local/test", expected_branch="main")

        self.assertFalse(valid)
        self.assertEqual(reason, "snapshot readiness_evidence is required")

    def test_snapshot_validation_rejects_non_local_readiness_evidence(self):
        snapshot = valid_snapshot(
            readiness_evidence={**local_repo_readiness_evidence(), "freshness": "saved_input"}
        )

        valid, reason = validate_repo_snapshot(snapshot, expected_repo="local/test", expected_branch="main")

        self.assertFalse(valid)
        self.assertEqual(reason, "snapshot readiness_evidence freshness must be local_only")


if __name__ == "__main__":
    unittest.main()
