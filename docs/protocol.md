# Agentic Cadence Protocol

Status: draft

Reference implementation: `codex_cadence/cli.py`

## Concepts

Agentic Cadence has five core concepts today:

- `handoff`: a durable message that lets a new coding-agent session continue from a precise point.
- `signature`: a machine-detectable marker that tells automation a handoff is ready.
- `cadence state`: the operator-facing football state that controls whether automation may continue.
- `brake`: the legacy persisted state behind Cadence state, retained for compatibility.
- `clean-square`: the shutdown routine for the old session after a handoff is safely written.

Future orchestration concepts include:

- `orchestrator`: the future policy authority that decides whether to continue, stop, retry, split, review, or hand off work.
- `agent role`: a bounded responsibility such as planning, architecture, build, review, QA, documentation, release, or handoff.
- `handoff contract`: the explicit state package that transfers work between sessions or between roles.

The current implementation mainly exercises the single-agent Phase 1 path. The
protocol should not assume that only one agent exists forever.

## GitHub-Native Orchestration Model

The long-term coordination surface is GitHub-native: issues or recorded
decisions define work, assignees or role claims establish ownership, branches
isolate implementation, pull requests expose changes, reviews and CI gate
quality, documentation keeps the repository aligned, and merges advance stable
`main`.

Cadence should govern that workflow rather than bypass it. Future agent-team
orchestration must preserve these invariants:

- no agent edits `main` directly;
- every implementation happens on a branch;
- every meaningful change produces a pull request;
- validation runs before merge;
- review is separate from implementation when possible;
- handoff is explicit when work crosses a session or role boundary;
- small bounded slices are preferred over large ambiguous work.

Current commands do not implement role assignment, live GitHub synchronization,
branch creation, PR creation, or merge authority. Protocol language that
mentions agent roles or an agent pool is a design target unless a command
explicitly documents otherwise.

## Runtime State

The default runtime root is:

```text
%USERPROFILE%\.codex\cadence
```

If the legacy `%USERPROFILE%\.codex\transmission` root already exists, Agentic Cadence must reuse it by default. This preserves queued handoffs, approvals, and legacy brake controls so a rename cannot reset `PARK` or `NEUTRAL` to a fresh `DRIVE` brake. If both legacy and cadence roots exist, Agentic Cadence must fail closed until the operator selects one with `CODEX_CADENCE_ROOT`, `CODEX_TRANSMISSION_ROOT`, or `--root`.

Commands that use the runtime root must reject an unignored repo-local runtime root when the target working directory is inside a git repo. A repo-local root is allowed only when git ignores the root path or when the operator passes `--allow-repo-local-root`. This keeps Cadence state from becoming a self-created dirty-worktree signal. Runtime roots outside the target repo remain the preferred default.

Primary entry points share the package-owned dispatcher in `codex_cadence/cli.py`. `scripts/cadence.py` is a source-tree wrapper for direct checkout execution. Legacy entry points remain compatibility shims during the rename: `scripts/transmission.py` delegates to `transmission_control.cli`, and `transmission_control.*` aliases `codex_cadence.*`. New integrations should use the Cadence names.

Suggested layout:

```text
cadence/
  brake.json
  handoffs/
    ready/
    claimed/
    completed/
    failed/
  logs/
```

Repo-local files should be adapters or configuration only. They should not be required for the global protocol to work.

## Cadence States

`status` output exposes a football-facing `cadence` object for operators:

```json
{
  "brake": {
    "status": "DRIVE"
  },
  "cadence": {
    "state": "PLAY_ON",
    "legacy_brake": "DRIVE",
    "can_start_work": true,
    "requires_operator_resume": false
  }
}
```

`status.brake.status` remains present for compatibility. `cadence.legacy_brake` mirrors it so operators can read the football-facing state while existing automation continues to read the legacy field.

Allowed Cadence states:

- `PLAY_ON`: automation may continue governed work.
- `HUDDLE`: automation must not start new pickup work.
- `TIMEOUT`: automation must stop pickup and require explicit operator resume.

`brake.json` remains the persisted compatibility format behind Cadence state.

```json
{
  "status": "DRIVE",
  "reason": null,
  "scope": "global",
  "resume_requires": null,
  "updated_at": "2026-05-22T00:00:00Z"
}
```

Allowed statuses:

