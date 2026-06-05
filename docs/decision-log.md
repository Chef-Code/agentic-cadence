# Decision Log

Status: living document
Last updated: 2026-06-04

This document records major architecture and governance decisions. Update it
when a meaningful implementation or policy choice is made, when an assumption
is invalidated, or when an open question is resolved.

## Entry Template

```markdown
## YYYY-MM-DD - Decision title

Decision:
- The choice made.

Why:
- Evidence and reasoning.

Alternatives considered:
- Alternative and why it was rejected or deferred.

Consequences:
- What this enables or constrains.

Open questions:
- Remaining unknowns.
```

## 2026-06-05 - Slow GitHub Actions spend without weakening required checks

Decision:
- Add PR-level concurrency cancellation to `PR Checks` so superseded pushes do
  not keep burning Actions minutes.
- Keep the existing required PR check names, but classify changed paths inside
  the jobs and skip expensive compile, unit, smoke, adapter, package, and
  example steps when a PR does not change code, packaging, tests, examples, or
  workflow files.
- Limit PR workflows to PRs targeting `main`.
- Change repo-owned Codex Review from repeated PR lifecycle paid-review
  triggers to explicit elect/force label events, while allowing
  `synchronize` events to cancel obsolete in-flight elected reviews without
  starting paid-review preflight.
- Add cancellation and timeout bounds to the manual release dry-run workflow.

Why:
- The organization saw material GitHub Actions Linux spend, and this repository
  was running multi-job PR CI on every PR update.
- The Windows/Linux package matrix is useful before code merges, but it should
  not run full package/example validation for docs-only or non-code changes.
- Paid AI review should be deliberate. Leaving an elect label on a PR should
  not cause paid review attempts on every new push.
- A pushed commit should still cancel an already-running paid review for the
  stale head instead of allowing obsolete feedback to finish.

Alternatives considered:
- Use workflow-level `paths-ignore`. Rejected for the current required checks
  because skipped required workflows can leave branch protection waiting for
  contexts that were never created.
- Remove Windows package validation entirely. Rejected because the package
  still exposes PowerShell/Windows first-run behavior.
- Make all heavy checks manual. Rejected because code PRs still need reliable
  required CI before merge.

Consequences:
- Rapid force-push or fixup loops cancel obsolete PR runs.
- Docs-only changes still run diff hygiene and protocol validation, while
  expensive code/package work exits quickly.
- Codex Review now requires a new elect/force label event when an operator
  wants a fresh paid review after a push.
- Unrelated label churn does not cancel an in-flight elected review.

Open questions:
- Should branch protection later require only a lightweight always-on check and
  make package matrix checks conditional rather than required by name?
- Should Windows package validation become label-triggered for non-release PRs
  after more cost data is collected?

## 2026-06-04 - Keep review feedback response planning read-only

Decision:
- Add `review-response-plan` as a deterministic local command that consumes
  saved PR JSON, saved review-thread JSON, optional candidate discovery output,
  and PR-body evidence, then emits a `review-response-plan.v1` packet.
- Group failed checks, unresolved actionable current review-thread feedback,
  missing PR body sections, and optional candidate matches into bounded
  response-plan items.
- Restrict recommendations to `emit_executor_task`, `refresh_pr_evidence`,
  `update_pr_body`, `wait_for_checks`, and `operator_review`.
- Keep GitHub comments, PR body updates, review-agent invocation, branch
  creation, commits, pushes, merge, release, and package publication outside
  this command.

Why:
- Cadence needs a bridge from saved CI/review evidence to follow-up work, but
  reading local evidence must not imply authority to mutate GitHub or spend
  review resources.
- Reusing saved evidence keeps planning deterministic and auditable after
  explicit `github-evidence-sync` capture.

Alternatives considered:
- Post or resolve GitHub review comments from the same packet. Rejected because
  comment writes need separate operator approval, freshness checks, and audit
  evidence.
- Invoke paid review agents from response planning. Rejected because the packet
  should summarize local evidence and recommend bounded next actions only.
- Fold response planning into `pr-readiness`. Rejected because readiness is a
  merge gate, while response planning chooses next work when readiness fails.

Consequences:
- Operators can review one stable packet before deciding whether to emit an
  executor task, refresh PR evidence, update the PR body, wait for checks, or
  escalate for manual review.
- Response plans remain advisory and do not prove that feedback is resolved.

Open questions:
- What approval and identity model should govern future write-side PR/comment
  response actions?
- Should future role-aware planning distinguish Builder, Reviewer, Maintainer,
  QA, and Documentation follow-up ownership before response writes exist?

## 2026-06-03 - Keep execution run binding local and optional at closeout

