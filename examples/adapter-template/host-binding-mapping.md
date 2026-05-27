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
| The operator asks the host session to stop and prepare pickup. | `kind: "operator_stop"`, host-specific `source`, `confidence`, `summary`, `task_type`, `drivers`, and `next_action`. | The adapter validates the signal, checks `agentic-cadence status`, and calls `prepare-handoff` with `--guardrail operator_stop`, `--summary`, `--task-type`, any `--driver` values, and `--next-action`. |
| The host has no stop or handoff signal. | `null`, matching `host-signal-fixtures/no-signal.json`. | The adapter makes no Cadence call and returns `no_handoff_needed`. |

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
generic smoke example:

```bash
python examples/generic-host-signal/run.py --cadence-python python
```

That smoke example runs `context_pressure`, `operator_stop`, and no signal
fixtures through `examples/adapter-template/adapter.py` and verifies the public
CLI mapping behavior. A real host binding should match those observable
behaviors before adding host-specific event detection.
