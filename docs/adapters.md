# Agent Adapter Direction

Agentic Cadence should stay agent-neutral. The core protocol defines handoffs, Cadence state, clean-square evidence, and continuation gates. Adapters translate a specific coding agent host into that protocol without changing the protocol model.

The current implementation is Codex-compatible because it ships the `agentic-cadence` command plus legacy `codex-cadence` and `codex-transmission` command names. That compatibility surface is a first host binding, not a reason to make the protocol Codex-only.

The public package metadata stays centered on `agentic-cadence` and shipped compatibility aliases. Future host names such as Claude and Gemini belong in adapter-roadmap documentation and pre-claim evidence, not in package keywords or support claims until a named adapter actually ships.

## Adapter Boundary

Adapters should consume CLI JSON packets and invoke the public command surface. Adapters do not directly write Cadence runtime files, mutate handoff records, or infer continuation permission outside the protocol gates.

The stable surface for early adapters is:

- `agentic-cadence status`
- `agentic-cadence prepare-handoff`
- `agentic-cadence approve-handoff`
- `agentic-cadence claim-handoff`
- `agentic-cadence complete-handoff`
- `agentic-cadence discover-candidates`
- `agentic-cadence loop-tick --emit-executor-task`
- `agentic-cadence validate-executor-result`
- `agentic-cadence pr-readiness`
- `agentic-cadence pr-body-preflight`

Adapters may render host-specific pickup instructions, map a host/session signal into explicit command arguments, and choose the runtime root for that host. They must preserve the packet fields returned by Cadence commands, especially `stop_current_session`.

## Generic Executor Contract

The executor contract is generic. It does not select Codex, Claude, Gemini, or
any other named implementation host.

`loop-tick --emit-executor-task` can attach a
`generic-executor-task.v1` packet when an elected task is available and local
repo confidence is not low. The packet records task identity, repo path,
branch/head snapshot, allowed repo-relative paths, required checks, positive
time/task limits, stop conditions, and the expected result evidence path.
Cadence still sets `executor_started: false`.

Executor result evidence uses `generic-executor-result.v1` and is checked by
`validate-executor-result`. The evidence must report executor id, timestamps,
status, files changed, commands run, validation results, summary, confidence,
blockers, dirty-worktree status, and resulting head SHA for successful results.
Successful evidence must not report a dirty worktree, must include command and
validation evidence, must show every required check command with exit code `0`,
and must include matching passed validation results. Changed files must stay
within the task packet's allowed paths, and reported commands/results must not
violate disabled commit, push, PR, or head-change permissions, including common
git/gh global-option and shell-wrapper forms.

This contract only defines the boundary between Cadence and an external
executor. It does not run a shell command, create a branch, commit, push, open a
PR, spend review, merge, or claim named host support.

## Host/Session Signal Contract

The minimal host stop-signal contract is adapter-facing behavior, not a new
core object model. The copyable template demonstrates this with an
adapter-local `HostSessionSignal` inside `examples/adapter-template/adapter.py`.
It must not be imported from or exported by `codex_cadence`.

For this slice, a host signal carries `kind`, `source`, `confidence`, `summary`,
`task_type`, `drivers`, and `next_action`. The adapter validates those fields,
requires `PLAY_ON` through `status`, and maps the CLI-backed fields into the
existing `prepare-handoff` arguments. `kind` maps to `--guardrail`:
`context_pressure` becomes `context`, `reviewer_loop` becomes `reviewer_loop`,
`ci_loop` becomes `ci_loop`, and `operator_stop` becomes `operator_stop`.
`summary`, `task_type`, `drivers`, and `next_action` map to their matching CLI
arguments. `source` and `confidence` are adapter-local validation/provenance
fields for now; the current public CLI does not store them as handoff metadata.
`drivers` may be empty because the public CLI allows no `--driver` values, but
any supplied driver must be accepted by the existing task sizing model.

This contract does not make Agentic Cadence infer context pressure, reviewer
loop exhaustion, or CI loop exhaustion from transcripts, token guesses, or CLI
internals. The host observes session state; the adapter passes explicit
arguments; Cadence enforces the protocol gates.

