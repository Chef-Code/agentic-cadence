# Autonomous Loop Readiness

Status: living document
Last updated: 2026-05-30
Baseline: released 0.1.3 tree
Current unattended-operation confidence: 10%

This document answers how close Agentic Cadence is to the "press start and
build continuously" experience.

## What The Magic Button Means

The target loop is:

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
- size tasks and enforce pickup policy;
- start, check, complete, or fail bounded epochs;
- prepare a signed handoff packet and clean-square evidence;
- approve, claim, complete, or fail handoffs;
- evaluate saved PR JSON and saved PR body/template files for readiness,
  including freshness labels for saved, stale, and caller-asserted live-like
  evidence.

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
- choose a task and hand it to a real executor through a formal implementation
  contract;
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
| Epoch governance | Implemented | `codex_cadence/epochs.py` |
| Handoff lifecycle | Implemented | `codex_cadence/handoff_loop.py`, `codex_cadence/cli.py` |
| PR body/readiness checks | Implemented from saved inputs | `codex_cadence/pr_readiness.py` |
| Elected Codex Review workflow | Implemented in GitHub Actions | `.github/workflows/codex-review.yml` |
| Continuous loop runner | Not built | Planned slice |
| Executor adapter contract | Not built | Planned slice |
| Autonomous implementation | Not built | Requires executor contract |
| Live GitHub sync | Not built | Planned slice |
| Branch/commit/push/PR creation | Not built | Planned slice |
| Review response loop | Partial local ingestion only | Saved review files can become candidates |
| Context-pressure monitor | Partial explicit signal only | Host/session signal required |
| New-session launch/resume | Partial handoff packets only | External orchestration required |

## Can It Run The Full Loop Today?

No.

It can inspect and suggest. It can govern handoff and continuation decisions.
It can evaluate saved PR evidence. It cannot perform the core build loop by
itself.

The current loop stops after:

```text
inspect repo -> discover/elect candidate -> prepare governed next action
```

At that point, a human or external agent still has to implement code changes,
run checks, commit, push, open or update a PR, fetch review feedback, and start
the next session after a handoff.

## What Would Break First

The first likely failure in a real unattended run is still missing live
synchronization. Repo snapshots are local git snapshots, and PR readiness reads
saved input files. Cadence now labels `local_only`, `saved_input`, `stale`, and
caller-asserted `live_like` evidence, but it still does not fetch or reconcile
live PR, review, or CI state. Snapshot validation rejects missing or malformed
local readiness evidence, and stale saved PR evidence waits before acting on
blockers, but those checks only prevent overtrust in local files.

The next likely failures are:

1. no executor exists to safely implement the elected task;
2. no branch/commit/push/PR workflow exists;
3. review comments and failing checks are not automatically synchronized back
   into the candidate loop;
4. context pressure is only known when a host explicitly reports it;
5. CLI root-using commands now block unignored repo-local runtime roots unless
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
- The handoff and task/epoch model is useful.
- Candidate discovery is deterministic and conservative.
- Adapter contracts are tested at the public CLI boundary.
- Readiness packets now distinguish local-only, saved, stale, and
  caller-asserted live-like evidence.
- The implementation executor, PR automation, live review sync, continuous
  loop runner, and resume orchestration are not built.

The rating should stay low until a controlled loop can make a real change in a
fixture repo, validate it, record evidence, and stop cleanly.

## Update Rule

Update this document whenever a PR changes what the loop can actually do, where
it stops, what fails first, or the confidence rating.
