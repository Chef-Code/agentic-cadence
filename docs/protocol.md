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
valid, read-only, `executor_invocation_ready: true`, side-effect-free, and
waiting at `recommended_next_action: invoke_real_executor`; and requires the
invocation plan to be valid, read-only, `executor_invocation_planned: true`,
side-effect-free, and waiting at
`recommended_next_action: invoke_real_executor`.

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
`refresh_executor_invocation_readiness`; invalid invocation-plan evidence,
invocation-plan/readiness mismatch, or invocation-plan target checksum mismatch
recommends
`recreate_executor_invocation_plan`; invalid controlled-start evidence
recommends `recreate_controlled_loop_start`; missing or wrong packet evidence
recommends `refresh_controlled_invocation_evidence`. Stable blocker codes
include `controlled_loop_start_evidence_missing`,
`readiness_evidence_missing`, `invocation_plan_evidence_missing`,
`controlled_invocation_packet_mismatch`, `controlled_start_invalid`,
`readiness_not_invocable`, `invocation_plan_not_invocable`,
`controlled_start_readiness_mismatch`, and
`invocation_plan_readiness_mismatch`, and
`invocation_plan_target_checksum_mismatch`.

Completed and blocked `controlled-loop-invocation-plan` packets append no audit
record and must not continue the loop, start a runner, start or retry an
executor, start an epoch, create branches, commit, push, call GitHub, create or
update pull requests, resolve review threads, merge, release, publish packages,
assign roles, schedule agents, claim distributed locks, or rewrite the supplied
controlled-start, readiness, or invocation-plan records.

`controlled-loop-real-invocation` composes an already saved
`controlled-loop-invocation-plan.v1` packet with an already saved
`real-executor-invocation.v1` record into
`controlled-loop-real-invocation.v1`. The command reads
`--controlled-invocation-plan-file` and `--real-invocation-file`; requires the
controlled invocation plan to be completed, read-only, side-effect-free, and
waiting at `recommended_next_action: invoke_real_executor`; requires the
embedded invocation plan checksum and target checksum to match the controlled
packet anchors; and requires the real invocation to be valid, executor-started,
not timed out, waiting at `recommended_next_action: bind_real_executor_closeout`,
and still `closeout_status: pending`.

The command rechecks that the real invocation `plan_checksum`,
`plan_target_checksum`, and `plan_file` match the embedded invocation plan and
the controlled invocation-plan input. It also rechecks that `record_file`
matches the supplied real-invocation file, the invocation file is the canonical
runtime invocation-record path, the `record_real_executor_invocation` audit
record is present and checksum-matched, `result_file` matches the invocation
target `expected_result_path`, and `result_evidence_checksum` matches the
current result file. A completed packet uses
`packet: controlled_loop_real_invocation`,
`controlled_real_invocation_status: completed`, `read_only: true`,
`valid: true`, and `recommended_next_action: closeout_executor_result`. The
top-level response envelope includes `controlled_invocation_plan_checksum`,
`invocation_plan_checksum`, `real_invocation_checksum`,
`result_evidence_checksum`, `task_id`, `epoch_id`, `target_checksum`, nested
copies of the controlled plan, real invocation, and result evidence, `blockers`,
and explicit false side-effect flags.

Blocked packets use `controlled_real_invocation_status: blocked`,
`valid: false`, stable blockers, and exit code 2. Controlled plan evidence
problems recommend `refresh_controlled_loop_invocation_plan`; real invocation,
record, plan, closeout, or result mismatches recommend
`inspect_real_invocation_evidence`. Stable blocker codes include
`controlled_invocation_plan_evidence_missing`,
`real_invocation_evidence_missing`,
`controlled_invocation_plan_packet_mismatch`,
`real_invocation_packet_mismatch`,
`controlled_invocation_plan_invalid`, `controlled_invocation_plan_mismatch`,
`controlled_invocation_plan_target_mismatch`, `real_invocation_invalid`,
`real_invocation_identity_missing`, `real_invocation_closeout_not_pending`,
`real_invocation_plan_mismatch`, `real_invocation_record_mismatch`,
`real_invocation_audit_mismatch`, and `real_invocation_result_mismatch`.

Completed and blocked `controlled-loop-real-invocation` packets append no audit
record and must not continue the loop, start a runner, start or retry an
executor, start an epoch, create branches, commit, push, call GitHub, create or
update pull requests, resolve review threads, merge, release, publish packages,
assign roles, schedule agents, claim distributed locks, or rewrite the supplied
controlled invocation-plan, real-invocation, or result records. The top-level
`executor_started: false` flag means this command did not start a new process;
the nested real-invocation record still carries the already approved process
start evidence.

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
`real_executor_invocation`, `controlled_loop_run_manifest`,
`controlled_loop_runner_execution`, `controlled_loop_runner_start`,
`controlled_loop_runner_stage_execution`, `release`, and
`package_publication`.

An `operator-approval.v1` packet must include `target_checksum`, `purpose`,
`operator_id`, `key_id`, `issued_at`, `expires_at`, and `signature`. The
verifier rejects unreadable or non-object approval packets, wrong schema,
malformed or mismatched target checksums, missing or unsupported purposes,
missing operator identity, caller-supplied expected operator mismatches, weak
key ids, malformed or reversed timestamps, validity windows longer than 60
minutes, expired approvals, future-issued approvals, missing verification
secret, invalid signatures, and audit append failures with stable blockers:
`operator_approval_file_unreadable`,
`operator_approval_invalid`, `operator_approval_schema_invalid`,
`operator_approval_target_invalid`, `operator_approval_target_mismatch`,
`operator_approval_purpose_missing`, `operator_approval_purpose_mismatch`,
`operator_approval_operator_missing`, `operator_approval_operator_mismatch`,
`operator_approval_expected_operator_invalid`, `operator_approval_key_id_weak`,
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

`controlled-loop-closeout` composes an already saved
`controlled-loop-real-invocation.v1` packet with an already saved
`executor-epoch-closeout.v1` packet into `controlled-loop-closeout.v1`. The
command reads `--controlled-real-invocation-file` and `--closeout-file`;
requires the controlled real-invocation packet to be completed, read-only,
side-effect-free, and waiting at
`recommended_next_action: closeout_executor_result`; requires closeout evidence
to be valid and terminal with `closeout_status: completed` or
`closeout_status: failed`; and requires the closeout real-invocation reference
to match the controlled packet's real-invocation file path, invocation id, and
pre-closeout checksum.

The command revalidates the controlled packet's embedded controlled invocation
plan, executor invocation plan, result evidence, and pre-closeout real
invocation checksum anchors before trusting the closeout composition. It safely
reads the updated real-invocation record only after the closeout path anchor
matches the controlled real-invocation input, the embedded invocation
`record_file`, and the canonical runtime invocation path derived from
`invocation_id`. It then rechecks the closeout `after_checksum`, updated
invocation `closeout_status`, `epoch_id`, `epoch_status`,
`epoch_closeout_checksum`, `result_evidence_checksum`, validation checksum,
task-file anchor, and record path. It also replays the current audit log and
verifies both the closeout audit reference and the real-invocation
closeout-update audit reference against their referenced audit lines. A
completed packet uses
`packet: controlled_loop_closeout`, `controlled_closeout_status: completed`,
`read_only: true`, `side_effects: []`, `valid: true`, and
`recommended_next_action: controlled_loop_tick`. The top-level response envelope
includes `controlled_real_invocation_checksum`, `closeout_checksum`,
`real_invocation_before_checksum`, `real_invocation_after_checksum`,
`epoch_closeout_checksum`, `task_id`, `epoch_id`, `closeout_status`, nested
copies of the controlled real-invocation packet, closeout packet, and updated
real-invocation record, `blockers`, and explicit false side-effect flags.

Blocked packets use `controlled_closeout_status: blocked`, `read_only: true`,
`side_effects: []`, `valid: false`, stable blockers, and exit code 2.
Controlled real-invocation evidence problems recommend
`refresh_controlled_loop_real_invocation`; closeout, updated real-invocation,
path, checksum, terminal-status, and audit mismatches recommend
`inspect_closeout_evidence`. Stable blocker codes include
`controlled_real_invocation_evidence_missing`, `closeout_evidence_missing`,
`controlled_real_invocation_packet_mismatch`, `closeout_packet_mismatch`,
`controlled_real_invocation_invalid`, `closeout_invalid`,
`closeout_not_terminal`, `closeout_epoch_mismatch`,
`closeout_result_mismatch`, `closeout_invocation_mismatch`,
`real_invocation_evidence_missing`, `real_invocation_packet_mismatch`,
`real_invocation_closeout_mismatch`, `closeout_audit_mismatch`, and
`real_invocation_audit_mismatch`.

Completed and blocked `controlled-loop-closeout` packets append no audit record
and must not continue the loop, start a runner, start or retry an executor,
start or close an epoch, create branches, commit, push, call GitHub, create or
update pull requests, resolve review threads, merge, release, publish packages,
assign roles, schedule agents, claim distributed locks, or rewrite the supplied
controlled real-invocation, real-invocation, or closeout records.

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

`controlled-loop-run-summary` is the read-only summary boundary for the saved
runner-adjacent packet chain. It reads `--loop-run-plan-file`,
`--controlled-loop-start-file`, `--controlled-invocation-plan-file`,
`--controlled-real-invocation-file`, `--controlled-closeout-file`, and
`--controlled-loop-tick-file`. The command verifies packet schemas and
completed statuses, then rechecks the checksum/file chain from
`loop-run-plan.v1` through `controlled-loop-start.v1`,
`controlled-loop-invocation-plan.v1`, `controlled-loop-real-invocation.v1`,
`controlled-loop-closeout.v1`, and `controlled-loop-tick.v1`.

The command emits `controlled-loop-run-summary.v1` with
`packet: controlled_loop_run_summary`, `read_only: true`, `side_effects: []`,
`controlled_run_status` of `completed` or `blocked`, step checksums, files,
blockers, and `recommended_next_action` of `review_controlled_loop_run` or
`inspect_controlled_loop_run_blockers`. It appends no audit evidence; the
controlled tick packet remains the audit boundary for the completed local
single-tick composition. Stable blockers include:

- Missing evidence: `loop_run_plan_evidence_missing`,
  `controlled_loop_start_evidence_missing`,
  `controlled_invocation_plan_evidence_missing`,
  `controlled_real_invocation_evidence_missing`,
  `controlled_closeout_evidence_missing`,
  `controlled_loop_tick_evidence_missing`.
- Packet and readiness mismatches: `controlled_run_packet_mismatch`,
  `loop_run_plan_not_ready`, `controlled_run_task_mismatch`,
  `controlled_run_epoch_mismatch`.
- Step completion mismatches: `controlled_loop_start_not_completed`,
  `controlled_invocation_plan_not_completed`,
  `controlled_real_invocation_not_completed`,
  `controlled_closeout_not_completed`, `controlled_loop_tick_not_completed`.
- Step next-action mismatches:
  `controlled_loop_start_unexpected_next_action`,
  `controlled_invocation_plan_unexpected_next_action`,
  `controlled_real_invocation_unexpected_next_action`,
  `controlled_closeout_unexpected_next_action`,
  `controlled_loop_tick_unexpected_next_action`.
- Contradictory completed packets:
  `controlled_loop_start_unexpected_blockers`,
  `controlled_invocation_plan_unexpected_blockers`,
  `controlled_real_invocation_unexpected_blockers`,
  `controlled_closeout_unexpected_blockers`,
  `controlled_loop_tick_unexpected_blockers`,
  `controlled_loop_start_unexpected_operator_confirmation`,
  `controlled_invocation_plan_unexpected_operator_confirmation`,
  `controlled_real_invocation_unexpected_operator_confirmation`,
  `controlled_closeout_unexpected_operator_confirmation`,
  `controlled_loop_tick_unexpected_operator_confirmation`,
  `controlled_loop_start_unexpected_side_effects`,
  `controlled_invocation_plan_unexpected_side_effects`,
  `controlled_real_invocation_unexpected_side_effects`,
  `controlled_closeout_unexpected_side_effects`,
  `controlled_loop_tick_unexpected_side_effects`.
- Cross-packet checksum and file mismatches:
  `loop_run_plan_checksum_mismatch`, `loop_run_plan_file_mismatch`,
  `controlled_loop_start_checksum_mismatch`,
  `controlled_loop_start_file_mismatch`,
  `controlled_invocation_plan_checksum_mismatch`,
  `controlled_invocation_plan_file_mismatch`,
  `controlled_real_invocation_checksum_mismatch`,
  `controlled_real_invocation_file_mismatch`.
- Controlled tick mismatches: `controlled_tick_source_mismatch`,
  `controlled_tick_loop_tick_checksum_mismatch`,
  `controlled_tick_task_checksum_mismatch`,
  `controlled_tick_execution_start_checksum_mismatch`,
  `controlled_tick_readiness_checksum_mismatch`,
  `controlled_tick_invocation_plan_checksum_mismatch`,
  `controlled_tick_result_checksum_mismatch`,
  `controlled_tick_closeout_checksum_mismatch`,
  `controlled_tick_real_invocation_checksum_mismatch`.

`controlled-loop-outcome-plan` is the read-only terminal planner for completed
terminal controlled run evidence. It reads `--controlled-run-summary-file`,
`--controlled-closeout-file`, and `--controlled-loop-tick-file`, then emits
`controlled-loop-outcome-plan.v1` with `packet:
controlled_loop_outcome_plan`, `read_only: true`, `side_effects: []`,
`outcome_plan_status` of `completed` or `blocked`, step checksums, files,
blockers, source decision, embedded Git/PR plan evidence when present, and a
bounded `recommended_next_action`.

The command verifies that the summary is completed and ready for review, the
controlled closeout is completed and ready for the controlled tick, and the
controlled tick is completed. It rechecks the controlled-run-summary checksum,
summary checksums for the controlled closeout and controlled tick, summary file
anchors, controlled tick closeout and real-invocation checksums, the embedded
controlled closeout checksum,
summary/tick/closeout next-decision equality, task id, epoch id, closeout
status, and that the source decision is one of `generate_git_pr_plan`,
`continue`, `handoff`, `stop`, or `validate_more_evidence`. The source
decision's `recommended_next_action` must also be in the command's bounded
allowlist for that decision.

When valid, recommendation mapping is:

- `generate_git_pr_plan` with no embedded plan: `run_git_pr_plan`, with no
  operator confirmation required.
- `generate_git_pr_plan` with a ready dry-run embedded plan and no side effects,
  where the controlled tick also anchors the same saved Git/PR plan file and
  checksum: `request_git_pr_materialization_approval`, with operator
  confirmation required.
- `generate_git_pr_plan` with a missing, malformed, unready, unanchored, or
  mismatched embedded Git/PR plan:
  `inspect_git_pr_plan_blockers`, with operator confirmation required.
- `continue`: `plan_next_controlled_executor_step`.
- `handoff`: `inspect_executor_failure`.
- `stop`: the source decision's recommended action, or
  `review_stop_decision`.
- `validate_more_evidence`: the source decision's recommended action, or
  `fix_executor_evidence`.

The command appends no audit evidence and does not start a runner or executor,
retry an executor, continue a loop, close an epoch, execute Git commands, call
GitHub, create branches, commit, push, create PRs, merge, release, publish
packages, assign roles, or schedule agents. Stable blockers include
`controlled_run_summary_evidence_missing`,
`controlled_closeout_evidence_missing`,
`controlled_loop_tick_evidence_missing`,
`controlled_loop_outcome_packet_mismatch`,
`controlled_run_summary_not_completed`,
`controlled_closeout_not_completed`, `controlled_loop_tick_not_completed`,
`controlled_run_summary_checksum_mismatch`,
`controlled_closeout_checksum_mismatch`,
`controlled_loop_tick_checksum_mismatch`,
`controlled_closeout_file_mismatch`, `controlled_loop_tick_file_mismatch`,
`controlled_closeout_embedded_closeout_checksum_mismatch`,
`controlled_loop_tick_closeout_checksum_mismatch`,
`controlled_loop_tick_real_invocation_checksum_mismatch`,
`controlled_run_summary_decision_mismatch`,
`controlled_loop_outcome_closeout_decision_mismatch`,
`controlled_loop_outcome_task_mismatch`,
`controlled_loop_outcome_epoch_mismatch`,
`controlled_loop_outcome_closeout_status_mismatch`, and
`controlled_loop_outcome_decision_unsupported`. Unsupported source actions
block with `controlled_loop_outcome_action_unsupported`. Embedded Git/PR plan
approval requires the controlled tick's `files.git_pr_plan` and
`checksums.git_pr_plan` to match the embedded plan; missing, unreadable,
malformed, mismatched, or structurally unready plan evidence blocks with stable
codes such as `git_pr_plan_checksum_mismatch`, `git_pr_plan_unanchored`,
`git_pr_plan_evidence_missing`, `git_pr_plan_file_mismatch`,
`git_pr_plan_not_ready`, and `git_pr_plan_approval_state_invalid`.

`controlled-loop-run-manifest-plan` is the read-only manifest planner for a
completed terminal controlled run. It reads
`--controlled-run-summary-file`, `--controlled-closeout-file`,
`--controlled-loop-tick-file`, and `--controlled-outcome-plan-file`, then emits
`controlled-loop-run-manifest-plan.v1` with `packet:
controlled_loop_run_manifest_plan`, `read_only: true`, `side_effects: []`,
`manifest_status` of `completed` or `blocked`, step checksums, files,
blockers, `next_controlled_action`, and a `run_manifest` containing
`evidence_files`, `checksums`, and the controlled one-cycle
`command_sequence`.

