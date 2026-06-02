# Autonomous Loop Readiness

Status: living document
Last updated: 2026-06-02
Baseline: released 0.1.3 plus unreleased audit-replay, policy/stop-control, executor closeout, git-pr-plan, and controlled executor fixture current tree
Current unattended-operation confidence: 10%

This document answers how close Agentic Cadence is to the "press start and
build continuously" experience. The first usable path is a governed
single-agent loop. The larger direction is GitHub-native orchestration for
multiple cooperating agents.

## What The Magic Button Means

The Phase 1 target loop is:

```text
inspect repo
-> choose next bounded task
-> prepare implementation task
-> run executor under policy
-> validate changes
-> commit/push/open or update PR
-> trigger reviews
-> ingest CI and review feedback
-> create follow-up tasks
-> monitor context pressure and policy limits
-> hand off when needed
-> resume in a fresh session
-> continue safely
```

The mature target is an orchestrator loop:

```text
roadmap/backlog
-> task decomposition/election
-> role-aware agent assignment
-> branch/PR/CI/review
-> docs/handoff/merge decision
-> next governed task
```

The loop must also stop safely when policy, CI, review, dirty worktree,
context pressure, operator brake, or confidence conditions require it.

## What Works Today

If Agentic Cadence is pointed at a real repository today, the CLI and local
runtime can do these things end-to-end:

- initialize and manage local runtime state;
- report Cadence state and compatibility brake state;
- snapshot local git state;
- detect local dirty worktree, branch, head SHA, detached head, and supplied CI
  status;
- discover candidate work from local deterministic inputs;
- elect conservative next candidates when allowed;
- run one read-only `loop-tick` that snapshots local repo state,
  discovers/elects candidates, checks Cadence state, and emits a next-action
  packet;
- emit a generic executor task packet for operator approval without starting an
  executor;
- run an explicit test/example-only controlled executor fixture command that
  validates the task packet and command policy before launching a fake external
  executor component, then validates the fixture's result evidence;
- apply an initial local loop policy file to bound emitted executor task paths,
  command policy, required checks, runtime, and stop conditions while retaining
  built-in safety stops;
- append compact audit records for root-backed loop decisions, controlled
  executor fixture invocation, and executor result validation, including
  task/result checksums for result validation;
- replay local audit history with a read-only `audit-replay` command that
  validates `cadence-audit.v1` JSONL shape, supported events, line counts, and
  checksum syntax;
- validate that executor task packets are anchored to the embedded local repo
  snapshot by requiring matching repo name, absolute cwd/path, branch, head,
  clean worktree, built-in safety stops, absolute expected evidence path, and
  non-low repo confidence;
- validate local generic executor result evidence against a task packet,
  including elapsed runtime bounds, expected evidence-path binding, command
  allow/deny policy, disabled commit/push/PR/merge/release/package-publication
  permissions, and active brake stop handling;
- close out an active epoch from local executor task/result/snapshot-after
  packets, recording successful task evidence while other epoch tasks remain,
  completing terminal successful epochs, failing failed/blocked/stopped or
  policy-violating evidence with stable reason codes, and emitting a local next
  decision;
- generate a dry-run Git/PR transition plan from validated executor evidence,
  with local branch/head/base checks, explicit materialized-change evidence,
  PR body preflight, operator-confirmation requirements, and no Git or GitHub
  side effects;
- size tasks and enforce pickup policy;
- start, check, complete, or fail bounded epochs;
- prepare a signed handoff packet and clean-square evidence;
- approve, claim, complete, or fail handoffs;
- evaluate saved PR JSON and saved PR body/template files for readiness,
  including `saved_input`, `stale`, and caller-asserted `live_like` evidence.

These capabilities are still single-agent Phase 1 primitives, but they are not
throwaway work. They are the same primitives a future orchestrator needs for
task ownership, bounded effort, review separation, handoff contracts, and
merge decisions.

This repository also includes validation and review guardrails that prove the
baseline is testable:

- package, protocol, first-run, and generic adapter contract checks in CI;
- an elected Codex Review GitHub Actions workflow for this repository.

