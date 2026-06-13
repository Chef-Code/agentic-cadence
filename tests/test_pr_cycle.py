import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_cadence.policy_audit import checksum_json, replay_audit_log


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"
GOOD_CHECKSUM = "sha256:" + "a" * 64


def run_cli(root: Path, *args: str):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def audit_records(root: Path) -> list[dict]:
    audit_path = root / "audit" / "events.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def pr_target(**overrides):
    target = {
        "number": "107",
        "head_ref": "codex/task-36-controlled-pr-cycle",
        "base_ref": "main",
        "head_sha": "abc123",
        "url": "https://github.com/Chef-Code/agentic-cadence/pull/107",
    }
    target.update(overrides)
    return target


def controlled_loop_tick_packet():
    return {
        "protocol_version": "v1",
        "schema_version": "controlled-loop-tick.v1",
        "packet": "controlled_loop_tick",
        "tick_id": "controlled-loop-tick-20260612T100000Z-00000001",
        "source_tick_id": "loop-tick-1",
        "valid": True,
        "controlled_tick_status": "completed",
        "reason": "controlled loop tick evidence is internally consistent",
        "executor_started": True,
        "side_effects": ["controlled_loop_tick_audit_appended"],
        "recommended_next_action": "controlled_tick_complete",
        "operator_confirmation_required": False,
        "task": {"id": "candidate-1", "checksum": GOOD_CHECKSUM, "source": "roadmap"},
        "epoch": {"id": "epoch-1", "closeout_status": "completed", "epoch_status": "COMPLETED"},
        "real_invocation": {
            "invocation_id": "invocation-1",
            "side_effect_mode": "real",
            "closeout_status": "completed",
        },
        "files": {},
        "checksums": {"git_pr_plan": GOOD_CHECKSUM},
        "steps": [],
        "blockers": [],
        "limitations": ["composes_existing_local_evidence_only"],
    }


def git_pr_materialization_packet(target=None):
    target = target or pr_target()
    return {
        "protocol_version": "v1",
        "schema_version": "git-pr-materialization.v1",
        "packet": "git_pr_materialization",
        "generated_at": "2026-06-12T10:05:00Z",
        "valid": True,
        "decision": "materialized",
        "recommended_next_action": "inspect_pull_request",
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": "approved",
        "execution_authority": "operator_approved_git_pr_materialization",
        "merge_readiness": "not_evaluated",
        "plan_file": "git-pr-plan.json",
        "plan_checksum": GOOD_CHECKSUM,
        "repository": {
            "repository_path": "C:/work/repo",
            "current_branch": target["head_ref"],
            "current_head": target["head_sha"],
            "base_branch": target["base_ref"],
        },
        "proposed_branch": target["head_ref"],
        "proposed_pr_title": "Task 36",
        "pr_number": target["number"],
        "pr_url": target["url"],
        "remote": "origin",
        "remote_url": "https://github.com/Chef-Code/agentic-cadence.git",
        "intended_side_effects": ["push_branch", "create_pull_request"],
        "side_effects": ["pushed_branch", "created_pull_request", "audit_result_record_appended"],
        "command_trace": [],
        "blockers": [],
        "warnings": [],
        "limitations": ["operator_approved_git_pr_materialization_only"],
    }


def review_response_materialization_packet(target=None):
    target = target or pr_target()
    return {
        "protocol_version": "v1",
        "schema_version": "review-response-materialization.v1",
        "packet": "review_response_materialization",
        "generated_at": "2026-06-12T10:15:00Z",
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
        "plan_checksum": GOOD_CHECKSUM,
        "target_checksum": "sha256:" + "b" * 64,
        "pr": {
            "number": target["number"],
            "head_ref": target["head_ref"],
            "base_ref": target["base_ref"],
            "head_sha": target["head_sha"],
            "url": target["url"],
        },
        "evidence": {},
        "intended_side_effects": ["post_review_comment"],
        "side_effects": ["posted_review_comment", "audit_result_record_appended"],
        "command_trace": [],
        "github_writes": [{"kind": "post_review_comment", "pr_number": target["number"], "thread_id": "thread-1"}],
        "blockers": [],
        "warnings": [],
        "limitations": ["operator_approved_review_response_materialization_only"],
    }


