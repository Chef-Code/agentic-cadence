# Agent-Team Orchestration Vision

Status: living document
Last updated: 2026-06-19
Baseline: released 0.1.3 plus current local governance, Git/PR planning, resume, review-response, role-readiness, and local work ownership evidence

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

## Product UI End State

The long-term product shape is a GitHub-native control room for agent teams.
The UI should not replace GitHub and should not become a general multi-agent
chat surface. It should show one human operator how a goal is moving through a
shared repository, which agents are involved, which role each agent is playing,
what evidence exists, and which actions still require human approval.

In that target shape, a user connects a repository and defines a goal. Cadence
then helps coordinate provider-specific agents such as Codex, Claude, Gemini,
or other future executors through role-bound policies instead of treating them
as interchangeable free agents. The same provider might serve different roles
in different runs, but each run should make the role, authority, and work
boundary explicit before any write or execution happens.

The UI should make these surfaces first-class:

- Goal Control Room: the active goal, current stage, last accepted evidence,
  next recommended action, and any blockers.
- Agent Roster: connected agent providers, role assignments or role claims,
  readiness state, permissions, and separation rules.
- Work Board: planned slices, in-progress branches, ownership claims, handoffs,
  blocked items, and completed tasks.
- PR And Review Console: branches, pull requests, CI state, review threads,
  requested changes, bot findings, and merge-readiness evidence.
- Approval Inbox: exact actions that require a human gate, including executor
  invocation, Git/PR writes, review responses, thread resolution, merge
  decisions, retries, and continuation.
- Evidence Timeline: immutable packets, audit replay, checksums, approvals,
  closeouts, and stage outcomes in chronological order.
- Policy Panel: role policies, command policies, branch policies, stop/brake
  controls, repo boundaries, and side-effect permissions.

The user should be able to answer, at a glance:

1. What goal is the team pursuing?
2. Which agent is responsible for each piece of work?
3. What did each agent do, and where is the evidence?
4. Which actions are blocked, waiting for review, or waiting for approval?
5. What is safe to continue, retry, split, hand off, or merge?

This is an end-state product vision, not a current capability claim. Today the
backend is intentionally building the evidence packets, gates, role boundaries,
and side-effect controls that a future UI would need before it can coordinate
agents safely.

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

## End-State Demo Story

A strong future demo should feel like watching a disciplined software team work
inside one repository. The operator creates a goal and Cadence decomposes it
into bounded slices. A Planning role proposes the next slice, a Builder role
claims the branch, a Reviewer role inspects the pull request, a QA role verifies
tests and acceptance evidence, and a Documentation role updates the living
repository context. Cadence tracks every handoff, packet, approval, blocker,
and pull request state so the operator can see progress without reading every
agent transcript.

The important promise is not that agents are busy. The promise is that a team of
agents can work toward one goal while the human can see the source of truth,
understand who is doing what, and approve only the actions that should cross a
trust boundary.

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

The current local ownership registry is evidence, not assignment authority.
Tasks 13-16 from `docs/roadmaps/2026-06-05-tasks-13-17-roadmap.md` completed
local ownership claim/closeout, ownership-bound execution-start,
ownership-bound resume-continuation, and role/readiness evidence while keeping
role assignment, agent-pool scheduling, GitHub issue assignment, distributed
lock, merge, release, and package-publication behavior out of scope. Task 17 is
the remaining pre-invocation slice for real-executor readiness evidence.

The current `role-readiness` command keeps that same boundary: it can verify a
local `role-policy.v1`, scoped ownership role labels, and saved review-thread
separation evidence, but it cannot assign a role, schedule an agent, call
GitHub, invoke paid review, or mutate PR state.
