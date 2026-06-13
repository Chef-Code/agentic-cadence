# Agentic Cadence Protocol

Status: draft

Reference implementation: `codex_cadence/cli.py`

## Concepts

Agentic Cadence has five core concepts today:

- `handoff`: a durable message that lets a new coding-agent session continue from a precise point.
- `signature`: a machine-detectable marker that tells automation a handoff is ready.
- `cadence state`: the operator-facing football state that controls whether automation may continue.
- `brake`: the legacy persisted state behind Cadence state, retained for compatibility.
- `clean-square`: the shutdown routine for the old session after a handoff is safely written.

Future orchestration concepts include:

- `orchestrator`: the future policy authority that decides whether to continue, stop, retry, split, review, or hand off work.
- `agent role`: a bounded responsibility such as planning, architecture, build, review, QA, documentation, release, or handoff.
- `handoff contract`: the explicit state package that transfers work between sessions or between roles.

The current implementation mainly exercises the single-agent Phase 1 path. The
protocol should not assume that only one agent exists forever.

## GitHub-Native Orchestration Model

The long-term coordination surface is GitHub-native: issues or recorded
decisions define work, assignees or role claims establish ownership, branches
isolate implementation, pull requests expose changes, reviews and CI gate
quality, documentation keeps the repository aligned, and merges advance stable
`main`.

Cadence should govern that workflow rather than bypass it. Future agent-team
orchestration must preserve these invariants:

- no agent edits `main` directly;
- every implementation happens on a branch;
- every meaningful change produces a pull request;
- validation runs before merge;
- review is separate from implementation when possible;
- handoff is explicit when work crosses a session or role boundary;
- small bounded slices are preferred over large ambiguous work.

Current commands do not implement role assignment, continuous or write-side
GitHub synchronization, branch creation, PR creation, or merge authority. Protocol language that
mentions agent roles or an agent pool is a design target unless a command
explicitly documents otherwise.

## Runtime State

The default runtime root is:

```text
%USERPROFILE%\.codex\cadence
```

If the legacy `%USERPROFILE%\.codex\transmission` root already exists, Agentic Cadence must reuse it by default. This preserves queued handoffs, approvals, and legacy brake controls so a rename cannot reset `PARK` or `NEUTRAL` to a fresh `DRIVE` brake. If both legacy and cadence roots exist, Agentic Cadence must fail closed until the operator selects one with `CODEX_CADENCE_ROOT`, `CODEX_TRANSMISSION_ROOT`, or `--root`.

Commands that use the runtime root must reject an unignored repo-local runtime root when the target working directory is inside a git repo. A repo-local root is allowed only when git ignores the root path or when the operator passes `--allow-repo-local-root`. This keeps Cadence state from becoming a self-created dirty-worktree signal. Runtime roots outside the target repo remain the preferred default.

Primary entry points share the package-owned dispatcher in `codex_cadence/cli.py`. `scripts/cadence.py` is a source-tree wrapper for direct checkout execution. Legacy entry points remain compatibility shims during the rename: `scripts/transmission.py` delegates to `transmission_control.cli`, and `transmission_control.*` aliases `codex_cadence.*`. New integrations should use the Cadence names.

Suggested layout:

```text
cadence/
  brake.json
  audit/
    events.jsonl
  handoffs/
    ready/
    claimed/
    completed/
    failed/
  logs/
```

Repo-local files should be adapters or configuration only. They should not be required for the global protocol to work.

## Cadence States

`status` output exposes a football-facing `cadence` object for operators:

```json
{
  "brake": {
    "status": "DRIVE"
  },
  "cadence": {
    "state": "PLAY_ON",
    "legacy_brake": "DRIVE",
    "can_start_work": true,
    "requires_operator_resume": false
  }
}
```

`status.brake.status` remains present for compatibility. `cadence.legacy_brake` mirrors it so operators can read the football-facing state while existing automation continues to read the legacy field.

Allowed Cadence states:

- `PLAY_ON`: automation may continue governed work.
- `HUDDLE`: automation must not start new pickup work.
- `TIMEOUT`: automation must stop pickup and require explicit operator resume.

`brake.json` remains the persisted compatibility format behind Cadence state.

```json
{
  "status": "DRIVE",
  "reason": null,
  "scope": "global",
  "resume_requires": null,
  "updated_at": "2026-05-22T00:00:00Z"
}
```

Allowed statuses:

- `DRIVE`: maps to `PLAY_ON`.
- `NEUTRAL`: maps to `HUDDLE`.
- `PARK`: maps to `TIMEOUT`.

## Handoff Lifecycle

1. The active session detects a guardrail: context pressure, CI loop exhaustion, reviewer loop exhaustion, explicit operator request, or policy limit.
2. The session writes a handoff file with enough context for a fresh session to continue.
3. The session writes a signature marking the handoff as ready.
4. Automation sees the ready signature, checks Cadence state, then claims the handoff.
5. A fresh coding-agent session receives the handoff message.
6. The old session runs clean-square and stops active work.
7. The new session marks the handoff completed or failed.

In the team-orchestration model, the same lifecycle also applies across roles:
a Planning Agent can hand a decomposed task to a Builder Agent, a Builder Agent
can hand a PR to a Reviewer Agent, a QA Agent can hand failures back to a
Builder Agent, and a Documentation Agent can record architecture or behavior
changes after merge. Role handoffs must be explicit and verifiable, not
implicit transcript memory.

## Prepare Handoff

`prepare-handoff` is the deterministic old-session orchestration command for context handoff. It must check Cadence state, capture a repo snapshot, create a signed ready handoff, validate that handoff, record clean-square, and emit a JSON packet with `stop_current_session: true`.

The command must not claim, complete, or fail the handoff it creates. It must not launch a new agent context, commit, push, create a pull request, spend elected review, or merge. If Cadence is `HUDDLE` or `TIMEOUT`, it fails closed before writing a ready handoff. Duplicate ready handoff ids must fail without overwriting the existing record.

V1 requires explicit guardrail input such as `--guardrail context`. Automatic context detection requires a host/session signal; Cadence must not infer token pressure from transcript guesses.

## Resume Verification

`verify-resume` is the read-only gate a fresh session can run before continuing
a handoff. It emits a `resume-verification.v1` packet. The command exits `0`
when `resumable` is true and exits `2` when verifier blockers are present;
normal CLI argument errors remain ordinary CLI errors.

The packet shape is stable:

```json
{
  "protocol_version": "v1",
  "schema_version": "resume-verification.v1",
  "packet": "resume_verification",
  "handoff_id": "context-loop",
  "resumable": false,
  "read_only": true,
  "handoff": {},
  "clean_square": {},
  "repository": {},
  "cadence": {},
  "active_epoch": {},
  "policy_evidence": {},
  "blockers": [{"code": "stable_code", "message": "human readable"}],
  "recommended_next_action": "inspect_resume_blockers"
}
```

Evidence sections have these meanings:

- `handoff`: observed handoff state, path, status, claimer, and signature
  validation errors;
- `clean_square`: presence and validity of old-session shutdown evidence;
- `repository`: current branch, `HEAD`, dirty-worktree state, and the expected
  repo, branch, head, snapshot id, snapshot path, and snapshot checksum;
- `cadence`: current brake and mapped Cadence state;
- `active_epoch`: active epoch count and matching evidence;
- `policy_evidence`: estimate checksum, approval requirement, approval
  presence, and policy action.

The verifier must check handoff signature and checksum, require the handoff to
be in `claimed` state with `status: "CLAIMED"` and a non-empty `claimed_by`
before `resume_work`, validate clean-square evidence, bind the handoff's
metadata resume snapshot to the persisted snapshot record and signed handoff
message, compare the current repo branch and `HEAD` with that snapshot, reject
dirty worktrees, reject missing, invalid, or non-`DRIVE` brakes, reject active
epoch conflicts or mismatches, and re-check pickup policy evidence.
Approval-gated handoffs must have a separate approval record bound to the
handoff checksum and estimate checksum.

Every blocker is an object with `code`, `message`, and optional structured
fields such as `path`, `expected`, `actual`, `status`, or `states`. Stable
blocker codes are:

- Handoff state and signature: `handoff_not_found`, `handoff_unreadable`,
  `handoff_not_claimed`, `handoff_claimed_by_other`,
  `handoff_state_conflict`, `handoff_signature_invalid`,
  `handoff_checksum_mismatch`, `handoff_protocol_unsupported`;
- resume snapshot and repository: `resume_snapshot_invalid`,
  `handoff_repo_evidence_missing`, `repo_inspection_failed`,
  `repo_branch_mismatch`, `repo_head_mismatch`, `dirty_worktree`;
- runtime brake: `runtime_brake_missing`, `runtime_brake_invalid`,
  `active_brake_stop`;
- active epoch: `active_epoch_conflict`, `active_epoch_invalid`,
  `active_epoch_repo_mismatch`, `active_epoch_branch_mismatch`,
  `active_epoch_head_mismatch`;
- clean-square and policy: `clean_square_missing`, `clean_square_invalid`,
  `policy_evidence_missing`, `policy_evidence_invalid`,
  `policy_approval_missing`, `policy_self_evolution_propose_only`.

Recommended actions are stable and ordered by precedence:

| `recommended_next_action` | Used when |
| --- | --- |
| `resume_work` | No blockers are present |
| `inspect_runtime_state` | Runtime brake evidence is missing or invalid |
| `clear_brake` | The active brake is not `DRIVE` |
| `clean_worktree` | The current worktree is dirty |
| `resolve_claim_conflict` | The handoff is claimed by someone else or exists in multiple states |
| `approve_handoff` | Pickup policy requires approval that is not present |
| `claim_handoff` | The only higher-priority issue is that the handoff is not claimed |
| `close_or_fail_active_epoch` | Active epoch records are present, malformed, duplicated, or mismatched |
| `recreate_handoff` | Handoff, resume snapshot, clean-square, repo binding, or policy evidence is stale or invalid |
| `inspect_resume_blockers` | Fallback for blocker combinations without a more specific recovery action |

`verify-resume` must not mutate handoff state, create or clear approvals, write
clean-square records, launch a new session, infer host context pressure, start
or invoke an executor, create branches, write pull requests, merge, release, or
publish packages. It is a gate packet only; operators or external orchestration
must perform any recommended next action separately.

## Resume Continuation

`resume-continuation` is the read-only gate between a saved
`resume-verification.v1` packet and a governed execution-start decision. It
does not start execution. It verifies that the saved packet is still fresh, then
recomputes `verify-resume` for the same handoff and claimer before recommending
whether external orchestration should call `start-governed-execution`.

```bash
agentic-cadence --root <runtime-root> resume-continuation --resume-verification-file resume-verification.json --cwd . --claimer codex
agentic-cadence --root <runtime-root> resume-continuation --resume-verification-file resume-verification.json --cwd . --claimer codex --ownership-target ownership-1 --ownership-role implementer --ownership-task-id task-1
```

The command emits a `resume-continuation.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "resume-continuation.v1",
  "packet": "resume_continuation",
  "handoff_id": "context-loop",
  "claimer": "codex",
  "valid": false,
  "continuable": false,
  "read_only": true,
  "executor_started": false,
  "epoch_started": false,
  "pr_action_started": false,
  "resume_verification": {},
  "fresh_resume_verification": {},
  "ownership": {},
  "ownership_scope": {},
  "checks": {},
  "blockers": [{"code": "stable_code", "message": "human readable"}],
  "recommended_next_action": "inspect_resume_blockers",
  "side_effects": []
}
```

`resume-continuation` rechecks the saved packet schema and checksum, packet file
mtime freshness, handoff id, claimer, handoff state, clean-square evidence,
repository branch and `HEAD`, dirty-worktree state, active brake, active epoch
state, and policy evidence. The default freshness window is 60 minutes and can
be overridden with `--max-resume-age-minutes`. When `--ownership-target` is
supplied, ownership is checked only after those resume blockers pass. The
continuation ownership scope uses `--ownership-task-id` when supplied, otherwise
the resumed handoff id as the local task anchor, and rechecks active
`work-ownership.v1` evidence for task id, handoff id, role, claimer, repo,
branch, `HEAD`, duplicate active ownership, freshness, malformed evidence, and
registry path safety. Ownership freshness defaults to the local ownership
window and can be overridden with `--max-ownership-age-minutes`.

Stable continuation-specific blocker codes include
`resume_verification_file_unreadable`, `resume_verification_invalid`,
`resume_verification_schema_unsupported`, `resume_handoff_id_missing`,
`resume_handoff_id_invalid`, `resume_handoff_id_mismatch`,
`resume_verification_not_resumable`, `resume_claimer_missing`,
`resume_claimer_mismatch`, `resume_recheck_failed`,
`resume_verification_from_future`, `resume_verification_stale`, and
`resume_verification_anchor_mismatch`. Ownership blocker codes include
`ownership_target_missing`, `ownership_record_missing`, `ownership_closed`,
`ownership_stale`, `duplicate_active_ownership`, `ownership_record_invalid`,
`ownership_schema_unsupported`, `ownership_required_field_missing`,
`ownership_field_type_invalid`, `ownership_record_unreadable`,
`ownership_record_path_invalid`, `ownership_record_outside_registry`,
`ownership_record_ambiguous`, `ownership_registry_state_invalid`,
`ownership_repo_evidence_missing`, `ownership_repo_mismatch`,
`ownership_branch_mismatch`, `ownership_task_mismatch`, `ownership_handoff_mismatch`,
`ownership_role_mismatch`, `ownership_claimer_mismatch`, and
`ownership_head_mismatch`. Fresh verifier blockers are forwarded unchanged,
including `repo_head_mismatch`, `repo_branch_mismatch`, `dirty_worktree`,
`clean_square_missing`, `policy_approval_missing`, `active_brake_stop`,
and `active_epoch_conflict`; resume continuation may also add
`active_epoch_exists` from fresh active-epoch evidence.