- `DRIVE`: maps to `PLAY_ON`.
- `NEUTRAL`: maps to `HUDDLE`.
- `PARK`: maps to `TIMEOUT`.

## Handoff Lifecycle

1. The active session detects a guardrail: context pressure, CI loop exhaustion, reviewer loop exhaustion, explicit operator request, or policy limit.
2. The session writes a handoff file with enough context for a fresh session to continue.
3. The session writes a signature marking the handoff as ready.
4. Automation sees the ready signature, checks Cadence state, then claims the handoff.
5. A fresh coding-agent session receives the handoff message.
6. The old session runs clean-square and stops active work.
7. The new session marks the handoff completed or failed.

In the team-orchestration model, the same lifecycle also applies across roles:
a Planning Agent can hand a decomposed task to a Builder Agent, a Builder Agent
can hand a PR to a Reviewer Agent, a QA Agent can hand failures back to a
Builder Agent, and a Documentation Agent can record architecture or behavior
changes after merge. Role handoffs must be explicit and verifiable, not
implicit transcript memory.

## Prepare Handoff

`prepare-handoff` is the deterministic old-session orchestration command for context handoff. It must check Cadence state, capture a repo snapshot, create a signed ready handoff, validate that handoff, record clean-square, and emit a JSON packet with `stop_current_session: true`.

The command must not claim, complete, or fail the handoff it creates. It must not launch a new agent context, commit, push, create a pull request, spend elected review, or merge. If Cadence is `HUDDLE` or `TIMEOUT`, it fails closed before writing a ready handoff. Duplicate ready handoff ids must fail without overwriting the existing record.

V1 requires explicit guardrail input such as `--guardrail context`. Automatic context detection requires a host/session signal; Cadence must not infer token pressure from transcript guesses.

## Handoff Signature

Draft marker:

```text
<!-- codex-handoff:v1 id=<id> status=READY sha=<sha> -->
```

The marker must include:

- protocol version
- handoff id
- status
- checksum or signature for the handoff body

## Task Sizing and Epoch Governance

Long-running work is governed by task estimates and epochs. New handoffs must declare `--task-type` so pickup policy cannot be bypassed by omitting task sizing. Estimates classify work as `execution` or `discovery`, assign a duration bucket, track uncertainty, and map the task to pickup policy. Epochs bound recursive work with task and time budgets, repo snapshots, epoch health, repo confidence, and self-check decisions.

`snapshot-repo` writes the snapshot path in its JSON output. Snapshot packets must include `readiness_evidence` with `source: local_git`, `freshness: local_only`, `live: false`, `stale: false`, and limitations that make clear open PRs and review threads were not fetched. Snapshot validators must reject missing or malformed local readiness evidence at epoch and self-check boundaries. `start-epoch` requires `--repo`, `--branch`, and `--snapshot-before-file`, and validates that the pre-epoch snapshot matches them. It records the explicit task list from `--tasks-file`; when that file is omitted, the epoch is an empty administrative checkpoint. It enforces the active epoch policy, including `max_tasks_per_epoch` and `max_discovery_tasks_per_epoch`. Any task recorded in an epoch must declare `task_type` as `execution` or `discovery`. Only one active epoch may exist in a runtime root; use `complete-epoch` or `fail-epoch` before starting another.

`self-check` at an epoch boundary should receive `--snapshot-after-file` from a fresh `snapshot-repo` run; use `--ci-status green` only when CI is known green. `CONTINUE` is allowed only when Cadence is `PLAY_ON` (legacy brake is `DRIVE`), exactly one active epoch exists, the self-check is grounded in that epoch with a valid pre-epoch snapshot and a valid current snapshot, current repo confidence is not `low`, uncertainty is not `high`, epoch health is not `degraded`, the epoch is within `max_minutes_per_epoch`, completed epoch history has not reached `max_epochs_without_user_approval`, CI satisfies `next_epoch_requires`, and the next elected task fits the stored epoch policy. Medium uncertainty or `watch` epoch health shrink the next election to one task. Current snapshot confidence wins over ad hoc CLI confidence flags. Candidate election is capped by the stored epoch task and discovery limits.

