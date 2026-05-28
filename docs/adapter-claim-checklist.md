# Adapter Claim Checklist

Use this checklist before opening any PR that claims support for a named
non-Codex host adapter. The current repository provides generic adapter
contracts only. No Claude or Gemini adapter is shipped in this repository.

## Claim Boundary

- Name the host binding being proposed and keep the implementation thin:
  host-specific code should observe host/session events, map them into the
  generic signal shape, call the public Cadence CLI, and render pickup text.
- Do not import `codex_cadence` or `transmission_control` internals from the
  adapter. Use subprocess calls to the public command surface.
- Host adapters must preserve CLI JSON packets returned by Cadence commands. They may
  render around packets, but they must not rewrite packet contents or synthesize
  protocol state.
- Treat `stop_current_session` as a hard stop for the current host window.
- Require Cadence state to be `PLAY_ON` before preparing continuation work.
- Surface approval gates to the operator instead of self-attesting around them.
- Until a real host binding and its evidence are included, do not claim named
  host support in README, roadmap, release notes, package metadata, or PR text.

## Mapping Evidence

Document the host-event mapping before claiming support:

- Update or reference `examples/adapter-template/host-binding-mapping.md`.
- Show how the host reports no signal, `context_pressure`, and `operator_stop`.
- Show how host fields map to `kind`, `source`, `confidence`, `summary`,
  `task_type`, `drivers`, and `next_action`.
- Explain any host event that cannot be represented by the current generic
  signal shape. If the shape needs to change, update the generic fixtures and
  contracts before adding the named adapter claim.

## Required Contract Commands

Run the schema and generic smoke contracts:

```bash
python examples/adapter-template/host_signal_contract.py
python examples/generic-host-signal/run.py --cadence-python python
python examples/generic-host-signal/run.py --parity-contract --cadence-python python
```

Run the generic shell replay contract:

```bash
python examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python
```

Run the external conformance harness. First run the generic baseline:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python
```

Then run the proposed binding through the same harness with a quoted command
template. The command must accept `{host_event_file}` and `{case_work_dir}` and
should forward `{cadence_args}` when it invokes Cadence:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
```

Run the composite pre-claim suite. First run the generic baseline:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python
```

Then run the proposed binding through the composite runner:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python --binding-command-template 'python path/to/external-binding.py --host-event-file "{host_event_file}" --work-dir "{case_work_dir}" {cadence_args}'
```

## PR Evidence

Before asking for review, include:

- the exact commands above and their results;
- the host-binding mapping evidence;
- the files that implement the named binding;
- documentation that says what is supported and what is not supported;
- confirmation that generic fixtures still pass and the adapter does not claim
  broader host support than it implements.

If any generic contract fails, fix the generic mapping or adapter behavior
before claiming support. If the named host cannot supply one of the required
signals yet, keep the PR generic and do not claim named host support.