Recommended actions are limited to `start_governed_execution`,
`claim_handoff`, `approve_handoff`, `recreate_handoff`,
`close_or_fail_active_epoch`, `claim_work_ownership`,
`refresh_ownership_evidence`, `close_or_fail_active_ownership`, and
`inspect_resume_blockers`. A valid packet recommends
`start_governed_execution`; non-resumable saved verifier packets preserve
`claim_handoff` or `approve_handoff` when that is the saved recovery action.
Stale repo, handoff, clean-square, snapshot, or policy anchors recommend
`recreate_handoff`; active epoch blockers recommend
`close_or_fail_active_epoch`; missing or closed ownership recommends
`claim_work_ownership`; stale ownership recommends
`refresh_ownership_evidence`; duplicate or mismatched active ownership
recommends `close_or_fail_active_ownership`; unsupported combinations
recommend `inspect_resume_blockers`.

The command must not claim handoffs, write clean-square records, launch a new
session, infer host context pressure, start or invoke an executor, create
branches, write pull requests, merge, release, or publish packages.

## Local Work Ownership

`work-ownership.v1` records are local filesystem evidence under
`<runtime-root>/work-ownership/{active,closed,failed}`. A record binds the
local task id, candidate id, role label, claimer, repo, branch, optional
`head`, optional PR number, optional epoch id, optional handoff id, status,
`created_at`, and `updated_at` before any future multi-worker orchestration is
introduced.

The read-only status command scans local ownership records for the current
repository scope:

```bash
agentic-cadence --root <runtime-root> work-ownership-status --cwd . --repo owner/repo --task-id task-1
```

It emits a `work-ownership-status.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "work-ownership-status.v1",
  "packet": "work_ownership_status",
  "read_only": true,
  "valid": false,
  "counts": {"total": 2, "active": 2, "closed": 0, "failed": 0},
  "records": [],
  "blockers": [{"code": "duplicate_active_ownership", "message": "human readable"}],
  "recommended_next_action": "resolve_duplicate_ownership",
  "side_effects": []
}
```

The read-only validation command checks one record by id or path:

```bash
agentic-cadence --root <runtime-root> validate-work-ownership ownership-1 --cwd . --repo owner/repo --task-id task-1 --require-active
```

It emits `work-ownership-validation.v1` with the checked record summary,
stable blockers, and a bounded recommendation. Stable blocker codes include
`duplicate_active_ownership`, `repo_inspection_failed`,
`ownership_registry_state_invalid`, `ownership_record_missing`,
`ownership_record_unreadable`, `ownership_record_path_invalid`,
`ownership_record_outside_registry`,
`ownership_record_ambiguous`, `ownership_record_invalid`,
`ownership_schema_unsupported`, `ownership_required_field_missing`,
`ownership_field_type_invalid`, `ownership_id_invalid`,
`ownership_id_mismatch`,
`ownership_status_invalid`, `ownership_state_mismatch`,
`ownership_timestamp_invalid`, `ownership_stale`, `ownership_closed`,
`ownership_repo_mismatch`, `ownership_branch_mismatch`, and
`ownership_task_mismatch`. Recommended actions are limited to
`use_work_ownership_status`, `use_work_ownership_record`,
`resolve_duplicate_ownership`, `provide_ownership_record`,
`refresh_ownership_evidence`, `repair_ownership_record`, and
`inspect_ownership_evidence`.

The explicit local claim command creates one active record:

```bash
agentic-cadence --root <runtime-root> claim-work-ownership \
  --cwd . \
  --repo owner/repo \
  --branch feature/task-1 \
  --head <current-head-sha> \
  --task-id task-1 \
  --candidate-id candidate-1 \
  --role implementer \
  --claimer local-agent
```

It emits `work-ownership-claim.v1` with `read_only: false`, `valid`,
`ownership_written`, `ownership_id`, `repository`, `request`, `record`,
`blockers`, `side_effects`, and `recommended_next_action`. The command rechecks
current branch, `HEAD`, dirty-worktree state, duplicate active ownership, stale
active ownership, malformed existing ownership evidence, and registry path
safety before writing. Accepted claims append a compact
`work_ownership_mutation` audit record.

The closeout commands move one active record by id or registry path:

```bash
agentic-cadence --root <runtime-root> close-work-ownership ownership-1 \
  --cwd . --repo owner/repo --branch feature/task-1 --head <current-head-sha> \
  --task-id task-1 --claimer local-agent --summary "completed locally"
agentic-cadence --root <runtime-root> fail-work-ownership ownership-1 \
  --cwd . --repo owner/repo --branch feature/task-1 --head <current-head-sha> \
  --task-id task-1 --claimer local-agent --summary "blocked locally"
agentic-cadence --root <runtime-root> complete-work-ownership-from-closeout ownership-1 \
  --cwd . \
  --closeout-file executor-closeout.json \
  --closeout-checksum sha256:<hex> \
  --candidate-id candidate-1 \
  --role implementer \
  --claimer local-agent \
  --summary "completed by executor closeout"
```

Manual close/fail commands emit `work-ownership-closeout.v1`, move the active
record to `closed` or `failed`, write closeout evidence onto the record, and
append `work_ownership_mutation` audit evidence. Missing records, mismatched
repo, branch, head, task, or claimer, already closed/failed records, malformed
records, dirty worktrees, stale repo heads, and unsafe registry paths block
before mutation.

`complete-work-ownership-from-closeout` is the closeout-bound completion mode.
It consumes a saved `executor_epoch_closeout` packet and supplied closeout
checksum before moving ownership. The packet must be valid, have
`closeout_status: completed`, expose a readable valid `executor_task`, and
match the supplied closeout checksum. The closeout-bound gate also rereads the
referenced `result_file` and `snapshot_after_file`, revalidates the result
packet, checks the saved snapshot checksum, and compares the saved validation
and next-decision anchors with the reread task/result evidence. Completed
closeout-bound completion also requires exactly one bound execution record
(`run_record` or `real_invocation`) with matching path, invocation, status,
epoch, validation, and evidence checksums, plus a referenced
`executor_epoch_closeout` audit-log line whose hash-chain metadata and payload
checksum match the supplied closeout packet. The closeout, task, result,
snapshot, execution record, and audit evidence bind task id, task checksum,
candidate id, repo, branch, `HEAD`, and epoch id; the command separately checks
the targeted ownership id, role, claimer, and candidate id against the active
ownership record before any active record moves to `closed`.
Failed executor closeout evidence is deliberately not treated as an ownership
failure by this mode; `fail-work-ownership` remains the explicit local failure
path. Accepted closeout-bound mutations append replayable
`work_ownership_mutation` audit records with `epoch_id`,
`executor_closeout_file`, `executor_closeout_checksum`, and
`executor_closeout_status: completed`.

Additional mutation blocker codes include `repo_branch_mismatch`,
`repo_head_mismatch`, `dirty_worktree`, `ownership_role_invalid`,
`ownership_claimer_invalid`, `ownership_claimer_mismatch`,
`ownership_head_mismatch`, `ownership_record_exists`,
`ownership_record_write_failed`, `ownership_closeout_unreadable`,
`ownership_closeout_invalid`, `ownership_closeout_schema_unsupported`,
`ownership_closeout_packet_invalid`, `ownership_closeout_epoch_missing`,
`ownership_closeout_not_completed`, `ownership_closeout_checksum_mismatch`,
`ownership_closeout_task_missing`, `ownership_closeout_task_unreadable`,
`ownership_closeout_task_invalid`,
`ownership_closeout_task_checksum_mismatch`,
`ownership_candidate_mismatch`, `ownership_role_mismatch`,
`ownership_epoch_mismatch`, and `audit_append_failed`. Bounded mutation
recommendations include `use_work_ownership_record`,
`close_or_fail_active_ownership`, `clean_worktree`, `inspect_repo_state`,
`fix_ownership_request`, `provide_ownership_record`, `repair_ownership_record`,
`inspect_runtime_state`, and `inspect_ownership_evidence`.

Status scans the requested repository, branch, and task scope; well-formed
records outside that scope are ignored, while malformed, unreadable, symlinked
registry or record-file paths, id/path mismatches, future timestamp, or failed
repo-inspection evidence still blocks with stable codes. Validation checks the
selected target record for active status and repo/branch/task mismatch when
requested. Duplicate active ownership is local evidence only. These records
are not distributed locks, do not assign roles, do not schedule agents, and do
not write GitHub issues. The ownership mutation commands do not start epochs,
invoke executors, create branches, commit, push, open or update PRs, merge,
release, or publish packages. `start-governed-execution` can deliberately
consume a supplied active ownership record and bind its `epoch_id`;
`resume-continuation` can deliberately consume supplied active ownership
evidence without mutating ownership or starting an epoch.

## Role Policy And Readiness

`role-policy.v1` is a local JSON policy packet that defines allowed ownership
role labels, bounded role capabilities, and review-separation requirements
before any future role assignment or agent-pool scheduler exists. A minimal
policy is:

```json
{
  "schema_version": "role-policy.v1",
  "roles": [
    {"role": "implementer", "capabilities": ["build", "modify_files"]},
    {"role": "reviewer", "capabilities": ["review", "comment"]}
  ],
  "review_separation": {
    "required": true,
    "builder_roles": ["implementer"],
    "reviewer_roles": ["reviewer"]
  }
}
```

The read-only role gate consumes local ownership records plus saved PR and
review-thread evidence:

```bash
agentic-cadence --root <runtime-root> role-readiness --cwd . --repo owner/repo --task-id task-1 --role-policy-file role-policy.json --pr-json-file pr.json --review-threads-file review-threads.json
```

It emits a `role-readiness.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "role-readiness.v1",
  "packet": "role_readiness",
  "read_only": true,
  "valid": false,
  "role_ready": false,
  "role_policy": {"schema_version": "role-policy.v1", "allowed_roles": []},
  "ownership": {"counts": {"active": 1}, "records": []},
  "review_evidence": {"actionable_review_authors": []},
  "role_summary": {"builder_claimers": [], "reviewer_claimers": []},
  "blockers": [{"code": "reviewer_evidence_missing", "message": "human readable"}],
  "recommended_next_action": "provide_reviewer_evidence",
  "side_effects": []
}
```

Stable role-readiness blocker codes include `role_policy_missing`,
`role_policy_unreadable`, `role_policy_schema_unsupported`,
`role_policy_invalid`, `pr_evidence_missing`, `pr_evidence_unreadable`,
`pr_evidence_invalid`, `pr_branch_mismatch`, `pr_head_mismatch`,
`pr_number_mismatch`, `review_thread_evidence_invalid`,
`ownership_role_unknown`, `builder_ownership_missing`,
`reviewer_evidence_missing`, and `review_separation_conflict`. Ownership and
repo blockers from
`work-ownership-status` are forwarded, including `ownership_stale`,
`ownership_head_mismatch`, `duplicate_active_ownership`,
`ownership_record_invalid`, `ownership_registry_state_invalid`,
`repo_branch_mismatch`, and `repo_inspection_failed`.
Recommended actions are limited to `use_role_readiness`,
`provide_role_policy`, `fix_role_policy_or_ownership`,
`refresh_ownership_evidence`, `claim_work_ownership`,
`provide_reviewer_evidence`, `assign_independent_reviewer`,
`refresh_pr_evidence`, `inspect_repo_state`, and
`inspect_role_readiness_blockers`.

Resolved or outdated review-thread comments are ignored for reviewer evidence
and cannot create same-claimer separation conflicts. Builder claimers that
appear in otherwise actionable review-thread comments are reported under
`ignored_builder_review_authors` and are not counted as reviewer evidence when
an independent reviewer author is present. `role-readiness` does not assign
roles, schedule agents, invoke review agents or paid review, call GitHub, post
comments, resolve review threads, update PRs, create branches, commit, push,
merge, release, or publish packages.

## Executor Invocation Readiness

`executor-invocation-readiness` is the read-only preflight packet before any
future real executor invocation. It consumes a reviewed
`generic-executor-task.v1` packet, active epoch evidence, active local
ownership evidence, task-carried command and branch policy, required checks,
an expected result path, and optional `role-readiness.v1` evidence:

```bash
agentic-cadence --root <runtime-root> executor-invocation-readiness --cwd . --task-file executor-task.json --epoch-id epoch-1 --ownership-target ownership-1 --expected-result-path <runtime-root>/executor-results/executor-result.json --role-readiness-file role-readiness.json
```

It emits an `executor-invocation-readiness.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "executor-invocation-readiness.v1",
  "packet": "executor_invocation_readiness",
  "read_only": true,
  "valid": false,
  "executor_invocation_ready": false,
  "executor_started": false,
  "task": {"checksum": "sha256:...", "id": "candidate-1"},
  "active_epoch": {"id": "epoch-1", "status": "ACTIVE"},
  "ownership": {"id": "ownership-1", "epoch_id": "epoch-1"},
  "role_readiness": {"present": true, "valid": true},
  "blockers": [{"code": "task_checksum_mismatch", "message": "human readable"}],
  "recommended_next_action": "refresh_task_evidence",
  "side_effects": []
}
```

Stable blocker codes include `task_file_unreadable`,
`executor_task_invalid`, `repo_inspection_failed`, `repo_path_invalid`,
`repo_path_mismatch`, `repo_branch_mismatch`, `repo_head_mismatch`,
`dirty_worktree`, `brake_state_invalid`, `brake_not_drive`,
`active_epoch_missing`, `active_epoch_conflict`, `active_epoch_invalid`,
`active_epoch_id_mismatch`, `active_epoch_status_invalid`,
`active_epoch_repo_mismatch`, `active_epoch_branch_mismatch`,
`active_epoch_task_missing`, `task_checksum_missing`,
`task_checksum_mismatch`, `ownership_record_missing`,
`ownership_record_unreadable`, `ownership_candidate_mismatch`,
`ownership_epoch_mismatch`, `ownership_head_mismatch`,
`duplicate_active_ownership`, `command_policy_invalid`,
`branch_policy_invalid`, `branch_policy_current_branch_main_disallowed`,
`required_checks_invalid`, `required_checks_missing`,
`result_path_missing`, `result_path_invalid`, `result_path_mismatch`,
`result_path_outside_runtime`, `role_readiness_unreadable`,
`role_readiness_invalid`, `role_readiness_scope_mismatch`, and
`role_readiness_blocked`.
Ownership validation blockers from `validate-work-ownership` are forwarded.

