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
```

Both emit `work-ownership-closeout.v1`, move the active record to `closed` or
`failed`, write closeout evidence onto the record, and append
`work_ownership_mutation` audit evidence. Missing records, mismatched repo,
branch, head, task, or claimer, already closed/failed records, malformed
records, dirty worktrees, stale repo heads, and unsafe registry paths block
before mutation.

Additional mutation blocker codes include `repo_branch_mismatch`,
`repo_head_mismatch`, `dirty_worktree`, `ownership_role_invalid`,
`ownership_claimer_invalid`, `ownership_claimer_mismatch`,
`ownership_head_mismatch`, `ownership_record_exists`,
`ownership_record_write_failed`, and `audit_append_failed`. Bounded mutation
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
`reviewThreads` JSON with `isResolved`, `isOutdated`, and comment `outdated`
status fields, then converts actionable unresolved, current review comments
into the same `review_finding` candidate shape. Candidate ingestion uses saved
feedback as local work-discovery input; readiness decisions apply stricter
completeness gates. This ingestion must stay
deterministic and local: it must not call GitHub, trust PR body text, include
resolved or outdated threads, assume missing status fields are current, include
non-actionable summaries such as walkthroughs or no-actionable-comments
reports, or bypass repo-relative path validation.

## Loop Tick

`loop-tick` is the Phase 1 read-only loop-controller command. It must check Cadence state, capture and persist a local repo snapshot, run deterministic candidate discovery with election enabled, and emit one JSON packet describing the next governed action. It must not start an executor, start or complete an epoch, create or update a branch, commit, push, create or update a pull request, spend review, merge, release, or publish.

The packet must include the brake and Cadence state, the persisted snapshot, the candidate-discovery packet, the elected candidates, `read_only: true`, `executor_started: false`, `epoch_started: false`, `pr_action_started: false`, and a `recommended_next_action`. Phase 1 recommended actions are `blocked` when Cadence state disallows work, `approval_required` when local repo confidence is low, `no_candidates` when election returns no candidate, `requires_executor_contract` when a candidate is available but Cadence has not emitted an executor task packet, `approve_executor_task` when an executor task packet has been emitted for operator approval, and `policy_denied` when a supplied local loop policy blocks the requested executor-task bounds. Low local repo confidence takes precedence over empty election and includes dirty worktrees, unborn or detached HEAD, known failures, and an operator-supplied red CI signal. This command is not a continuous runner; repeated ticks require an external operator or orchestrator.

When `--policy-file` is supplied, it must be local JSON with `schema_version: cadence-loop-policy.v1`. The initial policy shape supports `allowed_paths`, `denied_paths`, `allowed_commands`, `denied_commands`, `required_checks`, `max_executor_time_minutes`, `stop_conditions`, and an optional `branch_policy` object. Policy paths are repo-relative. Policy `allowed_paths` and max runtime provide defaults and caps for emitted executor task packets. Policy `allowed_commands` and `denied_commands` are copied into the emitted task packet `command_policy` so later result validation can reject commands outside the allowlist or matching the denylist. Policy `branch_policy` is copied into emitted executor task packets so later Git/PR planning checks the approved branch bounds instead of trusting a mutable policy file. The branch policy supports only `allowed_base_branches`, `denied_target_branches`, `required_branch_prefixes`, and `allow_current_branch_main`; unknown branch-policy fields are rejected. When no branch policy is supplied, existing no-policy behavior remains permissive for dry-run planning; when a `branch_policy` object is supplied, omitted list fields default to empty lists and omitted `allow_current_branch_main` preserves that permissive default. Built-in safety stops `brake_not_drive`, `operator_stop`, `context_pressure`, and `timeout` must always be retained. Policy `required_checks` and `stop_conditions` must always be retained, and requested CLI checks or stop conditions are additive after de-duplication. Requested executor allowed paths must stay inside policy `allowed_paths` and must not overlap `denied_paths`; requested runtime must not exceed `max_executor_time_minutes`; malformed requested allowed paths or malformed branch policy fail closed before a task packet is emitted. A policy denial must emit a `policy_denied` loop packet, require operator attention, and avoid emitting an executor task.

Each root-backed `loop-tick` must append a compact `cadence-audit.v1` record to `<root>/audit/events.jsonl` and include an `audit_record` reference in the returned packet. The audit record binds the decision to the tick id, recommended action, reason, repo, branch, head, snapshot id, optional executor task id, operator-confirmation flag, and a checksum of the emitted packet before the audit reference is added.
When the operator omits `--repo`, the loop-decision audit record uses the
resolved snapshot `cwd` as the local repo anchor so replay can still validate
the compact record.

`audit-replay` is the read-only local audit verification command. It reads
`<root>/audit/events.jsonl`, emits an `audit-replay.v1` packet, and exits
nonzero when the audit log contains malformed, corrupt, or unsupported records.
Missing or empty audit logs are valid zero-record packets for a fresh runtime
root; they are not evidence that older audit history was preserved.

Replay validates only the compact `cadence-audit.v1` record shape, supported
event names, event-specific required fields, physical JSONL line counts, and
`sha256:` checksum syntax. Supported events are `loop_tick_decision`,
`executor_fixture_invocation`, `execution_run_record`,
`executor_result_validation`, `executor_epoch_closeout`,
`execution_start_decision`, `git_pr_materialization_intent`,
`git_pr_materialization_result`, and `work_ownership_mutation`. It does not
recompute `payload_checksum`,
`run_record_checksum`,
`task_packet_checksum`, `result_evidence_checksum`,
`validation_packet_checksum`, `plan_checksum`, `snapshot_after_checksum`, or
`ownership_record_checksum`
from original packet bodies because those bodies are not stored in the compact
audit log.

Invalid packets include stable blocker codes such as
`audit_line_invalid_json`, `audit_record_not_object`,
`audit_schema_version_unsupported`, `audit_event_unsupported`,
`audit_required_field_missing`, and `audit_checksum_invalid`. The command uses
`recommended_next_action: "upgrade_cadence"` only when every blocker is an
unsupported schema or event; corruption, malformed records, unreadable files,
decode failures, or mixed unsupported/corrupt history recommend
`inspect_audit_log`.

`audit-replay` has no target repository `cwd`, so the dispatcher resolves the
runtime root and applies the runtime-root location safety guard without running
repo-cwd safety checks. The command must not append audit records, repair files,
run an executor, start or complete epochs, create branches, commit, push, open a
pull request, spend review, merge, or treat clean replay evidence as approval to
execute work.

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
result evidence, a fresh `--snapshot-after-file`, and optional
`--run-record-file`; validates the result with the same expected-path,
command-policy, disabled-permission, runtime, and active brake gates as
`validate-executor-result`; then binds the task packet to the requested active
epoch. Binding requires exactly the requested active epoch, a task id recorded
in that epoch, a task snapshot checksum matching the epoch `snapshot_before`,
matching repo/branch/head anchors, and a valid snapshot-after packet captured
after epoch start with a head matching the executor result and a `captured_at`
timestamp at or after the executor result `ended_at`. When a run record is
supplied, it must use `schema_version: execution-run.v1`,
`protocol_version: v1`, `packet: execution_run`, a non-empty run id, a
non-empty invocation id, `closeout_status: pending`, matching
task/result/validation checksums, matching task and result file paths, matching
task id, and matching repo name/path/branch/head anchors. The supplied
`--run-record-file` must also be the canonical local runtime path
`<root>/execution-runs/<run_id>.json`; malformed, unreadable, non-canonical, or
out-of-ledger run-record files block with stable run-record blockers instead of
falling through to top-level CLI errors. Stale task snapshots,
stale snapshot-after packets, head mismatches, active epoch conflicts,
already-terminal epochs, malformed packets, partial run records, run-record
checksum mismatches, run-record repo anchor mismatches, closeout replay, and
evidence that needs more validation block closeout without moving the active
epoch.

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
Already-terminal reruns report `closeout_status: already_closed` without
appending another closeout audit record. It must not start an executor, create a
branch, commit, push, call GitHub, open a pull request, merge, release, or
publish packages.

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

## Operator-Approved Git/PR Materialization

`git-pr-materialize` is the explicit write-side boundary for a reviewed
`git-pr-plan.v1` packet. It must consume the saved plan packet, require an exact
operator approval token produced as an HMAC over that packet checksum plus the
selected remote name, resolved remote push URL, and create-vs-update PR target,
using `CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`, and emit a
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

When all gates pass, the command may append a
`git_pr_materialization_intent` audit record, create the proposed branch at the
already-materialized current commit without switching the checkout, push the
branch to the selected remote with Git hook verification disabled for that push,
and create or update a pull request with `gh pr create` or `gh pr edit`. It must
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
or future evidence recommends `refresh_pr_evidence` before emitting work items.
Malformed, missing-status, or incomplete paginated review-thread evidence must
block with stable `review_thread_evidence_invalid` blockers. Candidate discovery
input is advisory only; malformed candidate packets block planning, and matched
candidates may be included in follow-up task summaries without authorizing
execution. The command must not call GitHub, resolve review threads, post
comments, update PR bodies, create branches, commit, push, open or edit pull
requests, merge, release, publish packages, spend paid review, or invoke review
agents. Its limitations should include
`does_not_invoke_review_agents_or_paid_review`.

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
