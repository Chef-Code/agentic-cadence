# Agentic Cadence Technical Roadmap

Status: living document
Last updated: 2026-05-30
Baseline: released 0.1.3 tree
Current unattended-operation confidence: 10%

This document tracks the practical path from the current Agentic Cadence
protocol toolkit toward the north-star "press start and build continuously"
experience. It should be updated whenever implementation, validation, or
review evidence changes the project reality.

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

If documentation remains unchanged in a meaningful implementation PR, the PR
author should be able to explain why no documentation update was necessary.
Evidence takes precedence over aspiration.

## North Star

Agentic Cadence should become an agent-neutral governance and loop-control
layer for long-running coding-agent work. The intended mature experience is:

1. point Agentic Cadence at a repository;
2. inspect current repo, CI, PR, review, policy, and handoff state;
3. identify the best bounded next task;
4. prepare an implementation task for a coding agent;
5. run the task inside explicit policy limits;
6. validate changes with configured checks;
7. open or update a pull request;
8. trigger and ingest reviews;
9. turn CI or review feedback into follow-up tasks;
10. monitor context pressure and policy limits;
11. create a handoff when needed;
12. let a fresh session claim, verify, resume, and continue safely.

The project should keep the core protocol small and inspectable. Host adapters
may render different user experiences, but they should share the same core
concepts: Cadence state, handoffs, clean-square evidence, task sizing, epoch
governance, approval gates, PR readiness, audit records, and release
guardrails.

## Current Readiness

Agentic Cadence is not currently a magic-button autonomous builder. The 0.1.3
baseline is a local CLI and protocol substrate that can govern and document
agentic work, but it cannot independently implement code, push branches, open
pull requests, resolve review feedback, launch fresh sessions, or continue in
an unattended loop.

Current confidence for unattended continuous operation is 10%.

The rating is low because the safety primitives are real, but the central
autonomous build loop is not implemented. The first real unattended run would
stop at implementation or PR/review integration.

See `docs/autonomous-loop-readiness.md` for the direct readiness assessment.

## Current State

The current tree is based on the released 0.1.3 baseline for the 0.1.x line.
It includes:

- packaged `agentic-cadence` CLI with Codex-compatible command aliases;
- local Cadence state with `PLAY_ON`, `HUDDLE`, and `TIMEOUT`;
- handoff creation, preparation, approval, claim, completion, and failure;
- repo snapshots and clean-square validation for old-session shutdown;
- task sizing, epoch governance, continuation checks, and pickup gates;
- read-only candidate discovery from local repo signals, saved review
  findings, saved GitHub review-thread files, text markers, and business
  memory;
- deterministic PR body preflight and PR readiness checks from saved local
  inputs;
- release dry-run checks that require operator confirmation before tag or
  release actions;
- elected Codex Review GitHub workflow with preflight, dedupe, pinned action,
  same-repository restriction, and read-only sandbox;
- generic adapter smoke, host-signal, shell-binding, conformance, contract
  runner, evidence, and claim-verifier examples;
- package install and first-run examples in CI on Ubuntu and Windows.

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
- implementation executor contract;
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
- Claimer, approver, and operator values are records, not authenticated
  identities with role enforcement.
- Review integration is deterministic and local. Candidate discovery can
  ingest saved review findings and saved GitHub review-thread files, while
  `pr-readiness` reads saved PR data; Cadence does not fetch, synchronize, or
  resolve live GitHub review threads.
- Release verification is documented and repeatable, and `release-dry-run` can
  inspect local release metadata and Git refs, but release tagging and GitHub
  release creation still require operator execution.
- Package distribution is clone-based. PyPI publication is not part of the
  current baseline.

## Target State

A mature Agentic Cadence system should provide:

- a reliable bounded loop controller that can inspect state, select work,
  delegate execution, collect evidence, and stop safely;
- thin, tested host adapters added only after generic adapter contracts are
  stable;
- explicit host/session signals for context pressure, reviewer loops, CI loops,
  and operator stop requests;
- a shared runtime backend with clear locking, identity, audit, and rollback
  expectations;
- role-aware approval and claim semantics tied to real users or agent
  identities;
- configurable policy for task sizing, pickup approval, review spend, command
  execution, PR behavior, and release authority;
- first-class PR review synchronization that fetches review threads, tracks
  resolution, and preserves deterministic local evaluation;
- an operator-facing view of Cadence state, active handoffs, approvals, PR
  readiness, audit history, and release status without bypassing CLI packets.

## 50% Confidence Target

The next target is not full autonomy. The practical 50% confidence target is a
bounded controlled loop that can run unattended only inside a pre-approved tick
on a low-risk repository, stop cleanly, and leave enough evidence for an
operator to understand what happened.

Target constraints:

- one repository;
- one branch or pull request at a time;
- one bounded loop tick per invocation;
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
`stale`, and caller-asserted `live_like` evidence. Snapshot validation now
requires local snapshot readiness evidence. Saved PR JSON can be age-gated with
`--max-pr-json-age-minutes`; stale or future-dated saved evidence waits before
acting on PR blockers and recommends `refresh_pr_evidence`. Negative age limits
are rejected. Caller-asserted `live_like` evidence is not gated by saved-JSON
age policy. The CLI still evaluates saved JSON files only and does not call
GitHub.