Decision:
- Add local `execution-run.v1` records under the runtime root to bind task
  checksum, invocation id, result evidence checksum, validation packet checksum,
  repo path/branch/head anchors, and closeout status.
- Have the controlled fixture runner write a run record and compact
  `execution_run_record` audit event after result validation.
- Let `closeout-executor-result --run-record-file` fail closed on mismatched or
  partial supplied run evidence before any epoch mutation, then update accepted
  run records with closeout status and epoch-closeout checksum.
- Keep the run ledger local and auditable without adding a remote backend,
  distributed lock, hash chain, real executor invocation, or named host adapter.

Why:
- Task/result/snapshot files alone do not prove that the result being closed out
  is the same run that was invoked and validated.
- A local record is enough to harden the fixture path and closeout boundary
  before live executor support exists.
- Keeping `--run-record-file` optional preserves compatibility for existing
  local closeout tests while allowing stricter evidence binding when run
  evidence exists.

Alternatives considered:
- Make run records mandatory for all closeouts. Deferred because existing local
  result validation and closeout examples still need to work without a fixture
  run ledger, and real executor invocation is still out of scope.
- Add a remote ledger or distributed lock. Rejected for this slice because the
  current runtime is local filesystem state and no agent pool exists yet.
- Treat a valid run record as permission to invoke a real executor or perform
  Git/PR writes. Rejected because run records are evidence, not authority.

Consequences:
- Controlled fixture runs now leave a deterministic local chain from invocation
  through result validation and closeout.
- Supplied run-record mismatches block closeout with stable codes before active
  epoch state moves.
- Real executor invocation, autonomous code edits, authenticated approvals,
  hash chaining, and remote coordination remain future explicit slices.

Open questions:
- Should a later task require a clean audit replay before accepting supplied
  run records for closeout?
- What authenticated approval and hash-chain shape is required before real
  executor invocation is allowed?

## 2026-06-03 - Start execution by epoch gate, not executor launch

Decision:
- Add `start-governed-execution` as the Task 8 boundary between a reviewed
  `generic-executor-task.v1` packet and local active epoch state.
- Require an exact checksum-bound approval token
  `approve-executor-task:<task-packet-checksum>`, then recheck the current repo
  path, branch, `HEAD`, clean worktree, task-carried command and branch policy,
  active brake, and active epoch state before creating one active epoch.
- Emit `execution-start.v1` with stable blockers, `epoch_started`, and
  `executor_started: false`; append `execution_start_decision` audit evidence
  only after an approved epoch start.
- Keep real executor invocation, autonomous code edits, branch creation,
  commits, pushes, PR writes, merge, release, and package publication outside
  this slice.

Why:
- A task packet can become stale after branch movement, new commits, dirty
  worktree changes, policy edits, brake changes, or active epoch conflicts.
- Starting local epoch state is a distinct governance decision from launching a
  real executor. Splitting the boundary gives reviewers a stable packet and
  blocker taxonomy without granting hidden implementation authority.
- Carrying task packet policy fields into the epoch keeps later checks tied to
  the reviewed packet rather than a mutable policy file.

Alternatives considered:
- Let `loop-tick --emit-executor-task` start the epoch directly. Rejected
  because the emitted task packet needs separate review and approval before any
  runtime mutation.
- Invoke the controlled fixture or a real executor after approval. Deferred
  because Task 8 is the start gate only; Task 9 should bind run evidence before
  any real executor work is considered.
- Use the HMAC materialization approval secret for this gate. Deferred because
  this token is local checksum review evidence, not remote Git/PR authority or
  authenticated approver identity.

Consequences:
- Operators and future orchestration now have a deterministic bridge from
  `approve_executor_task` to a single active epoch.
- Blocked starts leave no active epoch and no success audit record.
- Successful starts still require an external executor handoff; Cadence does
  not implement code from this command.

Open questions:
- What identity and hash-chain evidence should later authenticate task-packet
  approval before real executor invocation?
- Should Task 9 require a clean audit replay immediately before closeout binds
  execution-start, invocation, validation, and epoch-closeout records?

## 2026-06-03 - Keep resume pickup verification read-only

Decision:
- Add `verify-resume` as a read-only `resume-verification.v1` packet before a
  fresh session continues claimed handoff work.
- Require the packet to check handoff signature and claimed state,
  clean-square evidence, current repo branch/head and dirty-worktree state,
  active brake, active epoch state, and pickup-policy evidence.
- Keep claiming, new-session launch, host context-pressure detection, executor
  invocation, branch creation, pull request writes, merge, release, and package
  publication outside this slice.

Why:
- Handoffs can become stale after branch movement, new commits, dirty
  worktree changes, brake changes, policy edits, or active epoch conflicts.