The command verifies that the summary, closeout, tick, and outcome plan are
completed and side-effect-free as saved planner evidence. It rechecks the
outcome-plan checksums and file anchors for the supplied run summary,
controlled closeout, and controlled tick, and rechecks the run-summary
checksums for the controlled closeout and controlled tick. It also recomputes
the outcome-plan source decision, recommended next action, operator
confirmation requirement, task, epoch, and optional Git/PR plan from the
supplied terminal evidence instead of trusting copied outcome-plan fields.
When valid, it
recommends `review_controlled_run_manifest` with operator confirmation
required. Stale terminal or outcome evidence recommends
`refresh_controlled_loop_outcome_plan`.

The command appends no audit evidence and does not start a runner or executor,
retry an executor, continue a loop, start or close an epoch, execute Git
commands, call GitHub, create branches, commit, push, create PRs, merge,
release, publish packages, assign roles, or schedule agents. Stable blockers
include `controlled_run_summary_evidence_missing`,
`controlled_closeout_evidence_missing`,
`controlled_loop_tick_evidence_missing`,
`controlled_outcome_plan_evidence_missing`,
`controlled_run_manifest_packet_mismatch`,
`controlled_run_summary_not_completed`,
`controlled_closeout_not_completed`, `controlled_loop_tick_not_completed`,
`controlled_outcome_plan_not_completed`,
`controlled_outcome_plan_controlled_run_summary_checksum_mismatch`,
`controlled_outcome_plan_controlled_closeout_checksum_mismatch`,
`controlled_outcome_plan_controlled_loop_tick_checksum_mismatch`,
`controlled_outcome_plan_controlled_run_summary_file_mismatch`,
`controlled_outcome_plan_controlled_closeout_file_mismatch`,
`controlled_outcome_plan_controlled_loop_tick_file_mismatch`,
`controlled_run_manifest_controlled_closeout_checksum_mismatch`, and
`controlled_run_manifest_controlled_loop_tick_checksum_mismatch`,
`controlled_outcome_plan_source_decision_mismatch`,
`controlled_outcome_plan_recommended_next_action_mismatch`,
`controlled_outcome_plan_operator_confirmation_mismatch`,
`controlled_outcome_plan_task_mismatch`,
`controlled_outcome_plan_epoch_mismatch`, and
`controlled_outcome_plan_git_pr_plan_mismatch`.

`controlled-loop-run-manifest-approval` is the read-only approval gate for a
completed controlled run manifest. It reads
`--controlled-run-manifest-plan-file` and `--approval-file`, verifies the
manifest packet is a completed `controlled-loop-run-manifest-plan.v1`, and
verifies the `operator-approval.v1` packet with purpose
`controlled_loop_run_manifest` against the exact checksum of the supplied
manifest. The command emits `controlled-loop-run-manifest-approval.v1` with
`packet: controlled_loop_run_manifest_approval`, `read_only: true`,
`side_effects: []`, `approval_status` of `completed` or `blocked`, manifest and
approval checksums, approval identity fields, blockers, and
`next_controlled_action`.

When valid, the command recommends `review_controlled_run_manifest_approval`
with operator confirmation required and reports
`next_controlled_action: review_approved_controlled_run_manifest`. Stale or
blocked manifest evidence recommends `refresh_controlled_run_manifest_plan`.
Invalid approval evidence recommends `fix_controlled_run_manifest_approval`.

The command appends no audit evidence and does not start a runner or executor,
retry an executor, continue a loop, start or close an epoch, execute Git
commands, call GitHub, create branches, commit, push, create PRs, merge,
release, publish packages, assign roles, or schedule agents. Stable blockers
include `controlled_run_manifest_plan_evidence_missing`,
`controlled_run_manifest_plan_packet_mismatch`,
`controlled_run_manifest_plan_not_completed`,
`controlled_run_manifest_plan_blockers_present`,
`controlled_run_manifest_plan_steps_blocked`,
`controlled_run_manifest_plan_authority_flags_invalid`,
`controlled_run_manifest_plan_operator_confirmation_missing`,
`controlled_run_manifest_plan_command_sequence_mismatch`, and the
`operator_approval_*` blockers emitted by `operator-approval.v1`
verification, including `operator_approval_file_unreadable`,
`operator_approval_target_mismatch`, `operator_approval_purpose_mismatch`,
`operator_approval_signature_invalid`, and `operator_approval_secret_missing`.

`controlled-loop-runner-plan` is the read-only dry-run runner planner for an
approved controlled run manifest. It reads
`--controlled-run-manifest-plan-file` and
`--controlled-run-manifest-approval-file`, verifies the manifest packet is a
completed `controlled-loop-run-manifest-plan.v1`, verifies the approval packet
is a completed `controlled-loop-run-manifest-approval.v1`, recomputes the
manifest and approval checksums, rechecks the approval target and file anchors,
and rereads the saved `operator-approval.v1` file referenced by the approval
evidence. The reread operator approval file is rehashed, its checksum is
matched back to the approval evidence, and its signature is re-verified before
a completed runner plan can be emitted. The command emits
`controlled-loop-runner-plan.v1` with `packet:
controlled_loop_runner_plan`, `read_only: true`, `side_effects: []`,
`runner_plan_status` of `completed` or `blocked`, `approved_manifest`,
`manifest_approval`, `runner_plan`, checksums, blockers, and
`next_controlled_action`.

When valid, the command recommends `review_controlled_runner_plan` with
operator confirmation required and reports
`next_controlled_action: request_controlled_runner_execution_approval`. Stale
manifest approval evidence recommends
`refresh_controlled_run_manifest_approval`. Blocked approval evidence
recommends `fix_controlled_run_manifest_approval`.

The command appends no audit evidence and does not start a runner or executor,
retry an executor, continue a loop, start or close an epoch, execute Git
commands, call GitHub, create branches, commit, push, create PRs, merge,
release, publish packages, assign roles, or schedule agents. Stable blockers
include `controlled_runner_manifest_evidence_missing`,
`controlled_runner_manifest_packet_mismatch`,
`controlled_runner_manifest_plan_not_completed`,
`controlled_runner_manifest_command_sequence_mismatch`,
`controlled_runner_manifest_approval_evidence_missing`,
`controlled_runner_manifest_approval_packet_mismatch`,
`controlled_runner_manifest_approval_not_completed`,
`controlled_runner_manifest_approval_checksum_mismatch`,
`controlled_runner_manifest_approval_file_mismatch`,
`controlled_runner_operator_approval_file_missing`,
`controlled_runner_operator_approval_file_unreadable`,
`controlled_runner_operator_approval_checksum_mismatch`, and
`controlled_runner_operator_approval_target_mismatch`, and
`controlled_runner_operator_approval_verification_failed`.

`controlled-loop-runner-execution-approval` is the read-only approval gate for a
reviewed controlled runner plan. It reads
`--controlled-loop-runner-plan-file` and `--approval-file`, verifies the runner
plan packet is a completed `controlled-loop-runner-plan.v1`, and verifies the
`operator-approval.v1` packet with purpose
`controlled_loop_runner_execution` against the exact checksum of the supplied
runner plan. The command emits
`controlled-loop-runner-execution-approval.v1` with `packet:
controlled_loop_runner_execution_approval`, `read_only: true`,
`side_effects: []`, `approval_status` of `completed` or `blocked`, runner-plan
and approval checksums, approval identity fields, blockers, and
`next_controlled_action`.

When valid, the command recommends
`review_controlled_runner_execution_approval` with operator confirmation
required and reports
`next_controlled_action: review_approved_controlled_runner_execution`. Stale or
blocked runner-plan evidence recommends `refresh_controlled_runner_plan`.
Invalid approval evidence recommends `fix_controlled_runner_execution_approval`.

The command appends no audit evidence and does not start a runner or executor,
retry an executor, continue a loop, start or close an epoch, execute Git
commands, call GitHub, create branches, commit, push, create PRs, merge,
release, publish packages, assign roles, or schedule agents. Stable blockers
include `controlled_runner_plan_evidence_missing`,
`controlled_runner_plan_packet_mismatch`,
`controlled_runner_plan_not_completed`,
`controlled_runner_plan_authority_flags_invalid`,
`controlled_runner_plan_command_sequence_mismatch`,
`controlled_runner_plan_mode_invalid`,
`controlled_runner_plan_steps_mismatch`, and the `operator_approval_*` blockers
emitted by `operator-approval.v1` verification, including
`operator_approval_file_unreadable`, `operator_approval_target_mismatch`,
`operator_approval_purpose_mismatch`, `operator_approval_signature_invalid`,
and `operator_approval_secret_missing`.

`controlled-loop-runner-dry-run` is the read-only dry-run execution packet for
an approved controlled runner plan. It reads
`--controlled-loop-runner-plan-file` and
`--controlled-loop-runner-execution-approval-file`, verifies the runner plan is
a completed `controlled-loop-runner-plan.v1`, verifies the execution approval
is a completed `controlled-loop-runner-execution-approval.v1`, recomputes the
runner-plan and execution-approval checksums, rechecks file anchors, rereads
the saved `operator-approval.v1` file referenced by the approval evidence, and
re-verifies that approval against the current runner-plan checksum and purpose
`controlled_loop_runner_execution`. The command emits
`controlled-loop-runner-dry-run.v1` with `packet:
controlled_loop_runner_dry_run`, `read_only: true`, `side_effects: []`,
`runner_dry_run_status` of `completed` or `blocked`,
`non_execution_guarantees`, a `runner_dry_run.stages` list with `status:
would_process` evidence for each planned command, checksums, blockers, and
`next_controlled_action`.

When valid, the command recommends `review_controlled_runner_dry_run` with
operator confirmation required and reports
`next_controlled_action: stop_after_controlled_runner_dry_run`. Stale or
blocked runner-plan evidence recommends `refresh_controlled_runner_plan`.
Stale execution-approval evidence recommends
`refresh_controlled_runner_execution_approval`. Invalid operator approval
evidence recommends `fix_controlled_runner_execution_approval`.

The command appends no audit evidence and does not start a runner or executor,
invoke an executor, retry an executor, continue a loop, start or close an
epoch, execute Git commands, call GitHub, create branches, commit, push, create
PRs, merge, release, publish packages, assign roles, or schedule agents.
Stable blockers include `controlled_runner_plan_evidence_missing`,
`controlled_runner_plan_packet_mismatch`,
`controlled_runner_plan_not_completed`,
`controlled_runner_plan_authority_flags_invalid`,
`controlled_runner_plan_command_sequence_mismatch`,
`controlled_runner_plan_mode_invalid`,
`controlled_runner_plan_steps_mismatch`,
`controlled_runner_dry_run_execution_approval_evidence_missing`,
`controlled_runner_dry_run_execution_approval_packet_mismatch`,
`controlled_runner_dry_run_execution_approval_not_completed`,
`controlled_runner_dry_run_execution_approval_authority_flags_invalid`,
`controlled_runner_dry_run_execution_approval_approval_mismatch`,
`controlled_runner_dry_run_execution_approval_plan_checksum_mismatch`,
`controlled_runner_dry_run_execution_approval_file_mismatch`,
`controlled_runner_dry_run_operator_approval_file_missing`,
`controlled_runner_dry_run_operator_approval_file_mismatch`,
`controlled_runner_dry_run_operator_approval_file_unreadable`,
`controlled_runner_dry_run_operator_approval_checksum_mismatch`,
`controlled_runner_dry_run_operator_approval_target_mismatch`, and the
`operator_approval_*` blockers emitted by `operator-approval.v1` verification,
including `operator_approval_invalid`, `operator_approval_schema_invalid`,
`operator_approval_target_invalid`, `operator_approval_target_mismatch`,
`operator_approval_purpose_missing`, `operator_approval_purpose_mismatch`,
`operator_approval_operator_missing`, `operator_approval_key_id_weak`,
`operator_approval_timestamp_invalid`, `operator_approval_window_too_long`,
`operator_approval_expired`, `operator_approval_issued_in_future`,
`operator_approval_signature_invalid`, and `operator_approval_secret_missing`.

`controlled-loop-runner-start-readiness` is the read-only readiness packet after
a completed controlled runner dry run. It reads
`--controlled-loop-runner-dry-run-file`, `--controlled-loop-runner-plan-file`,
and `--controlled-loop-runner-execution-approval-file`, verifies the dry-run
packet is a completed `controlled-loop-runner-dry-run.v1`, revalidates the
supplied runner-plan and execution-approval packet schemas, statuses, approval
identity, authority flags, file anchors, and checksums, and recomputes dry-run,
runner-plan, and execution-approval checksums. It also verifies that the dry-run
planned command sequence and stage list still match the approved runner plan,
that all runner and executor authority flags remain false, and that each
dry-run stage remains `status: would_process` with no side effects. The command
emits `controlled-loop-runner-start-readiness.v1` with `packet:
controlled_loop_runner_start_readiness`, `read_only: true`, `side_effects: []`,
`runner_start_ready`, `runner_start_readiness_status` of `ready` or `blocked`,
`runner_start_authority: none`, `runner_start_readiness.stages`, checksums,
blockers, and `next_controlled_action`.

When valid, the command recommends
`review_controlled_runner_start_readiness` with operator confirmation required
and reports `next_controlled_action: stop_before_runner_start`. Stale or
blocked dry-run evidence recommends `refresh_controlled_runner_dry_run`.

The command appends no audit evidence and does not start a runner or executor,
invoke an executor, retry an executor, continue a loop, start or close an
epoch, execute Git commands, call GitHub, create branches, commit, push, create
PRs, merge, release, publish packages, assign roles, or schedule agents. Stable
blockers include `controlled_runner_start_readiness_dry_run_evidence_missing`,
`controlled_runner_start_readiness_runner_plan_evidence_missing`,
`controlled_runner_start_readiness_execution_approval_evidence_missing`,
`controlled_runner_start_readiness_dry_run_packet_mismatch`,
`controlled_runner_start_readiness_dry_run_not_completed`,
`controlled_runner_start_readiness_authority_flags_invalid`,
`controlled_runner_start_readiness_non_execution_guarantees_missing`,
`controlled_runner_start_readiness_runner_plan_packet_mismatch`,
`controlled_runner_start_readiness_runner_plan_not_completed`,
`controlled_runner_start_readiness_runner_plan_authority_flags_invalid`,
`controlled_runner_start_readiness_runner_plan_command_sequence_mismatch`,
`controlled_runner_start_readiness_runner_plan_mode_invalid`,
`controlled_runner_start_readiness_runner_plan_steps_mismatch`,
`controlled_runner_start_readiness_runner_plan_file_mismatch`,
`controlled_runner_start_readiness_runner_plan_checksum_mismatch`,
`controlled_runner_start_readiness_execution_approval_packet_mismatch`,
`controlled_runner_start_readiness_execution_approval_not_completed`,
`controlled_runner_start_readiness_execution_approval_authority_flags_invalid`,
`controlled_runner_start_readiness_execution_approval_approval_mismatch`,
`controlled_runner_start_readiness_execution_approval_plan_checksum_mismatch`,
`controlled_runner_start_readiness_execution_approval_plan_status_mismatch`,
`controlled_runner_start_readiness_execution_approval_file_mismatch`,
`controlled_runner_start_readiness_execution_approval_checksum_mismatch`, and
`controlled_runner_start_readiness_stage_malformed`,
`controlled_runner_start_readiness_stage_not_would_process`, and
`controlled_runner_start_readiness_stage_sequence_mismatch`.

`controlled-loop-runner-start-approval` is the read-only approval packet after
completed controlled runner start-readiness evidence. It reads
`--controlled-loop-runner-start-readiness-file` and `--approval-file`, verifies
the saved start-readiness packet is `controlled-loop-runner-start-readiness.v1`
and still ready, verifies all authority flags remain false, verifies readiness
stages remain non-executed and ready, and verifies a target-bound
`operator-approval.v1` packet with purpose `controlled_loop_runner_start`
against the exact checksum of the supplied start-readiness packet. The command
emits `controlled-loop-runner-start-approval.v1` with `packet:
controlled_loop_runner_start_approval`, `read_only: true`, `side_effects: []`,
`approval_status` of `completed` or `blocked`, `runner_start_authority:
operator_approved_not_started` when valid, approval identity evidence, checksums,
blockers, and `next_controlled_action`.

When valid, the command recommends
`review_controlled_runner_start_approval`, requires operator confirmation, and
reports `next_controlled_action: review_approved_controlled_runner_start`.
Blocked start-readiness evidence recommends
`refresh_controlled_runner_start_readiness`; blocked operator approval evidence
recommends `fix_controlled_runner_start_approval`.

The command appends no audit evidence and does not start a runner or executor,
invoke an executor, retry an executor, continue a loop, start or close an
epoch, execute Git commands, call GitHub, create branches, commit, push, create
PRs, merge, release, publish packages, assign roles, or schedule agents. Stable
blockers include `controlled_runner_start_readiness_evidence_missing`,
`controlled_runner_start_readiness_packet_mismatch`,
`controlled_runner_start_readiness_not_ready`,
`controlled_runner_start_readiness_authority_flags_invalid`,
`controlled_runner_start_readiness_stage_sequence_mismatch`,
`controlled_runner_start_readiness_mode_invalid`,
`controlled_runner_start_readiness_stage_malformed`,
`operator_approval_file_unreadable`, and the
`operator_approval_*` blockers emitted by `operator-approval.v1` verification,
including `operator_approval_invalid`, `operator_approval_schema_invalid`,
`operator_approval_target_invalid`, `operator_approval_target_mismatch`,
`operator_approval_purpose_missing`, `operator_approval_purpose_mismatch`,
`operator_approval_operator_missing`, `operator_approval_key_id_weak`,
`operator_approval_timestamp_invalid`, `operator_approval_window_too_long`,
`operator_approval_expired`, `operator_approval_issued_in_future`,
`operator_approval_signature_invalid`, and `operator_approval_secret_missing`.

