import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_cadence.policy_audit import replay_audit_log


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


GOOD_CHECKSUM = "sha256:" + "a" * 64


def run_cli(root: Path, *args: str, cwd: Path | None = None, allow_repo_local_root: bool = False):
    command = [sys.executable, str(SCRIPT), "--root", str(root)]
    if allow_repo_local_root:
        command.append("--allow-repo-local-root")
    command.extend(args)
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_cli_without_root(*args: str, env: dict[str, str], cwd: Path | None = None):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_cadence_cli(root: Path, *args: str):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def init_committed_repo(path: Path) -> None:
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def write_audit_lines(root: Path, lines: list[str]) -> Path:
    audit_path = root / "audit" / "events.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return audit_path


def write_audit_records(root: Path, *records: object) -> Path:
    return write_audit_lines(root, [json.dumps(record, sort_keys=True) for record in records])


def loop_tick_record(**overrides):
    record = {
        "schema_version": "cadence-audit.v1",
        "recorded_at": "2999-05-22T00:00:00Z",
        "event": "loop_tick_decision",
        "tick_id": "loop-tick-1",
        "action": "approve_executor_task",
        "reason": "executor task packet emitted for operator approval",
        "repo": "local/test",
        "branch": "main",
        "head": "abc123",
        "snapshot_id": "snapshot-1",
        "operator_confirmation_required": True,
        "payload_checksum": GOOD_CHECKSUM,
    }
    record.update(overrides)
    return record


def executor_result_record(valid: bool = True, **overrides):
    record = {
        "schema_version": "cadence-audit.v1",
        "recorded_at": "2999-05-22T00:00:00Z",
        "event": "executor_result_validation",
        "action": "record_executor_result" if valid else "fix_executor_evidence",
        "reason": "ok" if valid else "invalid executor task packet",
        "valid": valid,
        "task_file": "C:/tmp/executor-task.json",
        "result_file": "C:/tmp/executor-result.json",
        "payload_checksum": GOOD_CHECKSUM,
        "task_packet_checksum": GOOD_CHECKSUM,
        "result_evidence_checksum": GOOD_CHECKSUM,
    }
    if valid:
        record.update(
            {
                "task_id": "candidate-1",
                "repo": "local/test",
                "branch": "main",
                "head": "abc123",
            }
        )
    record.update(overrides)
    return record


def executor_epoch_closeout_record(valid: bool = True, **overrides):
    record = {
        "schema_version": "cadence-audit.v1",
        "recorded_at": "2999-05-22T00:00:00Z",
        "event": "executor_epoch_closeout",
        "action": "generate_git_pr_plan" if valid else "validate_more_evidence",
        "reason": "executor result succeeded" if valid else "stale task snapshot",
        "valid": valid,
        "epoch_id": "epoch-1",
        "closeout_status": "completed" if valid else "blocked",
        "task_file": "C:/tmp/executor-task.json",
        "result_file": "C:/tmp/executor-result.json",
        "snapshot_after_file": "C:/tmp/snapshot-after.json",
        "payload_checksum": GOOD_CHECKSUM,
        "task_packet_checksum": GOOD_CHECKSUM,
        "result_evidence_checksum": GOOD_CHECKSUM,
        "snapshot_after_checksum": GOOD_CHECKSUM,
    }
    if valid:
        record.update(
            {
                "epoch_status": "COMPLETED",
                "task_id": "candidate-1",
                "repo": "local/test",
                "branch": "main",
                "head": "abc123",
            }
        )
    record.update(overrides)
    return record


def blocker_codes(output: dict) -> list[str]:
    return [blocker["code"] for blocker in output["blockers"]]


