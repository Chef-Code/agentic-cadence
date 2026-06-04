# Governed Execution Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a governed execution-start gate that consumes an approved `generic-executor-task.v1` packet, rechecks live repo and Cadence state, starts one active epoch, and emits an `execution-start.v1` packet with `executor_started: false`.

**Architecture:** Keep `loop-tick` as the read-only task-packet producer and `start_epoch` as the low-level epoch writer. Add a CLI gate between them that validates task approval, repo freshness, brake state, active epoch state, and packet policy before mutating epoch state. The command appends audit only after the approved epoch-start decision and never invokes an executor.

**Tech Stack:** Python 3.11+, stdlib CLI, local JSON packets, Git, `unittest`, Agentic Cadence runtime JSON/audit files.

---

### Task 1: Failing CLI Tests For Execution Start

**Files:**
- Modify: `tests/test_cadence.py`

- [x] **Step 1: Write the success test**

Add a test that creates a fixture repo, emits an executor task through `loop-tick --emit-executor-task`, writes `approve-executor-task:<task-checksum>` approval evidence, runs `start-governed-execution --task-file <file>`, and asserts:

```python
self.assertEqual(output["schema_version"], "execution-start.v1")
self.assertTrue(output["valid"])
self.assertTrue(output["epoch_started"])
self.assertFalse(output["executor_started"])
self.assertEqual(output["recommended_next_action"], "handoff_to_executor")
```

- [x] **Step 2: Write blocker tests**

Add tests for non-`DRIVE` brake, active epoch conflict, dirty worktree, stale head, malformed task packet, missing approval, and policy mismatch. Each must assert `valid` is false, `epoch_started` is false, `executor_started` is false, a stable blocker code exists, and no active epoch is created.

- [x] **Step 3: Run tests to verify RED**

Run:

```powershell
python -m unittest tests.test_cadence.CadenceCliTests.test_start_governed_execution_starts_epoch_after_approval tests.test_cadence.CadenceCliTests.test_start_governed_execution_blocks_missing_approval -v
```

Expected: fail because `start-governed-execution` is not registered.

### Task 2: Execution Start Packet And Gate

**Files:**
- Modify: `codex_cadence/cli.py`
- Modify: `codex_cadence/executor_contract.py`
- Modify: `codex_cadence/epochs.py`

- [x] **Step 1: Add packet constants and helper functions**

Add `EXECUTION_START_SCHEMA_VERSION = "execution-start.v1"` and helpers that build stable blockers and recommendations.

- [x] **Step 2: Validate task and live repo state**

Load the task file, validate `generic-executor-task.v1`, compute its checksum, require `operator_confirmation_required: true`, require approval evidence, snapshot live repo with the task repo name, and compare current branch/head/dirty-worktree state to the task repo anchor.

- [x] **Step 3: Start one active epoch**

Inside the runtime lock, require brake `DRIVE`, require no active epoch, start an epoch with a single execution task derived from the executor task packet, and emit `execution-start.v1` with `executor_started: false`.

- [x] **Step 4: Re-run focused tests to verify GREEN**

Run the focused `tests.test_cadence` cases added in Task 1.

### Task 3: Audit Replay Support

**Files:**
- Modify: `codex_cadence/policy_audit.py`
- Test: `tests/test_audit_replay.py`

- [x] **Step 1: Add execution-start audit record validation**

Accept a compact `execution_start_decision` audit event with action, reason, task file, task checksum, repo, branch, head, `valid`, `epoch_started`, `executor_started`, and payload checksum.

- [x] **Step 2: Assert replay counts the new event**

Add a focused replay test that starts governed execution and checks `events_by_type.execution_start_decision == 1`.

- [x] **Step 3: Run audit tests**

Run:

```powershell
python -m unittest tests.test_audit_replay -v
```

### Task 4: Documentation And Validation

**Files:**
- Modify: `README.md`
- Modify: `docs/protocol.md`
- Modify: `docs/autonomous-loop-readiness.md`
- Modify: `docs/implementation-slices.md`
- Modify: `docs/progress-log.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/session-handoff.md`
- Modify: `scripts/validate_protocol.py`

- [x] **Step 1: Document the command**

Describe `start-governed-execution`, the `execution-start.v1` packet, approval evidence, blockers, audit record, and non-goals.

- [x] **Step 2: Update validator tokens**

Require docs to mention `start-governed-execution`, `execution-start.v1`, `execution_start_decision`, and `executor_started: false`.

- [x] **Step 3: Run final validation**

Run:

```powershell
python -m py_compile codex_cadence/cli.py codex_cadence/epochs.py codex_cadence/executor_contract.py codex_cadence/policy_audit.py
python -m unittest tests.test_cadence tests.test_epochs tests.test_executor_contract tests.test_audit_replay -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
git diff --check
```