The copyable template also includes generic JSON host-signal fixtures under
`examples/adapter-template/host-signal-fixtures`. They exercise
`context_pressure`, `reviewer_loop`, `ci_loop`, `operator_stop`, and no-signal
behavior through `--host-signal-file` without claiming to be a Claude, Gemini,
or other real host adapter.

The schema helper at `examples/adapter-template/host_signal_contract.py`
validates those host-signal fixtures against the generic shell host-event
payloads before running subprocess smoke examples. It checks exact fixture
fields and normalized meanings while still treating `source` as adapter-local
provenance.

The generic host-signal smoke example at `examples/generic-host-signal/run.py`
uses those fixtures as a host-neutral compatibility bridge. It invokes the
copyable adapter template as a subprocess, drives Cadence through the public
CLI, and verifies the no-signal, `context_pressure`, `reviewer_loop`,
`ci_loop`, and `operator_stop` mapping behavior before any real host adapter
exists.

The same example exposes `--parity-contract` to compare its normalized
adapter/CLI-observed behavior against the generic shell host-binding replay
contract. That parity check keeps the adapter-template host-signal fixtures
and shell host-event fixtures aligned without adding a named host adapter.

The host-binding mapping example at
`examples/adapter-template/host-binding-mapping.md` shows how a future host
binding can translate host-observed events into the same fixture fields and
public CLI arguments without shipping or claiming a real Claude or Gemini
adapter.

The generic shell host-binding example at
`examples/generic-shell-host-binding/run.py` turns simple host-event JSON into
that same adapter-local signal shape, invokes the copyable adapter template, and
verifies the public CLI behavior. Its fixture smoke mode runs the bundled
host-event examples, while `--host-event-file` and `--host-event-stdin`
provide file-backed and stdin-backed paths for one external host-event JSON
object or JSON `null`. It is a runnable host-binding pattern, not a real host
adapter.

The same example also exposes `--replay-contract`. That mode replays the
bundled `no-event`, `context_pressure`, `reviewer_loop`, `ci_loop`, and
`operator_stop` payloads through the bundled fixture path, file-backed path, and
stdin-backed path, then compares their normalized adapter/CLI behavior. The
comparison ignores timestamps and temporary paths while checking the observed
event, guardrail, summary, task type, drivers, next action, packet keys, handoff
status, and stop signal.

The generic external host-binding conformance harness at
`examples/external-host-binding-conformance/run.py` compares a supplied binding
command against that generic shell replay baseline. By default it uses the
generic shell host-binding example as the sample external command; future host
bindings can pass `--binding-command-template` with quoted path placeholders
such as `"{host_event_file}"` and `"{case_work_dir}"`. This is a pre-claim
acceptance harness, not a Claude, Gemini, or other named host adapter.

The generic adapter contract pre-claim suite at
`examples/adapter-contract-runner/run.py` composes the schema helper, generic
host-signal smoke, generic shell replay contract, host/shell parity contract,
and external host-binding conformance harness into one subprocess-only command.
It is the broad generic check to run before a future host-specific binding
claim. Its `--evidence-summary` mode emits compact PR evidence with required
contract coverage, observed contract labels, pass status, and binding-template
placeholder coverage while omitting nested packet payloads. The runner still
does not ship or claim Claude, Gemini, or other named host support.
The PR workflow stores that compact output as `adapter-contract-evidence.json`
inside the `generic-adapter-contract-evidence` artifact for reviewer
inspection. The compact artifact declares
`schema_version: "generic-adapter-contract-evidence.v1"` as its stable reviewer
contract, with its checked-in project-specific schema fixture at
`examples/adapter-contract-runner/generic-adapter-contract-evidence.v1.schema.json`.
That fixture pins the accepted top-level result, compact evidence mode,
required contract labels, observed-label parity, and required pass booleans.
After downloading the artifact file, reviewers can validate the compact shape
without rerunning the suite:

```bash
python examples/adapter-contract-runner/run.py --validate-evidence-file adapter-contract-evidence.json
```

The adapter claim verifier at `examples/adapter-claim-verifier/run.py` turns the
compact evidence into an explicit claim decision. The uploaded generic baseline
should verify as generic-only:

```bash
python examples/adapter-claim-verifier/run.py --evidence-file adapter-contract-evidence.json
```

A future named host adapter claim must use compact evidence from the proposed
binding template, then pass the same template to the verifier:

```bash
python examples/adapter-claim-verifier/run.py --evidence-file adapter-contract-evidence.json --claim-host ExampleHost --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
```

The verifier can say whether the evidence boundary permits a named claim or the
PR must remain generic. It does not replace host-binding mapping evidence,
implementation review, or support-boundary documentation.

Before a PR documents a named non-Codex host binding, use
`docs/adapter-claim-checklist.md`. The checklist keeps the claim tied to the
generic schema, smoke, replay, parity, and external conformance evidence instead
of relying on prose alone.

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
python examples/adapter-template/host_signal_contract.py
python examples/generic-host-signal/run.py --cadence-python python
python examples/generic-host-signal/run.py --parity-contract --cadence-python python
```

It is intentionally narrower than the full adapter smoke: it focuses on the
host/session signal fixture mapping and the adapter template's preserved
`status` and `prepare-handoff` packets. The parity contract additionally
compares that behavior with the generic shell host-binding replay contract so
the two generic fixture families do not drift.

The generic shell host-binding example is executable from a source clone:

```bash
python examples/generic-shell-host-binding/run.py --cadence-python python
```

That command runs the bundled fixture smoke. To exercise a real shell-captured
signal without adding a host-specific adapter, pass a file-backed host event or
pipe one host-event payload through stdin:

```bash
python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-file /path/to/host-event.json
some-host-signal-command | python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-stdin
python examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python
```

The external file or stdin payload must be one host-event JSON object for
`context_pressure`, `reviewer_loop`, `ci_loop`, or `operator_stop`, or JSON
`null` when no handoff is needed. This is narrower still than a real adapter: it
proves that a host binding can map host-native event JSON into the generic
signal shape before handing control to the adapter template and public CLI.

Use the replay contract when changing the generic shell host-binding example.
It verifies that the same host-event payload stays consistent across the
bundled fixture, file-backed, and stdin-backed paths without claiming support
for any named non-Codex host adapter.

Use the external host-binding conformance harness before claiming a future
host-specific binding matches the generic behavior. The default command uses the
generic shell host-binding example as the sample external command and proves the
harness plumbing:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python
```

Use the composite pre-claim suite when you want the full generic adapter
contract in one command:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python
```

On Windows, the composite runner defaults to a short per-checkout disposable
work directory under the system temp root to avoid nested Git path-length
failures. Use `--work-dir` to override that location.

For a future binding, pass a command template and quote path placeholders:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
python examples/adapter-contract-runner/run.py --cadence-python python --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
```

## Copyable Adapter Template

The copyable host adapter template lives at `examples/adapter-template`. It is the smallest practical shape for a future host binding: call the public CLI, pass an explicit runtime root, preserve returned JSON packets, stop on `stop_current_session`, and render host-specific pickup text around the preserved packet.

The template includes placeholder hooks for host stop-signal detection, generic
host-signal fixtures, and pickup rendering. It does not ship a Claude or Gemini
adapter; it gives those future adapters a concrete boundary to copy without
importing Cadence internals.

## Current Adapter Slice

The current adapter work is documentation, executable smoke fixtures, a copyable
template, host-signal fixtures, a host-binding mapping example, and a generic
shell host-binding pattern with fixture, file, and stdin input modes plus the
replay-contract verifier. It also includes a generic host/shell parity
contract, an external host-binding conformance harness, and a composite generic
adapter contract pre-claim runner with compact PR evidence, a checked-in
project-specific schema fixture, and a reviewer-side `--validate-evidence-file`
verifier for the downloaded `adapter-contract-evidence.json` artifact. It is
not a full host integration. A small host adapter package should prove that it
can:

- call the CLI without private imports;
- pass an explicit runtime root;
- preserve JSON packets unchanged;
- stop after `prepare-handoff` returns `stop_current_session: true`;
- refuse to continue when Cadence is not `PLAY_ON`.
