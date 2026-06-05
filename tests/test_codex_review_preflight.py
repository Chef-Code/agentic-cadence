import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "codex_review_preflight.py"


def load_preflight():
    assert PREFLIGHT.exists(), "missing Codex review preflight script"
    spec = importlib.util.spec_from_file_location("codex_review_preflight", PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodexReviewPreflightTests(unittest.TestCase):
    def setUp(self):
        self.preflight = load_preflight()

    def test_skips_code_changes_until_review_elected(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=[],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "not_elected")

    def test_elect_label_runs_for_code_changes(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_alias_labels_are_normalized_for_election_and_force(self):
        self.assertEqual(
            self.preflight.normalize_labels([" Elect-Codex-Review ", " FORCE-CODEX-REVIEW "]),
            {"elect-codex-review", "force-codex-review"},
        )

        elected = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=[" Elect-Codex-Review "],
        )
        forced = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["docs/protocol.md"],
            comments=[],
            comments_available=True,
            pr_title="Docs update",
            pr_body="",
            labels=[" FORCE-CODEX-REVIEW "],
        )

        self.assertTrue(elected["should_run"])
        self.assertEqual(elected["reason"], "operator_elected")
        self.assertTrue(forced["should_run"])
        self.assertEqual(forced["reason"], "force_requested")

    def test_skips_duplicate_review_for_same_head_and_dedupe_key(self):
        changed_files = ["codex_cadence/store.py"]
        dedupe_key = self.preflight.compute_dedupe_key("abc123", changed_files)
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=changed_files,
            comments=[
                {
                    "body": (
                        "## Codex Review\n\n"
                        f"<!-- codex-review:v1 head=abc123 dedupe={dedupe_key} -->\n\n"
                        "No material findings."
                    ),
                    "user": {"login": "github-actions[bot]"},
                }
            ],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "already_reviewed")
        self.assertEqual(decision["dedupe_key"], dedupe_key)

    def test_wrong_marker_does_not_skip_current_head(self):
        changed_files = ["codex_cadence/store.py"]
        dedupe_key = self.preflight.compute_dedupe_key("abc123", changed_files)
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=changed_files,
            comments=[
                {
                    "body": (
                        "## Codex Review\n\n"
                        f"<!-- codex-review:v1 head=older-head dedupe={dedupe_key} -->"
                    ),
                    "user": {"login": "github-actions[bot]"},
                }
            ],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_skips_docs_only_changes_without_force(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["docs/protocol.md", "README.md"],
            comments=[],
            comments_available=True,
            pr_title="Docs update",
            pr_body="",
            labels=[],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "docs_only")

    def test_repo_operational_markdown_is_not_docs_only(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["SKILL.md"],
            comments=[],
            comments_available=True,
            pr_title="Update skill",
            pr_body="",
            labels=[],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "not_elected")

    def test_root_requirements_txt_is_not_docs_only(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["requirements.txt"],
            comments=[],
            comments_available=True,
            pr_title="Update deps",
            pr_body="",
            labels=[],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "not_elected")

    def test_force_marker_overrides_docs_only_and_duplicate_marker(self):
        changed_files = ["docs/protocol.md"]
        dedupe_key = self.preflight.compute_dedupe_key("abc123", changed_files)
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=changed_files,
            comments=[
                {
                    "body": (
                        "## Codex Review\n\n"
                        f"<!-- codex-review:v1 head=abc123 dedupe={dedupe_key} -->"
                    ),
                    "user": {"login": "github-actions[bot]"},
                }
            ],
            comments_available=True,
            pr_title="Docs update",
            pr_body="",
            labels=["codex-review-force"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "force_requested")

    def test_elect_label_runs_docs_only_when_requested(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["docs/protocol.md", "README.md"],
            comments=[],
            comments_available=True,
            pr_title="Docs update",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_skip_marker_wins_for_code_changes(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-skip"],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_skip")

    def test_pr_text_skip_marker_does_not_count_as_operator_skip(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=True,
            pr_title="Update store [skip codex]",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_untrusted_review_marker_does_not_skip_current_head(self):
        changed_files = ["codex_cadence/store.py"]
        dedupe_key = self.preflight.compute_dedupe_key("abc123", changed_files)
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=changed_files,
            comments=[
                {
                    "body": f"<!-- codex-review:v1 head=abc123 dedupe={dedupe_key} -->",
                    "user": {"login": "octocat"},
                }
            ],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_noncanonical_bot_marker_does_not_skip_current_head(self):
        changed_files = ["codex_cadence/store.py"]
        dedupe_key = self.preflight.compute_dedupe_key("abc123", changed_files)
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=changed_files,
            comments=[
                {
                    "body": (
                        "## Codex Review\n\n"
                        "<!-- codex-review:v1 head=older-head dedupe=older-dedupe -->\n\n"
                        "Model-controlled text follows.\n"
                        f"<!-- codex-review:v1 head=abc123 dedupe={dedupe_key} -->"
                    ),
                    "user": {"login": "github-actions[bot]"},
                }
            ],
            comments_available=True,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertTrue(decision["should_run"])
        self.assertEqual(decision["reason"], "operator_elected")

    def test_missing_comment_access_does_not_fail_when_review_is_not_elected(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=False,
            pr_title="Update store",
            pr_body="",
            labels=[],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "not_elected")
        self.assertFalse(decision["fail_check"])

    def test_missing_comment_access_fails_closed_before_elected_review_spend(self):
        decision = self.preflight.decide_preflight(
            head_sha="abc123",
            changed_files=["codex_cadence/store.py"],
            comments=[],
            comments_available=False,
            pr_title="Update store",
            pr_body="",
            labels=["codex-review-elect"],
        )

        self.assertFalse(decision["should_run"])
        self.assertEqual(decision["reason"], "comments_unavailable")
        self.assertTrue(decision["fail_check"])

    def test_github_output_serializes_booleans_and_metadata(self):
        decision = {
            "should_run": False,
            "reason": "already_reviewed",
            "dedupe_key": "dedupe123",
            "changed_files_count": 2,
            "fail_check": False,
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "github_output.txt"
            self.preflight.write_github_outputs(decision, output_path)
            output = output_path.read_text(encoding="utf-8")

        self.assertIn("should_run=false", output)
        self.assertIn("reason=already_reviewed", output)
        self.assertIn("dedupe_key=dedupe123", output)
        self.assertIn("changed_files_count=2", output)
        self.assertIn("fail_check=false", output)

    def test_cli_json_mode_uses_comments_file_without_github_api(self):
        changed_files = ["codex_cadence/store.py"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            changed_files_path = tmp_path / "changed-files.txt"
            comments_path = tmp_path / "comments.json"
            changed_files_path.write_text("\n".join(changed_files), encoding="utf-8")
            comments_path.write_text(json.dumps([]), encoding="utf-8")

            stdout = StringIO()
            with redirect_stdout(stdout):
                decision = self.preflight.main(
                    [
                        "--head-sha",
                        "abc123",
                        "--changed-files-file",
                        str(changed_files_path),
                        "--comments-file",
                        str(comments_path),
                        "--labels-json",
                        json.dumps(["codex-review-elect"]),
                        "--github-output",
                        str(tmp_path / "github-output.txt"),
                        "--json",
                ]
            )

        self.assertEqual(decision, 0)
        self.assertIn('"should_run": true', stdout.getvalue())

    def test_cli_invalid_comments_file_writes_fail_closed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            changed_files_path = tmp_path / "changed-files.txt"
            comments_path = tmp_path / "comments.json"
            output_path = tmp_path / "github-output.txt"
            changed_files_path.write_text("codex_cadence/store.py\n", encoding="utf-8")
            comments_path.write_text("{not-json", encoding="utf-8")

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = self.preflight.main(
                    [
                        "--head-sha",
                        "abc123",
                        "--changed-files-file",
                        str(changed_files_path),
                        "--comments-file",
                        str(comments_path),
                        "--labels-json",
                        json.dumps(["codex-review-elect"]),
                        "--github-output",
                        str(output_path),
                        "--json",
                    ]
                )

            output = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertIn("reason=comments_unavailable", output)
        self.assertIn("fail_check=true", output)
        self.assertIn('"reason": "comments_unavailable"', stdout.getvalue())

    def test_cli_missing_changed_files_file_writes_fail_closed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_path = tmp_path / "github-output.txt"
            missing_changed_files_path = tmp_path / "missing-changed-files.txt"

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = self.preflight.main(
                    [
                        "--head-sha",
                        "abc123",
                        "--changed-files-file",
                        str(missing_changed_files_path),
                        "--github-output",
                        str(output_path),
                        "--json",
                    ]
                )

            output = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertIn("reason=diff_unavailable", output)
        self.assertIn("fail_check=true", output)
        self.assertIn('"reason": "diff_unavailable"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
