# Agent-Team Orchestration Vision

Status: living document
Last updated: 2026-05-31
Baseline: released 0.1.3 tree

Agentic Cadence began as a workflow system that helps one coding agent continue
building a repository in a disciplined way. That purpose remains valid. Task
election, bounded work, epochs, validation gates, pull requests, reviews,
context handoffs, stop/continue decisions, and living documentation are still
the foundation.

The larger product direction is GitHub-native orchestration for autonomous
software teams.

## Product Statement

Agentic Cadence should become a governance and orchestration layer for
autonomous software teams. The target system coordinates agents through
GitHub-native workflows so they can plan, build, review, validate, document,
hand off, and prepare merge decisions without duplicating effort or corrupting
repository state.

The system can start with one agent, but it should be designed to scale toward
multiple cooperating agents with explicit roles, responsibilities,
permissions, and handoff contracts.

## GitHub As The Coordination Model

Cadence should use the coordination model that already works for disciplined
software teams:

- issues or recorded decisions define work;
- assignees or agent claims establish ownership;
- branches isolate implementation;
- pull requests expose proposed changes;
- reviews provide quality gates;
- CI validates behavior;
- documentation keeps the repository aligned;
- merges advance the stable main branch.

Cadence should not invent a parallel project-management universe when GitHub
already provides the durable coordination surface. Its job is to govern agent
behavior on top of that surface.

## Phase 1 And Target Shape

The Phase 1 target shape remains useful, even though the current implementation
only ships the local governance primitives needed to reach it:

```text
Task
-> Single Agent
-> Validation
-> Pull Request
-> Review
-> Merge
-> Next Task
```

The target shape is broader:

```text
Roadmap / Backlog
-> Agentic Cadence Orchestrator
-> Task Decomposition / Election
-> Agent Pool
   -> Planning Agent
   -> Architecture Agent
   -> Builder Agent
   -> Reviewer Agent
   -> QA Agent
   -> Documentation Agent
   -> Release Agent
   -> Handoff Agent
-> Branch / PR / CI / Review
-> Docs / Handoff / Merge Decision
-> Next Governed Task
```

The target is not one endless agent loop. The target is a governed team loop
where each agent works inside a bounded role and Cadence decides whether to
continue, stop, retry, split, review, or hand off.

## Possible Agent Roles

These roles are future design guides, not current implementation claims:

- Planning Agent: reviews the roadmap, backlog, issues, and recent evidence to
  select or decompose the next bounded work item.
- Architecture Agent: checks whether proposed work fits the system design,
  governance model, and existing contracts.
- Builder Agent: implements one bounded task on one branch.
- Reviewer Agent: reviews a pull request and should not be the same agent that
  built the change when separation is possible.
- QA Agent: runs tests, investigates failures, and validates acceptance
  criteria.
- Documentation Agent: updates living docs when behavior, architecture,
  readiness, or roadmap direction changes.
- Release Agent: evaluates whether a pull request or release candidate is
  ready to merge, tag, or publish under policy.
- Handoff Agent: packages state for the next session or next role before
  context pressure or role boundaries create risk.

## Core Invariants

Future team orchestration must preserve these invariants:

1. No agent edits `main` directly.
2. Every unit of work maps to an issue, task, or clearly recorded decision.
3. Every implementation happens on a branch.
4. Every meaningful change produces a pull request.
5. Validation runs before merge.
6. Review is separate from implementation when possible.
7. Context handoff is explicit, not accidental.
8. Documentation evolves with the repository.
9. Small, bounded slices are preferred over large ambiguous work.
10. The orchestrator decides whether to continue, stop, retry, split, review,
    or hand off.

## Handoff As Coordination

Handoff is not only a response to one agent running out of context. It is a
coordination primitive:

- a Planning Agent hands a decomposed task to a Builder Agent;
- a Builder Agent hands a pull request to a Reviewer Agent;
- a QA Agent hands test failures back to a Builder Agent;
- a Documentation Agent records what changed after merge;
- a long-running session hands off before context pressure causes mistakes.

Every handoff should carry enough state for the next role or session to verify
the work boundary, repository state, validation evidence, and next action
without relying on transcript memory.

## Risks Cadence Exists To Reduce

The main risk is not that autonomous coding agents cannot write code. The main
risk is ungoverned momentum:

- duplicate work;
- conflicting changes;
- stale documentation;
- hallucinated assumptions;
- context overload;
- huge unreviewable branches;
- silent drift from the roadmap;
- merges without enough evidence.

Cadence should measure every future primitive by how well it prevents those
failures while preserving useful momentum.

## Immediate Documentation Implication

Current documentation should describe the single-agent flow as Phase 1 and the
team flow as the long-term direction. Future roadmap work should avoid assuming
only one agent exists, even when the first implementation slice still runs one
bounded agent at a time.
