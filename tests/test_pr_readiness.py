import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence.pr_readiness import evaluate_pr_body_preflight, evaluate_pr_readiness, required_sections_from_template
import codex_cadence.review_response as review_response_module
from codex_cadence.review_response import (
    evaluate_review_thread_resolution_plan,
    evaluate_review_response_materialization_plan,
    evaluate_review_response_plan,
    materialize_review_thread_resolution_plan,
    materialize_review_response_plan,
    review_thread_resolution_approval_token,
    review_response_materialization_approval_token,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"
REVIEW_RESPONSE_APPROVAL_SECRET_ENV = "CADENCE_REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET"
REVIEW_RESPONSE_APPROVAL_SECRET = "unit-test-review-response-materialization-secret"
REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV = "CADENCE_REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET"
REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET = "unit-test-review-thread-resolution-secret"


def checksum_json(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    import hashlib

    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        "headRefName": "codex/example-branch",
        "baseRefName": "main",
        "headRefOid": "abc123",
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
                    "number": 330,
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def init_pr_gate_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    Path(path, "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def review_response_materialization_result(pr, **overrides):
    packet = {
        "protocol_version": "cadence.v1",
        "schema_version": "review-response-materialization.v1",
        "packet": "review_response_materialization",
        "generated_at": "2026-06-11T10:05:00Z",
        "valid": True,
        "decision": "materialized",
        "recommended_next_action": "inspect_pull_request",
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": "approved",
        "execution_authority": "operator_approved_review_response_materialization",
        "github_write_started": True,
        "review_resolution": "not_claimed",
        "merge_readiness": "not_evaluated",
        "plan_file": "review-response-materialization-plan.json",
        "plan_checksum": checksum_json(
            {"packet": "review_response_materialization_plan", "mock": "plan"}
        ),
        "target_checksum": checksum_json(
            {"packet": "review_response_target", "mock": "target"}
        ),
        "pr": {
            "number": str(pr.get("number")),
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
            "url": pr.get("url", "https://github.com/Chef-Code/agentic-cadence/pull/330"),
        },
        "evidence": {},
        "intended_side_effects": ["update_pr_body"],
        "side_effects": ["updated_pr_body"],
        "command_trace": [],
        "github_writes": [{"kind": "update_pr_body"}],
        "blockers": [],
        "warnings": [],
        "limitations": ["does_not_merge"],
    }
    packet.update(overrides)
    return packet


def git_pr_materialization_result(pr, **overrides):
    packet = {
        "protocol_version": "cadence.v1",
        "schema_version": "git-pr-materialization.v1",
        "packet": "git_pr_materialization",
        "generated_at": "2026-06-11T10:05:00Z",
        "valid": True,
        "decision": "materialized",
        "recommended_next_action": "inspect_pull_request",
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": "approved",
        "execution_authority": "operator_approved_git_pr_materialization",
        "merge_readiness": "not_evaluated",
        "plan_file": "git-pr-plan.json",
        "plan_checksum": "sha256:plan",
        "repository": {
            "base_branch": pr.get("baseRefName"),
            "current_head": pr.get("headRefOid"),
        },
        "proposed_branch": pr.get("headRefName"),
        "proposed_pr_title": pr.get("title"),
        "pr_number": None,
        "pr_url": f"https://github.com/Chef-Code/agentic-cadence/pull/{pr.get('number')}",
        "remote": "origin",
        "remote_url": "https://github.com/Chef-Code/agentic-cadence.git",
        "intended_side_effects": ["push_branch", "create_pull_request"],
        "side_effects": ["audit_intent_record_appended", "pushed_branch", "created_pull_request", "audit_result_record_appended"],
        "command_trace": [],
        "blockers": [],
        "warnings": [],
        "limitations": ["does_not_auto_merge"],
    }
    packet.update(overrides)
    return packet


def review_thread_resolution_materialization_result(pr, *, thread_ids=None, **overrides):
    thread_ids = list(thread_ids or ["thread-1"])
    github_writes = [
        {
            "kind": "resolve_review_thread",
            "pr_number": str(pr.get("number")),
            "thread_id": thread_id,
            "comment_ids": [f"comment-{index}"],
            "github_thread_id": thread_id,
            "is_resolved": True,
            "status": "resolved",
        }
        for index, thread_id in enumerate(thread_ids, start=1)
    ]
    packet = {
        "protocol_version": "cadence.v1",
        "schema_version": "review-thread-resolution-materialization.v1",
        "packet": "review_thread_resolution_materialization",
        "generated_at": "2026-06-11T10:05:00Z",
        "valid": True,
        "decision": "materialized",
        "recommended_next_action": "inspect_pull_request",
        "approval_state": "approved",
        "execution_authority": "operator_approved_review_thread_resolution",
        "github_write_started": True,
        "review_resolution": "resolved",
        "merge_readiness": "not_evaluated",
        "plan_file": "review-thread-resolution-plan.json",
        "plan_checksum": checksum_json(
            {"packet": "review_thread_resolution_plan", "mock": "plan"}
        ),
        "target_checksum": checksum_json(
            {"packet": "review_thread_resolution_target", "mock": "target"}
        ),
        "approval_target": {
            "schema_version": "review-thread-resolution-approval-target.v1",
            "pr_number": str(pr.get("number")),
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
            "thread_ids": thread_ids,
        },
        "pr": {
            "number": str(pr.get("number")),
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
            "url": pr.get("url", "https://github.com/Chef-Code/agentic-cadence/pull/330"),
        },
        "evidence": {},
        "intended_side_effects": ["resolve_review_thread" for _thread_id in thread_ids],
        "side_effects": [
            "audit_intent_record_appended",
            *["resolved_review_thread" for _thread_id in thread_ids],
            "audit_result_record_appended",
        ],
        "command_trace": [],
        "github_writes": github_writes,
        "blockers": [],
        "warnings": [],
        "limitations": ["does_not_merge"],
    }
    packet.update(overrides)
    return packet


def write_github_evidence_sync_files(root, pr, review_threads, captured_at="2026-06-11T10:10:00Z"):
    evidence_dir = Path(root, "github-evidence")
    evidence_dir.mkdir()
    pr_number = pr["number"]
    pr_path = evidence_dir / f"pr-{pr_number}.json"
    threads_path = evidence_dir / f"pr-{pr_number}-review-threads.json"
    summary_path = evidence_dir / f"pr-{pr_number}-github-evidence.json"
    pr_payload = dict(pr)
    threads_payload = dict(review_threads)
    pr_payload["github_evidence"] = {
        "source": "gh_pr_view",
        "captured_at": captured_at,
        "freshness": "saved_input",
        "live": False,
        "stale": False,
    }
    threads_payload["github_evidence"] = {
        "source": "gh_graphql_review_threads",
        "captured_at": captured_at,
        "freshness": "saved_input",
        "live": False,
        "stale": False,
    }
    summary = {
        "protocol_version": "cadence.v1",
        "schema_version": "github-evidence-sync.v1",
        "packet": "github_evidence_sync",
        "valid": True,
        "decision": "saved",
        "recommended_next_action": "use_saved_github_evidence",
        "repo": "Chef-Code/agentic-cadence",
        "pr_number": pr_number,
        "out_dir": str(evidence_dir),
        "captured_at": captured_at,
        "evidence": {
            "source": "github_live_readonly",
            "captured_at": captured_at,
            "freshness": "saved_input",
            "live": False,
            "stale": False,
        },
        "files": {
            "pr_json": str(pr_path),
            "review_threads_json": str(threads_path),
            "summary_json": str(summary_path),
        },
        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": pr.get("state"),
            "head_ref": pr.get("headRefName"),
            "base_ref": pr.get("baseRefName"),
            "head_sha": pr.get("headRefOid"),
        },
        "blockers": [],
        "warnings": [],
        "side_effects": ["wrote_pr_json", "wrote_review_threads_json", "wrote_evidence_summary"],
        "github_write_started": False,
        "command_trace": [],
    }
    pr_path.write_text(json.dumps(pr_payload), encoding="utf-8")
    threads_path.write_text(json.dumps(threads_payload), encoding="utf-8")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path, summary


def write_fake_review_response_gh(fake_bin, log_path):
    script = Path(fake_bin) / "fake_gh_review_response.py"
    script.write_text(
        """
import json
import os
import sys

args = sys.argv[1:]
event = {"argv": args}
if "--body-file" in args:
    body_file = args[args.index("--body-file") + 1]
    with open(body_file, encoding="utf-8") as handle:
        event["body"] = handle.read()
if "-F" in args or "-f" in args:
    fields = []
    for index, value in enumerate(args):
        if value in ("-F", "-f") and index + 1 < len(args):
            fields.append(args[index + 1])
    event["fields"] = fields
    for field in fields:
        if field.startswith("body=@"):
            with open(field.removeprefix("body=@"), encoding="utf-8") as handle:
                event["body"] = handle.read()

with open(os.environ["GH_FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(event, sort_keys=True) + "\\n")

if args[:2] == ["pr", "edit"]:
    if os.environ.get("GH_FAKE_FAIL_EDIT"):
        print("edit failed", file=sys.stderr)
        sys.exit(1)
    print(os.environ.get("GH_FAKE_PR_URL", "https://github.example/local/test/pull/330"))
    sys.exit(0)

if args[:2] == ["api", "graphql"]:
    query = next((field.removeprefix("query=") for field in event.get("fields", []) if field.startswith("query=")), "")
    thread_id = next((field.removeprefix("threadId=") for field in event.get("fields", []) if field.startswith("threadId=")), "")
    if "resolveReviewThread" in query:
        if os.environ.get("GH_FAKE_FAIL_RESOLVE"):
            print("resolve failed", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("GH_FAKE_MALFORMED_RESOLVE"):
            print("{not json")
            sys.exit(0)
        resolved_thread_id = os.environ.get("GH_FAKE_RESOLVED_THREAD_ID", thread_id or "thread-1")
        if os.environ.get("GH_FAKE_RESOLVE_MISMATCH"):
            resolved_thread_id = "thread-other"
        resolved = os.environ.get("GH_FAKE_RESOLVE_UNCONFIRMED") != "1"
        thread_payload = {"isResolved": resolved}
        if not os.environ.get("GH_FAKE_RESOLVE_MISSING_ID"):
            thread_payload["id"] = resolved_thread_id
        print(json.dumps({
            "data": {
                "resolveReviewThread": {
                    "thread": thread_payload
                }
            }
        }))
        sys.exit(0)
    if os.environ.get("GH_FAKE_FAIL_COMMENT"):
        print("comment failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({
        "data": {
            "addPullRequestReviewThreadReply": {
                "comment": {
                    "id": os.environ.get("GH_FAKE_REPLY_ID", "reply-1"),
                    "url": os.environ.get("GH_FAKE_REPLY_URL", "https://github.example/local/test/pull/330#discussion_r1")
                }
            }
        }
    }))
    sys.exit(0)

print("unexpected gh invocation", file=sys.stderr)
sys.exit(99)
""".lstrip(),
        encoding="utf-8",
    )
    fake_gh = Path(fake_bin) / ("gh.cmd" if os.name == "nt" else "gh")
    if os.name == "nt":
        fake_gh.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        fake_gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        fake_gh.chmod(0o755)
    return fake_gh


def review_response_materialization_inputs():
    pr = base_pr(body="## Summary\nReady slice.\n")
    threads = review_threads_payload(
        [
            {
                "id": "thread-1",
                "path": "codex_cadence/cli.py",
                "line": 42,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "comment-1",
                            "path": "codex_cadence/cli.py",
                            "line": 42,
                            "outdated": False,
                            "body": "Please address this current review finding before merge.",
                            "author": {"login": "coderabbitai"},
                        }
                    ],
                },
            }
        ]
    )
    response_plan = evaluate_review_response_plan(
        pr,
        review_threads=threads,
        required_body_sections=["Summary", "Testing"],
        evidence_captured_at="2026-06-11T18:00:00Z",
        now="2026-06-11T18:05:00Z",
        max_evidence_age_minutes=30,
    )
    updated_body = "## Summary\nReady slice.\n\n## Testing\n- unit tests\n"
    comment_body = "Addressed in the latest push; tests now cover this path."
    writes = [
        {
            "kind": "update_pr_body",
            "body": updated_body,
            "body_checksum": checksum_json(updated_body),
        },
        {
            "kind": "post_review_comment",
            "comment_id": "comment-1",
            "body": comment_body,
            "body_checksum": checksum_json(comment_body),
        },
    ]
    plan = evaluate_review_response_materialization_plan(
        response_plan,
        pr=pr,
        review_threads=threads,
        intended_writes=writes,
        required_body_sections=["Summary", "Testing"],
        evidence_captured_at="2026-06-11T18:00:00Z",
        now="2026-06-11T18:05:00Z",
        max_evidence_age_minutes=30,
    )
    return pr, threads, response_plan, plan, updated_body, comment_body


