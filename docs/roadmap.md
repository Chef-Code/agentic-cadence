# Agentic Cadence Technical Roadmap

This roadmap documents where Agentic Cadence is now, the edges that are still intentionally exposed, and the direction for the next release slices.

## North Star

Agentic Cadence should become an agent-neutral handoff and governance layer for long-running coding-agent work. A host such as Codex, Claude, Gemini, or a future coding agent should be able to stop cleanly, preserve enough context for a fresh session, respect repository and review gates, and continue only when the protocol says continuation is allowed.

The protocol should stay small and inspectable. Host adapters can render different user experiences, but they should share the same core concepts: Cadence state, handoffs, clean-square evidence, task sizing, approval gates, PR readiness, and release guardrails.

## Current State

The current tree builds on the released 0.1.x line, with additional unreleased release-readiness and adapter-contract work on `main`. It includes:

- a packaged `agentic-cadence` CLI with Codex-compatible command aliases;
- local Cadence state with `PLAY_ON`, `HUDDLE`, and `TIMEOUT`;
- handoff creation, preparation, approval, claim, completion, and failure flows;
- repo snapshots and clean-square validation for old-session shutdown;
- task sizing, epoch governance, and conservative pickup gates;
- read-only candidate discovery from local repo signals, saved review findings, saved GitHub review-thread files, and business memory;
- deterministic PR body preflight and PR readiness checks from saved local inputs;
- release-readiness docs, current-tree audit, history audit, pinned GitHub Actions guardrails, a local `release-dry-run` helper, and a manual GitHub Actions release dry-run workflow for release-note generation and tag verification;
- an executable adapter smoke contract in `examples/adapter-smoke/run.py` that proves a host adapter can drive the public CLI and preserve returned JSON packets.
- a copyable adapter template in `examples/adapter-template` that shows the public CLI boundary, explicit runtime root, packet preservation, and host-specific pickup hooks.
- an adapter-local host/session signal contract in `examples/adapter-template`
  that maps explicit host context-pressure signals to existing
  `prepare-handoff` arguments without adding a core object model.
- generic host-signal fixtures for the adapter template that exercise
  `context_pressure`, `operator_stop`, and no-signal behavior without claiming
  to ship a real Claude or Gemini adapter.
- a generic host-signal smoke example that runs those fixtures through the
  copyable adapter template and public CLI before any real host adapter exists.
- a host-binding mapping example that shows future adapter authors how to map
  host events into those generic signal fields and existing CLI arguments
  without shipping unsupported host-specific adapters.
- a generic shell host-binding example with bundled fixture smoke mode plus
  file-backed `--host-event-file` and stdin-backed `--host-event-stdin` paths
  that read one host-event JSON payload, map it into the adapter-local signal
  shape, and exercise the copyable adapter template without claiming to be a
  real host adapter.
- a generic shell host-binding `--replay-contract` mode that compares the same
  bundled host-event payloads across fixture, file-backed, and stdin-backed
  paths.
- a generic host/shell `--parity-contract` mode that compares the
  adapter-template host-signal fixtures against the generic shell replay
  contract.
- a host-signal contract schema helper that validates the generic host-signal
  fixtures and shell host-event payloads before subprocess replay/parity runs.

## Known Edges

These are the important boundaries that are not solved yet:

- No Claude or Gemini adapter is shipped. The project has an adapter boundary and smoke contract, not full host integrations.
- There is no automatic real-host context-pressure integration. The adapter
  template defines the minimal adapter-local signal shape, but
  `prepare-handoff --guardrail context` still requires explicit input from the
  caller or host binding.
