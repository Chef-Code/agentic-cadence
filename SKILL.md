---
name: agentic-cadence
description: Manage durable agent handoffs, Cadence state, pickup signatures, and clean-square shutdowns for long-running or self-evolving workflows.
---

# Agentic Cadence

Use this skill when coding-agent work needs to survive context limits, pause safely, hand off to a fresh context, or obey the operator-controlled Cadence state.

## Core Rule

Before pickup or continuation, check `status`. If `cadence.state` is `HUDDLE` or `TIMEOUT`, do not start new work, do not merge, and do not claim a handoff unless the operator explicitly clears it back to `PLAY_ON`.

```bash
python scripts/cadence.py status
```

Status output includes a football-facing Cadence state and the legacy brake value used for compatibility:

- `PLAY_ON` (`brake.status == "DRIVE"`): automation may continue governed work.
- `HUDDLE` (`brake.status == "NEUTRAL"`): automation must not start new pickup work.
- `TIMEOUT` (`brake.status == "PARK"`): automation must stop pickup and require explicit operator resume.

## Runtime Root

The default runtime root is:

```text
%USERPROFILE%\.codex\cadence
```

Existing installations with `%USERPROFILE%\.codex\transmission` are reused by default so queued handoffs and legacy brake controls are not bypassed during the rename. If both legacy and cadence roots exist, Agentic Cadence fails closed until the operator selects one with `CODEX_CADENCE_ROOT`, `CODEX_TRANSMISSION_ROOT`, or `--root`.

## Compatibility

Use `scripts/cadence.py` and `codex_cadence.*` for new work. Existing adapters may continue invoking `scripts/transmission.py` or importing `transmission_control.*`; both route to Agentic Cadence during the rename.

## Handoff Workflow

1. Detect the guardrail: context pressure, reviewer loop exhaustion, CI loop exhaustion, policy limit, or operator request.
2. Write a concise handoff with the exact current objective, repo, branch, blockers, completed work, and next action.
3. Confirm the handoff has a `codex-handoff:v1` signature.
4. Run clean-square for the old session.
5. Stop active work in the old session after the handoff is ready.

Create a handoff:

```bash
python scripts/cadence.py create-handoff --title "PR 314 reviewer follow-up" --guardrail context --repo owner/repo --branch main --task-type execution --message-file handoff.md
```

Record clean-square:

```bash
python scripts/cadence.py clean-square <handoff-id> --summary "handoff written and old session stopped"
```

Prepare the old-session side in one command:

```bash
python scripts/cadence.py prepare-handoff --id context-loop --title "Continue bounded work" --guardrail context --repo owner/repo --cwd . --task-type execution --summary "current session is handing off"
```

`prepare-handoff` checks Cadence state, snapshots the repo, writes and validates a signed ready handoff, records clean-square, and emits `stop_current_session: true`. It must not claim the handoff it creates, launch a new context, commit, push, create a PR, spend review, or merge. V1 requires explicit guardrail input; automatic context detection requires a host/session signal and must not be inferred from transcript guesses.

## Pickup Workflow

1. Check `status`.
2. Inspect `next-handoff`.
3. Claim the handoff only if `cadence.state` is `PLAY_ON` (legacy `brake.status == "DRIVE"`).
4. Continue in the fresh context using the handoff message as the source of truth.
5. Mark the handoff `completed` or `failed`.

```bash
python scripts/cadence.py next-handoff
python scripts/cadence.py claim-handoff <handoff-id> --claimer codex
python scripts/cadence.py complete-handoff <handoff-id> --summary "work continued"
```

`create-handoff` requires `--task-type` for new handoffs. `claim-handoff` fails closed when a handoff has no persisted estimate, when pickup policy requires approval, or when its persisted estimate is malformed. Persisted estimates are recomputed against the canonical sizing model and their estimate binding checksum before pickup policy is trusted. Approval-gated pickup requires a separate `approve-handoff` record; handoff metadata such as `pickup_approved=true` is not trusted. Estimated handoffs without a binding must be re-created or explicitly migrated before pickup.

