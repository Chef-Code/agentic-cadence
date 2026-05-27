import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from codex_cadence.release import evaluate_release_dry_run


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def run(command, cwd):
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def write_release_files(repo: Path, version: str = "0.2.0") -> None:
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "agentic-cadence"',
                f'version = "{version}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "CHANGELOG.md").write_text(
        f"""# Changelog

## {version} - 2026-05-27

Release automation dry-run helper.

### Added

- `release-dry-run` checks package metadata, changelog notes, and tag target status.

### Release Notes

- This release is intended for local clone-based use with `pip install .`.
- PyPI publication is not part of the `{version}` baseline.

## 0.1.1 - 2026-05-26

Previous release.
""",
        encoding="utf-8",
    )


def init_release_repo(repo: Path, version: str = "0.2.0") -> str:
    write_release_files(repo, version=version)
    init = run(["git", "init", "-b", "main"], repo)
    if init.returncode != 0:
        fallback = run(["git", "init"], repo)
        if fallback.returncode != 0:
            raise AssertionError(fallback.stderr or fallback.stdout)
        checkout = run(["git", "checkout", "-b", "main"], repo)
        if checkout.returncode != 0:
            raise AssertionError(checkout.stderr or checkout.stdout)
    for command in (
        ["git", "config", "user.name", "Release Test"],
        ["git", "config", "user.email", "release@example.test"],
        ["git", "add", "pyproject.toml", "CHANGELOG.md"],
        ["git", "commit", "-m", "Release files"],
    ):
        result = run(command, repo)
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
    head = run(["git", "rev-parse", "HEAD"], repo)
    if head.returncode != 0:
        raise AssertionError(head.stderr or head.stdout)
    return head.stdout.strip()


class ReleaseDryRunTests(unittest.TestCase):
    def test_release_dry_run_generates_notes_and_requires_operator_for_missing_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            head = init_release_repo(repo)

            packet = evaluate_release_dry_run(repo, version="0.2.0")

        self.assertTrue(packet["ready_to_release"])
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["recommended_next_action"], "create_tag_after_operator_confirmation")
        self.assertTrue(packet["operator_confirmation_required"])
        self.assertTrue(packet["dry_run"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(packet["release"]["version"], "0.2.0")
        self.assertEqual(packet["release"]["tag"], "v0.2.0")
        self.assertEqual(packet["git"]["target_sha"], head)
        self.assertFalse(packet["git"]["tag"]["exists"])
        self.assertFalse(packet["package_publication"]["allowed"])
        self.assertEqual(packet["package_publication"]["recommended_next_action"], "do_not_publish_package")
        self.assertIn("## 0.2.0 - 2026-05-27", packet["release_notes"])
        self.assertIn("Release automation dry-run helper.", packet["release_notes"])
        self.assertIn("PyPI publication is not part of the `0.2.0` baseline.", packet["release_notes"])

    def test_existing_tag_must_match_target_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old_head = init_release_repo(repo)
            tag = run(["git", "tag", "v0.2.0", old_head], repo)
            self.assertEqual(tag.returncode, 0, tag.stderr or tag.stdout)
            (repo / "README.md").write_text("# Release repo\n", encoding="utf-8")
            for command in (
                ["git", "add", "README.md"],
                ["git", "commit", "-m", "Move release target"],
            ):
                result = run(command, repo)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            packet = evaluate_release_dry_run(repo, version="0.2.0")

        self.assertFalse(packet["ready_to_release"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "address_blockers")
        self.assertEqual(packet["git"]["tag"]["target_sha"], old_head)
        self.assertIn("tag_points_to_different_commit", {blocker["code"] for blocker in packet["blockers"]})

    def test_target_ref_must_match_checked_out_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old_head = init_release_repo(repo)
            (repo / "README.md").write_text("# Release repo\n", encoding="utf-8")
            for command in (
                ["git", "add", "README.md"],
                ["git", "commit", "-m", "Move checked-out head"],
            ):
                result = run(command, repo)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            packet = evaluate_release_dry_run(repo, version="0.2.0", target_ref=old_head)

        self.assertFalse(packet["ready_to_release"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["git"]["target_sha"], old_head)
        self.assertIn("target_ref_not_checked_out", {blocker["code"] for blocker in packet["blockers"]})

    def test_explicit_empty_tag_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_release_repo(repo)

            packet = evaluate_release_dry_run(repo, version="0.2.0", tag="  ")

        self.assertFalse(packet["ready_to_release"])
        self.assertEqual(packet["release"]["tag"], "")
        self.assertIn("release_tag_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_requested_version_must_match_package_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_release_repo(repo, version="0.2.0")

            packet = evaluate_release_dry_run(repo, version="0.3.0")

        self.assertFalse(packet["ready_to_release"])
        self.assertIn("version_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_file_cwd_returns_blocked_packet_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd_file = Path(tmp) / "not-a-directory"
            cwd_file.write_text("not a repo\n", encoding="utf-8")

            packet = evaluate_release_dry_run(cwd_file, version="0.2.0")

        self.assertFalse(packet["ready_to_release"])
        self.assertIn("release_cwd_not_directory", {blocker["code"] for blocker in packet["blockers"]})

    def test_git_oserror_returns_blocked_packet_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_release_repo(repo)

            with mock.patch("codex_cadence.release.subprocess.run", side_effect=OSError("git unavailable")):
                packet = evaluate_release_dry_run(repo, version="0.2.0")

        self.assertFalse(packet["ready_to_release"])
        self.assertIn("git_repo_missing", {blocker["code"] for blocker in packet["blockers"]})
        self.assertIn("git unavailable", packet["blockers"][-1]["message"])

    def test_cli_release_dry_run_reads_local_files_without_creating_tag_or_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            repo = tmp_root / "repo"
            repo.mkdir()
            init_release_repo(repo)
            fake_bin = tmp_root / "bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / ("gh.cmd" if os.name == "nt" else "gh")
            fake_twine = fake_bin / ("twine.cmd" if os.name == "nt" else "twine")
            if os.name == "nt":
                fake_gh.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
                fake_twine.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
            else:
                fake_gh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
                fake_twine.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
                fake_gh.chmod(0o755)
                fake_twine.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "release-dry-run",
                    "--cwd",
                    str(repo),
                    "--version",
                    "0.2.0",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            tags = run(["git", "tag", "--list"], repo)
            status = run(["git", "status", "--short"], repo)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        packet = json.loads(result.stdout)
        self.assertTrue(packet["ready_to_release"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual(tags.stdout.strip(), "")
        self.assertEqual(status.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
