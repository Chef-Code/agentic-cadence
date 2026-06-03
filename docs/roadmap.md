# Agentic Cadence Technical Roadmap

Status: living document
Last updated: 2026-06-02
Baseline: released 0.1.3 plus unreleased audit-replay, policy/stop-control, git-pr-plan, branch policy, read-only GitHub evidence sync, controlled executor fixture, and operator-approved Git/PR materialization current tree
Current unattended-operation confidence: 10%

This document tracks the practical path from the current Agentic Cadence
protocol toolkit toward GitHub-native orchestration for autonomous software
teams. The first usable path is a bounded single-agent loop, but the larger
product direction is an orchestrator that coordinates multiple cooperating
agents through issues, branches, pull requests, reviews, CI, documentation,
handoffs, and merge decisions. This document should be updated whenever
implementation, validation, or review evidence changes the project reality.

## Documentation Governance

Agentic Cadence documentation is part of the operating model, not static
reference material. Any pull request that materially changes architecture,
workflow, capabilities, limitations, roadmap priorities, confidence,
readiness, governance, handoff behavior, approvals, executor behavior, or
review behavior must evaluate whether these living documents need updates:

- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
- `docs/agent-team-orchestration.md`

If documentation remains unchanged in a meaningful implementation PR, the PR
author should be able to explain why no documentation update was necessary.
Evidence takes precedence over aspiration.

## North Star

Agentic Cadence should become an agent-neutral governance and orchestration
layer for autonomous software teams. It should start by governing one bounded
agent, but its primitives should scale toward an agent pool with role-aware
planning, architecture review, implementation, QA, documentation, release, and
handoff responsibilities.

The intended mature experience is:

1. point Agentic Cadence at a repository;
2. inspect current repo, CI, PR, review, policy, and handoff state;
3. identify, decompose, or elect the best bounded next task;
4. assign work to the appropriate agent role under policy;
5. run each task inside explicit branch, path, command, time, and review
   limits;
6. validate changes with configured checks;
7. open or update a pull request;
8. trigger and ingest separate review;
9. turn CI or review feedback into follow-up tasks;
10. update living documentation when behavior or architecture changes;
11. monitor context pressure, role boundaries, and policy limits;
12. create a handoff when a session, role, or work boundary requires it;
13. let the next role or fresh session claim, verify, resume, and continue
    safely.

The project should keep the core protocol small and inspectable. Host adapters
may render different user experiences, but they should share the same core
concepts: Cadence state, handoffs, clean-square evidence, task sizing, epoch
governance, approval gates, PR readiness, review separation, audit records,
agent roles, handoff contracts, and release guardrails.

## Current Readiness

Agentic Cadence is not currently a magic-button autonomous builder or an
agent-team orchestrator. The released 0.1.3 baseline is a local CLI and
protocol substrate that can govern and document agentic work. The current
development tree adds unreleased audit replay evidence, command-policy
enforcement, active-stop result-validation controls, dry-run Git/PR planning,
local branch policy, read-only GitHub evidence sync, and a controlled executor
fixture runner for tests/examples, but it still cannot independently implement code, push branches, open
pull requests, assign agent roles, resolve review feedback, launch fresh
sessions, coordinate an agent pool, or continue in an unattended loop.

Current confidence for unattended continuous operation is 10%.

The rating is low because the safety primitives are real, but the central
autonomous build loop is not implemented. The first real unattended run would
stop at implementation or PR/review integration.

See `docs/autonomous-loop-readiness.md` for the direct readiness assessment.

## Current State

The current development tree is based on the released 0.1.3 baseline for the
0.1.x line and adds the unreleased audit-replay implementation plus local
command-policy and active-stop controls. It includes:

- packaged `agentic-cadence` CLI with Codex-compatible command aliases;
- local Cadence state with `PLAY_ON`, `HUDDLE`, and `TIMEOUT`;
- handoff creation, preparation, approval, claim, completion, and failure;
- repo snapshots and clean-square validation for old-session shutdown;
- task sizing, epoch governance, continuation checks, and pickup gates;
- read-only candidate discovery from local repo signals, saved review
  findings, saved PR check evidence, saved GitHub review-thread files, text
  markers, and business memory;
- read-only `loop-tick` orchestration that emits a governed next-action packet
  without starting execution, epoch mutation, PR actions, review spend, merge,
  release, or publication;