def review_thread_resolution_materialization_packet(target=None):
    target = target or pr_target()
    return {
        "protocol_version": "v1",
        "schema_version": "review-thread-resolution-materialization.v1",
        "packet": "review_thread_resolution_materialization",
        "generated_at": "2026-06-12T10:25:00Z",
        "valid": True,
        "decision": "materialized",
        "recommended_next_action": "inspect_pull_request",
        "dry_run": False,
        "operator_confirmation_required": True,
        "approval_state": "approved",
        "execution_authority": "operator_approved_review_thread_resolution",
        "github_write_started": True,
        "review_resolution": "resolved",
        "resolution_status": "completed",
        "merge_readiness": "not_evaluated",
        "plan_file": "review-thread-resolution-plan.json",
        "plan_checksum": GOOD_CHECKSUM,
        "target_checksum": "sha256:" + "c" * 64,
        "approval_target": {
            "schema_version": "review-thread-resolution-approval.v1",
            "packet": "review_thread_resolution_approval",
            "target_checksum": "sha256:" + "c" * 64,
            "thread_ids": ["thread-1"],
        },
        "pr": {
            "number": target["number"],
            "head_ref": target["head_ref"],
            "base_ref": target["base_ref"],
            "head_sha": target["head_sha"],
            "url": target["url"],
        },
        "evidence": {},
        "intended_side_effects": ["resolve_review_thread"],
        "side_effects": ["resolved_review_thread", "audit_result_record_appended"],
        "command_trace": [],
        "github_writes": [
            {
                "kind": "resolve_review_thread",
                "pr_number": target["number"],
                "thread_id": "thread-1",
                "is_resolved": True,
                "status": "resolved",
            }
        ],
        "blockers": [],
        "warnings": [],
        "limitations": ["operator_approved_review_thread_resolution_only"],
    }


def post_write_gate_packet(materialization: dict, *, generated_at: str, captured_at: str):
    if materialization["packet"] == "git_pr_materialization":
        target = {
            "number": str(materialization["pr_number"]),
            "head_ref": materialization["proposed_branch"],
            "base_ref": materialization["repository"]["base_branch"],
            "head_sha": materialization["repository"]["current_head"],
            "url": materialization["pr_url"],
        }
        target_checksum = None
    else:
        pr = materialization["pr"]
        target = {
            "number": str(pr["number"]),
            "head_ref": pr["head_ref"],
            "base_ref": pr["base_ref"],
            "head_sha": pr["head_sha"],
            "url": pr["url"],
        }
        target_checksum = materialization.get("target_checksum")
    summary = {
        "type": materialization["packet"],
        "schema_version": materialization["schema_version"],
        "generated_at": materialization["generated_at"],
        "decision": materialization["decision"],
        "approval_state": materialization["approval_state"],
        "plan_checksum": materialization.get("plan_checksum"),
        "target_checksum": target_checksum,
        "result_checksum": checksum_json(materialization),
        "pr_number": target["number"],
        "head_ref": target["head_ref"],
        "base_ref": target["base_ref"],
        "head_sha": target["head_sha"],
        "pr_url": target["url"],
    }
    return {
        "protocol_version": "v1",
        "schema_version": "post-write-pr-evidence-gate.v1",
        "packet": "post_write_pr_evidence_gate",
        "generated_at": generated_at,
        "valid": True,
        "decision": "ready",
        "recommended_next_action": "ready_for_review",
        "refresh_required": False,
        "wait_for_checks": False,
        "respond_to_review": False,
        "ready_for_review": True,
        "operator_review_required": False,
        "materialization": summary,
        "refresh": {
            "source": "github_evidence_sync",
            "summary_file": "github-evidence.json",
            "pr_number": target["number"],
            "captured_at": captured_at,
            "pr_json_file": "pr.json",
            "pr_json_checksum": GOOD_CHECKSUM,
            "review_threads_file": "threads.json",
            "review_threads_checksum": GOOD_CHECKSUM,
            "head_ref": target["head_ref"],
            "base_ref": target["base_ref"],
            "head_sha": target["head_sha"],
        },
        "pr_readiness": {"ready_to_merge": True, "blockers": []},
        "candidate_discovery": {"valid": True, "blockers": []},
        "follow_up_candidates": [],
        "blockers": [],
        "warnings": [],
        "side_effects": [],
        "github_write_started": False,
        "limitations": ["post_write_read_only_gate"],
    }