- A fresh session needs a deterministic packet with stable blocker codes before
  trusting transcript context or old-session assumptions.
- Keeping the verifier read-only prevents a pickup check from silently
  changing ownership or starting work.

Alternatives considered:
- Let `claim-handoff` imply resume readiness. Rejected because claim mutates
  state and does not re-check clean-square, repo head, active epoch, or current
  policy evidence at resume time.
- Launch a new session or invoke an executor when verification passes.
  Rejected because launch and execution require separate host integration,
  approval, and result-evidence gates.

Consequences:
- Operators and future orchestration can distinguish `resume_work` from
  recovery actions such as `claim_handoff`, `recreate_handoff`, `clear_brake`,
  `clean_worktree`, `approve_handoff`, or `close_or_fail_active_epoch`.
- Prepared handoffs now carry structured resume snapshot bindings for
  branch/head comparison. Resume verification treats that metadata as a pointer
  to persisted snapshot evidence and checks it against the signed handoff
  message before trusting branch/head claims.
- Resume verification still depends on external orchestration to perform any
  recommended next action.

Open questions:
- What signed or hash-chained evidence should bind a future automatic
  new-session launch to the verified packet?
- Should future role-aware handoffs require claimer identity beyond the current
  local `claimed_by` string?

## 2026-06-02 - Gate Git/PR materialization on exact plan approval

Decision:
- Add `git-pr-materialize` as the only Task 6 write-side Git/PR path.
- Require the operator approval token to match an HMAC over the reviewed
  `git-pr-plan.v1` packet checksum plus selected remote, resolved push URL, and
  create-vs-update PR target before any audit, Git, or write-side `gh` side effect.
- Re-run the dry-run planner and PR body preflight immediately before writes,
  require materialized evidence to cover the complete local diff, then audit
  intended and completed side effects.
- Materialize only branch creation from the already-clean current commit without
  switching the checkout, push with Git hook verification disabled for that
  push, and PR create/update. Existing PR updates first verify the PR head and
  base through `gh pr view`. Keep dirty-worktree commits, auto-merge, release,
  package publication, paid review, and real executor invocation outside scope.

Why:
- A dry-run plan can become stale as soon as branch, HEAD, evidence files,
  branch policy, or PR-body requirements change.
- Binding approval to the exact plan packet keeps the operator-approved action
  reviewable and avoids treating executor output as Git/PR authority.
- Intent/result audit records make partial failures replayable.

Alternatives considered:
- Let `git-pr-plan` execute its command examples directly. Rejected because the
  dry-run packet is deliberately non-authoritative and must remain side-effect
  free.
- Commit dirty worktree contents during materialization. Deferred because the
  current executor-result contract already requires materialized change
  evidence at current `HEAD`; dirty-worktree commit creation needs a separate
  policy and evidence model.

Consequences:
- Operators can approve a specific plan and target for local branch/push/PR
  materialization without granting autonomous merge or release authority.
- Missing/mismatched approval, stale plans, policy failures, PR body failures,
  and failed Git/`gh` commands produce stable blocker packets.
- Approval identity and tamper evidence remain future work.

Open questions:
- How should a future hash chain or signed approval identity bind the operator,
  plan packet, and resulting PR?
- Should PR update materialization support existing local branch refreshes
  beyond the current clean-commit path?

## 2026-06-02 - Keep GitHub evidence sync read-only and local-file based

Decision:
- Add `github-evidence-sync` as an explicit operator-invoked command that uses
  read-only `gh pr view` and GitHub GraphQL review-thread reads, then saves PR,
  check, review-thread, and summary evidence as local JSON files.
- Let deterministic local commands consume those saved files: candidate
  discovery turns failed checks into `pr_check_failure` candidates, and PR
  readiness blocks unresolved actionable current review comments.
- Keep branch creation, commits, pushes, pull request creation or update,
  review-response writes, merge, release, and package publication outside this
  slice.

Why:
- Cadence needs current CI and review evidence before any future Git/PR
  materialization can be trusted, but fetching that evidence should not imply
  write authority.
- Saved local packets preserve deterministic replay and make stale/freshness
  policy reviewable before a later approved side-effecting action.

Alternatives considered:
- Call GitHub from `pr-readiness` or `discover-candidates` automatically.
  Rejected because those commands are deterministic local consumers and should
  not acquire hidden network side effects.
- Start responding to review comments or editing PRs in the same slice.
  Rejected because write-side GitHub actions require operator approval,
  branch-policy re-checks, freshness gates, and audit evidence.

Consequences:
- Operators can refresh PR evidence explicitly, then reuse saved JSON for local
  readiness and candidate decisions.