`controlled-loop-runner-start` is the controlled one-cycle runner-start packet
after completed runner start approval. It reads
`--controlled-loop-runner-start-approval-file`,
`--controlled-loop-runner-start-readiness-file`,
`--controlled-loop-runner-dry-run-file`, `--controlled-loop-runner-plan-file`,
and `--controlled-loop-runner-execution-approval-file`. It verifies the saved
start-approval packet is `controlled-loop-runner-start-approval.v1` and
completed, revalidates the target-bound operator approval file, revalidates the
saved start-readiness packet, rechecks dry-run, runner-plan, and
execution-approval file anchors and checksums, rereads the execution-approval
operator approval file, verifies stage sequences still match the approved
controlled-run manifest, and emits
`controlled-loop-runner-start.v1` with `packet:
controlled_loop_runner_start`, `runner_start_status` of `started` or
`blocked`, `runner_started: true`, `executor_started: false`,
`loop_continuation_started: false`, `runner_start_authority:
operator_approved_started` when valid, the approved command sequence,
checksums, blockers, and
`next_controlled_action`.

When valid, the command recommends `review_controlled_runner_start`, appends
one `controlled_loop_runner_start` audit record, reports `runner_started:
true`, and stops at `stop_after_controlled_runner_start`. Blocked input packets
recommend refreshing the stale or blocked packet type and append no audit
record.

The command starts no executor, invokes no executor, retries no executor,
continues no loop, starts or closes no epoch, executes no Git commands, calls
no GitHub APIs, creates no branches, commits, pushes, creates no PRs, merges no
PRs, releases no artifacts, publishes no packages, assigns no roles, and
schedules no agents. Stable blockers include
`controlled_runner_start_approval_evidence_missing`,
`controlled_runner_start_approval_packet_mismatch`,
`controlled_runner_start_approval_not_completed`,
`controlled_runner_start_approval_authority_flags_invalid`,
`controlled_runner_start_readiness_evidence_missing`,
`controlled_runner_start_readiness_checksum_mismatch`,
`controlled_runner_start_readiness_file_mismatch`,
`controlled_runner_start_dry_run_evidence_missing`,
`controlled_runner_start_dry_run_authority_flags_invalid`,
`controlled_runner_start_dry_run_checksum_mismatch`,
`controlled_runner_start_dry_run_non_execution_guarantees_missing`,
`controlled_runner_start_dry_run_stage_sequence_mismatch`,
`controlled_runner_start_runner_plan_evidence_missing`,
`controlled_runner_start_runner_plan_checksum_mismatch`,
`controlled_runner_start_execution_approval_evidence_missing`,
`controlled_runner_start_execution_approval_checksum_mismatch`,
`controlled_runner_start_execution_approval_not_completed`,
`controlled_runner_start_execution_approval_operator_file_missing`,
`controlled_runner_start_execution_approval_operator_file_mismatch`,
`controlled_runner_start_execution_approval_operator_file_unreadable`,
`controlled_runner_start_execution_approval_operator_checksum_mismatch`,
`controlled_runner_start_execution_approval_operator_target_mismatch`,
`controlled_runner_start_approval_operator_checksum_mismatch`,
`controlled_runner_start_approval_operator_file_missing`,
`controlled_runner_start_audit_append_failed`, and the
`controlled_runner_start_readiness_*`,
`controlled_runner_start_readiness_runner_plan_*`,
`controlled_runner_start_readiness_execution_approval_*`, and
`operator_approval_*` blockers emitted by the revalidated upstream gates.

`controlled-loop-runner-next-stage` is the read-only stage-selection packet
after the controlled runner-start boundary. It reads
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, and optional `--stage-number`.
Task 53 only supports `--stage-number 1`. It verifies the saved start packet is
`controlled-loop-runner-start.v1` and started, rechecks supplied runner-plan
and dry-run file anchors and checksums, verifies the dry-run stage sequence
still matches the approved runner plan, verifies dry-run read-only flags and
non-execution guarantees, verifies the runner-start audit summary against the
recorded audit log line, audit-chain metadata, denormalized audit fields, and
payload checksum, and emits
`controlled-loop-runner-next-stage.v1` with
`packet: controlled_loop_runner_next_stage`, `read_only: true`,
`stage_execution_started: false`, `executor_started: false`, and
`loop_continuation_started: false`.

When valid, the command recommends `review_controlled_runner_next_stage`,
sets `runner_started: true`,
`next_controlled_action: prepare_controlled_runner_stage_execution`, and selects
the first stage (`loop-run-plan`) as `selected_not_executed`. The
valid packet's `selected_stage` object binds `step`, `command`, `evidence_files`,
`execution_authority`, and `allowed_side_effects_when_executed` from the
approved manifest command sequence while adding `stage_status:
selected_not_executed`, `runner_started: true`, `stage_execution_started:
false`, `executor_started: false`, and `side_effects: []`. The packet also
includes `controlled_loop_runner_start`, `controlled_loop_runner_plan`, and
`controlled_loop_runner_dry_run` summary objects with `file` and `checksum`,
matching `files` and `checksums` objects keyed by the same three evidence
names. `blockers` is an array of `{code, message, ...}` objects and is empty
when valid. When blocked, the packet sets `runner_started: false`,
`runner_next_stage_status: blocked`, `selected_stage: null`, and
`next_controlled_action` to the blocker-specific recommendation. `side_effects`
is always `[]` because the command is read-only.

It starts no executor, invokes no executor, retries no executor, continues no
loop, appends no audit evidence, executes no Git commands, calls no GitHub
APIs, creates no branches, commits, pushes, creates no PRs, merges no PRs,
releases no artifacts, publishes no packages, assigns no roles, and schedules
no agents. Stable blockers include
`controlled_runner_next_stage_start_evidence_missing`,
`controlled_runner_next_stage_runner_plan_evidence_missing`,
`controlled_runner_next_stage_dry_run_evidence_missing`,
`controlled_runner_next_stage_unsupported_stage`,
`controlled_runner_next_stage_start_packet_mismatch`,
`controlled_runner_next_stage_start_not_started`,
`controlled_runner_next_stage_start_authority_flags_invalid`,
`controlled_runner_next_stage_start_boundary_unrecorded`,
`controlled_runner_next_stage_runner_plan_file_mismatch`,
`controlled_runner_next_stage_dry_run_file_mismatch`,
`controlled_runner_next_stage_runner_plan_checksum_mismatch`,
`controlled_runner_next_stage_dry_run_checksum_mismatch`,
`controlled_runner_next_stage_dry_run_packet_mismatch`,
`controlled_runner_next_stage_dry_run_authority_flags_invalid`,
`controlled_runner_next_stage_dry_run_not_completed`,
`controlled_runner_next_stage_dry_run_non_execution_guarantees_missing`,
`controlled_runner_next_stage_dry_run_non_execution_guarantees_invalid`,
`controlled_runner_next_stage_dry_run_runner_plan_file_mismatch`,
`controlled_runner_next_stage_dry_run_runner_plan_checksum_mismatch`,
`controlled_runner_next_stage_dry_run_stage_malformed`,
`controlled_runner_next_stage_dry_run_stage_not_would_process`,
`controlled_runner_next_stage_dry_run_stage_sequence_mismatch`,
`controlled_runner_next_stage_unknown_stage`, and the
`controlled_runner_start_readiness_runner_plan_*` blockers emitted by
runner-plan revalidation. The
`controlled_runner_next_stage_start_boundary_unrecorded` blocker may include
nested diagnostic `audit_blockers` with codes
`controlled_runner_next_stage_start_audit_missing`,
`controlled_runner_next_stage_start_audit_summary_incomplete`,
`controlled_runner_next_stage_start_audit_summary_checksum_invalid`,
`controlled_runner_next_stage_start_audit_summary_chain_index_invalid`,
`controlled_runner_next_stage_start_audit_path_mismatch`,
`controlled_runner_next_stage_start_audit_line_unreadable`,
`controlled_runner_next_stage_start_audit_line_malformed`,
`controlled_runner_next_stage_start_audit_chain_invalid`,
`controlled_runner_next_stage_start_audit_line_invalid`,
`controlled_runner_next_stage_start_audit_event_mismatch`,
`controlled_runner_next_stage_start_audit_summary_mismatch`,
`controlled_runner_next_stage_start_audit_event_hash_mismatch`, and
`controlled_runner_next_stage_start_audit_field_mismatch`, and
`controlled_runner_next_stage_start_audit_payload_checksum_mismatch`.

`controlled-loop-runner-stage-execution-readiness` is the read-only approval
target packet after controlled runner next-stage selection. It reads exactly
one stage selection source: `--controlled-loop-runner-next-stage-file` for the
initial stage, or `--controlled-loop-runner-next-stage-continuation-file` plus
`--controlled-loop-runner-stage-input-binding-file` for a continuation stage.
Continuation-backed readiness also requires
`--expected-stage-input-binding-checksum` to pin the exact reviewed
stage-input binding packet. It also reads `--controlled-loop-runner-start-file`,
`--controlled-loop-runner-plan-file`, `--controlled-loop-runner-dry-run-file`,
and optional `--stage-number`. Initial-stage behavior remains scoped to the
saved `controlled-loop-runner-next-stage.v1` packet. Continuation-backed
readiness verifies the saved `controlled-loop-runner-next-stage-continuation.v1`
packet is valid, read-only, selected, and still matched by a bound
`controlled-loop-runner-stage-input-binding.v1` packet whose checksum matches
the expected reviewed checksum. The continuation and binding must also advance
exactly from completed stage `N` to requested stage `N+1`. Both paths
revalidate the upstream runner-start, runner-plan, and dry-run chain so the
selected stage cannot be approved from stale evidence.

When valid, the command emits
`controlled-loop-runner-stage-execution-readiness.v1` with
`packet: controlled_loop_runner_stage_execution_readiness`,
`read_only: true`, `runner_stage_execution_readiness_status: ready`,
`runner_started: true`, `process_started: false`,
`stage_execution_started: false`, `executor_started: false`,
`audit_evidence_appended: false`, and `loop_continuation_started: false`. It
converts the selected stage into `stage_status: ready_for_approval_not_executed`, sets
`execution_authority: operator_approval_required`, and emits
`stage_execution_approval_target` plus
`stage_execution_approval_target_checksum` for a later operator-approval
slice. The approval target binds the approval purpose, selected stage number,
selected command, readiness `generated_at` timestamp, upstream runner packet
checksums, and selected-stage checksum. Continuation-backed targets also bind
`stage_selection_source: continuation`, the continuation file/checksum, and the
stage-input binding file plus actual and expected reviewed checksums.

The command starts no runner stage, invokes no executor, retries no executor,
continues no loop, appends no audit evidence, executes no Git commands, calls
no GitHub APIs, creates no branches, commits, pushes, creates no PRs, merges
no PRs, releases no artifacts, publishes no packages, assigns no roles, and
schedules no agents. Stable blockers include
`controlled_runner_stage_execution_readiness_next_stage_evidence_missing`,
`controlled_runner_stage_execution_readiness_next_stage_packet_mismatch`,
`controlled_runner_stage_execution_readiness_next_stage_not_selected`,
`controlled_runner_stage_execution_readiness_next_stage_authority_flags_invalid`,
`controlled_runner_stage_execution_readiness_next_stage_limitations_missing`,
`controlled_runner_stage_execution_readiness_next_stage_limitations_invalid`,
`controlled_runner_stage_execution_readiness_next_stage_selected_stage_mismatch`,
`controlled_runner_stage_execution_readiness_selection_source_count_invalid`,
`controlled_runner_stage_execution_readiness_stage_input_binding_required`,
`controlled_runner_stage_execution_readiness_stage_input_binding_unexpected`,
`controlled_runner_stage_execution_readiness_stage_input_binding_checksum_required`,
`controlled_runner_stage_execution_readiness_continuation_evidence_missing`,
`controlled_runner_stage_execution_readiness_continuation_packet_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_not_selected`,
`controlled_runner_stage_execution_readiness_continuation_authority_flags_invalid`,
`controlled_runner_stage_execution_readiness_continuation_limitations_invalid`,
`controlled_runner_stage_execution_readiness_continuation_selected_stage_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_selected_stage_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_stage_sequence_non_adjacent`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_start_file_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_plan_file_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_dry_run_file_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_start_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_plan_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_continuation_controlled_loop_runner_dry_run_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_evidence_missing`,
`controlled_runner_stage_execution_readiness_stage_input_binding_packet_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_not_bound`,
`controlled_runner_stage_execution_readiness_stage_input_binding_authority_flags_invalid`,
`controlled_runner_stage_execution_readiness_stage_input_binding_limitations_invalid`,
`controlled_runner_stage_execution_readiness_stage_input_binding_stage_sequence_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_selected_stage_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_next_stage_continuation_file_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_start_file_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_plan_file_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_dry_run_file_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_continuation_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_start_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_plan_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_controlled_loop_runner_dry_run_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_selected_stage_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_stage_input_binding_checksum_missing`,
`controlled_runner_stage_execution_readiness_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_upstream_invalid`,
`controlled_runner_stage_execution_readiness_controlled_loop_runner_start_file_mismatch`,
`controlled_runner_stage_execution_readiness_controlled_loop_runner_start_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_controlled_loop_runner_plan_file_mismatch`,
`controlled_runner_stage_execution_readiness_controlled_loop_runner_plan_checksum_mismatch`,
`controlled_runner_stage_execution_readiness_controlled_loop_runner_dry_run_file_mismatch`,
and
`controlled_runner_stage_execution_readiness_controlled_loop_runner_dry_run_checksum_mismatch`.

`controlled-loop-runner-stage-execution-approval` is the read-only approval
packet after stage-execution readiness. It reads
`--controlled-loop-runner-stage-execution-readiness-file`,
either `--controlled-loop-runner-next-stage-file` or
`--controlled-loop-runner-next-stage-continuation-file` plus
`--controlled-loop-runner-stage-input-binding-file` and
`--expected-stage-input-binding-checksum`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--approval-file`,
optional `--start-governed-execution-approval-file`,
`--expected-operator-id`, `--approval-secret-env` or `--approval-secret`, and
optional `--stage-number`. It revalidates the full runner chain, rereads the
readiness packet, rechecks continuation and stage-input binding anchors when
the readiness source is continuation-backed, and verifies the supplied
`operator-approval.v1` through the shared
`build_operator_approval_verification_packet` path without appending audit
evidence. The stage-execution approval must use purpose
`controlled_loop_runner_stage_execution`, must have an approval-secret-backed
valid signature, its `operator_id` must match `--expected-operator-id`, and
its `target_checksum` must match the readiness packet's
`stage_execution_approval_target_checksum`.

When the selected continuation command is `start-governed-execution`, the
command requires `--start-governed-execution-approval-file` to contain a second
valid `operator-approval.v1` with purpose `start_governed_execution` and a
target checksum matching the executor task checksum from the
`controlled-loop-runner-stage-input-binding.v1` packet. Before deriving that
token, approval rereads the executor task file anchored by the binding and
requires the current file checksum to match the binding's expected approval
target, executor-task summary checksum, embedded checksum, prior-stage output
checksum, and checksum map anchors. Only after that approval verifies does the
packet derive the future
`approve-executor-task:<checksum>` token; it does not call
`start-governed-execution`, start a process, start an epoch, append audit
evidence, continue the loop, or write Git/GitHub state.

When valid, the command emits
`controlled-loop-runner-stage-execution-approval.v1` with
`packet: controlled_loop_runner_stage_execution_approval`, `read_only: true`,
`approval_status: completed`, `runner_stage_execution_authority:
operator_approved_not_executed`, `runner_started: true`,
`stage_execution_started: false`, `executor_started: false`, and
`loop_continuation_started: false`. It preserves the operator approval file,
checksum, target checksum, approval target checksum, purpose, approval purpose,
expected operator id, operator id, key id, issued/expires timestamps,
signature, and signature verification state. It marks the selected stage as
`approved_not_executed`. Initial approvals set `next_controlled_action:
prepare_controlled_runner_stage_invocation_boundary`; continuation approvals set
`next_controlled_action:
generalize_controlled_runner_stage_invocation_boundary_for_continuation` so the
invocation-boundary command can build the exact continuation stage boundary.

