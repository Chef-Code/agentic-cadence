# Controlled Loop Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only packet that composes a saved `loop-run-plan.v1` packet with a separately produced `execution-start.v1` packet.

**Architecture:** Implement a new CLI command, `controlled-loop-start`, near the existing loop planning and controlled tick code in `codex_cadence/cli.py`. The command reads two local JSON files, validates their packet shapes and checksums, verifies that the execution start matches the executor task planned by the loop run plan, and emits `controlled-loop-start.v1` with explicit non-runner/non-executor/non-GitHub side-effect flags. Tests exercise the happy path and mismatch blocking through the public CLI.

**Tech Stack:** Python 3.11 standard library, existing Agentic Cadence CLI helpers, `unittest`, local git fixture helpers.

**Status:** Completed. Code review hardening added extra fail-closed coverage
for unapproved execution-start evidence, malformed embedded executor tasks, and
side-effect-contaminated inputs.

---

### Task 1: Add the Public CLI Contract

**Files:**
- Modify: `tests/test_cadence.py`
- Modify: `codex_cadence/cli.py`

- [x] **Step 1: Write the failing happy-path test**

Add a test that:

```python
def test_controlled_loop_start_composes_plan_and_execution_start(self):
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
        init_committed_repo(repo)
        marker = Path(repo) / "notes.py"
        marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
        git(repo, "add", "notes.py")
        git(repo, "commit", "-m", "add repo health marker")
        plan_path = Path(tmp) / "loop-run-plan.json"
        start_path = Path(tmp) / "execution-start.json"
        evidence_path = Path(tmp) / "executor-result.json"

        plan_result, plan = run_cli(
            tmp,
            "loop-run-plan",
            "--cwd",
            repo,
            "--repo",
            "local/test",
            "--intent",
            "repo_health",
            "--emit-executor-task",
            "--allowed-path",
            "notes.py",
            "--required-check",
            "python -m unittest tests.test_cadence",
            "--executor-evidence-path",
            str(evidence_path),
        )
        self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        start_result, start = run_cli(
            tmp,
            "start-governed-execution",
            "--task-file",
            str(Path(tmp) / "executor-task.json"),
            "--approval-token",
            f"approve-executor-task:{plan['executor_task_checksum']}",
        )
```

In the actual test, write `plan["executor_task"]` to `executor-task.json` before calling `start-governed-execution`, save `start` to `execution-start.json`, then run:

```python
result, output = run_cli(
    tmp,
    "controlled-loop-start",
    "--loop-run-plan-file",
    str(plan_path),
    "--execution-start-file",
    str(start_path),
)
```

Assert `schema_version == "controlled-loop-start.v1"`, `packet == "controlled_loop_start"`, `controlled_start_status == "completed"`, `recommended_next_action == "plan_executor_invocation"`, `runner_started is False`, `executor_started is False`, `loop_continuation_started is False`, matching plan/start checksums, and no audit file was created by the new command.

- [x] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start -v
```

Expected: FAIL because `controlled-loop-start` is not a known command.

- [x] **Step 3: Implement the minimal CLI path**

Add helpers in `codex_cadence/cli.py`:

```python
CONTROLLED_LOOP_START_SCHEMA_VERSION = "controlled-loop-start.v1"

def controlled_loop_start_command(args: argparse.Namespace) -> int:
    ...
```

The command must read both files, verify object packets, require `loop-run-plan.v1` and `execution-start.v1`, require `loop_run_plan.executor_task_checksum == execution_start.task_checksum`, require `execution_start.valid is True`, require `execution_start.epoch_started is True`, require `execution_start.executor_started is False`, and emit a read-only packet with `runner_started`, `executor_started`, `pr_action_started`, `github_write_started`, `merge_started`, `release_started`, `package_publication_started`, `role_assignment_started`, `agent_scheduling_started`, and `loop_continuation_started` all false.

- [x] **Step 4: Run the happy-path test to verify it passes**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start -v
```

Expected: PASS.

### Task 2: Add Blocking Coverage

**Files:**
- Modify: `tests/test_cadence.py`
- Modify: `codex_cadence/cli.py`

- [x] **Step 1: Write the failing mismatch test**

Add a test that saves a valid loop-run-plan and a valid execution-start packet, mutates the saved execution-start `task_checksum` to `sha256:` plus 64 `"0"` characters, runs `controlled-loop-start`, and asserts return code `2`, `controlled_start_status == "blocked"`, `recommended_next_action == "recreate_execution_start"`, blocker code `execution_start_task_mismatch`, and all side-effect flags false.

- [x] **Step 2: Run the mismatch test to verify it fails**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start -v
```

Expected: FAIL until blocker mapping is implemented.

- [x] **Step 3: Implement blocker mapping**

Extend the command so any read/shape/schema mismatch blocks without mutation. Use stable blocker codes including `loop_run_plan_evidence_missing`, `execution_start_evidence_missing`, `controlled_start_packet_mismatch`, `loop_run_plan_not_ready`, `execution_start_invalid`, and `execution_start_task_mismatch`.

- [x] **Step 4: Run focused tests**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start -v
```

Expected: both tests PASS.

### Task 3: Document the Slice

**Files:**
- Modify: `README.md`
- Modify: `docs/protocol.md`
- Modify: `docs/autonomous-loop-readiness.md`
- Modify: `docs/implementation-slices.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/progress-log.md`

- [x] **Step 1: Update docs**

Describe `controlled-loop-start` as a read-only composition boundary after `loop-run-plan` and `start-governed-execution`. State that it does not start a runner, start or retry an executor, continue a loop, create branches, commit, push, call GitHub, merge, release, publish packages, assign roles, or schedule agents.

- [x] **Step 2: Run validation**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_controlled_loop_start_composes_plan_and_execution_start tests.test_cadence.CadenceCliTests.test_controlled_loop_start_blocks_mismatched_execution_start -v
python scripts/validate_protocol.py
python -m py_compile codex_cadence\cli.py
git diff --check
```

Expected: all commands exit 0.

### Task 4: Final Verification

**Files:**
- All changed files.

- [x] **Step 1: Run broader tests**

Run:

```powershell
python -m unittest tests.test_cadence -v
python scripts/validate_protocol.py
python -m compileall scripts codex_cadence transmission_control tests
git diff --check
```

Expected: all commands exit 0.

- [x] **Step 2: Inspect git state**

Run:

```powershell
git status --short
git diff --stat
```

Expected: only intended files changed.
