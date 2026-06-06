# Agentic Cadence

![Python 3.11+, MIT license, PR checks, and agentic-cadence package badges.](docs/assets/readme-badges.svg)

![Agentic Cadence banner showing a signed handoff, clean square validation, and fresh agent continuation.](docs/assets/agentic-cadence-banner.svg)

GitHub-native governance and orchestration for coding agents that must work
without losing the repository's coordination discipline.

Agentic Cadence helps one agent today by stopping cleanly, handing off context
to a fresh session, and continuing only when the repository and Cadence state
allow it. That single-agent workflow remains Phase 1.

The larger product direction is GitHub-native orchestration for autonomous
software teams. Cadence should eventually coordinate multiple bounded agents
through issues, branches, pull requests, reviews, CI, documentation, handoff
contracts, and merge decisions without duplicating work or corrupting
repository state.

The first implementation is used with Codex, and the protocol is intentionally
agent-neutral so future adapters can support Claude, Gemini, and other coding
agents without changing the core governance model.

## Current Status

Agentic Cadence is an early public protocol and tooling release. The released `0.1.3` baseline is ready for local clone-based use with `pip install .`, protocol validation, first-run examples, the adapter smoke contract, generic host-signal and shell host-binding examples, the composite generic adapter contract runner with reviewer-verifiable compact evidence, release dry-run verification, and public-release history auditing. The current development tree additionally includes unreleased read-only audit replay, command-policy enforcement, and active-stop result-validation controls, plus governed execution-start epoch gating for approved generic executor task packets, local execution-run evidence records, local executor epoch closeout, branch-policy-gated dry-run Git/PR planning for local generic executor task and result evidence, read-only `github-evidence-sync`, read-only `review-response-plan`, operator-approved `git-pr-materialize`, read-only `verify-resume`, read-only `resume-continuation`, read-only `work-ownership-status`, read-only `validate-work-ownership`, and a fixture-only controlled executor runner for tests and examples.

The public package identity is `agentic-cadence`. The legacy `codex-cadence` and `codex-transmission` command names remain compatibility aliases, while Claude and Gemini remain future adapter directions rather than shipped support or package metadata keywords.

PyPI publication is not part of this baseline. Treat package-index publication, signed version tags, and broader adapter support as follow-on release work.

See the current [technical roadmap](docs/roadmap.md) for known edges and target
state. See [agent-team orchestration](docs/agent-team-orchestration.md) for the
expanded product vision.

## Vision

Agentic Cadence is a governance and orchestration layer for autonomous software
teams. It can start with one agent, but its primitives should scale toward an
agent pool with clear roles: planning, architecture, building, review, QA,
documentation, release, and handoff.

The system should preserve the same coordination rules disciplined human teams
use on GitHub:

- no direct edits to `main`;
- issue, task, or decision-backed work;
- branch isolation for implementation;
- pull requests for meaningful changes;
- validation before merge;
- review separation when possible;
- explicit context handoff;
- living documentation that changes with the repo;
- small bounded slices instead of ambiguous work;
- orchestrator decisions to continue, stop, retry, split, review, or hand off.

## Future Agent Adapters

The protocol is meant to stay agent-neutral while adapters handle host-specific details. See `docs/adapters.md` for the adapter boundary, current Codex compatibility surface, and the intended path for future Claude and Gemini support.

The adapter smoke contract is executable from a clone:

```bash
python examples/adapter-smoke/run.py --cadence-python python
```

It proves the adapter path through CLI JSON packets without importing Cadence internals. Current packets may still contain Codex-compatible packet labels retained by the 0.1.x command surface; adapters should preserve those packets rather than rewriting them.

The generic host-signal smoke example exercises the adapter-local signal
fixtures before a real host binding exists. Its parity contract compares that
fixture behavior with the generic shell host-binding replay contract:

```bash
python examples/adapter-template/host_signal_contract.py
python examples/generic-host-signal/run.py --cadence-python python
python examples/generic-host-signal/run.py --parity-contract --cadence-python python
```