- initial local loop policy and audit controls for `loop-tick
  --emit-executor-task` and `validate-executor-result`, including
  path/command/check/runtime/stop-condition bounds, active brake stop handling,
  and compact JSONL decision records;
- read-only `audit-replay` verification for local `cadence-audit.v1` JSONL
  history, including stable blockers for corrupt or unsupported records;
- generic executor task/result packet validation, including local snapshot
  trust-anchor checks for repo name, absolute cwd/path, branch, head, dirty
  worktree, and low-confidence state;
- fixture-only `run-controlled-executor-fixture` support that governs a fake
  external executor component through task policy, timeout handling, audit
  records, and result-evidence validation without claiming real executor or
  named-host adapter support;
- deterministic PR body preflight and PR readiness checks from saved local
  inputs;
- dry-run-only `git-pr-plan` packets that turn validated executor evidence into
  proposed branch, commit, PR title, and PR body text without creating a branch,
  committing, pushing, calling GitHub, or opening a pull request;
- explicit read-only `github-evidence-sync` packets that fetch PR metadata,
  status checks, and review threads into saved local JSON evidence files without
  GitHub writes;
- PR readiness and candidate discovery ingestion for saved review-thread
  evidence and saved failing-check evidence;
- release dry-run checks that require operator confirmation before tag or
  release actions;
- elected Codex Review GitHub workflow with preflight, dedupe, pinned action,
  same-repository restriction, and read-only sandbox;
- generic adapter smoke, host-signal, shell-binding, conformance, contract
  runner, evidence, and claim-verifier examples;
- package install and first-run examples in CI on Ubuntu and Windows.

These are Phase 1 foundations. They should be preserved because the same
primitives also support future multi-agent coordination: task election maps to
work ownership, epochs bound agent effort, handoffs become role-transfer
contracts, PR readiness supports merge decisions, and review feedback becomes
bounded follow-up work.

## Adapter Contract Baseline

The 0.1.3 roadmap still treats the public CLI and generic adapter contracts as
release-critical evidence. Future roadmap edits should preserve these concrete
contract references because tests and reviewers use them to verify that named
host adapter claims remain gated by generic evidence:

- `examples/adapter-smoke/run.py` proves that a host adapter can drive the
  public CLI and preserve returned JSON packets.
- `examples/adapter-template` is the copyable adapter template, including the
  host-binding mapping example at
  `examples/adapter-template/host-binding-mapping.md`, and maps host signals
  without adding a core object model.
- `examples/adapter-template/host_signal_contract.py` validates generic host
  signal fixtures and shell host-event payloads before replay and parity runs.
- `examples/generic-host-signal/run.py --parity-contract` is the generic host-signal smoke; it
  compares the adapter-template host-signal fixtures against the generic shell
  replay contract.
- `examples/generic-shell-host-binding/run.py` is the generic shell host-binding
  example. It includes file-backed `--host-event-file`,
  stdin-backed `--host-event-stdin`, and `--replay-contract` paths that compare
  fixture, file-backed, and stdin-backed behavior without claiming to be a real
  host adapter.
- `examples/external-host-binding-conformance/run.py` compares a supplied
  binding command against the generic shell baseline. Binding command templates
  must preserve the `"{host_event_file}"` and `"{case_work_dir}"`
  placeholders.
- `examples/adapter-contract-runner/run.py` is the generic adapter contract
  pre-claim runner. It composes schema, smoke, replay, parity, and external
  conformance contracts before any named host adapter claim and can emit compact
  PR evidence with `--evidence-summary`.
- PR checks upload the compact adapter contract evidence summary as the
  `generic-adapter-contract-evidence` artifact containing
  `adapter-contract-evidence.json`. The evidence uses
  `generic-adapter-contract-evidence.v1` and the schema file at
  `examples/adapter-contract-runner/generic-adapter-contract-evidence.v1.schema.json`.
- Reviewers should validate downloaded evidence with
  `python examples/adapter-contract-runner/run.py --validate-evidence-file adapter-contract-evidence.json`
  and the checklist in `docs/adapter-claim-checklist.md`.
- `examples/adapter-claim-verifier/run.py` turns compact adapter evidence into
  a generic-only or named-host-claim decision using
  `--evidence-file adapter-contract-evidence.json`; named claims also require
  `--claim-host` and the matching binding command template.

## What Is Planned But Not Built

The following capabilities are not implemented as of this baseline:

