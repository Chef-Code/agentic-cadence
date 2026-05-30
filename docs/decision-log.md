# Decision Log

Status: living document
Last updated: 2026-05-30

This document records major architecture and governance decisions. Update it
when a meaningful implementation or policy choice is made, when an assumption
is invalidated, or when an open question is resolved.

## Entry Template

```markdown
## YYYY-MM-DD - Decision title

Decision:
- The choice made.

Why:
- Evidence and reasoning.

Alternatives considered:
- Alternative and why it was rejected or deferred.

Consequences:
- What this enables or constrains.

Open questions:
- Remaining unknowns.
```

## 2026-05-30 - Keep executor integration behind a generic task/result contract

Decision:
- Add a generic executor task packet and result evidence validator before
  integrating any real executor or named host adapter.
- `loop-tick --emit-executor-task` may emit a bounded task packet for operator
  approval, but it must still set `executor_started: false`.
- `validate-executor-result` validates local result evidence without running an
  executor or mutating repo state.

Why:
- The project needs a formal boundary for implementation work before Cadence can
  safely start epochs, invoke executors, or record execution outcomes.
- Keeping the contract generic avoids smuggling in named host adapter support
  before there is explicit operator approval and verifier evidence.

Alternatives considered:
- Build a real executor adapter immediately. Deferred because branch, commit,
  PR, audit, and rollback controls are not ready.
- Let `loop-tick` call an external command directly. Rejected for this slice
  because the evidence shape and approval boundary need to be stable first.

Consequences:
- Cadence can now produce and validate the packets needed for a controlled
  external execution demo.
- The loop still cannot implement code by itself, and confidence remains low
  until a real executor flow is tested against a fixture repo.

Open questions:
- Which external executor should be integrated first, and under what approval
  policy?
- Should executor result validation become part of epoch completion or a
  separate audit record first?

## 2026-05-30 - Split single-tick orchestration into a read-only first phase

Decision:
- Add `loop-tick` as a Phase 1 read-only loop-controller command before adding
  executor, epoch mutation, validation, or PR side effects.
- The command emits `blocked`, `no_candidates`, `approval_required`, or
  `requires_executor_contract` and records that executor, epoch, and PR actions
  were not started.

Why:
- The project needs evidence that snapshot, candidate election, Cadence state,
  and next-action reporting can be stitched together before execution is added.
- Keeping Phase 1 read-only preserves the current safety boundary while making
  the next missing contract explicit.

Alternatives considered:
- Start epochs and call an executor in the first loop tick. Deferred because
  the executor evidence contract is not defined yet.
- Build continuous mode first. Rejected because repeated execution should
  compose a proven bounded tick.

Consequences:
- Cadence can now produce a single governed next-action packet from local repo
  state.
- Confidence remains low because no code is implemented, validated, committed,
  pushed, reviewed, or resumed by the loop.

Open questions:
- What exact executor evidence schema should satisfy
  `requires_executor_contract`?
- Should the next phase start an empty administrative epoch or wait until the
  executor contract exists?

## 2026-05-30 - Label evidence freshness before adding live sync

Decision:
- Add packet-level `readiness_evidence` labels before implementing any live
  GitHub synchronization.
- Use `local_only` for repo snapshots, `saved_input` for saved PR JSON,
  `stale` for saved PR JSON that exceeds an explicit age limit, and
  `live_like` only when a caller asserts live-origin evidence to the evaluator.
- Apply stale and future-timestamp gating only to saved PR JSON. Do not apply
  saved-file age policy to caller-asserted `live_like` evaluator inputs.
- Enforce local snapshot readiness evidence during snapshot validation.

Why:
- The current system evaluates local git snapshots and saved PR JSON. Without
  labels, downstream automation can overtrust local-only or stale state.
- Freshness labels make current limitations machine-readable while preserving
  deterministic, local-only behavior.
- Saved-file age policy should not create false stale labels for evaluator
  inputs that are explicitly asserted to be live-like by their caller.

Alternatives considered:
- Add live GitHub fetching first. Deferred because live sync needs a separate
  credential, rate-limit, and failure policy.
- Treat all saved PR JSON as stale by default. Rejected because existing
  deterministic local workflows would become noisy without an operator-supplied
  age policy.

Consequences:
- Loop automation can distinguish current local-only capability from later
  live synchronization.
- Operators can opt into stale saved-PR detection with
  `--max-pr-json-age-minutes`.
- Negative saved-PR age limits are rejected at the CLI boundary.
- Stale or future-dated saved PR evidence waits and recommends refresh before
  acting on PR blockers.
- `live_like` remains caller-asserted until Cadence owns the live fetch.

Open questions:
- What age threshold should controlled-demo policy require for saved PR JSON?
- Should future live sync preserve the same label schema or introduce signed
  source provenance?

## 2026-05-29 - Fail closed on unignored repo-local runtime roots