It verifies no-signal, `context_pressure`, `reviewer_loop`, `ci_loop`, and
`operator_stop` behavior through the copyable adapter template without claiming
Claude or Gemini adapter support.
The schema contract validates that the checked-in host-signal fixtures and
shell host-event payloads have the expected fields and normalized meanings.
The parity contract verifies that the shell host-event mapping stays aligned
with the adapter-template host-signal fixtures for normalized
adapter/CLI-observed behavior.

The generic shell host-binding example adds file-backed and stdin-backed shell
integration paths for one external host event, plus a replay contract that
compares those paths against the bundled fixtures:

```bash
python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-file /path/to/host-event.json
some-host-signal-command | python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-stdin
python examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python
```

The file or stdin payload must contain a `context_pressure`, `reviewer_loop`,
`ci_loop`, or `operator_stop` host-event object, or JSON `null` when no handoff
is needed. This still exercises the adapter template and public CLI boundary;
it does not ship a real Claude or Gemini adapter. The replay contract verifies
that the same payload produces the same normalized adapter/CLI-observed
behavior through bundled fixture, file-backed, and stdin-backed paths.

The generic external host-binding conformance harness compares a supplied
binding command against the same generic shell replay baseline before any named
host adapter is claimed:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python
```

By default it uses the generic shell host-binding example as the sample external
command. Future host bindings can pass `--binding-command-template` with
quoted path placeholders such as `"{host_event_file}"` and
`"{case_work_dir}"` to prove they match the generic fixture behavior without
claiming Claude or Gemini adapter support.

The generic adapter contract pre-claim suite composes the schema, smoke,
replay, parity, and external conformance contracts into one command:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python
```

On Windows, the runner uses a short per-checkout disposable directory under the
system temp root by default to avoid nested Git path-length failures. Pass
`--work-dir` if you need to choose a specific disposable work directory.