The command starts no runner stage, invokes no executor, retries no executor,
continues no loop, appends no audit evidence, executes no Git commands, calls
no GitHub APIs, creates no branches, commits, pushes, creates no PRs, merges
no PRs, releases no artifacts, publishes no packages, assigns no roles, and
schedules no agents. Stable blockers include
`controlled_runner_stage_execution_approval_readiness_evidence_missing`,
`controlled_runner_stage_execution_approval_readiness_packet_mismatch`,
`controlled_runner_stage_execution_approval_readiness_not_ready`,
`controlled_runner_stage_execution_approval_readiness_authority_flags_invalid`,
`controlled_runner_stage_execution_approval_readiness_generated_at_invalid`,
`controlled_runner_stage_execution_approval_readiness_envelope_mismatch`,
`controlled_runner_stage_execution_approval_readiness_limitations_invalid`,
`controlled_runner_stage_execution_approval_readiness_selected_stage_mismatch`,
`controlled_runner_stage_execution_approval_readiness_next_stage_file_mismatch`,
`controlled_runner_stage_execution_approval_readiness_next_stage_checksum_mismatch`,
`controlled_runner_stage_execution_approval_readiness_start_file_mismatch`,
`controlled_runner_stage_execution_approval_readiness_start_checksum_mismatch`,
`controlled_runner_stage_execution_approval_readiness_plan_file_mismatch`,
`controlled_runner_stage_execution_approval_readiness_plan_checksum_mismatch`,
`controlled_runner_stage_execution_approval_readiness_dry_run_file_mismatch`,
`controlled_runner_stage_execution_approval_readiness_dry_run_checksum_mismatch`,
`controlled_runner_stage_execution_approval_selection_source_count_invalid`,
`controlled_runner_stage_execution_approval_stage_input_binding_required`,
`controlled_runner_stage_execution_approval_stage_input_binding_unexpected`,
`controlled_runner_stage_execution_approval_stage_input_binding_checksum_required`,
`controlled_runner_stage_execution_approval_continuation_evidence_missing`,
`controlled_runner_stage_execution_approval_stage_input_binding_evidence_missing`,
`controlled_runner_stage_execution_approval_readiness_continuation_stage_sequence_non_adjacent`,
`controlled_runner_stage_execution_approval_executor_task_checksum_missing`,
`controlled_runner_stage_execution_approval_executor_task_file_missing`,
`controlled_runner_stage_execution_approval_executor_task_file_mismatch`,
`controlled_runner_stage_execution_approval_executor_task_file_unreadable`,
`controlled_runner_stage_execution_approval_executor_task_file_invalid`,
`controlled_runner_stage_execution_approval_executor_task_checksum_mismatch`,
`controlled_runner_stage_execution_approval_executor_task_approval_required`,
`controlled_runner_stage_execution_approval_target_missing`,
`controlled_runner_stage_execution_approval_target_checksum_mismatch`,
`controlled_runner_stage_execution_approval_target_purpose_mismatch`,
`controlled_runner_stage_execution_approval_target_mismatch`,
`controlled_runner_stage_execution_approval_next_stage_evidence_missing`,
`controlled_runner_stage_execution_approval_upstream_invalid`, and the
`operator_approval_*` blockers emitted by `operator-approval.v1`
verification, including `operator_approval_file_unreadable`,
`operator_approval_target_mismatch`,
`operator_approval_purpose_mismatch`, `operator_approval_expired`,
`operator_approval_issued_in_future`, `operator_approval_operator_missing`,
`operator_approval_operator_mismatch`,
`operator_approval_expected_operator_invalid`,
`operator_approval_secret_missing`, and `operator_approval_signature_invalid`.
Continuation-backed approval also maps every continuation and stage-input
binding readiness blocker above by replacing
`controlled_runner_stage_execution_readiness` with
`controlled_runner_stage_execution_approval_readiness`.

`controlled-loop-runner-stage-invocation-boundary` is the read-only invocation
boundary packet after stage-execution approval. It reads
`--controlled-loop-runner-stage-execution-approval-file`,
`--controlled-loop-runner-stage-execution-readiness-file`,
either `--controlled-loop-runner-next-stage-file` for the initial stage or
`--controlled-loop-runner-next-stage-continuation-file` plus
`--controlled-loop-runner-stage-input-binding-file` and
`--expected-stage-input-binding-checksum` for a continuation stage,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--stage-cwd`,
`--stage-output-file`, `--stage-timeout-seconds`, `--expected-operator-id`,
optional `--approval-secret` / `--approval-secret-env`, and optional
`--stage-number`. Ownership flags are parsed only to fail closed in Tasks
61-66. It revalidates the full runner chain, rereads the readiness and
initial next-stage packet or continuation next-stage plus stage-input binding
packets, requires a completed
`controlled-loop-runner-stage-execution-approval.v1` packet, rereads the saved
operator-approval file named by that approval packet, and re-verifies the
operator-approval signature, purpose, target checksum, and expected operator
through the shared operator-approval verifier. It confirms that the approved
selected stage still matches the revalidated readiness stage, the requested
stage number, and the command declared by the approved runner plan. For
continuation-backed inputs it also reuses the continuation/readiness
stage-adjacency and stage-input-binding checksum checks so the boundary cannot
advance from stale continuation evidence.
The approval packet's copied `stage_execution_approval_target`,
`stage_execution_approval_target_checksum`, nested approval `target_checksum`,
nested approval `approval_target_checksum`, purpose, approval purpose,
expected operator, signature verification state, and blocker codes must match
the rederived stage-execution approval target from the supplied readiness and
upstream runner chain plus the freshly reverified operator approval. Initial
approvals must still carry `next_controlled_action:
prepare_controlled_runner_stage_invocation_boundary`; continuation approvals
must carry `next_controlled_action:
generalize_controlled_runner_stage_invocation_boundary_for_continuation`.

When valid, the command emits
`controlled-loop-runner-stage-invocation-boundary.v1` with
`packet: controlled_loop_runner_stage_invocation_boundary`,
`read_only: true`, `boundary_status: completed`,
`runner_stage_execution_authority: boundary_prepared_not_started`,
`runner_started: true`, `stage_execution_started: false`,
`process_started: false`, `executor_started: false`, and
`loop_continuation_started: false`. The packet marks the selected stage as
`boundary_prepared_not_started`, preserves the source stage status, binds the
stage execution authority and allowed side effects from the approved runner
plan, and sets `next_controlled_action:
execute_approved_runner_stage_once`.

The `invocation_boundary` object includes the selected stage number, command
name, exact `argv`, normalized arguments, fixed working-directory policy,
stdout JSON evidence-output policy, finite timeout policy, execution authority,
and allowed side effects. The packet also emits
`invocation_boundary_checksum`, records file/checksum anchors for the
stage-execution approval, stage-execution readiness, initial next-stage or
continuation next-stage plus stage-input binding, runner-start, runner-plan,
and dry-run inputs, and records the planned stage output file
path. The current `loop-run-plan` boundary argv starts with the current Python
executable and `-m codex_cadence.cli`, and includes `--discovery-mode off` so
the argv is parser-valid without `--intent`. The planned output path must
have an existing directory parent and must not be an existing directory.
For continuation stage-2 `start-governed-execution`, boundary construction
rereads the executor task file anchored by the stage-input binding, re-verifies
the saved executor-task operator approval evidence recorded by the approval
packet, requires the current checksum to match every executor-task checksum
anchor in the binding and approval packet, requires the approval packet's
derived `start_governed_execution.approval_token` to match
`approve-executor-task:<current-task-checksum>`, and requires `--stage-cwd` to
match the executor task repo path. The emitted argv includes `--task-file`,
`--approval-token`, and `--cwd` for that command. It may also include
`--ownership-target`, `--ownership-role`, and `--ownership-claimer` only when
all three are supplied for continuation-backed stage-2
`start-governed-execution`, the live repo branch/HEAD and active ownership
record still match the executor task, and the approved runner-plan stage
declares `work_ownership_epoch_bound`.

The command starts no process, starts or closes no epoch, executes no runner
stage, invokes no executor, retries no executor, continues no loop, appends no
audit evidence, executes no Git commands, calls no GitHub APIs, creates no
branches, commits, pushes, creates no PRs, merges no PRs, releases no
artifacts, publishes no packages, assigns no roles, and schedules no agents.
Stable blockers include
`controlled_runner_stage_invocation_boundary_approval_evidence_missing`,
`controlled_runner_stage_invocation_boundary_readiness_evidence_missing`,
`controlled_runner_stage_invocation_boundary_next_stage_evidence_missing`,
`controlled_runner_stage_invocation_boundary_continuation_evidence_missing`,
`controlled_runner_stage_invocation_boundary_stage_input_binding_evidence_missing`,
`controlled_runner_stage_invocation_boundary_upstream_invalid`,
`controlled_runner_stage_invocation_boundary_approval_packet_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_not_completed`,
`controlled_runner_stage_invocation_boundary_approval_authority_flags_invalid`,
`controlled_runner_stage_invocation_boundary_approval_limitations_invalid`,
`controlled_runner_stage_invocation_boundary_approval_selected_stage_mismatch`,
`controlled_runner_stage_invocation_boundary_stage_number_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_readiness_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_readiness_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_next_stage_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_next_stage_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_continuation_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_continuation_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_stage_input_binding_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_start_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_start_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_plan_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_plan_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_dry_run_file_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_dry_run_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_target_missing`,
`controlled_runner_stage_invocation_boundary_approval_target_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_target_mismatch`,
`controlled_runner_stage_invocation_boundary_approval_identity_mismatch`,
`controlled_runner_stage_invocation_boundary_operator_approval_file_missing`,
`controlled_runner_stage_invocation_boundary_operator_approval_file_mismatch`,
`controlled_runner_stage_invocation_boundary_operator_approval_file_unreadable`,
`controlled_runner_stage_invocation_boundary_operator_approval_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_selection_source_count_invalid`,
`controlled_runner_stage_invocation_boundary_stage_input_binding_required`,
`controlled_runner_stage_invocation_boundary_stage_input_binding_unexpected`,
`controlled_runner_stage_invocation_boundary_stage_input_binding_checksum_unexpected`,
`controlled_runner_stage_invocation_boundary_stage_input_binding_checksum_required`,
`controlled_runner_stage_invocation_boundary_ownership_arguments_incomplete`,
`controlled_runner_stage_invocation_boundary_ownership_not_supported`,
`controlled_runner_stage_invocation_boundary_ownership_side_effect_policy_missing`,
`controlled_runner_stage_invocation_boundary_live_repo_unreadable`,
`controlled_runner_stage_invocation_boundary_live_repo_branch_mismatch`,
`controlled_runner_stage_invocation_boundary_live_repo_head_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_record_missing`,
`controlled_runner_stage_invocation_boundary_ownership_closed`,
`controlled_runner_stage_invocation_boundary_ownership_stale`,
`controlled_runner_stage_invocation_boundary_duplicate_active_ownership`,
`controlled_runner_stage_invocation_boundary_ownership_repo_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_branch_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_head_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_candidate_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_role_mismatch`,
`controlled_runner_stage_invocation_boundary_ownership_claimer_mismatch`,
`controlled_runner_stage_invocation_boundary_start_governed_execution_binding_missing`,
`controlled_runner_stage_invocation_boundary_executor_task_file_missing`,
`controlled_runner_stage_invocation_boundary_executor_task_file_unreadable`,
`controlled_runner_stage_invocation_boundary_executor_task_file_invalid`,
`controlled_runner_stage_invocation_boundary_executor_task_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_start_governed_execution_task_file_mismatch`,
`controlled_runner_stage_invocation_boundary_start_governed_execution_approval_token_mismatch`,
`controlled_runner_stage_invocation_boundary_executor_task_approval_missing`,
`controlled_runner_stage_invocation_boundary_executor_task_approval_file_mismatch`,
`controlled_runner_stage_invocation_boundary_executor_task_approval_file_unreadable`,
`controlled_runner_stage_invocation_boundary_executor_task_approval_checksum_mismatch`,
`controlled_runner_stage_invocation_boundary_executor_task_approval_identity_mismatch`,
`controlled_runner_stage_invocation_boundary_executor_task_repo_path_missing`,
`controlled_runner_stage_invocation_boundary_stage_cwd_mismatch`,
`controlled_runner_stage_invocation_boundary_root_missing`,
`controlled_runner_stage_invocation_boundary_cwd_invalid`,
`controlled_runner_stage_invocation_boundary_output_file_invalid`,
`controlled_runner_stage_invocation_boundary_timeout_invalid`,
`controlled_runner_stage_invocation_boundary_unknown_stage_command`,
`controlled_runner_stage_invocation_boundary_side_effect_policy_missing`, and
`controlled_runner_stage_invocation_boundary_execution_authority_missing`,
plus shared `operator_approval_*` blockers from the operator-approval verifier
such as `operator_approval_secret_missing`,
`operator_approval_signature_invalid`, `operator_approval_target_mismatch`,
and `operator_approval_operator_mismatch`.

`controlled-loop-runner-stage-execute` is the controlled single-stage execution
packet after stage-invocation-boundary review. It reads
`--controlled-loop-runner-stage-invocation-boundary-file`,
`--expected-invocation-boundary-checksum`,
`--controlled-loop-runner-stage-execution-approval-file`,
`--controlled-loop-runner-stage-execution-readiness-file`,
either `--controlled-loop-runner-next-stage-file` or
`--controlled-loop-runner-next-stage-continuation-file` plus
`--controlled-loop-runner-stage-input-binding-file` and
`--expected-stage-input-binding-checksum`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
`--approval-secret` or `--approval-secret-env`, and optional `--stage-number`.
It requires a runtime `--root` for the execution audit record. Before process
start, it revalidates the upstream runner start/plan/dry-run chain, rereads
the selection packet, readiness, approval, and invocation-boundary packets,
verifies their file/checksum anchors, re-verifies the saved operator approval
signature, purpose, target checksum, and expected operator through the shared
operator-approval verifier, confirms the approval remains operator-approved for
purpose `controlled_loop_runner_stage_execution`, and requires the invocation
boundary checksum to match `--expected-invocation-boundary-checksum`. For
continuation execution it also rechecks the continuation packet, matching
stage-input binding packet, and reviewed binding checksum. It requires the
exact argv, normalized arguments, fixed cwd policy, stdout JSON output policy,
finite timeout, execution authority, and allowed side-effect policy to match
the approved runner plan.

When pre-start validation passes, the command invokes exactly one stage command
using the boundary `argv`, boundary fixed `cwd`, `capture_output=True`,
`text=True`, `check=False`, the boundary timeout, and `shell=False`. It writes
captured stdout to the approved stage output file, records stdout, stderr,
return code, timestamps, timeout state, and `shell: false` in
`command_result`, emits `command_result_checksum`, and appends at most one
`controlled_runner_stage_execution` audit record with the stage number,
boundary checksum, approval checksum, readiness checksum, payload checksum,
command-result checksum, output path, return code, and timeout state.
Successful stage stdout must be nonempty JSON evidence. The command observes
explicit stdout `side_effects` plus true side-effect flags such as
`epoch_started` and `github_write_started`, and every observed effect must stay
within the approved stage's `allowed_side_effects_when_executed`, even when the
stage exits nonzero. For continuation stage-2 `start-governed-execution`, the
approved effects are `epoch_started`, one `execution_start_decision` audit
append reported by the child command after process start, and
`work_ownership_epoch_bound` only when the reviewed boundary carried ownership
arguments.

The valid packet is
`controlled-loop-runner-stage-execution.v1` with
`packet: controlled_loop_runner_stage_execution`. It sets
`stage_execution_status: completed` for return code `0`,
`stage_execution_status: failed` for a nonzero exit or timeout with otherwise
valid terminal evidence, and recommends `closeout_controlled_runner_stage`.
Pre-start blockers emit `stage_execution_status: blocked`, keep
`stage_execution_started: false`, `process_started: false`,
`command_result: null`, and `side_effects: []`, and append no audit evidence.

The command does not invoke an executor, retry an executor, execute a second
stage, continue the loop, execute Git commands, call GitHub APIs, create
branches, commit, push, create PRs, merge, release, publish packages, assign
roles, or schedule agents. Stable blockers include
`controlled_runner_stage_execution_root_missing`,
`controlled_runner_stage_execution_boundary_evidence_missing`,
`controlled_runner_stage_execution_approval_evidence_missing`,
`controlled_runner_stage_execution_readiness_evidence_missing`,
`controlled_runner_stage_execution_next_stage_evidence_missing`,
`controlled_runner_stage_execution_continuation_evidence_missing`,
`controlled_runner_stage_execution_stage_input_binding_evidence_missing`,
`controlled_runner_stage_execution_upstream_invalid`,
`controlled_runner_stage_execution_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_execution_approval_packet_mismatch`,
`controlled_runner_stage_execution_approval_not_completed`,
`controlled_runner_stage_execution_approval_target_checksum_mismatch`,
`controlled_runner_stage_execution_approval_target_mismatch`,
`controlled_runner_stage_execution_approval_identity_mismatch`,
`controlled_runner_stage_execution_boundary_packet_mismatch`,
`controlled_runner_stage_execution_boundary_not_completed`,
`controlled_runner_stage_execution_boundary_checksum_mismatch`,
`controlled_runner_stage_execution_expected_boundary_checksum_mismatch`,
`controlled_runner_stage_execution_boundary_selected_stage_mismatch`,
`controlled_runner_stage_execution_runtime_root_unsafe`,
`controlled_runner_stage_execution_cwd_invalid`,
`controlled_runner_stage_execution_output_file_invalid`,
`controlled_runner_stage_execution_timeout_invalid`,
`controlled_runner_stage_execution_argv_invalid`,
`controlled_runner_stage_execution_unapproved_command`,
`controlled_runner_stage_execution_stdout_missing`,
`controlled_runner_stage_execution_stdout_not_json`,
`controlled_runner_stage_execution_stage_side_effects_invalid`,
`controlled_runner_stage_execution_undeclared_side_effects`,
`controlled_runner_stage_execution_unexpected_ownership_side_effect`,
`controlled_runner_stage_execution_process_start_failed`,
`controlled_runner_stage_execution_output_write_failed`, and
`controlled_runner_stage_execution_audit_append_failed`, plus shared
`operator_approval_*` blockers from the operator-approval verifier such as
`operator_approval_secret_missing`, `operator_approval_signature_invalid`,
`operator_approval_target_mismatch`, and `operator_approval_operator_mismatch`.

`controlled-loop-runner-stage-closeout` is the read-only terminal closeout for
one saved runner-stage execution. It reads
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-stage-invocation-boundary-file`,
`--controlled-loop-runner-stage-execution-approval-file`,
`--controlled-loop-runner-stage-execution-readiness-file`,
exactly one of `--controlled-loop-runner-next-stage-file` or
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file` and
`--expected-stage-input-binding-checksum` when the continuation source is used,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
`--approval-secret` or `--approval-secret-env`, optional `--stage-output-file`,
and optional `--stage-number`. It consumes already-recorded evidence only. The
command must not start a process, execute a runner stage, invoke or retry an
executor, execute a second stage, select another stage, continue the loop,
append audit evidence, execute Git commands, call GitHub APIs, create branches,
commit, push, create PRs, merge, release, publish packages, assign roles, or
schedule agents.

Closeout rereads the full runner chain and reuses the same upstream validation
as stage execution: runner plan, dry-run, runner start, exactly one
stage-selection source, stage-execution readiness, stage-execution approval,
invocation boundary, and the saved operator approval must remain anchored by
their recorded file and checksum fields. Continuation closeout requires the
next-stage continuation packet, matching stage-input binding packet, and
reviewed binding checksum to match the saved continuation-backed execution
chain. The operator approval is verified through the shared
approval-secret-backed signature verifier, must have purpose
`controlled_loop_runner_stage_execution`, must match the expected operator,
and its target checksum must match
`stage_execution_approval_target_checksum`. The stage execution packet must be
`controlled-loop-runner-stage-execution.v1` for the same selected stage,
boundary checksum, approval checksum, readiness checksum, stage-selection checksum,
runner-start checksum, runner-plan checksum, dry-run checksum, argv, cwd,
timeout, output policy, output file, and `command_result_checksum`.

When the invocation boundary uses stdout JSON output evidence, closeout derives
the expected output path from that boundary policy. If the stage execution is
completed, the approved output file must exist, match the expected path, match
the captured stdout byte-for-byte after the same text capture semantics, and
parse as a nonempty JSON object matching the selected stage's expected
evidence identity. For the current `loop-run-plan` runner stage, that means
`schema_version: loop-run-plan.v1` and `packet: loop_run_plan`; real
`loop-run-plan.v1` packets do not emit a top-level `valid` field. For
continuation stage-2 `start-governed-execution`, completed closeout requires
`schema_version: execution-start.v1`, `packet: execution_start`, `valid: true`,
`read_only: false`, `epoch_started: true`, `executor_started: false`,
`pr_action_started: false`, `approval_state: approved`, empty `blockers`,
`recommended_next_action: handoff_to_executor`, an object `audit_record`, and
`task_file`, `task_checksum`, `task_id`, and `repo.path` values that match the
approved continuation input binding and boundary command context. The
`audit_record` summary must identify an `execution_start_decision` for the same
task id and checksum. Closeout does not read or append audit evidence while
checking that identity.
A failed stage may close out with empty or non-JSON diagnostic stdout when the
terminal execution evidence is otherwise consistent; it is not retried and no
continuation is selected.

A valid closeout emits
`controlled-loop-runner-stage-closeout.v1` with
`packet: controlled_loop_runner_stage_closeout`, `read_only: true`,
`side_effects: []`, `audit_evidence_appended: false`, the selected stage,
references to every consumed packet and checksum, output evidence when
required, and `stage_closeout_status`. `stage_closeout_status` is `completed`
for a consistent successful stage execution, `failed` for a consistent
terminal failed stage execution, and `blocked` when closeout evidence is
missing, mutated, stale, or inconsistent. Completed and blocked closeouts
recommend `plan_controlled_runner_stage_outcome`; failed closeouts recommend
`inspect_controlled_runner_stage_failure`. Closeout does not grant runner,
executor, continuation, Git/GitHub, merge, release, package publication, role,
or scheduling authority.

Stable blockers include
`controlled_runner_stage_closeout_execution_evidence_missing`,
`controlled_runner_stage_closeout_boundary_evidence_missing`,
`controlled_runner_stage_closeout_approval_evidence_missing`,
`controlled_runner_stage_closeout_readiness_evidence_missing`,
`controlled_runner_stage_closeout_next_stage_evidence_missing`,
`controlled_runner_stage_closeout_continuation_evidence_missing`,
`controlled_runner_stage_closeout_stage_input_binding_evidence_missing`,
`controlled_runner_stage_closeout_stage_input_binding_required`,
`controlled_runner_stage_closeout_stage_input_binding_checksum_required`,
`controlled_runner_stage_closeout_execution_packet_mismatch`,
`controlled_runner_stage_closeout_execution_not_valid`,
`controlled_runner_stage_closeout_root_missing`,
`controlled_runner_stage_closeout_execution_status_invalid`,
`controlled_runner_stage_closeout_execution_not_started`,
`controlled_runner_stage_closeout_execution_forbidden_flags`,
`controlled_runner_stage_closeout_stage_number_mismatch`,
`controlled_runner_stage_closeout_command_result_missing`,
`controlled_runner_stage_closeout_command_result_checksum_mismatch`,
`controlled_runner_stage_closeout_command_result_boundary_mismatch`,
`controlled_runner_stage_closeout_command_result_status_mismatch`,
`controlled_runner_stage_closeout_command_result_invalid`,
`controlled_runner_stage_closeout_invocation_boundary_checksum_mismatch`,
`controlled_runner_stage_closeout_stage_output_missing`,
`controlled_runner_stage_closeout_stage_output_file_mismatch`,
`controlled_runner_stage_closeout_stage_output_checksum_mismatch`,
`controlled_runner_stage_closeout_stage_output_unreadable`,
`controlled_runner_stage_closeout_stage_output_not_json`,
`controlled_runner_stage_closeout_stage_output_not_json_object`,
`controlled_runner_stage_closeout_stage_output_packet_mismatch`,
`controlled_runner_stage_closeout_execution_start_output_invalid`,
`controlled_runner_stage_closeout_execution_start_output_identity_mismatch`,
`controlled_runner_stage_closeout_execution_start_audit_record_missing`,
`controlled_runner_stage_closeout_selected_stage_mismatch`,
`controlled_runner_stage_closeout_boundary_file_mismatch`,
`controlled_runner_stage_closeout_approval_file_mismatch`,
`controlled_runner_stage_closeout_readiness_file_mismatch`,
`controlled_runner_stage_closeout_next_stage_file_mismatch`,
`controlled_runner_stage_closeout_continuation_file_mismatch`,
`controlled_runner_stage_closeout_stage_input_binding_file_mismatch`,
`controlled_runner_stage_closeout_start_file_mismatch`,
`controlled_runner_stage_closeout_plan_file_mismatch`,
`controlled_runner_stage_closeout_dry_run_file_mismatch`,
`controlled_runner_stage_closeout_boundary_checksum_mismatch`,
`controlled_runner_stage_closeout_approval_target_checksum_mismatch`,
`controlled_runner_stage_closeout_approval_checksum_mismatch`,
`controlled_runner_stage_closeout_readiness_checksum_mismatch`,
`controlled_runner_stage_closeout_next_stage_checksum_mismatch`,
`controlled_runner_stage_closeout_continuation_checksum_mismatch`,
`controlled_runner_stage_closeout_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_closeout_stage_input_binding_expected_checksum_mismatch`,
`controlled_runner_stage_closeout_start_checksum_mismatch`,
`controlled_runner_stage_closeout_plan_checksum_mismatch`,
`controlled_runner_stage_closeout_dry_run_checksum_mismatch`,
`controlled_runner_stage_closeout_stdout_missing`,
`controlled_runner_stage_closeout_stdout_not_json`,
`controlled_runner_stage_closeout_stage_side_effects_invalid`, and
`controlled_runner_stage_closeout_undeclared_side_effects`, plus
`controlled_runner_stage_closeout_upstream_invalid` and other rewritten
upstream next-stage, stage-boundary, stage-approval, and stage-readiness
blockers, plus shared `operator_approval_*` blockers from the operator-approval
verifier such as `operator_approval_secret_missing`,
`operator_approval_signature_invalid`, `operator_approval_target_mismatch`,
and `operator_approval_operator_mismatch`.

`controlled-loop-runner-stage-outcome-plan` is the read-only outcome planner
for one closed-out runner stage. It reads
`--controlled-loop-runner-stage-closeout-file`,
`--expected-stage-closeout-checksum`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-stage-invocation-boundary-file`,
`--controlled-loop-runner-stage-execution-approval-file`,
`--controlled-loop-runner-stage-execution-readiness-file`,
exactly one of `--controlled-loop-runner-next-stage-file` or
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file` and
`--expected-stage-input-binding-checksum` when the continuation source is used,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
`--approval-secret` or `--approval-secret-env`, and optional
`--stage-number`. It consumes already-recorded evidence only. The command must
not select a next stage, execute a retry, continue the loop, append audit
evidence, start a process, invoke an executor, execute Git commands, call
GitHub APIs, create branches, commit, push, create PRs, merge, release,
publish packages, assign roles, or schedule agents.

Outcome planning rereads and rechecks the closeout, execution, invocation
boundary, stage-execution approval, stage-execution readiness, exactly one
stage-selection source, runner-start, runner-plan, and dry-run chain. For a
continuation source it also rechecks the next-stage continuation packet,
stage-input binding packet, reviewed binding checksum, and the continuation
anchors saved in closeout. It requires the closeout checksum to match the
reviewed `--expected-stage-closeout-checksum` and reuses the same saved
operator approval verification as closeout, including expected operator id,
approval purpose `controlled_loop_runner_stage_execution`, approval target
checksum, file anchors, and checksums. The closeout packet must be
`controlled-loop-runner-stage-closeout.v1` for the requested stage and may
carry `stage_closeout_status: completed`, `failed`, or `blocked`; blocked
closeouts remain valid inputs for inspection planning only when they retain the
closed-out blocked shape emitted by closeout: `valid: false` with a non-empty
`blockers` list and otherwise self-consistent anchors.

A valid packet emits
`controlled-loop-runner-stage-outcome-plan.v1` with
`packet: controlled_loop_runner_stage_outcome_plan`, `read_only: true`,
`side_effects: []`, `stage_outcome_plan_status: completed`, references to
every consumed packet and checksum, `outcome_target`, and
`outcome_target_checksum`. Completed non-final stages produce
`stage_outcome_decision: select_next_stage` and
`next_controlled_action: select_controlled_runner_next_stage_continuation`
without selecting the next stage. Completed final stages produce
`stage_outcome_decision: complete_runner` and
`next_controlled_action: complete_controlled_runner`. A completed continuation
stage-2 closeout is treated as the final stage for this slice and targets
`controlled_loop_runner_completion`; it does not select stage 3 or continue
the loop. Failed stages produce
`stage_outcome_decision: inspect_stage_failure`; blocked stages produce
`stage_outcome_decision: inspect_stage_blocked`. Failed and blocked packets
also include a `controlled_loop_runner_stage_retry_planning` target with
`operator_approval_required: true`; no retry is executed or authorized.

Stable blockers include
`controlled_runner_stage_outcome_plan_closeout_evidence_missing`,
`controlled_runner_stage_outcome_plan_execution_evidence_missing`,
`controlled_runner_stage_outcome_plan_boundary_evidence_missing`,
`controlled_runner_stage_outcome_plan_approval_evidence_missing`,
`controlled_runner_stage_outcome_plan_readiness_evidence_missing`,
`controlled_runner_stage_outcome_plan_next_stage_evidence_missing`,
`controlled_runner_stage_outcome_plan_continuation_evidence_missing`,
`controlled_runner_stage_outcome_plan_stage_input_binding_evidence_missing`,
`controlled_runner_stage_outcome_plan_stage_input_binding_required`,
`controlled_runner_stage_outcome_plan_stage_input_binding_checksum_required`,
`controlled_runner_stage_outcome_plan_upstream_invalid`,
`controlled_runner_stage_outcome_plan_root_missing`,
`controlled_runner_stage_outcome_plan_closeout_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_packet_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_status_invalid`,
`controlled_runner_stage_outcome_plan_closeout_not_closed_out`,
`controlled_runner_stage_outcome_plan_closeout_forbidden_flags`,
`controlled_runner_stage_outcome_plan_closeout_stage_number_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_execution_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_boundary_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_approval_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_readiness_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_next_stage_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_continuation_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_stage_input_binding_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_start_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_plan_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_dry_run_file_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_execution_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_boundary_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_approval_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_readiness_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_next_stage_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_continuation_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_stage_input_binding_expected_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_start_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_plan_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_dry_run_checksum_mismatch`,
`controlled_runner_stage_outcome_plan_closeout_selected_stage_mismatch`,
and rewritten upstream stage-execution, stage-boundary, stage-approval,
stage-readiness, and next-stage blockers, plus shared `operator_approval_*`
blockers from the operator-approval verifier.

`controlled-loop-runner-stage-retry-plan` is the read-only retry approval-target
packet for failed or blocked initial or continuation controlled runner stage
outcomes. It reads
`--controlled-loop-runner-stage-outcome-plan-file`,
`--expected-stage-outcome-plan-checksum`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, and optional `--stage-number` (default
`1`). Continuation retry planning also reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`. It consumes already-recorded evidence
only. The command must not continue the runner or loop, select another stage,
emit stage-execution readiness, execute a retry, append audit evidence, start a
process, invoke an executor, execute Git commands, call GitHub APIs, create
branches, commit, push, create PRs, merge, release, publish packages, assign
roles, or schedule agents. Ownership-bound continuation evidence is treated as
already-recorded source execution/closeout evidence; retry planning does not
create or mutate ownership records and does not revalidate active ownership.

