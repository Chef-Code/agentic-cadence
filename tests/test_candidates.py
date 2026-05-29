import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence import candidates as candidates_module
from codex_cadence.candidates import (
    DISCOVERY_INTENTS,
    DISCOVERY_MODES,
    PROPOSAL_ALLOWANCES,
    CandidateBudget,
    candidate_record,
    discover_candidates,
    validate_budget,
)


ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def init_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    Path(path, "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def write_business_memory(root: str | Path, text: str) -> Path:
    memory = Path(root, "docs", "cadence", "business-memory.md")
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(text, encoding="utf-8")
    git(root, "add", "docs/cadence/business-memory.md")
    git(root, "commit", "-m", "add business memory")
    return memory


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


class CandidateModelTests(unittest.TestCase):
    def test_allowed_modes_are_explicit(self):
        self.assertEqual(DISCOVERY_INTENTS, ("merge_readiness", "repo_health", "product_evolution", "hybrid"))
        self.assertEqual(DISCOVERY_MODES, ("off", "local", "expanded"))
        self.assertEqual(PROPOSAL_ALLOWANCES, ("none", "surface", "elect"))

    def test_candidate_record_contains_governance_fields(self):
        candidate = candidate_record(
            candidate_id="known-failure-001",
            title="Resolve failing check: unit tests",
            task_type="execution",
            bucket="S",
            score=85,
            drivers=["ci_verification"],
            uncertainty="low",
            dependency_fan_out="low",
            source="known_failure",
            fingerprint="known-failure:unit-tests",
            risk_surface=["ci"],
            evidence={"failure": "unit tests"},
        )

        self.assertEqual(candidate["id"], "known-failure-001")
        self.assertEqual(candidate["relationships"], {
            "depends_on": [],
            "overlaps": [],
            "supersedes": [],
            "mutually_exclusive_with": [],
            "decomposes_from": None,
        })
        self.assertEqual(candidate["risk_surface"], ["ci"])
        self.assertEqual(candidate["fingerprint"], "known-failure:unit-tests")

    def test_candidate_record_rejects_extra_reserved_core_keys(self):
        with self.assertRaisesRegex(ValueError, "candidate extra cannot override reserved field: task_type"):
            candidate_record(
                candidate_id="known-failure-001",
                title="Resolve failing check: unit tests",
                task_type="execution",
                bucket="S",
                score=85,
                drivers=["ci_verification"],
                uncertainty="low",
                dependency_fan_out="low",
                source="known_failure",
                fingerprint="known-failure:unit-tests",
                risk_surface=["ci"],
                evidence={"failure": "unit tests"},
                extra={"task_type": "invalid"},
            )

    def test_candidate_record_copies_relationships(self):
        relationships = {
            "depends_on": ["candidate-001"],
            "overlaps": [],
            "supersedes": [],
            "mutually_exclusive_with": [],
            "decomposes_from": None,
        }
        candidate = candidate_record(
            candidate_id="known-failure-001",
            title="Resolve failing check: unit tests",
            task_type="execution",
            bucket="S",
            score=85,
            drivers=["ci_verification"],
            uncertainty="low",
            dependency_fan_out="low",
            source="known_failure",
            fingerprint="known-failure:unit-tests",
            risk_surface=["ci"],
            evidence={"failure": "unit tests"},
            relationships=relationships,
        )

        relationships["depends_on"].append("candidate-002")

        self.assertEqual(candidate["relationships"]["depends_on"], ["candidate-001"])

    def test_validate_budget_rejects_negative_values(self):
        with self.assertRaisesRegex(ValueError, "max_candidates must be a non-negative integer"):
            validate_budget(CandidateBudget(max_candidates=-1))

    def test_validate_budget_rejects_boolean_values(self):
        with self.assertRaisesRegex(ValueError, "max_proposals must be a non-negative integer"):
            validate_budget(CandidateBudget(max_proposals=True))


class CandidateDiscoverySourceTests(unittest.TestCase):
    def test_missing_business_memory_file_produces_no_candidate_or_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            self.assertEqual(result["sources"]["business_memory"], 0)
            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["warnings"], [])

    def test_business_memory_entry_creates_discovery_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Dispatch Scheduling Confidence

Pain: Dispatchers do not trust scheduling recommendations until they can trace the operational evidence.
Kind: problem
Workflow: dispatch scheduling
Time Saved: high
Risk: medium

Signals:
- Operators double-check every suggested schedule before committing work.
- Missed confidence cues slow dispatch handoff.

Do Not:
- auto-book jobs without operator confirmation
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution", elect=True)

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            candidate = business_memory_candidates[0]
            self.assertEqual(
                candidate["title"],
                "Ground Dispatch Scheduling Confidence into an executable repo slice",
            )
            self.assertEqual(candidate["task_type"], "discovery")
            self.assertEqual(candidate["maturity"], "discovery")
            self.assertEqual(candidate["classification"], "problem")
            self.assertEqual(candidate["classification_confidence"], "high")
            self.assertEqual(candidate["business_value"], "high")
            self.assertEqual(candidate["workflow"], "dispatch scheduling")
            self.assertEqual(candidate["repo_anchors"], [])
            self.assertEqual(candidate["guardrails"], ["auto-book jobs without operator confirmation"])
            self.assertEqual(candidate["evidence"]["path"], "docs/cadence/business-memory.md")
            self.assertEqual(candidate["evidence"]["heading"], "Dispatch Scheduling Confidence")
            self.assertIn("identify affected dispatch scheduling files", candidate["done_criteria"])
            self.assertEqual(result["sources"]["business_memory"], 1)
            self.assertEqual(result["elected_next"][0]["source"], "business_memory")

    def test_business_memory_budget_prefers_highest_scored_memory_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Toolbar Polish

