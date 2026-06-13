# Task 38 Loop Run Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `loop-run-plan` command that composes the next governed loop steps without starting an executor, epoch, PR action, merge, or GitHub write.

**Architecture:** Factor the existing `loop-tick` packet construction into a helper so the current command keeps its audit behavior while the new planner can wrap the same decision data. The planner emits a stable `loop_run_plan` packet with conservative planned steps and explicit non-start flags.

**Tech Stack:** Python CLI in `codex_cadence/cli.py`, unittest coverage in `tests/test_cadence.py`, repository documentation in `README.md`.

---

### Task 1: Add failing CLI tests

**Files:**
- Modify: `tests/test_cadence.py`

- [x] **Step 1: Write the no-candidate failing test**

Add this test near the existing `loop-tick` tests:

```python
    def test_loop_run_plan_reports_no_candidates_without_starting_runner(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)

            result, output = run_cli(
                tmp,
                "loop-run-plan",
                "--cwd",
                repo,
                "--repo",
                "local/test",
                "--intent",
                "repo_health",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["schema_version"], "loop-run-plan.v1")
            self.assertEqual(output["packet"], "loop_run_plan")
            self.assertTrue(output["read_only"])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertEqual(output["recommended_next_action"], "stop_no_candidates")
            self.assertEqual(output["loop_tick"]["recommended_next_action"], "no_candidates")
            self.assertEqual(output["planned_steps"][0]["name"], "loop_tick")
            self.assertEqual(output["planned_steps"][0]["status"], "accepted")
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])
```

- [x] **Step 2: Write the executor approval failing test**

Add this test next to the no-candidate test:

```python
    def test_loop_run_plan_emits_executor_task_approval_plan_for_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as repo:
            init_committed_repo(repo)
            marker = Path(repo) / "notes.py"
            marker.write_text("# TODO inspect repo health marker\n", encoding="utf-8")
            git(repo, "add", "notes.py")
            git(repo, "commit", "-m", "add repo health marker")
            evidence_path = Path(tmp) / "executor-result.json"

            result, output = run_cli(
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

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output["recommended_next_action"], "request_operator_approval")
            self.assertTrue(output["operator_confirmation_required"])
            self.assertFalse(output["runner_started"])
            self.assertFalse(output["executor_started"])
            self.assertFalse(output["epoch_started"])
            self.assertFalse(output["pr_action_started"])
            self.assertFalse(output["github_write_started"])
            self.assertFalse(output["merge_started"])
            self.assertEqual(output["loop_tick"]["recommended_next_action"], "approve_executor_task")
            executor_task = output["executor_task"]
            self.assertEqual(executor_task["packet"], "executor_task")
            self.assertEqual(output["executor_task_checksum"], checksum_json(executor_task))
            step_names = [step["name"] for step in output["planned_steps"]]
            self.assertEqual(step_names, ["loop_tick", "operator_approval", "start_governed_execution"])
            self.assertEqual(output["planned_steps"][1]["status"], "required")
            self.assertEqual(output["planned_steps"][2]["status"], "blocked_until_approval")
            self.assertEqual(output["planned_steps"][2]["approval_token_hint"], f"approve-executor-task:{checksum_json(executor_task)}")
            self.assertEqual(list((Path(tmp) / "epochs" / "active").glob("*.json")), [])
```

- [x] **Step 3: Run tests to verify RED**

Run:

```bash
python -m unittest tests.test_cadence.CadenceCliTests.test_loop_run_plan_reports_no_candidates_without_starting_runner tests.test_cadence.CadenceCliTests.test_loop_run_plan_emits_executor_task_approval_plan_for_candidate -v
```

Expected: both tests fail because `loop-run-plan` is not an accepted command.

### Task 2: Implement the read-only planner

**Files:**
- Modify: `codex_cadence/cli.py`
- Modify: `tests/test_cadence.py`

- [x] **Step 1: Factor loop tick payload construction**

Replace the body of `loop_tick_command` with a helper plus the original emit/audit behavior:

```python
def build_loop_tick_payload(args: argparse.Namespace) -> dict[str, Any]:
    ...
    return payload


def loop_tick_command(args: argparse.Namespace) -> int:
    payload = build_loop_tick_payload(args)
    payload["audit_record"] = append_audit_record(args.root, loop_tick_audit_record(payload))
    emit(payload)
    return 0
```

- [x] **Step 2: Add planner constants and helpers**

Add helpers near the loop tick code:

```python
LOOP_RUN_PLAN_SCHEMA_VERSION = "loop-run-plan.v1"


def loop_run_plan_next_action(loop_tick: dict[str, Any]) -> tuple[str, str]:
    ...


def build_loop_run_plan_steps(loop_tick: dict[str, Any]) -> list[dict[str, Any]]:
    ...
```

- [x] **Step 3: Add `loop_run_plan_command`**

Create a command that calls `build_loop_tick_payload(args)`, wraps the result, and emits `loop_run_plan` with `read_only`, `runner_started`, `executor_started`, `epoch_started`, `pr_action_started`, `github_write_started`, and `merge_started` flags all set conservatively.

- [x] **Step 4: Add parser wiring**

Add a `loop-run-plan` subparser with the same planning options as `loop-tick`, then set `func=loop_run_plan_command`.

- [x] **Step 5: Run tests to verify GREEN**

Run:

```bash
python -m unittest tests.test_cadence.CadenceCliTests.test_loop_run_plan_reports_no_candidates_without_starting_runner tests.test_cadence.CadenceCliTests.test_loop_run_plan_emits_executor_task_approval_plan_for_candidate -v
```

Expected: both tests pass.

### Task 3: Document and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-slices.md`
- Modify: `docs/autonomous-loop-readiness.md`

- [x] **Step 1: Document the command**

Add a short README entry showing `loop-run-plan` and explaining that it does not start execution or write to GitHub.

- [x] **Step 2: Update readiness docs**

Mark the new slice as a read-only planner and keep the continuous runner listed as not yet built.

- [x] **Step 3: Run verification**

Run:

```bash
python -m unittest tests.test_cadence.CadenceCliTests.test_loop_run_plan_reports_no_candidates_without_starting_runner tests.test_cadence.CadenceCliTests.test_loop_run_plan_emits_executor_task_approval_plan_for_candidate -v
python -m py_compile codex_cadence/cli.py
python scripts/validate_protocol.py
git diff --check
```

Expected: all commands pass with no warnings.
