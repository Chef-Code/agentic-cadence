# Decision Log

Status: living document
Last updated: 2026-05-29

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
- The repo does not have the central autonomous loop: no executor contract, no
  implementation runner, no live PR/review sync, no branch/commit/push/PR
  creation, no automatic context-pressure sensing, and no new-session launch
  or resume orchestration.

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
