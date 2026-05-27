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

## Host/Session Signal Contract

The minimal context-pressure signal contract is adapter-facing behavior, not a
new core object model. The copyable template demonstrates this with an
adapter-local `HostSessionSignal` inside `examples/adapter-template/adapter.py`.
It must not be imported from or exported by `codex_cadence`.

For this slice, a host signal carries `kind`, `source`, `confidence`, `summary`,
`task_type`, `drivers`, and `next_action`. The adapter validates those fields,
requires `PLAY_ON` through `status`, and maps the CLI-backed fields into the
existing `prepare-handoff` arguments. `kind` maps to `--guardrail`:
`context_pressure` becomes `context`, and `operator_stop` becomes
`operator_stop`. `summary`, `task_type`, `drivers`, and `next_action` map to
their matching CLI arguments. `source` and `confidence` are adapter-local
validation/provenance fields for now; the current public CLI does not store
them as handoff metadata. `drivers` may be empty because the public CLI allows
no `--driver` values, but any supplied driver must be accepted by the existing
task sizing model.

This contract does not make Agentic Cadence infer context pressure from
transcripts, token guesses, or CLI internals. The host observes session state;
the adapter passes explicit arguments; Cadence enforces the protocol gates.

The copyable template also includes generic JSON host-signal fixtures under
`examples/adapter-template/host-signal-fixtures`. They exercise
`context_pressure`, `operator_stop`, and no-signal behavior through
`--host-signal-file` without claiming to be a Claude, Gemini, or other real
host adapter.

The generic host-signal smoke example at `examples/generic-host-signal/run.py`
uses those fixtures as a host-neutral compatibility bridge. It invokes the
copyable adapter template as a subprocess, drives Cadence through the public
CLI, and verifies the no-signal, `context_pressure`, and `operator_stop`
mapping behavior before any real host adapter exists.

The host-binding mapping example at
`examples/adapter-template/host-binding-mapping.md` shows how a future host
binding can translate host-observed events into the same fixture fields and
public CLI arguments without shipping or claiming a real Claude or Gemini
adapter.

The generic shell host-binding stub at
`examples/generic-shell-host-binding/run.py` turns simple host-event JSON into
that same adapter-local signal shape, invokes the copyable adapter template, and
verifies the public CLI behavior. It is a runnable host-binding pattern, not a
real host adapter.

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

## Executable Smoke Contract

The executable public-CLI smoke contract lives at `examples/adapter-smoke/run.py`. It exercises the early adapter surface through subprocess calls only, preserves the JSON packets returned by Cadence, and never imports `codex_cadence` or `transmission_control` internals.

Run it from a clone after installing the package:

```bash
python examples/adapter-smoke/run.py
```

When running directly from a source checkout without installing the command, point it at the current Python interpreter:

```bash
python examples/adapter-smoke/run.py --cadence-python python
```

The smoke covers `status`, `prepare-handoff`, approval-gated `claim-handoff`, `approve-handoff`, `complete-handoff`, `discover-candidates`, `pr-body-preflight`, and `pr-readiness`. Its output is a JSON summary that includes the raw command packets under `packets` and a `command_trace` that separates old-session adapter, operator approval, new-session adapter, and utility phases.

Current packets may still contain Codex-compatible packet labels retained by the 0.1.x command surface. That is part of the current compatibility layer, not a requirement for future Claude or Gemini adapters. Host adapters should preserve returned packets and render host-specific text around them rather than rewriting packet contents.

The generic host-signal smoke contract is also executable from a source clone:

```bash
python examples/generic-host-signal/run.py --cadence-python python
```

It is intentionally narrower than the full adapter smoke: it focuses on the
host/session signal fixture mapping and the adapter template's preserved
`status` and `prepare-handoff` packets.

The generic shell host-binding stub is executable from a source clone:

```bash
python examples/generic-shell-host-binding/run.py --cadence-python python
```

It is narrower still: it proves that a host binding can map host-native event
JSON into the generic signal shape before handing control to the adapter
template and public CLI.

## Copyable Adapter Template

The copyable host adapter template lives at `examples/adapter-template`. It is the smallest practical shape for a future host binding: call the public CLI, pass an explicit runtime root, preserve returned JSON packets, stop on `stop_current_session`, and render host-specific pickup text around the preserved packet.

The template includes placeholder hooks for context-pressure detection, generic host-signal fixtures, and pickup rendering. It does not ship a Claude or Gemini adapter; it gives those future adapters a concrete boundary to copy without importing Cadence internals.

## First Implementation Slice

The current adapter slice is documentation, an executable smoke fixture, and a copyable template, not a full host integration. A small host adapter package should prove that it can:

- call the CLI without private imports;
- pass an explicit runtime root;
- preserve JSON packets unchanged;
- stop after `prepare-handoff` returns `stop_current_session: true`;
- refuse to continue when Cadence is not `PLAY_ON`.