Kind: nice_to_have
Pain: The toolbar could be slightly more convenient.
Workflow: toolbar
Time Saved: low
Risk: low

## Billing Authority Rule

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                budget=CandidateBudget(max_candidates=1, max_business_memory_candidates=1),
            )

            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(
                result["candidates"][0]["title"],
                "Ground Billing Authority Rule into an executable repo slice",
            )
            self.assertEqual(result["sources"]["business_memory"], 1)

    def test_fulfilled_business_memory_entry_is_not_re_elected(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## PR Readiness Needs A Single Decision Packet

Status: fulfilled
Fulfilled By: PR #10
Kind: risk
Pain: PR readiness needed a deterministic decision packet.
Workflow: PR review and merge readiness
Time Saved: high
Risk: high

## Old Reviewer API Direction

Status: superseded
Superseded By: Reviewer Findings Need Structured Ingestion
Kind: direction
Pain: Earlier reviewer API direction was replaced by structured ingestion.
Workflow: PR review and merge readiness
Time Saved: medium
Risk: medium

## Metadata Only Fulfilled Entry

Fulfilled By: PR #9
Kind: business_rule
Pain: Closure metadata without Status should still retain history without re-election.
Workflow: Cadence self-evolution governance
Time Saved: high
Risk: high

## Metadata Only Superseded Entry

Superseded By: Reviewer Findings Need Structured Ingestion
Kind: direction
Pain: Supersession metadata without Status should still retain history without re-election.
Workflow: PR review and merge readiness
Time Saved: medium
Risk: medium

## Reviewer Findings Need Structured Ingestion

Kind: feature
Pain: Reviewer findings still need structured ingestion from PR threads.
Workflow: PR review and merge readiness
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="hybrid", elect=True)

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertEqual(
                business_memory_candidates[0]["evidence"]["heading"],
                "Reviewer Findings Need Structured Ingestion",
            )
            self.assertEqual(result["sources"]["business_memory"], 1)
            self.assertEqual(
                result["elected_next"][0]["evidence"]["heading"],
                "Reviewer Findings Need Structured Ingestion",
            )

    def test_invalid_business_memory_status_is_not_re_elected(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Typoed Closure Status

Status: done
Kind: risk
Pain: A typoed status should not make this memory eligible.
Workflow: PR review and merge readiness
Time Saved: high
Risk: high

## Blank Closure Status

Status:
Kind: risk
Pain: A blank explicit status should not make this memory eligible.
Workflow: PR review and merge readiness
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="hybrid", elect=True)

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)
            self.assertEqual(result["elected_next"], [])
            self.assertTrue(
                any("unsupported Status: done" in warning for warning in result["warnings"]),
                result["warnings"],
            )
            self.assertTrue(
                any("has empty Status" in warning for warning in result["warnings"]),
                result["warnings"],
            )

    def test_business_memory_fingerprint_keeps_heading_slug_collisions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing: Approval

Kind: business_rule
Pain: Approval routing must stay auditable before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium

## Billing Approval

Kind: problem
Pain: Operators lose approval context when invoice drafts change hands.
Workflow: billing approvals
Time Saved: medium
Risk: medium
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 2)
            self.assertEqual(
                {candidate["evidence"]["heading"] for candidate in business_memory_candidates},
                {"Billing: Approval", "Billing Approval"},
            )
            self.assertEqual(len({candidate["fingerprint"] for candidate in business_memory_candidates}), 2)

    def test_business_memory_parser_accepts_deeper_indented_atx_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

  ### Billing Authority Rule ###

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertEqual(
                business_memory_candidates[0]["title"],
                "Ground Billing Authority Rule into an executable repo slice",
            )
            self.assertEqual(business_memory_candidates[0]["evidence"]["line"], 3)

    def test_business_memory_malformed_field_line_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Checkout Planning Problem