## Task Sizing and Epochs

Before starting long-running work, estimate it with `plan-task`.

```bash
python scripts/cadence.py plan-task --title "Resolve PR findings" --task-type execution --driver external_review --message-file task.md
```

Use `execution` for bounded implementation or fix work. Use `discovery` for investigation that can expand scope. Discovery work has stricter continuation rules and should not recursively elect more discovery without approval.

Run one bounded epoch at a time. Capture a repo snapshot with `snapshot-repo` before `start-epoch`; use the emitted `path` as `--snapshot-before-file` and pass the matching repo and branch. Epoch tasks from `--tasks-file` must declare `task_type` as `execution` or `discovery`; omitting `--tasks-file` records an empty administrative checkpoint epoch. At an epoch boundary, capture a fresh snapshot and pass it to `self-check` as `--snapshot-after-file`; pass `--ci-status green` only when CI is known green. Continuation requires exactly one active epoch with valid before/after snapshots. Current snapshot repo confidence wins over ad hoc confidence flags. `start-epoch` enforces task-count and discovery-count bounds from epoch policy and refuses a second active epoch. Use `complete-epoch` or `fail-epoch` before starting another. Medium uncertainty or `watch` epoch health shrink the next election to one task. Continue only when the decision is `CONTINUE`, the persisted self-check is bound to the active epoch and policy, the next election fits stored epoch policy, the epoch is within `max_minutes_per_epoch`, completed epoch history has not reached `max_epochs_without_user_approval`, CI satisfies `next_epoch_requires`, and Cadence remains `PLAY_ON` (legacy brake remains `DRIVE`).

## Candidate Discovery

Use `discover-candidates` to seed the election engine with read-only repo candidates.

```bash
python scripts/cadence.py discover-candidates --cwd <target-repo> --intent hybrid --discovery-mode local --elect
```

Repo-local `docs/cadence/business-memory.md` is an optional discovery input for durable business signals. It must be a single clean tracked regular file; dirty, untracked, symlinked, conflicted, or otherwise non-regular entries are rejected before parsing. Candidates read from that file must be emitted with `source: business_memory`, `maturity: discovery`, `classification`, and `classification_confidence`. Valid classification taxonomy values are exactly `direction`, `business_rule`, `problem`, `feature`, `nice_to_have`, `risk`, `constraint`, and `unknown`. Optional business-memory `Status` values are exactly `active`, `fulfilled`, and `superseded`; fulfilled and superseded entries stay in the memory file but are not emitted or elected as candidates. `Fulfilled By` or `Superseded By` also closes an entry when `Status` is omitted; entries without status or closure metadata remain active for legacy compatibility. When a meaningful signal cannot be mapped confidently, emit `classification: unknown` and include `unclassified_signal` as a driver. Business-memory-only initial candidates keep `repo_anchors: []` until code, docs, or test repo anchors are discovered; their traceability comes from `evidence.path`, `evidence.line`, and `evidence.heading` pointing to `docs/cadence/business-memory.md`. `--max-business-memory-candidates` caps how many may enter the candidate pool.

Use `--discovery-mode off` for focused execution. In that mode, the tool must not surface marker, maintenance, product-evolution, business-memory, or synthetic proposal work. Business-memory candidates are discovery-only: they may seed or inform election, but they must not directly start execution, modify files, commit, push, merge, or bypass task sizing, snapshots, Cadence state checks, self-check, or governance policy. Use `--proposal-allowance none`, `surface`, or `elect` to control whether synthetic proposals are hidden, shown, or eligible for election. Even with `--proposal-allowance elect`, proposals remain discovery-first and normal epoch governance still applies.