- Missing `gh`, auth failure, rate limit, network failure, malformed JSON,
  incomplete paginated review-thread evidence, or incomplete local write sets
  block sync without partial evidence files.
- Future materialization work must re-check saved evidence freshness before any
  branch, commit, push, or PR action.

Open questions:
- What freshness policy should Task 6 require immediately before
  operator-approved Git/PR materialization?
- Should future write-side response planning distinguish Builder, Reviewer,
  Maintainer, QA, and Documentation ownership before any PR/comment mutation
  loop exists?

## 2026-06-02 - Keep first executor launch fixture-only

Decision:
- Add `run-controlled-executor-fixture` as a test/example-only command that
  launches a fake external executor component from an explicit command
  template.
- Validate the generic executor task packet and formatted command before
  launch, require result evidence at the approved path, and record both
  `executor_fixture_invocation` and `executor_result_validation` audit events.
- Keep real executor invocation, named-host adapter support, epoch closeout,
  branch policy, live Git/PR actions, merge authority, release authority, and
  package-publication authority outside this slice.

Why:
- The next execution risk is proving Cadence can govern an executor-shaped
  component without becoming the implementation authority itself.
- A fixture command gives reviewers a deterministic way to test success,
  failed evidence, timeout/stopped evidence, active brake stops, command policy,
  disabled live actions, and audit replay before any real executor is allowed.
- At that point the policy surface still lacked execution closeout, approval
  identity, hash chaining, and live GitHub synchronization; branch policy was
  deferred from this fixture slice and later added as local dry-run policy.

Alternatives considered:
- Integrate a named host adapter first. Rejected because it would overfit the
  governance boundary before the generic component contract is proven.
- Let `loop-tick` directly execute implementation work. Rejected because
  `loop-tick` remains the read-only election and task-packet emission boundary.
- Add live commit, push, PR, merge, release, or package-publication flags.
  Rejected because those actions require branch ownership, role separation,
  approval identity, and release governance that are not implemented.

Consequences:
- The fixture proves executor-as-component behavior while preserving Cadence as
  the policy, evidence, and audit governor.
- Successful fixture evidence can be recorded, but it is not permission to
  merge, release, publish, or claim unattended repository writes.
- Subsequent execution slices should continue toward audited closeout, branch
  policy, and live-evidence gates without giving Cadence unattended write,
  merge, release, or package-publication authority.

Open questions:
- What should the epoch closeout packet contain when a fixture or real executor
  returns failed, blocked, stopped, or timed-out evidence?
- Which audit fields must be hash-chained before real executor invocation?

## 2026-06-01 - Keep Git/PR planning dry-run and role-separated

Decision:
- The first Minimal Git/PR Automation increment should be a dry-run-only
  `git-pr-plan` packet.
- The packet may translate successful executor result evidence into suggested
  branch, commit, and PR metadata, but Cadence must not execute suggested
  commands, create a branch, commit, push, call GitHub, open a pull request, or
  treat the plan as approval to proceed.
- The executor that produced result evidence must not be treated as the final
  authority for Git/PR transition approval.

Why:
- Current generic executor task packets still forbid commit, push, PR
  creation, and head-change permissions. A first planning packet can make the
  next transition reviewable, but it cannot honestly prove that a branchable
  commit exists.
- Future agent-team orchestration needs a coordination artifact that a
  reviewer, QA agent, release agent, documentation agent, or human operator can
  consume separately from the builder that produced implementation evidence.
- Keeping the first slice dry-run preserves the local, deterministic evidence
  model while avoiding live GitHub credentials or irreversible remote effects.
- Existing active-stop controls already require a runtime root before accepting
  otherwise-valid non-`stopped` evidence for tasks with `brake_not_drive`.
  Git/PR planning must preserve that fail-closed rule so stale success evidence
  cannot bypass the current brake.

Alternatives considered:
- Add optional live `gh pr create`, push, or commit flags in the first slice.
  Rejected because those actions need branch ownership, approval identity,
  rollback, and live-side-effect policy that are not stable yet.
- Extend `validate-executor-result` to emit Git/PR fields directly. Rejected
  because result validation and post-result transition planning are different
  governance decisions.

Consequences:
- The `git-pr-plan` packet is safe to review, not safe to execute.
- A ready plan can only be produced from result evidence that passes the same
  brake/runtime-root checks as `validate-executor-result`.
- The packet must record materialized-change evidence explicitly; absent
  evidence blocks Git/PR readiness instead of relying on result `files_changed`
  alone.
- Suggested commands remain suggestions only.
- Later live Git or GitHub behavior requires explicit approval and stable
  packet contracts.