Retry planning rereads and rechecks the reviewed stage outcome plan, source
closeout, source execution packet, runner-start evidence, runner-plan evidence,
and dry-run evidence. It requires the outcome-plan checksum to match the
reviewed `--expected-stage-outcome-plan-checksum`, the outcome plan to be valid
and completed, and the outcome plan to carry either
`stage_outcome_decision: inspect_stage_failure` or
`stage_outcome_decision: inspect_stage_blocked` with the matching
`next_controlled_action`. The nested `retry_planning_target` must carry
`purpose: controlled_loop_runner_stage_retry_planning`,
`operator_approval_required: true`, false retry-start flags, the requested
stage number, and checksum anchors for the saved closeout, execution,
runner-start, runner-plan, and dry-run packets. Continuation retry planning
additionally requires selected
`controlled-loop-runner-next-stage-continuation.v1` evidence, bound
`controlled-loop-runner-stage-input-binding.v1` evidence, the reviewed
stage-input binding checksum, matching continuation/input-binding linkage, and
matching continuation/input-binding anchors in the outcome plan, closeout, and
execution packets. The source execution packet must prove one executed runner
stage: valid executed evidence, started runner and process flags, execution
side-effect and audit proof, a command-result checksum and returncode compatible
with the execution status, empty execution blockers,
`runner_stage_execution_authority: stage_executed_once`, expected
stage-execution limitations, `next_controlled_action:
closeout_controlled_runner_stage`, and a selected stage that still matches the
approved runner plan. Continuation source execution may report an
already-recorded `epoch_started: true` from a governed `start-governed-execution`
stage; the retry-plan packet itself still reports `epoch_started: false`.

A valid packet emits `controlled-loop-runner-stage-retry-plan.v1` with
`packet: controlled_loop_runner_stage_retry_plan`, `read_only: true`,
`side_effects: []`, `stage_retry_plan_status: planned`, references to every
consumed packet and checksum, `retry_planning_target`, and a
`retry_approval_target` plus checksum. It sets `recommended_next_action` to
`review_controlled_runner_stage_retry_plan` and `next_controlled_action` to
`approve_controlled_runner_stage_retry`; the packet grants no retry execution,
continuation, executor, Git/GitHub, merge, release, publication, role, or
scheduling authority.

