import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_cadence.repo_state import snapshot_repo


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


if __name__ == "__main__":
    unittest.main()