def review_thread_resolution_inputs(tmp, evidence_tmp):
    from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

    init_pr_gate_repo(tmp)
    pr, threads, _response_plan, _plan, _updated_body, comment_body = review_response_materialization_inputs()
    response_materialization = review_response_materialization_result(
        pr,
        intended_side_effects=["post_review_comment"],
        side_effects=["posted_review_comment"],
        github_writes=[
            {
                "kind": "post_review_comment",
                "pr_number": str(pr["number"]),
                "thread_id": "thread-1",
                "comment_ids": ["comment-1"],
                "body_checksum": checksum_json(comment_body),
                "github_comment_id": "reply-1",
                "url": "https://github.example/local/test/pull/330#discussion_r1",
            }
        ],
    )
    sync_path, sync_packet = write_github_evidence_sync_files(
        evidence_tmp,
        pr,
        threads,
        captured_at="2026-06-11T18:10:00Z",
    )
    saved_pr = json.loads(Path(sync_packet["files"]["pr_json"]).read_text(encoding="utf-8"))
    saved_threads = json.loads(Path(sync_packet["files"]["review_threads_json"]).read_text(encoding="utf-8"))
    post_write_gate = evaluate_post_write_pr_evidence_gate(
        cwd=Path(tmp),
        materialization_result=response_materialization,
        github_evidence_sync=sync_packet,
        github_evidence_file=sync_path,
        required_checks=["Python and protocol checks"],
    )
    return saved_pr, saved_threads, response_materialization, post_write_gate