def full_pr_cycle_chain():
    loop_tick = controlled_loop_tick_packet()
    git_pr = git_pr_materialization_packet()
    initial_gate = post_write_gate_packet(
        git_pr,
        generated_at="2026-06-12T10:11:00Z",
        captured_at="2026-06-12T10:10:00Z",
    )
    review_response = review_response_materialization_packet()
    response_gate = post_write_gate_packet(
        review_response,
        generated_at="2026-06-12T10:21:00Z",
        captured_at="2026-06-12T10:20:00Z",
    )
    thread_resolution = review_thread_resolution_materialization_packet()
    thread_gate = post_write_gate_packet(
        thread_resolution,
        generated_at="2026-06-12T10:31:00Z",
        captured_at="2026-06-12T10:30:00Z",
    )
    return {
        "controlled_loop_tick": loop_tick,
        "git_pr_materialization": git_pr,
        "initial_post_write_gate": initial_gate,
        "review_response_materialization": review_response,
        "review_response_post_write_gate": response_gate,
        "review_thread_resolution_materialization": thread_resolution,
        "review_thread_resolution_post_write_gate": thread_gate,
    }


class PrCycleTests(unittest.TestCase):
    def test_compose_controlled_pr_cycle_accepts_full_chain_and_audits(self):
        from codex_cadence.pr_cycle import compose_controlled_pr_cycle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chain = full_pr_cycle_chain()
            files = {name: f"{name}.json" for name in chain}

            packet = compose_controlled_pr_cycle(root=root, files=files, **chain)

            self.assertTrue(packet["valid"])
            self.assertEqual(packet["schema_version"], "controlled-pr-cycle.v1")
            self.assertEqual(packet["packet"], "controlled_pr_cycle")
            self.assertEqual(packet["controlled_pr_cycle_status"], "completed")
            self.assertEqual(packet["recommended_next_action"], "plan_merge_readiness")
            self.assertEqual(packet["final_recommendation"], "ready_for_merge_planning")
            self.assertFalse(packet["github_write_started"])
            self.assertIn("controlled_pr_cycle_audit_appended", packet["side_effects"])
            self.assertIn("audit_record", packet)
            self.assertEqual([step["status"] for step in packet["steps"]], ["accepted"] * 7)
            self.assertEqual(packet["pr"]["number"], "107")
            self.assertEqual(packet["pr"]["head_sha"], "abc123")
            records = audit_records(root)
            self.assertEqual(records[-1]["event"], "controlled_pr_cycle")
            self.assertEqual(records[-1]["action"], "complete_controlled_pr_cycle")
            self.assertEqual(records[-1]["payload_checksum"], checksum_json({k: v for k, v in packet.items() if k != "audit_record"}))
            replay = replay_audit_log(root)
            self.assertEqual(replay["events_by_type"]["controlled_pr_cycle"], 1)

    def test_compose_controlled_pr_cycle_requires_final_gate_after_thread_resolution_without_audit_append(self):
        from codex_cadence.pr_cycle import compose_controlled_pr_cycle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chain = full_pr_cycle_chain()
            chain.pop("review_thread_resolution_post_write_gate")
            files = {name: f"{name}.json" for name in chain}

            packet = compose_controlled_pr_cycle(root=root, files=files, **chain)

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["controlled_pr_cycle_status"], "blocked")
            self.assertIn("thread_resolution_post_write_gate_missing", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("audit_record", packet)
            self.assertEqual(audit_records(root), [])

    def test_compose_controlled_pr_cycle_blocks_pr_target_drift_without_audit_append(self):
        from codex_cadence.pr_cycle import compose_controlled_pr_cycle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chain = full_pr_cycle_chain()
            drifted = dict(chain["review_response_materialization"])
            drifted["pr"] = {**drifted["pr"], "head_sha": "def456"}
            chain["review_response_materialization"] = drifted
            files = {name: f"{name}.json" for name in chain}

            packet = compose_controlled_pr_cycle(root=root, files=files, **chain)

            self.assertFalse(packet["valid"])
            self.assertIn("pr_cycle_pr_target_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("audit_record", packet)
            self.assertEqual(audit_records(root), [])

    def test_compose_controlled_pr_cycle_blocks_post_write_refresh_target_drift_without_audit_append(self):
        from codex_cadence.pr_cycle import compose_controlled_pr_cycle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chain = full_pr_cycle_chain()
            drifted_gate = dict(chain["review_thread_resolution_post_write_gate"])
            drifted_gate["refresh"] = {**drifted_gate["refresh"], "head_sha": "def456"}
            chain["review_thread_resolution_post_write_gate"] = drifted_gate
            files = {name: f"{name}.json" for name in chain}

            packet = compose_controlled_pr_cycle(root=root, files=files, **chain)

            self.assertFalse(packet["valid"])
            self.assertIn("pr_cycle_pr_target_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("audit_record", packet)
            self.assertEqual(audit_records(root), [])

    def test_compose_controlled_pr_cycle_blocks_steps_with_existing_blockers_without_audit_append(self):
        from codex_cadence.pr_cycle import compose_controlled_pr_cycle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chain = full_pr_cycle_chain()
            blocked_gate = dict(chain["initial_post_write_gate"])
            blocked_gate["blockers"] = [{"code": "check_failed", "message": "required check failed"}]
            chain["initial_post_write_gate"] = blocked_gate
            files = {name: f"{name}.json" for name in chain}

            packet = compose_controlled_pr_cycle(root=root, files=files, **chain)

            self.assertFalse(packet["valid"])
            self.assertIn("pr_cycle_step_blockers_present", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("audit_record", packet)
            self.assertEqual(audit_records(root), [])

    def test_cli_controlled_pr_cycle_reads_saved_packets_without_github_or_git_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            evidence = Path(tmp) / "evidence"
            chain = full_pr_cycle_chain()
            paths = {name: write_json(evidence / f"{name}.json", packet) for name, packet in chain.items()}

            result, output = run_cli(
                root,
                "controlled-pr-cycle",
                "--controlled-loop-tick-file",
                str(paths["controlled_loop_tick"]),
                "--git-pr-materialization-file",
                str(paths["git_pr_materialization"]),
                "--initial-post-write-gate-file",
                str(paths["initial_post_write_gate"]),
                "--review-response-materialization-file",
                str(paths["review_response_materialization"]),
                "--review-response-post-write-gate-file",
                str(paths["review_response_post_write_gate"]),
                "--review-thread-resolution-materialization-file",
                str(paths["review_thread_resolution_materialization"]),
                "--review-thread-resolution-post-write-gate-file",
                str(paths["review_thread_resolution_post_write_gate"]),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["packet"], "controlled_pr_cycle")
            self.assertEqual(output["side_effects"], ["controlled_pr_cycle_audit_appended"])
            self.assertEqual(output["command_trace"], [])
            self.assertFalse(output["github_write_started"])
            self.assertIn("does_not_call_github", output["limitations"])
            self.assertIn("does_not_execute_git_commands", output["limitations"])


if __name__ == "__main__":
    unittest.main()