- continuous loop runner;
- GitHub-native issue/task ownership and assignment workflow;
- agent pool, role registry, and role-aware permission model;
- task decomposition across Planning, Architecture, Builder, Reviewer, QA,
  Documentation, Release, and Handoff agents;
- real executor invocation or named executor adapter integration;
- autonomous code modification;
- branch, commit, push, and PR creation workflow;
- live GitHub PR, check, comment, and review-thread synchronization;
- review-feedback response loop;
- real host context-pressure integration;
- automatic fresh-session launch and resume orchestration;
- distributed locking, shared runtime, authenticated approval identity, or
  tamper-evident remote audit log;
- autonomous merge, release, or package publication.

## Known Edges

These are the important boundaries that are not solved yet:

- No Claude or Gemini adapter is shipped. The project has generic adapter
  contracts and reviewer-verifiable compact evidence, not full named host
  integrations.
- There is no automatic real-host context-pressure integration. Host/session
  signal handling remains explicit, and `prepare-handoff` still requires input
  from a caller or host binding.
- Runtime state is local filesystem state. There is no shared remote backend
  for teams or cloud agents.
- Local locks protect local transitions, but there is no distributed lock model
  for multiple machines.
- Claimer, approver, operator, and future agent-role values are records, not
  authenticated identities with role enforcement.
- Current flows mostly assume one active implementation agent. Future work must
  avoid baking that assumption into packet schemas, audit records, review
  gates, or GitHub automation.
- Review integration is deterministic after evidence capture. Candidate
  discovery can ingest saved review findings and saved GitHub review-thread
  files, `pr-readiness` reads saved PR data, and `github-evidence-sync` can
  explicitly fetch read-only PR/check/review-thread evidence into local files;
  Cadence does not write, continuously synchronize, or resolve live GitHub
  review threads.
- Release verification is documented and repeatable, and `release-dry-run` can
  inspect local release metadata and Git refs, but release tagging and GitHub
  release creation still require operator execution.
- Package distribution is clone-based. PyPI publication is not part of the
  current baseline.

## Target State

A mature Agentic Cadence system should provide:

- a reliable bounded loop controller that can inspect state, select work,
  delegate execution, collect evidence, and stop safely;
- GitHub-native coordination where issues or recorded decisions define work,
  branches isolate implementation, pull requests expose changes, reviews and CI
  gate quality, docs stay aligned, and merges advance stable `main`;
- an orchestrator that can coordinate an agent pool through explicit Planning,
  Architecture, Builder, Reviewer, QA, Documentation, Release, and Handoff
  roles;
- thin, tested host adapters added only after generic adapter contracts are
  stable;
- explicit host/session signals for context pressure, reviewer loops, CI loops,
  and operator stop requests;
- a shared runtime backend with clear locking, identity, audit, and rollback
  expectations;
- role-aware approval and claim semantics tied to real users or agent
  identities;
- enforceable review separation so the reviewing role is distinct from the
  building role when possible;
- handoff contracts that transfer state across sessions and across roles, not
  only when one context window is exhausted;
- configurable policy for task sizing, pickup approval, review spend, command
  execution, PR behavior, and release authority;
- first-class PR review synchronization that fetches review threads, tracks
  resolution, and preserves deterministic local evaluation;
- an operator-facing view of Cadence state, active handoffs, approvals, PR
  readiness, audit history, and release status without bypassing CLI packets.

## 50% Confidence Target

The next target is not full autonomy or full agent-team orchestration. The
practical 50% confidence target is a bounded controlled loop that can run
unattended only inside a pre-approved tick on a low-risk repository, stop
cleanly, and leave enough evidence for an operator to understand what happened.

Target constraints:

- one repository;
- one branch or pull request at a time;
- one bounded loop tick per invocation, usually with one implementation agent;
- generic executor adapter, not a named host adapter;
- explicit policy for allowed paths, commands, runtime, checks, and approval
  points;
- no auto-merge;
- no release or package publication;
- human approval for task start and PR creation until later evidence supports
  relaxing those gates;
- append-only audit trail for each decision and result.

The smallest slices expected to move confidence toward 50% are tracked in
`docs/implementation-slices.md`:

1. Single-Tick Loop Orchestrator
2. Generic Executor Adapter Contract
3. Policy, Audit, and Stop Controls
4. Minimal Git/PR Automation
5. CI/Review Feedback Back Into Candidate Discovery

## Roadmap

## Immediate Goals

### Runtime-root safety