Those workflow guardrails are not installed or triggered by the Agentic Cadence
CLI when it is pointed at an arbitrary target repository. External repositories
must provide equivalent workflow wiring before those checks are available.

## What Does Not Work Today

Agentic Cadence cannot currently:

- implement code changes by itself;
- choose a task and hand it to a real executor;
- decompose work across an agent pool;
- assign role-specific agents such as Planning, Architecture, Builder,
  Reviewer, QA, Documentation, Release, or Handoff agents;
- enforce that the reviewer is separate from the builder;
- create a branch;
- commit changes;
- push to a remote;
- open or update a pull request;
- fetch live GitHub PR, check, comment, or review-thread state from the CLI;
- resolve review feedback;
- trigger follow-up implementation from live CI or review failures;
- infer context pressure without explicit host input;
- launch a fresh coding-agent session;
- verify and resume a full new-session loop automatically;
- run continuously without an external operator or orchestrator;
- merge, release, or publish packages.

## Implemented Versus Planned

| Loop capability | Current status | Evidence |
| --- | --- | --- |
| Local repo snapshot | Implemented, local only | `codex_cadence/repo_state.py` |
| Candidate discovery | Implemented, read-only | `codex_cadence/candidates.py` |
| Task sizing | Implemented | `codex_cadence/model.py` |
| Epoch governance | Implemented | `codex_cadence/epochs.py`, `closeout-executor-result` |
| Handoff lifecycle | Implemented | `codex_cadence/handoff_loop.py`, `codex_cadence/cli.py` |
| PR body/readiness checks | Implemented from saved inputs | `codex_cadence/pr_readiness.py` |
| Elected Codex Review workflow | Implemented in GitHub Actions | `.github/workflows/codex-review.yml` |
| Single loop tick | Partial, read-only | `loop-tick` emits next action and stops before execution |
| Local policy/audit controls | Partial | `loop-tick --policy-file`, task command policy, active brake stop handling, `<root>/audit/events.jsonl`, and read-only `audit-replay`; no branch policy, hash chain, or authenticated approval identity |
| Agent-team orchestration | Not built | No agent pool, role registry, or GitHub-native assignment workflow |
| Continuous loop runner | Not built | Planned slice |
| Executor adapter contract | Partial generic contract | Task/result packet validation and a fake controlled fixture runner exist, including snapshot trust-anchor checks, but no real executor or named host adapter |
| Autonomous implementation | Not built | Requires real executor integration |
| Live GitHub sync | Not built | Planned slice |
| Git/PR transition planning | Partial, dry-run only | `git-pr-plan` emits reviewable branch/commit/PR plans without side effects |
| Branch/commit/push/PR creation | Not built | Live creation remains future work |
| Review response loop | Partial local ingestion only | Saved review files can become candidates |
| Context-pressure monitor | Partial explicit signal only | Host/session signal required |
| New-session launch/resume | Partial handoff packets only | External orchestration required |

## Can It Run The Full Loop Today?

No.

It can inspect and suggest. It can run a read-only loop tick that produces a
structured next action. It can emit a generic executor task packet for operator
approval, validate the packet's local snapshot trust anchor, run a controlled
fake executor fixture from an explicit command template for tests/examples,
validate local executor result evidence, close out the active epoch, and produce a dry-run Git/PR transition plan for
separate review. It can govern handoff and continuation decisions. It can
evaluate saved PR evidence. It cannot perform the core build loop by
itself, and it cannot yet coordinate a team of role-specific agents.

The current loop stops after:

```text
inspect repo -> discover/elect candidate -> emit blocked/no_candidates/approval_required/requires_executor_contract/approve_executor_task
```

It can also emit `policy_denied` when a supplied local loop policy blocks the
requested executor-task bounds.

At `requires_executor_contract`, a human or external agent still has to request
an executor task packet. At `approve_executor_task`, a human or external agent
still has to approve any real execution. The controlled fixture path can prove
policy, timeout, audit, and result-evidence behavior with fake local evidence,
and local closeout can record task completion or terminally complete/fail the
active epoch from that evidence, but it does not implement product changes. The
dry-run `git-pr-plan` handoff remains
review-only. Real code changes, branch policy, operator-approved Git/PR
materialization, live commit, push, PR creation or update, review feedback
fetching, and new-session launch remain external or future approved slices. At
`policy_denied`, an operator must adjust the task bounds or policy before
execution can be considered. Audit history is now locally inspectable through
`audit-replay`, but clean replay evidence is not approval to execute work and
does not provide tamper evidence.