Kind: problem
Pain: Checkout planning repeats context gathering every sprint.
Workflow: checkout planning
Time Saved high
Risk: low
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            self.assertTrue(
                any("malformed business memory line" in warning and "Time Saved high" in warning for warning in result["warnings"]),
                result["warnings"],
            )
            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertEqual(business_memory_candidates[0]["business_value"], "medium")

    def test_business_memory_symlink_is_rejected_without_reading_target(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            init_repo(tmp)
            outside_memory = Path(outside_tmp, "business-memory.md")
            outside_memory.write_text(
                """# Project Business Memory

## External Billing Rule

Kind: business_rule
Pain: This file is outside the repository.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
                encoding="utf-8",
            )
            memory = Path(tmp, "docs", "cadence", "business-memory.md")
            memory.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(outside_memory, memory)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"file symlinks unavailable: {exc}")

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_business_memory_intermediate_symlink_is_rejected_without_reading_target(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            init_repo(tmp)
            outside_memory = Path(outside_tmp, "cadence", "business-memory.md")
            outside_memory.parent.mkdir(parents=True, exist_ok=True)
            outside_memory.write_text(
                """# Project Business Memory

## External Billing Rule

Kind: business_rule
Pain: This file is outside the repository.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
                encoding="utf-8",
            )
            try:
                os.symlink(outside_tmp, Path(tmp, "docs"), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_business_memory_regular_file_is_not_read_from_worktree_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            memory = write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Approval Trace

Kind: business_rule
Pain: Approvers need a clearer billing audit trail before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium
""",
            )
            original_read_text = Path.read_text

            def blocked_read_text(path: Path, *args, **kwargs):
                if path == memory:
                    raise AssertionError("business memory must be read from the checked git blob")
                return original_read_text(path, *args, **kwargs)

            with mock.patch("pathlib.Path.read_text", blocked_read_text):
                result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)

    def test_business_memory_reads_clean_git_index_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Approval Trace

Kind: business_rule
Pain: Approvers need a clearer billing audit trail before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium
""",
            )

            with mock.patch("codex_cadence.candidates.run_git_bytes", wraps=candidates_module.run_git_bytes) as read_blob:
                result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertTrue(
                any(call.args[1:] == ("show", ":docs/cadence/business-memory.md") for call in read_blob.call_args_list),
                read_blob.call_args_list,
            )

    def test_business_memory_rejects_dirty_file_before_reading_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            memory = write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Approval Trace

Kind: business_rule
Pain: Approvers need a clearer billing audit trail before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium
""",
            )
            memory.write_text(memory.read_text(encoding="utf-8") + "\nNotes: local edit\n", encoding="utf-8")

            with mock.patch(
                "codex_cadence.candidates.read_repo_local_text_from_git_index",
                side_effect=AssertionError("dirty business memory must be rejected before blob read"),
            ):
                result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertIn(
                "business memory file docs/cadence/business-memory.md has working tree changes and cannot be read securely on this platform",
                result["warnings"],
            )

    def test_business_memory_rejects_untracked_file_before_reading_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            memory = Path(tmp, "docs", "cadence", "business-memory.md")
            memory.parent.mkdir(parents=True, exist_ok=True)
            memory.write_text(
                """# Project Business Memory

## Billing Approval Trace

Kind: business_rule
Pain: Approvers need a clearer billing audit trail before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium
""",
                encoding="utf-8",
            )

            with mock.patch(
                "codex_cadence.candidates.read_repo_local_text_from_git_index",
                side_effect=AssertionError("untracked business memory must be rejected before blob read"),
            ):
                result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertIn(
                "business memory file docs/cadence/business-memory.md is untracked and cannot be read securely on this platform",
                result["warnings"],
            )

    def test_business_memory_invalid_encoding_warns_without_aborting_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            memory = Path(tmp, "docs", "cadence", "business-memory.md")
            memory.parent.mkdir(parents=True, exist_ok=True)
            memory.write_bytes(b"\xff\xfe\x00\x00")
            git(tmp, "add", "docs/cadence/business-memory.md")
            git(tmp, "commit", "-m", "add invalid business memory")

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)
            self.assertTrue(
                any(
                    warning.startswith("could not decode business memory file docs/cadence/business-memory.md")
                    for warning in result["warnings"]
                ),
                result["warnings"],
            )

    def test_discovery_mode_off_returns_empty_candidates_without_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(cwd=Path(tmp), intent=None, discovery_mode="off")

            self.assertIsNone(result["intent"])
            self.assertEqual(result["discovery_mode"], "off")
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["elected_next"], [])
            self.assertEqual(result["sources"]["review_findings"], 0)
            self.assertEqual(result["sources"]["text_markers"], 0)
            self.assertEqual(result["sources"]["proposals"], 0)
            self.assertIn("discovery disabled by policy", result["warnings"])

    def test_known_failure_creates_merge_readiness_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="merge_readiness",
                known_failures=["unit tests"],
            )

            candidate = result["candidates"][0]
            self.assertEqual(candidate["source"], "known_failure")
            self.assertEqual(candidate["task_type"], "execution")
            self.assertEqual(candidate["fingerprint"], "known-failure:unit tests")
            self.assertIn("ci_verification", candidate["drivers"])

    def test_dirty_file_creates_git_status_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness")

            self.assertTrue(result["sources"]["git_status"])
            self.assertEqual(result["run_signals"]["repo_confidence"], "low")
            git_candidates = [item for item in result["candidates"] if item["source"] == "git_status"]
            self.assertEqual(len(git_candidates), 1)
            self.assertEqual(git_candidates[0]["evidence"]["path"], "README.md")

    def test_dirty_file_fingerprint_keeps_slug_collision_paths_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            nested = Path(tmp, "foo")
            nested.mkdir()
            Path(tmp, "foo", "bar.py").write_text("print('nested')\n", encoding="utf-8")
            Path(tmp, "foo-bar.py").write_text("print('flat')\n", encoding="utf-8")
            git(tmp, "add", "foo/bar.py", "foo-bar.py")
            git(tmp, "commit", "-m", "add collision paths")
            Path(tmp, "foo", "bar.py").write_text("print('nested changed')\n", encoding="utf-8")
            Path(tmp, "foo-bar.py").write_text("print('flat changed')\n", encoding="utf-8")

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness")

            git_candidates = [item for item in result["candidates"] if item["source"] == "git_status"]
            self.assertEqual(len(git_candidates), 2)
            self.assertEqual({item["evidence"]["path"] for item in git_candidates}, {"foo/bar.py", "foo-bar.py"})

    def test_git_status_paths_remain_repo_relative_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            subdir = Path(tmp, "nested")
            subdir.mkdir()
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")

            repo_root = Path(tmp).resolve()

            def fake_run_git(cwd, *args):
                if args == ("rev-parse", "--show-toplevel"):
                    return f"{repo_root}\n"
                if args == ("--no-optional-locks", "status", "--porcelain") and Path(cwd).resolve() == repo_root:
                    return " M README.md\n"
                if args == ("--no-optional-locks", "status", "--porcelain"):
                    return " M ../README.md\n"
                raise AssertionError(f"unexpected git call: {cwd} {args}")

            with mock.patch("codex_cadence.candidates.run_git", fake_run_git):
                result = discover_candidates(cwd=subdir, intent="merge_readiness")

            git_candidates = [item for item in result["candidates"] if item["source"] == "git_status"]
            self.assertEqual(len(git_candidates), 1)
            self.assertEqual(git_candidates[0]["evidence"]["path"], "README.md")
            self.assertNotIn("..", git_candidates[0]["fingerprint"])

    def test_discovery_reports_repo_root_when_started_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            subdir = Path(tmp, "nested")
            subdir.mkdir()

            result = discover_candidates(cwd=subdir, intent="merge_readiness")

            self.assertEqual(result["cwd"], str(Path(tmp).resolve()))

    def test_discovery_does_not_refresh_git_index_after_stat_only_touch(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            repo = Path(tmp)
            index = repo / ".git" / "index"
            readme = repo / "README.md"
            before = file_sha256(index)

            time.sleep(1.1)
            os.utime(readme, None)
            discover_candidates(cwd=repo, intent="merge_readiness")

            self.assertEqual(file_sha256(index), before)


class CandidateDiscoveryBudgetTests(unittest.TestCase):
    def test_review_findings_file_creates_execution_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "scripts/cadence.py", "line": 10, "body": "Handle invalid intent."}
                ]),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_findings_file=findings)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 1)
            self.assertEqual(review_candidates[0]["task_type"], "execution")
            self.assertEqual(
                review_candidates[0]["fingerprint"],
                "review-finding:review-1:scripts/cadence.py:10",
            )
            self.assertEqual(result["sources"]["review_findings"], 1)

    def test_review_threads_file_creates_actionable_review_finding_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
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
                                    "body": "Validate PR thread payloads before creating candidates.",
                                    "author": {"login": "coderabbitai"},
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-2",
                        "isResolved": True,
                        "isOutdated": False,
                        "path": "codex_cadence/candidates.py",
                        "line": 500,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "body": "Already handled and should not become work.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-3",
                        "isResolved": False,
                        "isOutdated": True,
                        "path": "codex_cadence/cli.py",
                        "line": 928,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-3",
                                    "body": "Outdated comments should not become work.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-4",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "docs/protocol.md",
                        "line": 127,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-4",
                                    "body": "<!-- walkthrough_start -->\n## Walkthrough\nNo actionable findings.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads, elect=True)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 1)
            self.assertEqual(review_candidates[0]["task_type"], "execution")
            self.assertEqual(
                review_candidates[0]["fingerprint"],
                "review-finding:comment-1:codex_cadence/candidates.py:448",
            )
            self.assertEqual(review_candidates[0]["evidence"]["file"], "codex_cadence/candidates.py")
            self.assertEqual(review_candidates[0]["evidence"]["line"], 448)
            self.assertEqual(review_candidates[0]["evidence"]["thread_id"], "thread-1")
            self.assertEqual(review_candidates[0]["evidence"]["author"], "coderabbitai")
            self.assertEqual(result["sources"]["review_findings"], 1)
            self.assertEqual(result["elected_next"][0]["source"], "review_finding")

    def test_review_threads_file_rejects_non_repo_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "../outside.py",
                        "line": 1,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Do not allow traversal paths.",
                                    "outdated": False,
                                }
                            ]
                        },
                    }
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(review_candidates, [])
            self.assertIn("review finding 1 file must be repo-relative", result["warnings"])

    def test_review_threads_file_skips_missing_status_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "path": "codex_cadence/candidates.py",
                        "line": 10,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Missing thread outdated status should fail closed.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                    {
                        "id": "thread-2",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/cli.py",
                        "line": 20,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "body": "Missing comment outdated status should fail closed.",
                                }
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(review_candidates, [])
            self.assertIn("review thread 1 missing isResolved or isOutdated status", result["warnings"])
            self.assertIn("review thread 2 comment 1 missing outdated status", result["warnings"])

    def test_review_threads_file_rejects_non_string_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": {"value": "../outside.py"},
                        "line": 10,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Malformed path types should not be coerced.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(review_candidates, [])
            self.assertIn("review finding 1 file must be a string", result["warnings"])

    def test_review_threads_file_keeps_actionable_body_with_status_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/candidates.py",
                        "line": 30,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Handle comments that mention no actionable summaries without dropping real work.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 1)
            self.assertEqual(
                review_candidates[0]["fingerprint"],
                "review-finding:comment-1:codex_cadence/candidates.py:30",
            )

    def test_review_threads_file_creates_candidate_per_actionable_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/candidates.py",
                        "line": 40,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "First actionable issue.",
                                    "outdated": False,
                                },
                                {
                                    "id": "comment-2",
                                    "body": "Second actionable issue.",
                                    "outdated": False,
                                    "line": 41,
                                },
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_threads_file=threads)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 2)
            self.assertEqual(
                {item["fingerprint"] for item in review_candidates},
                {
                    "review-finding:comment-1:codex_cadence/candidates.py:40",
                    "review-finding:comment-2:codex_cadence/candidates.py:41",
                },
            )

    def test_review_threads_file_ids_continue_after_skipped_normalized_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "bad", "line": 1, "body": "Missing file."},
                    {"id": "review-2", "file": "scripts/cadence.py", "line": 2, "body": "Existing finding."},
                ]),
                encoding="utf-8",
            )
            threads = Path(tmp, "review-threads.json")
            threads.write_text(
                json.dumps(review_threads_payload([
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "codex_cadence/candidates.py",
                        "line": 3,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "body": "Thread finding.",
                                    "outdated": False,
                                }
                            ]
                        },
                    },
                ])),
                encoding="utf-8",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="merge_readiness",
                review_findings_file=findings,
                review_threads_file=threads,
            )

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual({item["id"] for item in review_candidates}, {"review-finding-002", "review-finding-003"})

    def test_review_findings_same_id_different_locations_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "scripts/cadence.py", "line": 10, "body": "Handle invalid intent."},
                    {"id": "review-1", "file": "codex_cadence/candidates.py", "line": 232, "body": "Fix dedupe."},
                ]),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_findings_file=findings)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 2)
            self.assertEqual(result["sources"]["review_findings"], 2)
            self.assertEqual({item["evidence"]["file"] for item in review_candidates}, {
                "scripts/cadence.py",
                "codex_cadence/candidates.py",
            })

    def test_review_findings_use_repo_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            scripts = Path(tmp, "scripts")
            scripts.mkdir()
            Path(tmp, "scripts", "cadence.py").write_text("# script\n", encoding="utf-8")
            git(tmp, "add", "scripts/cadence.py")
            git(tmp, "commit", "-m", "add script")
            subdir = Path(tmp, "nested")
            subdir.mkdir()
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "scripts/cadence.py", "line": 10, "body": "Handle invalid intent."}
                ]),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=subdir, intent="merge_readiness", review_findings_file=findings)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(len(review_candidates), 1)
            self.assertEqual(review_candidates[0]["evidence"]["file"], "scripts/cadence.py")
            self.assertEqual(result["cwd"], str(Path(tmp).resolve()))

    def test_review_findings_reject_non_repo_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "../outside.py", "line": 1, "body": "Traversal path."},
                    {"id": "review-2", "file": str(Path(tmp).parent / "outside.py"), "line": 2, "body": "Absolute path."},
                ]),
                encoding="utf-8",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", review_findings_file=findings)

            review_candidates = [item for item in result["candidates"] if item["source"] == "review_finding"]
            self.assertEqual(review_candidates, [])
            self.assertIn("review finding 1 file must be repo-relative", result["warnings"])
            self.assertIn("review finding 2 file must be repo-relative", result["warnings"])

    def test_text_marker_candidates_are_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "notes.py")
            marker_file.write_text("\n".join([f"# TODO marker {index}" for index in range(20)]), encoding="utf-8")
            git(tmp, "add", "notes.py")
            git(tmp, "commit", "-m", "add markers")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="repo_health",
                budget=CandidateBudget(max_text_marker_candidates=3),
            )

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(len(marker_candidates), 3)
            self.assertEqual(result["sources"]["text_markers"], 3)

    def test_text_marker_candidates_are_deterministic_when_walk_order_varies(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "a.py").write_text("# TODO first alphabetically\n", encoding="utf-8")
            Path(tmp, "z.py").write_text("# TODO last alphabetically\n", encoding="utf-8")
            git(tmp, "add", "a.py", "z.py")
            git(tmp, "commit", "-m", "add markers")

            with mock.patch("pathlib.Path.walk", return_value=[(Path(tmp), [], ["z.py", "a.py"])]):
                result = discover_candidates(
                    cwd=Path(tmp),
                    intent="repo_health",
                    budget=CandidateBudget(max_text_marker_candidates=1),
                )

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(len(marker_candidates), 1)
            self.assertEqual(marker_candidates[0]["evidence"]["path"], "a.py")

    def test_text_marker_scanning_uses_repo_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "root_notes.py").write_text("# TODO root marker\n", encoding="utf-8")
            git(tmp, "add", "root_notes.py")
            git(tmp, "commit", "-m", "add root marker")
            subdir = Path(tmp, "nested")
            subdir.mkdir()

            result = discover_candidates(cwd=subdir, intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(len(marker_candidates), 1)
            self.assertEqual(marker_candidates[0]["evidence"]["path"], "root_notes.py")
            self.assertEqual(result["sources"]["text_markers"], 1)

    def test_text_marker_scanning_ignores_python_string_literals(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            tests_dir = Path(tmp, "tests")
            tests_dir.mkdir()
            fixture = tests_dir / "test_fixture.py"
            fixture.write_text('marker_text = "# TODO fixture marker"\n', encoding="utf-8")
            git(tmp, "add", "tests/test_fixture.py")
            git(tmp, "commit", "-m", "add fixture string")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(marker_candidates, [])
            self.assertEqual(result["sources"]["text_markers"], 0)

    def test_text_marker_scanning_ignores_markdown_fenced_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            docs_dir = Path(tmp, "docs")
            docs_dir.mkdir()
            example = docs_dir / "example.md"
            example.write_text("```python\n# TODO example marker\n```\n", encoding="utf-8")
            git(tmp, "add", "docs/example.md")
            git(tmp, "commit", "-m", "add fenced marker example")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(marker_candidates, [])
            self.assertEqual(result["sources"]["text_markers"], 0)

    def test_text_marker_scanning_skips_cache_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cache_dir = Path(tmp, "cache")
            cache_dir.mkdir()
            marker_file = cache_dir / "notes.py"
            marker_file.write_text("# TODO cached marker\n", encoding="utf-8")
            git(tmp, "add", "cache/notes.py")
            git(tmp, "commit", "-m", "add cache marker")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(marker_candidates, [])
            self.assertEqual(result["sources"]["text_markers"], 0)

    def test_text_marker_scanning_skips_dependency_runtime_and_agent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            noisy_paths = [
                Path(tmp, "venv", "Lib", "site-packages", "dependency.py"),
                Path(tmp, ".venv", "Lib", "site-packages", "dependency.py"),
                Path(tmp, "dist", "generated.py"),
                Path(tmp, ".claude", "worktrees", "branch", "notes.py"),
                Path(tmp, ".codex-run", "scratch.py"),
                Path(tmp, ".codex-cadence", "state.py"),
                Path(tmp, ".codex-transmission", "state.py"),
                Path(tmp, ".superpowers", "plans", "plan.py"),
            ]
            for noisy_path in noisy_paths:
                noisy_path.parent.mkdir(parents=True, exist_ok=True)
                noisy_path.write_text("# TODO dependency or runtime marker\n", encoding="utf-8")
            binary_path = Path(tmp, ".claude", "worktrees", "branch", "requirements.txt")
            binary_path.write_bytes(b"\xffTODO hidden binary marker\n")

            source_dir = Path(tmp, "src")
            source_dir.mkdir()
            source_file = source_dir / "app.py"
            source_file.write_text("# TODO real source marker\n", encoding="utf-8")
            git(tmp, "add", ".")
            git(tmp, "commit", "-m", "add noisy and source markers")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual([item["evidence"]["path"] for item in marker_candidates], ["src/app.py"])
            self.assertEqual(result["sources"]["text_markers"], 1)
            self.assertEqual(result["warnings"], [])

    def test_text_marker_scanning_decodes_utf16_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "requirements.txt")
            marker_file.write_text("TODO inspect legacy dependency\n", encoding="utf-16")
            git(tmp, "add", "requirements.txt")
            git(tmp, "commit", "-m", "add utf16 marker")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual([item["evidence"]["path"] for item in marker_candidates], ["requirements.txt"])
            self.assertEqual(result["sources"]["text_markers"], 1)
            self.assertEqual(result["warnings"], [])

    def test_text_marker_scanning_warns_for_utf32_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "requirements.txt")
            marker_file.write_text("TODO inspect legacy dependency\n", encoding="utf-32")
            git(tmp, "add", "requirements.txt")
            git(tmp, "commit", "-m", "add utf32 marker")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(marker_candidates, [])
            self.assertEqual(result["sources"]["text_markers"], 0)
            self.assertEqual(result["warnings"], ["could not scan non-UTF8 file requirements.txt"])

    def test_text_marker_scanning_skips_file_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "notes.md")
            marker_file.write_text("TODO external marker text\n", encoding="utf-8")
            git(tmp, "add", "notes.md")
            git(tmp, "commit", "-m", "add marker")

            target = marker_file.resolve()

            def fake_is_symlink(path):
                return path.resolve() == target

            with mock.patch("pathlib.Path.is_symlink", fake_is_symlink):
                result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            marker_candidates = [item for item in result["candidates"] if item["source"] == "text_marker"]
            self.assertEqual(marker_candidates, [])
            self.assertEqual(result["sources"]["text_markers"], 0)

    def test_duplicate_fingerprints_merge_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="merge_readiness",
                known_failures=["unit tests", "unit tests"],
            )

            known_failure_candidates = [item for item in result["candidates"] if item["source"] == "known_failure"]
            self.assertEqual(len(known_failure_candidates), 1)
            self.assertEqual(known_failure_candidates[0]["evidence"]["occurrences"], 2)

    def test_known_failure_fingerprint_keeps_slug_collisions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="merge_readiness",
                known_failures=["unit/tests", "unit-tests"],
            )

            known_failure_candidates = [item for item in result["candidates"] if item["source"] == "known_failure"]
            self.assertEqual(len(known_failure_candidates), 2)
            self.assertEqual(
                {item["evidence"]["failure"] for item in known_failure_candidates},
                {"unit/tests", "unit-tests"},
            )

    def test_total_budget_keeps_higher_score_review_finding_over_git_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "scripts/cadence.py", "line": 10, "body": "Handle invalid intent."}
                ]),
                encoding="utf-8",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="merge_readiness",
                review_findings_file=findings,
                budget=CandidateBudget(max_candidates=1),
            )

            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(result["candidates"][0]["source"], "review_finding")

    def test_doc_marker_candidates_obey_doc_marker_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            docs_dir = Path(tmp, "docs")
            docs_dir.mkdir()
            marker_file = docs_dir / "notes.md"
            marker_file.write_text("\n".join([f"TODO docs marker {index}" for index in range(4)]), encoding="utf-8")
            git(tmp, "add", "docs/notes.md")
            git(tmp, "commit", "-m", "add docs markers")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="repo_health",
                budget=CandidateBudget(max_doc_marker_candidates=1, max_text_marker_candidates=10),
            )

            doc_markers = [
                item
                for item in result["candidates"]
                if item["source"] == "text_marker" and item["evidence"]["path"].startswith("docs/")
            ]
            self.assertEqual(len(doc_markers), 1)


class CandidateDiscoveryGovernanceTests(unittest.TestCase):
    def test_proposal_surface_outputs_non_executable_discovery_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                proposal_allowance="surface",
            )

            proposals = [item for item in result["candidates"] if item["source"] == "agent_proposal"]
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]["task_type"], "discovery")
            self.assertFalse(proposals[0]["executable"])
            self.assertEqual(result["elected_next"], [])

    def test_proposal_elect_allows_proposal_into_election_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                proposal_allowance="elect",
                elect=True,
            )

            self.assertEqual(result["elected_next"][0]["source"], "agent_proposal")
            self.assertEqual(result["elected_next"][0]["task_type"], "discovery")

    def test_hybrid_does_not_elect_product_proposal_when_merge_blocker_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                proposal_allowance="elect",
                known_failures=["unit tests"],
                elect=True,
            )

            self.assertEqual(result["elected_next"][0]["source"], "known_failure")
            self.assertEqual(result["run_signals"]["intent_drift"], "blocked")

    def test_hybrid_does_not_elect_product_proposal_when_dirty_worktree_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                proposal_allowance="elect",
                elect=True,
                max_tasks=2,
            )

            self.assertEqual([candidate["source"] for candidate in result["elected_next"]], ["git_status"])
            self.assertEqual(result["run_signals"]["intent_drift"], "blocked")

    def test_hybrid_keeps_repo_health_markers_when_dirty_worktree_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            Path(tmp, "notes.py").write_text("# TODO: tighten repo health scan\n", encoding="utf-8")
            git(tmp, "add", "notes.py")
            git(tmp, "commit", "-m", "add marker")
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                proposal_allowance="elect",
                elect=True,
                max_tasks=2,
            )

            self.assertEqual([candidate["source"] for candidate in result["elected_next"]], ["git_status", "text_marker"])
            self.assertEqual(result["run_signals"]["intent_drift"], "blocked")

    def test_hybrid_caps_business_memory_product_evolution_election_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Authority Rule

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high

## Checkout Planning Problem

Kind: problem
Pain: Checkout planning repeats context gathering every sprint.
Workflow: checkout planning
Time Saved: high
Risk: high

## Onboarding Feature Direction

Kind: feature
Pain: Onboarding needs a guided first-run setup path.
Workflow: onboarding setup
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="hybrid", elect=True, max_tasks=3)

            self.assertEqual(result["sources"]["business_memory"], 3)
            self.assertEqual([candidate["source"] for candidate in result["elected_next"]], ["business_memory"])

    def test_hybrid_product_evolution_budget_zero_suppresses_business_memory_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Authority Rule

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                elect=True,
                budget=CandidateBudget(max_product_evolution_candidates_in_hybrid=0),
            )

            self.assertEqual(result["sources"]["business_memory"], 1)
            self.assertEqual(result["elected_next"], [])

    def test_hybrid_does_not_elect_business_memory_when_dirty_worktree_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Authority Rule

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
            )
            Path(tmp, "README.md").write_text("changed\n", encoding="utf-8")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                elect=True,
                max_tasks=2,
            )

            self.assertEqual([candidate["source"] for candidate in result["elected_next"]], ["git_status"])
            self.assertEqual(result["run_signals"]["intent_drift"], "blocked")

    def test_hybrid_does_not_elect_business_memory_when_review_finding_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Authority Rule

Kind: business_rule
Pain: Billing changes must preserve approval authority before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: high
""",
            )
            findings = Path(tmp, "findings.json")
            findings.write_text(
                json.dumps([
                    {"id": "review-1", "file": "codex_cadence/candidates.py", "line": 10, "body": "Fix blocker."}
                ]),
                encoding="utf-8",
            )
            git(tmp, "add", "findings.json")
            git(tmp, "commit", "-m", "add review findings")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                review_findings_file=findings,
                elect=True,
                max_tasks=2,
            )

            self.assertEqual([candidate["source"] for candidate in result["elected_next"]], ["review_finding"])
            self.assertEqual(result["run_signals"]["intent_drift"], "blocked")

    def test_product_evolution_proposal_only_outputs_high_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                proposal_allowance="elect",
                elect=True,
            )

            proposals = [item for item in result["candidates"] if item["source"] == "agent_proposal"]
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]["uncertainty"], "high")
            self.assertEqual(result["elected_next"][0]["uncertainty"], "high")
            self.assertEqual(result["run_signals"]["uncertainty"], "high")

    def test_product_evolution_budget_prefers_business_memory_over_surface_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Checkout Planning Pain