Goal: prevent Cadence runtime state from accidentally dirtying the target repo.

Status: implemented as an immediate stabilization slice.

Current evidence: root-using CLI commands now reject an unignored runtime root
inside the target git repo unless the operator passes
`--allow-repo-local-root`. Gitignored repo-local roots are allowed. Runtime
state is filesystem-based and candidate discovery reacts to dirty worktree
state, so this guard prevents a self-created dirty-worktree signal.

Implementation files: `codex_cadence/store.py`, `codex_cadence/repo_state.py`,
`codex_cadence/cli.py`, tests, docs.

Validation: temp-repo tests cover explicitly allowed repo-local runtime,
ignored repo-local runtime, blocked unignored repo-local runtime,
cross-command runtime-root guarding, and no-root planning/discovery commands.

Follow-up: future adapter conformance tests should prove equivalent
runtime-root policy when an adapter bypasses the CLI.

### Readiness and freshness labels

Goal: make local-only state, saved PR state, stale state, and live state
explicit in packets and docs.

Status: implemented and merged in PR #47 as an immediate stabilization slice.

Current evidence: repo snapshots now include `readiness_evidence` with
`freshness: local_only` and limitations for unfetched PR/review state.
`pr-readiness` packets now include `readiness_evidence` for `saved_input`,
`stale`, and caller-asserted `live_like` evidence, and
`github-evidence-sync` can refresh PR/check/review-thread evidence into saved
local files before those deterministic readers run. Snapshot validation now
requires local snapshot readiness evidence. Saved PR JSON can be age-gated with
`--max-pr-json-age-minutes`; stale or future-dated saved evidence waits before
acting on PR blockers and recommends `refresh_pr_evidence`. Negative age limits
are rejected. Caller-asserted `live_like` evidence is not gated by saved-JSON
age policy. `pr-readiness` still evaluates saved JSON files only and does not
call GitHub.

Implementation files: `codex_cadence/repo_state.py`,
`codex_cadence/pr_readiness.py`, `codex_cadence/cli.py`, tests, docs.

Validation: fixtures cover local-only repo snapshots, snapshot validation
rejection for missing or malformed readiness evidence, saved PR evidence,
stale and future-dated saved PR evidence with refresh recommendation before
stale blockers, non-negative CLI age limits, and live-like evaluator labels
that remain outside saved-JSON age policy.

Follow-up: continuous GitHub reconciliation and write-side PR/review actions
remain future work.

## Short-Term Goals

### Single-tick loop orchestrator

Goal: add one bounded command that performs snapshot, candidate discovery,
policy check, epoch start, executor handoff, validation collection,
epoch completion/failure, and next decision.

Status: partial. Phase 1 implements a read-only `loop-tick` command.

Current evidence: `loop-tick` captures and persists a local repo snapshot,
runs deterministic candidate discovery with election enabled, checks Cadence
state, and emits `blocked`, `no_candidates`, `approval_required`, or
`requires_executor_contract`; with `--emit-executor-task`, it can emit
`approve_executor_task` or `policy_denied` when a supplied local loop policy
rejects the requested executor-task bounds. It appends a compact audit record
for root-backed loop decisions. It sets `executor_started`, `epoch_started`,
and `pr_action_started` to false. It does not yet start an epoch, hand work to
a real executor, run execution, complete or fail epochs, or drive PR/handoff
decisions after execution.

Likely files: `codex_cadence/cli.py`, `codex_cadence/candidates.py`,
`codex_cadence/epochs.py`, `codex_cadence/model.py`,
`codex_cadence/store.py`, tests, examples.

Validation: fixture repo tests cover no-candidate, executor-contract-required,
dirty-worktree approval-required with and without elected candidates, red-CI
approval-required, stop-brake blocked paths, policy-denied task emission, and
loop-decision audit records. Full slice completion still needs executor
success/failure, active epoch conflict, stale snapshot rejection, validation
collection, and completion/failure paths.

Codex can implement directly if the command remains generic and does not push
or merge.

### Generic executor adapter contract

Goal: define how Cadence emits a task packet and receives structured executor
evidence.

Current evidence: adapter templates and generic host-binding contracts exist.
The first generic executor contract now defines and validates
`generic-executor-task.v1` and `generic-executor-result.v1`; `loop-tick
--emit-executor-task` can attach a bounded task packet for operator approval,
and `validate-executor-result` can validate local evidence. Task-packet
validation now treats the embedded local repo snapshot as a trust anchor by
validating snapshot shape/readiness, requiring non-empty repo identity,
absolute cwd/path consistency, branch/head consistency, and rejecting dirty,
low-confidence, relative-path, or mismatched snapshots. No real executor
applies code changes yet.