Open questions:
- What materialized-change evidence should later prove that executor output is
  actually present in a branchable commit?
- What identity model should prove that Git/PR approval came from a separate
  role or human operator?

## 2026-05-31 - Carry command policy in executor task packets

Decision:
- Store local command allow/deny policy on emitted generic executor task
  packets as `command_policy.allowed_commands` and
  `command_policy.denied_commands`.
- Enforce that carried command policy during executor result validation rather
  than re-reading a mutable policy file.
- Apply command policy to every effective command segment, including compound
  shell commands, shell grouping, command substitutions, and shell-wrapper
  payloads.
- Treat an active non-`DRIVE` brake as a stop control for result validation:
  non-`stopped` result evidence cannot be recorded as completion when
  `brake_not_drive` is one of the task stop conditions.
- Require a runtime root to validate otherwise-valid non-`stopped` completion
  evidence when `brake_not_drive` is present, so rootless validation cannot skip
  the current brake check.

Why:
- The executor result validator needs to validate the exact bounds approved for
  that task, not whichever policy file happens to exist later.
- Task packets are already the trust boundary for allowed paths, checks,
  runtime, permissions, and stop conditions; command policy belongs at the same
  boundary.
- The brake is the current local stop signal. Accepting `succeeded` evidence
  after the brake changes would make the stop condition advisory instead of
  enforceable.
- A command line can contain multiple effective commands. Checking only the
  first prefix would make command policy advisory for compound shell forms and
  shell grouping or command substitutions.
- Without a runtime root, Cadence cannot know the current brake state, so
  completion evidence for a brake-bound task must fail closed.

Alternatives considered:
- Re-read `--policy-file` during result validation. Rejected because policy can
  drift after task approval and before result evidence is recorded.
- Make command policy a global runtime setting. Deferred because 0.1.x remains
  local clone-based and task packets already provide portable evidence.
- Reject all result evidence after the brake changes. Rejected because
  `stopped` evidence is the correct way for an executor to report it honored
  the active stop.

Consequences:
- Policy-bounded task packets can now carry command bounds into later evidence
  validation.
- Active stop handling prevents completion evidence from being recorded after
  the operator brake changes unless the result status is `stopped`.
- Rootless validation can still report malformed evidence, but it cannot
  approve completion evidence for tasks that require the current brake check.
- Branch policy, hash chaining, authenticated approval identity, and real
  executor invocation remain future work.

Open questions:
- Which default command allowlist is sufficient for the first controlled local
  executor demo?
- Should clean audit replay be required before result evidence is recorded for
  a real executor run?

## 2026-05-31 - Specify audit replay before active execution controls

Decision:
- Merge a concrete read-only audit replay design before adding real executor invocation or active-loop stop controls.
- Keep the first replay scope limited to local `cadence-audit.v1` JSONL shape validation, event counts, checksum syntax, and stable blocker codes.
- Treat unsupported future schemas and events separately from malformed or corrupt audit records.

Why:
- Local policy/audit writes now create decision history, but future execution gates need a deterministic way to inspect that history before trusting it.
- The design gives implementation and review a stable packet contract without prematurely adding hash chaining, remote audit storage, or executor side effects.
- Distinguishing unsupported future records from corruption lets policy recommend `upgrade_cadence` only when history is valid but newer than the current reader.

Alternatives considered:
- Implement executor invocation before audit replay. Rejected because the first execution path should not depend on write-only audit history.
- Add hash chaining in the first replay slice. Deferred because the immediate gap is replay visibility for compact records that already exist.
- Make replay repair corrupt logs. Rejected because the first command should be read-only and suitable for gates.

Consequences:
- The audit implementation has an accepted packet shape, blocker taxonomy, count semantics, and required test list in `docs/designs/2026-05-31-audit-replay-design.md`.
- The first implementation slice now exposes `audit-replay` as a read-only local verifier; future execution gates can consume clean replay evidence without treating it as executor approval.

Open questions:
- Should hash chaining land immediately after basic replay, or wait until a controlled executor demo proves the audit fields are stable?
- Which future policy gates should require clean audit replay evidence before executor invocation?

## 2026-05-31 - Record loop decisions before invoking real executors

Decision:
- Add local policy and audit controls before adding real executor invocation.
- Keep policy file scope narrow for the first slice: executor task allowed
  paths, denied paths, required checks, max runtime, and stop conditions.
- Append compact audit records for root-backed loop decisions and executor
  result validation, using packet and evidence checksums instead of duplicating
  full packet bodies in the audit log.

Why:
- The next risky transition is from advisory packets to accepting or launching
  implementation work. Cadence needs a durable local record of what it decided
  and which bounds applied before that transition.