Implementation files: `codex_cadence/repo_state.py`,
`codex_cadence/pr_readiness.py`, `codex_cadence/cli.py`, tests, docs.

Validation: fixtures cover local-only repo snapshots, snapshot validation
rejection for missing or malformed readiness evidence, saved PR evidence,
stale and future-dated saved PR evidence with refresh recommendation before
stale blockers, non-negative CLI age limits, and live-like evaluator labels
that remain outside saved-JSON age policy.

Follow-up: live GitHub fetching and reconciliation remain future work.

## Short-Term Goals

### Single-tick loop orchestrator

Goal: add one bounded command that performs snapshot, candidate discovery,
policy check, epoch start, executor handoff, validation collection,
epoch completion/failure, and next decision.

Status: partial. Phase 1 implements a read-only `loop-tick` command.

Current evidence: `loop-tick` captures and persists a local repo snapshot,
runs deterministic candidate discovery with election enabled, checks Cadence
state, and emits `blocked`, `no_candidates`, `approval_required`, or
`requires_executor_contract`. It sets `executor_started`, `epoch_started`, and
`pr_action_started` to false. It does not yet start an epoch, hand work to an
executor, run validation, complete or fail epochs, or drive PR/handoff
decisions after execution.

Likely files: `codex_cadence/cli.py`, `codex_cadence/candidates.py`,
`codex_cadence/epochs.py`, `codex_cadence/model.py`,
`codex_cadence/store.py`, tests, examples.

Validation: fixture repo tests cover no-candidate, executor-contract-required,
dirty-worktree approval-required, and stop-brake blocked paths. Full slice
completion still needs executor success/failure, active epoch conflict, stale
snapshot rejection, validation collection, and completion/failure paths.

Codex can implement directly if the command remains generic and does not push
or merge.

### Generic executor adapter contract

Goal: define how Cadence emits a task packet and receives structured executor
evidence.

Current evidence: adapter templates and generic host-binding contracts exist,
but no executor contract applies code changes or returns implementation
evidence.

Likely files: `docs/adapters.md`, `examples/adapter-template`,
`examples/adapter-contract-runner`, `codex_cadence/model.py`,
`codex_cadence/cli.py`, tests.

Validation: fake executor success, failure, timeout, invalid evidence, and
disallowed-path cases.

Codex can implement the generic contract directly. Named host adapters require
operator approval.

### Policy, audit, and stop controls

Goal: make unattended loop behavior bounded, inspectable, and interruptible.

Current evidence: Cadence state, brakes, epochs, and handoff records exist, but
there is no single policy file or append-only audit log for loop decisions and
executor actions.

Likely files: `codex_cadence/model.py`, `codex_cadence/store.py`,
`codex_cadence/cli.py`, tests, docs.

Validation: policy denial tests, audit replay tests, active-loop stop tests,
and corrupted audit record tests.

Codex can implement direct local policy and audit controls. Destructive cleanup
or default-autonomous permissions require operator approval.

## Medium-Term Goals

### Minimal Git/PR automation

Goal: after an approved successful task, prepare branch, commit, push, PR body,
and PR creation evidence through dry-run-first commands.

Current evidence: PR body preflight and readiness checks exist, but Cadence
does not create branches, commits, pushes, or pull requests.

Likely files: `codex_cadence/cli.py`, `codex_cadence/pr_readiness.py`,
scripts, tests, docs.

Validation: mocked `gh` fixtures for PR creation, failed push, pending CI,
failing CI, passing CI, stale state, and dry-run packets.

Codex can implement dry-run packets directly. Live PR creation and push
behavior require operator approval.

### CI and review feedback as candidate input

Goal: convert failing checks and unresolved actionable review comments into
bounded candidates for the next loop tick.

Current evidence: candidate discovery can ingest saved review findings and
saved review-thread files, while PR readiness reports blockers. Live sync and
resolution tracking are not implemented.

Likely files: `codex_cadence/candidates.py`,
`codex_cadence/pr_readiness.py`, `codex_cadence/repo_state.py`, tests.

Validation: fixtures where a failing check becomes a fix candidate, unresolved
review feedback blocks merge readiness, resolved feedback is ignored, and
outdated feedback is ignored.

Codex can implement direct local ingestion. Live GitHub synchronization
requires operator approval before credentials or workflow permissions change.

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
- No PyPI publication.
- No replacement for generic secret scanning or external security review.

## Open Questions

- What repository should be used for the first controlled demo loop?
- What executor evidence schema is sufficient for 50% confidence?
- Which commands and paths should be allowed by the default local policy?
- How much PR automation can be enabled before human approval becomes
  mandatory?
- Which host should receive the first named adapter after the generic contract
  proves stable?
- What identity model is strong enough for approvals without making local use
  too heavy?