Stable blockers include
`controlled_runner_stage_retry_plan_outcome_evidence_missing`,
`controlled_runner_stage_retry_plan_closeout_evidence_missing`,
`controlled_runner_stage_retry_plan_execution_evidence_missing`,
`controlled_runner_stage_retry_plan_upstream_invalid`,
`controlled_runner_stage_retry_plan_root_missing`,
`controlled_runner_stage_retry_plan_continuation_evidence_missing`,
`controlled_runner_stage_retry_plan_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_plan_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_plan_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_plan_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_plan_continuation_invalid`,
`controlled_runner_stage_retry_plan_continuation_authority_flags_invalid`,
`controlled_runner_stage_retry_plan_continuation_selected_stage_checksum_mismatch`,
`controlled_runner_stage_retry_plan_continuation_selected_stage_mismatch`,
`controlled_runner_stage_retry_plan_continuation_start_file_mismatch`,
`controlled_runner_stage_retry_plan_continuation_start_checksum_mismatch`,
`controlled_runner_stage_retry_plan_continuation_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_plan_continuation_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_plan_continuation_dry_run_file_mismatch`,
`controlled_runner_stage_retry_plan_continuation_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_invalid`,
`controlled_runner_stage_retry_plan_stage_input_binding_authority_flags_invalid`,
`controlled_runner_stage_retry_plan_stage_input_binding_selected_stage_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_stage_sequence_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_selected_stage_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_continuation_stage_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_continuation_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_start_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_start_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_dry_run_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_prior_stage_output_file_missing`,
`controlled_runner_stage_retry_plan_stage_input_binding_prior_stage_output_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_prior_stage_output_file_unreadable`,
`controlled_runner_stage_retry_plan_stage_input_binding_prior_stage_output_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_executor_task_file_missing`,
`controlled_runner_stage_retry_plan_stage_input_binding_executor_task_file_mismatch`,
`controlled_runner_stage_retry_plan_stage_input_binding_executor_task_file_unreadable`,
`controlled_runner_stage_retry_plan_stage_input_binding_executor_task_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_packet_mismatch`,
`controlled_runner_stage_retry_plan_outcome_not_completed`,
`controlled_runner_stage_retry_plan_outcome_stage_number_mismatch`,
`controlled_runner_stage_retry_plan_selection_source_mismatch`,
`controlled_runner_stage_retry_plan_outcome_not_retryable`,
`controlled_runner_stage_retry_plan_outcome_decision_mismatch`,
`controlled_runner_stage_retry_plan_target_missing`,
`controlled_runner_stage_retry_plan_target_checksum_mismatch`,
`controlled_runner_stage_retry_plan_target_mismatch`,
`controlled_runner_stage_retry_plan_outcome_target_missing`,
`controlled_runner_stage_retry_plan_outcome_target_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_target_mismatch`,
`controlled_runner_stage_retry_plan_outcome_closeout_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_closeout_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_execution_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_execution_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_start_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_start_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_dry_run_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_continuation_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_plan_outcome_stage_input_binding_file_mismatch`,
`controlled_runner_stage_retry_plan_outcome_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_packet_mismatch`,
`controlled_runner_stage_retry_plan_closeout_not_retryable`,
`controlled_runner_stage_retry_plan_closeout_stage_number_mismatch`,
`controlled_runner_stage_retry_plan_closeout_status_mismatch`,
`controlled_runner_stage_retry_plan_closeout_execution_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_execution_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_start_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_start_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_dry_run_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_continuation_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_stage_input_binding_file_mismatch`,
`controlled_runner_stage_retry_plan_closeout_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_plan_closeout_selected_stage_mismatch`,
`controlled_runner_stage_retry_plan_execution_packet_mismatch`,
`controlled_runner_stage_retry_plan_execution_not_executed`,
`controlled_runner_stage_retry_plan_execution_closeout_status_mismatch`,
`controlled_runner_stage_retry_plan_execution_not_started`,
`controlled_runner_stage_retry_plan_execution_proof_missing`,
`controlled_runner_stage_retry_plan_command_result_missing`,
`controlled_runner_stage_retry_plan_command_result_checksum_mismatch`,
`controlled_runner_stage_retry_plan_command_result_status_mismatch`,
`controlled_runner_stage_retry_plan_execution_not_self_consistent`,
`controlled_runner_stage_retry_plan_execution_stage_number_mismatch`,
`controlled_runner_stage_retry_plan_execution_forbidden_flags`,
`controlled_runner_stage_retry_plan_execution_continuation_file_mismatch`,
`controlled_runner_stage_retry_plan_execution_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_plan_execution_stage_input_binding_file_mismatch`,
`controlled_runner_stage_retry_plan_execution_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_plan_execution_start_file_mismatch`,
`controlled_runner_stage_retry_plan_execution_start_checksum_mismatch`,
`controlled_runner_stage_retry_plan_execution_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_plan_execution_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_plan_execution_dry_run_file_mismatch`,
`controlled_runner_stage_retry_plan_execution_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_plan_stage_missing_from_runner_plan`, and
`controlled_runner_stage_retry_plan_execution_selected_stage_plan_mismatch`.

`controlled-loop-runner-stage-retry-approval` is the read-only approval evidence
packet for a reviewed controlled runner stage retry target. It reads
`--controlled-loop-runner-stage-retry-plan-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--approval-file`,
`--expected-operator-id`, approval-secret inputs, and optional `--stage-number`
(default `1`). Continuation retry approval also reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`. The operator approval purpose must be
`controlled_loop_runner_stage_retry`, and its target checksum must match the
retry plan's `retry_approval_target_checksum`.

Retry approval rereads and rechecks the planned retry-plan packet, source
outcome plan, closeout, execution, runner-start, runner-plan, dry-run, and, for
continuation sources, continuation and stage-input binding packets. It requires
the retry plan to remain valid, planned, read-only, and approval-target-only,
requires the retry plan's `retry_approval_target` checksum to remain current,
and rechecks source anchors before accepting the approval. A valid packet emits
`controlled-loop-runner-stage-retry-approval.v1` with
`packet: controlled_loop_runner_stage_retry_approval`, `read_only: true`,
`approval_status: completed`, the accepted `retry_approval_target`, approval
identity/signature details, `recommended_next_action:
review_controlled_runner_stage_retry_approval`, and `next_controlled_action:
prepare_controlled_runner_stage_retry_boundary`. The packet does not emit
stage-execution readiness, prepare a retry boundary, start a process, execute a
retry, invoke an executor, continue the loop, append audit evidence, write
Git/GitHub state, merge, release, publish packages, assign roles, or schedule
agents.

Stable blockers include
`controlled_runner_stage_retry_approval_retry_plan_evidence_missing`,
`controlled_runner_stage_retry_approval_outcome_evidence_missing`,
`controlled_runner_stage_retry_approval_closeout_evidence_missing`,
`controlled_runner_stage_retry_approval_execution_evidence_missing`,
`controlled_runner_stage_retry_approval_upstream_invalid`,
`controlled_runner_stage_retry_approval_root_missing`,
`controlled_runner_stage_retry_approval_continuation_evidence_missing`,
`controlled_runner_stage_retry_approval_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_approval_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_approval_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_approval_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_approval_retry_plan_packet_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_not_planned`,
`controlled_runner_stage_retry_approval_retry_plan_limitations_invalid`,
`controlled_runner_stage_retry_approval_stage_number_mismatch`,
`controlled_runner_stage_retry_approval_target_missing`,
`controlled_runner_stage_retry_approval_target_checksum_mismatch`,
`controlled_runner_stage_retry_approval_target_purpose_mismatch`,
`controlled_runner_stage_retry_approval_target_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_authority_flags_invalid`,
`controlled_runner_stage_retry_approval_retry_plan_stage_outcome_plan_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_outcome_plan_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_closeout_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_closeout_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_execution_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_execution_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_start_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_start_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_dry_run_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_continuation_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_input_binding_file_mismatch`,
`controlled_runner_stage_retry_approval_retry_plan_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_approval_continuation_start_file_mismatch`,
`controlled_runner_stage_retry_approval_continuation_start_checksum_mismatch`,
`controlled_runner_stage_retry_approval_continuation_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_approval_continuation_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_approval_continuation_dry_run_file_mismatch`,
`controlled_runner_stage_retry_approval_continuation_dry_run_checksum_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_continuation_file_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_start_file_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_start_checksum_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_runner_plan_file_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_dry_run_file_mismatch`,
`controlled_runner_stage_retry_approval_stage_input_binding_dry_run_checksum_mismatch`,
and shared `operator_approval_*` blockers from the operator-approval verifier.

`controlled-loop-runner-stage-retry-boundary` is the read-only retry
invocation-boundary evidence packet for one approved controlled runner stage
retry attempt. It reads
`--controlled-loop-runner-stage-retry-approval-file`,
`--controlled-loop-runner-stage-retry-plan-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--stage-cwd`,
`--stage-retry-output-file`, `--stage-timeout-seconds`, `--retry-attempt`,
`--expected-operator-id`, approval-secret inputs, and optional
`--stage-number` (default `1`). Continuation retry boundaries also read
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`. Only retry attempt `1` is supported
in this slice.

Retry-boundary preparation consumes already-recorded evidence only. It rereads
and rechecks the completed retry-approval packet, planned retry-plan packet,
source outcome plan, source closeout, source execution, runner-start,
runner-plan, and dry-run packets. Continuation retry boundaries additionally
recheck the continuation and stage-input binding packet checksums and their
internal start/plan/dry-run or continuation anchors. The command reconstructs
the exact command context from the approved runner plan stage. For
continuation `start-governed-execution` retries, it reconstructs the task file,
task checksum, approval token, cwd, and any ownership-target, ownership-role,
and ownership-claimer arguments from the saved source execution and supplied
stage-input binding without revalidating active ownership after the source
stage. It also re-reads the saved `operator-approval.v1` referenced by the
retry approval and verifies its checksum, purpose, target, signature, and
operator identity through the approval-secret-backed operator approval
verifier. For continuation `start-governed-execution` retries, it also
re-reads the source stage-execution approval anchored by the saved source
execution and re-verifies the embedded `start_governed_execution`
executor-task approval before deriving the retry argv approval token. The retry
approval target is recomputed from the current source evidence chain, and the
stage-input binding's embedded prior-stage-output and executor-task anchors are
re-read before a boundary can be emitted.

A valid packet emits `controlled-loop-runner-stage-retry-boundary.v1` with
`packet: controlled_loop_runner_stage_retry_boundary`, `read_only: true`,
`side_effects: []`, `boundary_status: completed`,
`runner_stage_retry_authority: retry_boundary_prepared_not_started`, the
selected stage, `stage_retry_boundary`, and `stage_retry_boundary_checksum`.
The boundary includes exact argv, normalized arguments, fixed cwd policy,
retry output policy, timeout policy, execution authority, and allowed
side-effect policy derived from the approved runner-plan stage. The retry
output file must be new, inside the runtime root, and must not equal any input
evidence file or the source stage output file. The command sets
`recommended_next_action` to
`review_controlled_runner_stage_retry_boundary` and `next_controlled_action` to
`execute_approved_runner_stage_retry_once`; it does not perform that action.
It must not start a runner, start a process, execute a retry, emit
stage-execution readiness, select another stage, continue the runner or loop,
append audit evidence, invoke an executor, execute Git commands, call GitHub
APIs, create branches, commit, push, create PRs, merge, release, publish
packages, assign roles, or schedule agents.

Stable blockers include
`controlled_runner_stage_retry_boundary_approval_evidence_missing`,
`controlled_runner_stage_retry_boundary_retry_plan_evidence_missing`,
`controlled_runner_stage_retry_boundary_outcome_evidence_missing`,
`controlled_runner_stage_retry_boundary_closeout_evidence_missing`,
`controlled_runner_stage_retry_boundary_execution_evidence_missing`,
`controlled_runner_stage_retry_boundary_upstream_invalid`,
`controlled_runner_stage_retry_boundary_root_missing`,
`controlled_runner_stage_retry_boundary_continuation_evidence_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_boundary_retry_attempt_unsupported`,
`controlled_runner_stage_retry_boundary_cwd_invalid`,
`controlled_runner_stage_retry_boundary_output_file_invalid`,
`controlled_runner_stage_retry_boundary_output_file_already_exists`,
`controlled_runner_stage_retry_boundary_output_file_outside_runtime_root`,
`controlled_runner_stage_retry_boundary_timeout_invalid`,
`controlled_runner_stage_retry_boundary_output_file_overwrites_source`,
`controlled_runner_stage_retry_boundary_output_file_overwrites_input_evidence`,
`controlled_runner_stage_retry_boundary_approval_packet_mismatch`,
`controlled_runner_stage_retry_boundary_approval_not_completed`,
`controlled_runner_stage_retry_boundary_approval_limitations_invalid`,
`controlled_runner_stage_retry_boundary_stage_number_mismatch`,
`controlled_runner_stage_retry_boundary_approval_target_missing`,
`controlled_runner_stage_retry_boundary_approval_target_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_approval_target_purpose_mismatch`,
`controlled_runner_stage_retry_boundary_approval_target_mismatch`,
`controlled_runner_stage_retry_boundary_approval_retry_target_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_retry_plan_packet_mismatch`,
`controlled_runner_stage_retry_boundary_retry_plan_not_planned`,
`controlled_runner_stage_retry_boundary_retry_plan_limitations_invalid`,
`controlled_runner_stage_retry_boundary_retry_plan_stage_number_mismatch`,
`controlled_runner_stage_retry_boundary_approval_stage_retry_plan_file_mismatch`,
`controlled_runner_stage_retry_boundary_approval_stage_retry_plan_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_approval_stage_outcome_plan_file_mismatch`,
`controlled_runner_stage_retry_boundary_approval_stage_outcome_plan_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_approval_stage_execution_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_retry_plan_stage_execution_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_continuation_start_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_continuation_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_continuation_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_runner_plan_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_stage_missing_from_runner_plan`,
`controlled_runner_stage_retry_boundary_unknown_stage_command`,
`controlled_runner_stage_retry_boundary_start_governed_execution_requires_continuation`,
`controlled_runner_stage_retry_boundary_side_effect_policy_missing`,
`controlled_runner_stage_retry_boundary_execution_authority_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_required`,
`controlled_runner_stage_retry_boundary_operator_approval_file_missing`,
`controlled_runner_stage_retry_boundary_operator_approval_file_mismatch`,
`controlled_runner_stage_retry_boundary_operator_approval_file_unreadable`,
`controlled_runner_stage_retry_boundary_operator_approval_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_approval_identity_mismatch`,
`controlled_runner_stage_retry_boundary_source_ownership_arguments_incomplete`,
`controlled_runner_stage_retry_boundary_source_ownership_not_supported`,
`controlled_runner_stage_retry_boundary_source_ownership_side_effect_policy_missing`,
`controlled_runner_stage_retry_boundary_stage_execution_approval_file_missing`,
`controlled_runner_stage_retry_boundary_stage_execution_approval_file_mismatch`,
`controlled_runner_stage_retry_boundary_stage_execution_approval_file_unreadable`,
`controlled_runner_stage_retry_boundary_stage_execution_approval_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_executor_task_file_missing`,
`controlled_runner_stage_retry_boundary_executor_task_file_unreadable`,
`controlled_runner_stage_retry_boundary_executor_task_file_invalid`,
`controlled_runner_stage_retry_boundary_executor_task_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_executor_task_repo_path_missing`,
`controlled_runner_stage_retry_boundary_start_governed_execution_binding_missing`,
`controlled_runner_stage_retry_boundary_start_governed_execution_task_file_mismatch`,
`controlled_runner_stage_retry_boundary_start_governed_execution_approval_token_mismatch`,
`controlled_runner_stage_retry_boundary_executor_task_approval_missing`,
`controlled_runner_stage_retry_boundary_executor_task_approval_file_mismatch`,
`controlled_runner_stage_retry_boundary_executor_task_approval_file_unreadable`,
`controlled_runner_stage_retry_boundary_executor_task_approval_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_executor_task_approval_identity_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_prior_stage_output_file_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_prior_stage_output_file_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_prior_stage_output_file_unreadable`,
`controlled_runner_stage_retry_boundary_stage_input_binding_prior_stage_output_checksum_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_executor_task_file_missing`,
`controlled_runner_stage_retry_boundary_stage_input_binding_executor_task_file_mismatch`,
`controlled_runner_stage_retry_boundary_stage_input_binding_executor_task_file_unreadable`,
`controlled_runner_stage_retry_boundary_stage_input_binding_executor_task_checksum_mismatch`,
and `controlled_runner_stage_retry_boundary_stage_cwd_mismatch`.

`controlled-loop-runner-stage-retry-execute` is the controlled retry
execution packet for one approved controlled runner stage retry attempt. It
reads `--controlled-loop-runner-stage-retry-boundary-file`,
`--expected-stage-retry-boundary-checksum`,
`--controlled-loop-runner-stage-retry-approval-file`,
`--controlled-loop-runner-stage-retry-plan-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
approval-secret inputs, and optional `--stage-number` (default `1`).
Continuation retry execution also reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`.

Retry execution consumes already-recorded retry-boundary evidence only after an
operator has reviewed the boundary checksum. Before process start, it rechecks
the completed retry boundary, retry approval, retry plan, source outcome plan,
source closeout, source execution, runner-start, runner-plan, and dry-run
packets. Continuation retry execution additionally rechecks the continuation
and stage-input binding packet checksums and their internal anchors. The
command recomputes the retry approval target, re-verifies the saved
operator-approval signature and operator identity, requires the reviewed
`--expected-stage-retry-boundary-checksum`, reconstructs the exact approved
argv/cwd from the saved runner plan and boundary packet, and rejects drift
before process start. The approved retry output file must not already exist at
pre-start validation time, which prevents replaying the same boundary as a
second retry. The retry runtime root must not sit inside the reviewed stage cwd
unless `--allow-repo-local-root` was explicitly provided. The command also
replays the local audit log before process start and blocks when a prior
`controlled_runner_stage_retry_execution` audit record already records the same
reviewed `stage_retry_boundary_checksum`, stage number, selection source, and
retry attempt. After all pre-start checks pass, it atomically creates a durable
reservation under `stage-retry-execution-reservations/` for that reviewed
boundary before `subprocess.run`; an existing reservation blocks with
`controlled_runner_stage_retry_execution_reservation_already_exists`. Together,
the audit log and reservation form the durable
single-retry marker even if the retry output file or mutable wrapper packet is
later removed or rewritten.

When all pre-start checks pass, retry execution starts exactly one subprocess
with `shell: false`, the reviewed argv, and the reviewed cwd. It writes stdout
to the approved retry output path, captures stdout, stderr, return code,
timeout status, start/completion timestamps, argv, cwd, and shell mode in
`command_result`, records `command_result_checksum`, writes retry stdout with
exclusive file creation, and appends exactly one replay-valid retry-execution
audit record after the process has started. If a pre-start validation fails, the command emits a blocked
`controlled-loop-runner-stage-retry-execution.v1` packet with
`process_started: false`, `retry_execution_started: false`,
`audit_evidence_appended: false`, and no audit append. If process start fails,
the same no-audit blocked packet shape is used because no approved retry
subprocess started, and any retry reservation is released.

A valid post-start packet emits
`controlled-loop-runner-stage-retry-execution.v1` with
`packet: controlled_loop_runner_stage_retry_execution`, `read_only: false`,
`process_started: true`, `stage_retry_started: true`,
`retry_execution_started: true`, `runner_stage_retry_authority:
stage_retry_executed_once`, selected-stage terminal evidence, the verified
`controlled_loop_runner_stage_retry_boundary`, terminal `command_result`, and a
post-start audit record. The command's allowed side effects are limited to
`stage_retry_process_started`, `stage_retry_output_written` when stdout is
written, and `controlled_runner_stage_retry_execution_audit_appended` when the
post-start audit append succeeds. It must not start a second retry, select a
next stage, continue the runner or loop, invoke any additional executor,
execute Git commands, call GitHub APIs, create branches, commit, push, create
PRs, merge, release, publish packages, assign roles, or schedule agents.

