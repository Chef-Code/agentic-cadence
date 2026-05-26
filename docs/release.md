# Release Checklist

Use this checklist before publishing a public release, creating a GitHub release, or changing package distribution settings.

## Scope

The `0.1.x` baseline is an early public release line for local clone-based use. It documents the protocol, packages the CLI, verifies first-run examples and the adapter smoke contract, and keeps public-release audit gates visible.

PyPI publication is a separate release decision. Do not publish to a package index until versioning, credentials, release ownership, and rollback expectations are explicitly reviewed.

## Before Opening A Release PR

- Confirm `README.md` describes the current support status and installation path.
- Confirm `CHANGELOG.md` has a dated entry for the release version.
- Confirm package metadata in `pyproject.toml` matches the intended version and project name.
- Confirm public-release guardrails still cover private-history, workflow pinning, and CODEOWNERS-sensitive paths.
- Run a dedicated secret scanner against the current tree and reachable history. `public_release_audit.py` is repo-specific and is not a substitute for generic secret scanning.
- Confirm any GitHub release notes match the merged changelog entry.

## Required Verification

Run these commands from the repository root:

```bash
python scripts/public_release_audit.py --history
python scripts/validate_protocol.py
python -m unittest tests.test_ci_checks -v
python -m compileall scripts codex_cadence transmission_control tests
python -m unittest discover -s tests -v
python scripts/ci_smoke.py
python -m pip install --upgrade pip build
python scripts/verify_package.py
git diff --check
```

## GitHub Release Notes

GitHub release notes should include:

- version number and release date;
- a short statement that the release is an early public protocol and tooling baseline;
- install command for clone-based use;
- the main capabilities shipped in this version;
- any explicit non-goals, such as PyPI publication not being part of the baseline.

## After Merge

- Wait for required branch-protection checks on `main` to pass.
- Create the release tag only from a verified `main` commit.
- If a GitHub release is created, keep its notes aligned with `CHANGELOG.md`.
- If a package publication is added in a future release, record the package-index URL and verification command in this document.