## What Would Break First

The first hard stop in a real unattended run is still governed execution.
Cadence can emit a bounded executor task packet, reject malformed, dirty,
low-confidence, relative-path, or mismatched snapshot anchors, run a fake
controlled fixture, and close local executor evidence into an epoch decision.
It still does not invoke a real executor or apply code changes.

The next likely failures are:

1. no branch policy exists yet, so executor output is not governed against
   protected base/target branch rules;
2. no live branch/commit/push/PR workflow exists; the current Git/PR increment
   is only a dry-run planning packet for operator or future role review;
3. missing live synchronization. Repo snapshots are local git snapshots, and PR
   readiness reads saved input files. Cadence labels `local_only`,
   `saved_input`, `stale`, and caller-asserted `live_like` evidence, but it
   still does not fetch or reconcile live PR, review, or CI state;
4. review comments and failing checks are not automatically synchronized back
   into the candidate loop;
5. context pressure is only known when a host explicitly reports it;
6. no agent-role identity or review-separation model exists, so Cadence cannot
   prove that a Builder Agent and Reviewer Agent are distinct actors;
7. CLI root-using commands now block unignored repo-local runtime roots unless
   an operator explicitly allows them; residual risk remains for manual
   filesystem changes or future adapters that bypass the CLI guard.

## Minimum Realistic Demo Loop

The minimum credible demo should not claim full autonomy. It should show
Cadence as the loop controller:

```text
snapshot repo
-> discover/elect next task
-> require approval
-> emit implementation packet
-> external executor or Codex session implements
-> run configured validation
-> record result
-> complete or fail epoch
-> prepare PR/readiness/handoff decision
```

The implementation step can remain external for the first demo. The important
progress is that Cadence owns the loop state, policy gates, evidence records,
and stop conditions.

## 50% Confidence Criteria

Agentic Cadence should not be considered near 50% confidence until the project
has evidence for:

- one bounded loop tick command;
- generic executor evidence contract;
- policy file with command/path/runtime/check limits;
- append-only audit log;
- stop/brake behavior during active loop work;
- dry-run-first branch and PR packet generation;
- CI and review feedback becoming candidate input;
- fixture tests for success, failure, stale state, dirty state, and stop state.

Even at 50%, the expected mode is low-risk controlled operation with strong
constraints and pre-approved unattended ticks, not production-autonomous work.

## Current Confidence Rating

Current rating: 10%.

Reasoning:

- Safety and governance primitives are real.
- A read-only `loop-tick` now stitches snapshot, candidate election, Cadence
  state, and next-action reporting into one packet.
- The generic executor task/result contract is now explicit and testable, but
  it is only wired to a fake controlled executor fixture, not a real executor.
- Executor task packets now fail closed on malformed local snapshots, missing
  repo identity, relative or unnormalizable cwd/path anchors, repo/cwd/branch/head
  mismatches, dirty worktrees, and low-confidence repo state.
- Initial local policy/audit controls can bound emitted executor task packets,
  record loop/result-validation decisions, reject commands outside task
  command policy, stop non-`stopped` result completion after the brake changes,
  run a controlled fixture, and replay local audit history, but they do not
  govern a real executor or provide tamper evidence.
- The handoff and task/epoch model is useful.
- Candidate discovery is deterministic and conservative.
- Adapter contracts are tested at the public CLI boundary.
- Readiness packets now distinguish `local_only`, `saved_input`, `stale`, and
  caller-asserted `live_like` evidence.
- The real implementation executor, epoch execution flow, PR automation, live
  review sync, continuous loop runner, and resume orchestration are not built.

The rating should stay low until a controlled loop can make a real change in a
fixture repo, validate it, record evidence, and stop cleanly.

## Update Rule

Update this document whenever a PR changes what the loop can actually do, where
it stops, what fails first, or the confidence rating.
