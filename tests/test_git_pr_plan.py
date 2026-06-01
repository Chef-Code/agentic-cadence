import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_cadence.executor_contract import DEFAULT_EXECUTOR_STOP_CONDITIONS, build_executor_task_packet


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def init_committed_repo(path):
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (Path(path) / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def write_brake(root, status="DRIVE"):
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / "brake.json").write_text(
        json.dumps(
            {
                "status": status,
                "reason": None,
                "scope": "global",
                "resume_requires": None,
                "updated_at": "2999-05-22T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def runtime_file_snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


def current_head(path):
    return git(path, "rev-parse", "HEAD").stdout.strip()


def current_branch(path):
    return git(path, "branch", "--show-current").stdout.strip()


def commit_plan_changes(path):
    git(path, "switch", "-c", "feature/git-pr-plan")
    code_path = Path(path) / "codex_cadence" / "git_pr_plan.py"
    test_path = Path(path) / "tests" / "test_git_pr_plan.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("print('plan')\n", encoding="utf-8")
    test_path.write_text("print('test plan')\n", encoding="utf-8")
    git(path, "add", "codex_cadence/git_pr_plan.py", "tests/test_git_pr_plan.py")
    git(path, "commit", "-m", "implement git pr plan")


def valid_snapshot(repo_path, **overrides):
    snapshot = {
        "id": "snapshot-1",
        "repo": "local/test",
        "cwd": str(Path(repo_path).resolve()),
        "branch": current_branch(repo_path),
        "head": current_head(repo_path),
        "ci": "green",
        "open_prs": [],
        "active_pr": None,
        "unresolved_review_threads": None,
        "dirty_worktree": False,
        "known_failures": [],
        "repo_confidence": "high",
        "repo_confidence_drivers": [],
        "readiness_evidence": {
            "source": "local_git",
            "freshness": "local_only",
            "live": False,
            "stale": False,
            "limitations": [
                "open_prs_not_fetched",
                "review_threads_not_fetched",
                "ci_status_operator_supplied",
            ],
        },
        "captured_at": "2999-05-22T00:00:00Z",
    }
    snapshot.update(overrides)
    return snapshot


def valid_task_packet(repo_path, evidence_path=None, **snapshot_overrides):
    return build_executor_task_packet(
        task={
            "id": "candidate-1",
            "title": "Implement Git PR plan",
            "summary": "Create a dry-run Git and PR transition plan.",
            "task_type": "execution",
            "bucket": "S",
            "source": "text_marker",
            "drivers": [],
            "evidence": {"path": "docs/roadmap.md"},
        },
        snapshot=valid_snapshot(repo_path, **snapshot_overrides),
        repo_path=repo_path,
        allowed_paths=["codex_cadence", "tests", "README.md"],
        required_checks=["python -m unittest tests.test_git_pr_plan"],
        max_minutes=30,
        max_tasks=1,
        stop_conditions=DEFAULT_EXECUTOR_STOP_CONDITIONS,
        evidence_path=evidence_path or Path(repo_path) / "executor-result.json",
    )


def valid_result(repo_path, **overrides):
    result = {
        "schema_version": "generic-executor-result.v1",
        "packet": "executor_result",
        "task_id": "candidate-1",
        "executor_id": "builder-agent",
        "started_at": "2999-05-22T00:00:00Z",
        "ended_at": "2999-05-22T00:05:00Z",
        "status": "succeeded",
        "files_changed": ["codex_cadence/git_pr_plan.py", "tests/test_git_pr_plan.py"],
        "commands_run": [
            {
                "command": "python -m unittest tests.test_git_pr_plan",
                "exit_code": 0,
            }
        ],
        "validation_results": [
            {
                "name": "git-pr-plan-tests",
                "status": "passed",
                "command": "python -m unittest tests.test_git_pr_plan",
            }
        ],
        "summary": "Dry-run Git/PR planning was implemented.",
        "confidence": "high",
        "blockers": [],
        "dirty_worktree": False,
        "resulting_head": current_head(repo_path),
        "materialized_change_evidence": {
            "status": "verified",
            "source": "executor_result.materialized_change_evidence",
            "task_id": "candidate-1",
            "resulting_head": current_head(repo_path),
            "files": ["codex_cadence/git_pr_plan.py", "tests/test_git_pr_plan.py"],
            "limitations": ["verified_against_result_metadata_not_local_diff"],
        },
    }
    result.update(overrides)
    return result


def write_packets(tmp, repo_path, task_packet=None, result_evidence=None):
    task_path = Path(tmp) / "executor-task.json"
    result_path = Path(tmp) / "executor-result.json"
    task_packet = task_packet or valid_task_packet(repo_path, result_path)
    result_evidence = result_evidence or valid_result(repo_path)
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    result_path.write_text(json.dumps(result_evidence), encoding="utf-8")
    return task_path, result_path, task_packet, result_evidence


def run_git_pr_plan(root, *args, cwd=None, env=None):
    command = [sys.executable, str(SCRIPT), "--root", str(root), "git-pr-plan", *args]
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


class GitPrPlanTests(unittest.TestCase):
    def test_ready_dry_run_packet_requires_operator_review_and_preserves_non_authority(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, task_packet, result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["ready_to_review"])
            self.assertEqual(packet["schema_version"], "git-pr-plan.v1")
            self.assertTrue(packet["dry_run"])
            self.assertEqual(packet["side_effects"], [])
            self.assertTrue(packet["operator_confirmation_required"])
            self.assertEqual(packet["approval_state"], "not_approved")
            self.assertEqual(packet["execution_authority"], "none")
            self.assertEqual(packet["merge_readiness"], "not_evaluated")
            self.assertEqual(packet["recommended_next_action"], "review_git_pr_plan")
            self.assertEqual(packet["task"]["id"], "candidate-1")
            self.assertEqual(packet["evidence_provenance"]["executor_id"], "builder-agent")
            self.assertEqual(packet["evidence_provenance"]["task_file_checksum"], checksum_json(task_packet))
            self.assertEqual(packet["evidence_provenance"]["result_file_checksum"], checksum_json(result_evidence))
            self.assertEqual(packet["materialized_change_evidence"]["status"], "verified")
            self.assertEqual(packet["proposed_branch"], "cadence/candidate-1")
            self.assertEqual(packet["proposed_commit_message"], "Implement Git PR plan")
            self.assertEqual(packet["proposed_pr_title"], "Implement Git PR plan")
            self.assertTrue(packet["pr_body_preflight"]["ready_to_publish"])
            self.assertIn("Dry run only.", packet["proposed_pr_body"])
            self.assertNotIn(str(Path.home()), packet["proposed_pr_body"])
            self.assertTrue(all(example["cadence_executable"] is False for example in packet["command_examples"]))
            commit_example = next(example for example in packet["command_examples"] if example["label"] == "commit_changes")
            pr_example = next(example for example in packet["command_examples"] if example["label"] == "open_pull_request")
            self.assertEqual(commit_example["argv"], ["git", "commit", "-m", "Implement Git PR plan"])
            self.assertEqual(
                pr_example["argv"],
                ["gh", "pr", "create", "--title", "Implement Git PR plan", "--body-file", "proposed-pr-body.md"],
            )
            self.assertEqual(pr_example["body_source"], "packet.proposed_pr_body")

    def test_empty_branch_prefix_generates_slug_without_leading_slash(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--branch-prefix",
                "",
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["ready_to_review"])
            self.assertEqual(packet["proposed_branch"], "candidate-1")
            self.assertNotIn("invalid_generated_branch", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_metadata_only_materialized_change_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertIn("materialized_change_evidence_unverified", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_missing_pr_body_section_contract(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertFalse(packet["pr_body_preflight"]["ready_to_publish"])
            self.assertIn("required_body_section_contract_not_supplied", {blocker["code"] for blocker in packet["blockers"]})

    def test_command_examples_include_argv_for_shell_sensitive_titles(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            title = 'Implement "quoted"; echo unsafe'
            task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
            task_packet["task"]["title"] = title
            result_evidence = valid_result(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(
                tmp,
                repo,
                task_packet=task_packet,
                result_evidence=result_evidence,
            )
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["ready_to_review"])
            commit_example = next(example for example in packet["command_examples"] if example["label"] == "commit_changes")
            pr_example = next(example for example in packet["command_examples"] if example["label"] == "open_pull_request")
            self.assertEqual(commit_example["argv"], ["git", "commit", "-m", title])
            self.assertEqual(pr_example["argv"], ["gh", "pr", "create", "--title", title, "--body-file", "proposed-pr-body.md"])
            self.assertEqual(pr_example["body_source"], "packet.proposed_pr_body")
            self.assertNotIn("--fill", pr_example["argv"])
            self.assertNotIn('git commit -m "Implement "quoted"; echo unsafe"', commit_example["command"])

    def test_blocks_files_changed_without_materialized_change_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            result_evidence = valid_result(repo)
            result_evidence.pop("materialized_change_evidence")
            task_path, result_path, _task_packet, _result_evidence = write_packets(
                tmp,
                repo,
                result_evidence=result_evidence,
            )
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertEqual(packet["recommended_next_action"], "address_blockers")
            self.assertEqual(packet["materialized_change_evidence"]["status"], "absent")
            self.assertIn("materialized_change_evidence_absent", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_invalid_materialized_change_evidence_shapes(self):
        cases = [
            ("status", {"status": "claimed"}),
            ("source", {"source": ""}),
            ("task_id", {"task_id": "other-task"}),
            ("resulting_head", {"resulting_head": "0" * 40}),
            ("files", {"files": []}),
            ("files", {"files": ["README.md"]}),
        ]
        for field, override in cases:
            with self.subTest(field=field, override=override):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    result_evidence = valid_result(repo)
                    result_evidence["materialized_change_evidence"].update(override)
                    task_path, result_path, _task_packet, _result_evidence = write_packets(
                        tmp,
                        repo,
                        result_evidence=result_evidence,
                    )
                    runtime_root = Path(tmp) / "runtime"
                    write_brake(runtime_root)

                    result, packet = run_git_pr_plan(
                        runtime_root,
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--required-body-section",
                        "Summary",
                        "--required-body-section",
                        "Validation",
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(packet["ready_to_review"])
                    self.assertIn("materialized_change_evidence_invalid", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_brake_gated_success_without_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["USERPROFILE"] = str(Path(tmp) / "home")
            env.pop("CODEX_CADENCE_ROOT", None)
            env.pop("CODEX_TRANSMISSION_ROOT", None)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "git-pr-plan",
                    "--cwd",
                    repo,
                    "--task-file",
                    str(task_path),
                    "--result-file",
                    str(result_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            packet = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertEqual(packet["recommended_next_action"], "provide_runtime_root")
            self.assertIn("runtime_root_required", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_dirty_worktree_head_branch_base_collision_and_template_failures(self):
        cases = [
            ("dirty_worktree", lambda repo, tmp, task, result: (Path(repo) / "README.md").write_text("dirty\n", encoding="utf-8")),
            ("head_mismatch", lambda repo, tmp, task, result: result.update({"resulting_head": "0" * 40})),
            ("detached_head", lambda repo, tmp, task, result: git(repo, "switch", "--detach", "HEAD")),
            ("current_branch_mismatch", lambda repo, tmp, task, result: git(repo, "switch", "-c", "other-feature")),
            ("base_branch_missing", lambda repo, tmp, task, result: None),
            ("generated_branch_exists", lambda repo, tmp, task, result: git(repo, "branch", "cadence/candidate-1")),
            (
                "required_body_section_missing",
                lambda repo, tmp, task, result: (Path(tmp) / "pull_request_template.md").write_text("## Summary\n\n## Testing\n", encoding="utf-8"),
            ),
        ]
        for expected_code, mutate in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
                    result_evidence = valid_result(repo)
                    mutate(repo, tmp, task_packet, result_evidence)
                    task_path, result_path, _task_packet, _result_evidence = write_packets(
                        tmp,
                        repo,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                    )
                    args = [
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                    ]
                    if expected_code == "base_branch_missing":
                        args.extend(["--base-branch", "missing-base"])
                    if expected_code == "required_body_section_missing":
                        args.extend(["--pr-template-file", str(Path(tmp) / "pull_request_template.md")])
                    runtime_root = Path(tmp) / "runtime"
                    write_brake(runtime_root)

                    result, packet = run_git_pr_plan(runtime_root, *args)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(packet["ready_to_review"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_untracked_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            (Path(repo) / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertIn("dirty_worktree", {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_result_file_and_repo_path_mismatches(self):
        cases = [
            (
                "result_file_mismatch",
                lambda tmp, repo, task, result: task["expected_output"].update({"evidence_path": str(Path(tmp) / "other-result.json")}),
            ),
            ("repo_path_mismatch", lambda tmp, repo, task, result: task["repo"].update({"path": str(Path(tmp) / "other-repo")})),
        ]
        for expected_code, mutate in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
                    result_evidence = valid_result(repo)
                    mutate(tmp, repo, task_packet, result_evidence)
                    task_path, result_path, _task_packet, _result_evidence = write_packets(
                        tmp,
                        repo,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                    )
                    runtime_root = Path(tmp) / "runtime"
                    write_brake(runtime_root)

                    result, packet = run_git_pr_plan(
                        runtime_root,
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--required-body-section",
                        "Summary",
                        "--required-body-section",
                        "Validation",
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(packet["ready_to_review"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_invalid_packets_non_success_active_brake_and_invalid_branch_names(self):
        cases = [
            ("invalid_task_packet", lambda repo, tmp, task, result: task.update({"schema_version": "wrong"}), []),
            ("invalid_result_evidence", lambda repo, tmp, task, result: result.update({"commands_run": "bad"}), []),
            ("result_not_successful", lambda repo, tmp, task, result: result.update({"status": "failed", "resulting_head": None}), []),
            ("active_brake_stop", lambda repo, tmp, task, result: None, ["PARK"]),
            ("active_brake_stop", lambda repo, tmp, task, result: None, ["NEUTRAL"]),
            ("invalid_base_branch", lambda repo, tmp, task, result: None, ["DRIVE", "--base-branch", "bad branch"]),
            ("invalid_generated_branch", lambda repo, tmp, task, result: None, ["DRIVE", "--branch-prefix", "bad branch"]),
        ]
        for expected_code, mutate, extras in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
                    result_evidence = valid_result(repo)
                    mutate(repo, tmp, task_packet, result_evidence)
                    task_path, result_path, _task_packet, _result_evidence = write_packets(
                        tmp,
                        repo,
                        task_packet=task_packet,
                        result_evidence=result_evidence,
                    )
                    runtime_root = Path(tmp) / "runtime"
                    brake_status = extras[0] if extras and extras[0] in {"DRIVE", "NEUTRAL", "PARK"} else "DRIVE"
                    write_brake(runtime_root, brake_status)
                    args = [
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                    ]
                    if extras and extras[0] in {"DRIVE", "NEUTRAL", "PARK"}:
                        args.extend(extras[1:])
                    else:
                        args.extend(extras)

                    result, packet = run_git_pr_plan(runtime_root, *args)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(packet["ready_to_review"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_missing_or_malformed_runtime_brake_file(self):
        cases = [
            ("runtime_brake_missing", None),
            ("runtime_brake_invalid", "{not-json"),
        ]
        for expected_code, brake_text in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
                    runtime_root = Path(tmp) / "runtime"
                    runtime_root.mkdir()
                    if brake_text is not None:
                        (runtime_root / "brake.json").write_text(brake_text, encoding="utf-8")

                    result, packet = run_git_pr_plan(
                        runtime_root,
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--required-body-section",
                        "Summary",
                        "--required-body-section",
                        "Validation",
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(packet["ready_to_review"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_cli_does_not_call_gh_or_mutate_git_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_gh = fake_bin / ("gh.cmd" if os.name == "nt" else "gh")
            gh_marker = Path(tmp) / "gh-invoked.txt"
            if os.name == "nt":
                fake_gh.write_text("@echo off\r\necho invoked> \"%GH_INVOKED_MARKER%\"\r\nexit /b 99\r\n", encoding="utf-8")
            else:
                fake_gh.write_text("#!/bin/sh\necho invoked > \"$GH_INVOKED_MARKER\"\nexit 99\n", encoding="utf-8")
                fake_gh.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_INVOKED_MARKER"] = str(gh_marker)
            refs_before = git(repo, "show-ref", "--heads").stdout
            status_before = git(repo, "status", "--porcelain").stdout
            head_before = current_head(repo)
            index_mtime_before = (Path(repo) / ".git" / "index").stat().st_mtime_ns
            task_bytes_before = task_path.read_bytes()
            result_bytes_before = result_path.read_bytes()
            task_mtime_before = task_path.stat().st_mtime_ns
            result_mtime_before = result_path.stat().st_mtime_ns
            runtime_entries_before = runtime_file_snapshot(runtime_root)

            result, packet = run_git_pr_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["ready_to_review"])
            self.assertEqual((Path(repo) / ".git" / "index").stat().st_mtime_ns, index_mtime_before)
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertEqual(git(repo, "status", "--porcelain").stdout, status_before)
            self.assertEqual(current_head(repo), head_before)
            self.assertFalse(gh_marker.exists())
            self.assertEqual(task_path.read_bytes(), task_bytes_before)
            self.assertEqual(result_path.read_bytes(), result_bytes_before)
            self.assertEqual(task_path.stat().st_mtime_ns, task_mtime_before)
            self.assertEqual(result_path.stat().st_mtime_ns, result_mtime_before)
            runtime_entries_after = runtime_file_snapshot(runtime_root)
            self.assertEqual(runtime_entries_after, runtime_entries_before)


def checksum_json(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