For reviewer follow-up, `--review-findings-file` accepts the existing normalized JSON list of findings, while `--review-threads-file` accepts saved GitHub GraphQL `reviewThreads` JSON that includes `isResolved`, `isOutdated`, and comment `outdated` status fields. Review-thread ingestion is local and deterministic: it must not call GitHub, must ignore resolved or outdated threads, must fail closed when required status fields are missing, must ignore non-actionable summaries such as walkthroughs or no-actionable-comments reports, and must map actionable comments to repo-relative `review_finding` candidates without bypassing path validation.

## Policy And Audit Boundary

`loop-tick --policy-file` can apply local `cadence-loop-policy.v1` executor-task bounds for paths, commands, checks, runtime, and stop conditions, and root-backed `loop-tick` and `validate-executor-result` append compact `cadence-audit.v1` records. Executor task packets carry `command_policy`, result validation enforces those command allow/deny lists across compound commands, command substitutions, and shell-wrapper payloads, and active non-`DRIVE` brakes require non-completion evidence to report `status: stopped`. If a task includes `brake_not_drive`, otherwise-valid non-`stopped` evidence requires a runtime root; rootless validation fails closed with `provide_runtime_root`. Use `audit-replay` to emit a read-only `audit-replay.v1` packet for `<root>/audit/events.jsonl`; it validates record shape, supported events, line counts, and checksum syntax, and reports blockers such as `audit_line_invalid_json` or `audit_schema_version_unsupported` without repairing files or approving executor invocation. A clean audit replay packet is not approval to invoke a real executor.

## Cadence State Controls

Use legacy `NEUTRAL` to put Cadence in `HUDDLE`, where automation should stop claiming new work but existing human inspection may continue.

```bash
python scripts/cadence.py set-brake NEUTRAL --reason "operator requested pause"
```

Use legacy `PARK` to put Cadence in `TIMEOUT`, where pickup must require an explicit operator resume.

```bash
python scripts/cadence.py set-brake PARK --reason "unsafe state" --resume-requires manual_ack
```

Clear back to `PLAY_ON` (legacy `DRIVE`) only after the operator approves.

```bash
python scripts/cadence.py clear-brake --reason "operator approved resume"
```

## Self-Evolution

Agentic Cadence may propose changes to its own protocol, scripts, or automation, but those changes must be made through normal reviewed commits or PRs. Do not silently mutate active safety rules while running a handoff or merge loop.

## PR Readiness Packets

Use `pr-readiness` to evaluate saved GitHub PR metadata before deciding whether a PR is ready for an operator-controlled merge. The command is deterministic and local: it reads a JSON file produced by `gh pr view --json ...`, can read a local Markdown PR template through `--pr-template-file`, reports blockers, waiting checks, duplicate check groups, skipped Codex Review jobs, missing required PR body or template sections, review state, and a recommended next action. It must not call GitHub, spend paid review, rewrite the PR body, or merge the PR.

```bash
gh pr view <number> --json number,title,state,isDraft,mergeable,mergeStateStatus,reviewDecision,body,headRefName,baseRefName,headRefOid,statusCheckRollup > pr.json
python scripts/cadence.py pr-readiness --pr-json-file pr.json --required-check "Python and protocol checks" --pr-template-file .github/pull_request_template.md
```

Use `pr-body-preflight` before publishing or updating a pull request body. It is deterministic and local: it reads `--body-file`, can read a local Markdown PR template through `--pr-template-file`, checks that required template headings are present in the draft body by heading label, reports missing template sections as blockers, and recommends `update_pr_body` or `publish_pr_body`. If no template file or `--required-body-section` is supplied, it must fail closed and recommend `provide_template_or_sections`. It must not call GitHub, spend paid review, rewrite the PR body, create a PR, update a PR, or merge a PR.

```bash
python scripts/cadence.py pr-body-preflight --body-file pr-body.md --pr-template-file .github/pull_request_template.md
```

## Release Dry Run Packets

