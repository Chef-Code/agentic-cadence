# Agentic Cadence

![Python 3.11+, MIT license, PR checks, and agentic-cadence package badges.](docs/assets/readme-badges.svg)

![Agentic Cadence banner showing a signed handoff, clean square validation, and fresh agent continuation.](docs/assets/agentic-cadence-banner.svg)

Durable handoff and governed continuation for coding agents that outlive one chat window.

Agentic Cadence helps an agent stop cleanly, hand off context to a fresh session, and continue only when the repository and Cadence state allow it.

The first implementation is used with Codex, and the protocol is intentionally agent-neutral so future adapters can support Claude, Gemini, and other coding agents without changing the core handoff model.

## Current Status

Agentic Cadence is an early public protocol and tooling release. The current baseline is ready for local clone-based use with `pip install .`, protocol validation, first-run examples, the adapter smoke contract, generic host-signal and shell host-binding examples, release dry-run verification, and public-release history auditing.

PyPI publication is not part of this baseline. Treat package-index publication, signed version tags, and broader adapter support as follow-on release work.

See the current [technical roadmap](docs/roadmap.md) for known edges and target state.

## Future Agent Adapters

The protocol is meant to stay agent-neutral while adapters handle host-specific details. See `docs/adapters.md` for the adapter boundary, current Codex compatibility surface, and the intended path for future Claude and Gemini support.

The adapter smoke contract is executable from a clone:

```bash
python examples/adapter-smoke/run.py --cadence-python python
```

It proves the adapter path through CLI JSON packets without importing Cadence internals. Current packets may still contain Codex-compatible packet labels retained by the 0.1.x command surface; adapters should preserve those packets rather than rewriting them.

The generic host-signal smoke example exercises the adapter-local signal
fixtures before a real host binding exists. Its parity contract compares that
fixture behavior with the generic shell host-binding replay contract:

```bash
python examples/adapter-template/host_signal_contract.py
python examples/generic-host-signal/run.py --cadence-python python
python examples/generic-host-signal/run.py --parity-contract --cadence-python python
```

It verifies no-signal, `context_pressure`, and `operator_stop` behavior through
the copyable adapter template without claiming Claude or Gemini adapter support.
The schema contract validates that the checked-in host-signal fixtures and
shell host-event payloads have the expected fields and normalized meanings.
The parity contract verifies that the shell host-event mapping stays aligned
with the adapter-template host-signal fixtures for normalized
adapter/CLI-observed behavior.

The generic shell host-binding example adds file-backed and stdin-backed shell
integration paths for one external host event, plus a replay contract that
compares those paths against the bundled fixtures:

```bash
python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-file /path/to/host-event.json
some-host-signal-command | python examples/generic-shell-host-binding/run.py --cadence-python python --host-event-stdin
python examples/generic-shell-host-binding/run.py --replay-contract --cadence-python python
```

The file or stdin payload must contain a `context_pressure` or `operator_stop`
host-event object, or JSON `null` when no handoff is needed. This still
exercises the adapter template and public CLI boundary; it does not ship a real
Claude or Gemini adapter. The replay contract verifies that the same payload
produces the same normalized adapter/CLI-observed behavior through bundled
fixture, file-backed, and stdin-backed paths.

The generic external host-binding conformance harness compares a supplied
binding command against the same generic shell replay baseline before any named
host adapter is claimed:

```bash
python examples/external-host-binding-conformance/run.py --cadence-python python
```

By default it uses the generic shell host-binding example as the sample external
command. Future host bindings can pass `--binding-command-template` with
quoted path placeholders such as `"{host_event_file}"` and
`"{case_work_dir}"` to prove they match the generic fixture behavior without
claiming Claude or Gemini adapter support.

The generic adapter contract pre-claim suite composes the schema, smoke,
replay, parity, and external conformance contracts into one command:

```bash
python examples/adapter-contract-runner/run.py --cadence-python python
```

Use it before any future host-specific binding claim. Future bindings can pass
the same quoted binding command template through the runner, and the runner
still reports that it does not ship Claude or Gemini adapter support.

## Protocol At A Glance

![Four-step Agentic Cadence handoff flow from old context to signed handoff, clean square, and fresh agent.](docs/assets/handoff-flow.svg)

## Requirements

- Python 3.11 or newer
- Git

## Install From A Clone

```bash
python -m pip install .
agentic-cadence --help
python -m codex_cadence --help
```

Compatibility command names are still available:

```bash
codex-cadence --help
codex-transmission --help
```

## Five-Minute First Run

Use a disposable runtime root so the first run does not touch your global agent state:

```bash
agentic-cadence --root .agentic-cadence-demo status
agentic-cadence --root .agentic-cadence-demo create-handoff --id read-the-repo --title "Read the repo" --repo local/example --branch main --task-type discovery --message-file examples/first-run/handoff.md
agentic-cadence --root .agentic-cadence-demo next-handoff
agentic-cadence --root .agentic-cadence-demo claim-handoff read-the-repo --claimer demo
agentic-cadence --root .agentic-cadence-demo complete-handoff read-the-repo --summary "first run completed"
```

`.agentic-cadence-demo/` is disposable and ignored by git. The legacy `.codex-cadence-demo/` path is also ignored for compatibility.

## Run The Example Workflow

On macOS or Linux:

```bash
bash examples/first-run/run.sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File examples/first-run/run.ps1
```

The example creates `examples/first-run/work/`, initializes a tiny target Git repository, runs a handoff lifecycle, and runs candidate discovery against `examples/first-run/work/repo`.

## Candidate Discovery

Candidate discovery is read-only. It can inspect local repo state and reviewed business memory from `docs/cadence/business-memory.md`. The example runner creates `examples/first-run/work/repo` and runs discovery automatically. After running the example workflow, you can repeat that discovery command:

```bash
agentic-cadence --root examples/first-run/work/runtime discover-candidates --cwd examples/first-run/work/repo --intent hybrid --discovery-mode local --elect
```

Business-memory candidates are discovery-only. They can seed investigation, but they cannot directly execute changes, commit, push, merge, or bypass Cadence governance.

## Context Handoff Preparation

`prepare-handoff` packages the old-session side of a context handoff into one deterministic local command. It checks Cadence state, snapshots the repo, writes a signed ready handoff, validates it, records clean-square, and returns a packet with `stop_current_session: true`.

```bash
agentic-cadence prepare-handoff --id context-loop --title "Continue bounded work" --guardrail context --repo owner/repo --cwd . --task-type execution --summary "current session is handing off"
```

The command does not claim the handoff, launch a new agent window, commit, push, open a PR, spend review, or merge. V1 requires an explicit guardrail such as `--guardrail context`; automatic context detection requires a host/session signal that Cadence does not infer from transcript guesses.

## PR Readiness

`pr-readiness` evaluates saved `gh pr view --json ...` output and returns a deterministic merge-readiness packet. It does not call GitHub, spend paid review, or merge the PR.

```bash
gh pr view 9 --json number,title,state,isDraft,mergeable,mergeStateStatus,reviewDecision,body,headRefName,baseRefName,headRefOid,statusCheckRollup > pr.json
agentic-cadence pr-readiness --pr-json-file pr.json --required-check "Python and protocol checks" --pr-template-file .github/pull_request_template.md
```

The packet reports blockers, waiting checks, duplicate check groups, skipped Codex Review jobs, missing body sections, missing PR-template sections, and the recommended next action. `--pr-template-file` reads a local Markdown template and checks that its headings are represented in the saved PR body; it does not rewrite the PR.

## PR Body Preflight

`pr-body-preflight` checks a draft PR body before publishing or updating a pull request. It reads local files only, uses the same Markdown heading parser as `pr-readiness`, and never rewrites the body.

```bash
agentic-cadence pr-body-preflight --body-file pr-body.md --pr-template-file .github/pull_request_template.md
```

Use this before `gh pr create` or `gh pr edit` when a repository has a PR template. Missing template headings are reported as blockers with `recommended_next_action: update_pr_body`. If no template file or `--required-body-section` is supplied, the packet fails closed with `recommended_next_action: provide_template_or_sections`.

## Release Dry Run

`release-dry-run` checks release metadata before an operator creates a tag or GitHub release. After `pyproject.toml` and `CHANGELOG.md` have been updated for the intended release version, it reads local metadata and Git refs, generates release notes from the matching changelog entry, requires the selected target ref to match checked-out `HEAD`, verifies an existing tag points at the selected release commit, and returns a JSON packet with `operator_confirmation_required: true`.

```bash
agentic-cadence release-dry-run --cwd . --version <version>
```

The command does not create tags, call GitHub, create a release, write release-note files, build distributions, upload artifacts, or publish packages. Package-index publication remains blocked in the packet with `recommended_next_action: do_not_publish_package`.

For repository releases, `.github/workflows/release-dry-run.yml` exposes the same check as a manual GitHub Actions workflow. It accepts `version`, `tag`, and optional `target_ref`, uploads `release-dry-run.json` and `release-notes.md`, and fails on blockers while still requiring operator confirmation for any tag, GitHub release, or package publication.

## Runtime State

Runtime state lives outside project repositories by default for new installs:

```text
~/.codex/cadence
```

If the legacy `~/.codex/transmission` root already exists, Agentic Cadence reuses it so queued handoffs and brake state survive the rename. If both legacy and Cadence roots exist, Cadence fails closed until you select one with `--root`, `CODEX_CADENCE_ROOT`, or `CODEX_TRANSMISSION_ROOT`. Commands invoked with `--root X` create the runtime layout at `X` when it is missing.

Cadence exposes these states:

![Cadence state summary for PLAY_ON, HUDDLE, and TIMEOUT.](docs/assets/cadence-states.svg)

- `PLAY_ON`: work may continue.
- `HUDDLE`: pause and coordinate.
- `TIMEOUT`: stop until an operator resumes.

## Compatibility

Primary names:

- `agentic-cadence`
- `python -m codex_cadence`
- `scripts/cadence.py`
- `codex_cadence.*`

Compatibility names:

- `codex-cadence`
- `codex-transmission`
- `scripts/transmission.py`
- `transmission_control.*`

## Development

```bash
python -m compileall scripts codex_cadence transmission_control tests examples
python -m unittest discover -s tests -v
python scripts/validate_protocol.py
python scripts/ci_smoke.py
```

Package verification:

```bash
python -m pip install build
python -m build
python scripts/verify_package.py
```

## License

Agentic Cadence is licensed under the MIT License. See `LICENSE`.
