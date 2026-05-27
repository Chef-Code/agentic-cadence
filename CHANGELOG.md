# Changelog

All notable public changes to Agentic Cadence are documented here.

## Unreleased

Current `main` contains post-0.1.1 release-readiness and adapter-contract work.

### Added

- `release-dry-run` command support for local version, changelog, target-ref, generated release-note, and tag-target verification while requiring operator confirmation for any tag, GitHub release, or package publication.
- Manual `.github/workflows/release-dry-run.yml` workflow that runs the same read-only release dry run from GitHub Actions, uploads `release-dry-run.json` and `release-notes.md`, and fails on blockers without creating tags, releases, pushes, merges, or packages.
- Generic host-signal fixtures and smoke coverage for no-signal, `context_pressure`, and `operator_stop` behavior through the copyable adapter template.
- Host-binding mapping documentation that shows how future host bindings should translate host-observed events into the adapter-local signal shape and public CLI arguments.
- Generic shell host-binding example with bundled fixture smoke mode, file-backed `--host-event-file`, and stdin-backed `--host-event-stdin` paths for one external host-event JSON payload or JSON `null`.

### Changed

- Adapter documentation now treats the generic shell host-binding example as the executable pattern for future host adapters while still avoiding unsupported Claude or Gemini adapter claims.
- Release documentation now points operators at both local `release-dry-run` and the manual GitHub Actions dry run before any operator-created tag or GitHub release.

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
