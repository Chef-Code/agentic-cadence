# Agent Adapter Direction

Agentic Cadence should stay agent-neutral. The core protocol defines handoffs, Cadence state, clean-square evidence, and continuation gates. Adapters translate a specific coding agent host into that protocol without changing the protocol model.

The current implementation is Codex-compatible because it ships the `agentic-cadence` command plus legacy `codex-cadence` and `codex-transmission` command names. That compatibility surface is a first host binding, not a reason to make the protocol Codex-only.

## Adapter Boundary

Adapters should consume CLI JSON packets and invoke the public command surface. Adapters do not directly write Cadence runtime files, mutate handoff records, or infer continuation permission outside the protocol gates.

The stable surface for early adapters is:

- `agentic-cadence status`
- `agentic-cadence prepare-handoff`
- `agentic-cadence approve-handoff`
- `agentic-cadence claim-handoff`
- `agentic-cadence complete-handoff`
- `agentic-cadence discover-candidates`
- `agentic-cadence pr-readiness`
- `agentic-cadence pr-body-preflight`

Adapters may render host-specific pickup instructions, map a host/session signal into explicit command arguments, and choose the runtime root for that host. They must preserve the packet fields returned by Cadence commands, especially `stop_current_session`.

## Required Behavior

An adapter must:

- respect `PLAY_ON`, `HUDDLE`, and `TIMEOUT` state;
- treat `stop_current_session` as a hard stop for the current agent window;
- preserve the `prepare-handoff` packet relationship between the repository snapshot, clean-square result, and stop packet;
- surface approval gates instead of self-attesting around them;
- keep candidate discovery read-only;
- must not bypass Cadence governance to modify files, commit, push, merge, or spend review.

## Host Notes

Codex support currently exists through the command names and local workflow that this repository exercises.

Future Claude and Gemini adapters should start as thin host bindings. Their first responsibility is to map their host/session signal into existing Cadence commands and render a reliable pickup packet for the next agent context.

No Claude or Gemini adapter is shipped in 0.1.x. Until those adapters exist, the public contract is the agent-neutral CLI and protocol documentation.

## First Implementation Slice

The next useful adapter slice is documentation plus fixtures, not a full host integration. A small adapter package should prove that it can:

- call the CLI without private imports;
- pass an explicit runtime root;
- preserve JSON packets unchanged;
- stop after `prepare-handoff` returns `stop_current_session: true`;
- refuse to continue when Cadence is not `PLAY_ON`.