Recommended actions are limited to `invoke_real_executor`,
`refresh_task_evidence`, `fix_ownership`, `close_or_fail_active_epoch`,
`inspect_policy_blockers`, and `operator_review`. The success recommendation
is only readiness evidence for a future orchestrator; this command does not
start a real executor, emit executor process metadata, modify code, create
branches, commit, push, open or update PRs, merge, release, publish packages,
assign roles, schedule agents, or write GitHub state.

## Executor Invocation Plan

`executor-invocation-plan` is a read-only plan packet before any real executor
process start. It consumes a fresh successful
`executor-invocation-readiness.v1` packet, a local `operator-approval.v1`
approval for purpose `real_executor_invocation`, clean `audit-replay`
hash-chain evidence, `executor-adapter.v1` metadata, `executor-rollback.v1`
rollback evidence, an exact command string, environment allowlist, timeout,
cwd, active epoch and ownership anchors, and expected result path:

```bash
agentic-cadence --root <runtime-root> executor-invocation-plan --cwd . --readiness-file executor-invocation-readiness.json --approval-file operator-approval.json --approval-secret-env CADENCE_OPERATOR_APPROVAL_SECRET --adapter-file executor-adapter.json --rollback-file executor-rollback.json --command "python -m unittest tests.test_cadence" --env-allow PATH --timeout-seconds 300 --expected-result-path <runtime-root>/executor-results/executor-result.json
```

It emits an `executor-invocation-plan.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "executor-invocation-plan.v1",
  "packet": "executor_invocation_plan",
  "read_only": true,
  "valid": false,
  "executor_invocation_planned": false,
  "executor_started": false,
  "target_checksum": "sha256:...",
  "readiness": {"checksum": "sha256:..."},
  "approval": {"purpose": "real_executor_invocation"},
  "adapter": {"id": "local-python"},
  "rollback": {"checksum": "sha256:..."},
  "audit_chain": {"chain_head": "sha256:..."},
  "command": {"command": "python -m unittest tests.test_cadence"},
  "blockers": [{"code": "readiness_packet_stale", "message": "human readable"}],
  "recommended_next_action": "operator_review",
  "side_effects": []
}
```

The approval target checksum is computed from an
`executor-invocation-target.v1` descriptor that binds the readiness checksum,
adapter checksum, rollback checksum, command, cwd, expected result path,
environment allowlist, timeout, and current audit-chain head. File anchors
persisted into the plan are absolute local paths, so later invocation and
closeout replay do not depend on the operator's current directory.

Stable blocker codes include `readiness_unreadable`,
`readiness_packet_stale`, `readiness_not_invocable`, `task_file_unreadable`,
`executor_task_invalid`, `task_checksum_mismatch`, `approval_missing`,
`approval_invalid`, `approval_schema_invalid`, `approval_target_invalid`,
`approval_target_mismatch`, `approval_expired`, `approval_purpose_missing`,
`approval_purpose_mismatch`, `approval_identity_invalid`,
`approval_timestamp_invalid`, `approval_window_too_long`,
`approval_issued_in_future`, `approval_signature_invalid`,
`audit_chain_not_clean`, `rollback_evidence_missing`,
`rollback_policy_invalid`, `adapter_contract_invalid`,
`executor_command_denied`, `executor_timeout_invalid`,
`repo_inspection_failed`, `repo_path_mismatch`, `repo_branch_mismatch`,
`repo_head_mismatch`, `dirty_worktree`, `brake_state_invalid`,
`brake_not_drive`, `active_epoch_missing`, `active_epoch_conflict`,
`active_epoch_invalid`, `active_epoch_mismatch`,
`active_epoch_repo_mismatch`, `active_epoch_branch_mismatch`,
`active_epoch_task_missing`, `active_epoch_task_duplicate`,
`active_epoch_task_completed`, `task_checksum_missing`,
`ownership_record_missing`, `ownership_record_unreadable`,
`ownership_record_path_invalid`, `ownership_record_outside_registry`,
`ownership_record_ambiguous`, `ownership_record_invalid`,
`ownership_schema_unsupported`, `ownership_required_field_missing`,
`ownership_field_type_invalid`, `ownership_id_invalid`,
`ownership_id_mismatch`, `ownership_status_invalid`,
`ownership_state_mismatch`, `ownership_timestamp_invalid`,
`ownership_stale`, `ownership_closed`, `ownership_repo_mismatch`,
`ownership_branch_mismatch`, `ownership_task_mismatch`,
`ownership_candidate_mismatch`, `ownership_epoch_mismatch`,
`ownership_head_mismatch`, `duplicate_active_ownership`,
`result_path_missing`, `result_path_mismatch`,
`result_path_outside_runtime`, and `result_path_invalid`.

Recommended actions are limited to `invoke_real_executor` and
`operator_review`. This command is read-only: it does not start a real executor,
append audit records, emit process metadata, modify code, create branches,
commit, push, open or update PRs, merge, release, publish packages, assign
roles, schedule agents, or write GitHub state.

## Real Executor Invocation

`invoke-real-executor` is the controlled process-start boundary after
`executor-invocation-plan`. It consumes a fresh successful
`executor-invocation-plan.v1` packet and an operator approval secret, then
re-runs the repository, brake, epoch, ownership, approval, audit-chain,
rollback, timeout, command-policy, and result-path gates immediately before
starting any process:

```bash
agentic-cadence --root <runtime-root> invoke-real-executor --plan-file executor-invocation-plan.json --approval-secret-env CADENCE_OPERATOR_APPROVAL_SECRET --side-effect-mode evidence_only
```

It emits and writes a `real-executor-invocation.v1` packet shaped as:

```json
{
  "protocol_version": "v1",
  "schema_version": "real-executor-invocation.v1",
  "packet": "real_executor_invocation",
  "valid": false,
  "executor_started": true,
  "timed_out": false,
  "invocation_id": "real-executor-invocation-...",
  "side_effect_mode": "evidence_only",
  "invocation_cwd": "/repo",
  "plan_file": "/absolute/path/executor-invocation-plan.json",
  "plan_checksum": "sha256:...",
  "plan_target_checksum": "sha256:...",
  "rechecked_plan_checksum": "sha256:...",
  "command": {
    "command": "python ...",
    "cwd": "/repo",
    "timeout_seconds": 300,
    "expected_result_path": "<runtime-root>/executor-results/executor-result.json"
  },
  "process": {"exit_code": 0, "timed_out": false},
  "result_file": "<runtime-root>/executor-results/executor-result.json",
  "result_evidence_checksum": "sha256:...",
  "stdout_log": "<runtime-root>/real-executor-invocations/<id>.stdout.log",
  "stderr_log": "<runtime-root>/real-executor-invocations/<id>.stderr.log",
  "record_file": "<runtime-root>/real-executor-invocations/<id>.json",
  "repository_before": {
    "head": "abc...",
    "dirty_worktree": false,
    "local_branch_refs": {"main": "abc..."}
  },
  "repository_after": {
    "head": "abc...",
    "dirty_worktree": false,
    "local_branch_refs": {"main": "abc..."}
  },
  "rollback": {"checksum": "sha256:..."},
  "audit_chain": {"chain_head": "sha256:..."},
  "blockers": [{"code": "executor_result_missing", "message": "human readable"}],
  "side_effects": ["real_executor_process_started", "stdout_stderr_captured", "real_executor_invocation_record_written"]
}
```

The command starts exactly one approved command with `shell=False`, explicit
cwd, bounded environment allowlist, approved timeout, and stdout/stderr capture
to runtime-owned log files. It records process exit status, timeout status,
`command.expected_result_path`, `result_file`, `result_evidence_checksum`,
before/after repository snapshots including `local_branch_refs`, rollback
evidence checksum, result path, output log paths, invocation cwd, absolute plan
file path, plan checksum, and the audit-chain head used by the immediate
pre-start recheck.

`--side-effect-mode evidence_only` requires the target repository to remain
clean after invocation. `--side-effect-mode materialized_changes` allows a
dirty worktree only when the executor result includes verified
`materialized_change_evidence`; Cadence records the dirty-worktree evidence and
an invocation-time dirty-worktree fingerprint checksum, but does not commit,
push, open PRs, resolve threads, merge, release, publish packages, assign
roles, schedule agents, claim distributed locks, or write GitHub state.
Added, removed, or retargeted local branch refs are recorded in
`local_branch_refs` and fail as `unexpected_repo_modification` in both
side-effect modes.

Stable blockers include `plan_packet_stale`, `plan_not_invocable`,
`approval_recheck_failed`, `rollback_evidence_missing`,
`rollback_recheck_failed`, `brake_not_drive`, `active_epoch_mismatch`,
`runtime_root_unsafe`, `repo_inspection_failed`, `executor_process_timeout`,
`executor_process_failed`, `executor_result_stale`, `executor_result_missing`,
`unexpected_repo_modification`, `materialized_change_evidence_missing`, and
`audit_append_failed`.
Immediate pre-start rechecks can also forward `executor-invocation-plan`
blockers such as `repo_head_mismatch`, `active_epoch_missing`, and
`executor_command_denied`.
Before-start blockers do not write a real executor invocation record. Once the
process starts, Cadence writes the local record even when timeout, missing
result evidence, or side-effect-mode blockers make the invocation invalid. When
result evidence is present, the record includes the invocation-time
`result_evidence_checksum` that closeout later rechecks before epoch mutation.
Materialized-change records also include `worktree_fingerprint_checksum` under
`materialized_change_evidence`, and closeout recomputes it from the live dirty
worktree so same-file post-invocation edits fail closed.
The command also appends a `real_executor_invocation_record` audit event whose
`invocation_record_checksum` anchors the just-written invocation JSON to the
pre-run audit-chain head.
If that post-run audit append fails, the command emits a structured blocked
payload with `real_executor_invocation_audit_append_failed`, rewrites the local
record as blocked when possible, reports `invocation_record_write_failed` if
the blocked rewrite fails, and attempts a
`record_real_executor_invocation_blocked` audit event only after the blocked
payload rewrite succeeds so callers do not retry a real process that already
ran.

`real-executor-invocation.v1` records can be supplied to
`closeout-executor-result --real-invocation-file` after result evidence is
written. Closeout revalidates the canonical invocation path, invocation id,
plan checksum, readiness task checksum, active epoch id, active ownership
binding, repository before/after anchors, current repo state, snapshot-after
cwd/branch/head/dirty-worktree state, materialized change evidence, result
validation, invocation-time result checksum, the audited invocation record
checksum, and audit-chain continuity before mutating epoch state. Accepted real
invocation evidence updates the same invocation record with `closeout_status`,
epoch id/status, closeout checksum, result checksum, validation checksum, and
snapshot-after checksum, then appends an
`update_real_executor_invocation_closeout` audit event whose
`invocation_record_checksum` anchors the updated record. This binding can complete or
fail the existing epoch path and may embed a dry-run Git/PR plan, but it still
does not commit dirty worktree changes, create branches, push, call GitHub,
open PRs, resolve threads, merge, release, publish packages, assign roles,
schedule agents, or claim distributed locks. Stable real-invocation closeout
blockers include `invocation_record_missing`, `invocation_checksum_mismatch`,
`invocation_epoch_mismatch`, `invocation_result_missing`,
`invocation_result_invalid`, `materialized_change_mismatch`,
`audit_chain_mismatch`, `ownership_closeout_blocked`,
`run_record_audit_append_failed`, `real_invocation_audit_append_failed`, and
`closeout_audit_append_failed`;
blocked real-run closeout recommends `inspect_real_run_blockers`, while
post-mutation audit append failures recommend `recover_closeout_audit`.

## Handoff Signature

Draft marker:

```text
<!-- codex-handoff:v1 id=<id> status=READY sha=<sha> -->
```

The marker must include:

- protocol version
- handoff id
- status
- checksum or signature for the handoff body

## Task Sizing and Epoch Governance

Long-running work is governed by task estimates and epochs. New handoffs must declare `--task-type` so pickup policy cannot be bypassed by omitting task sizing. Estimates classify work as `execution` or `discovery`, assign a duration bucket, track uncertainty, and map the task to pickup policy. Epochs bound recursive work with task and time budgets, repo snapshots, epoch health, repo confidence, and self-check decisions.

`snapshot-repo` writes the snapshot path in its JSON output. Snapshot packets must include `readiness_evidence` with `source: local_git`, `freshness: local_only`, `live: false`, `stale: false`, and limitations that make clear open PRs and review threads were not fetched. Snapshot validators must reject missing or malformed local readiness evidence at epoch and self-check boundaries. `start-epoch` requires `--repo`, `--branch`, and `--snapshot-before-file`, and validates that the pre-epoch snapshot matches them. It records the explicit task list from `--tasks-file`; when that file is omitted, the epoch is an empty administrative checkpoint. It enforces the active epoch policy, including `max_tasks_per_epoch` and `max_discovery_tasks_per_epoch`. Any task recorded in an epoch must declare `task_type` as `execution` or `discovery`. Only one active epoch may exist in a runtime root; use `complete-epoch` or `fail-epoch` before starting another.