Use `snapshot-repo` to record the repository state before an epoch and again before continuation. Use `start-epoch` to begin one bounded work interval from a repo snapshot and optional explicit task list. Use `self-check` at each epoch boundary to decide whether work may continue, must pause, or must stop. `complete-epoch --decision CONTINUE` requires a persisted `CONTINUE` self-check for the same active epoch, policy, baseline snapshot, current snapshot record, and completed-epoch count. The default `max_epochs_without_user_approval` value allows one automatic continuation before the next boundary must ask for approval or hand off.

At claim time, handoffs without persisted estimates and malformed estimated handoffs fail closed instead of being claimed. Persisted estimates are checked against the canonical task sizing model and their estimate binding checksum before pickup policy is trusted. Approval-gated handoffs require a separate `approve-handoff` record bound to the handoff checksum and estimate checksum; `pickup_approved=true` metadata is ignored. Handoffs with an estimate but without an estimate binding must be re-created or explicitly migrated before pickup.

## Candidate Discovery

Candidate discovery is read-only. It may inspect local repo state, known failures, review findings files, text markers, and proposal allowance, but it must not start epochs, claim handoffs, modify files, commit, push, or merge.

`discovery_mode: off` is focused execution mode. It suppresses candidate discovery and proposal surfacing so automation remains inside the approved task or handoff boundary.

The repo-local `docs/cadence/business-memory.md` file is an optional Candidate Discovery source. It must be a single clean tracked regular file; dirty, untracked, symlinked, conflicted, or otherwise non-regular entries are rejected before parsing. Entries discovered from it must produce candidates with `source: business_memory`, `maturity: discovery`, `classification`, and `classification_confidence`. Classification must use exactly these taxonomy values: `direction`, `business_rule`, `problem`, `feature`, `nice_to_have`, `risk`, `constraint`, and `unknown`. Optional business-memory `Status` values are exactly `active`, `fulfilled`, and `superseded`; fulfilled and superseded entries remain durable memory but must not be emitted or elected as candidates. `Fulfilled By` or `Superseded By` also closes an entry when `Status` is omitted; entries without status or closure metadata remain active for legacy compatibility. When a meaningful signal cannot be mapped confidently, the candidate must use `classification: unknown` and include `unclassified_signal` as a driver. Business-memory-only initial candidates must keep `repo_anchors: []` until code, docs, or test repo anchors are discovered. Initial discovery traceability must come from `evidence.path`, `evidence.line`, and `evidence.heading` pointing to `docs/cadence/business-memory.md`. `--max-business-memory-candidates` limits how many business-memory candidates may be surfaced or elected.

Business-memory candidates are discovery-only. They can inform the election pool, but they must not directly start execution, modify files, commit, push, merge, or bypass task sizing, repo snapshots, Cadence state checks, self-check, or governance policy. `discovery_mode: off` must suppress business-memory candidates along with marker, maintenance, product-evolution, and synthetic proposal work.

Proposal allowance has three modes: `none`, `surface`, and `elect`. `elect` allows proposals into the election pool, but synthetic proposals remain discovery-first and cannot bypass task sizing, snapshots, Cadence state checks, self-check, or governance policy.

Reviewer feedback may enter candidate discovery through two local files. `--review-findings-file` reads the existing normalized JSON list of findings. `--review-threads-file` reads saved GitHub GraphQL `reviewThreads` JSON with `isResolved`, `isOutdated`, and comment `outdated` status fields, then converts actionable unresolved, current review comments into the same `review_finding` candidate shape. This ingestion must stay deterministic and local: it must not call GitHub, trust PR body text, include resolved or outdated threads, assume missing status fields are current, include non-actionable summaries such as walkthroughs or no-actionable-comments reports, or bypass repo-relative path validation.

## Loop Tick

`loop-tick` is the Phase 1 read-only loop-controller command. It must check Cadence state, capture and persist a local repo snapshot, run deterministic candidate discovery with election enabled, and emit one JSON packet describing the next governed action. It must not start an executor, start or complete an epoch, create or update a branch, commit, push, create or update a pull request, spend review, merge, release, or publish.

