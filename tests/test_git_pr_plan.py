import json
import hashlib
import hmac
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_cadence import git_pr_plan as git_pr_plan_module
from codex_cadence.executor_contract import DEFAULT_EXECUTOR_STOP_CONDITIONS, build_executor_task_packet
from codex_cadence.executor_invocation import (
    DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION,
    _dirty_worktree_fingerprint,
    _local_dirty_files,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cadence.py"
APPROVAL_SECRET_ENV = "CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET"
APPROVAL_SECRET = "unit-test-materialization-approval-secret"


def git(cwd, *args, check=True):
    """Run a Git command in a test repository."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def init_committed_repo(path):
    """Create a minimal committed Git repository for tests."""
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (Path(path) / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")


def write_brake(root, status="DRIVE"):
    """Write a runtime brake file with the requested status."""
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
    """Return runtime file contents keyed by relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


def current_head(path):
    """Return the current commit for a test repository."""
    return git(path, "rev-parse", "HEAD").stdout.strip()


def current_branch(path):
    """Return the current branch for a test repository."""
    return git(path, "branch", "--show-current").stdout.strip()


def commit_plan_changes(path):
    """Commit representative git-pr-plan implementation files."""
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
    """Build a valid repository snapshot for executor task packets."""
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
    """Build a valid executor task packet for git-pr-plan tests."""
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
    """Build valid executor result evidence for git-pr-plan tests."""
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
    """Write task and result packets to temporary JSON files."""
    task_path = Path(tmp) / "executor-task.json"
    result_path = Path(tmp) / "executor-result.json"
    task_packet = task_packet or valid_task_packet(repo_path, result_path)
    result_evidence = result_evidence or valid_result(repo_path)
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    result_path.write_text(json.dumps(result_evidence), encoding="utf-8")
    return task_path, result_path, task_packet, result_evidence


def run_git_pr_plan(root, *args, cwd=None, env=None):
    """Run the git-pr-plan CLI and parse its JSON output."""
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


def run_git_pr_materialize(root, *args, cwd=None, env=None, approval_secret=APPROVAL_SECRET):
    """Run the git-pr-materialize CLI and parse its JSON output."""
    command = [sys.executable, str(SCRIPT), "--root", str(root), "git-pr-materialize", *args]
    command_env = os.environ.copy() if env is None else env.copy()
    if approval_secret is None:
        command_env.pop(APPROVAL_SECRET_ENV, None)
    else:
        command_env[APPROVAL_SECRET_ENV] = approval_secret
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=command_env,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_git_pr_dirty_materialization_plan(root, *args, cwd=None, env=None):
    """Run the dirty-worktree Git/PR materialization plan CLI."""
    command = [sys.executable, str(SCRIPT), "--root", str(root), "git-pr-dirty-materialization-plan", *args]
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


def run_git_pr_dirty_commit_materialize(root, *args, cwd=None, env=None, approval_secret=APPROVAL_SECRET):
    """Run the local dirty-worktree commit materializer CLI."""
    command = [sys.executable, str(SCRIPT), "--root", str(root), "git-pr-dirty-commit-materialize", *args]
    command_env = os.environ.copy() if env is None else env.copy()
    if approval_secret is None:
        command_env.pop(APPROVAL_SECRET_ENV, None)
    else:
        command_env[APPROVAL_SECRET_ENV] = approval_secret
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=command_env,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def run_audit_replay(root, cwd=None):
    """Run audit replay and parse its JSON output."""
    command = [sys.executable, str(SCRIPT), "--root", str(root), "audit-replay"]
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return result, output


def git_pr_materialization_approval_token(packet, *, remote="origin", remote_url=None, pr_number=None, approval_secret=APPROVAL_SECRET):
    """Return the operator approval token expected for a materialization packet."""
    approval = {
        "schema_version": "git-pr-materialization-approval.v1",
        "packet": "git_pr_materialization_approval",
        "plan_checksum": checksum_json(packet),
        "remote": remote,
        "remote_url": remote_url,
        "pr_number": str(pr_number) if pr_number is not None else None,
        "operation": "update_pull_request" if pr_number is not None else "create_pull_request",
    }
    payload = json.dumps(approval, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(approval_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return "approve-git-pr:hmac-sha256:" + digest


def git_pr_dirty_commit_materialization_approval_token(packet, *, approval_secret=APPROVAL_SECRET):
    """Return the operator approval token expected for a dirty local commit plan."""
    proposed_commit = packet.get("proposed_commit") if isinstance(packet.get("proposed_commit"), dict) else {}
    approval = {
        "schema_version": "git-pr-dirty-commit-materialization-approval.v1",
        "packet": "git_pr_dirty_commit_materialization_approval",
        "plan_checksum": checksum_json(packet),
        "target_checksum": packet.get("target_checksum"),
        "proposed_branch": packet.get("proposed_branch"),
        "source_head": proposed_commit.get("source_head"),
        "operation": "dirty_commit_materialization",
    }
    payload = json.dumps(approval, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(approval_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return "approve-git-pr:hmac-sha256:" + digest


def remote_push_url(repo, remote="origin"):
    """Return the configured push URL for a test remote."""
    return git(repo, "remote", "get-url", "--push", remote).stdout.strip()


def write_ready_plan(root, repo, tmp):
    """Write a ready git-pr-plan packet for materialization tests."""
    task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
    result, packet = run_git_pr_plan(
        root,
        "--cwd",
        repo,
        "--task-file",
        str(task_path),
        "--result-file",
        str(result_path),
        "--branch-prefix",
        "cadence/",
        "--required-body-section",
        "Summary",
        "--required-body-section",
        "Validation",
        cwd=repo,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    plan_path = Path(tmp) / "git-pr-plan.json"
    plan_path.write_text(json.dumps(packet), encoding="utf-8")
    return plan_path, packet


def write_materialization_pr_json(path, plan, **overrides):
    """Write a minimal saved PR JSON object for materialization freshness tests."""
    pr = {
        "number": 42,
        "title": plan["proposed_pr_title"],
        "state": "OPEN",
        "isDraft": False,
        "headRefName": plan["proposed_branch"],
        "baseRefName": plan["repository"]["base_branch"],
        "headRefOid": plan["repository"]["current_head"],
        "body": plan["proposed_pr_body"],
        "statusCheckRollup": [],
    }
    pr.update(overrides)
    Path(path).write_text(json.dumps(pr), encoding="utf-8")
    return pr


def write_dirty_materialization_inputs(tmp, repo):
    """Write task/result/invocation inputs for a closeout-approved dirty worktree."""
    git(repo, "switch", "-c", "feature/dirty-materialization")
    code_path = Path(repo) / "codex_cadence" / "git_pr_plan.py"
    test_path = Path(repo) / "tests" / "test_git_pr_plan.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("print('dirty plan')\n", encoding="utf-8")
    test_path.write_text("print('dirty test plan')\n", encoding="utf-8")

    result_path = Path(tmp) / "executor-result.json"
    task_packet = valid_task_packet(repo, result_path)
    task_path = Path(tmp) / "executor-task.json"
    files_changed = ["codex_cadence/git_pr_plan.py", "tests/test_git_pr_plan.py"]
    result_evidence = valid_result(
        repo,
        dirty_worktree=True,
        files_changed=files_changed,
        materialized_change_evidence={
            "status": "verified",
            "source": "real_executor_invocation.local_diff",
            "task_id": "candidate-1",
            "resulting_head": current_head(repo),
            "files": files_changed,
            "limitations": ["verified_against_local_worktree_status"],
        },
    )
    dirty_files, dirty_blocker = _local_dirty_files(Path(repo))
    if dirty_blocker is not None:
        raise AssertionError(dirty_blocker)
    fingerprint, fingerprint_blocker = _dirty_worktree_fingerprint(Path(repo), dirty_files)
    if fingerprint_blocker is not None:
        raise AssertionError(fingerprint_blocker)
    invocation_materialized = dict(result_evidence["materialized_change_evidence"])
    invocation_materialized["worktree_fingerprint_schema_version"] = DIRTY_WORKTREE_FINGERPRINT_SCHEMA_VERSION
    invocation_materialized["worktree_fingerprint_checksum"] = checksum_json(fingerprint)
    task_path.write_text(json.dumps(task_packet), encoding="utf-8")
    result_path.write_text(json.dumps(result_evidence), encoding="utf-8")
    invocation = {
        "protocol_version": "v1",
        "schema_version": "real-executor-invocation.v1",
        "packet": "real_executor_invocation",
        "valid": True,
        "executor_started": True,
        "timed_out": False,
        "invocation_id": "invocation-1",
        "side_effect_mode": "materialized_changes",
        "result_file": str(result_path),
        "result_evidence_checksum": checksum_json(result_evidence),
        "closeout_status": "completed",
        "epoch_id": "epoch-1",
        "epoch_status": "COMPLETED",
        "epoch_closeout_checksum": "sha256:" + "c" * 64,
        "task_file": str(task_path),
        "repository_after": {
            "cwd": str(Path(repo).resolve()),
            "branch": current_branch(repo),
            "head": current_head(repo),
            "dirty_worktree": True,
        },
        "materialized_change_evidence": invocation_materialized,
        "blockers": [],
    }
    invocation_path = Path(tmp) / "real-invocation.json"
    invocation["record_file"] = str(invocation_path)
    closeout_path = Path(tmp) / "executor-closeout.json"
    closeout = {
        "protocol_version": "v1",
        "schema_version": "executor-epoch-closeout.v1",
        "packet": "executor_epoch_closeout",
        "valid": True,
        "reason": "task completed",
        "epoch_id": "epoch-1",
        "epoch_status": "COMPLETED",
        "closeout_status": "completed",
        "blockers": [],
        "task_file": str(task_path),
        "task_checksum": checksum_json(task_packet),
        "result_file": str(result_path),
        "snapshot_after_file": str(Path(tmp) / "snapshot-after.json"),
        "snapshot_after_checksum": "sha256:" + "b" * 64,
        "executor_result_status": "succeeded",
        "executor_started": True,
        "pr_action_started": False,
        "operator_confirmation_required": True,
        "validation": {
            "valid": True,
            "invocation_id": "invocation-1",
        },
        "next_decision": {
            "decision": "generate_git_pr_plan",
            "recommended_next_action": "approve_dirty_git_pr_materialization",
        },
        "git_pr_plan": None,
        "side_effects": [],
        "limitations": ["unit_test_closeout_packet"],
    }
    closeout_checksum = checksum_json(closeout)
    invocation["epoch_closeout_checksum"] = closeout_checksum
    invocation_path.write_text(json.dumps(invocation), encoding="utf-8")
    closeout["real_invocation"] = {
        "path": str(invocation_path),
        "invocation_id": "invocation-1",
        "before_checksum": "sha256:" + "a" * 64,
        "closeout_status": "pending",
        "after_checksum": checksum_json(invocation),
        "epoch_closeout_checksum": closeout_checksum,
    }
    closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
    return task_path, result_path, invocation_path, closeout_path, task_packet, result_evidence, invocation, closeout, fingerprint


def add_origin_remote(tmp, repo):
    """Add a local bare origin remote for materialization push tests."""
    remote = Path(tmp) / "origin.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    return remote


def write_fake_gh_materializer(fake_bin, log_path, pr_url):
    """Write a fake gh executable that records PR create/edit calls."""
    script = Path(fake_bin) / "fake_gh.py"
    script.write_text(
        """
import json
import os
import sys

with open(os.environ["GH_FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    if os.environ.get("GH_FAKE_FAIL_VIEW"):
        print("view failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({
        "number": int(args[2]),
        "headRefName": os.environ.get("GH_FAKE_HEAD_REF", ""),
        "baseRefName": os.environ.get("GH_FAKE_BASE_REF", "main"),
        "headRefOid": os.environ.get("GH_FAKE_HEAD_OID", ""),
    }))
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    if os.environ.get("GH_FAKE_FAIL_CREATE"):
        print("create failed", file=sys.stderr)
        sys.exit(1)
    print(os.environ["GH_FAKE_PR_URL"])
    sys.exit(0)
if args[:2] == ["pr", "edit"]:
    if os.environ.get("GH_FAKE_FAIL_EDIT"):
        print("edit failed", file=sys.stderr)
        sys.exit(1)
    print(os.environ["GH_FAKE_PR_URL"])
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


def write_forbidden_git_hooks(repo):
    """Install hooks that would call gh if materialization failed to disable hooks."""
    hooks_dir = Path(repo) / ".git" / "hooks"
    script = "#!/bin/sh\ngh pr create --title hook-ran --body hook-ran >/dev/null 2>&1 || true\n"
    for hook_name in ("post-checkout", "prepare-commit-msg", "commit-msg", "post-commit"):
        hook_path = hooks_dir / hook_name
        hook_path.write_text(script, encoding="utf-8")
        if os.name != "nt":
            hook_path.chmod(0o755)


def write_ready_dirty_materialization_plan(runtime_root, tmp, repo):
    """Write a ready dirty-materialization plan packet and return its path."""
    add_origin_remote(tmp, repo)
    task_path, result_path, invocation_path, closeout_path, task_packet, result_evidence, invocation, closeout, fingerprint = (
        write_dirty_materialization_inputs(tmp, repo)
    )
    expected_base_head = git(repo, "rev-parse", "main").stdout.strip()
    result, packet = run_git_pr_dirty_materialization_plan(
        runtime_root,
        "--cwd",
        repo,
        "--task-file",
        str(task_path),
        "--result-file",
        str(result_path),
        "--real-invocation-file",
        str(invocation_path),
        "--closeout-file",
        str(closeout_path),
        "--expected-base-head",
        expected_base_head,
        "--required-body-section",
        "Summary",
        "--required-body-section",
        "Validation",
        cwd=repo,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or json.dumps(packet, indent=2))
    plan_path = Path(tmp) / "git-pr-dirty-materialization-plan.json"
    plan_path.write_text(json.dumps(packet), encoding="utf-8")
    return plan_path, packet, {
        "task_path": task_path,
        "result_path": result_path,
        "invocation_path": invocation_path,
        "closeout_path": closeout_path,
        "task_packet": task_packet,
        "result_evidence": result_evidence,
        "invocation": invocation,
        "closeout": closeout,
        "fingerprint": fingerprint,
    }


class GitPrPlanTests(unittest.TestCase):
    def test_dirty_materialization_plan_binds_closeout_approved_dirty_worktree_without_writes(self):
        """A matching dirty fingerprint yields a reviewed commit/PR materialization input."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            add_origin_remote(tmp, repo)
            task_path, result_path, invocation_path, closeout_path, task_packet, result_evidence, invocation, _closeout, fingerprint = (
                write_dirty_materialization_inputs(tmp, repo)
            )
            runtime_root = Path(tmp) / "runtime"
            before_status = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
            before_head = current_head(repo)
            expected_base_head = git(repo, "rev-parse", "main").stdout.strip()

            result, packet = run_git_pr_dirty_materialization_plan(
                runtime_root,
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--real-invocation-file",
                str(invocation_path),
                "--closeout-file",
                str(closeout_path),
                "--expected-base-head",
                expected_base_head,
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["valid"])
            self.assertTrue(packet["ready_to_review"])
            self.assertEqual(packet["schema_version"], "git-pr-dirty-materialization-plan.v1")
            self.assertEqual(packet["packet"], "git_pr_dirty_materialization_plan")
            self.assertTrue(packet["dry_run"])
            self.assertEqual(packet["side_effects"], [])
            self.assertTrue(packet["operator_confirmation_required"])
            self.assertEqual(packet["approval_state"], "not_approved")
            self.assertEqual(packet["execution_authority"], "none")
            self.assertEqual(packet["recommended_next_action"], "approve_dirty_git_pr_materialization")
            self.assertEqual(packet["target_checksum"], checksum_json(packet["target"]))
            self.assertEqual(packet["target"]["operation"], "dirty_worktree_git_pr_materialization")
            self.assertEqual(packet["target"]["real_invocation_checksum"], checksum_json(invocation))
            self.assertEqual(packet["target"]["task_file_checksum"], checksum_json(task_packet))
            self.assertEqual(packet["target"]["result_file_checksum"], checksum_json(result_evidence))
            self.assertEqual(packet["proposed_commit"]["message"], "Implement Git PR plan")
            self.assertEqual(packet["proposed_commit"]["files"], result_evidence["materialized_change_evidence"]["files"])
            self.assertEqual(packet["proposed_commit"]["base_head"], expected_base_head)
            self.assertEqual(packet["proposed_commit"]["source_head"], before_head)
            self.assertEqual(packet["dirty_worktree_fingerprint"], fingerprint)
            self.assertEqual(
                packet["materialized_change_evidence"]["worktree_fingerprint_checksum"],
                checksum_json(fingerprint),
            )
            self.assertTrue(packet["pr_body_preflight"]["ready_to_publish"])
            self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all").stdout, before_status)
            self.assertEqual(current_head(repo), before_head)
            self.assertFalse((runtime_root / "audit").exists())

    def test_dirty_materialization_plan_blocks_dirty_fingerprint_tampering(self):
        """Dirty content edits after invocation invalidate the fingerprint before planning."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, invocation_path, closeout_path, *_rest = write_dirty_materialization_inputs(tmp, repo)
            (Path(repo) / "codex_cadence" / "git_pr_plan.py").write_text("tampered\n", encoding="utf-8")

            result, packet = run_git_pr_dirty_materialization_plan(
                Path(tmp) / "runtime",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--real-invocation-file",
                str(invocation_path),
                "--closeout-file",
                str(closeout_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(packet["valid"])
            self.assertIn("dirty_worktree_fingerprint_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_extra_dirty_files(self):
        """The current dirty file set must exactly match materialized evidence."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, invocation_path, closeout_path, *_rest = write_dirty_materialization_inputs(tmp, repo)
            (Path(repo) / "extra.txt").write_text("extra\n", encoding="utf-8")

            result, packet = run_git_pr_dirty_materialization_plan(
                Path(tmp) / "runtime",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--real-invocation-file",
                str(invocation_path),
                "--closeout-file",
                str(closeout_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(packet["valid"])
            self.assertIn("materialized_change_files_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_malformed_closeout_checksum(self):
        """Closeout-approved real invocation evidence needs a checksum-shaped closeout anchor."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, invocation_path, closeout_path, _task, _result, invocation, _closeout, _fingerprint = (
                write_dirty_materialization_inputs(tmp, repo)
            )
            invocation["epoch_closeout_checksum"] = "not-a-checksum"
            invocation_path.write_text(json.dumps(invocation), encoding="utf-8")

            result, packet = run_git_pr_dirty_materialization_plan(
                Path(tmp) / "runtime",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--real-invocation-file",
                str(invocation_path),
                "--closeout-file",
                str(closeout_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(packet["valid"])
            self.assertIn("real_invocation_not_closeout_approved", {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_closeout_invocation_binding_mismatch(self):
        """The closeout packet must bind the supplied real-invocation record."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            task_path, result_path, invocation_path, closeout_path, _task, _result, _invocation, closeout, _fingerprint = (
                write_dirty_materialization_inputs(tmp, repo)
            )
            closeout["real_invocation"]["after_checksum"] = "sha256:" + "d" * 64
            closeout_path.write_text(json.dumps(closeout), encoding="utf-8")

            result, packet = run_git_pr_dirty_materialization_plan(
                Path(tmp) / "runtime",
                "--cwd",
                repo,
                "--task-file",
                str(task_path),
                "--result-file",
                str(result_path),
                "--real-invocation-file",
                str(invocation_path),
                "--closeout-file",
                str(closeout_path),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
                cwd=repo,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(packet["valid"])
            self.assertIn("closeout_invocation_mismatch", {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_real_invocation_contract_drift(self):
        """Real-invocation mode, repo head, and fingerprint-schema anchors are rechecked."""
        cases = [
            (
                "side-effect-mode",
                lambda invocation: invocation.__setitem__("side_effect_mode", "evidence_only"),
                "real_invocation_not_materialized",
            ),
            (
                "repository-head",
                lambda invocation: invocation["repository_after"].__setitem__("head", "0" * 40),
                "repository_head_mismatch",
            ),
            (
                "fingerprint-schema",
                lambda invocation: invocation["materialized_change_evidence"].pop(
                    "worktree_fingerprint_schema_version",
                    None,
                ),
                "dirty_worktree_fingerprint_missing",
            ),
        ]
        for name, mutate, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    task_path, result_path, invocation_path, closeout_path, _task, _result, invocation, closeout, _fingerprint = (
                        write_dirty_materialization_inputs(tmp, repo)
                    )
                    mutate(invocation)
                    invocation_path.write_text(json.dumps(invocation), encoding="utf-8")
                    closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
                    closeout_path.write_text(json.dumps(closeout), encoding="utf-8")

                    result, packet = run_git_pr_dirty_materialization_plan(
                        Path(tmp) / "runtime",
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--real-invocation-file",
                        str(invocation_path),
                        "--closeout-file",
                        str(closeout_path),
                        "--required-body-section",
                        "Summary",
                        "--required-body-section",
                        "Validation",
                        cwd=repo,
                    )

                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(packet["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_branch_and_base_drift(self):
        """Branch and supplied base-head anchors are rechecked before review readiness."""
        cases = [
            (
                "branch",
                lambda repo, invocation: invocation["repository_after"].__setitem__("branch", "stale-branch"),
                [],
                "repository_branch_mismatch",
            ),
            (
                "base",
                lambda repo, invocation: None,
                ["--expected-base-head", "0" * 40],
                "base_head_mismatch",
            ),
        ]
        for name, mutate, extra_args, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    task_path, result_path, invocation_path, closeout_path, _task, _result, invocation, closeout, _fingerprint = (
                        write_dirty_materialization_inputs(tmp, repo)
                    )
                    mutate(repo, invocation)
                    invocation_path.write_text(json.dumps(invocation), encoding="utf-8")
                    closeout["real_invocation"]["after_checksum"] = checksum_json(invocation)
                    closeout_path.write_text(json.dumps(closeout), encoding="utf-8")

                    result, packet = run_git_pr_dirty_materialization_plan(
                        Path(tmp) / "runtime",
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--real-invocation-file",
                        str(invocation_path),
                        "--closeout-file",
                        str(closeout_path),
                        *extra_args,
                        "--required-body-section",
                        "Summary",
                        "--required-body-section",
                        "Validation",
                        cwd=repo,
                    )

                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(packet["valid"])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_dirty_materialization_plan_blocks_pr_body_and_branch_policy_failures(self):
        """PR-body preflight and branch policy remain blocking dry-run gates."""
        cases = [
            (
                "body",
                lambda tmp: [],
                ["--required-body-section", "Missing Section"],
                "required_body_section_missing",
            ),
            (
                "branch-policy",
                lambda tmp: [
                    "--policy-file",
                    str(
                        Path(tmp).joinpath("policy.json")
                    ),
                ],
                ["--required-body-section", "Summary", "--required-body-section", "Validation"],
                "branch_policy_base_branch_disallowed",
            ),
        ]
        for name, policy_args, body_args, expected_code in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    task_path, result_path, invocation_path, closeout_path, *_rest = write_dirty_materialization_inputs(tmp, repo)
                    if name == "branch-policy":
                        Path(tmp, "policy.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": "cadence-loop-policy.v1",
                                    "branch_policy": {"allowed_base_branches": ["release"]},
                                }
                            ),
                            encoding="utf-8",
                        )

                    result, packet = run_git_pr_dirty_materialization_plan(
                        Path(tmp) / "runtime",
                        "--cwd",
                        repo,
                        "--task-file",
                        str(task_path),
                        "--result-file",
                        str(result_path),
                        "--real-invocation-file",
                        str(invocation_path),
                        "--closeout-file",
                        str(closeout_path),
                        *policy_args(tmp),
                        *body_args,
                        cwd=repo,
                    )

                    self.assertEqual(result.returncode, 1)
                self.assertFalse(packet["valid"])
                self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_git_pr_dirty_commit_materialize_commits_exact_dirty_files_after_approval(self):
        """Approved dirty plans become one local branch commit and do not push or call gh."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            plan_path, plan, _inputs = write_ready_dirty_materialization_plan(runtime_root, Path(tmp), repo)
            source_head = current_head(repo)
            source_branch = current_branch(repo)
            token = git_pr_dirty_commit_materialization_approval_token(plan)
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/local/test/pull/1")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/local/test/pull/1"
            write_forbidden_git_hooks(repo)

            result, packet = run_git_pr_dirty_commit_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertIsNotNone(packet, result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(packet["schema_version"], "git-pr-dirty-commit-materialization.v1")
            self.assertEqual(packet["packet"], "git_pr_dirty_commit_materialization")
            self.assertTrue(packet["valid"])
            self.assertFalse(packet["dry_run"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["approval_state"], "approved")
            self.assertEqual(packet["execution_authority"], "operator_approved_dirty_commit_materialization")
            self.assertEqual(current_branch(repo), plan["proposed_branch"])
            self.assertEqual(git(repo, "rev-parse", "HEAD^").stdout.strip(), source_head)
            self.assertEqual(git(repo, "log", "-1", "--pretty=%s").stdout.strip(), plan["proposed_commit"]["message"])
            self.assertEqual(
                git(repo, "log", "-1", "--format=%B").stdout.replace("\r\n", "\n").rstrip("\n"),
                plan["proposed_commit"]["message"],
            )
            self.assertEqual(
                sorted(git(repo, "diff", "--name-only", "HEAD^", "HEAD").stdout.splitlines()),
                plan["proposed_commit"]["files"],
            )
            self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all").stdout, "")
            self.assertEqual(git(repo, "ls-remote", "--heads", "origin", plan["proposed_branch"]).stdout, "")
            self.assertFalse(gh_log.exists())
            self.assertEqual(packet["source_branch"], source_branch)
            self.assertEqual(packet["source_head"], source_head)
            self.assertEqual(packet["created_commit"], current_head(repo))
            self.assertEqual(
                packet["side_effects"],
                [
                    "audit_intent_record_appended",
                    "created_branch",
                    "checked_out_branch",
                    "staged_files",
                    "created_commit",
                    "audit_result_record_appended",
                ],
            )
            command_argvs = [trace["argv"] for trace in packet["command_trace"]]
            self.assertEqual(
                command_argvs[0],
                ["git", "-c", "core.hooksPath=", "switch", "-c", plan["proposed_branch"], source_head],
            )
            self.assertEqual(
                command_argvs[1],
                ["git", "-c", "core.hooksPath=", "add", "--", *plan["proposed_commit"]["files"]],
            )
            self.assertEqual(
                command_argvs[2],
                ["git", "-c", "core.hooksPath=", "commit", "--no-verify", "-m", plan["proposed_commit"]["message"]],
            )
            forbidden_tokens = {"gh", "push", "pr", "merge", "release", "publish"}
            for argv in command_argvs:
                self.assertTrue(forbidden_tokens.isdisjoint(argv), argv)

            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_dirty_commit_materialization_intent"], 1)
            self.assertEqual(replay["events_by_type"]["git_pr_dirty_commit_materialization_result"], 1)

    def test_git_pr_dirty_commit_materialize_blocks_missing_or_mismatched_approval_without_side_effects(self):
        """Dirty commit materialization requires approval for the exact saved target."""
        cases = [
            ([], "operator_approval_missing"),
            (["--approval-token", "approve-git-pr:not-the-plan"], "operator_approval_mismatch"),
        ]
        for approval_args, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    runtime_root = Path(tmp) / "runtime"
                    write_brake(runtime_root)
                    plan_path, _plan, _inputs = write_ready_dirty_materialization_plan(runtime_root, Path(tmp), repo)
                    refs_before = git(repo, "show-ref", "--heads").stdout
                    status_before = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
                    branch_before = current_branch(repo)
                    head_before = current_head(repo)

                    result, packet = run_git_pr_dirty_commit_materialize(
                        runtime_root,
                        "--cwd",
                        repo,
                        "--plan-file",
                        str(plan_path),
                        *approval_args,
                        cwd=repo,
                    )

                    self.assertIsNotNone(packet, result.stderr)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(packet["valid"])
                    self.assertEqual(packet["schema_version"], "git-pr-dirty-commit-materialization.v1")
                    self.assertEqual(packet["decision"], "blocked")
                    self.assertEqual(packet["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})
                    self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
                    self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all").stdout, status_before)
                    self.assertEqual(current_branch(repo), branch_before)
                    self.assertEqual(current_head(repo), head_before)
                    self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_dirty_commit_materialize_rechecks_dirty_fingerprint_before_side_effects(self):
        """Dirty content changes after plan review block before audit or Git mutation."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            plan_path, plan, _inputs = write_ready_dirty_materialization_plan(runtime_root, Path(tmp), repo)
            token = git_pr_dirty_commit_materialization_approval_token(plan)
            refs_before = git(repo, "show-ref", "--heads").stdout
            branch_before = current_branch(repo)
            head_before = current_head(repo)
            (Path(repo) / "codex_cadence" / "git_pr_plan.py").write_text("tampered before commit\n", encoding="utf-8")

            result, packet = run_git_pr_dirty_commit_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
            )

            self.assertIsNotNone(packet, result.stderr)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn(
                "git_pr_dirty_materialization_plan_recheck_blocked",
                {blocker["code"] for blocker in packet["blockers"]},
            )
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertEqual(current_branch(repo), branch_before)
            self.assertEqual(current_head(repo), head_before)
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_ready_dry_run_packet_requires_operator_review_and_preserves_non_authority(self):
        """Ready packets remain dry-run plans with operator-only authority."""
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
        """An empty branch prefix yields a valid slug-only branch name."""
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

    def test_blocks_task_carried_branch_policy_violations(self):
        """Task-carried branch policy blocks disallowed dry-run Git/PR plans."""
        cases = [
            (
                "branch_policy_base_branch_disallowed",
                {
                    "allowed_base_branches": ["develop"],
                    "denied_target_branches": [],
                    "required_branch_prefixes": [],
                    "allow_current_branch_main": True,
                },
            ),
            (
                "branch_policy_target_branch_denied",
                {
                    "allowed_base_branches": [],
                    "denied_target_branches": ["cadence/candidate-1"],
                    "required_branch_prefixes": [],
                    "allow_current_branch_main": True,
                },
            ),
            (
                "branch_policy_required_prefix_missing",
                {
                    "allowed_base_branches": [],
                    "denied_target_branches": [],
                    "required_branch_prefixes": ["codex/"],
                    "allow_current_branch_main": True,
                },
            ),
        ]
        for expected_code, branch_policy in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
                    task_packet["branch_policy"] = branch_policy
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
                    self.assertFalse(packet["ready_to_review"])
                    self.assertEqual(packet["recommended_next_action"], "address_blockers")
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})

    def test_blocks_task_carried_branch_policy_current_main(self):
        """Branch policy can prevent Git/PR planning from a main checkout."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            git(repo, "branch", "base-for-plan")
            code_path = Path(repo) / "codex_cadence" / "git_pr_plan.py"
            test_path = Path(repo) / "tests" / "test_git_pr_plan.py"
            code_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.parent.mkdir(parents=True, exist_ok=True)
            code_path.write_text("print('plan')\n", encoding="utf-8")
            test_path.write_text("print('test plan')\n", encoding="utf-8")
            git(repo, "add", "codex_cadence/git_pr_plan.py", "tests/test_git_pr_plan.py")
            git(repo, "commit", "-m", "implement git pr plan on main")
            task_packet = valid_task_packet(repo, Path(tmp) / "executor-result.json")
            task_packet["branch_policy"] = {
                "allowed_base_branches": ["base-for-plan"],
                "denied_target_branches": [],
                "required_branch_prefixes": ["cadence/"],
                "allow_current_branch_main": False,
            }
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
                "--base-branch",
                "base-for-plan",
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertIn("branch_policy_current_branch_main_disallowed", {blocker["code"] for blocker in packet["blockers"]})

    def test_policy_file_branch_policy_blocks_git_pr_plan(self):
        """A local policy file can add branch-policy blockers to git-pr-plan."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "branch_policy": {
                            "allowed_base_branches": ["main"],
                            "denied_target_branches": [],
                            "required_branch_prefixes": ["codex/"],
                            "allow_current_branch_main": True,
                        },
                    }
                ),
                encoding="utf-8",
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
                "--policy-file",
                str(policy_file),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(packet["ready_to_review"])
            self.assertIn("branch_policy_required_prefix_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_policy_file_rejects_unknown_branch_policy_keys(self):
        """Malformed local branch policy fails before git-pr-plan emits a packet."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, _task_packet, _result_evidence = write_packets(tmp, repo)
            policy_file = Path(tmp) / "loop-policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "cadence-loop-policy.v1",
                        "branch_policy": {
                            "required_branch_prefix": ["codex/"],
                            "allow_current_branch_main": False,
                        },
                    }
                ),
                encoding="utf-8",
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
                "--policy-file",
                str(policy_file),
                "--required-body-section",
                "Summary",
                "--required-body-section",
                "Validation",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIsNone(packet)
            self.assertIn("loop policy branch_policy contains unknown keys: required_branch_prefix", result.stderr)

    def test_blocks_metadata_only_materialized_change_evidence(self):
        """Materialized evidence must match the local base diff."""
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

    def test_blocks_extra_local_diff_files_not_declared_in_materialized_evidence(self):
        """The local base diff must not contain files outside materialized evidence."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            (Path(repo) / "unreported.txt").write_text("not in executor evidence\n", encoding="utf-8")
            git(repo, "add", "unreported.txt")
            git(repo, "commit", "-m", "add unreported file")
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
            self.assertIn(
                "materialized_change_evidence_extra_local_changes",
                {blocker["code"] for blocker in packet["blockers"]},
            )

    def test_blocks_missing_pr_body_section_contract(self):
        """PR body preflight blocks when no section contract is supplied."""
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
        """Command examples expose argv for shell-sensitive task titles."""
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
        """files_changed alone is not accepted as materialized evidence."""
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
        """Malformed materialized evidence is reported as invalid."""
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
        """Brake-gated success requires an explicit runtime root."""
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
        """Git state, branch collisions, and template blockers are surfaced."""
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
                    if expected_code == "base_branch_missing":
                        limitations = packet["materialized_change_evidence"]["limitations"]
                        self.assertIn("verified_against_result_metadata_not_local_diff", limitations)
                        self.assertNotIn("verified_against_local_base_diff", limitations)

    def test_pr_body_preflight_warnings_are_propagated(self):
        """Warnings from PR body preflight appear in the git-pr-plan packet."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            task_path, result_path, task_packet, result_evidence = write_packets(tmp, repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            original_preflight = git_pr_plan_module.evaluate_pr_body_preflight

            def warning_preflight(body, *, required_body_sections=None):
                """Return normal preflight output with an advisory warning."""
                packet = original_preflight(body, required_body_sections=required_body_sections)
                packet["warnings"] = [{"code": "template_advisory", "message": "advisory"}]
                return packet

            git_pr_plan_module.evaluate_pr_body_preflight = warning_preflight
            try:
                packet = git_pr_plan_module.evaluate_git_pr_plan(
                    cwd=repo,
                    task_packet=task_packet,
                    result_evidence=result_evidence,
                    task_file=task_path,
                    result_file=result_path,
                    required_body_sections=["Summary", "Validation"],
                    runtime_root=runtime_root,
                )
            finally:
                git_pr_plan_module.evaluate_pr_body_preflight = original_preflight

            self.assertTrue(packet["ready_to_review"])
            self.assertIn("template_advisory", {warning["code"] for warning in packet["warnings"]})

    def test_blocks_untracked_dirty_worktree(self):
        """Untracked files keep git-pr-plan blocked."""
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
        """Task/result path binding mismatches block planning."""
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
        """Invalid packets, non-success results, brake stops, and bad refs block."""
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
        """Missing or malformed brake files block brake-gated planning."""
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
        """The CLI does not call gh or mutate Git, task, result, or runtime files."""
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

    def test_git_pr_materialize_blocks_missing_or_mismatched_approval_without_side_effects(self):
        """Operator approval must match the exact validated plan before any side effect."""
        cases = [
            ([], "operator_approval_missing"),
            (["--approval-token", "approve-git-pr:not-the-plan"], "operator_approval_mismatch"),
        ]
        for approval_args, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
                    init_committed_repo(repo)
                    commit_plan_changes(repo)
                    runtime_root = Path(tmp) / "runtime"
                    write_brake(runtime_root)
                    add_origin_remote(Path(tmp), repo)
                    plan_path, _plan = write_ready_plan(runtime_root, repo, Path(tmp))
                    fake_bin = Path(tmp) / "bin"
                    fake_bin.mkdir()
                    gh_log = Path(tmp) / "gh.log"
                    write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/1")
                    env = os.environ.copy()
                    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
                    env["GH_FAKE_LOG"] = str(gh_log)
                    env["GH_FAKE_PR_URL"] = "https://github.example/pr/1"
                    refs_before = git(repo, "show-ref", "--heads").stdout
                    status_before = git(repo, "status", "--porcelain").stdout
                    audit_path = runtime_root / "audit" / "events.jsonl"

                    result, packet = run_git_pr_materialize(
                        runtime_root,
                        "--cwd",
                        repo,
                        "--plan-file",
                        str(plan_path),
                        *approval_args,
                        cwd=repo,
                        env=env,
                    )

                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(packet["schema_version"], "git-pr-materialization.v1")
                    self.assertFalse(packet["valid"])
                    self.assertEqual(packet["decision"], "blocked")
                    self.assertEqual(packet["side_effects"], [])
                    self.assertIn(expected_code, {blocker["code"] for blocker in packet["blockers"]})
                    self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
                    self.assertEqual(git(repo, "status", "--porcelain").stdout, status_before)
                    self.assertFalse(gh_log.exists())
                    self.assertFalse(audit_path.exists())

    def test_git_pr_materialize_malformed_plan_file_returns_stable_blocker_packet(self):
        """Malformed plan files still return a git-pr-materialization.v1 blocker packet."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            plan_path = Path(tmp) / "bad-plan.json"
            plan_path.write_text("{not-json", encoding="utf-8")

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                cwd=repo,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(packet["schema_version"], "git-pr-materialization.v1")
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("git_pr_plan_unreadable", {blocker["code"] for blocker in packet["blockers"]})

    def test_git_pr_materialize_malformed_pr_json_returns_stable_blocker_packet(self):
        """Malformed optional PR evidence still returns a materialization blocker packet."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            pr_json = Path(tmp) / "bad-pr.json"
            pr_json.write_text("{not-json", encoding="utf-8")
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/local/test/pull/42")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/local/test/pull/42"
            refs_before = git(repo, "show-ref", "--heads").stdout
            status_before = git(repo, "status", "--porcelain").stdout
            head_before = current_head(repo)
            remote_heads_before = git(repo, "ls-remote", "--heads", "origin").stdout
            token = git_pr_materialization_approval_token(
                plan,
                remote_url=remote_push_url(repo),
                pr_number=42,
            )

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--pr-number",
                "42",
                "--pr-json-file",
                str(pr_json),
                "--max-pr-json-age-minutes",
                "30",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(packet["schema_version"], "git-pr-materialization.v1")
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("pr_evidence_unreadable", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertEqual(git(repo, "status", "--porcelain").stdout, status_before)
            self.assertEqual(current_head(repo), head_before)
            self.assertEqual(git(repo, "ls-remote", "--heads", "origin").stdout, remote_heads_before)
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialization_approval_token_requires_secret_and_uses_hmac(self):
        """Approval tokens are HMACs over the target-bound approval payload."""
        plan = {"packet": "git_pr_plan", "ready_to_review": True}

        with self.assertRaises(ValueError):
            git_pr_plan_module.git_pr_materialization_approval_token(plan, remote_url="file:///origin.git")

        token = git_pr_plan_module.git_pr_materialization_approval_token(
            plan,
            remote_url="file:///origin.git",
            approval_secret=APPROVAL_SECRET,
        )
        deterministic_checksum_token = "approve-git-pr:" + checksum_json(
            {
                "schema_version": "git-pr-materialization-approval.v1",
                "packet": "git_pr_materialization_approval",
                "plan_checksum": checksum_json(plan),
                "remote": "origin",
                "remote_url": "file:///origin.git",
                "pr_number": None,
                "operation": "create_pull_request",
            }
        ).removeprefix("sha256:")

        self.assertTrue(token.startswith("approve-git-pr:hmac-sha256:"))
        self.assertNotEqual(token, deterministic_checksum_token)

    def test_git_pr_materialize_requires_secret_and_does_not_emit_expected_token(self):
        """Blocked materialization does not disclose an expected approval credential."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                approval_secret=None,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["approval_state"], "approval_unresolved")
            self.assertTrue(packet["operator_confirmation_required"])
            self.assertNotIn("expected_approval_token", packet)
            self.assertIn("operator_approval_secret_missing", {blocker["code"] for blocker in packet["blockers"]})

    def test_git_pr_materialize_creates_branch_pushes_and_opens_pr_after_approval(self):
        """Approved materialization performs the bounded branch/push/PR sequence and audits it."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            source_branch = current_branch(repo)
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            pr_url = "https://github.example/local/test/pull/123"
            write_fake_gh_materializer(fake_bin, gh_log, pr_url)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = pr_url

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(packet["schema_version"], "git-pr-materialization.v1")
            self.assertTrue(packet["valid"])
            self.assertFalse(packet["dry_run"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["approval_state"], "approved")
            self.assertEqual(packet["pr_url"], pr_url)
            self.assertNotIn("expected_approval_token", packet)
            self.assertEqual(current_branch(repo), source_branch)
            self.assertEqual(
                git(repo, "rev-parse", plan["proposed_branch"]).stdout.strip(),
                plan["repository"]["current_head"],
            )
            self.assertEqual(git(repo, "status", "--porcelain").stdout, "")
            self.assertEqual(
                git(repo, "ls-remote", "--heads", "origin", plan["proposed_branch"]).stdout.split()[0],
                plan["repository"]["current_head"],
            )
            self.assertEqual(
                packet["side_effects"],
                [
                    "audit_intent_record_appended",
                    "created_branch",
                    "pushed_branch",
                    "created_pull_request",
                    "audit_result_record_appended",
                ],
            )
            gh_lines = gh_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(gh_lines), 1)
            self.assertTrue(gh_lines[0].startswith("pr create "))
            self.assertIn(f"--base {plan['repository']['base_branch']}", gh_lines[0])
            self.assertIn(f"--head {plan['proposed_branch']}", gh_lines[0])
            command_argvs = [trace["argv"] for trace in packet["command_trace"]]
            self.assertEqual(command_argvs[0], ["git", "ls-remote", "--heads", "origin", plan["proposed_branch"]])
            self.assertEqual(command_argvs[1], ["git", "branch", plan["proposed_branch"], plan["repository"]["current_head"]])
            self.assertEqual(command_argvs[2], ["git", "push", "--no-verify", "-u", "origin", plan["proposed_branch"]])
            self.assertEqual(command_argvs[3][:7], ["gh", "pr", "create", "--base", "main", "--head", plan["proposed_branch"]])
            forbidden_tokens = {
                "commit",
                "merge",
                "release",
                "publish",
                "executor",
            }
            for argv in command_argvs:
                command_text = " ".join(argv)
                for forbidden in forbidden_tokens:
                    self.assertNotIn(forbidden, command_text)

            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_intent"], 1)
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_result"], 1)
            records = [
                json.loads(line)
                for line in (runtime_root / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            result_record = next(record for record in records if record["event"] == "git_pr_materialization_result")
            self.assertEqual(result_record["payload_checksum"], checksum_json(packet))
            self.assertEqual(result_record["side_effects_checksum"], checksum_json(packet["side_effects"]))

    def test_git_pr_materialize_updates_existing_pr_after_approval(self):
        """Approved PR update mode preflights the target and uses gh pr edit."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(
                plan,
                remote_url=remote_push_url(repo),
                pr_number=42,
            )
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            pr_url = "https://github.example/local/test/pull/42"
            write_fake_gh_materializer(fake_bin, gh_log, pr_url)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = pr_url
            env["GH_FAKE_HEAD_REF"] = plan["proposed_branch"]
            env["GH_FAKE_BASE_REF"] = plan["repository"]["base_branch"]
            env["GH_FAKE_HEAD_OID"] = plan["repository"]["current_head"]

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--pr-number",
                "42",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["valid"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["pr_number"], "42")
            self.assertEqual(packet["pr_url"], pr_url)
            self.assertEqual(
                packet["side_effects"],
                [
                    "audit_intent_record_appended",
                    "created_branch",
                    "pushed_branch",
                    "updated_pull_request",
                    "audit_result_record_appended",
                ],
            )
            gh_lines = gh_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                gh_lines[0],
                "pr view 42 --json number,headRefName,baseRefName,headRefOid",
            )
            self.assertTrue(gh_lines[1].startswith("pr edit 42 "))
            self.assertNotIn("pr create", "\n".join(gh_lines))
            command_argvs = [trace["argv"] for trace in packet["command_trace"]]
            self.assertEqual(
                command_argvs[0],
                ["gh", "pr", "view", "42", "--json", "number,headRefName,baseRefName,headRefOid"],
            )
            self.assertEqual(command_argvs[1], ["git", "branch", plan["proposed_branch"], plan["repository"]["current_head"]])
            self.assertEqual(command_argvs[2], ["git", "push", "--no-verify", "-u", "origin", plan["proposed_branch"]])
            self.assertEqual(command_argvs[3][:4], ["gh", "pr", "edit", "42"])

            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_result"], 1)

    def test_git_pr_materialize_records_fresh_saved_pr_evidence_for_update(self):
        """Fresh saved PR evidence remains labeled in the write-side packet."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            pr_json = Path(tmp) / "pr.json"
            write_materialization_pr_json(pr_json, plan)
            token = git_pr_materialization_approval_token(
                plan,
                remote_url=remote_push_url(repo),
                pr_number=42,
            )
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            pr_url = "https://github.example/local/test/pull/42"
            write_fake_gh_materializer(fake_bin, gh_log, pr_url)
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = pr_url
            env["GH_FAKE_HEAD_REF"] = plan["proposed_branch"]
            env["GH_FAKE_BASE_REF"] = plan["repository"]["base_branch"]
            env["GH_FAKE_HEAD_OID"] = plan["repository"]["current_head"]

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--pr-number",
                "42",
                "--pr-json-file",
                str(pr_json),
                "--max-pr-json-age-minutes",
                "30",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(packet["valid"])
            self.assertEqual(packet["decision"], "materialized")
            self.assertEqual(packet["pr_evidence"]["source"], "saved_pr_json")
            self.assertEqual(packet["pr_evidence"]["freshness"], "saved_input")
            self.assertFalse(packet["pr_evidence"]["stale"])
            self.assertFalse(packet["pr_evidence"]["live"])
            self.assertEqual(packet["pr_evidence"]["path"], str(pr_json.resolve()))
            self.assertEqual(packet["pr_evidence"]["pr_json_checksum"], checksum_json(json.loads(pr_json.read_text(encoding="utf-8"))))

    def test_git_pr_materialize_blocks_stale_saved_pr_evidence_before_pr_update_preflight(self):
        """Stale saved PR evidence blocks before audit, Git writes, push, or gh preflight."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            pr_json = Path(tmp) / "pr.json"
            write_materialization_pr_json(pr_json, plan)
            old_timestamp = 946684800
            os.utime(pr_json, (old_timestamp, old_timestamp))
            token = git_pr_materialization_approval_token(
                plan,
                remote_url=remote_push_url(repo),
                pr_number=42,
            )
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/local/test/pull/42")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/local/test/pull/42"
            env["GH_FAKE_HEAD_REF"] = plan["proposed_branch"]
            env["GH_FAKE_BASE_REF"] = plan["repository"]["base_branch"]
            env["GH_FAKE_HEAD_OID"] = plan["repository"]["current_head"]
            refs_before = git(repo, "show-ref", "--heads").stdout

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--pr-number",
                "42",
                "--pr-json-file",
                str(pr_json),
                "--max-pr-json-age-minutes",
                "30",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertEqual(packet["recommended_next_action"], "refresh_pr_evidence")
            self.assertEqual(packet["side_effects"], [])
            self.assertEqual(packet["pr_evidence"]["source"], "saved_pr_json")
            self.assertEqual(packet["pr_evidence"]["freshness"], "stale")
            self.assertTrue(packet["pr_evidence"]["stale"])
            self.assertIn("pr_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("preflight_pull_request", {trace["label"] for trace in packet["command_trace"]})
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialize_labels_caller_asserted_live_like_pr_evidence(self):
        """Function callers can mark PR evidence live-like without applying saved-file age policy."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            pr = write_materialization_pr_json(Path(tmp) / "pr.json", plan)

            packet = git_pr_plan_module.materialize_git_pr_plan(
                cwd=repo,
                plan_packet=plan,
                plan_file=plan_path,
                approval_token=None,
                runtime_root=runtime_root,
                pr_evidence=pr,
                pr_evidence_source="live_pr_json",
                pr_evidence_captured_at="2000-01-01T00:00:00Z",
                max_pr_evidence_age_minutes=30,
            )

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["pr_evidence"]["source"], "live_pr_json")
            self.assertEqual(packet["pr_evidence"]["freshness"], "live_like")
            self.assertTrue(packet["pr_evidence"]["live"])
            self.assertFalse(packet["pr_evidence"]["stale"])
            self.assertIn("caller_asserted_live_source", packet["pr_evidence"]["limitations"])
            self.assertIn("operator_approval_missing", {blocker["code"] for blocker in packet["blockers"]})
            self.assertNotIn("pr_evidence_stale", {blocker["code"] for blocker in packet["blockers"]})

    def test_git_pr_materialize_rechecks_stale_head_before_side_effects(self):
        """A plan for an older HEAD is blocked before Git, gh, or audit writes."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/2")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/2"
            (Path(repo) / "README.md").write_text("hello\nnew head\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "advance after plan")
            refs_before = git(repo, "show-ref", "--heads").stdout
            audit_path = runtime_root / "audit" / "events.jsonl"

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("stale_git_pr_plan", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertFalse(gh_log.exists())
            self.assertFalse(audit_path.exists())

    def test_git_pr_materialize_blocks_dirty_worktree_before_side_effects(self):
        """The immediate recheck blocks dirty worktrees before audit, Git, or gh writes."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/4")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/4"
            (Path(repo) / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("git_pr_plan_recheck_blocked", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialize_blocks_unapproved_remote_override_without_side_effects(self):
        """Approval binds the remote target so callers cannot swap remotes after review."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            backup_remote = Path(tmp) / "backup.git"
            backup_remote.mkdir()
            git(backup_remote, "init", "--bare")
            git(repo, "remote", "add", "backup", str(backup_remote))
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo, "origin"))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/5")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/5"

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--remote",
                "backup",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("operator_approval_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialize_blocks_existing_remote_branch_before_side_effects(self):
        """PR-create materialization requires the proposed remote branch to be fresh."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            git(repo, "branch", plan["proposed_branch"], plan["repository"]["current_head"])
            git(repo, "push", "origin", plan["proposed_branch"])
            git(repo, "branch", "-D", plan["proposed_branch"])
            refs_before = git(repo, "show-ref", "--heads").stdout
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/7")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/7"

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("remote_branch_exists", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(packet["command_trace"][0]["label"], "preflight_remote_branch")
            self.assertEqual(git(repo, "show-ref", "--heads").stdout, refs_before)
            self.assertFalse(gh_log.exists())
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialize_blocks_pr_update_when_pr_preflight_mismatches_plan(self):
        """PR update mode verifies the existing PR head and base before branch or gh writes."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(
                plan,
                remote_url=remote_push_url(repo),
                pr_number=42,
            )
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/42")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/42"
            env["GH_FAKE_HEAD_REF"] = "someone-elses-branch"
            env["GH_FAKE_BASE_REF"] = plan["repository"]["base_branch"]
            env["GH_FAKE_HEAD_OID"] = plan["repository"]["current_head"]

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                "--pr-number",
                "42",
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["side_effects"], [])
            self.assertIn("pr_update_target_mismatch", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(gh_log.read_text(encoding="utf-8").splitlines(), ["pr view 42 --json number,headRefName,baseRefName,headRefOid"])
            self.assertFalse((runtime_root / "audit" / "events.jsonl").exists())

    def test_git_pr_materialize_failed_git_command_returns_blocker_and_audit_evidence(self):
        """Failed bounded side effects return stable blockers and retain replayable audit evidence."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            origin = add_origin_remote(Path(tmp), repo)
            hook = origin / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\necho rejected by test remote >&2\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/3")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/3"

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertEqual(current_branch(repo), "feature/git-pr-plan")
            self.assertIn("git_pr_materialization_command_failed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertIn("created_branch", packet["side_effects"])
            self.assertNotIn("created_pull_request", packet["side_effects"])
            self.assertFalse(gh_log.exists())

            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_intent"], 1)
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_result"], 1)

    def test_git_pr_materialize_failed_gh_command_returns_blocker_and_audit_evidence(self):
        """A failed PR create is blocked after branch/push and records result audit evidence."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            gh_log = Path(tmp) / "gh.log"
            write_fake_gh_materializer(fake_bin, gh_log, "https://github.example/pr/6")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["GH_FAKE_LOG"] = str(gh_log)
            env["GH_FAKE_PR_URL"] = "https://github.example/pr/6"
            env["GH_FAKE_FAIL_CREATE"] = "1"

            result, packet = run_git_pr_materialize(
                runtime_root,
                "--cwd",
                repo,
                "--plan-file",
                str(plan_path),
                "--approval-token",
                token,
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(packet["valid"])
            self.assertIn("created_branch", packet["side_effects"])
            self.assertIn("pushed_branch", packet["side_effects"])
            self.assertNotIn("created_pull_request", packet["side_effects"])
            self.assertIn("git_pr_materialization_command_failed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertTrue(gh_log.read_text(encoding="utf-8").startswith("pr create "))
            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_result"], 1)

    def test_git_pr_materialize_temp_body_failure_returns_blocker_and_audit_evidence(self):
        """Temporary PR body creation failures are structured after branch/push side effects."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            commit_plan_changes(repo)
            runtime_root = Path(tmp) / "runtime"
            write_brake(runtime_root)
            add_origin_remote(Path(tmp), repo)
            plan_path, plan = write_ready_plan(runtime_root, repo, Path(tmp))
            token = git_pr_materialization_approval_token(plan, remote_url=remote_push_url(repo))
            original_named_temporary_file = git_pr_plan_module.tempfile.NamedTemporaryFile
            original_approval_secret = os.environ.get(APPROVAL_SECRET_ENV)

            def fail_named_temporary_file(*_args, **_kwargs):
                raise OSError("body temp unavailable")

            git_pr_plan_module.tempfile.NamedTemporaryFile = fail_named_temporary_file
            os.environ[APPROVAL_SECRET_ENV] = APPROVAL_SECRET
            try:
                packet = git_pr_plan_module.materialize_git_pr_plan(
                    cwd=repo,
                    plan_packet=plan,
                    plan_file=plan_path,
                    approval_token=token,
                    runtime_root=runtime_root,
                )
            finally:
                git_pr_plan_module.tempfile.NamedTemporaryFile = original_named_temporary_file
                if original_approval_secret is None:
                    os.environ.pop(APPROVAL_SECRET_ENV, None)
                else:
                    os.environ[APPROVAL_SECRET_ENV] = original_approval_secret

            self.assertFalse(packet["valid"])
            self.assertEqual(packet["decision"], "blocked")
            self.assertIn("created_branch", packet["side_effects"])
            self.assertIn("pushed_branch", packet["side_effects"])
            self.assertNotIn("created_pull_request", packet["side_effects"])
            self.assertIn("temporary_pr_body_creation_failed", {blocker["code"] for blocker in packet["blockers"]})
            self.assertEqual(packet["command_trace"][-1]["label"], "temporary_pr_body_creation_failed")
            self.assertEqual(packet["command_trace"][-1]["returncode"], 1)

            replay_result, replay = run_audit_replay(runtime_root, cwd=repo)
            self.assertEqual(replay_result.returncode, 0, replay_result.stderr)
            self.assertTrue(replay["valid"])
            self.assertEqual(replay["events_by_type"]["git_pr_materialization_result"], 1)


def checksum_json(data):
    """Return the checksum format used by policy audit helpers."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