Kind: problem
Pain: Checkout teams lose planning context across handoffs.
Workflow: checkout planning
Time Saved: high
Risk: medium
""",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                proposal_allowance="surface",
                budget=CandidateBudget(max_candidates=1),
            )

            self.assertEqual([candidate["source"] for candidate in result["candidates"]], ["business_memory"])
            self.assertEqual(result["sources"]["business_memory"], 1)
            self.assertEqual([candidate for candidate in result["candidates"] if candidate["source"] == "agent_proposal"], [])

    def test_business_memory_budget_caps_raw_run_signal_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            entries = ["# Project Business Memory", ""]
            for index in range(12):
                entries.extend(
                    [
                        f"## Billing Rule {index}",
                        "",
                        "Kind: business_rule",
                        "Pain: Billing changes must preserve approval authority before invoices are sent.",
                        "Workflow: billing approvals",
                        "Time Saved: high",
                        "Risk: high",
                        "",
                    ]
                )
            write_business_memory(tmp, "\n".join(entries))

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                budget=CandidateBudget(max_business_memory_candidates=1),
            )

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertEqual(result["sources"]["business_memory"], 1)
            self.assertEqual(result["run_signals"]["candidate_growth"], "low")

    def test_repo_health_intent_prioritizes_text_marker_over_known_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "notes.py")
            marker_file.write_text("# TODO remove stale maintenance marker\n", encoding="utf-8")
            git(tmp, "add", "notes.py")
            git(tmp, "commit", "-m", "add marker")

            result = discover_candidates(
                cwd=Path(tmp),
                intent="repo_health",
                known_failures=["unit tests"],
                elect=True,
            )

            self.assertEqual(result["elected_next"][0]["source"], "text_marker")

    def test_repo_health_does_not_surface_business_memory_with_defaulted_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Customer Welcome Flow

Pain: Customers repeat onboarding details across handoff screens.
Workflow: customer onboarding
Time Saved: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_repo_health_does_not_surface_unrelated_feature_with_explicit_medium_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Onboarding Preference

Kind: feature
Pain: New users want a warmer welcome flow.
Workflow: onboarding
Time Saved: medium
Risk: medium
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_repo_health_does_not_surface_unrelated_explicit_risk_without_safety_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Onboarding Color Risk

Kind: risk
Pain: The onboarding color choice may confuse first-time readers.
Workflow: onboarding
Time Saved: medium
Risk: medium
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_repo_health_surfaces_business_memory_with_safety_workflow_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Billing Approval Trace

Kind: feature
Pain: Approvers need a clearer billing audit trail before invoices are sent.
Workflow: billing approvals
Time Saved: high
Risk: medium
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            self.assertEqual(business_memory_candidates[0]["workflow"], "billing approvals")

    def test_repo_health_workflow_safety_terms_do_not_match_inside_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Symbol Cleanup

Pain: Labels need clearer naming in the interface.
Workflow: symbol cleanup
Time Saved: high
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)

    def test_merge_readiness_skips_business_memory_without_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Empty Signal
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)
            self.assertEqual(result["warnings"], [])

    def test_zero_business_memory_budget_skips_business_memory_without_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Empty Signal
""",
            )

            result = discover_candidates(
                cwd=Path(tmp),
                intent="product_evolution",
                budget=CandidateBudget(max_business_memory_candidates=0),
            )

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(business_memory_candidates, [])
            self.assertEqual(result["sources"]["business_memory"], 0)
            self.assertEqual(result["warnings"], [])

    def test_explicit_unknown_business_memory_kind_is_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Checkout Context Gap