`self-check` at an epoch boundary should receive `--snapshot-after-file` from a fresh `snapshot-repo` run; use `--ci-status green` only when CI is known green. `CONTINUE` is allowed only when Cadence is `PLAY_ON` (legacy brake is `DRIVE`), exactly one active epoch exists, the self-check is grounded in that epoch with a valid pre-epoch snapshot and a valid current snapshot, current repo confidence is not `low`, uncertainty is not `high`, epoch health is not `degraded`, the epoch is within `max_minutes_per_epoch`, completed epoch history has not reached `max_epochs_without_user_approval`, CI satisfies `next_epoch_requires`, and the next elected task fits the stored epoch policy. Medium uncertainty or `watch` epoch health shrink the next election to one task. Current snapshot confidence wins over ad hoc CLI confidence flags. Candidate election is capped by the stored epoch task and discovery limits.

Use `snapshot-repo` to record the repository state before an epoch and again before continuation. Use `start-epoch` to begin one bounded work interval from a repo snapshot and optional explicit task list. Use `self-check` at each epoch boundary to decide whether work may continue, must pause, or must stop. `complete-epoch --decision CONTINUE` requires a persisted `CONTINUE` self-check for the same active epoch, policy, baseline snapshot, current snapshot record, and completed-epoch count. The default `max_epochs_without_user_approval` value allows one automatic continuation before the next boundary must ask for approval or hand off.

At claim time, handoffs without persisted estimates and malformed estimated handoffs fail closed instead of being claimed. Persisted estimates are checked against the canonical task sizing model and their estimate binding checksum before pickup policy is trusted. Approval-gated handoffs require a separate `approve-handoff` record bound to the handoff checksum and estimate checksum; `pickup_approved=true` metadata is ignored. Handoffs with an estimate but without an estimate binding must be re-created or explicitly migrated before pickup.

## Candidate Discovery

Candidate discovery is read-only. It may inspect local repo state, known failures, review findings files, text markers, and proposal allowance, but it must not start epochs, claim handoffs, modify files, commit, push, or merge.

`discovery_mode: off` is focused execution mode. It suppresses candidate discovery and proposal surfacing so automation remains inside the approved task or handoff boundary.

The repo-local `docs/cadence/business-memory.md` file is an optional Candidate Discovery source. It must be a single clean tracked regular file; dirty, untracked, symlinked, conflicted, or otherwise non-regular entries are rejected before parsing. Entries discovered from it must produce candidates with `source: business_memory`, `maturity: discovery`, `classification`, and `classification_confidence`. Classification must use exactly these taxonomy values: `direction`, `business_rule`, `problem`, `feature`, `nice_to_have`, `risk`, `constraint`, and `unknown`. Optional business-memory `Status` values are exactly `active`, `fulfilled`, and `superseded`; fulfilled and superseded entries remain durable memory but must not be emitted or elected as candidates. `Fulfilled By` or `Superseded By` also closes an entry when `Status` is omitted; entries without status or closure metadata remain active for legacy compatibility. When a meaningful signal cannot be mapped confidently, the candidate must use `classification: unknown` and include `unclassified_signal` as a driver. Business-memory-only initial candidates must keep `repo_anchors: []` until code, docs, or test repo anchors are discovered. Initial discovery traceability must come from `evidence.path`, `evidence.line`, and `evidence.heading` pointing to `docs/cadence/business-memory.md`. `--max-business-memory-candidates` limits how many business-memory candidates may be surfaced or elected.

Business-memory candidates are discovery-only. They can inform the election pool, but they must not directly start execution, modify files, commit, push, merge, or bypass task sizing, repo snapshots, Cadence state checks, self-check, or governance policy. `discovery_mode: off` must suppress business-memory candidates along with marker, maintenance, product-evolution, and synthetic proposal work.

Proposal allowance has three modes: `none`, `surface`, and `elect`. `elect` allows proposals into the election pool, but synthetic proposals remain discovery-first and cannot bypass task sizing, snapshots, Cadence state checks, self-check, or governance policy.

Reviewer and CI feedback may enter candidate discovery through local evidence
files. `--pr-json-file` reads saved PR JSON with `statusCheckRollup` and
converts failed check runs or failed status contexts into `pr_check_failure`
execution candidates. `--review-findings-file` reads the existing normalized
JSON list of findings. `--review-threads-file` reads saved GitHub GraphQL
`reviewThreads` JSON with completed `pageInfo`, `isResolved`, `isOutdated`,
and comment `outdated` status fields, then converts actionable unresolved,
current review comments into the same `review_finding` candidate shape.
Review-thread candidates must preserve source PR number and URL when present,
thread id, comment id or grouped comment ids, path, line, author, saved
freshness label, and target file evidence. Duplicate comments on the same
thread/file/line follow-up target may share one candidate with an occurrence
count and merged comment ids. Candidate ingestion uses saved feedback as local
work-discovery input and must fail closed when review-thread evidence is
malformed or incomplete. This ingestion must stay deterministic and local: it
must not call GitHub, trust PR body text, include resolved or outdated threads,
assume missing status fields are current, include non-actionable summaries such
as walkthroughs or no-actionable-comments reports, or bypass repo-relative path
validation.

## Loop Tick

`loop-tick` is the Phase 1 read-only loop-controller command. It must check Cadence state, capture and persist a local repo snapshot, run deterministic candidate discovery with election enabled, and emit one JSON packet describing the next governed action. It must not start an executor, start or complete an epoch, create or update a branch, commit, push, create or update a pull request, spend review, merge, release, or publish.

The packet must include the brake and Cadence state, the persisted snapshot, the candidate-discovery packet, the elected candidates, `read_only: true`, `executor_started: false`, `epoch_started: false`, `pr_action_started: false`, and a `recommended_next_action`. Phase 1 recommended actions are `blocked` when Cadence state disallows work, `approval_required` when local repo confidence is low, `no_candidates` when election returns no candidate, `requires_executor_contract` when a candidate is available but Cadence has not emitted an executor task packet, `approve_executor_task` when an executor task packet has been emitted for operator approval, and `policy_denied` when a supplied local loop policy blocks the requested executor-task bounds. Low local repo confidence takes precedence over empty election and includes dirty worktrees, unborn or detached HEAD, known failures, and an operator-supplied red CI signal. This command is not a continuous runner; repeated ticks require an external operator or orchestrator.

When `--policy-file` is supplied, it must be local JSON with `schema_version: cadence-loop-policy.v1`. The initial policy shape supports `allowed_paths`, `denied_paths`, `allowed_commands`, `denied_commands`, `required_checks`, `max_executor_time_minutes`, `stop_conditions`, and an optional `branch_policy` object. Policy paths are repo-relative. Policy `allowed_paths` and max runtime provide defaults and caps for emitted executor task packets. Policy `allowed_commands` and `denied_commands` are copied into the emitted task packet `command_policy` so later result validation can reject commands outside the allowlist or matching the denylist. Policy `branch_policy` is copied into emitted executor task packets so later Git/PR planning checks the approved branch bounds instead of trusting a mutable policy file. The branch policy supports only `allowed_base_branches`, `denied_target_branches`, `required_branch_prefixes`, and `allow_current_branch_main`; unknown branch-policy fields are rejected. When no branch policy is supplied, existing no-policy behavior remains permissive for dry-run planning; when a `branch_policy` object is supplied, omitted list fields default to empty lists and omitted `allow_current_branch_main` preserves that permissive default. Built-in safety stops `brake_not_drive`, `operator_stop`, `context_pressure`, and `timeout` must always be retained. Policy `required_checks` and `stop_conditions` must always be retained, and requested CLI checks or stop conditions are additive after de-duplication. Requested executor allowed paths must stay inside policy `allowed_paths` and must not overlap `denied_paths`; requested runtime must not exceed `max_executor_time_minutes`; malformed requested allowed paths or malformed branch policy fail closed before a task packet is emitted. A policy denial must emit a `policy_denied` loop packet, require operator attention, and avoid emitting an executor task.

Each root-backed `loop-tick` must append a compact `cadence-audit.v1` record to `<root>/audit/events.jsonl` and include an `audit_record` reference in the returned packet. The audit record binds the decision to the tick id, recommended action, reason, repo, branch, head, snapshot id, optional executor task id, operator-confirmation flag, and a checksum of the emitted packet before the audit reference is added. New audit appends also add `audit_chain_version: cadence-audit-chain.v1`, a physical-line `chain_index`, the `previous_event_hash`, and the record `event_hash`; the returned `audit_record` reference includes the same chain metadata.
When the operator omits `--repo`, the loop-decision audit record uses the
resolved snapshot `cwd` as the local repo anchor so replay can still validate
the compact record.

## Loop Run Planning And Controlled Start Composition

`loop-run-plan` is a read-only wrapper around the same loop decision path used
by `loop-tick`. It emits `loop-run-plan.v1` with planned next steps and explicit
non-start flags for runner, executor, epoch, PR, GitHub, merge, release,
package publication, role assignment, scheduling, and loop continuation. When
an executor task is emitted, the packet includes the executor task checksum as
an approval target for a later operator-approved execution-start gate. It does
not emit an approval token, append audit records, mutate runtime state, start a
runner, start an executor, start an epoch, call GitHub, or write Git state.

`controlled-loop-start` composes an already saved `loop-run-plan.v1` packet and
an already produced `execution-start.v1` packet into
`controlled-loop-start.v1`. The command reads
`--loop-run-plan-file` and `--execution-start-file`, requires matching packet
and schema values, requires the loop plan to carry an executor task checksum
and `recommended_next_action: request_operator_approval`, validates the
embedded generic executor task packet, requires the execution start to be valid
with `approval_state: approved`, `epoch_started: true`, and
`executor_started: false`, and verifies that the execution-start task checksum
and task id match the executor task embedded in the loop plan. It also rechecks
the runtime root for the matching active epoch and the prior
`execution_start_decision` audit record whose payload checksum binds the
supplied `execution-start.v1` packet. It rejects loop-plan or execution-start
evidence that reports runner, executor, PR, GitHub, merge, release,
package-publication, role-assignment, scheduling, or loop-continuation side
effects.

A completed packet uses `packet: controlled_loop_start`,
`controlled_start_status: completed`, `read_only: true`, and
`recommended_next_action: plan_executor_invocation`. The top-level response
envelope includes `packet`, `schema_version`, `controlled_start_status`,
`read_only`, `valid`, `recommended_next_action`, `loop_run_plan_checksum`,
`execution_start_checksum`, `executor_task_checksum`, `task_id`, `epoch_id`,
the nested `loop_run_plan` evidence, the nested `execution_start` evidence,
`blockers`, and explicit false side-effect flags. The nested `loop_run_plan`
contains the embedded `generic-executor-task.v1` packet plus its checksum and
loop metadata. The nested `execution_start` contains `approval_state`,
`epoch_started`, `executor_started`, `task_id`, `task_checksum`, `epoch_id`, and
the `audit_record` reference written by `start-governed-execution`.

Blocked packets use `controlled_start_status: blocked`, `valid: false`, stable
blockers, and exit code 2. A task-anchor mismatch recommends
`recreate_execution_start`; malformed or not-ready loop plans recommend
`regenerate_loop_run_plan`; invalid execution-start evidence, including missing
active epoch or audit binding, recommends `inspect_execution_start`; missing or
wrong packet evidence recommends `refresh_controlled_start_evidence`.
Completed and blocked `controlled-loop-start` packets append no audit record;
the command is read-only composition evidence only. Stable blocker codes include
`loop_run_plan_evidence_missing`, `execution_start_evidence_missing`,
`controlled_start_packet_mismatch`, `loop_run_plan_not_ready`,
`execution_start_invalid`, and `execution_start_task_mismatch`.

`controlled-loop-start` must not continue the loop, start a runner, start or
retry an executor, start another epoch, create branches, commit, push, call
GitHub, create or update pull requests, resolve review threads, merge, release,
publish packages, assign roles, schedule agents, claim distributed locks, or
rewrite the supplied plan or execution-start records.

`controlled-loop-invocation-plan` composes an already saved
`controlled-loop-start.v1` packet with saved `executor-invocation-readiness.v1`
and `executor-invocation-plan.v1` packets into
`controlled-loop-invocation-plan.v1`. The command reads
`--controlled-loop-start-file`, `--readiness-file`, and
`--invocation-plan-file`; requires matching packet and schema values; requires
the controlled start to be completed with
`recommended_next_action: plan_executor_invocation`; requires readiness to be
valid, read-only, executor-ready, and side-effect-free; and requires the
invocation plan to be valid, read-only, executor-planned, side-effect-free, and
waiting at `recommended_next_action: invoke_real_executor`.

The command rechecks that the controlled start task id, executor task checksum,
and epoch id match the readiness task and active-epoch anchors. It also rechecks
that the invocation plan's readiness file/checksum and target readiness
checksum match the supplied readiness packet, and that the invocation plan
target checksum matches its target payload. A completed packet uses
`packet: controlled_loop_invocation_plan`,
`controlled_invocation_plan_status: completed`, `read_only: true`,
`valid: true`, and `recommended_next_action: invoke_real_executor`. The
top-level response envelope includes `controlled_loop_start_checksum`,
`readiness_checksum`, `invocation_plan_checksum`, `task_id`, `epoch_id`,
`target_checksum`, nested copies of the three supplied packets, `blockers`, and
explicit false side-effect flags.

Blocked packets use `controlled_invocation_plan_status: blocked`,
`valid: false`, stable blockers, and exit code 2. Invalid readiness or
controlled-start/readiness anchor mismatch recommends
`refresh_executor_invocation_readiness`; invalid invocation-plan evidence or
invocation-plan/readiness mismatch recommends
`recreate_executor_invocation_plan`; invalid controlled-start evidence
recommends `recreate_controlled_loop_start`; missing or wrong packet evidence
recommends `refresh_controlled_invocation_evidence`. Stable blocker codes
include `controlled_loop_start_evidence_missing`,
`readiness_evidence_missing`, `invocation_plan_evidence_missing`,
`controlled_invocation_packet_mismatch`, `controlled_start_invalid`,
`readiness_not_invocable`, `invocation_plan_not_invocable`,
`controlled_start_readiness_mismatch`, and
`invocation_plan_readiness_mismatch`.