Likely files: `docs/adapters.md`, `examples/adapter-template`,
`examples/adapter-contract-runner`, `codex_cadence/model.py`,
`codex_cadence/cli.py`, tests.

Validation: fake executor success, failure, blocked, stopped/timeout-shaped
evidence, malformed timestamp order, required-check enforcement, forbidden
head-change/command rejection, dirty success rejection, invalid task paths, and
disallowed changed files.

Codex can implement the generic contract directly. Named host adapters require
operator approval.

### Policy, audit, and stop controls

Goal: make unattended loop behavior bounded, inspectable, and interruptible.

Current evidence: Cadence state, brakes, epochs, and handoff records exist.
`loop-tick --policy-file` can load local `cadence-loop-policy.v1` JSON to
bound emitted executor task `allowed_paths`, `denied_paths`,
`allowed_commands`, `denied_commands`, `required_checks`,
`max_executor_time_minutes`, `stop_conditions`, and a dry-run `branch_policy`
while retaining built-in safety stops.
Root-backed `loop-tick` and `validate-executor-result` append compact
`cadence-audit.v1` records to `<root>/audit/events.jsonl`; result-validation
audit records include task and result evidence checksums. `audit-replay`
validates that local JSONL history is readable, uses supported record shapes,
and has valid checksum syntax while reporting stable blockers for corrupt or
unsupported records. Executor task packets now carry command allow/deny policy
into result validation, and `validate-executor-result` prevents non-`stopped`
completion evidence from being recorded after an active brake stop. There is
now local branch policy for dry-run Git/PR planning, but still no hash chain or
authenticated approval identity.

Likely files: `codex_cadence/model.py`, `codex_cadence/store.py`,
`codex_cadence/cli.py`, tests, docs.

Validation: initial policy allow/deny tests, denied command tests, active-stop
brake tests, loop-decision audit record tests, executor-result validation audit
record tests, and audit replay tests exist.

Codex can implement direct local policy and audit controls. Destructive cleanup
or default-autonomous permissions require operator approval.

## Medium-Term Goals

### Minimal Git/PR automation

Goal: after an approved successful task, produce a dry-run Git/PR transition
plan from validated result evidence, then materialize that reviewed plan only
through an explicit operator-approved command.

Current evidence: PR body preflight and readiness checks exist. The first
increment added a dry-run-only `git-pr-plan` packet that turns validated
executor result evidence into a reviewable Git/PR transition plan without
executing suggested commands, calling GitHub, or treating the executor as the
final authority for Git/PR approval. `git-pr-materialize` can now consume that
reviewed packet plus exact target-bound operator approval, recheck current
branch/head/base branch, branch policy, complete local-diff materialized
evidence, PR body preflight, remote push URL, and optional PR update target,
then create the branch without switching the checkout, push with Git hook
verification disabled for that push, and create/update a PR. Cadence still does
not create dirty-worktree commits, auto-merge, release, publish packages, or
invoke a real executor.

Likely files: `codex_cadence/cli.py`, `codex_cadence/pr_readiness.py`,
scripts, tests, docs.

Validation: focused tests cover dry-run packet generation, PR body preflight
success/failure, no `gh` calls, no Git or runtime mutation, invalid task/result
evidence, brake-gated success without a runtime root, active brake stops for
non-`stopped` evidence, non-success results, no materialized changes, dirty
worktrees, HEAD mismatches, detached heads, current-branch mismatches, missing
local base branches, generated branch collisions, missing PR template sections,
and invalid branch names. Operator-approved materialization tests cover mocked
branch creation, push, PR creation, approval mismatch, stale state, failed Git
and `gh` commands, dirty-worktree materialization rechecks, target-bound remote
approval, PR update preflight mismatch, full local-diff materialized-evidence
coverage, command-trace allowlists, and replayable materialization audit
evidence. Later live-action increments can add pending CI, failing CI, passing
CI, and post-PR stale-state handling.

Cadence can materialize a reviewed dry-run packet only through the explicit
operator-approved `git-pr-materialize` command. Auto-merge, release, package
publication, and real executor invocation remain outside this capability.

### CI and review feedback as candidate input