Use `release-dry-run` before an operator creates a release tag or GitHub release. After `pyproject.toml` and `CHANGELOG.md` have been updated for the intended release version, the command is deterministic and local: it reads release metadata and Git refs under `--cwd`; verifies the requested version matches package metadata; derives `release_notes` from the matching changelog entry; checks that the changelog entry is latest; requires the selected target ref to match checked-out `HEAD`; verifies an existing tag points at the selected release commit; and emits `operator_confirmation_required: true`.

```bash
python scripts/cadence.py release-dry-run --cwd . --version <version>
```

It must not create tags, call GitHub, create a release, write release-note files, build distributions, upload artifacts, publish packages, push, or merge. Ready packets recommend `create_tag_after_operator_confirmation` or `create_github_release_after_operator_confirmation`; package-index publication remains blocked with `do_not_publish_package`.

Repository operators may also run `.github/workflows/release-dry-run.yml` manually after a release PR merges to `main`. The workflow accepts `version`, `tag`, and optional `target_ref`, checks out `main` with full history and tags, runs the same dry-run command, uploads `release-dry-run.json` and `release-notes.md`, and fails on blockers. It remains a read-only verification gate and does not create tags, create GitHub releases, publish packages, push, or merge.

## PR Review Automation

Pull requests should always run the deterministic PR checks. The Codex Review workflow is an elected paid reviewer, not an automatic blocker on every push. `.github/workflows/codex-review.yml` invokes pinned `openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c` through `pull_request_target` for `opened`, `synchronize`, `reopened`, `ready_for_review`, `labeled`, and `unlabeled` events when the PR is not a draft and comes from the same repository, but the free preflight skips paid review unless a trusted operator applies `codex-review-elect` or `codex-review-force`. Fork PRs run a skip notice because `pull_request_target` must not expose repository secrets to untrusted PR code. Same-repository review jobs check the live PR state before checkout and immediately before the paid Codex action, requiring the PR to remain open, unmerged, not draft, and on the same head SHA as the triggering event so obsolete, draft, merged, or closed PRs do not fail on missing synthetic merge refs or spend review credits. The workflow keeps the review prompt inline, uses `safety-strategy: drop-sudo` and `sandbox: read-only`, and tells Codex to review only, avoid modifying files, and focus on actionable findings, protocol drift, race conditions, missing tests, and security-sensitive automation behavior.

Before the paid Codex action can run, the workflow must pass a free preflight in `scripts/codex_review_preflight.py` checked out from the trusted base SHA, not from the PR merge tree. The PR checkout uses `github.event.pull_request.head.sha` instead of `refs/pull/<number>/merge` so a ready-for-review run remains stable if the PR is merged while the workflow is starting. The preflight computes a deterministic dedupe key from the PR head SHA and changed files, reads prior PR comments through the GitHub token, and skips if a matching hidden `codex-review:v1` marker in the canonical workflow-owned `## Codex Review` comment from `github-actions[bot]` already proves that elected head was reviewed. It also skips empty diffs, docs-only changes when not elected, trusted label opt-outs such as `codex-review-skip`, and non-elected code changes with `not_elected`; `codex-review-elect` elects a paid run when normal preflight safeguards pass, while `codex-review-force` explicitly allows a run. PR title/body text must not control review spending. If the preflight cannot read review comments for an elected review, it fails the check instead of silently passing or spending OpenAI credits. Workflow concurrency must cancel obsolete in-flight review runs at the PR level, and the paid Codex job must have a finite timeout.

Guardrail changes to `.github/workflows/codex-review.yml` or `scripts/codex_review_preflight.py` are evaluated by the current base branch guardrail and only become active after merge. Require manual operator review or branch-protected code-owner review before merging those changes.

The repository must define `OPENAI_API_KEY` as a GitHub secret before this workflow can run. Native `@codex review` can still be used manually, but the workflow is the repo-owned elected review gate.