Completed and blocked `controlled-loop-invocation-plan` packets append no audit
record and must not continue the loop, start a runner, start or retry an
executor, start an epoch, create branches, commit, push, call GitHub, create or
update pull requests, resolve review threads, merge, release, publish packages,
assign roles, schedule agents, claim distributed locks, or rewrite the supplied
controlled-start, readiness, or invocation-plan records.

`audit-replay` is the read-only local audit verification command. It reads
`<root>/audit/events.jsonl`, emits an `audit-replay.v1` packet, and exits
nonzero when the audit log contains malformed, corrupt, unsupported, or
hash-chain-invalid records. Missing or empty audit logs are valid zero-record
packets for a fresh runtime root; they are not evidence that older audit
history was preserved and recommend `start_new_audit_chain`.

Replay validates only the compact `cadence-audit.v1` record shape, supported
event names, event-specific required fields, physical JSONL line counts, and
`sha256:` checksum syntax for compact packet checksums. Replay also validates
`cadence-audit-chain.v1` metadata when present: `event_hash` is the canonical
`sha256:` checksum of the JSON audit record excluding the `event_hash` field
itself, `previous_event_hash` must match the prior valid record's computed
chain head, and `chain_index` must be unique and match the physical JSONL line.
Supported events are `loop_tick_decision`, `executor_fixture_invocation`,
`execution_run_record`, `executor_result_validation`,
`executor_epoch_closeout`, `execution_start_decision`,
`git_pr_materialization_intent`, `git_pr_materialization_result`,
`git_pr_dirty_commit_materialization_intent`,
`git_pr_dirty_commit_materialization_result`,
`review_response_materialization_intent`,
`review_response_materialization_result`,
`review_thread_resolution_intent`, `review_thread_resolution_result`,
`operator_approval_verification`, `controlled_loop_tick`,
`controlled_pr_cycle`, and
`work_ownership_mutation`. It does not
recompute `payload_checksum`,
`run_record_checksum`,
`task_packet_checksum`, `result_evidence_checksum`,
`validation_packet_checksum`, `plan_checksum`, `snapshot_after_checksum`, or
`ownership_record_checksum`
from original packet bodies because those bodies are not stored in the compact
audit log.

Legacy records without chain metadata are valid only before chained records and
replay as explicit chain roots. The `audit-replay.v1` packet reports
`audit_chain_version`, `chain_head`, `chain_records`, and
`legacy_chain_roots`. Clean fully chained history recommends
`use_audit_replay_evidence`; clean legacy-root history recommends
`continue_with_legacy_chain_root`.

Invalid packets include stable blocker codes such as
`audit_line_invalid_json`, `audit_record_not_object`,
`audit_schema_version_unsupported`, `audit_event_unsupported`,
`audit_required_field_missing`, `audit_checksum_invalid`,
`audit_chain_missing`, `audit_chain_broken`, `audit_event_hash_mismatch`,
`audit_chain_index_duplicate`, and `unsupported_audit_chain_record`. The
command uses `recommended_next_action: "upgrade_cadence"` only when every
blocker is an unsupported schema, event, or chain record; chain integrity
blockers recommend `repair_audit_history`; corruption, malformed records,
unreadable files, decode failures, or mixed unsupported/corrupt history
recommend `inspect_audit_log`.

`audit-replay` has no target repository `cwd`, so the dispatcher resolves the
runtime root and applies the runtime-root location safety guard without running
repo-cwd safety checks. The command must not append audit records, repair files,
run an executor, start or complete epochs, create branches, commit, push, open a
pull request, spend review, merge, or treat clean replay evidence as approval to
execute work.

## Operator Approval Identity Evidence

`verify-operator-approval` is the local verifier for reusable
`operator-approval.v1` evidence. It consumes `--approval-file`,
`--target-checksum`, `--purpose`, and a local HMAC secret from
`CADENCE_OPERATOR_APPROVAL_SECRET` by default, or from the named
`--approval-secret-env` variable. `--approval-secret` is reserved for explicit
local checks and tests. The command verifies an `hmac-sha256:` signature over
the approval fields and emits `operator-approval-verification.v1`. Supported purposes are
`start_governed_execution`, `git_pr_materialization`,
`real_executor_invocation`, `release`, and `package_publication`.

An `operator-approval.v1` packet must include `target_checksum`, `purpose`,
`operator_id`, `key_id`, `issued_at`, `expires_at`, and `signature`. The
verifier rejects unreadable or non-object approval packets, wrong schema,
malformed or mismatched target checksums, missing or unsupported purposes,
missing operator identity, weak key ids, malformed or reversed timestamps,
validity windows longer than 60 minutes, expired approvals, future-issued
approvals, missing verification secret, invalid signatures, and audit append
failures with stable blockers: `operator_approval_file_unreadable`,
`operator_approval_invalid`, `operator_approval_schema_invalid`,
`operator_approval_target_invalid`, `operator_approval_target_mismatch`,
`operator_approval_purpose_missing`, `operator_approval_purpose_mismatch`,
`operator_approval_operator_missing`, `operator_approval_key_id_weak`,
`operator_approval_timestamp_invalid`, `operator_approval_window_too_long`,
`operator_approval_expired`, `operator_approval_issued_in_future`,
`operator_approval_secret_missing`, `operator_approval_signature_invalid`, and
`operator_approval_audit_append_failed`.

Accepted verification appends an `operator_approval_verification` audit record
with `operator_id`, `key_id`, `purpose`, `target_checksum`,
`approval_checksum`, `issued_at`, `expires_at`, `checked_at`,
`signature_verified: true`, and
`approval_schema_version: operator-approval.v1`. The command returns
`executor_started: false`, `epoch_started: false`, and
`pr_action_started: false`; accepted approval evidence is not executor,
GitHub, merge, release, or package-publication authority.

## Generic Executor Contract

The generic executor contract is an agent-neutral boundary, not a named host adapter. `loop-tick --emit-executor-task` may include an `executor_task` packet with `schema_version: generic-executor-task.v1`, task identity, task type `execution` or `discovery`, bucket `XS`, `S`, `M`, `L`, or `XL`, repo name, absolute repo path, branch/head snapshot, allowed repo-relative paths, command policy, branch policy, required checks, positive time/task limits, stop conditions, an absolute expected result-evidence path, and permissions that forbid commit, push, PR creation, merge, release, and package publication. Task-packet validation must validate the embedded local repo snapshot, require non-empty repo identity, require absolute local cwd/path anchors, require snapshot repo/cwd/branch/head to match the task packet repo anchor, require the built-in safety stop conditions, validate optional `command_policy.allowed_commands` and `command_policy.denied_commands` string lists, validate task-carried `branch_policy` string lists and boolean shape, reject relative expected result-evidence paths, reject dirty snapshots, and reject low-confidence snapshots. Emitting this packet must set `executor_started: false`; Cadence must not execute the task from `loop-tick`.

`start-governed-execution` is the explicit execution-start gate for a reviewed
`generic-executor-task.v1` packet. It reads `--task-file`, validates the task
packet shape, including task-carried command and branch policy fields, requires
an exact approval token shaped as
`approve-executor-task:<task-packet-checksum>`, then rechecks the current repo
path, branch, `HEAD`, dirty-worktree state, repo confidence, active brake, and
active epoch state before mutating runtime state. When `--ownership-target` is
supplied, it then rechecks the active local `work-ownership.v1` record for task
id, candidate id, role, claimer, repo, branch, `HEAD`, duplicate active
ownership, freshness, malformed evidence, and registry path safety before
epoch mutation. A valid decision starts one active epoch with one task derived
from the approved task packet and `max_tasks_per_epoch: 1`, binds the started
epoch id back to supplied ownership evidence when present, emits
`schema_version: execution-start.v1`, appends an `execution_start_decision`
audit record, and reports `executor_started: false` plus
`pr_action_started: false`. The approval token is checksum-bound review
evidence only; it is not an authenticated approver identity or permission to
invoke a real executor.

`execution-start.v1` packets include `read_only: false`, `valid`,
`epoch_started`, `executor_started: false`, `approval_state`, `task_file`,
`task_checksum`, repo and snapshot evidence, optional `ownership` evidence,
optional `side_effects`, `blockers`, `recommended_next_action`, and
`limitations`. Ownership side effects are limited to
`work_ownership_epoch_bound` and `work_ownership_epoch_binding_rollback`.
Stable blocker codes include
`task_file_unreadable`, `executor_task_invalid`, `operator_approval_missing`,
`operator_approval_mismatch`, `repo_path_mismatch`,
`repo_inspection_failed`, `repo_branch_mismatch`, `repo_head_mismatch`,
`dirty_worktree`, `repo_confidence_low`, `brake_state_invalid`,
`brake_not_drive`, `active_epoch_exists`, `active_epoch_invalid`,
`epoch_start_failed`, `audit_append_failed`, `epoch_rollback_failed`,
`ownership_record_missing`, `ownership_closed`, `ownership_stale`,
`duplicate_active_ownership`, `ownership_record_invalid`,
`ownership_schema_unsupported`, `ownership_required_field_missing`,
`ownership_field_type_invalid`, `ownership_record_unreadable`,
`ownership_record_path_invalid`, `ownership_record_outside_registry`,
`ownership_record_ambiguous`, `ownership_registry_state_invalid`,
`ownership_repo_mismatch`, `ownership_branch_mismatch`,
`ownership_task_mismatch`, `ownership_candidate_mismatch`,
`ownership_role_mismatch`, `ownership_claimer_mismatch`,
`ownership_head_mismatch`, `ownership_record_write_failed`, and
`ownership_rollback_failed`.
Recommendation values include `handoff_to_executor`,
`fix_executor_task_packet`, `approve_executor_task`, `clear_brake`,
`close_or_fail_active_epoch`, `claim_work_ownership`,
`close_or_fail_active_ownership`, `repair_ownership_record`,
`refresh_ownership_evidence`, `inspect_runtime_state`, and
`recreate_executor_task`.

The command must not launch a new session, invoke a real executor, modify code,
create branches, commit, push, call GitHub, create or update pull requests,
merge, release, or publish packages. Blocked packets must not intentionally
leave an active epoch or append the success-only execution-start audit record.
If audit append fails after epoch creation, the command must emit
`audit_append_failed`; successful rollback reports `epoch_started: false`, and a
failed rollback adds `epoch_rollback_failed` with active epoch evidence. If
ownership was bound before the audit failure, successful rollback restores the
active ownership record and emits `work_ownership_epoch_binding_rollback`;
failed ownership rollback adds `ownership_rollback_failed`. Execution-start
audit records include `ownership_id` and `ownership_record_checksum` when
ownership evidence was supplied.

Executor result evidence uses `schema_version: generic-executor-result.v1` and `packet: executor_result`. It must include executor id, start/end timestamps, status `succeeded`, `failed`, `blocked`, or `stopped`, files changed, commands run, validation results, summary, confidence, blockers, dirty-worktree status, and resulting head SHA for successful results. `validate-executor-result` reads a task packet and result evidence from local JSON files and emits an `executor_result_validation` packet. Successful evidence must include command and validation evidence, must show every task-packet `required_checks` entry in both `commands_run` with exit code `0` and `validation_results` with matching `command` and `status: passed`, and all validation results in successful evidence must pass. Result evidence must stay within the task packet's max runtime based on `started_at` and `ended_at`, and the supplied result file must match the task packet's absolute `expected_output.evidence_path`. Result evidence must respect disabled permissions: it rejects reported `git commit`, `git push`, `gh pr create`, `git merge`, `gh pr merge`, release, or package-publication invocations while those permissions are false, including absolute-path, common git/gh global-option, git shell-alias, compound-command, shell-grouping, command-substitution, and shell-wrapper forms, and it rejects head changes when commits are forbidden. Release and package-publication guards cover `gh release create`, `gh release upload`, mutating `git tag` forms such as tag creation or deletion while allowing read-only listing/verification, `twine upload`, Python launcher `-m twine upload` forms including `python`, `python3`, versioned `python3.x`, and `py`, plus `npm publish`, `pnpm publish`, `yarn publish`, `yarn npm publish`, option-bearing `poetry publish` and `uv publish`, `hatch publish`, and `flit publish`. Result evidence must also respect task `command_policy`: any effective command segment matching `denied_commands` is invalid, and when `allowed_commands` is non-empty every effective command segment from direct compound commands, shell grouping, command substitutions, and shell-wrapper payloads must match that allowlist. If otherwise-valid non-`stopped` evidence includes `brake_not_drive` in the task stop conditions, rootless validation is invalid with `recommended_next_action: provide_runtime_root` because the current brake cannot be checked. When a runtime root is supplied, `validate-executor-result` must apply the runtime-root safety guard for the command working directory, the runtime-root location itself, and the task repo when the task repo path is valid, then check the current brake before recording completion. If `brake_not_drive` is in the task stop conditions and the current brake is not `DRIVE`, non-`stopped` result evidence must be invalid with `recommended_next_action: stop_active_loop`; stopped result evidence remains the valid way to report that the executor honored the active stop. The command then appends a compact `executor_result_validation` audit record with the task id, repo, branch, head, validity, recommendation, reason, local evidence paths, payload checksum, task-packet checksum, and result-evidence checksum. Invalid evidence exits nonzero. It must not run an executor, modify files, commit, push, open PRs, spend review, merge, release, publish packages, or infer named-host support.

