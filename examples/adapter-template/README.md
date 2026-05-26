# Agentic Cadence Adapter Template

`examples/adapter-template` is a copyable starting point for a host adapter. It
shows the smallest useful shape for turning a host/session signal into a
Cadence context handoff without depending on private project internals.

The template intentionally:

- uses the public CLI instead of importing Cadence internals;
- requires an explicit runtime root with `--runtime-root`;
- preserves returned JSON packets under `packets`;
- treats `stop_current_session` as the signal to stop the current host window;
- leaves `detect_host_session_signal()` as the host-specific signal hook;
- leaves `render_pickup_text()` as the host-specific pickup rendering hook;
- does not ship a Claude or Gemini adapter.

Run it against an installed command:

```bash
python examples/adapter-template/adapter.py \
  --runtime-root ./tmp/agentic-cadence-runtime \
  --repo local/example \
  --cwd . \
  --handoff-id example-context-handoff \
  --title "Example context handoff" \
  --summary "Current agent is close to context pressure." \
  --task-type execution \
  --driver reviewer_feedback \
  --cadence-timeout-seconds 120 \
  --next-action "Claim the handoff and continue from the preserved packet."
```

When copying this into a real host adapter, keep the Cadence boundary boring:
call `agentic-cadence` as a subprocess, pass the host's runtime root
explicitly, preserve returned JSON packets, and render host-specific text
around those packets instead of rewriting them.

Pass the host's sizing signal through required `--task-type` and repeatable `--driver`
arguments. Cadence uses those fields to decide whether pickup should require
approval, so copied adapters should not collapse every handoff into the default
`execution` shape.

From a source checkout, pass the module command as one quoted value:

```bash
python examples/adapter-template/adapter.py \
  --runtime-root ./tmp/agentic-cadence-runtime \
  --repo local/example \
  --cwd . \
  --handoff-id example-context-handoff \
  --title "Example context handoff" \
  --summary "Current agent is close to context pressure." \
  --task-type discovery \
  --driver unknown_repo_area \
  --driver cross_subsystem \
  --next-action "Claim the handoff and continue from the preserved packet." \
  --cadence-command "python -m codex_cadence"
```

On Windows, unquoted absolute command paths such as
`C:\Python312\python.exe -m codex_cadence` are parsed with Windows-safe rules so
the backslashes are preserved.

Each Cadence subprocess call has a timeout controlled by
`--cadence-timeout-seconds`, so a copied adapter fails deterministically instead
of hanging forever if the underlying command stalls.

## Host Session Signal

`HostSessionSignal` lives only inside `examples/adapter-template/adapter.py`.
It is a copyable template helper, not a stable Python API exported by
`codex_cadence`.

The template validates the signal before calling `prepare-handoff`:

- `kind`: `context_pressure` or `operator_stop`
- `source`: non-empty host/source label, up to 64 characters
- `confidence`: `low`, `medium`, or `high`
- `summary`: non-empty text passed to `--summary`
- `task_type`: `execution` or `discovery`
- `drivers`: zero or more task sizing drivers accepted by the public CLI
- `next_action`: non-empty text passed to `--next-action`

When no host signal is present, the adapter returns `no_handoff_needed` and
does not call Cadence. When a signal is present, the adapter still checks
`status`, requires `PLAY_ON`, and uses the existing `prepare-handoff` command.

The hooks to replace are:

- `detect_host_session_signal()`: map the host's context pressure or operator
  stop signal into an adapter-local `HostSessionSignal`.
- `render_pickup_text()`: format the next-agent instructions in the host's
  preferred surface while keeping the raw Cadence packet available.
