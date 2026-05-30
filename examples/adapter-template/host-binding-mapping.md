# Host Binding Mapping Example

This example shows how a future host binding should translate host-observed
session events into the adapter template's generic host-signal fixture shape.
It is documentation for adapter authors, not a real host integration.

No Claude or Gemini adapter is shipped in this repository. A future host binding
should use this mapping to call the copyable adapter template or equivalent
adapter code while preserving the existing public CLI boundary.

## Mapping Table

| Host event | Fixture field values | Public CLI behavior |
| --- | --- | --- |
| The host reports context pressure or a near-limit session. | `kind: "context_pressure"`, host-specific `source`, `confidence`, `summary`, `task_type`, `drivers`, and `next_action`. | The adapter validates the signal, checks `agentic-cadence status`, and calls `prepare-handoff` with `--guardrail context`, `--summary`, `--task-type`, any `--driver` values, and `--next-action`. |
| The host reports reviewer-loop exhaustion and requests a fresh session before continuing review follow-up. | `kind: "reviewer_loop"`, host-specific `source`, `confidence`, `summary`, `task_type`, `drivers`, and `next_action`. | The adapter validates the signal, checks `agentic-cadence status`, and calls `prepare-handoff` with `--guardrail reviewer_loop`, `--summary`, `--task-type`, any `--driver` values such as `reviewer_feedback`, and `--next-action`. |
| The host reports CI-loop exhaustion and requests a fresh session before continuing verification. | `kind: "ci_loop"`, host-specific `source`, `confidence`, `summary`, `task_type`, `drivers`, and `next_action`. | The adapter validates the signal, checks `agentic-cadence status`, and calls `prepare-handoff` with `--guardrail ci_loop`, `--summary`, `--task-type`, any `--driver` values such as `ci_verification`, and `--next-action`. |
| The operator asks the host session to stop and prepare pickup. | `kind: "operator_stop"`, host-specific `source`, `confidence`, `summary`, `task_type`, `drivers`, and `next_action`. | The adapter validates the signal, checks `agentic-cadence status`, and calls `prepare-handoff` with `--guardrail operator_stop`, `--summary`, `--task-type`, any `--driver` values, and `--next-action`. |
| The host has no stop or handoff signal. | `null`, matching `host-signal-fixtures/no-signal.json`. | The adapter makes no Cadence call and returns `no_handoff_needed`. |

Adapter-local fields are not all Cadence fields. `source` and `confidence` are
validated provenance fields for the host binding; they are not passed to
`prepare-handoff` or stored in handoff metadata by the current public CLI.
CLI-backed fields are `summary`, `task_type`, `drivers`, `next_action`, and
the `kind` to `--guardrail` mapping. `confidence` must be `low`, `medium`, or
`high`; `task_type` must be `execution` or `discovery`; and each `drivers`
entry must be accepted by the existing `prepare-handoff` task sizing model.

## Adapter Boundary

A host binding should observe host-specific state outside Cadence, then pass an
explicit signal into the adapter boundary. It should not import
`codex_cadence`, write runtime files directly, infer approval, or rewrite the
JSON packets returned by the CLI.

The host binding may choose the signal `source`, render host-specific pickup
text, and decide where its runtime root lives. Cadence still owns protocol
decisions through public commands such as `status` and `prepare-handoff`.

## Compatibility Check

Before building a real host binding, compare the host event mapping against the
generic smoke and shell host-binding examples:

```bash
python examples/adapter-template/host_signal_contract.py
python examples/generic-host-signal/run.py --cadence-python python
python examples/generic-host-signal/run.py --parity-contract --cadence-python python
python examples/generic-shell-host-binding/run.py --cadence-python python
python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-file /path/to/host-event.json
some-host-signal-command | python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-stdin
python examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python
python examples/external-host-binding-conformance/run.py --cadence-python python
python examples/adapter-contract-runner/run.py --cadence-python python
```

The schema contract validates the checked-in host-signal fixtures and generic
shell host-event payloads before subprocess smoke runs. It rejects extra or
missing fields and catches drift between paired host-signal and shell
host-event payloads in `kind` or `event`, `confidence`, `summary`,
`task_type`, `drivers`, and `next_action`.

The generic smoke runs `context_pressure`, `reviewer_loop`, `ci_loop`,
`operator_stop`, and no signal fixtures through
`examples/adapter-template/adapter.py`. The shell host-binding example adds a
runnable host-event JSON mapping step before invoking the same adapter template.
Its fixture mode uses the bundled examples; its file-backed `--host-event-file`
and stdin-backed `--host-event-stdin` modes consume one external host-event JSON
object, or JSON `null` for no handoff needed. A real host binding should match
those observable behaviors before adding host-specific event detection.

The replay contract is the strictest generic shell check. It feeds the bundled
`no-event`, `context_pressure`, `reviewer_loop`, `ci_loop`, and `operator_stop`
host-event payloads through fixture, file-backed, and stdin-backed paths, then
compares their normalized adapter and public CLI behavior. Use it as an
example-level contract before claiming that a future host-specific binding
matches the generic mapping.

The host/shell parity contract compares the generic host-signal smoke with the
generic shell replay contract. Use it to catch drift between the adapter-template
host-signal fixtures and the shell host-event fixtures before adding
host-specific detection.

The external host-binding conformance harness compares a supplied binding
command against the generic shell replay baseline. Its default command uses the
generic shell host-binding example as a sample external binding; future named
adapters can pass `--binding-command-template` with quoted path placeholders
such as `"{host_event_file}"` and `"{case_work_dir}"` before claiming
host-specific support.

The generic adapter contract pre-claim suite at
`examples/adapter-contract-runner/run.py` composes those schema, smoke, replay,
parity, and external conformance checks into one command. Future bindings can
pass the same `--binding-command-template` through the runner before making any
host-specific support claim. Add `--evidence-summary` when preparing PR
evidence so the reviewer can see required contract coverage and binding
placeholder coverage without nested packet payloads.

Repository PR checks upload that compact JSON as the
`generic-adapter-contract-evidence` artifact containing
`adapter-contract-evidence.json`. Use `docs/adapter-claim-checklist.md` as the
canonical reviewer procedure for the schema fixture and `--validate-evidence-file`
check.

Use `examples/adapter-claim-verifier/run.py --evidence-file
adapter-contract-evidence.json` to confirm generic evidence stays generic. For a
future named host claim, pass `--claim-host` and the same
`--binding-command-template` used to generate the compact evidence.