Kind: unknown
Pain: Teams are unsure where checkout context belongs.
Workflow: checkout planning
Time Saved: high
Risk: low
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            candidate = business_memory_candidates[0]
            self.assertEqual(candidate["classification"], "unknown")
            self.assertEqual(candidate["classification_confidence"], "low")
            self.assertIn("unclassified_signal", candidate["drivers"])

    def test_missing_workflow_business_memory_is_unknown_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Checkout Context Gap

Kind: problem
Pain: Teams manually rebuild checkout context across handoffs.
Time Saved: high
Risk: medium
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            candidate = business_memory_candidates[0]
            self.assertEqual(candidate["classification"], "unknown")
            self.assertEqual(candidate["classification_confidence"], "low")
            self.assertEqual(candidate["workflow"], "unknown")
            self.assertIn("unclassified_signal", candidate["drivers"])
            self.assertIn(
                "business memory entry 'Checkout Context Gap' missing Workflow; preserving as unclassified signal",
                result["warnings"],
            )

    def test_repo_business_memory_current_entries_are_closed_and_parse_without_warnings(self):
        source_text = (ROOT / "docs" / "cadence" / "business-memory.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(tmp, source_text)

            result = discover_candidates(cwd=Path(tmp), intent="hybrid", elect=True)

            self.assertEqual(result["warnings"], [])
            self.assertEqual(result["sources"]["business_memory"], 0)
            self.assertFalse(any(candidate["source"] == "business_memory" for candidate in result["candidates"]))

    def test_business_memory_classification_terms_do_not_match_inside_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            write_business_memory(
                tmp,
                """# Project Business Memory

## Asterisk Marker Display

Pain: Teams need a clearer marker display.
Workflow: asterisk handling
Time Saved: high
Risk: low
""",
            )

            result = discover_candidates(cwd=Path(tmp), intent="product_evolution")

            business_memory_candidates = [item for item in result["candidates"] if item["source"] == "business_memory"]
            self.assertEqual(len(business_memory_candidates), 1)
            candidate = business_memory_candidates[0]
            self.assertEqual(candidate["classification"], "unknown")
            self.assertEqual(candidate["classification_confidence"], "low")
            self.assertIn("unclassified_signal", candidate["drivers"])

    def test_medium_candidate_uncertainty_raises_run_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            marker_file = Path(tmp, "notes.py")
            marker_file.write_text("# TODO inspect this uncertainty source\n", encoding="utf-8")
            git(tmp, "add", "notes.py")
            git(tmp, "commit", "-m", "add marker")

            result = discover_candidates(cwd=Path(tmp), intent="repo_health")

            self.assertEqual(result["run_signals"]["uncertainty"], "medium")

    def test_known_failures_lower_repo_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", known_failures=["unit tests"])

            self.assertEqual(result["run_signals"]["repo_confidence"], "low")

    def test_candidate_growth_raises_run_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            failures = [f"check {index}" for index in range(12)]

            result = discover_candidates(cwd=Path(tmp), intent="merge_readiness", known_failures=failures)

            self.assertEqual(result["run_signals"]["candidate_growth"], "medium")
            self.assertEqual(result["run_signals"]["uncertainty"], "medium")

    def test_hybrid_product_evolution_budget_zero_suppresses_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)

            result = discover_candidates(
                cwd=Path(tmp),
                intent="hybrid",
                proposal_allowance="elect",
                budget=CandidateBudget(max_product_evolution_candidates_in_hybrid=0),
            )

            proposals = [item for item in result["candidates"] if item["source"] == "agent_proposal"]
            self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()