`run-controlled-executor-fixture` is a test/example-only command path for a fake external executor component. It reads a generic executor task packet from `--task-file`, validates the task packet, resolves the expected result file from `expected_output.evidence_path`, formats an explicit `--command-template` with `{task_file}`, `{result_file}`, and `{repo_path}`, and validates the formatted command against the task-carried `command_policy` and disabled permissions before starting the fixture. Malformed templates fail closed before launch. The formatted command must split into an argument vector whose entrypoint is the current Python interpreter running the bundled `examples/controlled-executor-fixture/run.py` script by absolute path; arbitrary non-fixture commands and untrusted Python executable paths are rejected before launch, and the subprocess must run with `shell=False`. The result evidence path must stay inside the runtime root and must not already exist, so the runner does not delete or overwrite stale evidence before launch. The command rewrites the canonical task packet to disk, appends an `executor_fixture_invocation` audit record, runs the fixture command in the task repo with a timeout capped by the task runtime limit, and requires the fixture to write result evidence at the expected path. Timeout writes `status: stopped` evidence with a `timeout` blocker. The command then validates result evidence with the same generic executor contract, including expected-path binding, observed runtime for non-`stopped` evidence, nonzero-success, and active-brake stop handling, appends an `executor_result_validation` audit record, writes a local `execution-run.v1` record under `<root>/execution-runs/`, appends an `execution_run_record` audit record, and emits a `controlled-executor-fixture-run.v1` packet that references the run record path, id, invocation id, checksum, and closeout status. It may start only the controlled fixture command; it must not claim real executor support, named-host adapter support, Git/PR automation, merge authority, release authority, or package-publication authority.

`closeout-executor-result` is the local epoch boundary after executor evidence
has been written. It reads a generic executor task packet, generic executor
result evidence, a fresh `--snapshot-after-file`, and exactly one evidence
artifact (`--run-record-file` or `--real-invocation-file`); validates the result with the
same expected-path, command-policy, disabled-permission, runtime, and active
brake gates as `validate-executor-result`; then binds the task packet to the
requested active epoch. A real invocation in `materialized_changes` mode may
close out successful dirty-worktree result evidence only when verified
`materialized_change_evidence` is also bound. Binding requires exactly the
requested active epoch, a task id recorded in that epoch, a task snapshot
checksum matching the epoch `snapshot_before`, matching repo/branch/head
anchors, and a valid snapshot-after packet captured after epoch start with a
head matching the executor result and a `captured_at` timestamp at or after the
executor result `ended_at`. When a run record is supplied, it must use
`schema_version: execution-run.v1`,
`protocol_version: v1`, `packet: execution_run`, a non-empty run id, a
non-empty invocation id, `closeout_status: pending`, matching
task/result/validation checksums, matching task and result file paths, matching
task id, and matching repo name/path/branch/head anchors. The supplied
`--run-record-file` must also be the canonical local runtime path
`<root>/execution-runs/<run_id>.json`; malformed, unreadable, non-canonical, or
out-of-ledger run-record files block with stable run-record blockers instead of
falling through to top-level CLI errors. `--run-record-file` and
`--real-invocation-file` are mutually exclusive. Stale task snapshots, stale
snapshot-after packets, head mismatches, active epoch conflicts,
already-terminal epochs, malformed packets, partial run records, run-record
checksum mismatches, run-record repo anchor mismatches, real-invocation
checksum or result mismatches, closeout replay, and evidence that needs more
validation block closeout without moving the active epoch.

When closeout succeeds, the command emits `executor-epoch-closeout.v1`.
Successful executor evidence records the task in active epoch `completed_tasks`
when other epoch tasks remain, with `closeout_status: task_completed` and a
`next_decision` of `continue`; successful evidence completes the epoch with
`decision: STOP` only when all recorded epoch tasks are complete. Valid
`failed`, `blocked`, `stopped`, timeout-shaped stopped evidence, and executor
policy violations fail the epoch with stable reason codes. The packet includes a
`next_decision` of `generate_git_pr_plan`, `continue`, `handoff`, `stop`, or
`validate_more_evidence`. With `--emit-git-pr-plan`, a terminal successful
closeout may embed a dry-run `git-pr-plan.v1` packet for review; supplied PR
template inputs and local policy files are read before terminal epoch state is
mutated, and any policy-file `branch_policy` is passed into the embedded
dry-run plan. The command
appends a compact `executor_epoch_closeout` audit record containing task/result
and snapshot-after path/checksum anchors for fresh closeout decisions. When a
supplied run record is accepted and closeout succeeds, the command updates that
local run record with the closeout status, epoch id/status, and closeout core
checksum, then appends an `execution_run_record` audit event for the update.
If that update audit append fails after epoch closeout, the command restores
the pre-closeout run record when possible, emits `run_record_audit_append_failed`,
`execution_run_audit_append_failed`, and either
`execution_run_record_update_rolled_back` or
`execution_run_record_update_unreconciled`, and recommends
`recover_closeout_audit`.
When a supplied real invocation is accepted and closeout succeeds, the command
updates that invocation record with closeout status and checksum anchors, then
appends an `update_real_executor_invocation_closeout` audit event.
Already-terminal reruns report `closeout_status: already_closed` without
appending another closeout audit record. It must not start an executor, create a
branch, commit, push, call GitHub, open a pull request, merge, release, or
publish packages.

`controlled-loop-tick` is the controlled single-tick composition boundary after
the individual local gates have already produced evidence. It reads saved
`loop-tick`, `generic-executor-task.v1`, `execution-start.v1`,
`executor-invocation-readiness.v1`, `executor-invocation-plan.v1`,
`real-executor-invocation.v1`, `generic-executor-result.v1`, snapshot-after,
`executor-epoch-closeout.v1`, and optional `git-pr-plan.v1` files. It does not
rediscover mutable state mid-flow; it rechecks packet shape, schema where
available, task id/checksum, execution-start task anchors, readiness epoch and
task anchors, invocation-plan readiness anchors, real-invocation plan/result
anchors and invocation id, result task id, closeout epoch/task/result/snapshot anchors,
closeout-to-real-invocation checksums, closeout validation status, and optional
Git/PR plan checksum agreement. When `--git-pr-plan-file` is supplied, that
plan must also be a review-ready, non-authorizing dry run with `dry_run: true`,
`operator_confirmation_required: true`, `side_effects: []`,
`approval_state: not_approved`, and `execution_authority: none`. The closeout
status must be terminal `completed` or `failed`; blocked closeout evidence is
not a completed controlled tick.

The command emits `controlled-loop-tick.v1` with
`packet: controlled_loop_tick`, `controlled_tick_status` of `completed` or
`blocked`, `generated_at`, step status/checksum evidence, files, checksums,
blockers,
`next_decision`, and stable limitations including
`composes_existing_local_evidence_only`, `does_not_retry_executor`, and
`does_not_rewrite_invocation_or_closeout_records`. A completed packet appends a
compact `controlled_loop_tick` audit record with the source tick id, task id,
epoch id, invocation id, local file anchors, optional Git/PR plan file/checksum,
packet checksums, and payload checksum, then records
`controlled_loop_tick_audit_appended`. Blocked packets
append no audit evidence. If the post-validation audit append fails, the
command emits `controlled_loop_tick_audit_append_failed`, recommends
`recover_controlled_tick_audit`, and leaves the packet blocked.

Stable blocker codes include `loop_tick_evidence_missing`,
`task_evidence_missing`, `execution_start_evidence_missing`,
`readiness_evidence_missing`, `invocation_plan_evidence_missing`,
`real_invocation_evidence_missing`, `result_evidence_missing`,
`snapshot_after_evidence_missing`, `closeout_evidence_missing`,
`git_pr_plan_evidence_missing`, `controlled_tick_packet_mismatch`,
`executor_task_invalid`, `loop_tick_identity_missing`,
`loop_tick_task_mismatch`, `loop_tick_not_ready`, `execution_start_invalid`,
`execution_start_task_mismatch`,
`snapshot_after_invalid`, `readiness_not_invocable`,
`readiness_task_mismatch`, `readiness_epoch_mismatch`,
`invocation_plan_not_invocable`, `invocation_plan_readiness_mismatch`,
`real_invocation_invalid`, `real_invocation_identity_missing`,
`real_invocation_plan_mismatch`,
`real_invocation_record_mismatch`, `real_invocation_result_mismatch`,
`real_invocation_closeout_mismatch`, `result_task_mismatch`,
`closeout_invalid`, `closeout_epoch_mismatch`, `closeout_task_mismatch`,
`closeout_result_mismatch`, `closeout_snapshot_mismatch`,
`closeout_invocation_mismatch`, `closeout_validation_mismatch`,
`closeout_not_completed`,
`git_pr_plan_unanchored`, `git_pr_plan_mismatch`, `git_pr_plan_not_ready`,
`git_pr_plan_not_dry_run`, `git_pr_plan_operator_confirmation_missing`,
`git_pr_plan_side_effects_present`, `git_pr_plan_approval_state_invalid`,
`git_pr_plan_execution_authority_invalid`,
`git_pr_plan_proposed_branch_missing`, `git_pr_plan_proposed_pr_title_missing`,
and `git_pr_plan_proposed_pr_body_missing`.

`controlled-loop-tick` may report `executor_started: true` only because the
accepted supplied `real-executor-invocation.v1` record says a prior controlled
process started. The command itself must not start or retry an executor, rewrite
invocation records, rewrite closeout records, execute Git commands, call
GitHub, create branches, commit, push, create or update pull requests, resolve
review threads, merge, release, publish packages, assign roles, schedule
agents, or claim distributed locks.

## Git/PR Dry-Run Planning

`git-pr-plan` reads a generic executor task packet and result evidence from
local JSON files, validates them with the executor contract helpers, checks
local Git state, optionally reads a local `cadence-loop-policy.v1` file, and
emits a `git-pr-plan.v1` packet. The packet must include
`dry_run: true`, `operator_confirmation_required: true`, `side_effects: []`,
`approval_state: not_approved`, `execution_authority: none`, and
`merge_readiness: not_evaluated`. A ready packet may include proposed branch,
commit, PR title, PR body, PR body preflight, provenance checksums, explicit
materialized-change evidence, and non-executable command examples for a future
operator or role-separated workflow.

Planning readiness is not Git/PR approval. `git-pr-plan` must not create a
branch, commit, push, call GitHub, open a pull request, append audit records,
or create runtime state. If a successful result is gated by
`brake_not_drive`, the command requires an existing runtime root with readable
brake state; rootless planning recommends `provide_runtime_root`, and an active
non-`DRIVE` brake recommends `stop_active_loop`. Result `files_changed` is not
materialized-change evidence. Missing or invalid `materialized_change_evidence`
blocks planning instead of emitting a review-ready packet.

The command must block invalid task packets, invalid result evidence,
non-success results, missing runtime-root brake validation, active brake stops,
dirty worktrees, mismatched current `HEAD`, detached checkouts, current-branch
mismatches, wrong repository paths, missing local base branches, generated
branch collisions, invalid base or generated branch names, absent materialized
change evidence, and missing required PR body sections. It must also enforce
task-carried branch policy and any additional local `--policy-file` branch
policy as additive dry-run restrictions: disallowed base branches, denied
generated target branches, missing required generated-branch prefixes, and a
current `main` checkout when `allow_current_branch_main` is false must block
review readiness. Branch-policy blockers must not create branches, change refs,
call GitHub, or treat a dry-run plan as approval.

## Dirty-Worktree Git/PR Materialization Planning

`git-pr-dirty-materialization-plan` is the read-only bridge from accepted
`invoke-real-executor --side-effect-mode materialized_changes` evidence to a
reviewed commit/PR materialization input. It reads the task packet, result
evidence, closeout-updated `real-executor-invocation.v1` record, and completed
`executor_epoch_closeout` packet from local JSON files and emits
`git-pr-dirty-materialization-plan.v1`. A valid packet must include
`packet: git_pr_dirty_materialization_plan`, `dry_run: true`,
`operator_confirmation_required: true`,
`side_effects: []`, `approval_state: not_approved`,
`execution_authority: none`, `merge_readiness: not_evaluated`, exact proposed
commit metadata, proposed branch/title/body, PR-body preflight, provenance
checksums, the recomputed dirty-worktree fingerprint, and a deterministic
`target_checksum` for later operator approval.

The command must require `real_invocation.side_effect_mode:
materialized_changes`, `real_invocation.closeout_status: completed`, an
`epoch_closeout_checksum`, a matching `result_evidence_checksum`, matching
task/result path anchors when present, matching current repo path/branch/HEAD,
and `repository_after.dirty_worktree: true`. It must also require
`--closeout-file` evidence whose task/result anchors, validation status,
`real_invocation.path`, `real_invocation.invocation_id`,
`real_invocation.after_checksum`, and recomputed epoch closeout checksum match
the supplied packets. It must recompute the current dirty file set and
dirty-worktree fingerprint, compare both with
`real_invocation.materialized_change_evidence`, and block same-file content
edits, extra dirty files, missing fingerprint schema, or fingerprint mismatches.
It must also recheck the local base branch, optional `--expected-base-head`,
task-carried and local `--policy-file` branch policy, and PR-body preflight
sections.

The bridge must not stage files, create commits, create branches, push, call
GitHub, create or update pull requests, append audit records, merge, release, or
publish packages. Stable blockers include
`real_invocation_not_closeout_approved`, `real_invocation_not_materialized`,
`closeout_invocation_mismatch`,
`dirty_worktree_fingerprint_mismatch`,
`materialized_change_files_mismatch`, `repository_branch_mismatch`,
`repository_head_mismatch`, `base_head_mismatch`,
`branch_policy_base_branch_disallowed`, and `required_body_section_missing`.

## Operator-Approved Dirty Commit Materialization

`git-pr-dirty-commit-materialize` is the explicit local write-side bridge for a
reviewed `git-pr-dirty-materialization-plan.v1` packet. It must consume the
saved plan packet, require an exact HMAC operator approval token over the plan
checksum, target checksum, proposed branch, source head, and
`dirty_commit_materialization` operation using
`CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`, and emit a
`git-pr-dirty-commit-materialization.v1` packet. Missing, mismatched, or
unverifiable approval must block before audit, branch checkout, staging, or
commit side effects. The packet must not emit the expected approval token or
approval secret.