Stable blockers include
`controlled_runner_stage_retry_execution_boundary_evidence_missing`,
`controlled_runner_stage_retry_execution_approval_evidence_missing`,
`controlled_runner_stage_retry_execution_retry_plan_evidence_missing`,
`controlled_runner_stage_retry_execution_outcome_evidence_missing`,
`controlled_runner_stage_retry_execution_closeout_evidence_missing`,
`controlled_runner_stage_retry_execution_execution_evidence_missing`,
`controlled_runner_stage_retry_execution_upstream_invalid`,
`controlled_runner_stage_retry_execution_root_missing`,
`controlled_runner_stage_retry_execution_continuation_evidence_missing`,
`controlled_runner_stage_retry_execution_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_execution_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_execution_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_execution_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_execution_boundary_packet_mismatch`,
`controlled_runner_stage_retry_execution_boundary_not_completed`,
`controlled_runner_stage_retry_execution_boundary_limitations_invalid`,
`controlled_runner_stage_retry_execution_stage_number_mismatch`,
`controlled_runner_stage_retry_execution_selection_source_mismatch`,
`controlled_runner_stage_retry_execution_retry_attempt_unsupported`,
`controlled_runner_stage_retry_execution_boundary_missing`,
`controlled_runner_stage_retry_execution_boundary_checksum_mismatch`,
`controlled_runner_stage_retry_execution_expected_boundary_checksum_mismatch`,
`controlled_runner_stage_retry_execution_boundary_selected_stage_mismatch`,
`controlled_runner_stage_retry_execution_cwd_invalid`,
`controlled_runner_stage_retry_execution_runtime_root_unsafe`,
`controlled_runner_stage_retry_execution_output_file_invalid`,
`controlled_runner_stage_retry_execution_output_file_already_exists`,
`controlled_runner_stage_retry_execution_already_recorded`,
`controlled_runner_stage_retry_execution_reservation_already_exists`,
`controlled_runner_stage_retry_execution_audit_history_invalid`,
`controlled_runner_stage_retry_execution_audit_history_unreadable`,
`controlled_runner_stage_retry_execution_output_file_mismatch`,
`controlled_runner_stage_retry_execution_output_file_overwrites_input_evidence`,
`controlled_runner_stage_retry_execution_output_file_overwrites_source`,
`controlled_runner_stage_retry_execution_timeout_invalid`,
`controlled_runner_stage_retry_execution_unapproved_command`,
`controlled_runner_stage_retry_execution_argv_invalid`,
`controlled_runner_stage_retry_execution_retry_plan_packet_mismatch`,
`controlled_runner_stage_retry_execution_retry_plan_not_planned`,
`controlled_runner_stage_retry_execution_retry_plan_limitations_invalid`,
`controlled_runner_stage_retry_execution_retry_plan_authority_flags_invalid`,
`controlled_runner_stage_retry_execution_approval_packet_mismatch`,
`controlled_runner_stage_retry_execution_approval_not_completed`,
`controlled_runner_stage_retry_execution_approval_limitations_invalid`,
`controlled_runner_stage_retry_execution_approval_target_checksum_mismatch`,
`controlled_runner_stage_retry_execution_reservation_checksum_missing`,
`controlled_runner_stage_retry_execution_reservation_retry_attempt_invalid`,
`controlled_runner_stage_retry_execution_reservation_create_failed`,
`controlled-loop-runner-stage-retry-execution-reservation.v1`,
`controlled_runner_stage_retry_execution_process_start_failed`,
`controlled_runner_stage_retry_execution_output_write_failed`,
`controlled_runner_stage_retry_execution_audit_append_failed`, mapped
retry-boundary/operator-approval/input-binding anchor blockers under the
`controlled_runner_stage_retry_execution_*` prefix, mapped stage stdout
side-effect blockers under the same prefix, and shared `operator_approval_*`
blockers from the operator-approval verifier.

`controlled-loop-runner-stage-retry-closeout` is the read-only closeout packet
for saved retry-execution evidence. It reads
`--controlled-loop-runner-stage-retry-execution-file`,
`--controlled-loop-runner-stage-retry-boundary-file`,
`--controlled-loop-runner-stage-retry-approval-file`,
`--controlled-loop-runner-stage-retry-plan-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
approval-secret inputs, optional `--stage-retry-output-file`, and optional
`--stage-number` (default `1`). Continuation retry closeout also reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`.

Retry closeout consumes already-recorded retry execution only. It rechecks the
saved retry execution, reviewed retry boundary, retry approval, retry plan,
source outcome plan, source closeout, source execution, runner-start,
runner-plan, dry-run, and, for continuation retries, continuation/input-binding
anchors. It validates the retry boundary status, retry attempt `1`, terminal
post-start retry execution evidence, command-result checksum, exact argv/cwd
identity against the reviewed boundary, terminal status derived from
returncode/timeout/stdout side-effect policy, selected-stage proof, and retry
output evidence. It does not append audit evidence; any retry-execution audit
evidence must already be present in the consumed Task 73 packet.

A valid packet emits `controlled-loop-runner-stage-retry-closeout.v1` with
`packet: controlled_loop_runner_stage_retry_closeout`, `read_only: true`,
`side_effects: []`, `runner_stage_retry_authority:
stage_retry_closeout_only`, `stage_retry_closeout_status` of `completed`,
`failed`, or `blocked`, the observed `command_result`, retry output evidence,
and file/checksum anchors for the consumed retry/source/runner chain. It sets
`recommended_next_action` and `next_controlled_action` to
`plan_controlled_runner_stage_retry_outcome`. The packet must not start a
process, execute a runner stage, start a second retry, select the next stage,
continue the runner or loop, invoke an executor, execute Git commands, call
GitHub APIs, create branches, commit, push, create PRs, merge, release,
publish packages, assign roles, or schedule agents.

Stable blockers include
`controlled_runner_stage_retry_closeout_execution_evidence_missing`,
`controlled_runner_stage_retry_closeout_boundary_evidence_missing`,
`controlled_runner_stage_retry_closeout_approval_evidence_missing`,
`controlled_runner_stage_retry_closeout_retry_plan_evidence_missing`,
`controlled_runner_stage_retry_closeout_outcome_evidence_missing`,
`controlled_runner_stage_retry_closeout_source_closeout_evidence_missing`,
`controlled_runner_stage_retry_closeout_source_execution_evidence_missing`,
`controlled_runner_stage_retry_closeout_upstream_invalid`,
`controlled_runner_stage_retry_closeout_root_missing`,
`controlled_runner_stage_retry_closeout_continuation_evidence_missing`,
`controlled_runner_stage_retry_closeout_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_closeout_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_closeout_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_closeout_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_closeout_boundary_packet_mismatch`,
`controlled_runner_stage_retry_closeout_boundary_not_completed`,
`controlled_runner_stage_retry_closeout_boundary_limitations_invalid`,
`controlled_runner_stage_retry_closeout_boundary_checksum_mismatch`,
`controlled_runner_stage_retry_closeout_output_file_invalid`,
`controlled_runner_stage_retry_closeout_output_file_mismatch`,
`controlled_runner_stage_retry_closeout_execution_packet_mismatch`,
`controlled_runner_stage_retry_closeout_execution_status_invalid`,
`controlled_runner_stage_retry_closeout_execution_not_terminal`,
`controlled_runner_stage_retry_closeout_execution_forbidden_flags`,
`controlled_runner_stage_retry_closeout_command_result_missing`,
`controlled_runner_stage_retry_closeout_command_result_checksum_mismatch`,
`controlled_runner_stage_retry_closeout_command_result_boundary_mismatch`,
`controlled_runner_stage_retry_closeout_command_result_invalid`,
`controlled_runner_stage_retry_closeout_command_result_status_mismatch`,
`controlled_runner_stage_retry_closeout_stage_retry_output_file_mismatch`,
and mapped retry/source/runner anchor or retry output blockers under the
`controlled_runner_stage_retry_closeout_*` prefix.

`controlled-loop-runner-stage-retry-outcome-plan` is the read-only outcome
planning packet for reviewed retry closeout evidence. It reads
`--controlled-loop-runner-stage-retry-closeout-file`,
`--expected-stage-retry-closeout-checksum`,
`--controlled-loop-runner-stage-retry-execution-file`,
`--controlled-loop-runner-stage-retry-boundary-file`,
`--controlled-loop-runner-stage-retry-approval-file`,
`--controlled-loop-runner-stage-retry-plan-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, `--expected-operator-id`,
approval-secret inputs, and optional `--stage-number` (default `1`).
Continuation retry outcome planning also reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`, and
`--expected-stage-input-binding-checksum`.

Retry outcome planning requires the reviewed retry closeout checksum, rechecks
that retry closeout is valid read-only `stage_retry_closeout_only` evidence,
rechecks the same retry/source/runner anchors, and emits only the next
operator target. Completed non-final retry closeout maps to
`stage_retry_outcome_decision: select_next_stage_after_retry` with target
purpose `controlled_loop_runner_next_stage_selection`; completed final retry
closeout maps to `complete_runner_after_retry` with target purpose
`controlled_loop_runner_completion`; failed retry closeout maps to
`inspect_stage_retry_failure`; blocked retry closeout maps to
`inspect_stage_retry_blocked`. Failed and blocked retry outcomes set
`second_retry_planning_target` and `second_retry_planning_target_checksum` to
`null`.

A valid packet emits `controlled-loop-runner-stage-retry-outcome-plan.v1` with
`packet: controlled_loop_runner_stage_retry_outcome_plan`, `read_only: true`,
`side_effects: []`, `runner_stage_retry_authority:
stage_retry_outcome_planned`, `stage_retry_outcome_decision`,
`retry_outcome_target`, `retry_outcome_target_checksum`, and no second retry
planning target. The packet does not select the next stage itself, start a
second retry, execute a runner stage, continue the runner or loop, append audit
evidence, start a process, invoke an executor, execute Git commands, call
GitHub APIs, create branches, commit, push, create PRs, merge, release,
publish packages, assign roles, or schedule agents.

Stable blockers include
`controlled_runner_stage_retry_outcome_plan_closeout_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_execution_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_boundary_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_approval_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_retry_plan_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_source_outcome_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_source_closeout_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_source_execution_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_closeout_checksum_mismatch`,
`controlled_runner_stage_retry_outcome_plan_upstream_invalid`,
`controlled_runner_stage_retry_outcome_plan_root_missing`,
`controlled_runner_stage_retry_outcome_plan_continuation_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_stage_input_binding_evidence_missing`,
`controlled_runner_stage_retry_outcome_plan_stage_input_binding_expected_checksum_missing`,
`controlled_runner_stage_retry_outcome_plan_stage_input_binding_checksum_mismatch`,
`controlled_runner_stage_retry_outcome_plan_unexpected_continuation_evidence`,
`controlled_runner_stage_retry_outcome_plan_closeout_packet_mismatch`,
`controlled_runner_stage_retry_outcome_plan_closeout_status_invalid`,
`controlled_runner_stage_retry_outcome_plan_closeout_not_closed_out`,
`controlled_runner_stage_retry_outcome_plan_closeout_forbidden_flags`,
`controlled_runner_stage_retry_outcome_plan_closeout_selected_stage_mismatch`,
`controlled_runner_stage_retry_outcome_plan_closeout_execution_status_mismatch`,
`controlled_runner_stage_retry_outcome_plan_closeout_status_execution_mismatch`,
`controlled_runner_stage_retry_outcome_plan_closeout_command_result_mismatch`,
`controlled_runner_stage_retry_outcome_plan_closeout_command_result_checksum_mismatch`,
and mapped retry/source/runner anchor blockers under the
`controlled_runner_stage_retry_outcome_plan_*` prefix.

