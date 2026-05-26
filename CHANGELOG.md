# Changelog

All notable public changes to Agentic Cadence are documented here.

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