Use it before any future host-specific binding claim. For PR evidence, add the
compact evidence summary:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python --evidence-summary
```

Future bindings can pass the same quoted binding command template through the
runner, with or without `--evidence-summary`, and the runner still reports that
it does not ship Claude or Gemini adapter support.

PR checks upload the compact JSON as the `generic-adapter-contract-evidence`
artifact containing `adapter-contract-evidence.json`, so reviewers can inspect
the generic contract coverage without expanding nested packet payloads in logs.
That compact artifact includes
`schema_version: "generic-adapter-contract-evidence.v1"` so reviewer tooling can
rely on a named evidence shape. The checked-in project-specific schema fixture
also pins the accepted top-level result, compact evidence mode, required
contract labels, observed-label parity, and required pass booleans. It lives at
`examples/adapter-contract-runner/generic-adapter-contract-evidence.v1.schema.json`.
After downloading the artifact file, reviewers can validate that compact shape
without rerunning the suite:

```bash
python examples/adapter-contract-runner/run.py --validate-evidence-file adapter-contract-evidence.json
```

The adapter claim verifier codifies the evidence-only part of the named-host
claim checklist. With no host name it reports that the uploaded generic baseline
must remain generic:

```bash
python examples/adapter-claim-verifier/run.py --evidence-file adapter-contract-evidence.json
```

Before documenting a named non-Codex host binding, run it against compact
evidence emitted from the proposed binding command template:

```bash
python examples/adapter-claim-verifier/run.py --evidence-file adapter-contract-evidence.json --claim-host ExampleHost --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
```

It only checks the compact evidence boundary. A named adapter PR still needs the
mapping evidence, implementation files, and docs required by the checklist.

Before documenting a named non-Codex host binding, follow
`docs/adapter-claim-checklist.md` and include the required generic contract
evidence in the PR.

## Protocol At A Glance

![Four-step Agentic Cadence handoff flow from old context to signed handoff, clean square, and fresh agent.](docs/assets/handoff-flow.svg)

## Requirements

- Python 3.11 or newer
- Git

## Install From A Clone

```bash
python -m pip install .
agentic-cadence --help
python -m codex_cadence --help
```

Compatibility command names are still available:

```bash
codex-cadence --help
codex-transmission --help
```

## Five-Minute First Run

Use a disposable runtime root so the first run does not touch your global agent state:

```bash
agentic-cadence --root .agentic-cadence-demo status
agentic-cadence --root .agentic-cadence-demo create-handoff --id read-the-repo --title "Read the repo" --repo local/example --branch main --task-type discovery --message-file examples/first-run/handoff.md
agentic-cadence --root .agentic-cadence-demo next-handoff
agentic-cadence --root .agentic-cadence-demo claim-handoff read-the-repo --claimer demo
agentic-cadence --root .agentic-cadence-demo complete-handoff read-the-repo --summary "first run completed"
```

`.agentic-cadence-demo/` is disposable and ignored by git. The legacy `.codex-cadence-demo/` path is also ignored for compatibility.

## Run The Example Workflow

On macOS or Linux:

```bash
bash examples/first-run/run.sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File examples/first-run/run.ps1
```

The example creates `examples/first-run/work/`, initializes a tiny target Git repository, runs a handoff lifecycle, and runs candidate discovery against `examples/first-run/work/repo`.

## Candidate Discovery

Candidate discovery is read-only. It can inspect local repo state and reviewed business memory from `docs/cadence/business-memory.md`. The example runner creates `examples/first-run/work/repo` and runs discovery automatically. After running the example workflow, you can repeat that discovery command:

```bash
agentic-cadence --root examples/first-run/work/runtime discover-candidates --cwd examples/first-run/work/repo --intent hybrid --discovery-mode local --elect
```

Business-memory candidates are discovery-only. They can seed investigation, but they cannot directly execute changes, commit, push, merge, or bypass Cadence governance.

## Loop Tick

`loop-tick` runs one read-only Phase 1 loop-controller tick. It snapshots local repo state, discovers and elects candidate work, checks Cadence state, and emits a structured next-action packet.

```bash
agentic-cadence --root examples/first-run/work/runtime loop-tick --cwd examples/first-run/work/repo --repo local/demo --intent repo_health
```

The command does not start an executor, start or complete an epoch, create a branch, commit, push, open a PR, spend review, or merge. Without executor-task emission, it stops with `recommended_next_action` set to `blocked`, `no_candidates`, `approval_required`, or `requires_executor_contract`.

When an elected task exists, `--emit-executor-task` can attach a generic executor task packet for operator approval. The packet includes repo identity, an absolute repo path, allowed paths, command policy, branch policy, required checks, stop conditions, limits, and the expected result-evidence path. Cadence validates the embedded local snapshot as a trust anchor before accepting the packet, but it still does not run the executor:

```bash
agentic-cadence --root examples/first-run/work/runtime loop-tick --cwd examples/first-run/work/repo --repo local/demo --intent repo_health --emit-executor-task --allowed-path . --required-check "python -m unittest discover -s tests" > loop-tick.json
python -c "import json; print(json.dumps(json.load(open('loop-tick.json'))['executor_task'], indent=2))" > executor-task.json
```

The task packet is nested under `executor_task` in the `loop-tick` packet. It must be saved before validating returned executor evidence.

`start-governed-execution` is the local write-side gate that consumes a reviewed
`generic-executor-task.v1` packet and starts exactly one active epoch when the
task packet shape, task-carried command and branch policy fields, current repo
path, branch, `HEAD`, clean worktree, operator approval, brake, and
active-epoch state still match. The approval token is the exact checksum token
for the reviewed task packet:
`approve-executor-task:<task-packet-checksum>`.

```bash
agentic-cadence --root examples/first-run/work/runtime start-governed-execution --task-file executor-task.json --approval-token approve-executor-task:<task-packet-checksum>
```

The command emits an `execution-start.v1` packet with `read_only: false`,
`epoch_started`, `executor_started: false`, `blockers`, and
`recommended_next_action`. Stable blocker codes include
`task_file_unreadable`, `executor_task_invalid`, `operator_approval_missing`,
`operator_approval_mismatch`, `repo_path_mismatch`,
`repo_inspection_failed`, `repo_branch_mismatch`, `repo_head_mismatch`,
`dirty_worktree`, `repo_confidence_low`, `brake_state_invalid`,
`brake_not_drive`, `active_epoch_exists`, `active_epoch_invalid`,
`epoch_start_failed`, `audit_append_failed`, and `epoch_rollback_failed`.
Successful starts append an `execution_start_decision` audit record, but the
command does not invoke an executor, edit code, create branches, write pull
requests, merge, release, or publish packages.

Executor result evidence can be checked without running an executor:

```bash
agentic-cadence --root examples/first-run/work/runtime validate-executor-result --task-file executor-task.json --result-file executor-result.json
```

After an active epoch exists and a fresh `snapshot-repo` packet has been saved,
`closeout-executor-result` can consume the local task packet, result evidence,
snapshot-after packet, and optional `--run-record-file`. It validates the same
executor evidence gates, rejects supplied run records whose task checksum,
result checksum, validation checksum, repo branch/head anchors, or closeout
status no longer match, records a successful task result while keeping the epoch
active when other epoch tasks remain, completes the epoch only when all recorded
tasks are complete, fails the epoch for valid failed/blocked/stopped evidence or
policy violations, and emits the next local decision:

```bash
agentic-cadence --root examples/first-run/work/runtime closeout-executor-result --epoch-id epoch-123 --task-file executor-task.json --result-file executor-result.json --snapshot-after-file snapshot-after.json --run-record-file execution-run.json --emit-git-pr-plan --cwd examples/first-run/work/repo --required-body-section Summary --required-body-section Validation
```

The optional Git/PR plan is embedded as a dry-run packet only after terminal
epoch completion, and supplied PR-template inputs are validated before terminal
state is mutated. When a supplied run record is accepted and closeout succeeds,
Cadence updates that local `execution-run.v1` record with the closeout status,
epoch id/status, and closeout checksum, then appends an `execution_run_record`
audit event. The closeout command does not start an executor, create a branch,
commit, push, call GitHub, open a pull request, merge, release, or publish
packages.

For tests and examples, `run-controlled-executor-fixture` can launch a fake
external executor component from an explicit command template. The template may
use `{task_file}`, `{result_file}`, and `{repo_path}` placeholders, but it must
invoke the bundled fixture script by absolute path through the current Python interpreter.
Cadence validates the task packet and command policy before starting the
fixture, runs the fixture as an argument vector without shell expansion,
requires the expected result evidence file to stay inside the runtime root and
not already exist, writes a local `execution-run.v1` record under
`<root>/execution-runs/`, records `executor_fixture_invocation`,
`executor_result_validation`, and `execution_run_record` audit records, and
validates timeout or active-brake stop evidence before accepting the result:

```bash
agentic-cadence --root examples/first-run/work/runtime run-controlled-executor-fixture --task-file executor-task.json --command-template "\"/absolute/path/to/current-python\" \"/absolute/path/to/agentic-cadence/examples/controlled-executor-fixture/run.py\" --task-file \"{task_file}\" --result-file \"{result_file}\" --status succeeded --summary \"fixture completed\" --command \"python -m unittest discover -s tests\"" --timeout-seconds 60
```

The returned packet uses `controlled-executor-fixture-run.v1`. This command is
fixture-only. It is not a real executor integration or named host adapter, and
malformed templates fail closed before launch. It must not create branches,
commit, push, open or merge PRs, create releases, or publish packages.

Root-backed loop ticks, governed execution-start decisions, controlled fixture
invocation, execution-run records, executor-result validation, and executor
closeout append compact `cadence-audit.v1` records under
`<root>/audit/events.jsonl`; closeout audit anchors include the task packet,
result evidence, snapshot-after packet, and supplied run-record binding when
present. A local
`cadence-loop-policy.v1` file can bound emitted executor task paths, required
checks, command allow/deny lists, runtime, stop conditions, and a dry-run
`branch_policy`. The branch policy supports `allowed_base_branches`,
`denied_target_branches`, `required_branch_prefixes`, and
`allow_current_branch_main`; unknown branch-policy fields fail closed. It is
copied into emitted executor task packets and enforced by `git-pr-plan` along
with any local `--policy-file` branch policy.
Result validation enforces task-carried command policy across compound commands,
shell grouping, command substitutions, and shell-wrapper payloads, and it rejects non-`stopped`
completion evidence after an active brake stop. If a task includes `brake_not_drive`, otherwise-valid
non-`stopped` completion evidence requires a runtime root so the current brake
can be checked; rootless validation fails closed with `provide_runtime_root`.
`audit-replay` validates that
local audit history is readable, uses supported record shapes, has valid
checksum syntax, and reports stable blockers without modifying the log:

```bash
agentic-cadence --root examples/first-run/work/runtime audit-replay > audit-replay.json
```

The replay command does not repair audit files, recompute compact record
checksums from original packet bodies, run executors, create branches, commit,
push, open a PR, spend review, or merge.

## Context Handoff Preparation

`prepare-handoff` packages the old-session side of a context handoff into one deterministic local command. It checks Cadence state, snapshots the repo, writes a signed ready handoff, validates it, records clean-square, and returns a packet with `stop_current_session: true`.

```bash
agentic-cadence prepare-handoff --id context-loop --title "Continue bounded work" --guardrail context --repo owner/repo --cwd . --task-type execution --summary "current session is handing off"
```

The command does not claim the handoff, launch a new agent window, commit, push, open a PR, spend review, or merge. V1 requires an explicit guardrail such as `--guardrail context`; automatic context detection requires a host/session signal that Cadence does not infer from transcript guesses.

## Resume Verification

`verify-resume` is the read-only pickup gate for a fresh session. It verifies a
handoff signature, claimed handoff state, clean-square evidence, current repo
branch and `HEAD`, dirty-worktree state, active Cadence brake, active epoch
state, and handoff pickup-policy evidence before reporting whether work can
resume:

```bash
agentic-cadence --root <runtime-root> verify-resume context-loop --cwd . --claimer codex
```

The command returns a `resume-verification.v1` packet with `resumable`,
`read_only: true`, evidence sections, `blockers`, and
`recommended_next_action`. Each blocker is shaped as
`{"code": "...", "message": "...", ...}`. The command exits `0` when
`resumable` is true and exits `2` when verifier blockers are present.

Stable blocker codes include `handoff_not_found`, `handoff_unreadable`,
`handoff_not_claimed`, `handoff_claimed_by_other`,
`handoff_state_conflict`, `handoff_signature_invalid`,
`handoff_checksum_mismatch`, `handoff_protocol_unsupported`,
`resume_snapshot_invalid`, `handoff_repo_evidence_missing`,
`repo_inspection_failed`, `repo_head_mismatch`, `repo_branch_mismatch`,
`dirty_worktree`, `runtime_brake_missing`, `runtime_brake_invalid`,
`active_brake_stop`, `active_epoch_conflict`, `active_epoch_invalid`,
`active_epoch_repo_mismatch`, `active_epoch_branch_mismatch`,
`active_epoch_head_mismatch`, `clean_square_missing`,
`clean_square_invalid`, `policy_evidence_missing`,
`policy_evidence_invalid`, `policy_approval_missing`, and
`policy_self_evolution_propose_only`.

Recommendation values are stable: `resume_work`, `inspect_runtime_state`,
`clear_brake`, `clean_worktree`, `resolve_claim_conflict`,
`approve_handoff`, `claim_handoff`, `close_or_fail_active_epoch`,
`recreate_handoff`, and `inspect_resume_blockers`. Approval-gated ready
handoffs recommend `approve_handoff` before `claim_handoff`; active epoch
blockers recommend `close_or_fail_active_epoch`; stale repo, invalid handoff,
invalid resume snapshot, clean-square, or policy evidence recommends
`recreate_handoff`.

The command does not claim, complete, fail, or recreate handoffs; it does not
launch a new session, infer host context pressure, start or invoke an executor,
create branches, write pull requests, merge, release, or publish packages.

## Resume Continuation

`resume-continuation` is the read-only bridge from a saved
`resume-verification.v1` packet to the next governed execution-start decision.
It consumes a saved verifier packet, rechecks the handoff id, claimer, repo
branch and `HEAD`, dirty-worktree state, active brake, active epoch state,
clean-square evidence, pickup policy, and packet freshness, then emits a
`resume-continuation.v1` packet:

```bash
agentic-cadence --root <runtime-root> resume-continuation --resume-verification-file resume-verification.json --cwd . --claimer codex
```

A fresh matching packet exits `0` with `recommended_next_action:
start_governed_execution`, `executor_started: false`, `epoch_started: false`,
and `side_effects: []`. Blockers exit `2` and recommend only
`claim_handoff`, `approve_handoff`, `recreate_handoff`,
`close_or_fail_active_epoch`, or `inspect_resume_blockers`. Stable blocker
codes include `resume_verification_stale`,
`resume_verification_not_resumable`, `resume_claimer_mismatch`,
`resume_verification_anchor_mismatch`, and the forwarded verifier blockers
such as `repo_head_mismatch`, `clean_square_missing`,
`policy_approval_missing`, `active_brake_stop`, and
`active_epoch_exists` or `active_epoch_conflict`.

The command does not claim handoffs, launch sessions, start epochs, invoke an
executor, create branches, push, open PRs, merge, release, or publish packages.

## Local Work Ownership

`work-ownership-status` and `validate-work-ownership` are read-only local
evidence gates for `work-ownership.v1` records under
`<runtime-root>/work-ownership/{active,closed,failed}`. Records bind a local
task id, candidate id, role label, claimer, repo, branch, optional PR number,
optional epoch id, optional handoff id, status, and timestamps.

```bash
agentic-cadence --root <runtime-root> work-ownership-status --cwd . --repo owner/repo --task-id task-1
agentic-cadence --root <runtime-root> validate-work-ownership ownership-1 --cwd . --repo owner/repo --task-id task-1 --require-active
```

Status emits `work-ownership-status.v1`; validation emits
`work-ownership-validation.v1`. Both packets report `read_only: true`,
`side_effects: []`, stable blockers such as `duplicate_active_ownership`,
`ownership_record_invalid`, `ownership_registry_state_invalid`,
`ownership_timestamp_invalid`, and `repo_inspection_failed`. Validation also
reports target-record blockers such as `ownership_closed` and
`ownership_repo_mismatch`. Bounded next actions include
`use_work_ownership_status`, `resolve_duplicate_ownership`,
`refresh_ownership_evidence`, and `repair_ownership_record`.

These commands do not assign roles, schedule agents, write GitHub issues,
claim distributed locks, start epochs, invoke executors, mutate Git/PR state,
merge, release, or publish packages. Execution-start and resume-continuation
enforcement remains a later explicit integration point.

## GitHub Evidence Sync

`github-evidence-sync` is an explicit read-only live fetch for PR evidence. It
uses `gh pr view` and GitHub GraphQL review-thread reads only when the operator
asks for them, then writes local JSON evidence files for later deterministic
commands:

```bash
agentic-cadence github-evidence-sync --repo owner/repo --pr-number 9 --out-dir .cadence/github-evidence/pr-9
```

Successful sync writes saved PR JSON, saved review-thread JSON, and a summary
packet. Missing `gh`, authentication failure, rate limit, network failure, or
malformed JSON returns a blocked packet and does not write partial evidence
files. Incomplete paginated review-thread evidence also blocks instead of being
saved as valid. The command does not create branches, commit, push, edit pull
requests, merge, release, or publish packages.

## PR Readiness

`pr-readiness` evaluates saved `gh pr view --json ...` output and optional saved
review-thread JSON, then returns a deterministic merge-readiness packet. It does
not call GitHub, spend paid review, or merge the PR.

```bash
gh pr view 9 --json number,title,state,isDraft,mergeable,mergeStateStatus,reviewDecision,body,headRefName,baseRefName,headRefOid,statusCheckRollup > pr.json
agentic-cadence pr-readiness --pr-json-file pr.json --review-threads-file review-threads.json --required-check "Python and protocol checks" --pr-template-file .github/pull_request_template.md
```

The packet reports blockers, waiting checks, duplicate check groups, skipped Codex Review jobs, unresolved actionable review comments, malformed or incomplete review-thread evidence, missing body sections, missing PR-template sections, readiness evidence freshness, and the recommended next action. Saved PR JSON is labeled `saved_input`; when `--max-pr-json-age-minutes` is supplied and the file mtime is older than that limit, or appears to come from the future, the packet is labeled `stale`, waits, and recommends `refresh_pr_evidence`. The age limit must be non-negative and applies to saved PR JSON only; caller-asserted `live_like` evaluator inputs are labeled but not stale-gated by this saved-file policy. `--pr-template-file` reads a local Markdown template and checks that its headings are represented in the saved PR body; it does not rewrite the PR. `discover-candidates --pr-json-file <path> --review-threads-file <path>` can turn failing checks and unresolved current actionable review comments from saved evidence into bounded candidates.

## Review Response Plan

`review-response-plan` consumes saved PR JSON, optional saved review-thread JSON,
and optional candidate discovery output, then emits a read-only
`review-response-plan.v1` packet. It groups failed checks by check name,
unresolved actionable current review comments by review thread and file path,
and missing PR body sections as bounded plan items with likely follow-up tasks.

```bash
agentic-cadence review-response-plan --pr-json-file pr.json --review-threads-file review-threads.json --candidate-discovery-file candidates.json --pr-template-file .github/pull_request_template.md
```

The command recommends `emit_executor_task`, `refresh_pr_evidence`,
`update_pr_body`, `wait_for_checks`, or `operator_review`. It reads local saved
evidence only and does not call GitHub, resolve review threads, post comments,
update PR bodies, create branches, commit, push, merge, release, publish
packages, spend paid review, or invoke review agents.

## PR Body Preflight

`pr-body-preflight` checks a draft PR body before publishing or updating a pull request. It reads local files only, uses the same Markdown heading parser as `pr-readiness`, and never rewrites the body.

```bash
agentic-cadence pr-body-preflight --body-file pr-body.md --pr-template-file .github/pull_request_template.md
```

Use this before `gh pr create` or `gh pr edit` when a repository has a PR template. Missing template headings are reported as blockers with `recommended_next_action: update_pr_body`. If no template file or `--required-body-section` is supplied, the packet fails closed with `recommended_next_action: provide_template_or_sections`.

## Git/PR Dry-Run Plan

`git-pr-plan` turns a validated generic executor task packet and result evidence into a reviewable Git/PR transition plan. It inspects local Git refs, validates the current brake when `brake_not_drive` applies, generates proposed branch, commit, PR title, and PR body text, and can run the generated body through PR body preflight:

```bash
agentic-cadence --root <runtime-root> git-pr-plan --cwd . --task-file executor-task.json --result-file executor-result.json --required-body-section Summary --required-body-section Validation
```

The packet is dry-run only. Cadence does not create a branch, commit, push, call GitHub, or open a pull request. Result `files_changed` is not enough by itself; the planner requires explicit `materialized_change_evidence` before it reports the plan as ready for review.

## Operator-Approved Git/PR Materialization

`git-pr-materialize` consumes a reviewed `git-pr-plan.v1` packet plus an exact operator approval token for that packet and materialization target. The token is an HMAC over the plan checksum, selected remote name, resolved push URL, and create-vs-update PR target, using `CADENCE_GIT_PR_MATERIALIZATION_APPROVAL_SECRET`; the secret is required for verification and is never emitted in result packets. Immediately before side effects it rechecks the current branch and `HEAD`, branch policy, full local-diff coverage by materialized-change evidence, PR body preflight, task/result checksums, and plan freshness:

```bash
agentic-cadence --root <runtime-root> git-pr-materialize --cwd . --plan-file git-pr-plan.json --approval-token approve-git-pr:hmac-sha256:<materialization-target-hmac>
```

When all gates pass, Cadence appends a `git_pr_materialization_intent` audit record, creates the proposed branch from the current materialized commit without switching the checkout, pushes it with Git hook verification disabled for that push, and creates or updates a pull request through `gh`. Existing PR updates first run a read-only `gh pr view` preflight to verify the PR head and base match the approved plan. Cadence then appends a `git_pr_materialization_result` audit record. Missing, mismatched, or unverifiable approval, stale plans, dirty worktrees, branch-policy failures, materialized-evidence failures, PR body failures, and failed Git/`gh` commands return `git-pr-materialization.v1` blocker packets. The command does not auto-merge, release, publish packages, or invoke an executor.

## Release Dry Run

`release-dry-run` checks release metadata before an operator creates a tag or GitHub release. After `pyproject.toml` and `CHANGELOG.md` have been updated for the intended release version, it reads local metadata and Git refs, generates release notes from the matching changelog entry, requires the selected target ref to match checked-out `HEAD`, verifies an existing tag points at the selected release commit, and returns a JSON packet with `operator_confirmation_required: true`.

```bash
agentic-cadence release-dry-run --cwd . --version <version>
```

The command does not create tags, call GitHub, create a release, write release-note files, build distributions, upload artifacts, or publish packages. Package-index publication remains blocked in the packet with `recommended_next_action: do_not_publish_package`.

For repository releases, `.github/workflows/release-dry-run.yml` exposes the same check as a manual GitHub Actions workflow. It accepts `version`, `tag`, and optional `target_ref`, uploads `release-dry-run.json` and `release-notes.md`, and fails on blockers while still requiring operator confirmation for any tag, GitHub release, or package publication.

## Runtime State

Runtime state lives outside project repositories by default for new installs:

```text
~/.codex/cadence
```

If the legacy `~/.codex/transmission` root already exists, Agentic Cadence reuses it so queued handoffs and brake state survive the rename. If both legacy and Cadence roots exist, Cadence fails closed until you select one with `--root`, `CODEX_CADENCE_ROOT`, or `CODEX_TRANSMISSION_ROOT`. Commands invoked with `--root X` create the runtime layout at `X` when it is missing.

Root-using commands also guard against accidentally placing runtime state inside the target git checkout. If `--root` points inside the repo selected by `--cwd`, or inside the current repo for commands without `--cwd`, the root must be ignored by git. Otherwise the command fails unless the operator passes the top-level `--allow-repo-local-root` flag before the subcommand. Prefer runtime roots outside project repositories; the override is for explicit operator-owned exceptions.

Cadence exposes these states:

![Cadence state summary for PLAY_ON, HUDDLE, and TIMEOUT.](docs/assets/cadence-states.svg)

- `PLAY_ON`: work may continue.
- `HUDDLE`: pause and coordinate.
- `TIMEOUT`: stop until an operator resumes.

## Compatibility

Primary names:

- `agentic-cadence`
- `python -m codex_cadence`
- `scripts/cadence.py`
- `codex_cadence.*`

Compatibility names:

- `codex-cadence`
- `codex-transmission`
- `scripts/transmission.py`
- `transmission_control.*`

## Development

```bash
python -m compileall scripts codex_cadence transmission_control tests examples
python -m unittest discover -s tests -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
```

Package verification:

```bash
python -m pip install build
python -m build
python scripts/verify_package.py
```

## License

Agentic Cadence is licensed under the MIT License. See `LICENSE`.