Goal: convert failing checks and unresolved actionable review comments into
bounded candidates for the next loop tick.

Current evidence: candidate discovery can ingest saved review findings, saved
review-thread files, and saved PR JSON check failures. `github-evidence-sync`
can explicitly fetch read-only live PR metadata, status checks, and review
threads into local evidence files, while PR readiness reports blockers from
saved PR JSON and saved review-thread JSON. Write-side sync, automatic response
actions, and resolution tracking are not implemented.

Likely files: `codex_cadence/github_evidence.py`,
`codex_cadence/candidates.py`, `codex_cadence/pr_readiness.py`, tests.

Validation: fixtures where mocked `gh` success writes saved evidence, missing
or failing `gh` blocks without partial files, a failing check becomes a fix
candidate, unresolved review feedback blocks merge readiness, resolved feedback
is ignored, and outdated feedback is ignored.

Codex can implement direct local ingestion and explicit read-only GitHub
evidence capture. GitHub writes require operator approval before credentials or
workflow permissions change.

### Resume verifier

Goal: validate handoff signature, clean-square, claimed state, repo head,
branch, policy, and Cadence state before a fresh session resumes work.

Current evidence: handoff preparation, claim, completion, and failure exist,
but session launch and resume orchestration remain external.

Likely files: `codex_cadence/handoff_loop.py`, `codex_cadence/cli.py`,
`codex_cadence/store.py`, tests, docs.

Validation: stale SHA, wrong branch, dirty worktree, double claim, missing
approval, and failed clean-square fixtures.

Codex can implement directly.

## Long-Term Goals

### GitHub-native agent-team orchestration

Goal: coordinate multiple bounded agents through GitHub-native work ownership,
branch isolation, PR review, CI, documentation updates, handoff contracts, and
merge decisions.

Current evidence: Phase 1 governance primitives exist locally, but no agent
pool, role registry, issue assignment workflow, distributed lock, write-side
GitHub sync, or role-aware permission system exists.

Risk: high.

Codex should ask before implementing role assignment, autonomous merge, shared
runtime, or live GitHub write permissions. Documentation can describe these as
future design targets when it preserves the current limitations.

### Controlled continuous build mode

Goal: run repeated bounded loop ticks under policy with explicit stop,
handoff, audit, PR, CI, and review gates.

Current evidence: loop-control primitives exist, but repeated autonomous
orchestration does not.

Risk: high.

Codex should ask before changing default behavior or enabling unattended
operation by default.

### Named host adapters

Goal: add real host adapters only after the generic adapter and executor
contracts are stable and verified.

Current evidence: docs and examples intentionally avoid named non-Codex host
adapter claims.

Risk: high.

Codex should ask before adding or documenting named host adapter support.

### Shared runtime or dashboard

Goal: expose loop state, approvals, handoffs, PR readiness, failures, and audit
history for teams.

Current evidence: runtime state is local filesystem state and no remote backend
is promised in 0.1.x.

Risk: high.

Codex should ask before starting this work.

## Evidence Required For Confidence Changes

Confidence should only move when evidence changes. Useful evidence includes:

- passing unit tests;
- passing fixture-loop tests;
- CI results;
- adapter contract evidence;
- successful dry-run packets;
- successful controlled demo runs;
- review outcomes;
- audit replay output;
- documented failure and recovery runs.

Each meaningful implementation slice should update `docs/progress-log.md` with
the date, changed capability, validation evidence, confidence impact, and any
new blockers.

## Non-Goals For 0.1.x

- No autonomous merge or release without explicit operator instruction.
- No claim that Claude or Gemini adapters are shipped.
- No hidden writes to Cadence runtime files outside the public CLI.
- No remote backend or distributed lock promise.
- No claim that agent-team orchestration, role assignment, or autonomous merge
  is implemented.
- No PyPI publication.
- No replacement for generic secret scanning or external security review.

## Open Questions

- What repository should be used for the first controlled demo loop?
- What additional executor evidence and policy controls are sufficient for 50%
  confidence once a real executor is invoked?
- Which commands and paths should be allowed by the default local policy?
- How much PR automation can be enabled before human approval becomes
  mandatory?
- What issue/task identity should bind a future agent role claim to a specific
  branch and pull request?
- What evidence proves that review separation was preserved when possible?
- Which host should receive the first named adapter after the generic contract
  proves stable?
- What identity model is strong enough for approvals without making local use
  too heavy?