Before side effects, the command must re-read the task, result,
real-invocation, and closeout evidence named by the dirty plan provenance,
verify their checksums still match, rerun `git-pr-dirty-materialization-plan`
against current local state, and compare the current repo path, branch, `HEAD`,
base branch/head, dirty file list, dirty-worktree fingerprint, materialized
change evidence, closeout anchors, branch policy, PR body preflight, proposed
branch, proposed commit message, planned file list, and target checksum against
the approved packet. Stale heads, branch drift, base drift, generated-branch
collisions, extra or missing dirty files, dirty content tampering, PR body
blockers, and provenance checksum changes must return blocker packets before
audit or Git writes.

When all gates pass, the command may append a
`git_pr_dirty_commit_materialization_intent` audit record, snapshot the index
for rollback, create and check out only the approved proposed branch at the
approved source head, stage only the planned files with `git add --`, verify the
staged file set exactly equals the approved file list, and create exactly the
approved commit message. Before staging, planned files whose Git `filter` driver
configures `clean` or `process` steps must block to avoid filter command
execution. All bounded Git write commands must run with hooks disabled, commit
signing disabled for the local materialization commit, and the completed commit
must be rechecked against the approved parent, full commit message, and
committed file set. It
must append a
`git_pr_dirty_commit_materialization_result` audit record after success or
after a bounded side-effect failure; successful side effects without a result
audit record must return an invalid blocker packet. Failed Git write paths must
return stable blocker packets with command trace and structured recovery
evidence, including rollback attempts that restore the source branch/index and
delete the generated branch when a branch write already started. The command
must not push, call GitHub, create or update pull requests, auto-merge, release,
publish packages, spend paid review, assign roles, schedule agents, claim
distributed locks, or invoke a real executor.

## Operator-Approved Git/PR Materialization

`git-pr-materialize` is the explicit write-side boundary for a reviewed
`git-pr-plan.v1` packet or, when supplied with dirty commit source evidence, a
reviewed `git-pr-dirty-materialization-plan.v1` packet. It must consume the
saved plan packet, require an exact operator approval token produced as an HMAC
over that packet checksum plus the selected remote name, resolved remote push
URL, and create-vs-update PR target, using
`CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`, and emit a
`git-pr-materialization.v1` packet. Missing, mismatched, or unverifiable
approval must block before any audit, Git, or write-side `gh` side effect.
Materialization packets must not emit the expected approval token or approval
secret.

Before side effects, the command must re-read the task and result evidence named
by the plan provenance, verify their checksums still match the plan, rerun
`git-pr-plan` against current local state, and compare the current branch, HEAD,
base branch, base HEAD, worktree cleanliness, proposed branch/title/body, branch
policy, materialized-change evidence, and PR body preflight against the approved
packet. Materialized-change evidence must cover the complete local diff against
the base branch; extra local diff files must block. Stale heads, dirty
worktrees, branch-policy blockers, missing materialized evidence, or PR body
blockers must return stable blocker packets before branch creation, push, or PR
creation/update. PR update mode must first run a read-only `gh pr view`
preflight and verify the existing PR head branch, base branch, and head SHA
match the approved plan before branch creation, push, or PR edit.

When saved PR JSON is supplied with `--pr-json-file`, materialization packets
must include `pr_evidence` with `saved_input` or `stale` freshness labels,
`live: false`, the saved JSON checksum, and the saved evidence path. When
`--pr-json-file` is unreadable or malformed, or when
`--max-pr-json-age-minutes` is supplied and saved PR evidence is stale or
future-dated, materialization must return a `git-pr-materialization.v1` blocker
packet before audit, branch, push, or PR create/update side effects and
recommend `refresh_pr_evidence`. Function-level caller-asserted live inputs are
labeled `live_like`; the saved-file age policy must not stale-gate those
caller-asserted inputs.

When `--dirty-commit-materialization-file` is supplied, the saved plan packet
must be `git-pr-dirty-materialization-plan.v1` and the dirty commit source must
be a valid, materialized, operator-approved
`git-pr-dirty-commit-materialization.v1` result. Before side effects, the
command must verify the dirty source plan checksum, target checksum, proposed
branch, source parent, committed branch head, full commit message, committed
file set, repository path, clean worktree, branch policy, PR body preflight,
remote push URL, and optional saved PR evidence freshness. Dirty commit source
evidence must be necessary but not sufficient: the separate Git/PR
materialization approval token remains required for the push and PR write
target. Dirty PR materialization must push the already-created branch from the
dirty commit result; it must not create a second local branch or infer
dirty-worktree commit authority. Materialization packets and intent/result audit
records must carry dirty commit source anchors including the source file path,
source packet checksum, and created commit.

When all gates pass, the command may append a
`git_pr_materialization_intent` audit record, create the proposed branch at the
already-materialized current commit without switching the checkout for standard
plans, push the branch to the selected remote with Git hook verification
disabled for that push, and create or update a pull request with `gh pr create`
or `gh pr edit`. It must
append a `git_pr_materialization_result` audit record after success or after a
bounded side-effect failure; successful side effects without a result audit
record must return an invalid blocker packet. Failed Git or `gh` commands must
return stable blocker packets with command trace and replayable audit evidence.
The command must not run `git commit` against a dirty worktree, auto-merge,
release, publish packages, spend paid review, or invoke a real executor.

`github-evidence-sync` is the explicit read-only live evidence boundary. It may
shell out to `gh` only when an operator invokes the command with `--repo`,
`--pr-number`, and `--out-dir`. It fetches PR metadata/check state through
`gh pr view` and review-thread state through GitHub GraphQL, then writes saved
PR JSON, saved `reviewThreads` JSON, and a summary packet to local files. It
must label the evidence as live read-only input, fail closed for missing `gh`,
GitHub CLI spawn failure, authentication failure, rate limit, network failure,
malformed JSON, or
malformed repo slugs, and does not write partial evidence when either live fetch
fails or evidence-file writes cannot complete as a set. Review-thread fetches
must include `pageInfo.hasNextPage` for review threads and comments and follow
GitHub cursors until all pages have been captured; if pagination cannot be
completed or pagination evidence is omitted, the sync must block instead of
saving incomplete evidence as valid. `--out-dir` must be outside the current
Git worktree so local evidence capture does not dirty the repository. The
command must not create branches, commit, push, call GitHub write endpoints,
create or edit pull requests, spend paid review, merge, release, or publish
packages.

PR readiness may check target-repository template compliance and review
feedback from local files. `pr-readiness --pr-template-file <path>` must read a
Markdown pull request template, derive required PR body sections from its
headings, and report missing template sections through the readiness packet.
`pr-readiness --review-threads-file <path>` must read saved GitHub GraphQL
`reviewThreads` JSON and block unresolved actionable current review comments
while ignoring resolved, outdated, and non-actionable comments. Malformed,
missing-status, or incomplete paginated review-thread evidence must block
readiness instead of being treated as zero feedback. It must include
`readiness_evidence` labels so consumers can distinguish `saved_input`, `stale`,
and caller-asserted `live_like` evidence. The CLI reads local saved PR JSON and
labels it `saved_input`; when `--max-pr-json-age-minutes` is supplied and the
file mtime exceeds that limit, it must label the packet `stale`, add a
`pr_evidence_stale` waiting item, and recommend `refresh_pr_evidence` before
acting on stale blockers. When a saved PR JSON file has a future mtime, it must
label the packet `stale`, add a `pr_evidence_from_future` waiting item, and
recommend `refresh_pr_evidence`. Negative `--max-pr-json-age-minutes` values
must fail closed. Saved-JSON age policy applies only to saved PR JSON, not to
caller-asserted `live_like` evaluator inputs. It must ignore headings inside
HTML comments or fenced code blocks, match saved PR body headings by section
label rather than by heading level, stay repo-agnostic, and must not hard-code
target-specific labels, call GitHub, rewrite the PR body, spend paid review, or
merge the PR.

`review-response-plan` is the read-only response-planning boundary for saved PR
feedback evidence. It must read saved PR JSON from `--pr-json-file`, optional
saved GitHub GraphQL `reviewThreads` JSON from `--review-threads-file`, and
optional candidate discovery output from `--candidate-discovery-file`, then
emit a `review-response-plan.v1` packet. The packet must group failed checks by
check name, unresolved actionable current review comments by review thread and
file path, and missing PR body sections by required section. Each plan item
must carry a bounded `follow_up_task` summary and the command-level
recommendation must be one of `emit_executor_task`, `refresh_pr_evidence`,
`update_pr_body`, `wait_for_checks`, or `operator_review`. Saved PR JSON age is
checked from the file mtime when `--max-pr-json-age-minutes` is supplied; stale
or future evidence is labeled `stale` and recommends `refresh_pr_evidence`
before emitting work items. Non-stale saved PR evidence is labeled
`saved_input`; both freshness labels stay visible in the packet evidence
summary.
Malformed, missing-status, or incomplete paginated review-thread evidence must
block with stable `review_thread_evidence_invalid` blockers. Candidate discovery
input is advisory only; malformed candidate packets block planning, and matched
candidates may be included in follow-up task summaries without authorizing
execution. The command must not call GitHub, resolve review threads, post
comments, update PR bodies, create branches, commit, push, open or edit pull
requests, merge, release, publish packages, spend paid review, or invoke review
agents. Its limitations should include
`does_not_invoke_review_agents_or_paid_review`.

`review-response-materialization-plan` is the read-only approval-target boundary
for later review-response GitHub writes. It must consume a saved
`review-response-plan.v1`, saved PR JSON, optional saved review-thread JSON,
optional candidate discovery output, and an exact intended write list. Allowed
write kinds are limited to `update_pr_body` and `post_review_comment`; review
thread resolution is explicitly unsupported in this slice. Each intended write
must carry the exact body text and a matching body checksum. The packet must
recheck PR number, head branch, base branch, head SHA, saved evidence checksums,
review-thread pagination/completeness, unresolved actionable comment targets,
PR body preflight for body updates, response-plan checksum, and target text
checksums before setting `plan_ready`. Stale or future saved PR JSON must block
approval targeting. Unknown write kinds, missing or mismatched text checksums,
non-actionable comment targets, incomplete review-thread evidence, changed PR
head, or PR body preflight failure must emit stable blockers.

When valid, the command must emit a
`review-response-materialization-plan.v1` packet with
`operator_confirmation_required: true`, `approval_state: not_approved`,
`execution_authority: none`, `github_write_started: false`, a target payload,
and `target_checksum` suitable for a later operator approval. Duplicate
same-target review-comment writes must be grouped without duplicating write
actions. The command must not call GitHub, post comments, update PR bodies,
resolve review threads, create branches, commit, push, merge, release, publish
packages, spend paid review, invoke review agents, or claim that feedback has
been resolved.

`review-response-materialize` is the explicit write-side boundary for a
reviewed `review-response-materialization-plan.v1` packet. It must consume the
saved plan, saved PR JSON, optional saved review-thread JSON, optional saved
candidate discovery output, and an exact HMAC approval token for the plan
checksum and target checksum using
`CADENCE_REVIEW_RESPONSE_MATERIALIZATION_APPROVAL_SECRET`. Missing,
mismatched, or unverifiable approval must block before audit or GitHub writes.
Immediately before any `gh` side effect it must recheck saved PR freshness, PR
number, head branch, base branch, head SHA, saved evidence checksums,
review-thread completeness, unresolved actionable comment targets, PR body
preflight, allowed write kind, and target text checksum. It must execute only
approved `update_pr_body` and `post_review_comment` writes.

When valid, the command must emit `review-response-materialization.v1` with
`approval_state: approved`, `execution_authority:
operator_approved_review_response_materialization`, `github_write_started`,
`command_trace`, and `github_writes` containing GitHub URLs or ids when
available. It must append `review_response_materialization_intent` before the
first GitHub write and append `review_response_materialization_result` after
success or after a partial write failure. Audit append failure before the
intent record must block before GitHub writes and recommend audit repair.
Failed `gh` commands must emit stable blockers without claiming review
resolution. The command must not resolve review threads, invoke paid review,
edit labels, merge, release, publish packages, assign roles, schedule agents,
or continue a loop automatically.

`post-write-pr-evidence-gate` is the read-only boundary after approved Git/PR,
review-response, or review-thread-resolution writes. It must consume a
successful `git-pr-materialization.v1`,
`review-response-materialization.v1`, or
`review-thread-resolution-materialization.v1` result and fresh
`github-evidence-sync.v1` summary output. It must load the refreshed saved PR
JSON and saved review-thread JSON named by the sync packet, verify each file's
embedded `github_evidence.captured_at` metadata matches the sync summary,
verify the materialized PR number, head branch, base branch, and head SHA still
match the refreshed PR evidence, verify the refreshed review-thread evidence
belongs to that same PR, verify approved thread-resolution targets are present
and resolved when the materialization resolved review threads, and reject
missing, stale, malformed, incomplete, wrong-PR, or mismatched refreshed
evidence before recommending any follow-up action. For
`review-thread-resolution-materialization.v1`, the approved
`approval_target.thread_ids` are canonical and must exactly match confirmed
`resolve_review_thread` write records before refreshed target status is trusted.

When the refreshed target matches, the gate must re-run PR readiness and
merge-readiness candidate discovery from the refreshed saved PR and
review-thread files. It emits `post-write-pr-evidence-gate.v1` with
`recommended_next_action` limited to `ready_for_review`, `refresh_required`,
`follow_up_candidates`, `wait_for_checks`, `respond_to_review`, or
`operator_review`. Failing checks may become bounded `pr_check_failure`
follow-up candidates; unresolved actionable review threads may become bounded
`review_finding` follow-up candidates; PR body gaps block and require operator
review unless another bounded candidate applies. Refreshed evidence for resolved
approved target threads must suppress those threads from follow-up candidates;
refreshed evidence that shows an approved target is still unresolved must block
for operator review. The command must perform only one evaluation, must not call
GitHub directly, and must not post comments, update PR bodies, resolve review
threads, trigger paid review, merge, release, publish packages, assign roles,
schedule agents, or continue a loop.

