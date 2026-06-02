import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_cadence.pr_readiness import evaluate_pr_body_preflight, evaluate_pr_readiness, required_sections_from_template

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def check_run(name, status="COMPLETED", conclusion="SUCCESS", workflow="tests"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "workflowName": workflow,
        "detailsUrl": f"https://example.test/checks/{name}",
    }


def status_context(name, state="SUCCESS"):
    return {
        "__typename": "StatusContext",
        "context": name,
        "state": state,
        "targetUrl": f"https://example.test/status/{name}",
    }


def base_pr(**overrides):
    data = {
        "number": 330,
        "title": "[codex] Harden dispatch Board handoff contract",
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "",
        "body": "## Summary\nReady slice.\n\n## Testing\n- unit tests\n",
        "statusCheckRollup": [
            check_run("Python and protocol checks"),
            check_run("pytest"),
            check_run("pytest"),
            check_run("preflight", workflow="Codex Review"),
            check_run("codex", conclusion="SKIPPED", workflow="Codex Review"),
            status_context("CodeRabbit"),
        ],
    }
    data.update(overrides)
    return data


def review_threads_payload(nodes):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": nodes,
                    }
                }
            }
        }
    }


class PrReadinessTests(unittest.TestCase):
    def test_body_preflight_reports_ready_draft_body(self):
        packet = evaluate_pr_body_preflight(
            "# Summary\nReady slice.\n\nTesting\n-------\n- unit tests\n",
            required_body_sections=["Summary", "Testing"],
        )

        self.assertTrue(packet["ready_to_publish"])
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["recommended_next_action"], "publish_pr_body")
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(packet["template_summary"]["missing_sections"], [])

    def test_body_preflight_blocks_missing_template_sections(self):
        packet = evaluate_pr_body_preflight(
            "## Summary\nTesting is mentioned, but no testing section exists.\n",
            required_body_sections=["Summary", "Testing"],
        )

        self.assertFalse(packet["ready_to_publish"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "update_pr_body")
        self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"required_body_section_missing"})

    def test_body_preflight_blocks_when_no_section_contract_is_supplied(self):
        packet = evaluate_pr_body_preflight("## Summary\nReady slice.\n")

        self.assertFalse(packet["ready_to_publish"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "provide_template_or_sections")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"required_body_section_contract_missing"})

    def test_body_preflight_does_not_match_setext_heading_across_removed_blocks(self):
        cases = [
            "Testing\n<!-- note -->\n---\n",
            "Testing\n```md\nignored\n```\n---\n",
        ]
        for body in cases:
            with self.subTest(body=body):
                packet = evaluate_pr_body_preflight(body, required_body_sections=["Testing"])

                self.assertFalse(packet["ready_to_publish"])
                self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])

    def test_cli_pr_body_preflight_reads_body_and_template_without_runtime_or_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            fake_bin = Path(tmp) / "bin"
            body_file = Path(tmp) / "body.md"
            template_file = Path(tmp) / "pull_request_template.md"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)
            fake_bin.mkdir()
            fake_gh = fake_bin / ("gh.cmd" if os.name == "nt" else "gh")
            if os.name == "nt":
                fake_gh.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
            else:
                fake_gh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
                fake_gh.chmod(0o755)
            body_file.write_text("## Summary\nReady slice.\n\n## Risk Area\nLow.\n", encoding="utf-8")
            original_body = body_file.read_text(encoding="utf-8")
            template_file.write_text("## Summary\n\n## Risk Area\n\n## Testing\n", encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env.pop("CODEX_CADENCE_ROOT", None)
            env.pop("CODEX_TRANSMISSION_ROOT", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-body-preflight",
                    "--body-file",
                    str(body_file),
                    "--pr-template-file",
                    str(template_file),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["ready_to_publish"])
            self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])
            self.assertEqual(body_file.read_text(encoding="utf-8"), original_body)

    def test_cli_pr_body_preflight_blocks_when_no_template_or_required_sections_are_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_file = Path(tmp) / "body.md"
            body_file.write_text("## Summary\nReady slice.\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-body-preflight",
                    "--body-file",
                    str(body_file),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["ready_to_publish"])
            self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"required_body_section_contract_missing"})

    def test_ready_packet_allows_duplicate_successes_and_skipped_codex_review(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks", "pytest"],
            required_body_sections=["## Testing"],
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(
            set(packet),
            {
                "ready_to_merge",
                "decision",
                "recommended_next_action",
                "blockers",
                "waiting",
                "warnings",
                "check_summary",
                "duplicate_check_groups",
                "review_summary",
                "template_summary",
                "readiness_evidence",
                "pr",
            },
        )
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["recommended_next_action"], "merge_after_operator_confirmation")
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(packet["check_summary"]["failed"], 0)
        self.assertEqual(packet["check_summary"]["pending"], 0)
        self.assertEqual(packet["check_summary"]["passed"], 5)
        self.assertEqual(packet["check_summary"]["skipped"], 1)
        self.assertEqual(packet["template_summary"]["missing_sections"], [])
        self.assertEqual(packet["review_summary"]["decision"], "")
        self.assertEqual(packet["duplicate_check_groups"], [
            {
                "name": "pytest",
                "count": 2,
                "states": ["SUCCESS"],
                "blocking": False,
            }
        ])
        self.assertIn("duplicate_successful_checks", {warning["code"] for warning in packet["warnings"]})
        self.assertIn("codex_review_skipped", {warning["code"] for warning in packet["warnings"]})
        self.assertEqual(packet["readiness_evidence"]["source"], "saved_pr_json")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "saved_input")
        self.assertFalse(packet["readiness_evidence"]["live"])
        self.assertFalse(packet["readiness_evidence"]["stale"])
        self.assertIn("does_not_call_github", packet["readiness_evidence"]["limitations"])

    def test_stale_saved_pr_evidence_waits_for_refresh(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            evidence_source="saved_pr_json",
            evidence_captured_at="2026-05-30T00:00:00Z",
            max_evidence_age_minutes=30,
            now="2026-05-30T01:00:00Z",
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "stale")
        self.assertTrue(packet["readiness_evidence"]["stale"])
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"pr_evidence_stale"})

    def test_stale_saved_pr_evidence_refreshes_before_acting_on_blockers(self):
        packet = evaluate_pr_readiness(
            base_pr(reviewDecision="CHANGES_REQUESTED"),
            required_checks=["Python and protocol checks"],
            evidence_source="saved_pr_json",
            evidence_captured_at="2026-05-30T00:00:00Z",
            max_evidence_age_minutes=30,
            now="2026-05-30T01:00:00Z",
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "stale")
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"pr_evidence_stale"})
        self.assertIn("review_changes_requested", {item["code"] for item in packet["blockers"]})

    def test_future_pr_evidence_waits_for_refresh(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            evidence_source="saved_pr_json",
            evidence_captured_at="2026-05-30T02:00:00Z",
            max_evidence_age_minutes=30,
            now="2026-05-30T01:00:00Z",
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "stale")
        self.assertTrue(packet["readiness_evidence"]["stale"])
        self.assertLess(packet["readiness_evidence"]["age_minutes"], 0)
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"pr_evidence_from_future"})

    def test_live_like_pr_evidence_is_labeled_without_calling_github(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            evidence_source="live_pr_json",
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(packet["readiness_evidence"]["source"], "live_pr_json")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "live_like")
        self.assertTrue(packet["readiness_evidence"]["live"])
        self.assertFalse(packet["readiness_evidence"]["stale"])
        self.assertIn("caller_asserted_live_source", packet["readiness_evidence"]["limitations"])

    def test_live_like_pr_evidence_ignores_saved_json_age_policy(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            evidence_source="live_pr_json",
            evidence_captured_at="2026-05-30T00:00:00Z",
            max_evidence_age_minutes=30,
            now="2026-05-30T01:00:00Z",
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["readiness_evidence"]["freshness"], "live_like")
        self.assertFalse(packet["readiness_evidence"]["stale"])
        self.assertEqual(packet["waiting"], [])

    def test_failed_required_check_missing_template_and_changes_requested_block(self):
        packet = evaluate_pr_readiness(
            base_pr(
                reviewDecision="CHANGES_REQUESTED",
                body="## Summary\nMissing test section.\n",
                statusCheckRollup=[
                    check_run("Python and protocol checks"),
                    check_run("pytest", conclusion="FAILURE"),
                ],
            ),
            required_checks=["Python and protocol checks", "pytest"],
            required_body_sections=["## Testing"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "address_blockers")
        self.assertEqual(
            {blocker["code"] for blocker in packet["blockers"]},
            {"check_failed", "review_changes_requested", "required_body_section_missing"},
        )

    def test_unresolved_actionable_review_threads_block_readiness(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            review_threads=review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 120,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Address this current review finding before merge.",
                                    "outdated": False,
                                    "author": {"login": "coderabbitai"},
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-2",
                        "isResolved": True,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 130,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "body": "Resolved comments should not block.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-3",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "docs/protocol.md",
                        "line": 20,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-3",
                                    "body": "<!-- walkthrough_start -->\n## Walkthrough\nNo actionable findings.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                ]
            ),
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"unresolved_review_comment"})
        self.assertEqual(packet["review_feedback_summary"]["unresolved_actionable_comments"], 1)
        self.assertEqual(packet["review_feedback_summary"]["findings"][0]["id"], "comment-1")

    def test_template_sections_are_extracted_from_markdown_headings(self):
        template = """<!--
## Hidden Template Metadata
-->
# Pull Request

## Risk Area

```md
## Example Only
```

### Testing

#### Follow-up
"""

        self.assertEqual(
            required_sections_from_template(template),
            ["Pull Request", "Risk Area", "Testing", "Follow-up"],
        )

    def test_template_sections_match_body_headings_by_label(self):
        packet = evaluate_pr_readiness(
            base_pr(
                body="# summary\nReady slice.\n\n### testing\n- unit tests\n",
            ),
            required_body_sections=["Summary", "Testing"],
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(packet["template_summary"]["missing_sections"], [])
        self.assertEqual(packet["template_summary"]["required_sections"], [
            {"section": "Summary", "present": True},
            {"section": "Testing", "present": True},
        ])

    def test_template_sections_require_body_headings_not_substrings(self):
        packet = evaluate_pr_readiness(
            base_pr(
                body="## Summary\nTesting is mentioned, but no testing section was added.\n",
            ),
            required_body_sections=["Testing"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])
        self.assertIn("required_body_section_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_template_sections_ignore_body_comments_and_fenced_examples(self):
        packet = evaluate_pr_readiness(
            base_pr(
                body="## Summary\n<!-- ## Testing -->\n\n```md\n## Testing\n```\n",
            ),
            required_body_sections=["Testing"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])

    def test_template_fence_parser_tracks_opener_marker(self):
        template = """## Summary

```md
~~~not a fence close for backticks
## Example Only
```

## Testing
"""

        self.assertEqual(required_sections_from_template(template), ["Summary", "Testing"])

    def test_template_sections_support_setext_headings(self):
        template = "Summary\n=======\n\nTesting\n-------\n"

        self.assertEqual(required_sections_from_template(template), ["Summary", "Testing"])

    def test_template_sections_match_body_setext_headings(self):
        packet = evaluate_pr_readiness(
            base_pr(
                body="Summary\n=======\n\nTesting\n-------\n- unit tests\n",
            ),
            required_body_sections=["Summary", "Testing"],
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(packet["template_summary"]["missing_sections"], [])

    def test_template_sections_preserve_hash_in_heading_text(self):
        self.assertEqual(required_sections_from_template("## C#\n\n## F# ##\n"), ["C#", "F#"])

    def test_cli_reads_required_sections_from_pr_template_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            pr_json = Path(tmp) / "pr.json"
            template = Path(tmp) / "pull_request_template.md"
            pr_json.write_text(
                json.dumps(base_pr(body="## Summary\nReady slice.\n\n## Risk Area\nLow.\n")),
                encoding="utf-8",
            )
            template.write_text(
                "## Summary\n\n## Risk Area\n\n## Testing\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--required-check",
                    "Python and protocol checks",
                    "--pr-template-file",
                    str(template),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["ready_to_merge"])
            self.assertEqual(packet["template_summary"]["missing_sections"], ["Testing"])
            self.assertIn("required_body_section_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_required_codex_review_skip_is_warning_not_blocker(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[check_run("codex", conclusion="SKIPPED", workflow="Codex Review")]),
            required_checks=["codex"],
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertEqual(packet["blockers"], [])
        self.assertIn("codex_review_skipped", {warning["code"] for warning in packet["warnings"]})

    def test_pending_required_check_waits_without_readying_merge(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[check_run("Python and protocol checks", status="IN_PROGRESS", conclusion=None)]),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual(packet["recommended_next_action"], "wait_for_checks")
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(packet["waiting"], [
            {
                "code": "check_pending",
                "check": "Python and protocol checks",
                "message": "required check is still pending: Python and protocol checks",
            }
        ])

    def test_expected_status_context_waits(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[status_context("Python and protocol checks", state="EXPECTED")]),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"check_pending"})

    def test_startup_failure_blocks_required_check(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[check_run("Python and protocol checks", conclusion="STARTUP_FAILURE")]),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual({item["code"] for item in packet["blockers"]}, {"check_failed"})

    def test_missing_required_check_blocks(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[check_run("pytest")]),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual({item["code"] for item in packet["blockers"]}, {"required_check_missing"})

    def test_pr_state_blockers_are_explicit(self):
        cases = [
            ({"state": "CLOSED"}, "pr_not_open"),
            ({"isDraft": True}, "pr_draft"),
            ({"mergeable": "CONFLICTING"}, "merge_conflict"),
            ({"mergeStateStatus": "BLOCKED"}, "merge_state_blocked"),
            ({"reviewDecision": "REVIEW_REQUIRED"}, "review_required"),
        ]
        for overrides, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                packet = evaluate_pr_readiness(base_pr(**overrides), required_checks=["Python and protocol checks"])

                self.assertFalse(packet["ready_to_merge"])
                self.assertIn(expected_code, {item["code"] for item in packet["blockers"]})

    def test_unknown_merge_state_waits(self):
        packet = evaluate_pr_readiness(
            base_pr(mergeStateStatus="UNKNOWN"),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"merge_state_unknown"})

    def test_unstable_merge_state_waits_for_checks(self):
        packet = evaluate_pr_readiness(
            base_pr(mergeStateStatus="UNSTABLE"),
            required_checks=["Python and protocol checks"],
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"merge_state_waiting"})

    def test_failed_check_blocks_without_required_check_allowlist(self):
        packet = evaluate_pr_readiness(
            base_pr(statusCheckRollup=[check_run("lint", conclusion="FAILURE")]),
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual({item["code"] for item in packet["blockers"]}, {"check_failed"})

    def test_missing_status_check_rollup_waits(self):
        pr = base_pr()
        pr.pop("statusCheckRollup")

        packet = evaluate_pr_readiness(pr)

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "waiting")
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"status_check_rollup_missing"})

    def test_nested_workflow_name_marks_skipped_codex_review_warning(self):
        packet = evaluate_pr_readiness(
            base_pr(
                statusCheckRollup=[
                    {
                        "__typename": "CheckRun",
                        "name": "review",
                        "status": "COMPLETED",
                        "conclusion": "SKIPPED",
                        "checkSuite": {
                            "workflowRun": {
                                "workflow": {"name": "Codex Review"},
                            },
                        },
                    }
                ]
            )
        )

        self.assertTrue(packet["ready_to_merge"])
        self.assertIn("codex_review_skipped", {warning["code"] for warning in packet["warnings"]})

    def test_cli_reads_pr_json_file_and_required_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            pr_json = Path(tmp) / "pr.json"
            pr_json.write_text(json.dumps(base_pr()), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--required-check",
                    "Python and protocol checks",
                    "--required-check",
                    "pytest",
                    "--required-body-section",
                    "## Testing",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertTrue(packet["ready_to_merge"])
            self.assertEqual(packet["pr"]["number"], 330)
            self.assertEqual(packet["decision"], "ready")
            self.assertEqual(packet["readiness_evidence"]["freshness"], "saved_input")
            self.assertEqual(packet["readiness_evidence"]["source"], "saved_pr_json")

    def test_cli_pr_readiness_reads_review_threads_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr_json = Path(tmp) / "pr.json"
            threads = Path(tmp) / "threads.json"
            pr_json.write_text(json.dumps(base_pr()), encoding="utf-8")
            threads.write_text(
                json.dumps(
                    review_threads_payload(
                        [
                            {
                                "id": "thread-1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "codex_cadence/cli.py",
                                "line": 120,
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "comment-1",
                                            "body": "Fix this before merge.",
                                            "outdated": False,
                                        }
                                    ]
                                },
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--review-threads-file",
                    str(threads),
                    "--required-check",
                    "Python and protocol checks",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["ready_to_merge"])
            self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"unresolved_review_comment"})

    def test_cli_marks_stale_saved_pr_json_when_max_age_is_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr_json = Path(tmp) / "pr.json"
            pr_json.write_text(json.dumps(base_pr()), encoding="utf-8")
            old_timestamp = 946684800
            os.utime(pr_json, (old_timestamp, old_timestamp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--required-check",
                    "Python and protocol checks",
                    "--max-pr-json-age-minutes",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["ready_to_merge"])
            self.assertEqual(packet["decision"], "waiting")
            self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
            self.assertEqual(packet["readiness_evidence"]["freshness"], "stale")
            self.assertEqual({item["code"] for item in packet["waiting"]}, {"pr_evidence_stale"})

    def test_cli_rejects_negative_pr_json_max_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr_json = Path(tmp) / "pr.json"
            pr_json.write_text(json.dumps(base_pr()), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--max-pr-json-age-minutes",
                    "-1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-negative integer", result.stderr)

    def test_cli_pr_readiness_does_not_require_runtime_root_or_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            legacy_root = home / ".codex" / "transmission"
            cadence_root = home / ".codex" / "cadence"
            fake_bin = Path(tmp) / "bin"
            pr_json = Path(tmp) / "pr.json"
            legacy_root.mkdir(parents=True)
            cadence_root.mkdir(parents=True)
            fake_bin.mkdir()
            fake_gh = fake_bin / ("gh.cmd" if os.name == "nt" else "gh")
            if os.name == "nt":
                fake_gh.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
            else:
                fake_gh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
                fake_gh.chmod(0o755)
            pr_json.write_text(json.dumps(base_pr()), encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env.pop("CODEX_CADENCE_ROOT", None)
            env.pop("CODEX_TRANSMISSION_ROOT", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pr-readiness",
                    "--pr-json-file",
                    str(pr_json),
                    "--required-check",
                    "Python and protocol checks",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ready_to_merge"])


if __name__ == "__main__":
    unittest.main()