- Compact records keep audit history readable while binding each record to the
  emitted packet or validated evidence with checksums.
- Starting with local JSON avoids live credentials, remote state, or named host
  assumptions while still making the approval boundary explicit.

Alternatives considered:
- Invoke a real executor first and add audit afterward. Rejected because the
  first execution demo needs a decision trail before code changes are accepted.
- Store full packets in every audit record. Deferred because snapshots and
  executor packets can grow large, and compact checksummed records are enough
  for the first local audit slice.
- Build a remote tamper-evident audit backend now. Deferred because the 0.1.x
  baseline is local clone-based use.

Consequences:
- `loop-tick --emit-executor-task` can now be bounded by a local policy file
  and can return `policy_denied`.
- Root-backed loop and result-validation packets now leave append-only JSONL
  evidence under the Cadence runtime root.
- Audit replay, hash chaining, command allow/deny policy, branch policy,
  active-loop stop controls, and authenticated identity remain future work.

Open questions:
- Should audit records be hash-chained before real executor invocation, or is
  packet checksum plus append-only JSONL enough for the first fixture demo?
- Which command allowlist should be the default for a controlled local demo?

## 2026-05-31 - Reframe Cadence as GitHub-native agent-team orchestration

Decision:
- Agentic Cadence should be described as a governance and orchestration layer
  for autonomous software teams, not only as a continuous coding loop for one
  agent.
- The current single-agent flow remains Phase 1 and should continue to work.
- Future architecture should evaluate primitives by how well they support
  GitHub-native coordination across agent roles, branches, pull requests,
  reviews, CI, documentation, handoff contracts, and merge decisions.
- Documentation may describe future Planning, Architecture, Builder, Reviewer,
  QA, Documentation, Release, and Handoff agent roles, but must not imply those
  roles are implemented before evidence exists.

Why:
- GitHub already provides the coordination model that prevents teams from
  duplicating work or breaking each other's changes.
- The main risk for autonomous coding agents is ungoverned momentum: duplicate
  work, conflicting changes, stale docs, hallucinated assumptions, context
  overload, huge branches, roadmap drift, and merges without evidence.
- Existing Cadence primitives are not wasted; task election, epochs,
  validation, PR readiness, reviews, handoffs, and living docs are the right
  foundation for later multi-agent coordination.

Alternatives considered:
- Keep all documentation centered on one long-running agent. Rejected because
  it would bias future packet schemas, audit records, and roadmap work toward
  a narrower product than the architecture can support.
- Rewrite implementation toward team orchestration immediately. Rejected
  because the current request is vision and documentation alignment only, and
  the Phase 1 safety foundation is still the right next implementation path.
- Invent a coordination model outside GitHub. Rejected because issues,
  assignees, branches, pull requests, reviews, CI, documentation, and merges
  already provide the durable coordination surface.

Consequences:
- Roadmap language should frame the current loop as Phase 1.
- Handoff should be documented as a coordination primitive across sessions and
  roles, not only as a context-pressure escape hatch.
- Future work should avoid assumptions that only one agent exists.
- Confidence remains 10% because no new implementation capability shipped.

Open questions:
- What issue/task identity should bind a future role claim to a branch and PR?
- What identity and audit model proves review separation without making local
  use too heavy?
- Which team-orchestration slice should follow the controlled single-agent
  loop once the Phase 1 evidence target is met?

## 2026-05-31 - Bind executor task packets to local snapshot trust anchors

Decision:
- Executor task packets must include a non-empty repo name and absolute repo
  path.
- Task-packet validation must validate the embedded local repo snapshot and
  require snapshot repo, cwd, branch, and head to match the packet repo anchor.
- Dirty, low-confidence, malformed, relative-path, unnormalizable-path, or
  mismatched snapshot anchors must fail before execution can be approved.

Why:
- A task packet is the boundary between Cadence and an external implementation
  executor. It must not borrow clean snapshot evidence from one checkout while
  directing work at another checkout.
- Relative paths make the same packet context-dependent because they resolve
  against the validator process, not a stable repo identity.
- Requiring explicit repo identity keeps the documented trust anchor from
  becoming optional.

Alternatives considered:
- Validate only branch and head. Rejected because two checkouts can share a
  branch/head while representing different local paths or packet repo anchors.
- Allow relative paths and resolve them at validation time. Rejected because it
  makes validation depend on the caller's current working directory.
- Qualify docs to say repo identity is checked only when present. Rejected
  because executor task packets should be stricter than generic local metadata.

Consequences:
- The generic executor contract is safer for a controlled external-executor
  demo.
- Task packets remain local, generic, and approval-gated; Cadence still does
  not run a real executor, commit, push, or open PRs.