class AuditReplayCliTests(unittest.TestCase):
    def test_missing_audit_log_returns_valid_zero_record_packet_without_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cadence"
            audit_path = (root / "audit" / "events.jsonl").resolve(strict=False)

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output,
                {
                    "protocol_version": "v1",
                    "schema_version": "audit-replay.v1",
                    "packet": "audit_replay",
                    "audit_path": str(audit_path),
                    "audit_exists": False,
                    "valid": True,
                    "lines_seen": 0,
                    "records_seen": 0,
                    "records_valid": 0,
                    "records_invalid": 0,
                    "events_by_type": {},
                    "blockers": [],
                    "recommended_next_action": "use_audit_replay_evidence",
                },
            )
            self.assertFalse((root / "audit").exists())
            self.assertFalse(audit_path.exists())

    def test_empty_audit_log_returns_valid_zero_record_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit" / "events.jsonl"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text("", encoding="utf-8")

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["audit_exists"])
            self.assertTrue(output["valid"])
            self.assertEqual(output["lines_seen"], 0)
            self.assertEqual(output["records_seen"], 0)
            self.assertEqual(output["records_valid"], 0)
            self.assertEqual(output["records_invalid"], 0)
            self.assertEqual(output["events_by_type"], {})
            self.assertEqual(output["blockers"], [])

    def test_replays_loop_tick_audit_record_when_repo_argument_is_omitted(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            root = Path(tmp)
            repo_path = Path(repo)
            init_committed_repo(repo_path)

            tick_result, tick_output = run_cadence_cli(
                root,
                "loop-tick",
                "--cwd",
                str(repo_path),
                "--intent",
                "repo_health",
            )
            replay_result, replay_output = run_cli(root, "audit-replay")

            self.assertEqual(tick_result.returncode, 0, tick_result.stderr)
            self.assertEqual(tick_output["snapshot"]["repo"], None)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay_output["valid"])
            self.assertEqual(replay_output["events_by_type"], {"loop_tick_decision": 1})
            record = json.loads((root / "audit" / "events.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["repo"], str(repo_path.resolve()))

    def test_valid_records_are_counted_by_event_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_records(root, loop_tick_record(), executor_result_record(), executor_epoch_closeout_record())

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output["valid"])
            self.assertEqual(output["lines_seen"], 3)
            self.assertEqual(output["records_seen"], 3)
            self.assertEqual(output["records_valid"], 3)
            self.assertEqual(output["records_invalid"], 0)
            self.assertEqual(
                output["events_by_type"],
                {
                    "executor_epoch_closeout": 1,
                    "executor_result_validation": 1,
                    "loop_tick_decision": 1,
                },
            )
            self.assertEqual(output["recommended_next_action"], "use_audit_replay_evidence")

    def test_executor_epoch_closeout_audit_requires_snapshot_after_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = executor_epoch_closeout_record()
            record.pop("snapshot_after_file")
            record.pop("snapshot_after_checksum")
            write_audit_records(root, record)

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["records_valid"], 0)
            self.assertEqual(output["records_invalid"], 1)
            self.assertEqual(blocker_codes(output), ["audit_required_field_missing", "audit_required_field_missing"])

    def test_mixed_valid_blank_and_bad_json_lines_report_counts_and_line_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_lines(root, [json.dumps(loop_tick_record()), "", "{bad json"])

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["lines_seen"], 3)
            self.assertEqual(output["records_seen"], 3)
            self.assertEqual(output["records_valid"], 1)
            self.assertEqual(output["records_invalid"], 2)
            self.assertEqual(output["events_by_type"], {"loop_tick_decision": 1})
            self.assertEqual(blocker_codes(output), ["audit_line_blank", "audit_line_invalid_json"])
            self.assertEqual([blocker["line"] for blocker in output["blockers"]], [2, 3])
            self.assertEqual(output["recommended_next_action"], "inspect_audit_log")

    def test_non_standard_json_constants_are_invalid_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_lines(
                root,
                [
                    (
                        '{"schema_version":"cadence-audit.v1","recorded_at":"2999-05-22T00:00:00Z",'
                        '"event":"loop_tick_decision","tick_id":"loop-tick-1","action":"no_candidates",'
                        '"reason":"no elected candidate","repo":"local/test","branch":"main","head":"abc123",'
                        '"snapshot_id":"snapshot-1","operator_confirmation_required":false,'
                        f'"payload_checksum":"{GOOD_CHECKSUM}","extra":NaN}}'
                    )
                ],
            )

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["records_seen"], 1)
            self.assertEqual(output["records_valid"], 0)
            self.assertEqual(output["records_invalid"], 1)
            self.assertEqual(blocker_codes(output), ["audit_line_invalid_json"])
            self.assertEqual(output["blockers"][0]["line"], 1)

    def test_record_shape_schema_event_and_checksum_blockers_are_stable(self):
        cases = [
            ("array", [], "audit_record_not_object"),
            ("scalar", "bad", "audit_record_not_object"),
            (
                "missing schema",
                {key: value for key, value in loop_tick_record().items() if key != "schema_version"},
                "audit_schema_version_missing",
            ),
            ("schema type", loop_tick_record(schema_version=1), "audit_schema_version_type_invalid"),
            (
                "missing event",
                {key: value for key, value in loop_tick_record().items() if key != "event"},
                "audit_event_missing",
            ),
            ("event type", loop_tick_record(event=1), "audit_event_type_invalid"),
            ("missing required field", loop_tick_record(repo=None), "audit_required_field_missing"),
            ("wrong required field type", loop_tick_record(operator_confirmation_required="true"), "audit_field_type_invalid"),
            (
                "missing required checksum",
                {key: value for key, value in loop_tick_record().items() if key != "payload_checksum"},
                "audit_required_field_missing",
            ),
            ("malformed checksum", loop_tick_record(payload_checksum="sha256:bad"), "audit_checksum_invalid"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, record, code in cases:
                with self.subTest(name=name):
                    root = base / name.replace(" ", "-")
                    write_audit_records(root, record)

                    result, output = run_cli(root, "audit-replay")

                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(output["valid"])
                    self.assertIn(code, blocker_codes(output))
                    self.assertEqual(output["records_seen"], 1)
                    self.assertEqual(output["records_valid"], 0)
                    self.assertEqual(output["records_invalid"], 1)

    def test_malformed_required_checksum_reports_one_blocker_per_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_records(root, loop_tick_record(payload_checksum="sha256:bad"))

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertEqual(blocker_codes(output), ["audit_checksum_invalid"])
            self.assertEqual(output["blockers"][0]["line"], 1)

    def test_unsupported_schema_and_event_recommend_upgrade_only_without_corruption(self):
        unsupported_schema = {"schema_version": "cadence-audit.v2"}
        unsupported_event = loop_tick_record(event="future_event")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_records(root, unsupported_schema, unsupported_event)

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(
                blocker_codes(output),
                ["audit_schema_version_unsupported", "audit_event_unsupported"],
            )
            self.assertEqual(output["recommended_next_action"], "upgrade_cadence")

    def test_mixed_unsupported_and_corrupt_history_recommends_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_audit_records(root, {"schema_version": "cadence-audit.v2"}, loop_tick_record(repo=None))

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["recommended_next_action"], "inspect_audit_log")
            self.assertEqual(
                blocker_codes(output),
                ["audit_schema_version_unsupported", "audit_required_field_missing"],
            )

    def test_executor_result_anchor_rules_depend_on_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid_result_without_anchors = executor_result_record(valid=False)
            successful_result_without_task = executor_result_record(valid=True)
            successful_result_without_task.pop("task_id")
            root = Path(tmp)
            write_audit_records(root, invalid_result_without_anchors, successful_result_without_task)

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["lines_seen"], 2)
            self.assertEqual(output["records_valid"], 1)
            self.assertEqual(output["records_invalid"], 1)
            self.assertEqual(output["events_by_type"], {"executor_result_validation": 1})
            self.assertEqual(blocker_codes(output), ["audit_required_field_missing"])
            self.assertEqual(output["blockers"][0]["line"], 2)

    def test_non_file_audit_path_returns_file_scoped_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "audit" / "events.jsonl").mkdir(parents=True)

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertTrue(output["audit_exists"])
            self.assertFalse(output["valid"])
            self.assertEqual(output["lines_seen"], 0)
            self.assertEqual(output["records_seen"], 0)
            self.assertEqual(blocker_codes(output), ["audit_path_not_file"])
            self.assertNotIn("line", output["blockers"][0])

    def test_undecodable_audit_bytes_return_decode_blocker_without_partial_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit" / "events.jsonl"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_bytes(json.dumps(loop_tick_record()).encode("utf-8") + b"\n\xff\n")

            result, output = run_cli(root, "audit-replay")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output["valid"])
            self.assertEqual(output["lines_seen"], 0)
            self.assertEqual(output["records_seen"], 0)
            self.assertEqual(blocker_codes(output), ["audit_file_decode_failed"])

    def test_unreadable_audit_file_returns_file_scoped_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_path = root / "audit" / "events.jsonl"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text("", encoding="utf-8")

            with mock.patch("codex_cadence.policy_audit.Path.open", side_effect=PermissionError("denied")):
                output = replay_audit_log(root)

            self.assertFalse(output["valid"])
            self.assertEqual(output["lines_seen"], 0)
            self.assertEqual(output["records_seen"], 0)
            self.assertEqual(blocker_codes(output), ["audit_file_unreadable"])
            self.assertEqual(output["blockers"][0]["message"], "audit file could not be read")

    def test_runtime_root_inside_repo_is_rejected_unless_allowed(self):
        with tempfile.TemporaryDirectory() as repo:
            repo_path = Path(repo)
            init_committed_repo(repo_path)
            runtime_root = repo_path / ".cadence-runtime"

            blocked, blocked_output = run_cli(runtime_root, "audit-replay", cwd=repo_path)
            allowed, allowed_output = run_cli(
                runtime_root,
                "audit-replay",
                cwd=repo_path,
                allow_repo_local_root=True,
            )

            self.assertNotEqual(blocked.returncode, 0)
            self.assertIsNone(blocked_output)
            self.assertIn("runtime root is inside target repo but is not ignored", blocked.stderr)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue(allowed_output["valid"])

    def test_without_root_uses_default_runtime_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cadence-env-root"
            env = {
                "PATH": str(Path(sys.executable).parent),
                "SYSTEMROOT": "C:\\Windows",
                "CODEX_CADENCE_ROOT": str(root),
            }

            result, output = run_cli_without_root("audit-replay", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["audit_path"], str((root / "audit" / "events.jsonl").resolve(strict=False)))
            self.assertTrue(output["valid"])


if __name__ == "__main__":
    unittest.main()
