# Public Release Checklist

Agentic Cadence is safe to publish only after both the current tree and Git history pass release audit.

## Current Private Repository

Run these checks before every public-release PR:

```powershell
python scripts\public_release_audit.py
python -m unittest discover -s tests -v
python scripts\validate_protocol.py
python scripts\verify_package.py
```

The default audit scans tracked files plus untracked local release files, verifies GitHub Actions are pinned to full commit SHAs, and checks that CODEOWNERS covers automation guardrails.

## Clean Public History

Do not make the existing private repository public while old private commits are reachable. Deleted files remain available in Git history after a normal PR merge.

Create a clean public mirror or perform an explicitly reviewed history rewrite, then run:

```powershell
python scripts\public_release_audit.py --history
```

The history audit must pass in the exact repository that will become public.

## GitHub Settings

Before switching visibility:

- Require pull requests before merging into `main`.
- Require status checks from `PR Checks`.
- Block force pushes and branch deletion on `main`.
- Require CODEOWNERS review for `.github/workflows/**`, `.github/CODEOWNERS`, `scripts/codex_review_preflight.py`, and `scripts/public_release_audit.py`.
- Keep `pull_request_target` review jobs limited to same-repository PRs unless the workflow is redesigned to avoid repository secrets.