Open questions:
- Should the next slice record task-packet validation as an append-only audit
  event before any executor result is accepted?
- Should future live repo evidence sign the repo anchor or keep local snapshot
  validation as the first gate?

## 2026-05-30 - Keep executor integration behind a generic task/result contract

Decision:
- Add a generic executor task packet and result evidence validator before
  integrating any real executor or named host adapter.
- `loop-tick --emit-executor-task` may emit a bounded task packet for operator
  approval, but it must still set `executor_started: false`.
- `validate-executor-result` validates local result evidence without running an
  executor or mutating repo state.

Why:
- The project needs a formal boundary for implementation work before Cadence can
  safely start epochs, invoke executors, or record execution outcomes.
- Keeping the contract generic avoids smuggling in named host adapter support
  before there is explicit operator approval and verifier evidence.

Alternatives considered:
- Build a real executor adapter immediately. Deferred because branch, commit,
  PR, audit, and rollback controls are not ready.
- Let `loop-tick` call an external command directly. Rejected for this slice
  because the evidence shape and approval boundary need to be stable first.

Consequences:
- Cadence can now produce and validate the packets needed for a controlled
  external execution demo.
- The loop still cannot implement code by itself, and confidence remains low
  until a real executor flow is tested against a fixture repo.

Open questions:
- Which external executor should be integrated first, and under what approval
  policy?
- Should executor result validation become part of epoch completion or a
  separate audit record first?

## 2026-05-30 - Split single-tick orchestration into a read-only first phase

Decision:
- Add `loop-tick` as a Phase 1 read-only loop-controller command before adding
  executor, epoch mutation, validation, or PR side effects.
- The command emits `blocked`, `no_candidates`, `approval_required`, or
  `requires_executor_contract` and records that executor, epoch, and PR actions
  were not started.

Why:
- The project needs evidence that snapshot, candidate election, Cadence state,
  and next-action reporting can be stitched together before execution is added.
- Keeping Phase 1 read-only preserves the current safety boundary while making
  the next missing contract explicit.

Alternatives considered:
- Start epochs and call an executor in the first loop tick. Deferred because
  the executor evidence contract is not defined yet.
- Build continuous mode first. Rejected because repeated execution should
  compose a proven bounded tick.

Consequences:
- Cadence can now produce a single governed next-action packet from local repo
  state.
- Confidence remains low because no code is implemented, validated, committed,
  pushed, reviewed, or resumed by the loop.

Open questions:
- What exact executor evidence schema should satisfy
  `requires_executor_contract`?
- Should the next phase start an empty administrative epoch or wait until the
  executor contract exists?

## 2026-05-30 - Label evidence freshness before adding live sync

Decision:
- Add packet-level `readiness_evidence` labels before implementing any live
  GitHub synchronization.
- Use `local_only` for repo snapshots, `saved_input` for saved PR JSON,
  `stale` for saved PR JSON that exceeds an explicit age limit, and
  `live_like` only when a caller asserts live-origin evidence to the evaluator.
- Apply stale and future-timestamp gating only to saved PR JSON. Do not apply
  saved-file age policy to caller-asserted `live_like` evaluator inputs.
- Enforce local snapshot readiness evidence during snapshot validation.

Why:
- The current system evaluates local git snapshots and saved PR JSON. Without
  labels, downstream automation can overtrust local-only or stale state.
- Freshness labels make current limitations machine-readable while preserving
  deterministic, local-only behavior.
- Saved-file age policy should not create false stale labels for evaluator
  inputs that are explicitly asserted to be live-like by their caller.

Alternatives considered:
- Add live GitHub fetching first. Deferred because live sync needs a separate
  credential, rate-limit, and failure policy.
- Treat all saved PR JSON as stale by default. Rejected because existing
  deterministic local workflows would become noisy without an operator-supplied
  age policy.

Consequences:
- Loop automation can distinguish current local-only capability from later
  live synchronization.
- Operators can opt into stale saved-PR detection with
  `--max-pr-json-age-minutes`.
- Negative saved-PR age limits are rejected at the CLI boundary.
- Stale or future-dated saved PR evidence waits and recommends refresh before
  acting on PR blockers.
- `live_like` remains caller-asserted until Cadence owns the live fetch.

Open questions:
- What age threshold should controlled-demo policy require for saved PR JSON?
- Should future live sync preserve the same label schema or introduce signed
  source provenance?

## 2026-05-29 - Fail closed on unignored repo-local runtime roots

Decision:
- Commands that use the Cadence runtime root reject an unignored root inside
  the target git repo unless the operator passes `--allow-repo-local-root`.
- Gitignored repo-local runtime roots remain allowed.