`review-thread-resolution-plan` is the read-only approval-target boundary for
later review-thread resolution. It must consume saved PR JSON, saved GitHub
GraphQL `reviewThreads` JSON, a successful
`review-response-materialization.v1` result, a matching
`post-write-pr-evidence-gate.v1` packet, and one or more explicit target thread
ids. The post-write gate may still be blocked by unresolved actionable review
comments, but it must prove post-materialization freshness for the same PR
number, head branch, base branch, head SHA, PR evidence checksum, and
review-thread evidence checksum, and it must bind the full response
materialization result checksum before any resolution target can be planned.
Missing, stale, malformed, incomplete, pre-materialization, wrong-PR,
wrong-head, failed-check, operator-review, or mismatched gate evidence must
block before approval targeting.

When valid, the command must emit `review-thread-resolution-plan.v1` with
`operator_confirmation_required: true`, `approval_state: not_approved`,
`execution_authority: none`, `github_write_started: false`, a target payload,
and `target_checksum` suitable for later exact operator approval. Duplicate
target thread ids must collapse into one planned resolution action. Each
planned action must bind the thread id to PR number, head branch, base branch,
head SHA, the refreshed review-thread evidence checksum, and the prior response
materialization checksum. Already resolved threads, outdated threads,
non-actionable summary-only threads, absent target threads, incomplete
pagination, missing response materialization evidence, target threads that were
not part of the approved review-response materialization, and current
actionable comments that were not covered by the approved response
materialization must emit stable blockers. The command must not call GitHub,
resolve review threads, post comments, update PR bodies, create branches,
commit, push, merge, release, publish packages, spend paid review, assign
roles, schedule agents, or continue a loop automatically.

`review-thread-resolution-materialize` is the explicit write-side boundary for
a reviewed `review-thread-resolution-plan.v1` packet. It must consume the saved
plan, saved PR JSON, saved review-thread JSON, the prior successful
`review-response-materialization.v1` result, the saved post-write gate packet,
and an exact HMAC approval token for the plan checksum, target checksum, PR
number, and target thread ids using
`CADENCE_REVIEW_THREAD_RESOLUTION_APPROVAL_SECRET`. Missing, mismatched, or
unverifiable approval must block before audit or GitHub writes. Immediately
before any `gh` side effect it must recheck saved PR freshness, PR number, head
branch, base branch, head SHA, review-thread completeness, target thread ids,
unresolved state, target checksum, saved PR/review-thread checksums, and the
actual checksums of the supplied prior response materialization and post-write
gate packets. It must
execute only approved
`resolve_review_thread` actions through the narrow `resolveReviewThread`
GitHub mutation.

When valid, the command must emit
`review-thread-resolution-materialization.v1` with `approval_state: approved`,
`execution_authority: operator_approved_review_thread_resolution`,
`github_write_started`, `command_trace`, GitHub thread ids, resolution status,
blockers, approval target evidence, and `github_writes`. It must append
`review_thread_resolution_intent` before the first GitHub write and append
`review_thread_resolution_result` after success, approved pre-write blockers,
or after a started-write failure. Audit append failure before the intent record
must block before GitHub writes and recommend audit repair. Failed `gh` commands must emit stable
blockers and recovery evidence without claiming merge readiness. The command
must not post comments, update PR bodies, invoke paid review, edit labels,
merge, release, publish packages, assign roles, schedule agents, or continue a
loop automatically.

`controlled-pr-cycle` is the read-only Phase 1/4/5 composition packet after
approved PR and review writes. It consumes saved `controlled-loop-tick.v1`,
`git-pr-materialization.v1`, the first `post-write-pr-evidence-gate.v1`,
optional `review-response-materialization.v1` plus its post-write gate, and
optional `review-thread-resolution-materialization.v1` plus its final
post-write gate. The command must read only those local JSON files, recheck
packet schema, packet type, validity, materialization approval state, packet
checksums, PR number, head branch, base branch, head SHA, Git/PR plan checksum
binding, post-write gate materialization bindings, and chronological ordering
before emitting a `controlled-pr-cycle.v1` packet.

When valid, `controlled-pr-cycle` emits `controlled_pr_cycle_status:
completed`, lists accepted step files and checksums, records the final
post-write gate, and recommends `plan_merge_readiness` when the final gate is
`ready_for_review`. It appends success-only `controlled_pr_cycle` audit
evidence after validating the compact audit record. Missing, blocked,
malformed, wrong-order, wrong-PR, wrong-head, wrong-checksum, unapproved, or
unpaired optional packets must emit stable blockers and append no audit record.
If review-thread resolution evidence is supplied, the final
post-resolution post-write gate is required. The command must not run
executors, create branches, commit, push, call GitHub, post comments, update PR
bodies, resolve review threads, trigger paid review, merge, release, publish
packages, assign roles, schedule agents, or continue a loop.

`merge-decision-plan` is the read-only merge-readiness planning packet after a
valid controlled PR cycle exists. It consumes saved PR JSON, saved
review-thread JSON, a saved `pr-readiness` packet, saved `audit-replay`
evidence, a required `controlled-pr-cycle.v1` packet, and optional
`role-readiness.v1` evidence. It must read only local JSON files, recheck PR
number, head branch, base branch, and head SHA across the supplied evidence,
require valid audit replay with `controlled_pr_cycle` audit evidence, require
completed controlled PR-cycle evidence, forward PR-readiness blockers, block
unresolved actionable review comments, and block invalid or not-ready optional
role-readiness evidence.

The packet must use `schema_version: merge-decision-plan.v1`, `packet:
merge_decision_plan`, `read_only: true`, `operator_confirmation_required:
true`, `merge_started: false`, `github_write_started: false`, empty
`command_trace`, empty `side_effects`, input file paths, input checksums,
blockers, and limitation tokens including `does_not_call_github` and
`does_not_merge`. Valid packets recommend
`merge_after_operator_confirmation`; blocked packets must recommend
`respond_to_review`, `refresh_pr_evidence`, the supplied role-readiness action,
or `address_blockers`/`inspect_merge_decision_inputs` as appropriate. Stable
blockers include `merge_decision_pr_json_invalid`,
`merge_decision_review_threads_missing`,
`merge_decision_review_threads_invalid`, `review_thread_evidence_invalid`,
`merge_decision_review_threads_pr_anchor_missing`,
`merge_decision_review_threads_pr_mismatch`, `unresolved_review_comment`,
`merge_decision_pr_readiness_invalid`, `pr_readiness_decision_not_ready`,
`pr_readiness_action_not_merge_ready`, `pr_readiness_evidence_incomplete`,
`pr_readiness_not_ready`, `pr_readiness_checks_not_clear`,
`pr_readiness_required_checks_missing`, `pr_readiness_template_sections_missing`,
`pr_readiness_template_contract_missing`, `pr_readiness_evidence_stale`,
`pr_readiness_review_feedback_missing`,
`pr_readiness_review_feedback_mismatch`, `merge_decision_audit_replay_invalid`,
`merge_decision_audit_replay_schema_invalid`, `audit_replay_blocked`,
`audit_replay_controlled_pr_cycle_missing`,
`merge_decision_controlled_pr_cycle_missing`,
`merge_decision_controlled_pr_cycle_schema_invalid`,
`merge_decision_controlled_pr_cycle_invalid`,
`controlled_pr_cycle_not_completed`,
`controlled_pr_cycle_audit_reference_invalid`,
`controlled_pr_cycle_audit_checksum_mismatch`, `role_readiness_invalid`,
`role_readiness_schema_invalid`, `role_readiness_blocked`,
`merge_decision_pr_target_anchor_missing`, and
`merge_decision_pr_target_mismatch`. The command must not call GitHub, run Git
commands, merge, delete branches, create tags, release, publish packages,
assign roles, schedule agents, or continue a loop.

PR body preflight covers the pre-publish side of the same template contract. `pr-body-preflight --body-file <path> --pr-template-file <path>` must read a draft PR body and a local Markdown pull request template, derive required template sections from template headings, and report missing template sections before PR creation or update. It must reuse the same heading parser as PR readiness, match draft body headings by normalized section label, ignore headings inside HTML comments or fenced code blocks without creating false setext headings across skipped blocks, stay repo-agnostic, and must not hard-code target-specific labels, call GitHub, rewrite the body file, create a PR, update a PR, spend paid review, or merge the PR. If no template file or `--required-body-section` is supplied, it must fail closed and recommend `provide_template_or_sections`.

## Release Dry Run

`release-dry-run` checks a release candidate before an operator creates a tag or GitHub release. It must read only local `pyproject.toml`, `CHANGELOG.md`, and Git refs; derive release notes from the matching changelog entry; verify the requested version matches package metadata; require the changelog entry to be the latest release entry; require the selected target ref to match the checked-out `HEAD`; verify an existing tag points at the selected release commit; and report a clean-worktree, target-branch, and local `origin/<branch>` comparison when those refs are available.

The packet must include `operator_confirmation_required: true` and recommend `create_tag_after_operator_confirmation`, `create_github_release_after_operator_confirmation`, `address_blockers`, or `do_not_publish_package` as appropriate. The command must not create tags, call GitHub, create a release, write release-note files, build distributions, upload artifacts, publish packages, push, or merge.

`.github/workflows/release-dry-run.yml` is the manual GitHub Actions wrapper for this packet. It must use `workflow_dispatch`, accept `version`, `tag`, and optional `target_ref` inputs, check out `main` with full history and tags, run `python scripts/cadence.py release-dry-run`, upload `release-dry-run.json` and `release-notes.md`, and fail when `ready_to_release` is not true. The workflow must keep `contents: read` permissions and must not create tags, create GitHub releases, publish packages, push, or merge.
The workflow must keep release-tag-scoped concurrency with `group: release-dry-run-${{ inputs.tag }}`, `cancel-in-progress: true`, and a finite `timeout-minutes: 10` dry-run job bound so repeated manual attempts do not stack Actions minutes.

## Self-Evolution Rule

Agentic Cadence may propose changes to itself, but it must not silently rewrite its own safety rules while executing active work. Self-evolution execution tasks are blocked by pickup and self-check under the default `propose_only` policy. Policy changes should be reviewed like normal code changes before becoming active.

## PR Review Automation

Repository pull requests should run the normal PR checks. `.github/workflows/pr.yml` must target PRs into `main`, keep the branch-protection check names stable, use PR-number-scoped concurrency cancellation, and classify changed paths inside each job. Docs-only PRs must still create the required check contexts, run diff hygiene and protocol validation, and skip expensive compile, unit, smoke, adapter, package, and example steps when no code, packaging, tests, examples, or workflow files changed.

The Codex Review workflow is an elected paid reviewer, not an automatic blocker on every push. `.github/workflows/codex-review.yml` uses pinned `openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c` through `pull_request_target` `labeled` and `synchronize` events targeting `main`, skips draft PRs, starts paid-review preflight only for elect/force label events from trusted operators, and stays limited to same-repository PRs with `safety-strategy: drop-sudo` and `sandbox: read-only`. `synchronize` is a cancel-only event for obsolete in-flight elected reviews; it must not start paid-review preflight by itself. Unrelated label events must not cancel an in-flight elected review. It posts the action `final-message` back to the PR only after the elected paid review returns feedback. Fork PRs run a skip notice only for elected labels because `pull_request_target` must not expose repository secrets to untrusted PR code. Same-repository review jobs check the live PR state before checkout and immediately before the paid Codex action, requiring the PR to remain open, unmerged, not draft, and on the same head SHA as the triggering event; obsolete, draft, merged, closed, or non-elected PRs skip instead of failing on a missing synthetic merge ref or spending review credits.

The paid Codex action is guarded by a free preflight implemented in `scripts/codex_review_preflight.py` and checked out from the trusted base SHA rather than the PR merge tree. The PR checkout uses `github.event.pull_request.head.sha` instead of `refs/pull/<number>/merge` so a label-triggered run remains stable if the PR merges while the workflow is starting. The preflight runs before any OpenAI API key is used, computes a dedupe key from the PR head SHA and changed files, reads prior PR comments, and skips when it finds a matching hidden `codex-review:v1` marker in the canonical workflow-owned `## Codex Review` comment from `github-actions[bot]` for an elected head. It also skips empty diffs, docs-only changes when not elected, trusted label opt-outs such as `codex-review-skip`, and non-elected code changes with `not_elected`; `codex-review-elect` elects a paid run when normal preflight safeguards pass, while `codex-review-force` overrides dedupe and docs-only skips. PR title/body text must not control review spending. Inability to inspect prior review comments is a failing preflight condition only when a paid review has been elected. Workflow concurrency must cancel obsolete in-flight review runs at the PR level for elect/force label events and synchronize events, while unrelated label events must not interrupt an elected review. The paid Codex job must have a finite timeout.

Guardrail changes to `.github/workflows/codex-review.yml` or `scripts/codex_review_preflight.py` have a bootstrap boundary: a PR is evaluated by the current base branch guardrail, and its updated guardrail only becomes active after merge. Those changes require manual operator review or branch-protected code-owner review before merge so an untrusted PR cannot relax spending controls and use them in the same review cycle.

The workflow requires the repository secret `OPENAI_API_KEY`. The review prompt is kept inline in `.github/workflows/codex-review.yml` so a PR cannot redirect the action through a modified prompt file. It must tell Codex not to modify files and to focus on actionable findings, protocol drift, race conditions, missing tests, and security-sensitive automation behavior. Native `@codex review` remains a useful manual fallback, but the repo-owned workflow is the repeatable elected PR review gate.