Decision:
- Commands that use the Cadence runtime root reject an unignored root inside
  the target git repo unless the operator passes `--allow-repo-local-root`.
- Gitignored repo-local runtime roots remain allowed.

Why:
- Cadence stores runtime state on disk, and candidate discovery treats dirty
  worktree state as repo evidence.
- A repo-local runtime root that is not ignored can create the dirty state that
  Cadence then reports back to the operator.

Alternatives considered:
- Allow all repo-local roots and document the risk. Rejected because the
  failure mode is easy to trigger and undermines state awareness.
- Ban all repo-local roots. Rejected because ignored repo-local state can be a
  valid local operator workflow, and explicit overrides are useful for tests or
  controlled environments.

Consequences:
- Runtime roots outside project repositories remain the preferred default.
- Operators can still make an explicit repo-local choice with
  `--allow-repo-local-root`.
- Future adapters must preserve this policy or document an equivalent guard.

Open questions:
- Should adapter conformance tests eventually include runtime-root policy
  checks outside the CLI entry point?

## 2026-05-29 - Treat documentation as part of the operating model

Decision:
- Maintain `roadmap.md`, `autonomous-loop-readiness.md`,
  `implementation-slices.md`, `progress-log.md`, and `decision-log.md` as
  living documents.
- Meaningful implementation PRs must evaluate whether these documents need
  updates.

Why:
- Agentic Cadence is intended to govern long-running agentic work. Future
  sessions and contributors need an accurate view of current capability,
  confidence, risks, and decisions.
- Readiness and confidence must be grounded in evidence, not intent.

Alternatives considered:
- Keep roadmap material only in PR descriptions or chat transcripts. Rejected
  because those are harder for future sessions to discover and verify.
- Keep a single roadmap document only. Rejected because readiness, slice
  tracking, progress evidence, and decisions have different audiences and
  update rhythms.

Consequences:
- Documentation updates become part of normal delivery for meaningful changes.
- Confidence ratings may go up or down when evidence changes.
- Stale documentation is treated as a project quality problem.

Open questions:
- Should CI eventually enforce a documentation-check prompt or checklist for
  changes that touch loop behavior?

## 2026-05-29 - Use 10% as the current unattended-operation confidence rating

Decision:
- Record current unattended continuous-operation confidence as 10%.

Why:
- The repo has real governance primitives: handoffs, task sizing, epochs,
  candidate discovery, PR readiness from saved inputs, release dry-run, and
  adapter contracts.
- At that point, the repo did not have the central autonomous loop: no executor
  contract, no implementation runner, no live PR/review sync, no
  branch/commit/push/PR creation, no automatic context-pressure sensing, and no
  new-session launch or resume orchestration.

Alternatives considered:
- Higher rating because many safety primitives exist. Rejected because the
  system cannot currently build, PR, review, hand off, resume, and continue by
  itself.
- Lower rating because full autonomy is absent. Rejected because the existing
  protocol and safety substrate is useful and tested.

Consequences:
- Progress toward autonomy should be measured from a conservative baseline.
- The next target is a constrained 50% confidence loop, not full autonomous
  production operation.

Open questions:
- What exact demo evidence should be required before moving from 10% to a
  higher confidence rating?

## 2026-05-29 - Target a bounded single-tick loop before continuous mode

Decision:
- Prioritize a single-tick loop orchestrator before any always-on continuous
  runner.

Why:
- Existing capabilities are one-shot commands. A single bounded tick is the
  smallest safe bridge between advisor behavior and loop-controller behavior.
- Repeated autonomous execution without a proven bounded tick would make
  failures harder to understand and recover from.

Alternatives considered:
- Build a daemon or scheduler first. Rejected because repeated execution should
  compose a proven safe tick, not hide missing control flow.
- Build named host adapters first. Rejected because the generic executor and
  loop contracts are not stable yet.

Consequences:
- The first demo should show `snapshot -> discover -> approve -> execute via
  contract -> validate -> record -> stop/next decision`.
- Continuous build mode remains deferred until one tick is reliable.

Open questions:
- Should the first tick command call an executor subprocess directly or only
  emit task packets for an external orchestrator?

## 2026-05-29 - Keep executor integration generic before named host adapters

Decision:
- Define a generic executor adapter contract before adding named host adapters.

Why:
- The repo already follows a generic adapter-contract pattern for host signals.
- Named adapter claims would be premature before the execution evidence shape,
  policy limits, and validation behavior are stable.

Alternatives considered:
- Build a Codex-specific executor first. Deferred because it could overfit the
  core protocol to one host.
- Build Claude or Gemini adapters first. Rejected for the current baseline
  because docs explicitly do not claim shipped support for those hosts.

Consequences:
- The next execution work should focus on packet schemas, fake executors,
  validation, and conformance tests.
- Named host support requires explicit operator approval.

Open questions:
- Which fields are required in executor evidence for the first controlled demo?
