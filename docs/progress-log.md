# Progress Log

Status: living document
Last updated: 2026-05-30

This log records meaningful project progress, confidence changes, new risks,
and evidence. New discoveries count as progress when they change what the
project knows.

## Entry Template

```markdown
## YYYY-MM-DD - Short title

Summary:
- What changed.

Completed slices:
- Slice name or "None".

Confidence change:
- Previous: N%
- New: N%
- Reason:

Evidence:
- Tests, demos, PRs, review results, audit output, or command output.

New risks or blockers:
- Risk or "None".

Docs updated:
- List living docs updated.
```

## 2026-05-30 - Readiness and freshness labels

Summary:
- Added `readiness_evidence` metadata to repo snapshots and PR-readiness
  packets.
- Labeled repo snapshots as `local_only` local-git evidence with explicit
  limitations for unfetched PR and review state.
- Labeled PR readiness inputs as `saved_input`, `stale`, or caller-asserted
  `live_like` evidence.
- Added `--max-pr-json-age-minutes` so stale saved PR JSON waits and
  recommends `refresh_pr_evidence`.

Completed slices:
- Readiness and freshness labels.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: this makes evidence freshness explicit and prevents stale saved PR
  JSON from looking ready when an age limit is supplied, but it does not add
  live GitHub synchronization, an executor, loop runner, PR creation, or
  autonomous resume capability.

Evidence:
- `python -m unittest tests.test_repo_state tests.test_pr_readiness tests.test_cadence`
- `python -m unittest discover -s tests`
- `python scripts\validate_protocol.py`
- `git diff --check`

New risks or blockers:
- Live PR, review, and CI synchronization is still not implemented.
- Caller-asserted `live_like` evidence is labeled, not independently verified
  by Cadence.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-29 - Runtime-root safety guard

Summary:
- Added a CLI guard that rejects unignored runtime roots inside the target git
  repo unless the operator explicitly passes `--allow-repo-local-root`.
- Allowed repo-local runtime roots when the path is ignored by git.
- Limited the guard to commands that actually use Cadence runtime state so
  no-root planning and discovery commands do not get false-positive blocks.

Completed slices:
- Runtime-root safety guard.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: this removes a repo-state footgun, but it does not add the missing
  executor, loop runner, PR creation, live review sync, or autonomous resume
  capability required for unattended continuous operation.

Evidence:
- `python -m unittest tests.test_cadence`
- `python -m unittest discover -s tests`
- `python scripts\validate_protocol.py`
- `git diff --check`
- New CLI tests cover blocked unignored repo-local runtime roots, allowed
  ignored repo-local runtime roots, explicit operator override, cross-command
  root-using behavior, no-cwd current-repo behavior, and no-root planning and
  discovery commands.

New risks or blockers:
- Future host adapters that bypass the CLI must preserve the same guard or
  prove an equivalent runtime-root policy.

Docs updated:
- `README.md`
- `docs/protocol.md`
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/progress-log.md`
- `docs/decision-log.md`

## 2026-05-29 - Living readiness documentation initialized

Summary:
- Established the living documentation set for roadmap, autonomous-loop
  readiness, implementation slices, progress tracking, and decisions.
- Captured the blunt readiness assessment against the "press start and build
  continuously" vision.
- Recorded the first 50% confidence target slices.

Completed slices:
- None. This was documentation and governance work only.

Confidence change:
- Previous: 10%
- New: 10%
- Reason: no implementation capability changed. The project remains a governed
  protocol toolkit, not an unattended autonomous builder.

Evidence:
- Released 0.1.3 baseline documentation and code review.
- Current implemented commands and docs show local state inspection,
  candidate discovery, task sizing, handoffs, PR readiness from saved inputs,
  release dry-run, and generic adapter contracts.
- Current gaps remain: no executor contract, no continuous loop runner, no
  branch/commit/push/PR creation, no live GitHub sync, no automatic
  session-resume orchestration.

New risks or blockers:
- Documentation must be kept current as implementation slices land, or the
  roadmap will drift back into aspiration.
- Runtime roots placed inside target repositories can create dirty-worktree
  signals unless ignored or guarded.

Docs updated:
- `docs/roadmap.md`
- `docs/autonomous-loop-readiness.md`
- `docs/implementation-slices.md`
- `docs/progress-log.md`
- `docs/decision-log.md`