The packet must include the brake and Cadence state, the persisted snapshot, the candidate-discovery packet, the elected candidates, `read_only: true`, `executor_started: false`, `epoch_started: false`, `pr_action_started: false`, and a `recommended_next_action`. Phase 1 recommended actions are `blocked` when Cadence state disallows work, `approval_required` when local repo confidence is low, `no_candidates` when election returns no candidate, `requires_executor_contract` when a candidate is available but Cadence has not emitted an executor task packet, and `approve_executor_task` when an executor task packet has been emitted for operator approval. Low local repo confidence takes precedence over empty election and includes dirty worktrees, unborn or detached HEAD, known failures, and an operator-supplied red CI signal. This command is not a continuous runner; repeated ticks require an external operator or orchestrator.

## Generic Executor Contract

The generic executor contract is an agent-neutral boundary, not a named host adapter. `loop-tick --emit-executor-task` may include an `executor_task` packet with `schema_version: generic-executor-task.v1`, task identity, task type `execution` or `discovery`, bucket `XS`, `S`, `M`, `L`, or `XL`, repo name, absolute repo path, branch/head snapshot, allowed repo-relative paths, required checks, positive time/task limits, stop conditions, an expected result-evidence path, and permissions that forbid commit, push, and PR creation. Task-packet validation must validate the embedded local repo snapshot, require non-empty repo identity, require absolute local cwd/path anchors, require snapshot repo/cwd/branch/head to match the task packet repo anchor, reject dirty snapshots, and reject low-confidence snapshots. Emitting this packet must set `executor_started: false`; Cadence must not execute the task.

Executor result evidence uses `schema_version: generic-executor-result.v1` and `packet: executor_result`. It must include executor id, start/end timestamps, status `succeeded`, `failed`, `blocked`, or `stopped`, files changed, commands run, validation results, summary, confidence, blockers, dirty-worktree status, and resulting head SHA for successful results. `validate-executor-result` reads a task packet and result evidence from local JSON files and emits an `executor_result_validation` packet. Successful evidence must include command and validation evidence, must show every task-packet `required_checks` entry in both `commands_run` with exit code `0` and `validation_results` with matching `command` and `status: passed`, and all validation results in successful evidence must pass. Result evidence must respect disabled permissions: it rejects reported `git commit`, `git push`, or `gh pr create` invocations while those permissions are false, including absolute-path, common git/gh global-option, and shell-wrapper forms, and it rejects head changes when commits are forbidden. Invalid evidence exits nonzero. It must not run an executor, modify files, commit, push, open PRs, spend review, merge, or infer named-host support.

PR readiness may check target-repository template compliance from local files. `pr-readiness --pr-template-file <path>` must read a Markdown pull request template, derive required PR body sections from its headings, and report missing template sections through the readiness packet. It must include `readiness_evidence` labels so consumers can distinguish `saved_input`, `stale`, and caller-asserted `live_like` evidence. The CLI reads local saved PR JSON and labels it `saved_input`; when `--max-pr-json-age-minutes` is supplied and the file mtime exceeds that limit, it must label the packet `stale`, add a `pr_evidence_stale` waiting item, and recommend `refresh_pr_evidence` before acting on stale blockers. When a saved PR JSON file has a future mtime, it must label the packet `stale`, add a `pr_evidence_from_future` waiting item, and recommend `refresh_pr_evidence`. Negative `--max-pr-json-age-minutes` values must fail closed. Saved-JSON age policy applies only to saved PR JSON, not to caller-asserted `live_like` evaluator inputs. It must ignore headings inside HTML comments or fenced code blocks, match saved PR body headings by section label rather than by heading level, stay repo-agnostic, and must not hard-code target-specific labels, call GitHub, rewrite the PR body, spend paid review, or merge the PR.

PR body preflight covers the pre-publish side of the same template contract. `pr-body-preflight --body-file <path> --pr-template-file <path>` must read a draft PR body and a local Markdown pull request template, derive required template sections from template headings, and report missing template sections before PR creation or update. It must reuse the same heading parser as PR readiness, match draft body headings by normalized section label, ignore headings inside HTML comments or fenced code blocks without creating false setext headings across skipped blocks, stay repo-agnostic, and must not hard-code target-specific labels, call GitHub, rewrite the body file, create a PR, update a PR, spend paid review, or merge the PR. If no template file or `--required-body-section` is supplied, it must fail closed and recommend `provide_template_or_sections`.

## Release Dry Run