class PrReadinessTests(unittest.TestCase):
    def test_review_thread_resolution_plan_binds_fresh_unresolved_thread_targets(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)

            packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1", "thread-1"],
                evidence_captured_at="2026-06-11T18:10:00Z",
                now="2026-06-11T18:15:00Z",
                max_evidence_age_minutes=30,
            )

        self.assertTrue(packet["valid"])
        self.assertTrue(packet["plan_ready"])
        self.assertEqual(packet["schema_version"], "review-thread-resolution-plan.v1")
        self.assertEqual(packet["packet"], "review_thread_resolution_plan")
        self.assertEqual(packet["recommended_next_action"], "approve_review_thread_resolution")
        self.assertEqual(packet["approval_state"], "not_approved")
        self.assertTrue(packet["operator_confirmation_required"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual(packet["target_checksum"], checksum_json(packet["target"]))
        self.assertEqual(packet["target"]["response_materialization_checksum"], checksum_json(response_materialization))
        self.assertEqual(packet["target"]["post_write_gate_checksum"], checksum_json(post_write_gate))
        self.assertEqual(packet["target"]["review_threads_checksum"], checksum_json(threads))
        self.assertEqual(packet["target"]["thread_ids"], ["thread-1"])
        self.assertEqual(len(packet["resolution_plan"]), 1)
        self.assertEqual(packet["resolution_plan"][0]["kind"], "resolve_review_thread")
        self.assertEqual(packet["resolution_plan"][0]["thread_id"], "thread-1")
        self.assertEqual(packet["resolution_plan"][0]["comment_ids"], ["comment-1"])
        self.assertEqual(packet["command_trace"], [])
        self.assertIn("does_not_call_github", packet["limitations"])

    def test_review_thread_resolution_plan_blocks_ineligible_targets(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-responded",
                        "path": "codex_cadence/cli.py",
                        "line": 10,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-responded",
                                    "path": "codex_cadence/cli.py",
                                    "line": 10,
                                    "outdated": False,
                                    "body": "Please address this current review finding.",
                                }
                            ],
                        },
                    },
                    {
                        "id": "thread-resolved",
                        "path": "codex_cadence/cli.py",
                        "line": 20,
                        "isResolved": True,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-resolved",
                                    "path": "codex_cadence/cli.py",
                                    "line": 20,
                                    "outdated": False,
                                    "body": "Resolved feedback should not be targeted.",
                                }
                            ],
                        },
                    },
                    {
                        "id": "thread-outdated",
                        "path": "codex_cadence/cli.py",
                        "line": 30,
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-outdated",
                                    "path": "codex_cadence/cli.py",
                                    "line": 30,
                                    "outdated": False,
                                    "body": "Outdated feedback should not be targeted.",
                                }
                            ],
                        },
                    },
                    {
                        "id": "thread-summary",
                        "path": "codex_cadence/cli.py",
                        "line": 40,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-summary",
                                    "path": "codex_cadence/cli.py",
                                    "line": 40,
                                    "outdated": False,
                                    "body": "No actionable comments",
                                }
                            ],
                        },
                    },
                    {
                        "id": "thread-unresponded",
                        "path": "codex_cadence/cli.py",
                        "line": 50,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-unresponded",
                                    "path": "codex_cadence/cli.py",
                                    "line": 50,
                                    "outdated": False,
                                    "body": "This needs a prior approved response before resolution.",
                                }
                            ],
                        },
                    },
                ]
            )
            response_materialization = review_response_materialization_result(
                pr,
                intended_side_effects=["post_review_comment"],
                side_effects=["posted_review_comment"],
                github_writes=[
                    {
                        "kind": "post_review_comment",
                        "pr_number": str(pr["number"]),
                        "thread_id": "thread-responded",
                        "comment_ids": ["comment-responded"],
                        "body_checksum": checksum_json("responded"),
                    }
                ],
            )
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, threads)
            saved_pr = json.loads(Path(sync_packet["files"]["pr_json"]).read_text(encoding="utf-8"))
            saved_threads = json.loads(Path(sync_packet["files"]["review_threads_json"]).read_text(encoding="utf-8"))
            post_write_gate = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=response_materialization,
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
            )

            packet = evaluate_review_thread_resolution_plan(
                pr=saved_pr,
                review_threads=saved_threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=[
                    "thread-resolved",
                    "thread-outdated",
                    "thread-summary",
                    "thread-unresponded",
                    "thread-missing",
                ],
            )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], [])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("review_thread_resolution_target_already_resolved", blocker_codes)
        self.assertIn("review_thread_resolution_target_outdated", blocker_codes)
        self.assertIn("review_thread_resolution_target_not_actionable", blocker_codes)
        self.assertIn("review_thread_resolution_target_unresponded", blocker_codes)
        self.assertIn("review_thread_resolution_target_missing", blocker_codes)

    def test_review_thread_resolution_plan_blocks_stale_incomplete_mismatched_or_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)

            stale_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
                evidence_captured_at="2026-06-11T18:10:00Z",
                now="2026-06-11T19:00:00Z",
                max_evidence_age_minutes=30,
            )
            incomplete_threads = json.loads(json.dumps(threads))
            incomplete_threads["data"]["repository"]["pullRequest"]["reviewThreads"]["pageInfo"]["hasNextPage"] = True
            incomplete_gate = json.loads(json.dumps(post_write_gate))
            incomplete_gate["refresh"]["review_threads_checksum"] = checksum_json(incomplete_threads)
            incomplete_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=incomplete_threads,
                response_materialization=response_materialization,
                post_write_gate=incomplete_gate,
                target_thread_ids=["thread-1"],
            )
            mismatched_gate = json.loads(json.dumps(post_write_gate))
            mismatched_gate["refresh"]["head_sha"] = "def456"
            mismatch_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=mismatched_gate,
                target_thread_ids=["thread-1"],
            )
            wrong_pr_threads = json.loads(json.dumps(threads))
            wrong_pr_threads["data"]["repository"]["pullRequest"]["number"] = 331
            wrong_pr_gate = json.loads(json.dumps(post_write_gate))
            wrong_pr_gate["refresh"]["review_threads_checksum"] = checksum_json(wrong_pr_threads)
            wrong_pr_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=wrong_pr_threads,
                response_materialization=response_materialization,
                post_write_gate=wrong_pr_gate,
                target_thread_ids=["thread-1"],
            )
            missing_gate_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=None,
                target_thread_ids=["thread-1"],
            )
            blocked_gate = json.loads(json.dumps(post_write_gate))
            blocked_gate["valid"] = False
            blocked_gate["decision"] = "blocked"
            blocked_gate["recommended_next_action"] = "operator_review"
            blocked_gate["blockers"] = [{"code": "check_failed", "message": "required check failed"}]
            blocked_gate_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=blocked_gate,
                target_thread_ids=["thread-1"],
            )
            tampered_materialization = json.loads(json.dumps(response_materialization))
            tampered_materialization["github_writes"].append(
                {
                    "kind": "post_review_comment",
                    "pr_number": str(pr["number"]),
                    "thread_id": "thread-2",
                    "comment_ids": ["comment-2"],
                    "body_checksum": checksum_json("tampered"),
                }
            )
            tampered_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=tampered_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            missing_materialization_packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=None,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )

        self.assertFalse(stale_packet["valid"])
        self.assertEqual(stale_packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertIn("pr_evidence_stale", {blocker["code"] for blocker in stale_packet["blockers"]})
        self.assertFalse(incomplete_packet["valid"])
        self.assertEqual(incomplete_packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertIn("review_thread_evidence_invalid", {blocker["code"] for blocker in incomplete_packet["blockers"]})
        self.assertFalse(mismatch_packet["valid"])
        self.assertIn("post_write_gate_refresh_mismatch", {blocker["code"] for blocker in mismatch_packet["blockers"]})
        self.assertFalse(wrong_pr_packet["valid"])
        self.assertIn("review_thread_resolution_pr_target_mismatch", {blocker["code"] for blocker in wrong_pr_packet["blockers"]})
        self.assertFalse(missing_gate_packet["valid"])
        self.assertIn("post_write_gate_missing", {blocker["code"] for blocker in missing_gate_packet["blockers"]})
        self.assertFalse(blocked_gate_packet["valid"])
        self.assertIn("post_write_gate_not_ready", {blocker["code"] for blocker in blocked_gate_packet["blockers"]})
        self.assertIn("check_failed", {blocker["code"] for blocker in blocked_gate_packet["blockers"]})
        self.assertFalse(tampered_packet["valid"])
        self.assertIn(
            "post_write_gate_materialization_checksum_mismatch",
            {blocker["code"] for blocker in tampered_packet["blockers"]},
        )
        self.assertFalse(missing_materialization_packet["valid"])
        self.assertEqual(
            missing_materialization_packet["recommended_next_action"],
            "provide_review_response_materialization",
        )
        self.assertIn(
            "review_response_materialization_missing",
            {blocker["code"] for blocker in missing_materialization_packet["blockers"]},
        )

    def test_review_thread_resolution_plan_blocks_unresponded_current_comment_in_responded_thread(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            changed_threads = json.loads(json.dumps(threads))
            comments = changed_threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"][
                "nodes"
            ]
            comments.append(
                {
                    "id": "comment-2",
                    "path": "codex_cadence/cli.py",
                    "line": 43,
                    "outdated": False,
                    "body": "Please also address this newer actionable review finding.",
                    "author": {"login": "coderabbitai"},
                }
            )
            changed_gate = json.loads(json.dumps(post_write_gate))
            changed_gate["refresh"]["review_threads_checksum"] = checksum_json(changed_threads)

            packet = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=changed_threads,
                response_materialization=response_materialization,
                post_write_gate=changed_gate,
                target_thread_ids=["thread-1"],
            )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertIn(
            "review_thread_resolution_target_comment_unresponded",
            {blocker["code"] for blocker in packet["blockers"]},
        )

    def test_cli_review_thread_resolution_plan_reads_saved_files_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
            before = {
                pr_path: pr_path.read_text(encoding="utf-8"),
                threads_path: threads_path.read_text(encoding="utf-8"),
                materialization_path: materialization_path.read_text(encoding="utf-8"),
                gate_path: gate_path.read_text(encoding="utf-8"),
            }

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "review-thread-resolution-plan",
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--thread-id",
                    "thread-1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet["schema_version"], "review-thread-resolution-plan.v1")
            self.assertTrue(packet["plan_ready"])
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["target"]["thread_ids"], ["thread-1"])
            self.assertEqual(packet["side_effects"], [])
            self.assertEqual({path: path.read_text(encoding="utf-8") for path in before}, before)

    def test_cli_review_thread_resolution_plan_uses_gate_refresh_timestamp_for_freshness(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "review-thread-resolution-plan",
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--thread-id",
                    "thread-1",
                    "--max-pr-json-age-minutes",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            packet = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertIn("pr_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})

    def test_review_thread_resolution_materialize_resolves_exact_targets_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            current_captured_at = review_response_module.utc_now()
            post_write_gate = json.loads(json.dumps(post_write_gate))
            post_write_gate["refresh"]["captured_at"] = current_captured_at
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
                evidence_captured_at=current_captured_at,
                now=current_captured_at,
                max_evidence_age_minutes=30,
            )
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-thread-resolution-plan.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
            token = review_thread_resolution_approval_token(
                plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-thread-resolution-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--approval-token",
                    token,
                    "--max-pr-json-age-minutes",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet["schema_version"], "review-thread-resolution-materialization.v1")
            self.assertEqual(packet["packet"], "review_thread_resolution_materialization")
            self.assertTrue(packet["valid"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["approval_state"], "approved")
            self.assertEqual(packet["execution_authority"], "operator_approved_review_thread_resolution")
            self.assertTrue(packet["github_write_started"])
            self.assertEqual(packet["review_resolution"], "resolved")
            self.assertEqual(packet["merge_readiness"], "not_evaluated")
            self.assertEqual(packet["plan_checksum"], checksum_json(plan))
            self.assertEqual(packet["target_checksum"], plan["target_checksum"])
            self.assertEqual(
                packet["side_effects"],
                [
                    "audit_intent_record_appended",
                    "resolved_review_thread",
                    "audit_result_record_appended",
                ],
            )
            self.assertEqual([trace["label"] for trace in packet["command_trace"]], ["resolve_review_thread"])
            self.assertEqual(packet["github_writes"][0]["kind"], "resolve_review_thread")
            self.assertEqual(packet["github_writes"][0]["thread_id"], "thread-1")
            self.assertEqual(packet["github_writes"][0]["comment_ids"], ["comment-1"])
            self.assertTrue(packet["github_writes"][0]["is_resolved"])
            self.assertEqual(packet["github_writes"][0]["status"], "resolved")
            self.assertIn("does_not_post_comments", packet["limitations"])
            self.assertIn("does_not_update_pr_body", packet["limitations"])
            gh_events = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(gh_events), 1)
            self.assertEqual(gh_events[0]["argv"][:2], ["api", "graphql"])
            self.assertIn("threadId=thread-1", gh_events[0]["fields"])
            self.assertNotIn("body", gh_events[0])

            replay_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["review_thread_resolution_intent"], 1)
            self.assertEqual(replay["events_by_type"]["review_thread_resolution_result"], 1)

    def test_review_thread_resolution_materialize_blocks_missing_or_wrong_approval_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-thread-resolution-plan.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

            missing_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-thread-resolution-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                wrong_packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=plan_path,
                    approval_token="approve-review-thread-resolution:hmac-sha256:" + "0" * 64,
                    runtime_root=runtime_root,
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        self.assertEqual(missing_result.returncode, 1, missing_result.stderr)
        missing_packet = json.loads(missing_result.stdout)
        self.assertFalse(missing_packet["valid"])
        self.assertEqual(missing_packet["approval_state"], "not_approved")
        self.assertFalse(missing_packet["github_write_started"])
        self.assertEqual(missing_packet["side_effects"], [])
        self.assertIn("operator_approval_missing", {blocker["code"] for blocker in missing_packet["blockers"]})
        self.assertFalse(wrong_packet["valid"])
        self.assertEqual(wrong_packet["approval_state"], "approval_mismatch")
        self.assertEqual(wrong_packet["side_effects"], [])
        self.assertIn("operator_approval_mismatch", {blocker["code"] for blocker in wrong_packet["blockers"]})
        self.assertFalse(gh_log.exists())
        self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_review_thread_resolution_materialize_rechecks_fresh_unresolved_exact_target_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
                evidence_captured_at="2026-06-11T18:10:00Z",
                now="2026-06-11T18:15:00Z",
                max_evidence_age_minutes=30,
            )
            changed_pr = dict(pr)
            changed_pr["headRefOid"] = "def456"
            changed_threads = json.loads(json.dumps(threads))
            changed_threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["isResolved"] = True
            token = review_thread_resolution_approval_token(
                plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime",
                    pr=changed_pr,
                    review_threads=changed_threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                    pr_evidence_captured_at="2026-06-11T18:10:00Z",
                    max_pr_evidence_age_minutes=30,
                    now="2026-06-11T19:00:00Z",
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], ["audit_result_record_appended"])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("pr_evidence_stale", blocker_codes)
        self.assertIn("review_thread_resolution_pr_target_mismatch", blocker_codes)
        self.assertIn("review_thread_resolution_plan_review_threads_checksum_mismatch", blocker_codes)
        self.assertIn("review_thread_resolution_target_already_resolved", blocker_codes)

    def test_review_thread_resolution_materialize_blocks_plan_target_action_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            tampered_plan = json.loads(json.dumps(plan))
            tampered_plan["resolution_plan"][0]["thread_id"] = "thread-other"
            token = review_thread_resolution_approval_token(
                tampered_plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=tampered_plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], ["audit_result_record_appended"])
        self.assertIn(
            "review_thread_resolution_plan_target_mismatch",
            {blocker["code"] for blocker in packet["blockers"]},
        )

    def test_review_thread_resolution_materialize_blocks_target_payload_and_checksum_drift(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            target_payload_drift = json.loads(json.dumps(plan))
            target_payload_drift["target"]["thread_ids"] = ["thread-1", "thread-other"]
            target_payload_drift["target_checksum"] = checksum_json(target_payload_drift["target"])
            target_checksum_drift = json.loads(json.dumps(plan))
            target_checksum_drift["target_checksum"] = checksum_json({"unexpected": "target"})
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                payload_drift_packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=target_payload_drift,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=review_thread_resolution_approval_token(
                        target_payload_drift,
                        approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
                    ),
                    runtime_root=tmp_path / "runtime-payload",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                )
                checksum_drift_packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=target_checksum_drift,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=review_thread_resolution_approval_token(
                        target_checksum_drift,
                        approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
                    ),
                    runtime_root=tmp_path / "runtime-checksum",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        for packet in (payload_drift_packet, checksum_drift_packet):
            self.assertFalse(packet["valid"])
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["side_effects"], ["audit_result_record_appended"])
            self.assertIn(
                "review_thread_resolution_target_checksum_mismatch",
                {blocker["code"] for blocker in packet["blockers"]},
            )

    def test_review_thread_resolution_materialize_rechecks_post_write_gate_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            changed_gate = json.loads(json.dumps(post_write_gate))
            changed_gate["refresh"]["head_sha"] = "def456"
            token = review_thread_resolution_approval_token(
                plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=runtime_root,
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=changed_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

            self.assertFalse(packet["valid"])
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["side_effects"], ["audit_result_record_appended"])
            blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
            self.assertIn("post_write_gate_refresh_mismatch", blocker_codes)
            self.assertIn("review_thread_resolution_target_checksum_mismatch", blocker_codes)
            replay_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["review_thread_resolution_result"], 1)

    def test_cli_review_thread_resolution_materialize_uses_gate_refresh_timestamp_for_freshness(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            stale_gate = json.loads(json.dumps(post_write_gate))
            stale_gate["refresh"]["captured_at"] = "2026-06-11T17:00:00Z"
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
                evidence_captured_at="2026-06-11T18:10:00Z",
                now="2026-06-11T18:15:00Z",
                max_evidence_age_minutes=30,
            )
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-thread-resolution-plan.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(stale_gate), encoding="utf-8")
            token = review_thread_resolution_approval_token(
                plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-thread-resolution-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--approval-token",
                    token,
                    "--max-pr-json-age-minutes",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        packet = json.loads(result.stdout)
        self.assertFalse(packet["valid"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], ["audit_result_record_appended"])
        self.assertIn("pr_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})
        self.assertFalse(gh_log.exists())

    def test_review_thread_resolution_materialize_blocks_prior_materialization_mismatch_or_missing(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            changed_materialization = json.loads(json.dumps(response_materialization))
            changed_materialization["github_writes"][0]["github_comment_id"] = "reply-other"
            token = review_thread_resolution_approval_token(
                plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                mismatch_packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime-mismatch",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=changed_materialization,
                    post_write_gate=post_write_gate,
                )
                missing_packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime-missing",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=None,
                    post_write_gate=post_write_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(mismatch_packet["valid"])
        self.assertFalse(mismatch_packet["github_write_started"])
        self.assertEqual(mismatch_packet["side_effects"], ["audit_result_record_appended"])
        mismatch_codes = {blocker["code"] for blocker in mismatch_packet["blockers"]}
        self.assertIn("review_thread_resolution_response_materialization_checksum_mismatch", mismatch_codes)
        self.assertIn("post_write_gate_materialization_checksum_mismatch", mismatch_codes)
        self.assertFalse(missing_packet["valid"])
        self.assertFalse(missing_packet["github_write_started"])
        self.assertEqual(missing_packet["side_effects"], ["audit_result_record_appended"])
        missing_codes = {blocker["code"] for blocker in missing_packet["blockers"]}
        self.assertIn("review_response_materialization_missing", missing_codes)
        self.assertIn("review_thread_resolution_response_materialization_checksum_mismatch", missing_codes)

    def test_review_thread_resolution_materialize_blocks_blank_prior_materialization_checksum(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            tampered_plan = json.loads(json.dumps(plan))
            tampered_plan["target"]["response_materialization_checksum"] = ""
            tampered_plan["target"]["actions"][0]["response_materialization_checksum"] = ""
            tampered_plan["resolution_plan"][0]["response_materialization_checksum"] = ""
            tampered_plan["target_checksum"] = checksum_json(tampered_plan["target"])
            token = review_thread_resolution_approval_token(
                tampered_plan,
                approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
            )
            original_secret = os.environ.get(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET
            try:
                packet = materialize_review_thread_resolution_plan(
                    cwd=tmp_path,
                    plan_packet=tampered_plan,
                    plan_file=tmp_path / "review-thread-resolution-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime",
                    pr=pr,
                    review_threads=threads,
                    response_materialization=response_materialization,
                    post_write_gate=post_write_gate,
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["github_write_started"])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("review_thread_resolution_target_checksum_invalid", blocker_codes)
        self.assertIn("review_thread_resolution_action_checksum_invalid", blocker_codes)
        self.assertIn("review_thread_resolution_response_materialization_checksum_invalid", blocker_codes)

    def test_review_thread_resolution_materialize_unconfirmed_success_keeps_write_boundary_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-thread-resolution-plan.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
            token = review_thread_resolution_approval_token(plan, approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_RESOLVE_UNCONFIRMED"] = "1"
            env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-thread-resolution-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--approval-token",
                    token,
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["valid"])
            self.assertTrue(packet["github_write_started"])
            self.assertEqual(packet["review_resolution"], "partial")
            self.assertEqual(packet["github_writes"][0]["thread_id"], "thread-1")
            self.assertEqual(packet["github_writes"][0]["status"], "unconfirmed")
            self.assertFalse(packet["github_writes"][0]["is_resolved"])
            self.assertNotIn("resolved_review_thread", packet["side_effects"])
            self.assertIn("review_thread_resolution_unconfirmed", {blocker["code"] for blocker in packet["blockers"]})

    def test_review_thread_resolution_materialize_mismatched_or_malformed_response_records_result_audit(self):
        cases = [
            ("GH_FAKE_RESOLVE_MISMATCH", "review_thread_resolution_response_mismatch"),
            ("GH_FAKE_RESOLVE_MISSING_ID", "review_thread_resolution_response_mismatch"),
            ("GH_FAKE_MALFORMED_RESOLVE", "review_thread_resolution_unconfirmed"),
        ]
        for env_flag, expected_code in cases:
            with self.subTest(env_flag=env_flag):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
                    pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
                    tmp_path = Path(tmp)
                    runtime_root = Path(evidence_tmp) / "runtime"
                    plan = evaluate_review_thread_resolution_plan(
                        pr=pr,
                        review_threads=threads,
                        response_materialization=response_materialization,
                        post_write_gate=post_write_gate,
                        target_thread_ids=["thread-1"],
                    )
                    pr_path = tmp_path / "pr.json"
                    threads_path = tmp_path / "review-threads.json"
                    plan_path = tmp_path / "review-thread-resolution-plan.json"
                    materialization_path = tmp_path / "review-response-materialization.json"
                    gate_path = tmp_path / "post-write-gate.json"
                    pr_path.write_text(json.dumps(pr), encoding="utf-8")
                    threads_path.write_text(json.dumps(threads), encoding="utf-8")
                    plan_path.write_text(json.dumps(plan), encoding="utf-8")
                    materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
                    gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
                    token = review_thread_resolution_approval_token(
                        plan,
                        approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET,
                    )
                    fake_bin = tmp_path / "bin"
                    fake_bin.mkdir()
                    gh_log = tmp_path / "gh.log"
                    write_fake_review_response_gh(fake_bin, gh_log)
                    env = os.environ.copy()
                    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
                    env["GH_FAKE_LOG"] = str(gh_log)
                    env[env_flag] = "1"
                    env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--root",
                            str(runtime_root),
                            "review-thread-resolution-materialize",
                            "--cwd",
                            str(tmp_path),
                            "--plan-file",
                            str(plan_path),
                            "--pr-json-file",
                            str(pr_path),
                            "--review-threads-file",
                            str(threads_path),
                            "--response-materialization-file",
                            str(materialization_path),
                            "--post-write-gate-file",
                            str(gate_path),
                            "--approval-token",
                            token,
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=env,
                    )

                    self.assertEqual(result.returncode, 1, result.stderr)
                    packet = json.loads(result.stdout)
                    self.assertFalse(packet["valid"])
                    self.assertTrue(packet["github_write_started"])
                    self.assertEqual(packet["review_resolution"], "partial")
                    self.assertEqual(packet["github_writes"][0]["thread_id"], "thread-1")
                    self.assertEqual(packet["github_writes"][0]["status"], "unconfirmed")
                    self.assertNotIn("resolved_review_thread", packet["side_effects"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

                    replay_result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
                    replay = json.loads(replay_result.stdout)
                    self.assertTrue(replay["valid"])
                    self.assertEqual(replay["events_by_type"]["review_thread_resolution_intent"], 1)
                    self.assertEqual(replay["events_by_type"]["review_thread_resolution_result"], 1)

    def test_review_thread_resolution_materialize_failed_mutation_records_result_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            pr, threads, response_materialization, post_write_gate = review_thread_resolution_inputs(tmp, evidence_tmp)
            tmp_path = Path(tmp)
            runtime_root = Path(evidence_tmp) / "runtime"
            plan = evaluate_review_thread_resolution_plan(
                pr=pr,
                review_threads=threads,
                response_materialization=response_materialization,
                post_write_gate=post_write_gate,
                target_thread_ids=["thread-1"],
            )
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-thread-resolution-plan.json"
            materialization_path = tmp_path / "review-response-materialization.json"
            gate_path = tmp_path / "post-write-gate.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            materialization_path.write_text(json.dumps(response_materialization), encoding="utf-8")
            gate_path.write_text(json.dumps(post_write_gate), encoding="utf-8")
            token = review_thread_resolution_approval_token(plan, approval_secret=REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_FAIL_RESOLVE"] = "1"
            env[REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET_ENV] = REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-thread-resolution-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--response-materialization-file",
                    str(materialization_path),
                    "--post-write-gate-file",
                    str(gate_path),
                    "--approval-token",
                    token,
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertTrue(packet["github_write_started"])
            self.assertEqual([trace["label"] for trace in packet["command_trace"]], ["resolve_review_thread"])
            self.assertEqual(packet["github_writes"][0]["status"], "command_failed")
            self.assertIn("audit_intent_record_appended", packet["side_effects"])
            self.assertIn("audit_result_record_appended", packet["side_effects"])
            self.assertNotIn("resolved_review_thread", packet["side_effects"])
            self.assertIn("review_thread_resolution_command_failed", {blocker["code"] for blocker in packet["blockers"]})
            replay_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["review_thread_resolution_result"], 1)

    def test_post_write_gate_accepts_fresh_matching_review_response_evidence(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            threads = review_threads_payload([])
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, threads)

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
                required_body_sections=["Summary", "Testing"],
                max_pr_evidence_age_minutes=30,
                now="2026-06-11T10:20:00Z",
            )

        self.assertTrue(packet["valid"])
        self.assertEqual(packet["schema_version"], "post-write-pr-evidence-gate.v1")
        self.assertEqual(packet["packet"], "post_write_pr_evidence_gate")
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["recommended_next_action"], "ready_for_review")
        self.assertEqual(packet["blockers"], [])
        self.assertEqual(packet["side_effects"], [])
        self.assertTrue(packet["ready_for_review"])
        self.assertFalse(packet["refresh_required"])
        self.assertEqual(packet["materialization"]["type"], "review_response_materialization")
        self.assertEqual(packet["materialization"]["pr_number"], "330")
        self.assertEqual(packet["refresh"]["pr_number"], 330)
        self.assertTrue(packet["refresh"]["pr_json_checksum"].startswith("sha256:"))
        self.assertTrue(packet["pr_readiness"]["ready_to_merge"])
        self.assertEqual(packet["candidate_discovery"]["recommended_next_action"], "select_candidate")
        self.assertEqual(packet["follow_up_candidates"], [])

    def test_post_write_gate_accepts_git_pr_materialization_with_pr_url_number(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, review_threads_payload([]))

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=git_pr_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
                required_body_sections=["Summary", "Testing"],
            )

        self.assertTrue(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "ready_for_review")
        self.assertEqual(packet["materialization"]["type"], "git_pr_materialization")
        self.assertEqual(packet["materialization"]["pr_number"], "330")
        self.assertEqual(packet["refresh"]["head_ref"], "codex/example-branch")
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_accepts_thread_resolution_result_with_resolved_targets(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "isResolved": True,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 120,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Resolved review finding should not produce a follow-up candidate.",
                                    "outdated": False,
                                    "author": {"login": "coderabbitai"},
                                }
                            ],
                        },
                    }
                ]
            )
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, threads)

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_thread_resolution_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
                required_body_sections=["Summary", "Testing"],
            )

        self.assertTrue(packet["valid"])
        self.assertEqual(packet["decision"], "ready")
        self.assertEqual(packet["recommended_next_action"], "ready_for_review")
        self.assertEqual(packet["materialization"]["type"], "review_thread_resolution_materialization")
        self.assertEqual(packet["materialization"]["target_thread_ids"], ["thread-1"])
        self.assertEqual(packet["refresh"]["resolved_target_thread_ids"], ["thread-1"])
        self.assertEqual(packet["refresh"]["unresolved_target_thread_ids"], [])
        self.assertEqual(packet["pr_readiness"]["review_feedback_summary"]["unresolved_actionable_comments"], 0)
        self.assertEqual(packet["follow_up_candidates"], [])
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_blocks_thread_resolution_target_still_unresolved(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 120,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Approved resolution did not actually close this thread.",
                                    "outdated": False,
                                    "author": {"login": "coderabbitai"},
                                }
                            ],
                        },
                    }
                ]
            )
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, threads)

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_thread_resolution_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "operator_review")
        self.assertEqual(packet["refresh"]["unresolved_target_thread_ids"], ["thread-1"])
        self.assertIn(
            "post_write_thread_resolution_target_unresolved",
            {blocker["code"] for blocker in packet["blockers"]},
        )
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_requires_refreshed_thread_resolution_target_ids(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(
                evidence_tmp,
                pr,
                review_threads_payload([]),
            )

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_thread_resolution_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        self.assertEqual(packet["refresh"]["target_thread_ids"], ["thread-1"])
        self.assertEqual(packet["refresh"]["missing_target_thread_ids"], ["thread-1"])
        self.assertIn(
            "post_write_thread_resolution_target_missing",
            {blocker["code"] for blocker in packet["blockers"]},
        )
        self.assertEqual(packet["pr_readiness"], {})
        self.assertEqual(packet["candidate_discovery"], {})
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_requires_github_evidence_sync_after_materialization(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp:
            init_pr_gate_repo(tmp)
            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(base_pr()),
                github_evidence_sync=None,
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        self.assertTrue(packet["refresh_required"])
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"github_evidence_sync_missing"})
        self.assertEqual(packet["pr_readiness"], {})
        self.assertEqual(packet["candidate_discovery"], {})
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_requires_fresh_github_evidence_sync(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(
                evidence_tmp,
                pr,
                review_threads_payload([]),
                captured_at="2026-06-11T10:00:00Z",
            )

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                max_pr_evidence_age_minutes=30,
                now="2026-06-11T11:00:00Z",
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        self.assertTrue(packet["refresh"]["stale"])
        self.assertIn("github_evidence_sync_stale", {blocker["code"] for blocker in packet["blockers"]})
        self.assertEqual(packet["pr_readiness"], {})

    def test_post_write_gate_rejects_evidence_captured_before_materialization(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(
                evidence_tmp,
                pr,
                review_threads_payload([]),
                captured_at="2026-06-11T10:10:00Z",
            )

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(
                    pr,
                    generated_at="2026-06-11T10:20:00Z",
                ),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        self.assertIn("github_evidence_sync_before_materialization", {blocker["code"] for blocker in packet["blockers"]})
        self.assertEqual(packet["pr_readiness"], {})

    def test_post_write_gate_does_not_fall_back_to_process_cwd_for_relative_sync_paths(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        original_cwd = Path.cwd()
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as evidence_tmp,
            tempfile.TemporaryDirectory() as process_tmp,
        ):
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, review_threads_payload([]))
            missing_pr_name = "missing-pr-330.json"
            missing_threads_name = "missing-pr-330-review-threads.json"
            sync_packet["files"]["pr_json"] = missing_pr_name
            sync_packet["files"]["review_threads_json"] = missing_threads_name
            Path(process_tmp, missing_pr_name).write_text(json.dumps(pr), encoding="utf-8")
            Path(process_tmp, missing_threads_name).write_text(json.dumps(review_threads_payload([])), encoding="utf-8")
            os.chdir(process_tmp)
            try:
                packet = evaluate_post_write_pr_evidence_gate(
                    cwd=Path(tmp),
                    materialization_result=review_response_materialization_result(pr),
                    github_evidence_sync=sync_packet,
                    github_evidence_file=sync_path,
                )
            finally:
                os.chdir(original_cwd)

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        self.assertIn("refreshed_pr_evidence_unreadable", {blocker["code"] for blocker in packet["blockers"]})
        self.assertIn("refreshed_review_threads_unreadable", {blocker["code"] for blocker in packet["blockers"]})

    def test_post_write_gate_blocks_changed_pr_head_before_follow_up(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            original_pr = base_pr()
            refreshed_pr = base_pr(headRefOid="def456")
            sync_path, sync_packet = write_github_evidence_sync_files(
                evidence_tmp,
                refreshed_pr,
                review_threads_payload([]),
            )

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(original_pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "refresh_required")
        blockers = packet["blockers"]
        self.assertEqual({blocker["code"] for blocker in blockers}, {"post_write_pr_target_mismatch"})
        self.assertEqual(blockers[0]["field"], "head_sha")
        self.assertEqual(packet["pr_readiness"], {})
        self.assertEqual(packet["candidate_discovery"], {})

    def test_post_write_gate_recommends_review_response_from_refreshed_threads(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 120,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Please address this post-write review finding.",
                                    "outdated": False,
                                    "author": {"login": "coderabbitai"},
                                }
                            ],
                        },
                    }
                ]
            )
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, threads)

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks"],
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual(packet["recommended_next_action"], "respond_to_review")
        self.assertTrue(packet["respond_to_review"])
        self.assertIn("unresolved_review_comment", {blocker["code"] for blocker in packet["blockers"]})
        self.assertEqual(packet["pr_readiness"]["review_feedback_summary"]["unresolved_actionable_comments"], 1)
        self.assertEqual(packet["follow_up_candidates"][0]["source"], "review_finding")
        self.assertEqual(packet["follow_up_candidates"][0]["evidence"]["thread_id"], "thread-1")
        self.assertEqual(packet["side_effects"], [])

    def test_post_write_gate_feeds_failed_checks_back_to_candidate_discovery(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr(
                statusCheckRollup=[
                    check_run("Python and protocol checks", conclusion="FAILURE", workflow="PR Checks"),
                    check_run("Package install", conclusion="SUCCESS", workflow="PR Checks"),
                ]
            )
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, review_threads_payload([]))

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_checks=["Python and protocol checks", "Package install"],
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "follow_up_candidates")
        self.assertIn("check_failed", {blocker["code"] for blocker in packet["blockers"]})
        self.assertEqual(packet["candidate_discovery"]["sources"]["pr_check_failures"], 1)
        self.assertEqual(packet["follow_up_candidates"][0]["source"], "pr_check_failure")
        self.assertEqual(packet["follow_up_candidates"][0]["evidence"]["check"], "Python and protocol checks")

    def test_post_write_gate_routes_pr_body_gaps_to_operator_review(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr(body="## Summary\nMissing testing section.\n")
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, review_threads_payload([]))

            packet = evaluate_post_write_pr_evidence_gate(
                cwd=Path(tmp),
                materialization_result=review_response_materialization_result(pr),
                github_evidence_sync=sync_packet,
                github_evidence_file=sync_path,
                required_body_sections=["Summary", "Testing"],
            )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["recommended_next_action"], "operator_review")
        self.assertTrue(packet["operator_review_required"])
        self.assertIn("required_body_section_missing", {blocker["code"] for blocker in packet["blockers"]})
        self.assertEqual(packet["follow_up_candidates"], [])

    def test_post_write_gate_reraises_unexpected_candidate_discovery_errors(self):
        from codex_cadence.github_evidence import evaluate_post_write_pr_evidence_gate

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as evidence_tmp:
            init_pr_gate_repo(tmp)
            pr = base_pr()
            sync_path, sync_packet = write_github_evidence_sync_files(evidence_tmp, pr, review_threads_payload([]))

            with mock.patch("codex_cadence.candidates.discover_candidates", side_effect=TypeError("programmer bug")):
                with self.assertRaises(TypeError):
                    evaluate_post_write_pr_evidence_gate(
                        cwd=Path(tmp),
                        materialization_result=review_response_materialization_result(pr),
                        github_evidence_sync=sync_packet,
                        github_evidence_file=sync_path,
                    )

    def test_review_response_plan_groups_failed_checks_and_review_threads_with_candidates(self):
        pr = base_pr(
            statusCheckRollup=[
                check_run("Python and protocol checks", conclusion="FAILURE", workflow="PR Checks"),
                check_run("Package install", conclusion="SUCCESS", workflow="PR Checks"),
            ]
        )
        threads = review_threads_payload(
            [
                {
                    "id": "thread-1",
                    "path": "codex_cadence/cli.py",
                    "line": 42,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "comment-1",
                                "path": "codex_cadence/cli.py",
                                "line": 42,
                                "outdated": False,
                                "body": "Handle this current review finding before merge.",
                                "author": {"login": "coderabbitai"},
                            },
                            {
                                "id": "comment-2",
                                "path": "codex_cadence/cli.py",
                                "line": 44,
                                "outdated": False,
                                "body": "No actionable comments.",
                                "author": {"login": "coderabbitai"},
                            },
                        ],
                    },
                },
                {
                    "id": "thread-2",
                    "path": "README.md",
                    "line": 9,
                    "isResolved": True,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "comment-3",
                                "path": "README.md",
                                "line": 9,
                                "outdated": False,
                                "body": "Resolved comment should not be planned.",
                            }
                        ],
                    },
                },
            ]
        )
        candidates = {
            "schema_version": "candidate-discovery.v1",
            "candidates": [
                {
                    "id": "pr-check-failure-001",
                    "source": "pr_check_failure",
                    "title": "Resolve failing PR check: Python and protocol checks",
                    "task_type": "execution",
                    "bucket": "S",
                    "evidence": {"check": "Python and protocol checks", "state": "FAILURE", "workflow": "PR Checks"},
                },
                {
                    "id": "review-finding-001",
                    "source": "review_finding",
                    "title": "Address review finding: Handle this current review finding before merge.",
                    "task_type": "execution",
                    "bucket": "S",
                    "evidence": {
                        "id": "comment-1",
                        "thread_id": "thread-1",
                        "file": "codex_cadence/cli.py",
                        "line": 42,
                    },
                },
            ],
        }

        packet = evaluate_review_response_plan(pr, review_threads=threads, candidate_discovery=candidates)

        self.assertTrue(packet["valid"])
        self.assertTrue(packet["plan_ready"])
        self.assertEqual(packet["schema_version"], "review-response-plan.v1")
        self.assertEqual(packet["recommended_next_action"], "emit_executor_task")
        self.assertEqual(packet["summary"]["failed_checks"], 1)
        self.assertEqual(packet["summary"]["review_threads"], 1)
        self.assertEqual(packet["summary"]["files"], ["codex_cadence/cli.py"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual([item["kind"] for item in packet["plan_items"]], ["failed_check", "review_thread"])
        self.assertEqual(packet["plan_items"][0]["group"]["check"], "Python and protocol checks")
        self.assertEqual(packet["plan_items"][0]["follow_up_task"]["candidate_id"], "pr-check-failure-001")
        self.assertEqual(packet["plan_items"][1]["group"]["thread_id"], "thread-1")
        self.assertEqual(packet["plan_items"][1]["group"]["file"], "codex_cadence/cli.py")
        self.assertEqual(packet["plan_items"][1]["follow_up_task"]["candidate_id"], "review-finding-001")
        self.assertIn("does_not_update_pr_body", packet["limitations"])

    def test_review_response_plan_groups_duplicate_failed_checks(self):
        packet = evaluate_review_response_plan(
            base_pr(
                statusCheckRollup=[
                    check_run("Python and protocol checks", conclusion="FAILURE", workflow="PR Checks"),
                    {
                        **check_run("Python and protocol checks", conclusion="FAILURE", workflow="PR Checks"),
                        "detailsUrl": "https://example.test/checks/python-rerun",
                    },
                ]
            )
        )

        self.assertTrue(packet["valid"])
        self.assertTrue(packet["plan_ready"])
        self.assertEqual(packet["summary"]["failed_checks"], 2)
        self.assertEqual(len(packet["plan_items"]), 1)
        self.assertEqual(packet["plan_items"][0]["kind"], "failed_check")
        self.assertEqual(packet["plan_items"][0]["group"]["check"], "Python and protocol checks")

    def test_review_response_plan_matches_check_candidates_by_state_and_workflow(self):
        pr = base_pr(
            statusCheckRollup=[
                check_run("Shared check", conclusion="FAILURE", workflow="Workflow A"),
                check_run("Shared check", conclusion="FAILURE", workflow="Workflow B"),
            ]
        )
        candidates = {
            "candidates": [
                {
                    "id": "candidate-workflow-b",
                    "source": "pr_check_failure",
                    "title": "Resolve failing PR check: Shared check",
                    "task_type": "execution",
                    "bucket": "S",
                    "evidence": {"check": "Shared check", "state": "FAILURE", "workflow": "Workflow B"},
                },
                {
                    "id": "candidate-workflow-a",
                    "source": "pr_check_failure",
                    "title": "Resolve failing PR check: Shared check",
                    "task_type": "execution",
                    "bucket": "S",
                    "evidence": {"check": "Shared check", "state": "FAILURE", "workflow": "Workflow A"},
                },
            ],
        }

        packet = evaluate_review_response_plan(pr, candidate_discovery=candidates)

        self.assertTrue(packet["valid"])
        self.assertEqual(len(packet["plan_items"]), 2)
        candidates_by_workflow = {
            item["group"]["workflow"]: item["follow_up_task"]["candidate_id"]
            for item in packet["plan_items"]
        }
        self.assertEqual(
            candidates_by_workflow,
            {"Workflow A": "candidate-workflow-a", "Workflow B": "candidate-workflow-b"},
        )

    def test_review_response_plan_recommends_refresh_for_stale_pr_evidence(self):
        packet = evaluate_review_response_plan(
            base_pr(statusCheckRollup=[check_run("Python and protocol checks", conclusion="FAILURE")]),
            evidence_captured_at="2026-06-04T00:00:00Z",
            now="2026-06-04T02:00:00Z",
            max_evidence_age_minutes=30,
        )

        self.assertTrue(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual(packet["evidence"]["source"], "saved_pr_json")
        self.assertEqual(packet["evidence"]["freshness"], "stale")
        self.assertFalse(packet["evidence"]["live"])
        self.assertTrue(packet["evidence"]["stale"])
        self.assertIn("refresh_saved_pr_json_before_merge", packet["evidence"]["limitations"])
        self.assertEqual(packet["plan_items"], [])

    def test_review_response_plan_recommends_wait_for_pending_checks(self):
        packet = evaluate_review_response_plan(
            base_pr(statusCheckRollup=[check_run("Python and protocol checks", status="IN_PROGRESS", conclusion="")])
        )

        self.assertTrue(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "wait_for_checks")
        self.assertEqual(packet["summary"]["pending_checks"], 1)
        self.assertEqual({item["code"] for item in packet["waiting"]}, {"check_pending"})

    def test_review_response_plan_recommends_pr_body_update_for_missing_sections(self):
        packet = evaluate_review_response_plan(
            base_pr(body="## Summary\nReady slice.\n"),
            required_body_sections=["Summary", "Testing"],
        )

        self.assertTrue(packet["valid"])
        self.assertTrue(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "update_pr_body")
        self.assertEqual(packet["plan_items"][0]["kind"], "pr_body")
        self.assertEqual(packet["plan_items"][0]["missing_sections"], ["Testing"])

    def test_review_response_plan_blocks_malformed_review_threads(self):
        packet = evaluate_review_response_plan(
            base_pr(),
            review_threads={"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": "not-list"}}}}},
        )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"review_thread_evidence_invalid"})

    def test_review_response_plan_blocks_review_threads_missing_comment_nodes(self):
        packet = evaluate_review_response_plan(
            base_pr(),
            review_threads=review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "path": "codex_cadence/cli.py",
                        "line": 42,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                ]
            ),
        )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"review_thread_evidence_invalid"})
        self.assertIn("review thread 1 comments.nodes must be a list", packet["blockers"][0]["message"])

    def test_cli_review_response_plan_reads_saved_files_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            candidates_path = tmp_path / "candidates.json"
            pr = base_pr(statusCheckRollup=[check_run("Python and protocol checks", conclusion="FAILURE")])
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "path": "codex_cadence/cli.py",
                        "line": 42,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "path": "codex_cadence/cli.py",
                                    "line": 42,
                                    "outdated": False,
                                    "body": "Handle this current review finding before merge.",
                                }
                            ],
                        },
                    }
                ]
            )
            candidates = {
                "candidates": [
                    {
                        "id": "review-finding-001",
                        "source": "review_finding",
                        "title": "Address review finding",
                        "task_type": "execution",
                        "bucket": "S",
                        "evidence": {"id": "comment-1", "thread_id": "thread-1", "file": "codex_cadence/cli.py", "line": 42},
                    }
                ]
            }
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
            before = {
                pr_path: pr_path.read_text(encoding="utf-8"),
                threads_path: threads_path.read_text(encoding="utf-8"),
                candidates_path: candidates_path.read_text(encoding="utf-8"),
            }

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "review-response-plan",
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--candidate-discovery-file",
                    str(candidates_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet["schema_version"], "review-response-plan.v1")
            self.assertEqual(packet["recommended_next_action"], "emit_executor_task")
            self.assertEqual(packet["side_effects"], [])
            self.assertEqual(packet["summary"]["failed_checks"], 1)
            self.assertEqual(packet["summary"]["review_threads"], 1)
            self.assertEqual({path: path.read_text(encoding="utf-8") for path in before}, before)

    def test_review_response_materialization_plan_binds_exact_body_and_comment_writes(self):
        pr = base_pr(body="## Summary\nReady slice.\n")
        threads = review_threads_payload(
            [
                {
                    "id": "thread-1",
                    "path": "codex_cadence/cli.py",
                    "line": 42,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "comment-1",
                                "path": "codex_cadence/cli.py",
                                "line": 42,
                                "outdated": False,
                                "body": "Please address this current review finding before merge.",
                            }
                        ],
                    },
                }
            ]
        )
        response_plan = evaluate_review_response_plan(
            pr,
            review_threads=threads,
            required_body_sections=["Summary", "Testing"],
            evidence_captured_at="2026-06-11T18:00:00Z",
            now="2026-06-11T18:05:00Z",
            max_evidence_age_minutes=30,
        )
        updated_body = "## Summary\nReady slice.\n\n## Testing\n- unit tests\n"
        comment_body = "Addressed in the latest push; tests now cover this path."
        writes = [
            {
                "kind": "update_pr_body",
                "body": updated_body,
                "body_checksum": checksum_json(updated_body),
            },
            {
                "kind": "post_review_comment",
                "comment_id": "comment-1",
                "body": comment_body,
                "body_checksum": checksum_json(comment_body),
            },
        ]

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            review_threads=threads,
            intended_writes=writes,
            required_body_sections=["Summary", "Testing"],
            evidence_captured_at="2026-06-11T18:00:00Z",
            now="2026-06-11T18:05:00Z",
            max_evidence_age_minutes=30,
        )

        self.assertTrue(packet["valid"])
        self.assertTrue(packet["plan_ready"])
        self.assertEqual(packet["schema_version"], "review-response-materialization-plan.v1")
        self.assertEqual(packet["packet"], "review_response_materialization_plan")
        self.assertEqual(packet["approval_state"], "not_approved")
        self.assertTrue(packet["operator_confirmation_required"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual(packet["target_checksum"], checksum_json(packet["target"]))
        self.assertEqual(packet["target"]["response_plan_checksum"], checksum_json(response_plan))
        self.assertEqual(packet["target"]["pr_number"], "330")
        self.assertEqual(packet["target"]["head_ref"], pr["headRefName"])
        self.assertEqual(packet["target"]["head_sha"], pr["headRefOid"])
        self.assertEqual(packet["target"]["write_kinds"], ["post_review_comment", "update_pr_body"])
        self.assertEqual([write["kind"] for write in packet["write_plan"]], ["post_review_comment", "update_pr_body"])
        self.assertEqual(packet["write_plan"][0]["comment_ids"], ["comment-1"])
        self.assertEqual(packet["write_plan"][0]["body_checksum"], checksum_json(comment_body))
        self.assertEqual(packet["write_plan"][1]["body_checksum"], checksum_json(updated_body))
        self.assertIn("does_not_call_github", packet["limitations"])

    def test_cli_review_response_materialization_plan_reads_saved_files_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            response_plan_path = tmp_path / "review-response-plan.json"
            writes_path = tmp_path / "writes.json"
            pr = base_pr(body="## Summary\nReady slice.\n")
            threads = review_threads_payload(
                [
                    {
                        "id": "thread-1",
                        "path": "codex_cadence/cli.py",
                        "line": 42,
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "path": "codex_cadence/cli.py",
                                    "line": 42,
                                    "outdated": False,
                                    "body": "Please address this current review finding before merge.",
                                }
                            ],
                        },
                    }
                ]
            )
            response_plan = evaluate_review_response_plan(pr, review_threads=threads, required_body_sections=["Summary", "Testing"])
            updated_body = "## Summary\nReady slice.\n\n## Testing\n- unit tests\n"
            writes = {
                "writes": [
                    {
                        "kind": "update_pr_body",
                        "body": updated_body,
                        "body_checksum": checksum_json(updated_body),
                    }
                ]
            }
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            response_plan_path.write_text(json.dumps(response_plan), encoding="utf-8")
            writes_path.write_text(json.dumps(writes), encoding="utf-8")
            before = {
                pr_path: pr_path.read_text(encoding="utf-8"),
                threads_path: threads_path.read_text(encoding="utf-8"),
                response_plan_path: response_plan_path.read_text(encoding="utf-8"),
                writes_path: writes_path.read_text(encoding="utf-8"),
            }

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "review-response-materialization-plan",
                    "--response-plan-file",
                    str(response_plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--write-file",
                    str(writes_path),
                    "--required-body-section",
                    "Summary",
                    "--required-body-section",
                    "Testing",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet["schema_version"], "review-response-materialization-plan.v1")
            self.assertTrue(packet["plan_ready"])
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["target"]["write_kinds"], ["update_pr_body"])
            self.assertEqual(packet["side_effects"], [])
            self.assertEqual({path: path.read_text(encoding="utf-8") for path in before}, before)

    def test_review_response_materialization_plan_blocks_stale_evidence_and_head_drift(self):
        pr = base_pr(body="## Summary\nReady slice.\n")
        response_plan = evaluate_review_response_plan(
            pr,
            required_body_sections=["Summary", "Testing"],
            evidence_captured_at="2026-06-11T18:00:00Z",
            now="2026-06-11T18:05:00Z",
            max_evidence_age_minutes=30,
        )
        updated_body = "## Summary\nReady slice.\n\n## Testing\n- unit tests\n"
        current_pr = dict(pr)
        current_pr["headRefOid"] = "def456"

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=current_pr,
            intended_writes=[
                {
                    "kind": "update_pr_body",
                    "body": updated_body,
                    "body_checksum": checksum_json(updated_body),
                }
            ],
            required_body_sections=["Summary", "Testing"],
            evidence_captured_at="2026-06-11T18:00:00Z",
            now="2026-06-11T19:00:00Z",
            max_evidence_age_minutes=30,
        )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], [])
        self.assertIn("pr_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})
        self.assertIn("review_response_pr_target_mismatch", {blocker["code"] for blocker in packet["blockers"]})
        self.assertIn("review_response_plan_pr_checksum_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_review_response_materialization_plan_blocks_incomplete_threads_and_non_actionable_targets(self):
        pr = base_pr()
        threads = review_threads_payload(
            [
                {
                    "id": "thread-1",
                    "path": "codex_cadence/cli.py",
                    "line": 42,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        "nodes": [
                            {
                                "id": "comment-1",
                                "path": "codex_cadence/cli.py",
                                "line": 42,
                                "outdated": False,
                                "body": "No actionable comments.",
                            }
                        ],
                    },
                }
            ]
        )
        response_plan = evaluate_review_response_plan(pr, review_threads=threads)
        comment_body = "Addressed in the latest push."

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            review_threads=threads,
            intended_writes=[
                {
                    "kind": "post_review_comment",
                    "comment_id": "comment-1",
                    "body": comment_body,
                    "body_checksum": checksum_json(comment_body),
                }
            ],
        )

        self.assertFalse(packet["valid"])
        self.assertEqual(packet["side_effects"], [])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("review_thread_evidence_invalid", blocker_codes)
        self.assertIn("review_response_plan_not_ready", blocker_codes)
        self.assertIn("review_response_comment_target_not_actionable", blocker_codes)

    def test_review_response_materialization_plan_blocks_invalid_write_kinds_checksums_and_body_preflight(self):
        pr = base_pr(body="## Summary\nReady slice.\n")
        response_plan = evaluate_review_response_plan(pr, required_body_sections=["Summary", "Testing"])
        bad_body = "## Summary\nStill missing validation details.\n"

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            intended_writes=[
                {"kind": "resolve_review_thread", "thread_id": "thread-1", "body": "done", "body_checksum": checksum_json("done")},
                {
                    "kind": "update_pr_body",
                    "body": bad_body,
                    "body_checksum": "sha256:" + "0" * 64,
                },
            ],
            required_body_sections=["Summary", "Testing"],
        )

        self.assertFalse(packet["valid"])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        self.assertIn("review_response_write_kind_invalid", blocker_codes)
        self.assertIn("review_response_write_body_checksum_mismatch", blocker_codes)
        self.assertIn("review_response_pr_body_preflight_failed", blocker_codes)
        self.assertNotIn("resolve_review_thread", packet["target"]["write_kinds"])

    def test_review_response_materialization_plan_groups_duplicate_same_target_comment_writes(self):
        pr = base_pr()
        threads = review_threads_payload(
            [
                {
                    "id": "thread-1",
                    "path": "codex_cadence/cli.py",
                    "line": 42,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "comment-1",
                                "path": "codex_cadence/cli.py",
                                "line": 42,
                                "outdated": False,
                                "body": "Please address this current review finding before merge.",
                            }
                        ],
                    },
                }
            ]
        )
        response_plan = evaluate_review_response_plan(pr, review_threads=threads)
        comment_body = "Addressed in the latest push."
        duplicate_write = {
            "kind": "post_review_comment",
            "comment_id": "comment-1",
            "body": comment_body,
            "body_checksum": checksum_json(comment_body),
        }

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            review_threads=threads,
            intended_writes=[duplicate_write, dict(duplicate_write)],
        )

        self.assertTrue(packet["valid"])
        self.assertEqual(len(packet["write_plan"]), 1)
        self.assertEqual(packet["write_plan"][0]["comment_ids"], ["comment-1"])
        self.assertEqual(packet["target"]["writes"], [{"kind": "post_review_comment", "comment_ids": ["comment-1"], "body_checksum": checksum_json(comment_body)}])

    def test_review_response_materialization_plan_blocks_missing_pr_target_anchors(self):
        pr = base_pr()
        for field in ("number", "headRefName", "baseRefName", "headRefOid"):
            pr.pop(field)
        response_plan = evaluate_review_response_plan(pr, required_body_sections=["Summary", "Testing"])
        updated_body = "## Summary\nReady slice.\n\n## Testing\n- unit tests\n"

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            intended_writes=[
                {
                    "kind": "update_pr_body",
                    "body": updated_body,
                    "body_checksum": checksum_json(updated_body),
                }
            ],
            required_body_sections=["Summary", "Testing"],
        )

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["plan_ready"])
        self.assertIn("review_response_pr_target_anchor_missing", {blocker["code"] for blocker in packet["blockers"]})
        self.assertIsNone(packet["target"]["head_sha"])

    def test_review_response_materialization_plan_canonicalizes_duplicate_multi_comment_writes(self):
        pr = base_pr()
        threads = review_threads_payload(
            [
                {
                    "id": "thread-1",
                    "path": "codex_cadence/cli.py",
                    "line": 42,
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "comment-1",
                                "path": "codex_cadence/cli.py",
                                "line": 42,
                                "outdated": False,
                                "body": "Please address this current review finding before merge.",
                            },
                            {
                                "id": "comment-2",
                                "path": "codex_cadence/cli.py",
                                "line": 43,
                                "outdated": False,
                                "body": "Handle this second current review finding too.",
                            },
                        ],
                    },
                }
            ]
        )
        response_plan = evaluate_review_response_plan(pr, review_threads=threads)
        comment_body = "Addressed in the latest push."

        packet = evaluate_review_response_materialization_plan(
            response_plan,
            pr=pr,
            review_threads=threads,
            intended_writes=[
                {
                    "kind": "post_review_comment",
                    "comment_ids": ["comment-1", "comment-2"],
                    "body": comment_body,
                    "body_checksum": checksum_json(comment_body),
                },
                {
                    "kind": "post_review_comment",
                    "comment_ids": ["comment-2", "comment-1"],
                    "body": comment_body,
                    "body_checksum": checksum_json(comment_body),
                },
            ],
        )

        self.assertTrue(packet["valid"])
        self.assertEqual(len(packet["write_plan"]), 1)
        self.assertEqual(packet["write_plan"][0]["comment_ids"], ["comment-1", "comment-2"])
        self.assertEqual(len(packet["target"]["writes"]), 1)

    def test_review_response_materialize_updates_body_posts_review_reply_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runtime_root = tmp_path / "runtime"
            pr, threads, _response_plan, plan, updated_body, comment_body = review_response_materialization_inputs()
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-response-materialization-plan.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            token = review_response_materialization_approval_token(
                plan,
                approval_secret=REVIEW_RESPONSE_APPROVAL_SECRET,
            )
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = REVIEW_RESPONSE_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-response-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--approval-token",
                    token,
                    "--max-pr-json-age-minutes",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            packet = json.loads(result.stdout)
            self.assertEqual(packet["schema_version"], "review-response-materialization.v1")
            self.assertEqual(packet["packet"], "review_response_materialization")
            self.assertTrue(packet["valid"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["approval_state"], "approved")
            self.assertTrue(packet["github_write_started"])
            self.assertEqual(packet["execution_authority"], "operator_approved_review_response_materialization")
            self.assertEqual(packet["plan_checksum"], checksum_json(plan))
            self.assertEqual(packet["target_checksum"], plan["target_checksum"])
            self.assertEqual(
                packet["side_effects"],
                [
                    "audit_intent_record_appended",
                    "updated_pr_body",
                    "posted_review_comment",
                    "audit_result_record_appended",
                ],
            )
            self.assertEqual([trace["label"] for trace in packet["command_trace"]], ["update_pr_body", "post_review_comment"])
            self.assertEqual([write["kind"] for write in packet["github_writes"]], ["update_pr_body", "post_review_comment"])
            self.assertEqual(packet["github_writes"][0]["url"], "https://github.example/local/test/pull/330")
            self.assertEqual(packet["github_writes"][1]["comment_ids"], ["comment-1"])
            self.assertEqual(packet["github_writes"][1]["thread_id"], "thread-1")
            self.assertEqual(packet["github_writes"][1]["github_comment_id"], "reply-1")
            gh_events = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(gh_events[0]["argv"][:3], ["pr", "edit", "330"])
            self.assertEqual(gh_events[0]["body"], updated_body)
            self.assertEqual(gh_events[1]["argv"][:2], ["api", "graphql"])
            self.assertEqual(gh_events[1]["body"], comment_body)

            replay_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["review_response_materialization_intent"], 1)
            self.assertEqual(replay["events_by_type"]["review_response_materialization_result"], 1)

    def test_review_response_materialize_blocks_missing_approval_without_github_or_audit_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runtime_root = tmp_path / "runtime"
            pr, threads, _response_plan, plan, _updated_body, _comment_body = review_response_materialization_inputs()
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-response-materialization-plan.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = REVIEW_RESPONSE_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-response-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertEqual(packet["approval_state"], "not_approved")
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("operator_approval_missing", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_review_response_materialize_rechecks_fresh_pr_threads_and_target_text_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pr, threads, _response_plan, plan, _updated_body, _comment_body = review_response_materialization_inputs()
            changed_pr = dict(pr)
            changed_pr["number"] = 331
            tampered_plan = json.loads(json.dumps(plan))
            tampered_plan["write_plan"][0]["body"] = "tampered body"
            token = review_response_materialization_approval_token(
                tampered_plan,
                approval_secret=REVIEW_RESPONSE_APPROVAL_SECRET,
            )

            packet = materialize_review_response_plan(
                cwd=tmp_path,
                plan_packet=tampered_plan,
                plan_file=tmp_path / "review-response-materialization-plan.json",
                approval_token=token,
                runtime_root=tmp_path / "runtime",
                pr=changed_pr,
                review_threads=threads,
                pr_evidence_captured_at="2026-06-11T18:00:00Z",
                max_pr_evidence_age_minutes=30,
                now="2026-06-11T19:00:00Z",
            )

            self.assertFalse(packet["valid"])
            self.assertFalse(packet["github_write_started"])
            self.assertEqual(packet["side_effects"], [])
            blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
            self.assertIn("pr_evidence_stale", blocker_codes)
            self.assertIn("review_response_pr_target_mismatch", blocker_codes)
            self.assertIn("review_response_write_body_checksum_mismatch", blocker_codes)
            self.assertIn("review_response_materialization_target_checksum_mismatch", blocker_codes)

    def test_review_response_materialize_failed_comment_reports_partial_write_and_result_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runtime_root = tmp_path / "runtime"
            pr, threads, _response_plan, plan, _updated_body, _comment_body = review_response_materialization_inputs()
            pr_path = tmp_path / "pr.json"
            threads_path = tmp_path / "review-threads.json"
            plan_path = tmp_path / "review-response-materialization-plan.json"
            pr_path.write_text(json.dumps(pr), encoding="utf-8")
            threads_path.write_text(json.dumps(threads), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            token = review_response_materialization_approval_token(plan, approval_secret=REVIEW_RESPONSE_APPROVAL_SECRET)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_FAIL_COMMENT"] = "1"
            env[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = REVIEW_RESPONSE_APPROVAL_SECRET

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(runtime_root),
                    "review-response-materialize",
                    "--cwd",
                    str(tmp_path),
                    "--plan-file",
                    str(plan_path),
                    "--pr-json-file",
                    str(pr_path),
                    "--review-threads-file",
                    str(threads_path),
                    "--approval-token",
                    token,
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            packet = json.loads(result.stdout)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertTrue(packet["github_write_started"])
            self.assertIn("updated_pr_body", packet["side_effects"])
            self.assertNotIn("posted_review_comment", packet["side_effects"])
            self.assertIn("audit_result_record_appended", packet["side_effects"])
            self.assertIn("review_response_materialization_command_failed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotEqual(packet["recommended_next_action"], "inspect_resolved_review")
            replay_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(runtime_root), "audit-replay"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            replay = json.loads(replay_result.stdout)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["review_response_materialization_result"], 1)

    def test_review_response_materialize_audit_append_failure_blocks_before_github_writes(self):
        pr, threads, _response_plan, plan, _updated_body, _comment_body = review_response_materialization_inputs()
        token = review_response_materialization_approval_token(plan, approval_secret=REVIEW_RESPONSE_APPROVAL_SECRET)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            review_response_module,
            "append_audit_record",
            side_effect=OSError("audit unavailable"),
        ):
            tmp_path = Path(tmp)
            original_secret = os.environ.get(REVIEW_RESPONSE_APPROVAL_SECRET_ENV)
            os.environ[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = REVIEW_RESPONSE_APPROVAL_SECRET
            try:
                packet = materialize_review_response_plan(
                    cwd=tmp_path,
                    plan_packet=plan,
                    plan_file=tmp_path / "review-response-materialization-plan.json",
                    approval_token=token,
                    runtime_root=tmp_path / "runtime",
                    pr=pr,
                    review_threads=threads,
                    pr_evidence_captured_at="2026-06-11T18:00:00Z",
                    max_pr_evidence_age_minutes=30,
                    now="2026-06-11T18:05:00Z",
                )
            finally:
                if original_secret is None:
                    os.environ.pop(REVIEW_RESPONSE_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(packet["valid"])
        self.assertFalse(packet["github_write_started"])
        self.assertEqual(packet["side_effects"], [])
        self.assertEqual(packet["recommended_next_action"], "repair_audit_materialization")
        self.assertIn("audit_write_failed", {blocker["code"] for blocker in packet["blockers"]})

    def test_review_response_materialize_result_audit_failure_after_partial_write_is_blocking(self):
        pr, threads, _response_plan, plan, _updated_body, _comment_body = review_response_materialization_inputs()
        token = review_response_materialization_approval_token(plan, approval_secret=REVIEW_RESPONSE_APPROVAL_SECRET)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh_log = tmp_path / "gh.log"
            write_fake_review_response_gh(fake_bin, gh_log)
            original_path = os.environ.get("PATH", "")
            original_log = os.environ.get("GH_FAKE_LOG")
            original_fail_comment = os.environ.get("GH_FAKE_FAIL_COMMENT")
            original_secret = os.environ.get(REVIEW_RESPONSE_APPROVAL_SECRET_ENV)
            os.environ["PATH"] = str(fake_bin) + os.pathsep + original_path
            os.environ["GH_FAKE_LOG"] = str(gh_log)
            os.environ["GH_FAKE_FAIL_COMMENT"] = "1"
            os.environ[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = REVIEW_RESPONSE_APPROVAL_SECRET
            try:
                with mock.patch.object(
                    review_response_module,
                    "append_audit_record",
                    side_effect=[{"event": "intent"}, OSError("result audit unavailable")],
                ):
                    packet = materialize_review_response_plan(
                        cwd=tmp_path,
                        plan_packet=plan,
                        plan_file=tmp_path / "review-response-materialization-plan.json",
                        approval_token=token,
                        runtime_root=tmp_path / "runtime",
                        pr=pr,
                        review_threads=threads,
                        pr_evidence_captured_at="2026-06-11T18:00:00Z",
                        max_pr_evidence_age_minutes=30,
                        now="2026-06-11T18:05:00Z",
                    )
            finally:
                os.environ["PATH"] = original_path
                if original_log is None:
                    os.environ.pop("GH_FAKE_LOG", None)
                else:
                    os.environ["GH_FAKE_LOG"] = original_log
                if original_fail_comment is None:
                    os.environ.pop("GH_FAKE_FAIL_COMMENT", None)
                else:
                    os.environ["GH_FAKE_FAIL_COMMENT"] = original_fail_comment
                if original_secret is None:
                    os.environ.pop(REVIEW_RESPONSE_APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[REVIEW_RESPONSE_APPROVAL_SECRET_ENV] = original_secret

        self.assertFalse(packet["valid"])
        self.assertTrue(packet["github_write_started"])
        self.assertEqual(packet["recommended_next_action"], "repair_audit_materialization")
        self.assertIn("updated_pr_body", packet["side_effects"])
        self.assertNotIn("posted_review_comment", packet["side_effects"])
        blocker_codes = {blocker["code"] for blocker in packet["blockers"]}
        warning_codes = {warning["code"] for warning in packet["warnings"]}
        self.assertIn("review_response_materialization_command_failed", blocker_codes)
        self.assertIn("audit_write_failed", blocker_codes)
        self.assertNotIn("audit_write_failed", warning_codes)

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
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
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
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
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
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
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

    def test_malformed_review_threads_block_readiness(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            review_threads={"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": "not-list"}}}}},
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"review_thread_evidence_invalid"})

    def test_incomplete_review_threads_block_readiness(self):
        packet = evaluate_pr_readiness(
            base_pr(),
            required_checks=["Python and protocol checks"],
            review_threads={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                "nodes": [],
                            }
                        }
                    }
                }
            },
        )

        self.assertFalse(packet["ready_to_merge"])
        self.assertEqual(packet["decision"], "blocked")
        self.assertEqual({blocker["code"] for blocker in packet["blockers"]}, {"review_thread_evidence_invalid"})

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
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
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