- Runtime state is local filesystem state. There is no shared remote backend for teams or cloud agents.
- Local locks protect local transitions, but there is no distributed lock model for multiple machines.
- Claimer, approver, and operator values are records, not authenticated identities with role enforcement.
- Policy is conservative but not yet an organization-level policy engine.
- Review integration is deterministic and local. Candidate discovery can ingest saved review findings and saved GitHub review-thread files, while `pr-readiness` reads saved PR data; Cadence does not fetch, synchronize, or resolve live GitHub review threads.
- Release verification is documented and repeatable, and `release-dry-run` can inspect the local target commit, changelog notes, and tag status locally or through a manual GitHub Actions workflow, but release tagging and GitHub release creation still require operator execution.
- Package distribution is clone-based. PyPI publication is not part of the current baseline.
- The user experience is primarily CLI and JSON, with static visual docs rather than a live dashboard.
- Some packet labels remain Codex-compatible in 0.1.x. Adapters must preserve packets and render host-specific text around them instead of rewriting packet contents.
- Security guardrails exist, but there is no full signed identity model, remote tamper-evident log, or sandbox enforcement boundary.

## Target State

A mature Agentic Cadence system should provide:

- thin, tested host adapters for at least two agents beyond Codex;
- an adapter template that makes the public CLI contract easy to copy without private imports;
- explicit host/session signals for context pressure, reviewer loops, CI loops, and operator stop requests;
- a shared runtime option with clear locking, identity, audit, and rollback expectations;
- role-aware approval and claim semantics tied to real users or agent identities;
- configurable policy for task sizing, pickup approval, review spend, and release authority;
- first-class PR review synchronization that fetches review threads, tracks resolution, and preserves deterministic local evaluation;
- broader release authority controls that keep generated notes, tag verification, and publication decisions behind explicit operator approval;
- a dashboard or visual companion that shows Cadence state, active handoffs, approvals, PR readiness, and release status without bypassing the CLI contract.

## Roadmap

### Now

- Keep the 0.1.x release line stable and clone-installable.
- Treat the CLI JSON packets as the public adapter boundary.
- Preserve Codex compatibility while moving user-facing docs toward agent-neutral language.
- Use PRs, required CI, elected bot review where appropriate, and release checks before tagging public releases.
- Use `release-dry-run` or the manual GitHub Actions dry-run workflow before operator-created tags or GitHub releases to compare generated notes, target commit, and tag status.

### Next

- Use the generic shell host-binding `--replay-contract` helper to refine
  `examples/adapter-template` before adding
  host-specific adapter claims.
- Use `examples/adapter-template/host_signal_contract.py` to catch schema and
  fixture drift before running the heavier replay and parity contracts.
- Use the generic host/shell `--parity-contract` helper to keep the
  adapter-template host-signal fixtures and shell host-event fixtures aligned.
- Use the generic host-signal fixtures as the compatibility bridge while
  comparing future host bindings against the same public CLI mapping behavior.
- Keep the generic host-signal smoke example green as the adapter contract is
  compared against future real host bindings.
- Compare the first real host binding against
  `examples/adapter-template/host-binding-mapping.md` and the generic
  host-signal smoke plus
  `examples/generic-host-signal/run.py --parity-contract` and
  `examples/generic-shell-host-binding/run.py --replay-contract` before
  adding host-specific claims.
- Keep the generic shell host-binding example aligned with the mapping example,
  including its file-backed `--host-event-file` and stdin-backed
  `--host-event-stdin` paths, as the executable pattern for future host
  adapters.

### Later

- Design a shared runtime backend with distributed locking, identity, and audit logs.
- Add real host adapters once the template and session-signal contract are stable.
- Expand policy configuration for teams, repositories, and agent classes.
- Build an operator-facing dashboard for handoff, review, and release visibility.
- Decide whether and when PyPI publication is worth the operational burden.

## Non-Goals For 0.1.x

- No autonomous merge or release without explicit operator instruction.
- No claim that Claude or Gemini adapters are shipped.
- No hidden writes to Cadence runtime files outside the public CLI.
- No remote backend or distributed lock promise.
- No PyPI publication.
- No replacement for generic secret scanning or external security review.

## Open Questions

- Which host should get the first named non-Codex adapter after the generic shell binding contract is stable: Claude, Gemini, or another host?
- Should shared runtime state be GitHub-backed, file-share-backed, database-backed, or pluggable?
- What identity model is strong enough for approvals without making local use heavy?
- Which policy controls should be hard-coded safety rules and which should be repo-configurable?
- How much release automation should the tool own before package-index publication is considered?
- What dashboard view would help operators most without encouraging bypasses around the CLI packet contract?