`release-dry-run` checks a release candidate before an operator creates a tag or GitHub release. It must read only local `pyproject.toml`, `CHANGELOG.md`, and Git refs; derive release notes from the matching changelog entry; verify the requested version matches package metadata; require the changelog entry to be the latest release entry; require the selected target ref to match the checked-out `HEAD`; verify an existing tag points at the selected release commit; and report a clean-worktree, target-branch, and local `origin/<branch>` comparison when those refs are available.

The packet must include `operator_confirmation_required: true` and recommend `create_tag_after_operator_confirmation`, `create_github_release_after_operator_confirmation`, `address_blockers`, or `do_not_publish_package` as appropriate. The command must not create tags, call GitHub, create a release, write release-note files, build distributions, upload artifacts, publish packages, push, or merge.

`.github/workflows/release-dry-run.yml` is the manual GitHub Actions wrapper for this packet. It must use `workflow_dispatch`, accept `version`, `tag`, and optional `target_ref` inputs, check out `main` with full history and tags, run `python scripts/cadence.py release-dry-run`, upload `release-dry-run.json` and `release-notes.md`, and fail when `ready_to_release` is not true. The workflow must keep `contents: read` permissions and must not create tags, create GitHub releases, publish packages, push, or merge.

## Self-Evolution Rule

Agentic Cadence may propose changes to itself, but it must not silently rewrite its own safety rules while executing active work. Self-evolution execution tasks are blocked by pickup and self-check under the default `propose_only` policy. Policy changes should be reviewed like normal code changes before becoming active.

## PR Review Automation

Repository pull requests should run the normal PR checks. The Codex Review workflow is an elected paid reviewer, not an automatic blocker on every push. `.github/workflows/codex-review.yml` uses pinned `openai/codex-action@a26d2d4d8b78a694338b8e3715c3630254340b2c` through `pull_request_target` events for `opened`, `synchronize`, `reopened`, `ready_for_review`, `labeled`, and `unlabeled`, skips draft PRs, runs only for same-repository PRs with `safety-strategy: drop-sudo` and `sandbox: read-only`, then posts the action `final-message` back to the PR only when a trusted operator applies `codex-review-elect` or `codex-review-force`. Fork PRs run a skip notice because `pull_request_target` must not expose repository secrets to untrusted PR code. Same-repository review jobs check the live PR state before checkout and immediately before the paid Codex action, requiring the PR to remain open, unmerged, not draft, and on the same head SHA as the triggering event; obsolete, draft, merged, or closed PRs skip instead of failing on a missing synthetic merge ref or spending review credits.

The paid Codex action is guarded by a free preflight implemented in `scripts/codex_review_preflight.py` and checked out from the trusted base SHA rather than the PR merge tree. The PR checkout uses `github.event.pull_request.head.sha` instead of `refs/pull/<number>/merge` so a ready-for-review run remains stable if the PR merges while the workflow is starting. The preflight runs before any OpenAI API key is used, computes a dedupe key from the PR head SHA and changed files, reads prior PR comments, and skips when it finds a matching hidden `codex-review:v1` marker in the canonical workflow-owned `## Codex Review` comment from `github-actions[bot]` for an elected head. It also skips empty diffs, docs-only changes when not elected, trusted label opt-outs such as `codex-review-skip`, and non-elected code changes with `not_elected`; `codex-review-elect` elects a paid run when normal preflight safeguards pass, while `codex-review-force` overrides dedupe and docs-only skips. PR title/body text must not control review spending. Inability to inspect prior review comments is a failing preflight condition only when a paid review has been elected. Workflow concurrency must cancel obsolete in-flight review runs at the PR level so repeated events do not stack paid reviews, and the paid Codex job must have a finite timeout.

Guardrail changes to `.github/workflows/codex-review.yml` or `scripts/codex_review_preflight.py` have a bootstrap boundary: a PR is evaluated by the current base branch guardrail, and its updated guardrail only becomes active after merge. Those changes require manual operator review or branch-protected code-owner review before merge so an untrusted PR cannot relax spending controls and use them in the same review cycle.

The workflow requires the repository secret `OPENAI_API_KEY`. The review prompt is kept inline in `.github/workflows/codex-review.yml` so a PR cannot redirect the action through a modified prompt file. It must tell Codex not to modify files and to focus on actionable findings, protocol drift, race conditions, missing tests, and security-sensitive automation behavior. Native `@codex review` remains a useful manual fallback, but the repo-owned workflow is the repeatable elected PR review gate.
