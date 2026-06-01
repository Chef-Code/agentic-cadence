# Changelog

All notable public changes to Agentic Cadence are documented here.

## Unreleased

### Added

- Generic adapter-contract fixtures now cover `reviewer_loop` and `ci_loop`
  host/session signals alongside no-signal, `context_pressure`, and
  `operator_stop` behavior without claiming a named host adapter.
- Added `examples/adapter-claim-verifier/run.py` to turn compact adapter
  evidence into a generic-only or named-host-claim decision before any
  host-specific support claim is documented.
- Added initial `loop-tick --policy-file` support for local executor-task
  path/check/runtime/stop-condition bounds that retain built-in safety stops,
  plus compact audit records for loop decisions and executor-result validation
  with task/result checksums.
- Tightened generic executor packet validation so built-in safety stops and
  absolute expected result-evidence paths are required before result evidence
  can validate.
- Added an audit replay design spec for the read-only audit verification
  slice. The spec defines the `audit-replay.v1` packet, blocker codes, count
  semantics, and focused test coverage.
- Implemented the read-only `audit-replay` command for local
  `cadence-audit.v1` JSONL history, including zero-record fresh roots, stable
  corrupt/unsupported blocker codes, event counts, and checksum syntax
  validation.
- Added local command policy support for emitted executor task packets:
  `cadence-loop-policy.v1` can provide `allowed_commands` and
  `denied_commands`, and executor result validation rejects evidence outside
  those task-carried bounds.

### Changed

- Roadmap current-state wording now distinguishes the released `0.1.3`
  baseline from unreleased audit-replay work in the current development tree.
- Living docs now describe `audit-replay` as implemented while preserving the
  boundary that clean replay evidence does not approve executor invocation.
- `validate-executor-result` now treats a non-`DRIVE` brake as an active stop
  for task packets that include `brake_not_drive`; non-`stopped` completion
  evidence is rejected with `stop_active_loop`.

### Fixed

- Hardened command-policy validation against compound commands, command
  substitutions, and shell-wrapper payloads, and rejected null task
  command-policy fields without crashing result validation.
- `validate-executor-result` now fails closed with `provide_runtime_root` when
  otherwise-valid non-`stopped` completion evidence includes `brake_not_drive`
  but no runtime root was supplied.

## 0.1.3 - 2026-05-29

Adapter contract runner Windows path-depth fix.

### Fixed

- Adapter contract runner now uses a short per-checkout default work directory
  on Windows to avoid nested Git path-length failures in deep checkouts.

### Release Notes

- This release is intended for local clone-based use with `pip install .`.
- PyPI publication is not part of the `0.1.3` baseline.
- No Claude or Gemini adapter is shipped; the adapter contract remains generic
  pre-claim evidence for future host bindings.

## 0.1.2 - 2026-05-29

Adapter contract and release-readiness baseline.

### Added

- `release-dry-run` command support for local version, changelog, target-ref, generated release-note, and tag-target verification while requiring operator confirmation for any tag, GitHub release, or package publication.
- Manual `.github/workflows/release-dry-run.yml` workflow that runs the same read-only release dry run from GitHub Actions, uploads `release-dry-run.json` and `release-notes.md`, and fails on blockers without creating tags, releases, pushes, merges, or packages.
- Generic host-signal fixtures and smoke coverage for no-signal, `context_pressure`, and `operator_stop` behavior through the copyable adapter template.
- Host-binding mapping documentation that shows how future host bindings should translate host-observed events into the adapter-local signal shape and public CLI arguments.
- Generic shell host-binding example with bundled fixture smoke mode, file-backed `--host-event-file`, and stdin-backed `--host-event-stdin` paths for one external host-event JSON payload or JSON `null`.
- Generic shell host-binding replay, generic host/shell parity, external host-binding conformance, and composite adapter-contract runner coverage for future host-binding claims.
- Reviewer-verifiable compact adapter evidence with the `generic-adapter-contract-evidence` artifact, checked-in schema fixture, and `--validate-evidence-file` verifier for `adapter-contract-evidence.json`.

### Changed

- Adapter documentation now treats the generic shell host-binding example as the executable pattern for future host adapters while still avoiding unsupported Claude or Gemini adapter claims.
- Release documentation now points operators at both local `release-dry-run` and the manual GitHub Actions dry run before any operator-created tag or GitHub release.

### Release Notes

- This release is intended for local clone-based use with `pip install .`.
- PyPI publication is not part of the `0.1.2` baseline.
- No Claude or Gemini adapter is shipped; the adapter contract remains generic pre-claim evidence for future host bindings.

## 0.1.1 - 2026-05-26

Adapter smoke contract release.

### Added

- Executable public-CLI adapter smoke example that validates the current adapter boundary without importing Cadence internals.
- Linux and Windows CI coverage for the adapter smoke example in the package install matrix.
- Adapter documentation that clarifies the current Codex-compatible packet labels and the intended path for future Claude and Gemini host adapters.

### Changed

- Release baseline now explicitly includes the adapter smoke contract as part of clone-based verification.

### Release Notes

- This release is intended for local clone-based use with `pip install .`.
- PyPI publication is not part of the `0.1.1` baseline.
- Host adapters should preserve returned JSON packets and render host-specific pickup text around them.

## 0.1.0 - 2026-05-26

Initial public release.

### Added

- Agent-neutral Cadence protocol documentation for governed continuation across coding agents.
- `agentic-cadence` command-line entry point, with compatibility wrappers for `codex-cadence` and `codex-transmission`.
- Local handoff lifecycle commands for creating, claiming, completing, and discovering handoffs.
- `prepare-handoff` for deterministic old-session packaging, signed ready handoff creation, clean-square recording, and explicit stop packets.
- `pr-readiness` and `pr-body-preflight` for deterministic pull-request readiness checks from saved local inputs.
- First-run examples for POSIX shells and Windows PowerShell.
- Public-release audit tooling for current-tree and Git-history checks.
- README visual assets for the handoff flow and Cadence states.

### Release Notes

- This release is intended for local clone-based use with `pip install .`.
- PyPI publication is not part of the `0.1.0` baseline.
- The protocol is designed to remain agent-neutral while the first implementation keeps Codex compatibility names available.