`controlled-loop-runner-executor-invocation-readiness` is the read-only
handoff packet from a successful continuation `start-governed-execution` stage,
or from a successful attempt-1 retry of that stage, to the existing executor
invocation readiness path. It reads
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-execution-approval-file`,
`--controlled-loop-runner-next-stage-continuation-file`,
`--controlled-loop-runner-stage-input-binding-file`,
`--expected-stage-input-binding-checksum`, `--executor-task-file`,
`--expected-operator-id`, approval-secret inputs, and optional
`--stage-number` (default `2`). Retry-sourced readiness also reads
`--controlled-loop-runner-stage-retry-execution-file`,
`--controlled-loop-runner-stage-retry-closeout-file`, and
`--controlled-loop-runner-stage-retry-outcome-plan-file`; all three retry
inputs are required together.

Executor invocation readiness rechecks the saved `execution-start.v1` stdout
packet from the stage execution or retry execution, verifies the executor task
checksum against the stage-input binding and active epoch record, validates the
continuation and input-binding chain, rechecks source closeout/outcome anchors,
and verifies the already bound executor-task approval evidence without
requiring active ownership to still be open. Retry-sourced readiness requires
completed retry execution, completed read-only retry closeout, and completed
read-only retry outcome planning with `stage_retry_outcome_decision:
complete_runner_after_retry` before it can emit the handoff target. Retry
outcomes that select another stage, request a second retry, or continue the
runner are rejected by this command.

A valid packet emits `controlled-loop-runner-executor-invocation-readiness.v1`
with `packet: controlled_loop_runner_executor_invocation_readiness`,
`read_only: true`, `execution_source`, `source_execution_start`,
`source_execution_checksum`, `active_epoch`, and
`executor_invocation_readiness_target` with purpose `executor_invocation_readiness`. The packet emits only this target for the
existing controlled real executor invocation approval/readiness path. It does
not start a process, execute or retry a runner stage, invoke an executor,
select another stage, continue the runner or loop, append audit evidence,
execute Git commands, call GitHub APIs, create branches, commit, push, create
PRs, merge, release, publish packages, assign roles, or schedule agents.

Stable blockers include
`controlled_runner_executor_invocation_readiness_retry_evidence_incomplete`,
`controlled_runner_executor_invocation_readiness_stage_number_unsupported`,
`controlled_runner_executor_invocation_readiness_execution_not_ready`,
`controlled_runner_executor_invocation_readiness_closeout_not_ready`,
`controlled_runner_executor_invocation_readiness_outcome_not_ready`,
`controlled_runner_executor_invocation_readiness_retry_execution_not_completed`,
`controlled_runner_executor_invocation_readiness_retry_execution_authority_mismatch`,
`controlled_runner_executor_invocation_readiness_retry_execution_forbidden_flags`,
`controlled_runner_executor_invocation_readiness_retry_closeout_not_completed`,
`controlled_runner_executor_invocation_readiness_retry_closeout_authority_mismatch`,
`controlled_runner_executor_invocation_readiness_retry_closeout_forbidden_flags`,
`controlled_runner_executor_invocation_readiness_retry_outcome_not_completed`,
`controlled_runner_executor_invocation_readiness_retry_outcome_authority_unsupported`,
`controlled_runner_executor_invocation_readiness_retry_outcome_authority_mismatch`,
`controlled_runner_executor_invocation_readiness_retry_outcome_target_checksum_mismatch`,
`controlled_runner_executor_invocation_readiness_retry_outcome_forbidden_flags`,
`controlled_runner_executor_invocation_readiness_execution_start_invalid`,
`controlled_runner_executor_invocation_readiness_execution_start_task_checksum_mismatch`,
`controlled_runner_executor_invocation_readiness_execution_start_task_id_mismatch`,
`controlled_runner_executor_invocation_readiness_executor_task_file_mismatch`,
`controlled_runner_executor_invocation_readiness_executor_task_checksum_mismatch`,
`controlled_runner_executor_invocation_readiness_active_epoch_conflict`,
`controlled_runner_executor_invocation_readiness_active_epoch_status_invalid`,
and mapped continuation, stage-input binding, approval, source-chain, and
retry-chain anchor blockers under the
`controlled_runner_executor_invocation_readiness_*` prefix.

`controlled-loop-runner-completion` is the read-only terminal evidence packet
for a final controlled runner outcome. It reads
`--controlled-loop-runner-stage-outcome-plan-file`,
`--expected-stage-outcome-plan-checksum`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, and optional
`--completed-stage-number` (default `1`). It consumes already-recorded
evidence only. The command must not continue the runner or loop, select another
stage, emit stage-execution readiness, execute a retry, append audit evidence,
start a process, invoke an executor, execute Git commands, call GitHub APIs,
create branches, commit, push, create PRs, merge, release, publish packages,
assign roles, or schedule agents.

Completion rereads and rechecks the reviewed stage outcome plan, completed
closeout, completed execution packet, runner-start evidence, runner-plan
evidence, and dry-run evidence. It requires the outcome-plan checksum to match
the reviewed `--expected-stage-outcome-plan-checksum`, the outcome plan to be
valid and completed, and the outcome plan to carry
`stage_outcome_decision: complete_runner` plus `next_controlled_action:
complete_controlled_runner`. The nested outcome target must carry `purpose:
controlled_loop_runner_completion`, the completed-stage number, no next-stage
number, and checksum anchors for the saved closeout, execution, runner-start,
runner-plan, and dry-run packets. The closeout and execution evidence must be
completed for the requested completed stage, the execution packet's started
process, side-effect, audit, command-result, and selected-stage proof must be
self-consistent, continuation-sourced completion must carry matching
continuation/input-binding anchors, and stale anchors block the packet instead
of treating completion as accepted.

A valid packet emits `controlled-loop-runner-completion.v1` with
`packet: controlled_loop_runner_completion`, `read_only: true`,
`side_effects: []`, `runner_completion_status: completed`, references to every
consumed packet and checksum, `completion_target`, and
`completion_target_checksum`. It sets `recommended_next_action` and
`next_controlled_action` to `review_controlled_runner_completion`; the packet
is terminal evidence and grants no retry, continuation, executor, Git/GitHub,
merge, release, publication, role, or scheduling authority.

Stable blockers include `controlled_runner_completion_outcome_evidence_missing`,
`controlled_runner_completion_closeout_evidence_missing`,
`controlled_runner_completion_execution_evidence_missing`,
`controlled_runner_completion_upstream_invalid`,
`controlled_runner_completion_root_missing`,
`controlled_runner_completion_outcome_checksum_mismatch`,
`controlled_runner_completion_outcome_packet_mismatch`,
`controlled_runner_completion_outcome_not_completed`,
`controlled_runner_completion_outcome_stage_number_mismatch`,
`controlled_runner_completion_outcome_selection_source_mismatch`,
`controlled_runner_completion_outcome_not_complete_runner`,
`controlled_runner_completion_outcome_closeout_file_mismatch`,
`controlled_runner_completion_outcome_execution_file_mismatch`,
`controlled_runner_completion_outcome_start_file_mismatch`,
`controlled_runner_completion_outcome_plan_file_mismatch`,
`controlled_runner_completion_outcome_dry_run_file_mismatch`,
`controlled_runner_completion_outcome_target_missing`,
`controlled_runner_completion_outcome_target_checksum_mismatch`,
`controlled_runner_completion_outcome_target_purpose_invalid`,
`controlled_runner_completion_outcome_target_closeout_status_mismatch`,
`controlled_runner_completion_outcome_closed_out_stage_mismatch`,
`controlled_runner_completion_outcome_completed_stage_mismatch`,
`controlled_runner_completion_outcome_next_stage_present`,
`controlled_runner_completion_outcome_total_stage_count_mismatch`,
`controlled_runner_completion_outcome_not_final_stage`,
`controlled_runner_completion_closeout_checksum_mismatch`,
`controlled_runner_completion_execution_checksum_mismatch`,
`controlled_runner_completion_start_checksum_mismatch`,
`controlled_runner_completion_runner_plan_checksum_mismatch`,
`controlled_runner_completion_dry_run_checksum_mismatch`,
`controlled_runner_completion_closeout_packet_mismatch`,
`controlled_runner_completion_closeout_not_completed`,
`controlled_runner_completion_execution_packet_mismatch`,
`controlled_runner_completion_execution_not_completed`,
`controlled_runner_completion_execution_not_started`,
`controlled_runner_completion_execution_proof_missing`,
`controlled_runner_completion_command_result_missing`,
`controlled_runner_completion_command_result_checksum_mismatch`,
`controlled_runner_completion_command_result_status_mismatch`,
`controlled_runner_completion_execution_forbidden_flags`,
`controlled_runner_completion_execution_stage_number_mismatch`,
`controlled_runner_completion_continuation_anchor_mismatch`,
`controlled_runner_completion_stage_missing_from_runner_plan`,
`controlled_runner_completion_selected_stage_plan_mismatch`,
`controlled_runner_completion_execution_selected_stage_plan_mismatch`, and rewritten
upstream next-stage, start, plan, and dry-run blockers.

`controlled-loop-runner-next-stage-continuation` is the read-only selector for
stage `N+1` after a completed runner stage. It reads
`--controlled-loop-runner-stage-outcome-plan-file`,
`--expected-stage-outcome-plan-checksum`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, and optional
`--completed-stage-number` (default `1`). It consumes already-recorded
evidence only. The command must not emit a stage-execution readiness target,
authorize the stage-1 readiness command for continuation packets, execute the
selected stage, execute a retry, continue the loop, append audit evidence,
start a process, invoke an executor, execute Git commands, call GitHub APIs,
create branches, commit, push, create PRs, merge, release, publish packages,
assign roles, or schedule agents.

Continuation selection rereads and rechecks the reviewed stage outcome plan,
completed closeout, completed execution packet, runner-start evidence,
runner-plan evidence, and dry-run evidence. It requires the outcome-plan
checksum to match the reviewed `--expected-stage-outcome-plan-checksum`, the
outcome plan to be valid and completed, and the outcome plan to carry
`stage_outcome_decision: select_next_stage` plus `next_controlled_action:
select_controlled_runner_next_stage_continuation`. The nested outcome target
must carry `purpose: controlled_loop_runner_next_stage_selection` plus the
completed-stage number, exact next-stage number, and checksum anchors. The
closeout must be a completed closeout for the requested completed stage, and
the continuation target must select exactly `completed_stage_number + 1`.

A valid packet emits
`controlled-loop-runner-next-stage-continuation.v1` with
`packet: controlled_loop_runner_next_stage_continuation`, `read_only: true`,
`side_effects: []`, `runner_next_stage_continuation_status: selected`,
references to every consumed packet and checksum, `selected_stage`, and
`selected_stage_checksum`. It sets
`next_controlled_action:
generalize_controlled_runner_stage_execution_readiness_for_continuation`;
the current continuation path then binds the continuation to prior stage output
with `controlled-loop-runner-stage-input-binding` and feeds that binding into
the continuation-aware stage-execution readiness, approval, invocation-boundary,
execution, closeout, and outcome-planning stages.

Stable blockers include
`controlled_runner_next_stage_continuation_outcome_evidence_missing`,
`controlled_runner_next_stage_continuation_closeout_evidence_missing`,
`controlled_runner_next_stage_continuation_execution_evidence_missing`,
`controlled_runner_next_stage_continuation_upstream_invalid`,
`controlled_runner_next_stage_continuation_root_missing`,
`controlled_runner_next_stage_continuation_outcome_checksum_mismatch`,
`controlled_runner_next_stage_continuation_outcome_packet_mismatch`,
`controlled_runner_next_stage_continuation_outcome_not_completed`,
`controlled_runner_next_stage_continuation_outcome_not_select_next_stage`,
`controlled_runner_next_stage_continuation_outcome_target_missing`,
`controlled_runner_next_stage_continuation_outcome_target_checksum_mismatch`,
`controlled_runner_next_stage_continuation_outcome_target_purpose_invalid`,
`controlled_runner_next_stage_continuation_completed_stage_mismatch`,
`controlled_runner_next_stage_continuation_stage_sequence_gap`,
`controlled_runner_next_stage_continuation_outcome_closeout_file_mismatch`,
`controlled_runner_next_stage_continuation_outcome_execution_file_mismatch`,
`controlled_runner_next_stage_continuation_outcome_start_file_mismatch`,
`controlled_runner_next_stage_continuation_outcome_plan_file_mismatch`,
`controlled_runner_next_stage_continuation_outcome_dry_run_file_mismatch`,
`controlled_runner_next_stage_continuation_closeout_checksum_mismatch`,
`controlled_runner_next_stage_continuation_execution_checksum_mismatch`,
`controlled_runner_next_stage_continuation_start_checksum_mismatch`,
`controlled_runner_next_stage_continuation_runner_plan_checksum_mismatch`,
`controlled_runner_next_stage_continuation_dry_run_checksum_mismatch`,
`controlled_runner_next_stage_continuation_closeout_packet_mismatch`,
`controlled_runner_next_stage_continuation_closeout_not_completed`,
`controlled_runner_next_stage_continuation_execution_packet_mismatch`,
`controlled_runner_next_stage_continuation_execution_not_completed`,
`controlled_runner_next_stage_continuation_execution_stage_number_mismatch`,
`controlled_runner_next_stage_continuation_stage_missing_from_runner_plan`,
`controlled_runner_next_stage_continuation_selected_stage_plan_mismatch`, and
rewritten upstream next-stage, start, plan, and dry-run blockers.

`controlled-loop-runner-stage-input-binding` is the read-only bridge from a
selected continuation stage to the concrete inputs that stage needs before
readiness can be generalized. It reads
`--controlled-loop-runner-next-stage-continuation-file`,
`--expected-next-stage-continuation-checksum`,
`--controlled-loop-runner-stage-outcome-plan-file`,
`--controlled-loop-runner-stage-closeout-file`,
`--controlled-loop-runner-stage-execution-file`,
`--prior-stage-output-file`, `--executor-task-file`,
`--controlled-loop-runner-start-file`, `--controlled-loop-runner-plan-file`,
`--controlled-loop-runner-dry-run-file`, and optional
`--completed-stage-number` (default `1`). It consumes already-recorded
evidence only. The command must not emit a stage-execution readiness target,
generate or reveal an approval token, execute the selected stage, start a
process, start an epoch, invoke or retry an executor, continue the loop, append
audit evidence, execute Git commands, call GitHub APIs, create branches,
commit, push, create PRs, merge, release, publish packages, assign roles, or
schedule agents.

Input binding rereads and rechecks the reviewed
`controlled-loop-runner-next-stage-continuation.v1` packet plus the saved
stage outcome plan, closeout, execution, runner-start, runner-plan, and dry-run
evidence. It requires the continuation checksum to match the reviewed
`--expected-next-stage-continuation-checksum`, the continuation packet to be
valid, selected, read-only, and still recommending
`generalize_controlled_runner_stage_execution_readiness_for_continuation`, and
the selected continuation stage to match the approved runner plan. It also
requires the stage outcome plan to be completed, read-only, side-effect-free,
still carrying `runner_stage_execution_authority: stage_outcome_planned`,
still targeting `select_next_stage`, and carrying a next-stage outcome target
whose own checksum, evidence file anchors, evidence checksums, completed
stage, closed-out stage, next stage, and total stage count still match the
consumed runner evidence. For the current stage-2 `start-governed-execution`
path, it also requires the prior stage output to be a schema-compatible
`loop-run-plan.v1` packet whose
`recommended_next_action` is `request_operator_approval`, whose `read_only`
and `operator_confirmation_required` fields are true, whose
`executor_contract_required` field is false, whose embedded loop tick checksum
still matches, whose embedded `generic-executor-task.v1` packet validates,
whose executor task checksum matches the embedded task, and whose planned
steps exactly preserve the required operator-approval step followed by blocked
`start-governed-execution`.

A valid packet emits
`controlled-loop-runner-stage-input-binding.v1` with
`packet: controlled_loop_runner_stage_input_binding`, `read_only: true`,
`side_effects: []`, `stage_input_binding_status: bound`, references to every
consumed packet and checksum, `selected_stage`, `selected_stage_checksum`,
the prior stage output checksum, the exact executor task file checksum, and
`expected_executor_task_approval_target_checksum`. It sets
`next_controlled_action:
prepare_controlled_runner_continuation_stage_execution_readiness`, but does
not prepare readiness or approve `start-governed-execution`.

Stable blockers include
`controlled_runner_stage_input_binding_continuation_evidence_missing`,
`controlled_runner_stage_input_binding_outcome_evidence_missing`,
`controlled_runner_stage_input_binding_closeout_evidence_missing`,
`controlled_runner_stage_input_binding_execution_evidence_missing`,
`controlled_runner_stage_input_binding_prior_stage_output_missing`,
`controlled_runner_stage_input_binding_executor_task_file_missing`,
`controlled_runner_stage_input_binding_upstream_invalid`,
`controlled_runner_stage_input_binding_root_missing`,
`controlled_runner_stage_input_binding_continuation_checksum_mismatch`,
`controlled_runner_stage_input_binding_continuation_selected_stage_checksum_mismatch`,
`controlled_runner_stage_input_binding_continuation_packet_mismatch`,
`controlled_runner_stage_input_binding_continuation_not_selected`,
`controlled_runner_stage_input_binding_continuation_authority_flags_invalid`,
`controlled_runner_stage_input_binding_selected_stage_mismatch`,
`controlled_runner_stage_input_binding_outcome_packet_mismatch`,
`controlled_runner_stage_input_binding_outcome_not_completed`,
`controlled_runner_stage_input_binding_outcome_stage_number_mismatch`,
`controlled_runner_stage_input_binding_outcome_not_select_next_stage`,
`controlled_runner_stage_input_binding_outcome_closeout_file_mismatch`,
`controlled_runner_stage_input_binding_outcome_execution_file_mismatch`,
`controlled_runner_stage_input_binding_outcome_start_file_mismatch`,
`controlled_runner_stage_input_binding_outcome_plan_file_mismatch`,
`controlled_runner_stage_input_binding_outcome_dry_run_file_mismatch`,
`controlled_runner_stage_input_binding_outcome_target_missing`,
`controlled_runner_stage_input_binding_outcome_target_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_closeout_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_execution_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_start_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_plan_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_dry_run_checksum_mismatch`,
`controlled_runner_stage_input_binding_outcome_target_purpose_invalid`,
`controlled_runner_stage_input_binding_outcome_closed_out_stage_mismatch`,
`controlled_runner_stage_input_binding_outcome_completed_stage_mismatch`,
`controlled_runner_stage_input_binding_outcome_stage_sequence_gap`,
`controlled_runner_stage_input_binding_outcome_total_stage_count_mismatch`,
`controlled_runner_stage_input_binding_closeout_packet_mismatch`,
`controlled_runner_stage_input_binding_execution_packet_mismatch`,
`controlled_runner_stage_input_binding_prior_stage_output_file_mismatch`,
`controlled_runner_stage_input_binding_prior_stage_output_checksum_mismatch`,
`controlled_runner_stage_input_binding_prior_stage_output_packet_mismatch`,
`controlled_runner_stage_input_binding_executor_task_missing`,
`controlled_runner_stage_input_binding_executor_task_checksum_missing`,
`controlled_runner_stage_input_binding_executor_task_checksum_mismatch`,
`controlled_runner_stage_input_binding_executor_task_invalid`,
`controlled_runner_stage_input_binding_prior_stage_not_waiting_for_approval`,
`controlled_runner_stage_input_binding_prior_stage_approval_contract_invalid`,
`controlled_runner_stage_input_binding_prior_stage_authority_flags_invalid`,
`controlled_runner_stage_input_binding_prior_stage_loop_tick_mismatch`,
`controlled_runner_stage_input_binding_prior_stage_planned_steps_missing`,
`controlled_runner_stage_input_binding_prior_stage_planned_steps_mismatch`,
`controlled_runner_stage_input_binding_executor_task_file_mismatch`,
`controlled_runner_stage_input_binding_closeout_not_completed`,
`controlled_runner_stage_input_binding_closeout_forbidden_flags`,
`controlled_runner_stage_input_binding_execution_not_completed`,
`controlled_runner_stage_input_binding_execution_stage_number_mismatch`,
`controlled_runner_stage_input_binding_execution_stage_output_file_mismatch`,
`controlled_runner_stage_input_binding_unsupported_continuation_stage`, and
rewritten upstream next-stage, start, plan, dry-run, file-anchor, and checksum
anchor blockers.

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
The Python/protocol job keeps a finite 20-minute timeout for the full unit,
protocol, smoke, and adapter-contract suite; package install/example matrix
jobs keep finite 15-minute timeouts per OS.

The Codex Review workflow is an elected paid reviewer, not an automatic blocker on every push. `.github/workflows/codex-review.yml` uses pinned `openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c` through `pull_request_target` `labeled` and `synchronize` events targeting `main`, skips draft PRs, starts paid-review preflight only for elect/force label events from trusted operators, and stays limited to same-repository PRs with `safety-strategy: drop-sudo` and `sandbox: read-only`. `synchronize` is a cancel-only event for obsolete in-flight elected reviews; it must not start paid-review preflight by itself. Unrelated label events must not cancel an in-flight elected review. It posts the action `final-message` back to the PR only after the elected paid review returns feedback. Fork PRs run a skip notice only for elected labels because `pull_request_target` must not expose repository secrets to untrusted PR code. Same-repository review jobs check the live PR state before checkout and immediately before the paid Codex action, requiring the PR to remain open, unmerged, not draft, and on the same head SHA as the triggering event; obsolete, draft, merged, closed, or non-elected PRs skip instead of failing on a missing synthetic merge ref or spending review credits.

The paid Codex action is guarded by a free preflight implemented in `scripts/codex_review_preflight.py` and checked out from the trusted base SHA rather than the PR merge tree. The PR checkout uses `github.event.pull_request.head.sha` instead of `refs/pull/<number>/merge` so a label-triggered run remains stable if the PR merges while the workflow is starting. The preflight runs before any OpenAI API key is used, computes a dedupe key from the PR head SHA and changed files, reads prior PR comments, and skips when it finds a matching hidden `codex-review:v1` marker in the canonical workflow-owned `## Codex Review` comment from `github-actions[bot]` for an elected head. It also skips empty diffs, docs-only changes when not elected, trusted label opt-outs such as `codex-review-skip`, and non-elected code changes with `not_elected`; `codex-review-elect` elects a paid run when normal preflight safeguards pass, while `codex-review-force` overrides dedupe and docs-only skips. PR title/body text must not control review spending. Inability to inspect prior review comments is a failing preflight condition only when a paid review has been elected. Workflow concurrency must cancel obsolete in-flight review runs at the PR level for elect/force label events and synchronize events, while unrelated label events must not interrupt an elected review. The paid Codex job must have a finite timeout.

Guardrail changes to `.github/workflows/codex-review.yml` or `scripts/codex_review_preflight.py` have a bootstrap boundary: a PR is evaluated by the current base branch guardrail, and its updated guardrail only becomes active after merge. Those changes require manual operator review or branch-protected code-owner review before merge so an untrusted PR cannot relax spending controls and use them in the same review cycle.

The workflow requires the repository secret `OPENAI_API_KEY`. The review prompt is kept inline in `.github/workflows/codex-review.yml` so a PR cannot redirect the action through a modified prompt file. It must tell Codex not to modify files and to focus on actionable findings, protocol drift, race conditions, missing tests, and security-sensitive automation behavior. Native `@codex review` remains a useful manual fallback, but the repo-owned workflow is the repeatable elected PR review gate.