Why:
- Cadence stores runtime state on disk, and candidate discovery treats dirty
  worktree state as repo evidence.
- A repo-local runtime root that is not ignored can create the dirty state that
  Cadence then reports back to the operator.

Alternatives considered:
- Allow all repo-local roots and document the risk. Rejected because the
  failure mode is easy to trigger and undermines state awareness.
- Ban all repo-local roots. Rejected because ignored repo-local state can be a
  valid local operator workflow, and explicit overrides are useful for tests or
  controlled environments.

Consequences:
- Runtime roots outside project repositories remain the preferred default.
- Operators can still make an explicit repo-local choice with
  `--allow-repo-local-root`.
- Future adapters must preserve this policy or document an equivalent guard.

Open questions:
- Should adapter conformance tests eventually include runtime-root policy
  checks outside the CLI entry point?

## 2026-05-29 - Treat documentation as part of the operating model

Decision:
- Maintain `roadmap.md`, `autonomous-loop-readiness.md`,
  `implementation-slices.md`, `progress-log.md`, and `decision-log.md` as
  living documents.
- Meaningful implementation PRs must evaluate whether these documents need
  updates.

Why:
- Agentic Cadence is intended to govern long-running agentic work. Future
  sessions and contributors need an accurate view of current capability,
  confidence, risks, and decisions.
- Readiness and confidence must be grounded in evidence, not intent.

Alternatives considered:
- Keep roadmap material only in PR descriptions or chat transcripts. Rejected
  because those are harder for future sessions to discover and verify.
- Keep a single roadmap document only. Rejected because readiness, slice
  tracking, progress evidence, and decisions have different audiences and
  update rhythms.

Consequences:
- Documentation updates become part of normal delivery for meaningful changes.
- Confidence ratings may go up or down when evidence changes.
- Stale documentation is treated as a project quality problem.

Open questions:
- Should CI eventually enforce a documentation-check prompt or checklist for
  changes that touch loop behavior?

## 2026-05-29 - Use 10% as the current unattended-operation confidence rating

Decision:
- Record current unattended continuous-operation confidence as 10%.

Why:
- The repo has real governance primitives: handoffs, task sizing, epochs,
  candidate discovery, PR readiness from saved inputs, release dry-run, and
  adapter contracts.
- At that point, the repo did not have the central autonomous loop: no executor
  contract, no implementation runner, no live PR/review sync, no
  branch/commit/push/PR creation, no automatic context-pressure sensing, and no
  new-session launch or resume orchestration.

Alternatives considered:
- Higher rating because many safety primitives exist. Rejected because the
  system cannot currently build, PR, review, hand off, resume, and continue by
  itself.
- Lower rating because full autonomy is absent. Rejected because the existing
  protocol and safety substrate is useful and tested.

Consequences:
- Progress toward autonomy should be measured from a conservative baseline.
- The next target is a constrained 50% confidence loop, not full autonomous
  production operation.

Open questions:
- What exact demo evidence should be required before moving from 10% to a
  higher confidence rating?

## 2026-05-29 - Target a bounded single-tick loop before continuous mode

Decision:
- Prioritize a single-tick loop orchestrator before any always-on continuous
  runner.

Why:
- Existing capabilities are one-shot commands. A single bounded tick is the
  smallest safe bridge between advisor behavior and loop-controller behavior.
- Repeated autonomous execution without a proven bounded tick would make
  failures harder to understand and recover from.

Alternatives considered:
- Build a daemon or scheduler first. Rejected because repeated execution should
  compose a proven safe tick, not hide missing control flow.
- Build named host adapters first. Rejected because the generic executor and
  loop contracts are not stable yet.

Consequences:
- The first demo should show `snapshot -> discover -> approve -> execute via
  contract -> validate -> record -> stop/next decision`.
- Continuous build mode remains deferred until one tick is reliable.

Open questions:
- Should the first tick command call an executor subprocess directly or only
  emit task packets for an external orchestrator?

## 2026-05-29 - Keep executor integration generic before named host adapters

Decision:
- Define a generic executor adapter contract before adding named host adapters.

Why:
- The repo already follows a generic adapter-contract pattern for host signals.
- Named adapter claims would be premature before the execution evidence shape,
  policy limits, and validation behavior are stable.

Alternatives considered:
- Build a Codex-specific executor first. Deferred because it could overfit the
  core protocol to one host.
- Build Claude or Gemini adapters first. Rejected for the current baseline
  because docs explicitly do not claim shipped support for those hosts.

Consequences:
- The next execution work should focus on packet schemas, fake executors,
  validation, and conformance tests.
- Named host support requires explicit operator approval.

Open questions:
- Which fields are required in executor evidence for the first controlled demo?
